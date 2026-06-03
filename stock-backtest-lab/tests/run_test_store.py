"""直接运行 store 模块的快速测试，不依赖 pytest。"""
import os
import sys
import tempfile
import sqlite3
from pathlib import Path

# 确保能导入 backtest_core
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from backtest_core import store


def test(desc, fn):
    try:
        fn()
    except Exception as e:
        print(f"FAIL: {desc}")
        raise
    print(f"PASS: {desc}")


def run_all():
    # 临时数据库
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    db_path = tmp.name
    tmp.close()
    original = store.get_db_path
    store.get_db_path = lambda: Path(db_path)

    conn = store.get_connection()
    store.init_schema(conn)

    test("建表", lambda: _test_schema(conn))
    test("upsert_instrument", lambda: _test_instrument(conn))
    test("insert_daily_bars", lambda: _test_daily_bars(conn))
    test("INSERT OR REPLACE 幂等", lambda: _test_idempotent(conn))
    test("日期范围过滤", lambda: _test_date_filter(conn))
    test("insert_index_bars", lambda: _test_index_bars(conn))
    test("check_coverage", lambda: _test_coverage(conn))
    test("sync_state CRUD", lambda: _test_sync_state(conn))

    conn.close()
    os.unlink(db_path)
    store.get_db_path = original

    print("\nALL TESTS PASSED")


def _test_schema(conn):
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {t["name"] for t in tables}
    for t in ["instruments", "daily_bars", "index_bars", "sync_state"]:
        assert t in names, f"Missing table: {t}"


def _test_instrument(conn):
    store.upsert_instrument(conn, "SZ000333", "a", "美的集团", "stock")
    r = conn.execute(
        "SELECT * FROM instruments WHERE symbol = ?", ("SZ000333",)
    ).fetchone()
    assert r["name"] == "美的集团"
    assert r["market"] == "a"
    # 更新
    store.upsert_instrument(conn, "SZ000333", "a", "Midea Group")
    r = conn.execute(
        "SELECT * FROM instruments WHERE symbol = ?", ("SZ000333",)
    ).fetchone()
    assert r["name"] == "Midea Group"


def _test_daily_bars(conn):
    bars = [
        {"date": "2024-01-02", "open": 50.0, "high": 52.0, "low": 49.5, "close": 51.5, "volume": 1000000},
        {"date": "2024-01-03", "open": 51.5, "high": 53.0, "low": 51.0, "close": 52.5, "volume": 1200000},
    ]
    n = store.insert_daily_bars(conn, bars, "SZ000333")
    assert n == 2
    rows = store.read_daily_bars(conn, "SZ000333")
    assert len(rows) == 2
    assert rows[0]["trade_date"] == "2024-01-02"
    assert rows[0]["close"] == 51.5


def _test_idempotent(conn):
    bars = [
        {"date": "2024-01-02", "open": 50.0, "high": 52.0, "low": 49.5, "close": 51.5, "volume": 1000000},
    ]
    # 先确保之前的数据已清除
    conn.execute("DELETE FROM daily_bars")
    conn.commit()
    store.insert_daily_bars(conn, bars, "SZ000333")
    store.insert_daily_bars(conn, bars, "SZ000333")
    rows = store.read_daily_bars(conn, "SZ000333")
    assert len(rows) == 1
    # 更新价格
    bars2 = [
        {"date": "2024-01-02", "open": 51.0, "high": 53.0, "low": 50.5, "close": 52.0, "volume": 1100000},
    ]
    store.insert_daily_bars(conn, bars2, "SZ000333")
    rows = store.read_daily_bars(conn, "SZ000333")
    assert len(rows) == 1
    assert rows[0]["close"] == 52.0


def _test_date_filter(conn):
    conn.execute("DELETE FROM daily_bars")
    conn.commit()
    bars = [
        {"date": "2024-01-02", "open": 50.0, "high": 52.0, "low": 49.5, "close": 51.5, "volume": 1000000},
        {"date": "2024-01-15", "open": 52.0, "high": 54.0, "low": 51.5, "close": 53.0, "volume": 1100000},
        {"date": "2024-02-01", "open": 53.0, "high": 55.0, "low": 52.5, "close": 54.0, "volume": 1200000},
    ]
    store.insert_daily_bars(conn, bars, "SZ000333")
    rows = store.read_daily_bars(conn, "SZ000333", from_date="2024-01-01", to_date="2024-01-31")
    assert len(rows) == 2
    rows = store.read_daily_bars(conn, "SZ000333", from_date="2024-02-01")
    assert len(rows) == 1


def _test_index_bars(conn):
    bars = [
        {"date": "2024-01-02", "open": 3000.0, "high": 3050.0, "low": 2990.0, "close": 3040.0},
    ]
    n = store.insert_index_bars(conn, bars, "sh000300")
    assert n == 1
    rows = store.read_index_bars(conn, "sh000300")
    assert len(rows) == 1
    assert rows[0]["index_code"] == "sh000300"
    assert rows[0]["close"] == 3040.0


def _test_coverage(conn):
    conn.execute("DELETE FROM daily_bars")
    conn.commit()
    bars = [
        {"date": "2024-01-02", "open": 50.0, "high": 52.0, "low": 49.5, "close": 51.5, "volume": 1000000},
        {"date": "2024-01-31", "open": 52.0, "high": 54.0, "low": 51.5, "close": 53.0, "volume": 1100000},
    ]
    store.insert_daily_bars(conn, bars, "SZ000333")
    cov = store.check_coverage(conn, "SZ000333", "2024-01-02", "2024-01-31")
    assert cov["total_rows"] == 2
    assert cov["covered"]
    # 区间超出
    cov2 = store.check_coverage(conn, "SZ000333", "2024-01-02", "2024-02-15")
    assert cov2["covered"] is False
    # 非交易日起始日容忍：from_date 是非交易日（假期），first_date 是随后首个交易日
    bars2 = [
        {"date": "2020-01-02", "open": 50.0, "high": 52.0, "low": 49.5, "close": 51.5, "volume": 1000000},
        {"date": "2020-01-03", "open": 51.0, "high": 53.0, "low": 50.5, "close": 52.0, "volume": 1100000},
    ]
    conn.execute("DELETE FROM daily_bars")
    conn.commit()
    store.insert_daily_bars(conn, bars2, "SZ000333")
    # from_date=2020-01-01（元旦放假），first_date=2020-01-02（首个交易日），应在容忍范围内
    cov3 = store.check_coverage(conn, "SZ000333", "2020-01-01", "2020-01-03")
    assert cov3["covered"], f"非交易日起始日应通过覆盖检查: first_date={cov3.get('first_date')}"
    # 结束日容忍：to_date 是非交易日，last_date 在此之前
    cov4 = store.check_coverage(conn, "SZ000333", "2020-01-02", "2020-01-05")
    assert cov4["covered"], f"非交易日结束日应通过覆盖检查: last_date={cov4.get('last_date')}"
    # 超出容忍范围：from_date 远早于 first_date
    cov5 = store.check_coverage(conn, "SZ000333", "2019-12-01", "2020-01-03")
    assert cov5["covered"] is False, "from_date 超出容忍范围应失败"


def _test_sync_state(conn):
    store.upsert_sync_state(conn, "daily", "SZ000333", "2024-01-31", "ok")
    s = store.read_sync_state(conn, "daily", "SZ000333")
    assert s["status"] == "ok"
    assert s["last_trade_date"] == "2024-01-31"
    # 更新
    store.upsert_sync_state(conn, "daily", "SZ000333", "2024-02-01", "ok")
    s = store.read_sync_state(conn, "daily", "SZ000333")
    assert s["last_trade_date"] == "2024-02-01"
    # 错误
    store.upsert_sync_state(conn, "daily", "SZ000333", None, "error", "Network timeout")
    s = store.read_sync_state(conn, "daily", "SZ000333")
    assert s["status"] == "error"
    assert s["error"] == "Network timeout"
    # 不存在
    assert store.read_sync_state(conn, "daily", "SH600000") is None


def _test_get_db_path():
    path = store.get_db_path()
    assert path.name == "history.sqlite"
    assert path.parent.exists()


if __name__ == "__main__":
    run_all()

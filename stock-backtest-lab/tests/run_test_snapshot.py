"""Snapshot + Point-in-Time 验证测试。"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from backtest_core import store
from backtest_core.snapshot import build_snapshot


def test(desc, fn):
    try:
        fn()
    except Exception as e:
        print(f"FAIL: {desc}")
        import traceback
        traceback.print_exc()
        raise
    print(f"PASS: {desc}")


def run_all():
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    db_path = tmp.name
    tmp.close()
    original = store.get_db_path
    store.get_db_path = lambda: Path(db_path)

    conn = store.get_connection()
    store.init_schema(conn)

    # 准备测试数据（需要 >= 60 个工作日，所以生成 200 天覆盖足够长）
    _seed_stock_data(conn, "SZ000333", days=200, start_date="2023-06-01")
    _seed_index_data(conn, "sh000300", days=200, start_date="2023-06-01")
    _seed_index_data(conn, "sz399006", days=200, start_date="2023-06-01")

    test("build_snapshot 基本流程", lambda: _test_basic_snapshot(conn))
    test("K 线不足 60 天返回 None", lambda: _test_insufficient(conn))
    test("PIT 截断：不含 T 之后数据", lambda: _test_pit_truncation(conn))
    test("PIT 截断：T 日数据包含", lambda: _test_pit_includes_t(conn))

    conn.close()
    os.unlink(db_path)
    store.get_db_path = original

    print("\nALL SNAPSHOT TESTS PASSED")


def _seed_stock_data(conn, symbol, days=120, start_date="2024-01-01"):
    """生成测试用的日线数据。"""
    d = datetime.strptime(start_date, "%Y-%m-%d")
    bars = []
    base = 50.0
    for i in range(days):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        date_str = d.strftime("%Y-%m-%d")
        close = base + i * 0.1
        bars.append({
            "date": date_str,
            "open": close - 0.2,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1000000 + i * 10000,
        })
        d += timedelta(days=1)
    store.insert_daily_bars(conn, bars, symbol)


def _seed_index_data(conn, index_code, days=120, start_date="2024-01-01"):
    """生成测试用的指数数据。"""
    d = datetime.strptime(start_date, "%Y-%m-%d")
    bars = []
    base = 3000.0
    for i in range(days):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        date_str = d.strftime("%Y-%m-%d")
        close = base + i * 0.5
        bars.append({
            "date": date_str,
            "open": close - 5,
            "high": close + 10,
            "low": close - 10,
            "close": close,
        })
        d += timedelta(days=1)
    store.insert_index_bars(conn, bars, index_code)


def _test_basic_snapshot(conn):
    """基本快照构造。"""
    # T 日为数据中的某个日期
    snapshot = build_snapshot(conn, "SZ000333", "2024-03-15", "a")
    assert snapshot is not None
    assert snapshot.symbol == "SZ000333"
    assert snapshot.date == "2024-03-15"
    assert snapshot.data_sufficient
    assert len(snapshot.stock_klines) >= 60
    # 指数应该有数据
    assert "sh000300" in snapshot.index_klines


def _test_insufficient(conn):
    """数据不足 60 天时返回 None。"""
    # 创建一个只有 10 天的股票
    _seed_stock_data(conn, "SH600001", days=10, start_date="2024-05-01")
    snapshot = build_snapshot(conn, "SH600001", "2024-05-15", "a")
    assert snapshot is None  # K 线不足 60 天


def _test_pit_truncation(conn):
    """PIT 截断：快照不应包含 T 之后的日期。"""
    snapshot = build_snapshot(conn, "SZ000333", "2024-02-01", "a")
    assert snapshot is not None
    for k in snapshot.stock_klines:
        assert k["trade_date"] <= "2024-02-01", \
            f"发现未来数据: {k['trade_date']} > 2024-02-01"


def _test_pit_includes_t(conn):
    """T 日的数据应被包含（包括 T 日收盘价）。"""
    snapshot = build_snapshot(conn, "SZ000333", "2024-03-01", "a")
    assert snapshot is not None
    dates = [k["trade_date"] for k in snapshot.stock_klines]
    assert "2024-03-01" in dates, f"T 日 2024-03-01 不在快照中，日期: {dates[:5]}..."


if __name__ == "__main__":
    run_all()

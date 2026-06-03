"""直接运行 sync 模块的测试，mock 外部 fetch 函数。"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# 需要项目根目录来导入 shared 模块
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from backtest_core import store, sync


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

    test("sync_daily_kline 基本流程", lambda: _test_sync_daily(conn))
    test("sync_daily_kline from_date 过滤", lambda: _test_sync_from_date(conn))
    test("sync_index_kline 基本流程", lambda: _test_sync_index(conn))
    test("sync_all_indices 批量同步", lambda: _test_sync_all_indices(conn))
    test("增量同步：已有最新数据时跳过", lambda: _test_incremental_skip(conn))

    conn.close()
    os.unlink(db_path)
    store.get_db_path = original

    print("\nALL SYNC TESTS PASSED")


# mock K 线数据
def _make_kline(start_date, days=100, base_price=50.0):
    """生成模拟 K 线数据。"""
    from datetime import datetime, timedelta
    bars = []
    d = datetime.strptime(start_date, "%Y-%m-%d")
    for i in range(days):
        while d.weekday() >= 5:  # 跳过周末
            d += timedelta(days=1)
        date_str = d.strftime("%Y-%m-%d")
        bars.append({
            "date": date_str,
            "open": base_price + i * 0.1,
            "high": base_price + i * 0.1 + 1.0,
            "low": base_price + i * 0.1 - 0.5,
            "close": base_price + i * 0.1 + 0.5,
            "volume": 1000000,
        })
        d += timedelta(days=1)
    return bars


def _test_sync_daily(conn):
    mock_bars = _make_kline("2024-01-01", days=10)
    with patch("shared.stock_core.kline.fetch_daily_kline", return_value=mock_bars):
        result = sync.sync_daily_kline(conn, "SZ000333", "a", from_date="2024-01-01")
    assert result["status"] == "ok"
    assert result["synced"] >= 5  # 至少有工作日数据
    # 验证数据确实写入
    rows = store.read_daily_bars(conn, "SZ000333")
    assert len(rows) == result["synced"]
    # 验证 sync_state
    s = store.read_sync_state(conn, "daily", "SZ000333")
    assert s is not None
    assert s["status"] == "ok"


def _test_sync_from_date(conn):
    """验证 from_date 过滤逻辑。"""
    conn.execute("DELETE FROM daily_bars")
    conn.execute("DELETE FROM sync_state")
    conn.commit()

    mock_bars = _make_kline("2024-01-01", days=20)
    with patch("shared.stock_core.kline.fetch_daily_kline", return_value=mock_bars):
        result = sync.sync_daily_kline(conn, "SZ000333", "a", from_date="2024-01-15")
    assert result["status"] == "ok"
    rows = store.read_daily_bars(conn, "SZ000333")
    # 所有写入的日期都应 >= 2024-01-15
    for r in rows:
        assert r["trade_date"] >= "2024-01-15"


def _test_sync_index(conn):
    mock_bars = _make_kline("2024-01-01", days=10)
    with patch("shared.stock_core.market_regime.fetch_index_daily", return_value=mock_bars):
        result = sync.sync_index_kline(conn, "sh000300", "沪深300", from_date="2024-01-01")
    assert result["status"] == "ok"
    assert result["synced"] >= 5
    rows = store.read_index_bars(conn, "sh000300")
    assert len(rows) == result["synced"]


def _test_sync_all_indices(conn):
    mock_bars = _make_kline("2024-01-01", days=5)
    with patch("shared.stock_core.market_regime.fetch_index_daily", return_value=mock_bars):
        results = sync.sync_all_indices(conn, from_date="2024-01-01", count=100)
    assert len(results) == 4
    for r in results:
        assert r["status"] == "ok"


def _test_incremental_skip(conn):
    """增量同步：sync_state 已覆盖最新日期时，后续 sync 应跳过。"""
    from datetime import date, timedelta
    conn.execute("DELETE FROM daily_bars")
    conn.execute("DELETE FROM sync_state")
    conn.commit()

    # 生成近期数据（结尾距今天 <= 2 天），使其满足增量跳过条件
    today = date.today()
    start = today - timedelta(days=20)
    mock_bars = _make_kline(start.strftime("%Y-%m-%d"), days=14)

    with patch("shared.stock_core.kline.fetch_daily_kline", return_value=mock_bars):
        result1 = sync.sync_daily_kline(conn, "SZ000333", "a",
                                        from_date=start.strftime("%Y-%m-%d"))
    assert result1["status"] == "ok"
    assert result1["synced"] > 0

    # 第二次 sync：sync_state.last_trade_date 距今天 <= 2 天，应跳过
    with patch("shared.stock_core.kline.fetch_daily_kline") as mock_fetch:
        result2 = sync.sync_daily_kline(conn, "SZ000333", "a",
                                        from_date=start.strftime("%Y-%m-%d"))
    mock_fetch.assert_not_called()
    assert result2["status"] == "ok"
    assert result2.get("skipped", 0) > 0


if __name__ == "__main__":
    run_all()

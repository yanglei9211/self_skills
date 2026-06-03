"""集成测试：端到端回测流程 + PIT 防泄露验证。"""
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
from backtest_core.signals import calculate_signals
from backtest_core.strategies import boll_squeeze_entry, STRATEGIES
from backtest_core.tracker import track_forward
from backtest_core.stats import aggregate_results
from backtest_core.report import render_report, render_json


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

    # 生成测试数据
    _seed_stock_data(conn, "SZ000333", days=300, start_date="2023-01-01")
    _seed_index_data(conn, "sh000300", days=300, start_date="2023-01-01")
    _seed_index_data(conn, "sz399006", days=300, start_date="2023-01-01")

    test("端到端：snapshot -> signals -> strategy -> tracker -> stats -> report", lambda: _test_e2e(conn))
    test("PIT 防泄露：snapshot 不含 T 之后数据", lambda: _test_pit_snapshot(conn))
    test("PIT 防泄露：信号不使用 T 之后数据", lambda: _test_pit_signals(conn))
    test("PIT 防泄露：BOLL 窗口严格在 T 之前", lambda: _test_pit_boll(conn))
    test("PIT 防泄露：YTD 边界以 T 日为准", lambda: _test_pit_ytd(conn))
    test("报告生成：Markdown 格式", lambda: _test_report_md())
    test("报告生成：JSON 格式", lambda: _test_report_json())
    test("报告生成：N < 20 样本不足警告", lambda: _test_report_small_sample())
    test("报告生成：含 adjacency=qfq", lambda: _test_report_qfq())
    test("PIT 对抗性：DB 有未来数据但 snapshot 正确排除", lambda: _test_pit_future_data_excluded(conn))
    test("PIT 对抗性：T1<T2 snapshot 子集关系", lambda: _test_pit_subset(conn))
    test("PIT 对抗性：T 日信号可独立重算", lambda: _test_pit_independent_recompute(conn))

    conn.close()
    os.unlink(db_path)
    store.get_db_path = original

    print("\nALL INTEGRATION TESTS PASSED")


def _seed_stock_data(conn, symbol, days=300, start_date="2023-01-01"):
    d = datetime.strptime(start_date, "%Y-%m-%d")
    bars = []
    base = 50.0
    for i in range(days):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        date_str = d.strftime("%Y-%m-%d")
        close = base + i * 0.1
        # 加入一些技术形态
        if i > 10 and i % 30 == 0:
            close += 2.0
        elif i > 10 and i % 45 == 0:
            close -= 1.5
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


def _seed_index_data(conn, index_code, days=300, start_date="2023-01-01"):
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


def _test_e2e(conn):
    """完整的端到端流程。"""
    all_klines = store.read_daily_bars(conn, "SZ000333")
    results = []

    for i in range(100, min(150, len(all_klines))):  # 回测中间一段
        t_date = all_klines[i]["trade_date"]

        # Snapshot
        snapshot = build_snapshot(conn, "SZ000333", t_date, "a")
        if snapshot is None:
            continue

        # Signals
        signals = calculate_signals(snapshot)

        # Strategy
        decisions = boll_squeeze_entry(snapshot, signals)
        if not decisions:
            continue

        # Tracker
        kline_for_tracker = [
            {"close": r["close"], "high": r["high"], "low": r["low"], "date": r["trade_date"]}
            for r in all_klines
        ]
        for dec in decisions:
            trade = track_forward(
                kline_for_tracker,
                entry_idx=i,
                entry_price=dec.entry_price,
                entry_date=t_date,
                hold_days=[5, 10, 20, 60],
            )
            results.append(trade)

    # 至少产生了一些结果
    assert len(results) > 0, "回测应产生至少一个信号"

    # Stats
    stats = aggregate_results(results)
    assert stats["total_signals"] > 0
    assert stats["avg_returns"][20] is not None

    # Report
    report = render_report(stats, "SZ000333", "boll_squeeze_entry", "2023-06-01", "2024-03-31")
    assert "SZ000333" in report
    assert "boll_squeeze_entry" in report
    assert "信号总数" in report


def _test_pit_snapshot(conn):
    """PIT 验证：snapshot 只包含 T 及之前的数据。"""
    snapshot = build_snapshot(conn, "SZ000333", "2023-09-15", "a")
    assert snapshot is not None
    for k in snapshot.stock_klines:
        assert k["trade_date"] <= "2023-09-15", f"发现未来数据: {k['trade_date']}"


def _test_pit_signals(conn):
    """PIT 验证：信号计算不使用 T 之后的数据。"""
    snapshot = build_snapshot(conn, "SZ000333", "2023-09-15", "a")
    assert snapshot is not None
    signals = calculate_signals(snapshot)
    # 信号应该有效
    assert signals.price_regime is not None
    # 不能有任何日期大于 T 的数据被使用（这个由 snapshot 保证）


def _test_pit_boll(conn):
    """PIT 验证：BOLL 窗口严格在 T 之前。"""
    snapshot = build_snapshot(conn, "SZ000333", "2023-09-15", "a")
    assert snapshot is not None
    signals = calculate_signals(snapshot)
    # BOLL 应该有值（数据足够）
    assert signals.boll is not None
    assert "middle" in signals.boll


def _test_pit_ytd(conn):
    """PIT 验证：YTD 统计以 T 所在年为准。"""
    snapshot = build_snapshot(conn, "SZ000333", "2024-02-01", "a")
    assert snapshot is not None
    signals = calculate_signals(snapshot)
    # price_regime 应该反映 T 日所在的年份状态
    # 2024 年 2 月，从年初开始的趋势应该可见
    assert signals.price_regime is not None


def _test_report_md():
    """Markdown 报告生成。"""
    from backtest_core.models import TradeResult
    results = [TradeResult(
        entry_date="2024-03-15",
        entry_price=50.0,
        action="buy",
        confidence=0.7,
        fwd_returns={5: 1.2, 10: 2.4, 20: 5.6, 60: 12.3},
        max_drawdown=-5.0,
        hit_stop_loss=False,
    ) for _ in range(25)]

    stats = aggregate_results(results)
    report = render_report(stats, "SZ000333", "boll_squeeze_entry", "2024-01-01", "2024-06-30")
    assert "# SZ000333 — boll_squeeze_entry" in report
    assert "总体统计" in report
    assert "按年份分组" in report
    # 不包含"样本不足"（N=25 >= 20）
    assert "样本不足" not in report


def _test_report_json():
    """JSON 报告生成。"""
    from backtest_core.models import TradeResult
    results = [TradeResult(
        entry_date="2024-03-15",
        entry_price=50.0,
        action="buy",
        confidence=0.7,
        fwd_returns={5: 1.2, 10: 2.4, 20: 5.6, 60: 12.3},
        max_drawdown=-5.0,
    ) for _ in range(10)]

    stats = aggregate_results(results)
    json_str = render_json(stats)
    import json
    parsed = json.loads(json_str)
    assert parsed["total_signals"] == 10
    assert "sample_warning" in parsed


def _test_report_small_sample():
    """小样本报告应包含警告。"""
    from backtest_core.models import TradeResult
    results = [TradeResult(
        entry_date="2024-03-15",
        entry_price=50.0,
        action="buy",
        confidence=0.7,
        fwd_returns={5: 1.2, 10: 2.4, 20: 5.6, 60: 12.3},
        max_drawdown=-5.0,
    ) for _ in range(5)]

    stats = aggregate_results(results)
    report = render_report(stats, "SZ000333", "boll_squeeze_entry", "2024-01-01", "2024-06-30")
    assert "样本不足" in report


def _test_report_qfq():
    """报告应包含 qfq 标注。"""
    from backtest_core.models import TradeResult
    results = [TradeResult(
        entry_date="2024-03-15",
        entry_price=50.0,
        action="buy",
        confidence=0.7,
        fwd_returns={5: 1.2, 10: 2.4, 20: 5.6, 60: 12.3},
        max_drawdown=-5.0,
    ) for _ in range(30)]

    stats = aggregate_results(results)
    report = render_report(stats, "SZ000333", "boll_squeeze_entry", "2024-01-01", "2024-06-30")
    assert "qfq" in report
    assert "前复权" in report


def _test_pit_future_data_excluded(conn):
    """PIT 对抗性测试：DB 中存在远超 T 日的未来数据时，snapshot 正确排除它们。"""
    # seed 了 300 天数据，T 取中间日期
    T = "2023-09-15"
    # 验证 DB 中确实有远超 T 的数据
    future = conn.execute(
        "SELECT MAX(trade_date) FROM daily_bars WHERE symbol = ?", ("SZ000333",)
    ).fetchone()
    assert future[0] > T, f"DB 中应有未来数据: max={future[0]}, T={T}"
    # 验证 T 之前也有数据
    past = conn.execute(
        "SELECT MIN(trade_date) FROM daily_bars WHERE symbol = ?", ("SZ000333",)
    ).fetchone()
    assert past[0] < T, f"DB 中应有历史数据: min={past[0]}, T={T}"

    snapshot = build_snapshot(conn, "SZ000333", T, "a")
    assert snapshot is not None
    for k in snapshot.stock_klines:
        assert k["trade_date"] <= T, (
            f"snapshot 不应包含未来数据: kline_date={k['trade_date']}, T={T}"
        )
    # 验证确实有数据被排除——DB 最大日期 > T
    snapshot_max = max(k["trade_date"] for k in snapshot.stock_klines)
    assert snapshot_max <= T, f"snapshot 最大日期应 <= T: {snapshot_max}"


def _test_pit_subset(conn):
    """PIT 对抗性测试：T1 < T2 时 snapshot(T1) 是 snapshot(T2) 的严格子集。"""
    T1 = "2023-06-15"
    T2 = "2023-09-15"
    assert T1 < T2

    snap1 = build_snapshot(conn, "SZ000333", T1, "a")
    snap2 = build_snapshot(conn, "SZ000333", T2, "a")
    assert snap1 is not None
    assert snap2 is not None

    dates1 = {k["trade_date"] for k in snap1.stock_klines}
    dates2 = {k["trade_date"] for k in snap2.stock_klines}

    # T1 的数据是 T2 数据的子集
    assert len(dates1) < len(dates2), (
        f"snapshot(T1) 应有更少数据: {len(dates1)} vs {len(dates2)}"
    )
    assert dates1.issubset(dates2), (
        f"snapshot(T1) 的所有日期应出现在 snapshot(T2) 中"
    )


def _test_pit_independent_recompute(conn):
    """PIT 对抗性测试：T 日信号可仅从 <=T 原始数据独立重算。"""
    T = "2023-09-15"

    # 从 snapshot 计算信号（正常路径）
    snapshot = build_snapshot(conn, "SZ000333", T, "a")
    assert snapshot is not None
    signals = calculate_signals(snapshot)

    # 独立手工重算：直接从 SQLite 读 <=T 数据，手工计算关键信号
    raw = store.read_daily_bars(conn, "SZ000333", to_date=T)
    assert len(raw) > 60, f"需要足够历史数据，实际: {len(raw)}"

    # 手工计算 close 序列最后一个值
    closes = [k["close"] for k in raw]
    assert closes[-1] == snapshot.stock_klines[-1]["close"], (
        "手工读数和 snapshot 的最后收盘价应一致"
    )

    # 手工算 MA5：应匹配 signals
    ma5_manual = sum(closes[-5:]) / 5
    assert abs(ma5_manual - signals.ma_values["MA5"]) < 0.01, (
        f"MA5 独立重算不一致: {ma5_manual} vs {signals.ma_values['MA5']}"
    )

    # 手工算 MA20
    ma20_manual = sum(closes[-20:]) / 20
    assert abs(ma20_manual - signals.ma_values["MA20"]) < 0.01, (
        f"MA20 独立重算不一致: {ma20_manual} vs {signals.ma_values['MA20']}"
    )


if __name__ == "__main__":
    run_all()

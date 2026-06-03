"""Strategies + Stats 测试。"""
import os
import sys

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from backtest_core.models import BacktestSnapshot, SignalVector, Decision, TradeResult
from backtest_core.strategies import (
    boll_squeeze_entry,
    ma_breakout,
    price_new_high,
    STRATEGIES,
)
from backtest_core.stats import aggregate_results


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
    test("boll_squeeze_entry 正常触发", lambda: _test_boll_squeeze())
    test("boll_squeeze_entry 放量下跌不触发", lambda: _test_boll_falling_volume())
    test("boll_squeeze_entry 无 squeeze 不触发", lambda: _test_boll_no_squeeze())
    test("ma_breakout 多头排列触发", lambda: _test_ma_breakout())
    test("ma_breakout 无突破不触发", lambda: _test_ma_no_breakout())
    test("price_new_high 触发", lambda: _test_price_new_high())
    test("stats 基本聚合", lambda: _test_aggregate_results())
    test("stats N < 20 样本不足", lambda: _test_small_sample())
    test("stats 按年份分组", lambda: _test_by_year())
    test("STRATEGIES 注册表", lambda: _test_strategy_registry())

    print("\nALL STRATEGIES & STATS TESTS PASSED")


def _make_kline(n, base=50.0, trend=0.1):
    """生成模拟日线。"""
    result = []
    for i in range(n):
        close = base + i * trend
        result.append({
            "date": f"2024-01-{(i + 1):02d}",
            "trade_date": f"2024-01-{(i + 1):02d}",
            "open": close - 0.2,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1000000 + i * 10000,
        })
    return result


def _make_snapshot(klines, market="a", date="2024-03-15"):
    """构造测试用 BacktestSnapshot。"""
    return BacktestSnapshot(
        date=date,
        symbol="SZ000333",
        market=market,
        stock_klines=klines,
        index_klines={},
        data_sufficient=len(klines) >= 60,
    )


def _make_signals(klines, **overrides):
    """构造测试用 SignalVector。"""
    closes = [k["close"] for k in klines]
    n = len(closes)

    defaults = {
        "date": "2024-03-15",
        "symbol": "SZ000333",
        "price_regime": "IN_RANGE",
        "market_regime_a": "NEUTRAL",
        "market_regime_hk": "NEUTRAL",
        "boll": {
            "middle": 50.0,
            "upper": 52.0,
            "lower": 48.0,
            "bandwidth_pct": 8.0,
            "position_pct": 50.0,
            "squeeze": True,
            "volatility_pct": 10.0,
        },
        "volume_price": {
            "label": "rising_with_volume",
            "vol_5d_avg": 1000000,
            "vol_20d_avg": 800000,
            "vol_ratio": 1.25,
            "price_5d_chg_pct": 2.5,
            "recent_days": n,
        },
        "ma_values": {
            "MA5": closes[-1] - 0.5,
            "MA10": closes[-1] - 1.0,
            "MA20": closes[-1] - 1.5,
            "MA60": closes[-1] - 3.0,
        },
        "rsi": 60.0,
    }
    defaults.update(overrides)
    return SignalVector(**defaults)


def _test_boll_squeeze():
    kline = _make_kline(100, base=50.0, trend=0.05)
    snapshot = _make_snapshot(kline)
    signals = _make_signals(kline, boll={"middle": 50.0, "upper": 52.0, "lower": 48.0, "bandwidth_pct": 4.0, "position_pct": 50.0, "squeeze": True, "volatility_pct": 5.0})

    decisions = boll_squeeze_entry(snapshot, signals)
    assert len(decisions) >= 1
    assert decisions[0].action == "buy"
    assert decisions[0].confidence > 0


def _test_boll_falling_volume():
    """放量下跌时不触发入场。"""
    kline = _make_kline(100, base=50.0, trend=0.05)
    snapshot = _make_snapshot(kline)
    signals = _make_signals(
        kline,
        boll={"middle": 50.0, "upper": 52.0, "lower": 48.0, "bandwidth_pct": 4.0, "position_pct": 50.0, "squeeze": True, "volatility_pct": 5.0},
        volume_price={"label": "falling_with_volume", "vol_5d_avg": 1200000, "vol_20d_avg": 800000, "vol_ratio": 1.5, "price_5d_chg_pct": -3.0, "recent_days": 100},
    )

    decisions = boll_squeeze_entry(snapshot, signals)
    assert len(decisions) == 0  # 放量下跌不触发


def _test_boll_no_squeeze():
    """无 squeeze 时不触发。"""
    kline = _make_kline(100, base=50.0, trend=0.05)
    snapshot = _make_snapshot(kline)
    signals = _make_signals(
        kline,
        boll={"middle": 50.0, "upper": 52.0, "lower": 48.0, "bandwidth_pct": 8.0, "position_pct": 50.0, "squeeze": False, "volatility_pct": 5.0},
    )

    decisions = boll_squeeze_entry(snapshot, signals)
    assert len(decisions) == 0


def _test_ma_breakout():
    kline = _make_kline(100, base=50.0, trend=0.1)
    snapshot = _make_snapshot(kline)
    # 当前价格远高于各均线（突破状态）
    last_close = kline[-1]["close"]
    signals = _make_signals(
        kline,
        ma_values={"MA5": last_close - 2, "MA10": last_close - 3, "MA20": last_close - 4, "MA60": last_close - 6},
        volume_price={"label": "rising_with_volume", "vol_5d_avg": 1200000, "vol_20d_avg": 800000, "vol_ratio": 1.5, "price_5d_chg_pct": 3.0, "recent_days": 100},
    )

    decisions = ma_breakout(snapshot, signals)
    assert len(decisions) >= 1
    assert decisions[0].action == "buy"


def _test_ma_no_breakout():
    kline = _make_kline(100, base=50.0, trend=-0.1)
    snapshot = _make_snapshot(kline)
    last_close = kline[-1]["close"]
    # 价格在所有均线之下
    signals = _make_signals(
        kline,
        ma_values={"MA5": last_close + 0.5, "MA10": last_close + 1.0, "MA20": last_close + 2.0, "MA60": last_close + 5.0},
    )

    decisions = ma_breakout(snapshot, signals)
    # 价格低于所有均线，不应产生 buy 信号（所有 reasons 为空）
    assert len(decisions) == 0


def _test_price_new_high():
    kline = _make_kline(100, base=50.0, trend=0.1)
    snapshot = _make_snapshot(kline)
    signals = _make_signals(
        kline,
        price_regime="NEW_YTD_HIGH",
        rsi=55.0,
        volume_price={"label": "rising_with_volume", "vol_5d_avg": 1200000, "vol_20d_avg": 800000, "vol_ratio": 1.5, "price_5d_chg_pct": 3.0, "recent_days": 100},
    )

    decisions = price_new_high(snapshot, signals)
    assert len(decisions) >= 1
    assert decisions[0].action == "buy"


def _test_aggregate_results():
    """基本统计聚合。"""
    results = []
    for i in range(30):
        r = TradeResult(
            entry_date=f"2024-{(i % 6) + 1:02d}-15",
            entry_price=50.0,
            action="buy",
            confidence=0.7,
            fwd_returns={
                5: 1.0 + i * 0.1,
                10: 2.0 + i * 0.1,
                20: 3.0 + i * 0.1,
                60: 5.0 + i * 0.2,
            },
            max_drawdown=-5.0 - i * 0.1,
            hit_stop_loss=(i % 5 == 0),
            hit_take_profit=(i % 7 == 0),
            data_incomplete=False,
        )
        results.append(r)

    stats = aggregate_results(results)
    assert stats["total_signals"] == 30
    assert stats["win_rate_20d_pct"] is not None
    assert stats["avg_returns"][20] is not None
    # 样本量 >= 20，不应有 warning
    assert "sample_warning" not in stats


def _test_small_sample():
    """小样本警告。"""
    results = [TradeResult(
        entry_date="2024-03-15",
        entry_price=50.0,
        action="buy",
        confidence=0.7,
        fwd_returns={5: 1.0, 10: 2.0, 20: 3.0, 60: 5.0},
        max_drawdown=-5.0,
    )]

    stats = aggregate_results(results)
    assert stats["total_signals"] == 1
    assert stats["sample_warning"] is not None
    assert "样本不足" in stats["sample_warning"]


def _test_by_year():
    """按年份分组。"""
    results = []
    for i in range(40):
        year = 2023 if i < 15 else 2024
        results.append(TradeResult(
            entry_date=f"{year}-06-15",
            entry_price=50.0,
            action="buy",
            confidence=0.7,
            fwd_returns={5: 1.0, 10: 2.0, 20: 3.0, 60: 5.0},
            max_drawdown=-5.0,
        ))

    stats = aggregate_results(results)
    assert "by_year" in stats
    assert "2023" in stats["by_year"]
    assert "2024" in stats["by_year"]
    assert stats["by_year"]["2023"]["n"] == 15
    assert stats["by_year"]["2024"]["n"] == 25
    # 2023 年样本不足应有警告
    assert stats["by_year"]["2023"]["sample_warning"] is not None
    # 2024 年样本 >= 20，无警告
    assert stats["by_year"]["2024"]["sample_warning"] is None


def _test_strategy_registry():
    """策略注册表包含所有 P0 策略。"""
    assert "boll_squeeze_entry" in STRATEGIES
    assert "ma_breakout" in STRATEGIES
    assert "price_new_high" in STRATEGIES
    assert callable(STRATEGIES["boll_squeeze_entry"])


if __name__ == "__main__":
    run_all()

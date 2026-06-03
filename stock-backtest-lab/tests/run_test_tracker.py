"""Tracker 测试：前向收益、回撤、止损/止盈触发。"""
import os
import sys

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from backtest_core.tracker import track_forward, batch_track


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
    test("track_forward 上涨场景", lambda: _test_uptrend())
    test("track_forward 下跌场景", lambda: _test_downtrend())
    test("track_forward 震荡场景", lambda: _test_sideways())
    test("止损触发", lambda: _test_stop_loss())
    test("止盈触发", lambda: _test_take_profit())
    test("trailing stop 触发", lambda: _test_trailing_stop())
    test("数据不足标记", lambda: _test_data_incomplete())
    test("batch_track 批量追踪", lambda: _test_batch_track())

    print("\nALL TRACKER TESTS PASSED")


def _make_kline(n, base=50.0, trend=0.0, noise=0.0):
    """生成模拟日线数据。"""
    import random
    random.seed(42)
    result = []
    for i in range(n):
        t = i if i < n // 2 else n - i - 1  # 震荡
        close = base + i * trend + t * noise * (1 if random.random() > 0.5 else -1)
        close = max(close, 1.0)  # 价格不能为负
        result.append({
            "date": f"2024-01-{(i + 1):02d}",
            "open": close - 0.2,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1000000,
        })
    return result


def _test_uptrend():
    # 持续上涨 60 天
    kline = _make_kline(100, base=50.0, trend=0.2)
    entry_idx = 30
    entry_price = kline[entry_idx]["close"]

    result = track_forward(kline, entry_idx, entry_price)
    # 上涨趋势中，各持有期应该为正收益
    assert result.fwd_returns[5] is not None
    assert result.fwd_returns[20] is not None
    assert result.fwd_returns[5] > 0  # 5 日正收益
    assert result.fwd_returns[20] > 0  # 20 日正收益
    # 上涨中最大回撤应该很小或为 0
    assert result.max_drawdown <= 0  # 回撤为 0 或正很小
    assert not result.hit_stop_loss


def _test_downtrend():
    # 持续下跌
    kline = _make_kline(100, base=50.0, trend=-0.2)
    entry_idx = 30
    entry_price = kline[entry_idx]["close"]

    result = track_forward(kline, entry_idx, entry_price)
    assert result.fwd_returns[5] is not None
    assert result.fwd_returns[5] < 0  # 下跌
    assert result.max_drawdown < 0  # 有回撤


def _test_sideways():
    # 震荡横盘
    kline = _make_kline(100, base=50.0, trend=0.0, noise=0.3)
    entry_idx = 30
    entry_price = kline[entry_idx]["close"]

    result = track_forward(kline, entry_idx, entry_price)
    # 横盘：收益应该接近 0
    assert abs(result.fwd_returns[5] or 0) < 5


def _test_stop_loss():
    # 先涨后暴跌，触发止损
    kline = _make_kline(80, base=50.0, trend=0.05)
    # 在某个位置之后手动制造暴跌
    for i in range(40, 60):
        kline[i]["close"] = kline[i]["close"] * (1.0 - 0.05 * (i - 39))

    entry_idx = 35
    entry_price = kline[entry_idx]["close"]

    result = track_forward(kline, entry_idx, entry_price, stop_loss_pct=8.0)
    # 持续下跌应触发止损
    assert result.hit_stop_loss
    assert result.max_drawdown < 0  # 有回撤


def _test_take_profit():
    # 快速上涨触发止盈
    kline = _make_kline(80, base=50.0, trend=0.1)
    # 制造跳涨
    for i in range(40, 55):
        kline[i]["close"] = kline[i]["close"] * (1.0 + 0.03 * (i - 39))

    entry_idx = 35
    entry_price = kline[entry_idx]["close"]

    result = track_forward(kline, entry_idx, entry_price, take_profit_pct=15.0)
    assert result.hit_take_profit


def _test_trailing_stop():
    # 先涨后跌，触发 trailing stop
    kline = _make_kline(100, base=50.0, trend=0.1)
    # 从 50 位置开始暴涨，然后快速回落
    peak_region_start = 40
    for i in range(peak_region_start, peak_region_start + 15):
        kline[i]["close"] = kline[i]["close"] * (1.0 + 0.05 * (i - peak_region_start + 1))
    # 然后快速回落
    for i in range(peak_region_start + 15, peak_region_start + 30):
        kline[i]["close"] = kline[i - 1]["close"] * 0.95

    entry_idx = 35
    entry_price = kline[entry_idx]["close"]

    result = track_forward(kline, entry_idx, entry_price, trailing_stop_pct=15.0)
    # 快速回撤应触发 trailing stop
    assert result.hit_stop_loss  # trailing stop 触发算 hit_stop_loss


def _test_data_incomplete():
    # 数据不足以覆盖所有持有期
    kline = _make_kline(50, base=50.0, trend=0.1)
    entry_idx = 20  # T 日在 kline 中部
    entry_price = kline[entry_idx]["close"]

    result = track_forward(kline, entry_idx, entry_price, hold_days=[5, 10, 20, 60])
    # 5/10/20 天有数据，60 天数据不足
    assert result.fwd_returns[5] is not None  # 5 天有数据
    assert result.fwd_returns[10] is not None  # 10 天有数据
    assert result.fwd_returns[60] is None  # 60 天数据不足（需要到 index 80）
    assert result.data_incomplete


def _test_batch_track():
    kline = _make_kline(100, base=50.0, trend=0.1)
    entries = [
        {"entry_idx": 10, "entry_price": kline[10]["close"], "entry_date": kline[10]["date"], "action": "buy", "confidence": 0.8},
        {"entry_idx": 30, "entry_price": kline[30]["close"], "entry_date": kline[30]["date"], "action": "buy", "confidence": 0.9},
        {"entry_idx": 50, "entry_price": kline[50]["close"], "entry_date": kline[50]["date"], "action": "buy", "confidence": 0.7},
    ]
    results = batch_track(kline, entries)
    assert len(results) == 3
    for r in results:
        assert r.fwd_returns[5] is not None
        assert r.entry_date != ""


if __name__ == "__main__":
    run_all()

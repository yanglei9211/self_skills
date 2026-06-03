"""Signals + Point-in-Time 信号测试。"""
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
from backtest_core.signals import (
    calculate_signals,
    _calculate_price_regime,
    _calculate_bollinger,
    _calculate_ma_values,
    _calculate_rsi,
    _calculate_volume_price,
    _calculate_market_regime_a,
    _calculate_market_regime_hk,
    _classify_regime,
    _summarize_single_index,
)


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

    # 准备测试数据
    _seed_stock_data(conn, "SZ000333", days=200, start_date="2023-06-01")
    _seed_index_data(conn, "sh000300", days=300, start_date="2023-01-01")
    _seed_index_data(conn, "sz399006", days=300, start_date="2023-01-01")
    _seed_index_data(conn, "hkHSI", days=300, start_date="2023-01-01")
    _seed_index_data(conn, "hkHSTECH", days=300, start_date="2023-01-01")

    test("price_regime 基本判定", lambda: _test_price_regime())
    test("BOLL 计算", lambda: _test_bollinger())
    test("BOLL 不足 20 天返回 None", lambda: _test_boll_insufficient())
    test("MA 值计算", lambda: _test_ma_values())
    test("MA60 不足 60 天不出现在结果中", lambda: _test_ma_insufficient())
    test("RSI 计算", lambda: _test_rsi())
    test("RSI 不足 15 天返回 None", lambda: _test_rsi_insufficient())
    test("量价分析", lambda: _test_volume_price())
    test("market_regime_a 分类", lambda: _test_market_regime_a())
    test("market_regime 不足 200 天返回 None", lambda: _test_regime_insufficient())
    test("calculate_signals 集成", lambda: _test_calculate_signals(conn))
    test("PIT：窗口不足信号返回 None", lambda: _test_pit_insufficient(conn))

    conn.close()
    os.unlink(db_path)
    store.get_db_path = original

    print("\nALL SIGNALS TESTS PASSED")


def _seed_stock_data(conn, symbol, days=200, start_date="2023-06-01"):
    d = datetime.strptime(start_date, "%Y-%m-%d")
    bars = []
    base = 50.0
    for i in range(days):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        date_str = d.strftime("%Y-%m-%d")
        close = base + i * 0.1
        vol = 1000000
        # 加入一些波动
        if i > 0 and i % 5 == 0:
            close = base + i * 0.1 + 2.0  # 偶尔跳空
        bars.append({
            "date": date_str,
            "open": close - 0.2,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": vol + i * 10000,
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


def _make_simple_klines(n, base=50.0, trend=0.1):
    """生成简单 K 线用于单元测试。"""
    result = []
    for i in range(n):
        close = base + i * trend
        result.append({
            "date": f"2024-0{(i // 30) + 1:01d}-{(i % 28 + 1):02d}",
            "open": close - 0.2,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1000000 + i * 1000,
        })
    return result


def _test_price_regime():
    # 普通上涨趋势，不应为极端 regime
    kline = _make_simple_klines(100, base=50.0, trend=0.1)
    regime = _calculate_price_regime(kline)
    assert regime is not None
    # 趋势上涨：current close 应接近或等于 all-time high
    assert "HIGH" in regime or regime == "NEAR_YTD_HIGH" or regime == "IN_RANGE"

    # 如果在最后一天是历史新高
    max_high = max(k["high"] for k in kline)
    kline[-1]["high"] = max_high + 10
    kline[-1]["close"] = max_high + 5
    regime2 = _calculate_price_regime(kline)
    assert "NEW" in regime2 or "HIGH" in regime2


def _test_bollinger():
    kline = _make_simple_klines(25, base=50.0, trend=0.05)
    result = _calculate_bollinger(kline)
    assert result is not None
    assert "middle" in result
    assert "upper" in result
    assert "lower" in result
    assert "bandwidth_pct" in result
    assert "squeeze" in result


def _test_boll_insufficient():
    kline = _make_simple_klines(15)  # 不足 20 天
    result = _calculate_bollinger(kline)
    assert result is None


def _test_ma_values():
    closes = [50.0 + i * 0.1 for i in range(100)]
    result = _calculate_ma_values(closes)
    assert "MA5" in result
    assert "MA10" in result
    assert "MA20" in result
    assert "MA60" in result
    # 验证 MA5 值
    expected_ma5 = sum(closes[-5:]) / 5
    assert abs(result["MA5"] - expected_ma5) < 0.01


def _test_ma_insufficient():
    closes = [50.0 + i * 0.1 for i in range(30)]  # 不足 60 天
    result = _calculate_ma_values(closes)
    assert "MA5" in result
    assert "MA10" in result
    assert "MA20" in result
    assert "MA60" not in result  # 不足 60 天不出现在结果中


def _test_rsi():
    # 持续上涨的趋势，RSI 应该偏高
    closes = [50.0 + i * 0.3 for i in range(30)]
    result = _calculate_rsi(closes)
    assert result is not None
    assert 0 <= result <= 100

    # 持续下跌的趋势，RSI 应该偏低
    closes2 = [50.0 - i * 0.3 for i in range(30)]
    result2 = _calculate_rsi(closes2)
    assert result2 is not None
    assert result2 < result  # 下跌 RSI 应低于上涨 RSI


def _test_rsi_insufficient():
    closes = [50.0 + i * 0.1 for i in range(10)]  # 不足 15 天
    result = _calculate_rsi(closes)
    assert result is None


def _test_volume_price():
    # 需要 >= 5 根 K 线
    kline = _make_simple_klines(20, base=50.0, trend=0.1)
    result = _calculate_volume_price(kline)
    assert result is not None
    assert "label" in result
    assert result["recent_days"] == 20

    # 不足 5 根 K 线
    kline2 = _make_simple_klines(3)
    result2 = _calculate_volume_price(kline2)
    assert result2 is not None
    assert result2["label"] == "insufficient"


def _test_market_regime_a():
    # 构造持续上涨的指数 K 线（RISK_ON 条件：距 52w 高 >= -3% AND 站上 MA200）
    index_klines = {}
    for code in ["sh000300", "sz399006"]:
        kline = _make_simple_klines(300, base=3000.0, trend=0.5)
        # 最后一天：距历史高点很近
        max_high = max(k["high"] for k in kline)
        kline[-1]["close"] = max_high * 0.99  # 距高点 -1%
        kline[-1]["high"] = max_high
        index_klines[code] = kline

    regime = _calculate_market_regime_a(index_klines)
    # 持续上涨且距高点近：应为 RISK_ON
    assert regime == "RISK_ON"


def _test_regime_insufficient():
    # 只有 100 根 K 线，不足 200 天 MA200
    index_klines = {}
    for code in ["sh000300", "sz399006"]:
        kline = _make_simple_klines(100, base=3000.0, trend=0.5)
        index_klines[code] = kline

    regime = _calculate_market_regime_a(index_klines)
    assert regime is None  # 需要 >= 200 天数据


def _test_calculate_signals(conn):
    """集成测试：从数据库构建 snapshot 并计算信号。"""
    snapshot = build_snapshot(conn, "SZ000333", "2024-01-31", "a")
    assert snapshot is not None
    signals = calculate_signals(snapshot)
    assert signals.date == "2024-01-31"
    assert signals.symbol == "SZ000333"
    assert signals.price_regime is not None
    assert signals.boll is not None
    assert signals.ma_values is not None
    assert signals.rsi is not None
    assert signals.volume_price is not None


def _test_pit_insufficient(conn):
    """PIT 验证：只有少量数据时，窗口不足的信号返回 None。"""
    # 创建只有 15 天的股票
    _seed_stock_data(conn, "SH600002", days=15, start_date="2024-06-01")
    snapshot = build_snapshot(conn, "SH600002", "2024-06-20", "a")
    assert snapshot is None  # K 线不足 60 天，整个 snapshot 应返回 None


if __name__ == "__main__":
    run_all()

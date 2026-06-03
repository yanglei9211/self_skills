"""
P0 信号计算器：从 BacktestSnapshot 计算所有技术信号。

核心约束：
  - 所有计算只基于 T 及之前的数据（snapshot 已保证）
  - 窗口不足时返回 None，不强行计算
  - 不访问网络，不依赖外部 API
  - BOLL 需 >= 20 天，RSI 需 >= 15 天，MA60 需 >= 60 天，market_regime MA200 需 >= 200 天
"""

from __future__ import annotations

import math
from typing import Any


def calculate_signals(snapshot) -> "SignalVector":
    """从 snapshot 计算所有 P0 信号，返回 SignalVector。

    这是信号计算的主入口，内部调用各个专项计算函数。
    """
    from .models import SignalVector

    # 将 SQLite 的 trade_date 字段统一为 date，保持与 kline.py 接口一致
    klines = []
    for k in snapshot.stock_klines:
        kline = {}
        for key in ("open", "high", "low", "close", "volume"):
            kline[key] = k.get(key, 0)
        kline["date"] = k.get("trade_date", k.get("date", ""))
        klines.append(kline)

    closes = [k["close"] for k in klines]

    price_regime = _calculate_price_regime(klines)
    boll = _calculate_bollinger(klines)
    ma_values = _calculate_ma_values(closes)
    rsi = _calculate_rsi(closes)
    volume_price = _calculate_volume_price(klines)

    # 大盘 regime
    market_regime_a = _calculate_market_regime_a(snapshot.index_klines)
    market_regime_hk = _calculate_market_regime_hk(snapshot.index_klines)

    return SignalVector(
        date=snapshot.date,
        symbol=snapshot.symbol,
        price_regime=price_regime,
        market_regime_a=market_regime_a,
        market_regime_hk=market_regime_hk,
        boll=boll,
        volume_price=volume_price,
        ma_values=ma_values,
        rsi=rsi,
    )


def _calculate_price_regime(klines: list[dict]) -> str | None:
    """计算价格 regime。

    复用 summarize_price_history 的 regime 判定逻辑（截断到 T）：
      - NEW_ALL_TIME_HIGH / NEW_ALL_TIME_LOW
      - NEW_YTD_HIGH / NEW_YTD_LOW
      - NEAR_YTD_HIGH / NEAR_YTD_LOW
      - IN_RANGE

    需要至少 1 根 K 线。
    """
    if not klines:
        return None

    last = klines[-1]
    cur = last["close"]
    last_low = last["low"]
    last_high = last["high"]

    # 全部历史的高/低
    all_high = max(k["high"] for k in klines)
    all_low = min(k["low"] for k in klines)

    # 52 周（约 260 个交易日）高/低
    last_52w = klines[-260:] if len(klines) >= 260 else klines
    h52 = max(k["high"] for k in last_52w)
    l52 = min(k["low"] for k in last_52w)

    # YTD（当年）高/低
    today_year = last["date"][:4]
    ytd_rows = [k for k in klines if k["date"].startswith(today_year)]
    ytd_high = None
    ytd_low = None
    if ytd_rows:
        ytd_high = max(k["high"] for k in ytd_rows)
        ytd_low = min(k["low"] for k in ytd_rows)

    # 判断 breakouts
    is_new_all_high = last_high >= all_high - 1e-6
    is_new_all_low = last_low <= all_low + 1e-6
    is_new_ytd_high = ytd_high is not None and last_high >= ytd_high - 1e-6
    is_new_ytd_low = ytd_low is not None and last_low <= ytd_low + 1e-6

    if is_new_all_high:
        return "NEW_ALL_TIME_HIGH"
    elif is_new_all_low:
        return "NEW_ALL_TIME_LOW"
    elif is_new_ytd_high:
        return "NEW_YTD_HIGH"
    elif is_new_ytd_low:
        return "NEW_YTD_LOW"
    else:
        if ytd_high is not None and ytd_low is not None:
            mid = (ytd_high + ytd_low) / 2
            if cur > mid:
                return "NEAR_YTD_HIGH"
            else:
                return "NEAR_YTD_LOW"
        return "IN_RANGE"


def _calculate_bollinger(
    klines: list[dict],
    period: int = 20,
    multiplier: float = 2.0,
    lookback: int = 60,
) -> dict | None:
    """计算 BOLL 指标。

    需要至少 period（默认 20）根 K 线。
    返回字段同 kline.py::compute_bollinger_bands。
    """
    if len(klines) < period:
        return None

    closes = [k["close"] for k in klines]
    lookback_data = closes[-lookback:] if len(closes) >= lookback else closes
    recent = lookback_data[-period:]

    if len(recent) < period:
        return None

    middle_val = sum(recent) / period
    if middle_val <= 0:
        return None

    variance = sum((c - middle_val) ** 2 for c in recent) / period
    std_val = math.sqrt(variance)
    upper = middle_val + multiplier * std_val
    lower = middle_val - multiplier * std_val
    bandwidth_pct = (upper - lower) / middle_val * 100
    current_close = closes[-1]
    position_pct = (current_close - lower) / (upper - lower) * 100 if upper > lower else 50.0
    position_pct = max(0.0, min(100.0, position_pct))

    # squeeze 检测
    squeeze = False
    hist_data = closes[-lookback:] if len(closes) >= lookback else closes
    if len(hist_data) >= period + 5:
        hist_bw = []
        for i in range(period, len(hist_data) + 1):
            window = hist_data[i - period:i]
            m = sum(window) / period
            if m <= 0:
                continue
            v = sum((c - m) ** 2 for c in window) / period
            s = math.sqrt(v)
            bw = (m + multiplier * s - (m - multiplier * s)) / m * 100
            hist_bw.append(bw)
        if hist_bw:
            min_bw = min(hist_bw)
            squeeze = bandwidth_pct <= min_bw * 1.05

    volatility_pct = std_val / middle_val * 100

    return {
        "middle": round(middle_val, 4),
        "upper": round(upper, 4),
        "lower": round(lower, 4),
        "bandwidth_pct": round(bandwidth_pct, 2),
        "position_pct": round(position_pct, 1),
        "squeeze": squeeze,
        "volatility_pct": round(volatility_pct, 2),
    }


def _calculate_ma_values(closes: list[float]) -> dict[str, float]:
    """计算常用均线值：MA5, MA10, MA20, MA60。

    窗口不足的均线不出现在返回 dict 中。
    """
    result = {}
    for period, key in [(5, "MA5"), (10, "MA10"), (20, "MA20"), (60, "MA60")]:
        if len(closes) >= period:
            result[key] = round(sum(closes[-period:]) / period, 4)
    return result


def _calculate_rsi(closes: list[float], period: int = 14) -> float | None:
    """计算 RSI（相对强弱指标）。

    需要至少 period + 1（默认 15）个收盘价。
    使用 Wilder's smoothing 方法。
    """
    if len(closes) < period + 1:
        return None

    gains = 0.0
    losses = 0.0

    # 初始平均用简单平均
    for i in range(1, period + 1):
        diff = closes[-(period + 1) + i] - closes[-(period + 1) + i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += abs(diff)

    avg_gain = gains / period
    avg_loss = losses / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


def _calculate_volume_price(klines: list[dict]) -> dict | None:
    """量价关系分析。

    复用 compute_volume_analysis 的算法。
    需要至少 5 根 K 线。
    """
    if len(klines) < 5:
        return {"label": "insufficient", "recent_days": len(klines)}

    recent_5 = klines[-5:]
    recent_20 = klines[-20:] if len(klines) >= 20 else klines
    vol_5 = [k.get("volume", 0) for k in recent_5]
    vol_20 = [k.get("volume", 0) for k in recent_20]
    avg_vol_5 = sum(vol_5) / len(vol_5) if vol_5 else 0
    avg_vol_20 = sum(vol_20) / len(vol_20) if vol_20 else 0
    vol_ratio_val = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 1.0

    close_first = recent_5[0].get("close", 0)
    close_last = recent_5[-1].get("close", 0)
    price_5d_chg = (close_last / close_first - 1) * 100 if close_first > 0 else 0

    price_up = price_5d_chg > 1.0
    price_down = price_5d_chg < -1.0
    vol_expanding = vol_ratio_val > 1.2
    vol_shrinking = vol_ratio_val < 0.8

    if vol_expanding and price_up:
        label = "rising_with_volume"
    elif vol_shrinking and price_up:
        label = "rising_shrinking"
    elif vol_expanding and price_down:
        label = "falling_with_volume"
    elif vol_shrinking and price_down:
        label = "falling_shrinking"
    else:
        label = "sideways"

    return {
        "label": label,
        "vol_5d_avg": round(avg_vol_5, 0),
        "vol_20d_avg": round(avg_vol_20, 0),
        "vol_ratio": round(vol_ratio_val, 2),
        "price_5d_chg_pct": round(price_5d_chg, 2),
        "recent_days": len(klines),
    }


def _calculate_market_regime_a(index_klines: dict[str, list[dict]]) -> str | None:
    """计算 A 股大盘 regime：RISK_ON / NEUTRAL / RISK_OFF。

    使用沪深300 (sh000300) 和创业板指 (sz399006)。
    复用 market_regime._classify 逻辑。
    需要至少 200 天数据（MA200 计算）。
    """
    a_indices = {
        "sh000300": "沪深300",
        "sz399006": "创业板指",
    }
    summaries = []
    for tcode, name in a_indices.items():
        klines = index_klines.get(tcode, [])
        if len(klines) < 200:
            continue
        summary = _summarize_single_index(klines, tcode, name)
        if summary:
            summaries.append(summary)

    if not summaries:
        return None

    return _classify_regime(summaries)


def _calculate_market_regime_hk(index_klines: dict[str, list[dict]]) -> str | None:
    """计算港股大盘 regime：RISK_ON / NEUTRAL / RISK_OFF。

    使用恒生指数 (hkHSI) 和恒生科技指数 (hkHSTECH)。
    """
    hk_indices = {
        "hkHSI": "恒生指数",
        "hkHSTECH": "恒生科技",
    }
    summaries = []
    for tcode, name in hk_indices.items():
        klines = index_klines.get(tcode, [])
        if len(klines) < 200:
            continue
        summary = _summarize_single_index(klines, tcode, name)
        if summary:
            summaries.append(summary)

    if not summaries:
        return None

    return _classify_regime(summaries)


def _summarize_single_index(
    klines: list[dict], tcode: str, name: str
) -> dict | None:
    """对单个指数做 K 线摘要（截断到 snapshot T 日）。

    返回：close, high_52w, low_52w, from_52w_high_pct,
           ma200, broke_ma200, above_ma200, ma200_dev_pct 等。
    """
    if not klines:
        return None

    last = klines[-1]
    closes = [k["close"] for k in klines]
    last_close = last["close"]

    # 52w 高/低（约 260 个交易日）
    window_252 = klines[-252:] if len(klines) >= 252 else klines
    high_52w = max(k["high"] for k in window_252)
    low_52w = min(k["low"] for k in window_252)

    from_high_pct = round((last_close / high_52w - 1) * 100, 2) if high_52w > 0 else None
    from_low_pct = round((last_close / low_52w - 1) * 100, 2) if low_52w > 0 else None

    # MA200
    if len(closes) >= 200:
        ma200 = sum(closes[-200:]) / 200
        broke_ma200 = last_close < ma200
        above_ma200 = last_close >= ma200
        ma200_dev_pct = round((last_close / ma200 - 1) * 100, 2) if ma200 > 0 else None
    else:
        ma200 = None
        broke_ma200 = None
        above_ma200 = None
        ma200_dev_pct = None

    return {
        "tcode": tcode,
        "name": name,
        "close": last_close,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "from_52w_high_pct": from_high_pct,
        "from_52w_low_pct": from_low_pct,
        "ma200": round(ma200, 2) if ma200 else None,
        "broke_ma200": broke_ma200,
        "above_ma200": above_ma200,
        "ma200_dev_pct": ma200_dev_pct,
    }


def _classify_regime(summaries: list[dict]) -> str:
    """根据多个指数 summary 给出 regime。

    复用 market_regime._classify 逻辑：
      - RISK_OFF：任一指数 距 52w 高 <= -15% AND 跌破 MA200
      - RISK_ON：所有指数 距 52w 高 >= -3% AND 站上 MA200
      - NEUTRAL：其余所有情形
    """
    if not summaries:
        return "NEUTRAL"

    risk_off_hits = []
    risk_on_hits = []
    for s in summaries:
        from_high = s.get("from_52w_high_pct")
        broke = s.get("broke_ma200")
        above = s.get("above_ma200")
        if from_high is not None and from_high <= -15 and broke:
            risk_off_hits.append(s["name"])
        if from_high is not None and from_high >= -3 and above:
            risk_on_hits.append(s["name"])

    if risk_off_hits:
        return "RISK_OFF"
    if len(risk_on_hits) == len(summaries):
        return "RISK_ON"
    return "NEUTRAL"

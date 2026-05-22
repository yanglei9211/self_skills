"""技术指标计算模块：MACD / RSI / 布林带 / 量价分析 / K 线形态。

所有函数输入为日 K 线列表 ``[{date, open, high, low, close, volume}]``，
由 ``summarize_tech_indicators`` 统一入口输出结构化摘要。
"""

from __future__ import annotations

import math
from typing import Any


def _ema(data: list[float], period: int) -> list[float]:
    """计算指数移动平均。返回与 data 等长的列表，前 period-1 个值为 None。"""
    if len(data) < period:
        return [None] * len(data)
    k = 2.0 / (period + 1)
    out: list[float | None] = [None] * (period - 1)
    # 初始值用 SMA
    sma = sum(data[:period]) / period
    out.append(sma)
    for i in range(period, len(data)):
        out.append(data[i] * k + out[-1] * (1 - k))
    return out  # type: ignore[return-value]


def compute_macd(kline: list[dict]) -> dict:
    """MACD 计算 + 顶/底背离检测。

    Returns
    -------
    dict with keys: dif, dea, hist, divergence, divergence_detail, values_today
    """
    if len(kline) < 60:
        return {"error": "K 线不足 60 条，无法计算 MACD"}

    closes = [k["close"] for k in kline]
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)

    dif: list[float | None] = []
    for a, b in zip(ema12, ema26):
        if a is None or b is None:
            dif.append(None)
        else:
            dif.append(a - b)

    # DEA = 9-day EMA of DIF
    valid_dif = [d for d in dif if d is not None]
    dea_raw = _ema(valid_dif, 9)
    dea: list[float | None] = [None] * (len(dif) - len(dea_raw)) + dea_raw

    # HIST = 2 * (DIF - DEA)
    hist: list[float | None] = []
    for d, e in zip(dif, dea):
        if d is not None and e is not None:
            hist.append(2 * (d - e))
        else:
            hist.append(None)

    # ── 背离检测 ──
    divergence: str | None = None
    divergence_detail: str | None = None

    # 取最近 ~60 个有效 DIF 值做检测
    lookback = min(60, len(kline))
    recent_closes = closes[-lookback:]
    recent_dif = dif[-lookback:]

    # 找最近两个显著的局部极值点（间隔 >= 8 天）
    def find_peaks(data: list[float], min_dist: int = 8) -> list[tuple[int, float]]:
        peaks: list[tuple[int, float]] = []
        for i in range(min_dist, len(data) - min_dist):
            v = data[i]
            if v is None:
                continue
            if all(v > data[j] for j in range(i - min_dist, i + min_dist + 1) if j != i and data[j] is not None):
                peaks.append((i, v))
        return peaks

    def find_troughs(data: list[float], min_dist: int = 8) -> list[tuple[int, float]]:
        troughs: list[tuple[int, float]] = []
        for i in range(min_dist, len(data) - min_dist):
            v = data[i]
            if v is None:
                continue
            if all(v < data[j] for j in range(i - min_dist, i + min_dist + 1) if j != i and data[j] is not None):
                troughs.append((i, v))
        return troughs

    rc = [c for c in recent_closes if c is not None]
    rd = [d for d in recent_dif if d is not None]
    if len(rc) >= 20 and len(rd) >= 20:
        # 对齐：取两者都能覆盖的最近区间
        n = min(len(rc), len(rd))
        rc_aligned = rc[-n:]
        rd_aligned = rd[-n:]

        peaks_price = find_peaks(rc_aligned)
        peaks_dif = find_peaks(rd_aligned)
        troughs_price = find_troughs(rc_aligned)
        troughs_dif = find_troughs(rd_aligned)

        # 顶背离：最近两个 price 峰，后峰价格更高但 DIF 更低
        if len(peaks_price) >= 2 and len(peaks_dif) >= 2:
            p1_idx, p1_val = peaks_price[-2]
            p2_idx, p2_val = peaks_price[-1]
            d_peaks = {idx: val for idx, val in peaks_dif}
            # 找 DIF 在对应区域的峰
            d1_candidates = [(idx, val) for idx, val in peaks_dif if abs(idx - p1_idx) <= 5]
            d2_candidates = [(idx, val) for idx, val in peaks_dif if abs(idx - p2_idx) <= 5]
            if d1_candidates and d2_candidates:
                _, d1 = d1_candidates[-1]
                _, d2 = d2_candidates[-1]
                if p2_val > p1_val and d2 < d1:
                    divergence = "bearish_top"
                    divergence_detail = (
                        f"顶背离：价格前高 {p1_val:.2f}→{p2_val:.2f} 创新高，"
                        f"但 DIF {d1:.4f}→{d2:.4f} 走低，上涨动能衰竭"
                    )

        # 底背离：最近两个 price 谷，后谷价格更低但 DIF 更高
        if divergence is None and len(troughs_price) >= 2 and len(troughs_dif) >= 2:
            t1_idx, t1_val = troughs_price[-2]
            t2_idx, t2_val = troughs_price[-1]
            d_troughs = {idx: val for idx, val in troughs_dif}
            d1_candidates = [(idx, val) for idx, val in troughs_dif if abs(idx - t1_idx) <= 5]
            d2_candidates = [(idx, val) for idx, val in troughs_dif if abs(idx - t2_idx) <= 5]
            if d1_candidates and d2_candidates:
                _, d1 = d1_candidates[-1]
                _, d2 = d2_candidates[-1]
                if t2_val < t1_val and d2 > d1:
                    divergence = "bullish_bottom"
                    divergence_detail = (
                        f"底背离：价格前低 {t1_val:.2f}→{t2_val:.2f} 创新低，"
                        f"但 DIF {d1:.4f}→{d2:.4f} 走高，下跌动能衰竭"
                    )

    return {
        "dif": round(dif[-1], 4) if dif[-1] is not None else None,
        "dea": round(dea[-1], 4) if dea[-1] is not None else None,
        "hist": round(hist[-1], 4) if hist[-1] is not None else None,
        "dif_prev": round(dif[-2], 4) if len(dif) >= 2 and dif[-2] is not None else None,
        "dea_prev": round(dea[-2], 4) if len(dea) >= 2 and dea[-2] is not None else None,
        "hist_prev": round(hist[-2], 4) if len(hist) >= 2 and hist[-2] is not None else None,
        "hist_trend": _hist_trend(hist),
        "divergence": divergence,
        "divergence_detail": divergence_detail,
    }


def _hist_trend(hist: list[float | None]) -> str | None:
    """判断最近几根 HIST 柱的趋势方向。"""
    valid = [h for h in hist[-6:] if h is not None]
    if len(valid) < 3:
        return None
    if all(valid[i] >= valid[i + 1] for i in range(len(valid) - 1)):
        return "shortening_bearish"  # 绿柱/红柱在缩短
    if all(valid[i] <= valid[i + 1] for i in range(len(valid) - 1)):
        if valid[-1] > valid[0]:
            return "expanding_bullish"
    return None


def compute_rsi(kline: list[dict], period: int = 14) -> dict:
    """RSI 计算 + 超买/超卖判定。

    Returns
    -------
    dict with keys: rsi, overbought, oversold, level
    """
    if len(kline) < period + 1:
        return {"error": f"K 线不足 {period + 1} 条，无法计算 RSI"}

    closes = [k["close"] for k in kline]
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))

    # 初始平均
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_values: list[float | None] = [None] * period
    if avg_loss == 0:
        rsi_values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100.0 - 100.0 / (1.0 + rs))

    # Wilder's smoothing
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - 100.0 / (1.0 + rs))

    latest = rsi_values[-1]
    return {
        "rsi": round(latest, 1) if latest is not None else None,
        "overbought": latest is not None and latest >= 80,
        "oversold": latest is not None and latest <= 30,
        "near_overbought": latest is not None and 70 <= latest < 80,
        "near_oversold": latest is not None and 30 < latest <= 35,
        "level": (
            "overbought" if (latest is not None and latest >= 80) else
            "near_overbought" if (latest is not None and latest >= 70) else
            "oversold" if (latest is not None and latest <= 30) else
            "near_oversold" if (latest is not None and latest <= 35) else
            "neutral"
        ),
    }


def compute_bollinger(kline: list[dict], period: int = 20, num_std: float = 2.0) -> dict:
    """布林带计算。

    Returns
    -------
    dict with keys: upper, middle, lower, bandwidth_pct, position, squeeze
    """
    if len(kline) < period:
        return {"error": f"K 线不足 {period} 条，无法计算布林带"}

    closes = [k["close"] for k in kline]
    recent = closes[-period:]
    middle = sum(recent) / period

    variance = sum((c - middle) ** 2 for c in recent) / period
    std = math.sqrt(variance)

    upper = middle + num_std * std
    lower = middle - num_std * std
    bandwidth = (upper - lower) / middle * 100 if middle > 0 else 0

    latest_close = closes[-1]
    if latest_close > upper:
        position = "above_upper"
    elif latest_close >= upper - 0.3 * (upper - middle):
        position = "near_upper"
    elif latest_close < lower:
        position = "below_lower"
    elif latest_close <= lower + 0.3 * (middle - lower):
        position = "near_lower"
    else:
        position = "middle"

    # Squeeze：带宽缩到最近 60 天最低 20% 分位
    squeeze = False
    if len(closes) >= 60:
        bandwidths: list[float] = []
        for i in range(period, min(len(closes), 60)):
            sub = closes[i - period:i]
            m = sum(sub) / period
            v = sum((c - m) ** 2 for c in sub) / period
            s = math.sqrt(v)
            bw = (2 * num_std * s) / m * 100 if m > 0 else 0
            bandwidths.append(bw)
        if bandwidths:
            threshold = sorted(bandwidths)[max(0, int(len(bandwidths) * 0.2))]
            squeeze = bandwidth < threshold

    return {
        "upper": round(upper, 2),
        "middle": round(middle, 2),
        "lower": round(lower, 2),
        "bandwidth_pct": round(bandwidth, 1),
        "position": position,
        "squeeze": squeeze,
    }


def compute_volume_analysis(kline: list[dict]) -> dict:
    """量价关系分析。

    Returns
    -------
    dict with keys: vol_today, vol_20d_avg, vol_ratio, divergence, shrink_to_extreme
    """
    if len(kline) < 25:
        return {"error": "K 线不足 25 条，无法做量价分析"}

    volumes = [k["volume"] for k in kline]
    closes = [k["close"] for k in kline]

    vol_today = volumes[-1]
    vol_20d_avg = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else sum(volumes[:-1]) / (len(volumes) - 1)
    vol_ratio = vol_today / vol_20d_avg if vol_20d_avg > 0 else 1.0

    # 成交量极端萎缩：< 1/3 20 日均量
    shrink_to_extreme = vol_ratio < 0.35

    # 量价背离
    divergence: str | None = None
    divergence_detail: str | None = None

    price_change_1d = (closes[-1] / closes[-2] - 1) if closes[-2] != 0 else 0

    # 近 5 日量价趋势对比
    if len(closes) >= 6:
        price_5d_change = (closes[-1] / closes[-6] - 1) if closes[-6] != 0 else 0
        vol_first_5d = sum(volumes[-10:-5]) / 5 if len(volumes) >= 10 else vol_20d_avg
        vol_last_5d = sum(volumes[-5:]) / 5
        vol_5d_change = (vol_last_5d / vol_first_5d - 1) if vol_first_5d > 0 else 0

        # 价涨量缩（bearish divergence）
        if price_5d_change > 0.03 and vol_5d_change < -0.2:
            divergence = "bearish"
            divergence_detail = (
                f"价涨量缩：近 5 日价格 +{price_5d_change*100:.1f}%，"
                f"均量 -{abs(vol_5d_change)*100:.0f}%，上攻缺乏量能配合"
            )
        # 价跌量缩到极致 → 可能见底（bullish signal, not divergence）
        elif price_5d_change < -0.03 and shrink_to_extreme:
            divergence = "bullish_shrink"
            divergence_detail = (
                f"地量地价：近 5 日价格 {price_5d_change*100:.1f}%，"
                f"今日量仅为 20 日均量 {vol_ratio*100:.0f}%，恐慌抛售接近尾声"
            )

    return {
        "vol_today": vol_today,
        "vol_20d_avg": round(vol_20d_avg, 0),
        "vol_ratio": round(vol_ratio, 2),
        "divergence": divergence,
        "divergence_detail": divergence_detail,
        "shrink_to_extreme": shrink_to_extreme,
    }


def _body_size(k: dict) -> float:
    return abs(k["close"] - k["open"])


def _upper_shadow(k: dict) -> float:
    return k["high"] - max(k["close"], k["open"])


def _lower_shadow(k: dict) -> float:
    return min(k["close"], k["open"]) - k["low"]


def _range(k: dict) -> float:
    return k["high"] - k["low"]


def _is_bullish(k: dict) -> bool:
    return k["close"] > k["open"]


def compute_kline_patterns(kline: list[dict]) -> dict:
    """K 线形态识别。

    只识别最近 1-2 根 K 线的形态，返回最有意义的那个。
    """
    if len(kline) < 3:
        return {"error": "K 线不足 3 条"}

    today = kline[-1]
    yesterday = kline[-2]
    body = _body_size(today)
    rng = _range(today)
    upper_s = _upper_shadow(today)
    lower_s = _lower_shadow(today)
    is_bull = _is_bullish(today)

    # 用于判断位置（需要 20 日均价做参考）
    closes_20 = [k["close"] for k in kline[-21:-1]]
    avg_20 = sum(closes_20) / len(closes_20) if closes_20 else today["close"]

    patterns: list[dict] = []

    # Doji / 十字星
    if rng > 0 and body / rng < 0.1:
        location = "high" if today["close"] > avg_20 * 1.05 else "low" if today["close"] < avg_20 * 0.95 else "mid"
        patterns.append({"name": "doji", "location": location, "weight": 1})

    # Hammer / 锤子线（长下影，小实体，处于相对低位）
    if rng > 0 and lower_s > 2 * body and upper_s < body * 0.5:
        if today["close"] < avg_20 * 1.02:  # 不在高位
            patterns.append({"name": "hammer", "weight": 2})

    # Inverted hammer / 倒锤子（长上影，小实体，处于下跌趋势低位）
    if rng > 0 and upper_s > 2 * body and lower_s < body * 0.5:
        if today["close"] < avg_20 * 0.95:
            patterns.append({"name": "inverted_hammer", "weight": 2})

    # Shooting star / 射击之星（长上影，处于相对高位）
    if rng > 0 and upper_s > 2 * body and lower_s < body * 0.5:
        if today["close"] > avg_20 * 1.05:
            patterns.append({"name": "shooting_star", "weight": 3})

    # 长上影（不是标准形态但有警示意义）
    if rng > 0 and upper_s > body * 3 and upper_s > rng * 0.5:
        patterns.append({"name": "long_upper_shadow", "weight": 2})

    # 长下影
    if rng > 0 and lower_s > body * 3 and lower_s > rng * 0.5:
        patterns.append({"name": "long_lower_shadow", "weight": 2})

    # Engulfing / 吞没形态
    y_body = _body_size(yesterday)
    if is_bull and not _is_bullish(yesterday):
        if today["open"] <= yesterday["close"] and today["close"] >= yesterday["open"] and body > y_body * 0.8:
            if today["close"] < avg_20 * 1.02:
                patterns.append({"name": "bullish_engulfing", "weight": 3})
    elif not is_bull and _is_bullish(yesterday):
        if today["open"] >= yesterday["close"] and today["close"] <= yesterday["open"] and body > y_body * 0.8:
            if today["close"] > avg_20 * 0.98:
                patterns.append({"name": "bearish_engulfing", "weight": 3})

    if not patterns:
        return {"patterns": [], "main": None}

    # 按 weight 降序，取最高权重的
    patterns.sort(key=lambda p: p["weight"], reverse=True)
    return {
        "patterns": patterns,
        "main": patterns[0]["name"],
        "details": [p["name"] for p in patterns],
    }


def summarize_tech_indicators(kline: list[dict]) -> dict:
    """统一入口：计算所有技术指标，返回结构化摘要。"""
    return {
        "macd": compute_macd(kline),
        "rsi": compute_rsi(kline),
        "bollinger": compute_bollinger(kline),
        "volume": compute_volume_analysis(kline),
        "patterns": compute_kline_patterns(kline),
    }

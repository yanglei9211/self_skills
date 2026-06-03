"""
P0 策略引擎：将 SignalVector 输入策略规则，产生 Decision。

当前实现：
  - boll_squeeze_entry: BOLL squeeze 入场
  - ma_breakout: 均线突破/趋势跟随
  - price_new_high: 新高趋势策略
"""

from __future__ import annotations

from .models import SignalVector, Decision


def boll_squeeze_entry(snapshot, signals: SignalVector) -> list[Decision]:
    """BOLL 挤压入场策略。

    条件：
      1. BOLL squeeze == True（带宽处于低分位）
      2. volume_price label != 'falling_with_volume'（不是放量下跌）
      3. price_regime 不在极端低位（避免抄底）
      4. data_sufficient == True

    Returns:
        list[Decision]，通常最多一个决策。
    """
    decisions = []

    # 数据不足时无法决策
    if not snapshot.data_sufficient:
        return decisions

    # 信号不可用时跳过
    if signals.boll is None or signals.volume_price is None:
        return decisions

    if signals.boll.get("squeeze") is not True:
        return decisions

    # 排除放量下跌（最危险的信号）
    if signals.volume_price.get("label") == "falling_with_volume":
        return decisions

    # 排除极端低位 regime
    low_regimes = {"NEW_ALL_TIME_LOW", "NEW_YTD_LOW", "NEAR_YTD_LOW"}
    if signals.price_regime in low_regimes:
        return decisions

    # 计算置信度
    confidence = 0.7
    reasons = ["BOLL squeeze 信号触发"]
    risks = []

    # BOLL position 靠近中轨或下轨更有利
    pos = signals.boll.get("position_pct", 50)
    if 20 <= pos <= 80:
        confidence += 0.05
        reasons.append("BOLL 位置适中")
    if pos < 20:
        confidence -= 0.05
        risks.append("BOLL 位置偏下轨，可能继续下行")

    # 量价配合加分
    vol_label = signals.volume_price.get("label", "")
    if vol_label in ("rising_with_volume",):
        confidence += 0.1
        reasons.append("量价配合良好（放量上涨）")
    elif vol_label in ("rising_shrinking", "falling_shrinking"):
        confidence += 0.03
        reasons.append("量价平缓")

    # 大盘环境调整
    if signals.market_regime_a == "RISK_ON":
        confidence += 0.05
        reasons.append("大盘 RISK_ON 环境")
    elif signals.market_regime_a == "RISK_OFF":
        confidence -= 0.1
        risks.append("大盘 RISK_OFF，谨慎")

    confidence = max(0.0, min(1.0, confidence))

    decisions.append(Decision(
        action="buy",
        confidence=round(confidence, 2),
        reasons=reasons,
        risks=risks,
        path="trend",
        entry_date=snapshot.date,
        entry_price=snapshot.stock_klines[-1]["close"] if snapshot.stock_klines else 0,
    ))

    return decisions


def ma_breakout(snapshot, signals: SignalVector) -> list[Decision]:
    """均线突破 / 趋势跟随策略。

    条件：
      1. MA5 > MA10 > MA20 多头排列 或
      2. 收盘价突破 MA20 / MA60
      3. volume_price 不出现放量下跌
      4. data_sufficient == True
    """
    decisions = []

    if not snapshot.data_sufficient:
        return decisions

    if signals.ma_values is None:
        return decisions

    ma = signals.ma_values
    reasons = []
    risks = []
    confidence = 0.6

    klines = snapshot.stock_klines
    if not klines:
        return decisions
    last_close = klines[-1]["close"]

    # 检查多头排列
    if all(k in ma for k in ("MA5", "MA10", "MA20")):
        if ma["MA5"] > ma["MA10"] > ma["MA20"]:
            confidence += 0.15
            reasons.append("均线多头排列（MA5 > MA10 > MA20）")

    # 检查突破
    if "MA20" in ma and last_close > ma["MA20"]:
        confidence += 0.05
        reasons.append("收盘价突破 MA20")
    if "MA60" in ma and last_close > ma["MA60"]:
        confidence += 0.05
        reasons.append("收盘价突破 MA60")

    # 检查跌破
    if "MA20" in ma and last_close < ma["MA20"]:
        confidence -= 0.1
        risks.append("收盘价低于 MA20")
    if "MA60" in ma and last_close < ma["MA60"]:
        confidence -= 0.1
        risks.append("收盘价低于 MA60")

    # 量价过滤
    if signals.volume_price and signals.volume_price.get("label") == "falling_with_volume":
        confidence -= 0.1
        risks.append("放量下跌，量价不配合")

    # 大盘环境
    if signals.market_regime_a == "RISK_ON":
        confidence += 0.05
        reasons.append("大盘 RISK_ON")
    elif signals.market_regime_a == "RISK_OFF":
        confidence -= 0.1
        risks.append("大盘 RISK_OFF")

    if not reasons:
        return decisions  # 没有触发条件，不产生决策

    confidence = max(0.0, min(1.0, confidence))

    decisions.append(Decision(
        action="buy",
        confidence=round(confidence, 2),
        reasons=reasons,
        risks=risks,
        path="trend",
        entry_date=snapshot.date,
        entry_price=last_close,
    ))

    return decisions


def price_new_high(snapshot, signals: SignalVector) -> list[Decision]:
    """新高趋势策略。

    条件：
      1. price_regime 为 NEW_YTD_HIGH / NEW_52W_HIGH / NEW_ALL_TIME_HIGH
      2. 量价配合不出现放量下跌
      3. 不是 extreme_overbought（RSI 极端）
    """
    decisions = []

    if not snapshot.data_sufficient:
        return decisions

    if signals.price_regime is None:
        return decisions

    high_regimes = {"NEW_YTD_HIGH", "NEW_ALL_TIME_HIGH"}
    if signals.price_regime not in high_regimes:
        # 也接受 NEAR_YTD_HIGH 如果满足其他条件
        if signals.price_regime != "NEAR_YTD_HIGH":
            return decisions

    reasons = []
    risks = []
    confidence = 0.5

    reasons.append(f"价格 regime: {signals.price_regime}")

    # RSI 过热风险
    if signals.rsi is not None and signals.rsi > 80:
        confidence -= 0.15
        risks.append(f"RSI 过热 ({signals.rsi})，追高风险大")
    elif signals.rsi is not None and signals.rsi > 70:
        confidence -= 0.05
        risks.append(f"RSI 偏高 ({signals.rsi})")

    # 量价过滤
    if signals.volume_price:
        vol_label = signals.volume_price.get("label", "")
        if vol_label == "falling_with_volume":
            confidence -= 0.2
            risks.append("放量下跌，新高可能是假突破")
        elif vol_label == "rising_with_volume":
            confidence += 0.1
            reasons.append("放量上涨确认新高")
        elif vol_label == "rising_shrinking":
            confidence -= 0.05
            risks.append("缩量上涨，新高动能不足")

    # BOLL 位置
    if signals.boll:
        pos = signals.boll.get("position_pct", 50)
        if pos > 90:
            confidence -= 0.05
            risks.append("BOLL 位置接近上轨，短期回调风险")
        if signals.boll.get("squeeze"):
            confidence += 0.05
            reasons.append("BOLL squeeze 配合新高突破")

    # 大盘环境
    if signals.market_regime_a == "RISK_ON":
        confidence += 0.05
        reasons.append("大盘 RISK_ON")
    elif signals.market_regime_a == "RISK_OFF":
        confidence -= 0.1
        risks.append("大盘 RISK_OFF，新高策略风险大")

    if not reasons:
        return decisions

    confidence = max(0.0, min(1.0, confidence))

    klines = snapshot.stock_klines
    last_close = klines[-1]["close"] if klines else 0

    decisions.append(Decision(
        action="buy",
        confidence=round(confidence, 2),
        reasons=reasons,
        risks=risks,
        path="trend",
        entry_date=snapshot.date,
        entry_price=last_close,
    ))

    return decisions


# 策略注册表
STRATEGIES = {
    "boll_squeeze_entry": boll_squeeze_entry,
    "ma_breakout": ma_breakout,
    "price_new_high": price_new_high,
}

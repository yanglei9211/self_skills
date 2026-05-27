"""A 股 1-4 周中短线初筛引擎。

本模块只做纯计算：把行情、资金流、K 线位置压成特征，再分别跑
``trend_continuation`` 和 ``bottom_reversal`` 两条候选路径。网络抓取放在
stock-market-hub 的 CLI 脚本里，便于测试和复用。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


TREND_TYPE = "trend_continuation"
REVERSAL_TYPE = "bottom_reversal"


@dataclass(frozen=True)
class ScreenFeatures:
    symbol: str
    code: str
    name: str
    current: float | None
    percent: float | None
    amount_yi: float
    turnover_rate: float | None
    rsi: float | None
    pos_60: float | None
    ma20_ratio: float | None
    ma60_ratio: float | None
    chg_20d: float | None
    flow_5d: float | None
    flow_10d: float | None
    flow_20d: float | None
    fund_verdict: str
    fund_acceleration: str | None = None
    fund_conflict: bool = False
    fund_is_resonance: bool = False


@dataclass(frozen=True)
class Candidate:
    symbol: str
    code: str
    name: str
    candidate_type: str
    action: str
    score: float
    current: float | None
    percent: float | None
    amount_yi: float
    turnover_rate: float | None
    rsi: float | None
    pos_60: float | None
    chg_20d: float | None
    flow_5d: float | None
    flow_10d: float | None
    flow_20d: float | None
    fund_verdict: str
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += abs(diff)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def _num(v: Any, default: float = 0.0) -> float:
    return v if isinstance(v, (int, float)) else default


def _has_st_risk(name: str) -> bool:
    upper = (name or "").upper()
    return "ST" in upper or "退" in upper


def build_features(quote: dict[str, Any], fund_summary: dict[str, Any], kline: list[dict]) -> ScreenFeatures:
    """把三类原始数据合并成可评分特征。"""
    symbol = str(quote.get("symbol") or "").upper()
    code = "".join(ch for ch in symbol if ch.isdigit())[-6:]
    if not symbol or len(code) != 6:
        raise ValueError(f"unsupported symbol for swing screen: {symbol!r}")
    closes = [float(k["close"]) for k in kline if isinstance(k.get("close"), (int, float))]
    current = quote.get("current")
    if not isinstance(current, (int, float)) and closes:
        current = closes[-1]

    high_60 = max(closes[-60:]) if len(closes) >= 60 else (max(closes) if closes else None)
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
    cur_float = float(current) if isinstance(current, (int, float)) else None
    rsi_raw = _rsi(closes)

    rolling = fund_summary.get("rolling") or {}
    cross = fund_summary.get("cross_validation") or {}

    return ScreenFeatures(
        symbol=symbol,
        code=code,
        name=str(quote.get("name") or ""),
        current=cur_float,
        percent=quote.get("percent") if isinstance(quote.get("percent"), (int, float)) else None,
        amount_yi=round(_num(quote.get("amount")) / 1e8, 3),
        turnover_rate=quote.get("turnover_rate") if isinstance(quote.get("turnover_rate"), (int, float)) else None,
        rsi=round(rsi_raw, 1) if rsi_raw is not None else None,
        pos_60=round(cur_float / high_60, 4) if cur_float and high_60 else None,
        ma20_ratio=round(cur_float / ma20, 4) if cur_float and ma20 else None,
        ma60_ratio=round(cur_float / ma60, 4) if cur_float and ma60 else None,
        chg_20d=round((closes[-1] / closes[-21] - 1) * 100, 2) if len(closes) >= 21 and closes[-21] else None,
        flow_5d=(rolling.get("5d") or {}).get("main_yi"),
        flow_10d=(rolling.get("10d") or {}).get("main_yi"),
        flow_20d=(rolling.get("20d") or {}).get("main_yi"),
        fund_verdict=str(cross.get("verdict") or ""),
        fund_acceleration=cross.get("acceleration"),
        fund_conflict=bool(cross.get("short_long_conflict")),
        fund_is_resonance=bool(cross.get("is_resonance")),
    )


def _hard_filter(f: ScreenFeatures) -> list[str]:
    risks: list[str] = []
    if _has_st_risk(f.name):
        risks.append("ST/退市风险名称，剔除")
    if f.amount_yi < 0.5:
        risks.append("成交额低于 0.5 亿，流动性不足")
    if f.turnover_rate is not None and f.turnover_rate < 0.2:
        risks.append("换手率低于 0.2%，交易活跃度不足")
    if f.rsi is not None and f.rsi >= 80:
        risks.append("RSI >= 80，短线过热")
    if f.fund_conflict:
        risks.append("资金流短长周期冲突，信号不稳定")
    if (f.pos_60 or 0) > 1.0 and (f.chg_20d or 0) >= 40:
        risks.append("突破 60 日高点后 20 日涨幅过大，追高风险")
    return risks


def _score_trend(f: ScreenFeatures) -> tuple[float, list[str], list[str]]:
    score = 0.0
    reasons: list[str] = []
    risks: list[str] = []

    if f.fund_verdict == "RESONANCE_INFLOW" or f.fund_is_resonance:
        score += 35
        reasons.append("资金共振流入")
    elif f.fund_verdict == "PERSISTENT_INFLOW_STEADY":
        score += 24
        reasons.append("持续净流入")
    elif f.fund_verdict == "DECELERATING_INFLOW":
        score += 12
        risks.append("流入动能减弱")
    else:
        return 0.0, [], []

    f5, f10, f20 = _num(f.flow_5d), _num(f.flow_10d), _num(f.flow_20d)
    if f10 > 0 and f20 > 0:
        score += 15
        reasons.append("10d/20d 主力净流入为正")
    if f5 > 0:
        score += 8
        reasons.append("5d 资金仍为正")
    if f.fund_acceleration == "accelerating_inflow":
        score += 8
        reasons.append("流入节奏加速")

    if (f.ma20_ratio or 0) >= 1.0:
        score += 8
        reasons.append("站上 MA20")
    if (f.ma60_ratio or 0) >= 1.0:
        score += 6
        reasons.append("站上 MA60")

    pos60 = f.pos_60
    if pos60 is not None:
        if 0.85 <= pos60 <= 1.0:
            score += 8
            reasons.append("处于 60 日高位区，趋势已确立")
        elif pos60 < 0.85:
            risks.append("距离 60 日高点较远，趋势强度待确认")

    rsi = f.rsi
    if rsi is not None:
        if 45 <= rsi <= 72:
            score += 6
            reasons.append("RSI 处于趋势可跟随区间")
        elif rsi > 75:
            score -= 12
            risks.append("RSI 偏高，追高风险")

    chg20 = f.chg_20d
    if chg20 is not None and chg20 > 35:
        score -= 15
        risks.append("20 日涨幅过大，等待回踩更稳")
    elif chg20 is not None and 5 <= chg20 <= 25:
        score += 4
        reasons.append("20 日涨幅温和")

    return score, reasons, risks


def _score_reversal(f: ScreenFeatures) -> tuple[float, list[str], list[str]]:
    score = 0.0
    reasons: list[str] = []
    risks: list[str] = []

    if f.fund_verdict == "REVERSAL_INFLOW_CONFIRMED":
        score += 35
        reasons.append("反转流入确认")
    elif f.fund_verdict == "WEAKENING_OUTFLOW":
        score += 20
        reasons.append("下跌资金动能衰竭")
    else:
        return 0.0, [], []

    if _num(f.flow_5d) > 0:
        score += 12
        reasons.append("5d 主力转正")
    if f.flow_20d is not None and f.flow_20d < 0:
        score += 5
        reasons.append("仍处修复早期，未充分反弹")

    pos60 = f.pos_60
    if pos60 is not None:
        if pos60 <= 0.82:
            score += 14
            reasons.append("接近 60 日低位区域")
        elif pos60 <= 0.9:
            score += 7
            reasons.append("位置不高")
        else:
            risks.append("反弹位置已偏高")

    if (f.ma20_ratio or 0) >= 1.0:
        score += 10
        reasons.append("重新站回 MA20")
    elif (f.ma20_ratio or 0) >= 0.97:
        score += 4
        reasons.append("接近 MA20，等待确认")

    rsi = f.rsi
    if rsi is not None:
        if 35 <= rsi <= 55:
            score += 10
            reasons.append("RSI 从低位修复")
        elif rsi < 30:
            risks.append("RSI 仍深度超卖，可能是下跌中继")
        elif rsi > 65:
            score -= 8
            risks.append("RSI 已偏高，反弹性价比下降")

    chg20 = f.chg_20d
    if chg20 is not None:
        if -25 <= chg20 <= 5:
            score += 6
            reasons.append("20 日表现符合筑底修复区间")
        elif chg20 > 15:
            score -= 10
            risks.append("20 日已明显反弹，左侧优势下降")

    return score, reasons, risks


def classify_candidate(f: ScreenFeatures) -> Candidate | None:
    hard_risks = _hard_filter(f)
    if hard_risks:
        return None

    trend_score, trend_reasons, trend_risks = _score_trend(f)
    reversal_score, reversal_reasons, reversal_risks = _score_reversal(f)

    if trend_score < 55 and reversal_score < 55:
        return None

    if trend_score >= reversal_score:
        ctype = TREND_TYPE
        score = trend_score
        action = "trend_entry_candidate" if score >= 70 else "trend_pullback_watch"
        reasons = trend_reasons
        risks = trend_risks
    else:
        ctype = REVERSAL_TYPE
        score = reversal_score
        action = "reversal_probe_candidate" if score >= 60 else "reversal_confirm_watch"
        reasons = reversal_reasons
        risks = reversal_risks

    return Candidate(
        symbol=f.symbol,
        code=f.code,
        name=f.name,
        candidate_type=ctype,
        action=action,
        score=round(score, 1),
        current=f.current,
        percent=f.percent,
        amount_yi=f.amount_yi,
        turnover_rate=f.turnover_rate,
        rsi=f.rsi,
        pos_60=f.pos_60,
        chg_20d=f.chg_20d,
        flow_5d=f.flow_5d,
        flow_10d=f.flow_10d,
        flow_20d=f.flow_20d,
        fund_verdict=f.fund_verdict,
        reasons=reasons,
        risks=risks,
    )


def screen_candidates(features: Iterable[ScreenFeatures], top: int = 15) -> dict[str, list[Candidate]]:
    buckets: dict[str, list[Candidate]] = {
        TREND_TYPE: [],
        REVERSAL_TYPE: [],
    }
    for f in features:
        candidate = classify_candidate(f)
        if candidate is None:
            continue
        buckets[candidate.candidate_type].append(candidate)

    for key in buckets:
        buckets[key].sort(key=lambda c: c.score, reverse=True)
        buckets[key] = buckets[key][:top]
    return buckets


def _fmt_pct(v: float | None) -> str:
    return "-" if v is None else f"{v:+.1f}%"


def _fmt_ratio_pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.0%}"


def render_text(result: dict[str, list[Candidate]], *, title: str = "A 股中短线初筛") -> str:
    lines = [
        f"# {title}",
        "",
        "> 用途：1-4 周交易机会初筛，只缩小研究范围，不等同于买入建议。",
        "",
    ]

    sections = [
        (TREND_TYPE, "趋势延续候选"),
        (REVERSAL_TYPE, "筑底反转候选"),
    ]
    for key, label in sections:
        items = result.get(key) or []
        lines.append(f"## {label}（{len(items)}）")
        if not items:
            lines.append("")
            lines.append("（无符合条件标的）")
            lines.append("")
            continue
        lines.append("")
        lines.append("| 排名 | 代码 | 名称 | 动作 | 分数 | 现价 | 涨跌 | RSI | 距60高 | 20日涨幅 | 资金流 | 入选理由 | 风险 |")
        lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|")
        for idx, c in enumerate(items, 1):
            flow = f"5d {c.flow_5d:+.2f} / 10d {c.flow_10d:+.2f} / 20d {c.flow_20d:+.2f} 亿" if all(
                v is not None for v in (c.flow_5d, c.flow_10d, c.flow_20d)
            ) else c.fund_verdict
            reasons = "；".join(c.reasons[:4]) or "-"
            risks = "；".join(c.risks[:3]) or "-"
            cur = "-" if c.current is None else f"{c.current:.2f}"
            rsi = "-" if c.rsi is None else f"{c.rsi:.0f}"
            lines.append(
                f"| {idx} | `{c.symbol}` | {c.name} | {c.action} | {c.score:.1f} | "
                f"{cur} | {_fmt_pct(c.percent)} | {rsi} | {_fmt_ratio_pct(c.pos_60)} | "
                f"{_fmt_pct(c.chg_20d)} | {flow} | {reasons} | {risks} |"
            )
        lines.append("")
    return "\n".join(lines)

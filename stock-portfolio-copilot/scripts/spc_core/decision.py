from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from spc_core.ledger import latest_snapshots, list_watch, save_analysis_run
from spc_core.market_bridge import StockMarketHubProvider
from spc_core.portfolio import sync_portfolio
from spc_core.settings import capital_settings, ensure_defaults
from spc_core.utils import decimal_str, normalize_code, normalize_market, q_money, to_decimal


RISK_KEYWORDS = ("立案", "处罚", "诉讼", "减持", "退市", "停牌", "风险", "问询", "亏损", "爆雷")
POSITIVE_KEYWORDS = ("回购", "增持", "中标", "分红", "合作", "增长", "预增", "扭亏", "新品")
LOW_REGIMES = {"NEW_YTD_LOW", "NEW_52W_LOW", "NEW_ALL_TIME_LOW"}
HIGH_REGIMES = {"NEW_YTD_HIGH", "NEW_52W_HIGH", "NEW_ALL_TIME_HIGH"}
# 中间区间：既不创新低也不创新高的"非破位非右侧追高"位置。
# 反转买入候选只在这个集合内成立；LOW_REGIMES 永远禁止 buy。
MID_REGIMES = {"NEAR_YTD_LOW", "IN_RANGE", "NEAR_YTD_HIGH"}
FUND_PERSISTENT_INFLOW = "PERSISTENT_INFLOW"
FUND_PERSISTENT_OUTFLOW = "PERSISTENT_OUTFLOW"
FUND_REVERSAL_DOWN = "INFLOW_TO_OUTFLOW"
FUND_REVERSAL_UP = "OUTFLOW_TO_INFLOW"
MARKET_REGIME_RISK_OFF = "RISK_OFF"
MARKET_REGIME_RISK_ON = "RISK_ON"
MARKET_REGIME_NEUTRAL = "NEUTRAL"
ACTION_LABELS = {
    "avoid": "回避",
    "buy": "买入候选",
    "focus": "重点关注",
    "hold": "持有",
    "sell": "卖出",
    "trim": "减仓",
    "watch": "观察",
}
ACTION_DESCRIPTIONS = {
    "avoid": "风险或价格结构较差，暂不纳入交易候选",
    "buy": "满足更严格的买入候选条件，仍需结合开盘承接和仓位计划执行",
    "focus": "宽松信号较好，优先加入盯盘清单，但还没有达到买入条件",
    "hold": "持仓未触发明确处理信号",
    "sell": "持仓亏损与风险信号同时触发，优先考虑退出",
    "trim": "仓位、风险或价格结构触发减仓条件",
    "watch": "继续跟踪，等待更清晰的触发条件",
}


def _extract_security_name(analysis: dict) -> str:
    info = analysis.get("info") or {}
    for key in ("name", "short_name", "stock_name", "公司简称", "证券简称", "中文简称"):
        value = str(info.get(key) or "").strip()
        if value:
            return value
    rename_history = str(info.get("证券简称更名历史") or "").strip()
    if rename_history:
        parts = [part for part in rename_history.split() if part]
        if parts:
            return parts[-1]
    return str(info.get("公司名称") or info.get("中文名称") or "").strip()


def _select_targets(conn, account_id: int, scope: str, market: str | None, code: str | None) -> list[tuple[str, str, str]]:
    targets: list[tuple[str, str, str]] = []
    if market and code:
        return [(market, code, "single")]
    if scope in {"holdings", "all"}:
        for snap in latest_snapshots(conn, account_id):
            targets.append((snap["market"], snap["code"], "holdings"))
    if scope in {"watchlist", "all"}:
        existing = {(m, c) for m, c, _ in targets}
        for item in list_watch(conn, account_id):
            key = (item["market"], item["code"])
            if key not in existing:
                targets.append((item["market"], item["code"], "watchlist"))
    return targets


# ─────────────────────────────────────────────────────────────────
# 决策特征 + 信号收集 + 分支处理
#
# 重构思路：把"特征量提取 / 各维度 reasons-risks 收集 / 持仓 vs 自选两条决策分支"
# 拆成独立 helper，让 ``_decision_from_analysis`` 只剩"调度 + 组装"的骨架。
# 这样未来加新维度（券商研报、行业 regime、做 T 节奏 等）只需新增一个 collect_xxx，
# 不需要再去碰那段 200 行的决策树。
#
# 测试用例都通过 ``analyze_now`` 端到端断言公开字段，没有依赖私有函数 / reasons
# 的具体顺序，所以这种拆分可以"零行为变化"完成。
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Features:
    """从 ``(snapshot, analysis, capital_total, market_regime)`` 提取出的全部特征量。

    决策树只读 ``Features``，不再直接碰原始 dict。这样：
      - 测试可以构造一个 Features 单测某个分支
      - 新加字段只改本类 + ``_extract_features`` 一处
      - 决策树本身从 200 行降到 ~80 行，可读性显著上升
    """
    # 行情
    current: object | None
    change_pct: object | None
    # 价格 regime
    regime: str | None
    # 公告关键词命中数
    risk_hits: int
    positive_hits: int
    # 持仓
    qty: Decimal
    avg_cost: Decimal
    weight_pct: Decimal
    # 主力资金（缺数据时全部为 None / False）
    ff_available: bool
    ff_regime: str | None
    ff_reversal: str | None
    ff_1d: float | None
    ff_3d: float | None
    ff_5d: float | None
    ff_20d: float | None
    # 大盘
    market_regime: str | None


def _extract_features(
    snapshot: dict | None,
    analysis: dict,
    capital_total: Decimal,
    market_regime: str | None,
) -> Features:
    quote = analysis.get("quote") or {}
    announcements = analysis.get("announcements") or []
    price_history = analysis.get("price_history") or {}
    fund_flow = analysis.get("fund_flow") or {}

    titles = [item.get("title", "") for item in announcements]
    risk_hits = sum(1 for t in titles if any(k in t for k in RISK_KEYWORDS))
    positive_hits = sum(1 for t in titles if any(k in t for k in POSITIVE_KEYWORDS))

    ff_available = bool(fund_flow) and not fund_flow.get("error") and fund_flow.get("today") is not None
    rolling = fund_flow.get("rolling") or {}

    def _ff_yi(window: str) -> float | None:
        return (rolling.get(window) or {}).get("main_yi") if ff_available else None

    qty = Decimal(snapshot["qty"]) if snapshot else Decimal("0")
    avg_cost = to_decimal(snapshot["avg_cost_price"], "avg_cost_price") if snapshot else Decimal("0")
    position_value_cny = (
        to_decimal(snapshot["position_value_cny"] or "0", "position_value_cny") if snapshot else Decimal("0")
    )
    weight_pct = Decimal("0")
    if capital_total > 0 and position_value_cny > 0:
        weight_pct = q_money(position_value_cny / capital_total * Decimal("100"))

    return Features(
        current=quote.get("current"),
        change_pct=quote.get("percent"),
        regime=price_history.get("regime"),
        risk_hits=risk_hits,
        positive_hits=positive_hits,
        qty=qty,
        avg_cost=avg_cost,
        weight_pct=weight_pct,
        ff_available=ff_available,
        ff_regime=fund_flow.get("regime") if ff_available else None,
        ff_reversal=fund_flow.get("reversal") if ff_available else None,
        ff_1d=_ff_yi("1d"),
        ff_3d=_ff_yi("3d"),
        ff_5d=_ff_yi("5d"),
        ff_20d=_ff_yi("20d"),
        market_regime=market_regime,
    )


# ── signal 收集 helpers ──────────────────────────────────────────
# 每个 collect_xxx_signals 只负责从 Features 提取本维度的 reasons / risks 文案，
# 不改 action / confidence；具体动作触发由 _decide_for_holding / _decide_for_watching
# 集中决定。

def _collect_macro_signals(f: Features) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    risks: list[str] = []
    if f.market_regime == MARKET_REGIME_RISK_OFF:
        risks.append("所属市场大盘 RISK_OFF，整体风险偏好低，宏观防御为主")
    elif f.market_regime == MARKET_REGIME_RISK_ON:
        reasons.append("所属市场大盘 RISK_ON，整体风险偏好高，但不据此主动加仓")
    return reasons, risks


def _collect_price_signals(f: Features) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    risks: list[str] = []
    if f.regime in LOW_REGIMES:
        risks.append("价格处于破位或创新低区间")
    if f.regime in HIGH_REGIMES:
        reasons.append("价格处于强势区间或突破状态")
    return reasons, risks


def _collect_announcement_signals(f: Features) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    risks: list[str] = []
    if f.risk_hits:
        risks.append(f"近期待公告标题中命中 {f.risk_hits} 个风险关键词")
    if f.positive_hits:
        reasons.append(f"近期待公告标题中命中 {f.positive_hits} 个正向关键词")
    return reasons, risks


def _collect_fund_flow_signals(f: Features) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    risks: list[str] = []
    if not f.ff_available:
        return reasons, risks
    if f.ff_regime == FUND_PERSISTENT_INFLOW:
        reasons.append(f"主力 20 日持续净流入 {f.ff_20d:+.2f} 亿")
    elif f.ff_regime == FUND_PERSISTENT_OUTFLOW:
        risks.append(f"主力 20 日持续净流出 {f.ff_20d:+.2f} 亿")
    if f.ff_reversal == FUND_REVERSAL_DOWN:
        risks.append("近 5 日主力资金由流入转为流出，趋势可能在切换")
    elif f.ff_reversal == FUND_REVERSAL_UP:
        reasons.append("近 5 日主力资金由流出转为流入，下跌动能在衰竭")
    return reasons, risks


_SIGNAL_COLLECTORS = (
    _collect_macro_signals,
    _collect_price_signals,
    _collect_announcement_signals,
    _collect_fund_flow_signals,
)


# ── confidence_trace 工具 ────────────────────────────────────────
# 让 0.78 这个数字不再是黑盒：每次 confidence 变化都记一行
# {step, action, value, delta, rule}，让 caller / Agent / 人类可以反查
# "0.78 是哪几条规则叠加出来的"。供 `spc explain` 子命令展开渲染。


def _new_trace(base: Decimal, base_action: str, base_rule: str) -> list[dict]:
    """初始化 trace。base step 的 delta 等同 value（从 0 起点贡献了 base）。"""
    return [{
        "step": "base",
        "action": base_action,
        "value": float(base),
        "delta": float(base),
        "rule": base_rule,
    }]


def _make_recorder(trace: list[dict]):
    """返回两个闭包：``record(name, action, new_value, rule)`` 用于无条件改 confidence；
    ``raise_to(name, action, candidate, rule)`` 用于 ``max(old, candidate)`` 升档场景。

    后者特殊处理：candidate <= 当前置信度时仍写一条 trace 记 "规则触发但被前序更高
    置信度封顶"，delta=0，便于审计"为什么这条规则没影响最终值"。
    """
    state = {"value": Decimal(str(trace[-1]["value"])), "action": trace[-1]["action"]}

    def record(name: str, action: str, new_value: Decimal, rule: str) -> None:
        delta = new_value - state["value"]
        state["value"] = new_value
        state["action"] = action
        trace.append({
            "step": name, "action": action,
            "value": float(new_value), "delta": float(delta), "rule": rule,
        })

    def raise_to(name: str, action: str, candidate: Decimal, rule: str) -> None:
        if candidate > state["value"]:
            record(name, action, candidate, rule)
        else:
            # 规则触发了，但被前序更高置信度封顶
            state["action"] = action  # action 标签更新，confidence 不变
            trace.append({
                "step": name, "action": action,
                "value": float(state["value"]), "delta": 0.0,
                "rule": rule + "（被前序更高置信度封顶，confidence 未变）",
            })

    def current() -> tuple[str, Decimal]:
        return state["action"], state["value"]

    return record, raise_to, current


# ── 决策分支：持仓 vs 自选 ───────────────────────────────────────

def _decide_for_holding(
    f: Features, max_single_pct: Decimal,
) -> tuple[str, Decimal, list[str], list[dict]]:
    """qty > 0：从 hold 出发，按风险 / 资金 / 现价亏损 / 仓位超限四类信号升降级。"""
    extra_reasons: list[str] = []
    trace = _new_trace(Decimal("0.55"), "hold", "持仓默认起点 hold @ 0.55")
    record, raise_to, current = _make_recorder(trace)

    if f.risk_hits >= 2 or f.regime in LOW_REGIMES:
        record("risk_or_low_regime", "trim", Decimal("0.72"),
               f"risk_hits={f.risk_hits} or regime={f.regime} → trim")

    # 主力资金分层使用：
    #   - 20d 定背景（偏强 / 偏弱）
    #   - 5d/3d 定动作（是否真的要减仓 / 回避）
    #   - 1d 只做提示，不单独触发动作
    if f.ff_regime == FUND_PERSISTENT_OUTFLOW:
        if f.regime in LOW_REGIMES and _ff_negative(f.ff_5d):
            raise_to("ff_outflow+low+5d_neg", "sell", Decimal("0.78"),
                     f"20d 资金弱 + 5d 续出({f.ff_5d:+.2f}yi) + 价格破位({f.regime}) → 升 sell")
            extra_reasons.append("20 日资金背景偏弱，且近 5 日继续流出并叠加价格破位，建议优先退出")
        elif current()[0] == "hold" and _ff_negative(f.ff_5d) and _ff_negative(f.ff_3d):
            raise_to("ff_outflow+5d+3d_neg", "trim", Decimal("0.65"),
                     f"20d 资金弱 + 5d({f.ff_5d:+.2f}) + 3d({f.ff_3d:+.2f}) 续出 → 升 trim")
            extra_reasons.append("20 日资金背景偏弱，且近 5/3 日继续流出，建议先减仓")

    if f.current is not None and f.avg_cost > 0:
        cur_price = to_decimal(f.current, "current")
        if cur_price < f.avg_cost * Decimal("0.92") and f.risk_hits:
            record("price_loss+risk_announce", "sell", Decimal("0.78"),
                   f"现价 {cur_price} < 92% 成本 {f.avg_cost} 且有风险公告 → 强制 sell")
            extra_reasons.append("现价明显低于持仓成本且伴随风险公告")
        elif f.weight_pct > max_single_pct and cur_price > f.avg_cost:
            record("weight_over_cap", "trim", Decimal("0.70"),
                   f"持仓权重 {f.weight_pct}% > 单票上限 {max_single_pct}% 且浮盈 → trim")
            extra_reasons.append("当前仓位超过单票上限且已有浮盈")

    action, confidence = current()
    return action, confidence, extra_reasons, trace


def _decide_for_watching(f: Features) -> tuple[str, Decimal, list[str], list[str], list[dict]]:
    """qty == 0：自选侧从 watch 出发，按风险 / 资金 / buy 候选 / focus 升降级。"""
    extra_reasons: list[str] = []
    extra_risks: list[str] = []
    trace = _new_trace(Decimal("0.55"), "watch", "自选默认起点 watch @ 0.55")
    record, _raise_to, _current = _make_recorder(trace)

    if f.risk_hits >= 2 or f.regime in LOW_REGIMES:
        record("risk_or_low_regime", "avoid", Decimal("0.72"),
               f"risk_hits={f.risk_hits} or regime={f.regime} → avoid")
        return "avoid", Decimal("0.72"), extra_reasons, extra_risks, trace

    if f.ff_regime == FUND_PERSISTENT_OUTFLOW and _ff_negative(f.ff_5d) and _ff_negative(f.ff_3d):
        # 20 日资金背景偏弱，且近 5/3 日继续流出，才直接回避
        record("ff_outflow+5d+3d_neg", "avoid", Decimal("0.68"),
               f"20d 资金弱 + 5d({f.ff_5d:+.2f}) + 3d({f.ff_3d:+.2f}) 续出 → avoid")
        return "avoid", Decimal("0.68"), extra_reasons, extra_risks, trace

    buy_eval = _evaluate_self_select_buy(
        f.regime, f.risk_hits, f.positive_hits, f.change_pct,
        f.ff_regime, f.ff_3d, f.ff_5d, f.ff_reversal, f.market_regime,
    )
    if buy_eval is not None:
        action, confidence, buy_reason = buy_eval
        # buy_reason 文案里已经标了 trend / reversal / RISK_OFF 降级路径，
        # 直接拿来当 rule，trace 不再细拆 trend/reversal 内部
        record("self_select_buy_path", action, confidence, buy_reason[:120])
        extra_reasons.append(buy_reason)
        return action, confidence, extra_reasons, extra_risks, trace

    if f.positive_hits > f.risk_hits and f.regime not in LOW_REGIMES:
        record("positive_over_risk_no_low", "focus", Decimal("0.62"),
               f"positive_hits={f.positive_hits} > risk_hits={f.risk_hits}, regime={f.regime} → focus")
        extra_reasons.append("正向信号多于风险信号，优先纳入重点盯盘")
        if f.regime not in HIGH_REGIMES:
            extra_risks.append("价格结构尚未达到强趋势买入候选条件")
        if _is_extended_intraday_gain(f.change_pct):
            extra_risks.append("当日涨幅较高，次日不宜直接追高")
        return "focus", Decimal("0.62"), extra_reasons, extra_risks, trace

    # 无任何信号触发，留在 watch；trace 不增加新 step
    extra_reasons.append("建议继续跟踪，等待更清晰的触发条件")
    return "watch", Decimal("0.55"), extra_reasons, extra_risks, trace


def _build_sources(analysis: dict, f: Features) -> list[str]:
    """组装审计用 sources：最近 3 条公告 PDF + price_history.regime + fund_flow + market_regime。"""
    sources: list[str] = []
    announcements = analysis.get("announcements") or []
    for item in announcements[:3]:
        title = item.get("title") or "公告"
        pdf = item.get("pdf_url") or "-"
        sources.append(f"{item.get('date')}: {title} ({pdf})")
    if not sources:
        sources.append("analyze_company.quote")
    sources.append(f"price_history.regime={f.regime or '-'}")
    if f.ff_available:
        def _fmt(v: float | None) -> str:
            return "-" if v is None else f"{v:+.2f}yi"
        sources.append(
            f"fund_flow.regime={f.ff_regime} "
            f"(1d={_fmt(f.ff_1d)}, 3d={_fmt(f.ff_3d)}, 5d={_fmt(f.ff_5d)}, 20d={_fmt(f.ff_20d)})"
        )
    if f.market_regime is not None:
        sources.append(f"market_regime={f.market_regime}")
    return sources


# ── 主入口（调度器）──────────────────────────────────────────────

def _decision_from_analysis(
    snapshot: dict | None,
    analysis: dict,
    capital_total: Decimal,
    max_single_pct: Decimal,
    market_regime: str | None = None,
) -> dict:
    """市场风险偏好软联动（``market_regime``）规则：
    - RISK_OFF：自选侧 buy 候选自动降为 focus（在 ``_evaluate_self_select_buy`` 内处理）；
      持仓侧 hold 仅加风险提示，不强制降仓；已经在 trim / sell 的 confidence + 0.05。
    - RISK_ON：在 reasons 头部加一句宏观正向提示，但不会主动加仓 / 升档。
    - NEUTRAL / 缺数据：完全不影响 action。
    """
    f = _extract_features(snapshot, analysis, capital_total, market_regime)

    # 1. 各维度信号收集（只贡献 reasons / risks 文案，不触发 action）
    reasons: list[str] = []
    risks: list[str] = []
    for collector in _SIGNAL_COLLECTORS:
        r, k = collector(f)
        reasons.extend(r)
        risks.extend(k)

    # 2. 决策分支：持仓侧 vs 自选侧
    if f.qty > 0:
        action, confidence, more_reasons, trace = _decide_for_holding(f, max_single_pct)
        reasons.extend(more_reasons)
        if action == "hold" and not reasons:
            reasons.append("当前没有触发明显的减仓或卖出信号")
    else:
        action, confidence, more_reasons, more_risks, trace = _decide_for_watching(f)
        reasons.extend(more_reasons)
        risks.extend(more_risks)

    # 3. RISK_OFF 给 trim / sell 升一点 confidence（防御动作得到宏观背书）
    if f.market_regime == MARKET_REGIME_RISK_OFF and action in ("trim", "sell"):
        new_conf = min(Decimal("0.95"), confidence + Decimal("0.05"))
        delta = new_conf - confidence
        trace.append({
            "step": "macro_risk_off_boost",
            "action": action,
            "value": float(new_conf),
            "delta": float(delta),
            "rule": (
                "大盘 RISK_OFF 给防御动作 (trim/sell) +0.05"
                + ("" if delta > 0 else "（被 0.95 上限封顶，confidence 未变）")
            ),
        })
        confidence = new_conf

    # 4. fallback
    if not reasons and f.current is not None:
        reasons.append(f"最新价为 {f.current}")

    # 5. 审计 sources
    sources = _build_sources(analysis, f)

    return {
        "action": action,
        "action_label": ACTION_LABELS.get(action, action),
        "description": ACTION_DESCRIPTIONS.get(action, ""),
        "confidence": decimal_str(confidence),
        "reasoning": reasons,
        "risks": risks,
        "sources": sources,
        "weight_pct": decimal_str(f.weight_pct),
        "confidence_trace": trace,
    }


def _is_extended_intraday_gain(change_pct: object) -> bool:
    if change_pct is None:
        return False
    try:
        return Decimal(str(change_pct)) >= Decimal("8")
    except Exception:  # noqa: BLE001
        return False


def _ff_negative(value: float | None) -> bool:
    return value is not None and value < 0


def _is_trend_buy_candidate(
    regime: str | None,
    risk_hits: int,
    positive_hits: int,
    change_pct: object,
    ff_regime: str | None = None,
    ff_3d: float | None = None,
    ff_5d: float | None = None,
) -> bool:
    """趋势跟随路径（突破创新高型）：

    - regime 必须 ∈ HIGH_REGIMES（创年内 / 52w / 历史新高）
    - 至少 1 条正向公告，0 条风险公告
    - 当日涨幅不过热（避免追高）
    - 主力资金不持续流出，且近 3/5 日累计不为负（数据缺失则跳过该约束）

    本路径偏好"右侧确认"：用价格本身的新高来代理趋势确认。
    """
    if risk_hits != 0 or positive_hits <= 0:
        return False
    if regime not in HIGH_REGIMES:
        return False
    if _is_extended_intraday_gain(change_pct):
        return False
    if ff_regime is not None:
        if ff_regime == FUND_PERSISTENT_OUTFLOW:
            return False
        if ff_3d is not None and ff_3d < 0:
            return False
        if ff_5d is not None and ff_5d < 0:
            return False
    return positive_hits >= 2 or positive_hits > risk_hits


def _is_reversal_buy_candidate(
    regime: str | None,
    risk_hits: int,
    positive_hits: int,
    change_pct: object,
    ff_regime: str | None = None,
    ff_3d: float | None = None,
    ff_5d: float | None = None,
    ff_reversal: str | None = None,
) -> bool:
    """反转买入路径（左侧抢反转型）：

    - regime 必须 ∈ MID_REGIMES（接近年内低位 / 区间内 / 接近年内高位）
      —— 显式排除 LOW_REGIMES（创新低不允许 buy）和 HIGH_REGIMES（那是趋势路径）
    - 必须有"资金已经掉头"的硬证据：
        fund_flow.reversal == OUTFLOW_TO_INFLOW，**或**
        fund_flow.regime == PERSISTENT_INFLOW（已经是持续流入背景）
    - 近 3 日 + 近 5 日累计资金都为正（确保不是单日反弹）
    - 至少 2 条正向公告，0 条风险公告（比趋势路径要求更高）
    - 当日涨幅不过热

    主力资金缺数据时，该路径**不**生效（反转买入比趋势买入更需要资金背书，
    缺数据宁可让标的留在 focus）。
    """
    if risk_hits != 0:
        return False
    if positive_hits < 2:
        return False
    if regime not in MID_REGIMES:
        return False
    if _is_extended_intraday_gain(change_pct):
        return False
    if ff_regime is None:
        # 反转路径：缺资金数据则不允许 buy
        return False
    if ff_regime == FUND_PERSISTENT_OUTFLOW:
        return False
    direction_ok = (ff_reversal == FUND_REVERSAL_UP) or (ff_regime == FUND_PERSISTENT_INFLOW)
    if not direction_ok:
        return False
    if ff_3d is None or ff_3d <= 0:
        return False
    if ff_5d is None or ff_5d <= 0:
        return False
    return True


def _is_strict_buy_candidate(
    regime: str | None,
    risk_hits: int,
    positive_hits: int,
    change_pct: object,
    ff_regime: str | None = None,
    ff_3d: float | None = None,
    ff_5d: float | None = None,
    ff_reversal: str | None = None,
) -> tuple[bool, str | None]:
    """聚合调度：返回 (是否 buy 候选, 触发路径名)。

    路径名：``"trend"`` / ``"reversal"`` / ``None``。caller 据此区分 reasons 文案，
    让人 / LLM 一眼知道是"突破跟随买入"还是"左侧反转买入"——两者风险性质不同。
    """
    if _is_trend_buy_candidate(regime, risk_hits, positive_hits, change_pct, ff_regime, ff_3d, ff_5d):
        return True, "trend"
    if _is_reversal_buy_candidate(
        regime, risk_hits, positive_hits, change_pct, ff_regime, ff_3d, ff_5d, ff_reversal,
    ):
        return True, "reversal"
    return False, None


def _evaluate_self_select_buy(
    regime: str | None,
    risk_hits: int,
    positive_hits: int,
    change_pct: object,
    ff_regime: str | None,
    ff_3d: float | None,
    ff_5d: float | None,
    ff_reversal: str | None,
    market_regime: str | None,
) -> tuple[str, Decimal, str] | None:
    """自选侧 buy 决策评估，返回 (action, confidence, reason_msg) 或 None。

    None 表示不构成 buy 候选，调用方应继续往下走 focus / watch 分支。
    返回非 None 时：
      - 大盘 RISK_OFF：action 自动降为 ``focus``，置信度更低，reason 注明降级原因
      - 否则：action 为 ``buy``，trend 路径置信度更高（0.72），reversal 较低（0.68）
    """
    buy_ok, buy_path = _is_strict_buy_candidate(
        regime, risk_hits, positive_hits, change_pct, ff_regime, ff_3d, ff_5d, ff_reversal,
    )
    if not buy_ok:
        return None
    if buy_path == "trend":
        trend_msg = (
            "【趋势跟随路径】强趋势（创新高）+ 正向信号 + 主力资金短中期不撤离 + 不过热涨幅同时满足"
        )
        if market_regime == MARKET_REGIME_RISK_OFF:
            return (
                "focus",
                Decimal("0.65"),
                trend_msg + "；但大盘 RISK_OFF，先降级为 focus 等大盘修复",
            )
        return "buy", Decimal("0.72"), trend_msg
    if buy_path == "reversal":
        reversal_msg = (
            "【反转买入路径】非破位区间 + 资金已反向流入（近 3/5 日累计转正）+ 多重正向公告 + 不过热涨幅；"
            "属于左侧 / 反转型 buy，置信度低于趋势型"
        )
        if market_regime == MARKET_REGIME_RISK_OFF:
            return (
                "focus",
                Decimal("0.62"),
                reversal_msg + "；大盘 RISK_OFF，先降级为 focus 等大盘修复",
            )
        return "buy", Decimal("0.68"), reversal_msg
    return None


def _digits_from_symbol(symbol: str) -> str:
    return "".join(ch for ch in str(symbol) if ch.isdigit())


def _discover_opportunities(results: list[dict], analysis_cache: dict[tuple[str, str], dict], provider) -> list[dict]:
    universe = {(item["market"], item["code"]) for item in results}
    opportunities: dict[tuple[str, str], dict] = {}

    for item in results:
        market = item["market"]
        code = item["code"]
        analysis = analysis_cache.get((market, code), {})
        peers = analysis.get("peers") or []
        concepts = analysis.get("concepts") or []
        if market != "a":
            continue
        for peer in peers:
            sym = peer.get("symbol", "")
            peer_code = _digits_from_symbol(sym)
            if not peer_code:
                continue
            key = ("a", peer_code)
            if key in universe:
                continue
            score = Decimal("0")
            percent = peer.get("percent")
            ytd = peer.get("ytd_pct")
            inflow = peer.get("main_inflow_yi")
            if isinstance(percent, (int, float)) and percent > 0:
                score += Decimal("1")
            if isinstance(ytd, (int, float)) and ytd > 0:
                score += Decimal("1")
            if isinstance(inflow, (int, float)) and inflow > 0:
                score += Decimal("1")
            if score <= 0:
                continue
            reason_parts = [f"来自你关注标的 {code} 的同概念/同板块 peer"]
            if concepts:
                reason_parts.append("概念：" + " / ".join(concepts[:3]))
            if isinstance(percent, (int, float)):
                reason_parts.append(f"当日涨跌幅 {percent:+.2f}%")
            opportunities[key] = {
                "market": "a",
                "code": peer_code,
                "name": peer.get("name") or sym,
                "kind": "peer",
                "score": decimal_str(score),
                "reasons": reason_parts,
            }

    boards_to_check = [("all_a", "gainers"), ("all_a", "main_inflow"), ("hk", "gainers")]
    for board_market, board_name in boards_to_check:
        try:
            board_data = provider.market_board(board_market, board_name, top=8)
        except Exception:  # noqa: BLE001
            continue
        for stock in board_data.get("items", []):
            sym = stock.get("symbol", "")
            name = stock.get("name") or sym
            if board_market == "hk":
                market = "hk"
                code = _digits_from_symbol(sym).zfill(5)
            else:
                market = "a"
                code = _digits_from_symbol(sym)
            if not code:
                continue
            key = (market, code)
            if key in universe or key in opportunities:
                continue
            market_cap_yi = stock.get("market_cap_yi")
            amount_yi = stock.get("amount_yi")
            if board_market == "hk":
                if isinstance(market_cap_yi, (int, float)) and market_cap_yi < 150:
                    continue
                if isinstance(amount_yi, (int, float)) and amount_yi < 3:
                    continue
            else:
                if isinstance(market_cap_yi, (int, float)) and market_cap_yi < 80:
                    continue
                if isinstance(amount_yi, (int, float)) and amount_yi < 5:
                    continue
            reason_parts = [f"全市场 {board_name} 榜靠前"]
            percent = stock.get("percent")
            if isinstance(percent, (int, float)):
                reason_parts.append(f"当日涨跌幅 {percent:+.2f}%")
            main_yi = stock.get("main_yi")
            if isinstance(main_yi, (int, float)) and main_yi > 0:
                reason_parts.append(f"主力净流入 {main_yi:+.2f} 亿")
            opportunities[key] = {
                "market": market,
                "code": code,
                "name": name,
                "kind": "market",
                "score": "1",
                "reasons": reason_parts,
            }

    ranked = sorted(
        opportunities.values(),
        key=lambda item: (
            1 if item["kind"] == "peer" else 0,
            Decimal(item["score"]),
            item["market"],
            item["code"],
        ),
        reverse=True,
    )
    return ranked[:8]


def analyze_now(conn, account_id: int, account_slug: str, account_display_name: str, scope: str, market: str | None = None, code: str | None = None, analysis_provider=None) -> dict:
    ensure_defaults(conn)
    provider = analysis_provider or StockMarketHubProvider()
    if market:
        market = normalize_market(market)
    if market and code:
        code = normalize_code(market, code)
    sync_portfolio(conn, account_id, market=market, code=code, analysis_provider=provider)
    snapshots = {(item["market"], item["code"]): item for item in latest_snapshots(conn, account_id)}
    caps = capital_settings(conn, account_id)
    capital_total = to_decimal(caps["total_cny"], "capital.total_cny")
    max_single_pct = to_decimal(caps["max_single_position_pct"], "capital.max_single_position_pct")

    results = []
    analysis_cache: dict[tuple[str, str], dict] = {}
    targets = _select_targets(conn, account_id, scope, market, code)

    # 大盘 regime 软联动：在循环前按 targets 实际涉及的市场拉一次，
    # A 股目标用 a regime，港股目标用 hk regime（不交叉）。
    market_regime_payload: dict[str, dict] = {}
    market_regime_label: dict[str, str | None] = {}
    involved_markets = {tgt_market for tgt_market, _c, _s in targets if tgt_market in ("a", "hk")}
    for mkt in involved_markets:
        try:
            mr = provider.get_market_regime(mkt) if hasattr(provider, "get_market_regime") else None
        except Exception:  # noqa: BLE001
            mr = None
        if mr:
            market_regime_payload[mkt] = mr
            market_regime_label[mkt] = mr.get("regime")
        else:
            market_regime_label[mkt] = None

    for tgt_market, tgt_code, tgt_scope in targets:
        analysis = provider.analyze(tgt_market, tgt_code, with_peers=(tgt_market == "a"))
        analysis_cache[(tgt_market, tgt_code)] = analysis
        snapshot = snapshots.get((tgt_market, tgt_code))
        decision = _decision_from_analysis(
            snapshot,
            analysis,
            capital_total,
            max_single_pct,
            market_regime=market_regime_label.get(tgt_market),
        )
        name = _extract_security_name(analysis)
        results.append(
            {
                "account": {"slug": account_slug, "display_name": account_display_name},
                "market": tgt_market,
                "code": tgt_code,
                "name": name,
                "scope": tgt_scope,
                "position": snapshot,
                "market_data": {
                    "last_price": analysis.get("quote", {}).get("current"),
                    "change_pct": analysis.get("quote", {}).get("percent"),
                    "as_of": analysis.get("fetched_at"),
                },
                "decision": decision,
            }
        )

    opportunities = _discover_opportunities(results, analysis_cache, provider)

    payload = {
        "account": {"slug": account_slug, "display_name": account_display_name},
        "scope": scope,
        "requested_market": market,
        "requested_code": code,
        "results": results,
        "opportunities": opportunities,
        "capital_total_cny": decimal_str(capital_total),
        "max_single_position_pct": decimal_str(max_single_pct),
        "market_regime": market_regime_payload,  # {"a": {...}, "hk": {...}}
    }
    save_analysis_run(conn, account_id, scope, market, code, payload)
    return payload


def render_analysis_text(payload: dict) -> str:
    lines = []
    account = payload.get("account", {})
    if account:
        lines.append(f"账户：{account.get('display_name', '-')} ({account.get('slug', '-')})")
        lines.append("")

    market_regime = payload.get("market_regime") or {}
    if market_regime:
        regime_display = {
            MARKET_REGIME_RISK_OFF: "🔴 RISK_OFF（避险）",
            MARKET_REGIME_NEUTRAL: "⚪️ NEUTRAL（中性）",
            MARKET_REGIME_RISK_ON: "🟢 RISK_ON（进攻）",
        }
        lines.append("== 大盘风险偏好 ==")
        for mkt_key, mkt_label in (("a", "A 股"), ("hk", "港股")):
            mr = market_regime.get(mkt_key)
            if not mr:
                continue
            regime = mr.get("regime") or "-"
            label = regime_display.get(regime, regime)
            lines.append(f"{mkt_label}：{label}")
            for r in mr.get("reasons", [])[:2]:
                lines.append(f"  - {r}")
            indices = mr.get("indices") or []
            for idx in indices:
                if idx.get("error"):
                    continue
                lines.append(
                    f"  · {idx.get('name')} {idx.get('close')} "
                    f"(距 52w 高 {idx.get('from_52w_high_pct'):+.2f}%, "
                    f"YTD {idx.get('ytd_pct') or 0:+.2f}%, "
                    f"年线 {'站上' if idx.get('above_ma200') else '跌破'})"
                )
        lines.append("")
        lines.append("软联动：RISK_OFF 时，自选侧本应 buy 的标的会自动降为 focus；持仓侧 hold 仅加风险提示，不强制减仓。")
        lines.append("")

    for item in payload.get("results", []):
        market = item["market"]
        code = item["code"]
        name = item.get("name") or ""
        position = item.get("position") or {}
        decision = item["decision"]
        target_line = f"标的：{market.upper()} {code}"
        if name:
            target_line += f" {name}"
        lines.append(target_line)
        action_label = decision.get("action_label") or decision["action"]
        lines.append(f"建议：{action_label}（{decision['action']}）")
        if decision.get("description"):
            lines.append(f"说明：{decision['description']}")
        md = item["market_data"]
        if md.get("last_price") is not None:
            price_str = f"最新价：{md['last_price']}"
            if md.get("change_pct") is not None:
                price_str += f"（{'涨' if float(md['change_pct']) >= 0 else '跌'}{abs(float(md['change_pct'])):.2f}%）"
            lines.append(price_str)
        if md.get("as_of"):
            lines.append(f"时间：截至 {md['as_of']}")
        if position:
            lines.append(
                f"持仓：{position.get('qty')} 股，摊薄成本 {position.get('avg_cost_price')} {position.get('currency')}"
            )
        lines.append(f"置信度：{decision['confidence']}")
        lines.append("理由：")
        for reason in decision["reasoning"]:
            lines.append(f"- {reason}")
        if decision["risks"]:
            lines.append("风险：")
            for risk in decision["risks"]:
                lines.append(f"- {risk}")
        lines.append("数据来源：")
        for source in decision["sources"]:
            lines.append(f"- {source}")
        lines.append("")
    opportunities = payload.get("opportunities") or []
    if opportunities:
        lines.append("可额外关注的标的：")
        for item in opportunities:
            lines.append(f"- {item['market'].upper()} {item['code']} {item['name']}：{'；'.join(item['reasons'])}")
    return "\n".join(lines).strip()

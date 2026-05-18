from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from spc_core.ledger import latest_snapshots, list_watch, save_analysis_run
from spc_core.market_bridge import StockMarketHubProvider
from spc_core.portfolio import sync_portfolio
from spc_core.settings import capital_settings, ensure_defaults
from spc_core.utils import (
    ETF_CATEGORY_BOND,
    ETF_CATEGORY_COMMODITY,
    ETF_CATEGORY_CROSS_BORDER,
    decimal_str,
    etf_category as etf_category_fn,
    is_etf as is_etf_fn,
    normalize_code,
    normalize_market,
    q_money,
    to_decimal,
)


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
MARKET_A = "a"
MARKET_HK = "hk"
ACTION_LABELS = {
    "add": "加仓",
    "avoid": "回避",
    "buy": "买入候选",
    "focus": "重点关注",
    "hold": "持有",
    "probe": "试探买入",
    "sell": "卖出",
    "trim": "减仓",
    "watch": "观察",
}
ACTION_DESCRIPTIONS = {
    "add": (
        "已持仓且趋势/反转信号依然成立，仓位仍有空间且未亏损，可考虑分批加仓；"
        "建议不要一次顶满，配合开盘承接情况执行"
    ),
    "avoid": "风险或价格结构较差，暂不纳入交易候选",
    "buy": "满足更严格的买入候选条件，仍需结合开盘承接和仓位计划执行",
    "focus": "宽松信号较好，优先加入盯盘清单，但还没有达到买入条件",
    "hold": "持仓未触发明确处理信号",
    "probe": "港股大盘弱市下，反转买入候选降档为试探：先用常规仓位 1/4-1/3 建首仓，确认修复后再加第二笔",
    "sell": "持仓亏损与风险信号同时触发，优先考虑退出",
    "trim": "仓位、风险、止盈或价格结构触发减仓条件",
    "watch": "继续跟踪，等待更清晰的触发条件",
}

# ─────────────────────────────────────────────────────────────────
# P0/P1/P2 持仓侧策略默认参数
#
# 这些值都可以被 account_settings 里的同名 key 覆盖，让不同账户（短线 / 长线 /
# ETF 组合）跑不同风格的止盈止损：
#   decision.hard_stop_pct.{a_stock|hk_stock|etf}.{t1|t2|t3}
#     - 三档分级硬止损（详见下方 P0a 注释）
#     - 旧的 decision.hard_stop_pct.{a_stock|hk_stock|etf}（无后缀）已废弃
#   decision.take_profit.t1_pct / .t2_pct / .t3_pct
#   decision.add_position.weight_headroom_ratio  (默认 0.85 = 加仓时单票上限留 15% 缓冲)
#   decision.trailing_stop.pct                   (默认 0.15 = 从持仓期间最高价回撤 15% 触发 trim)
#   decision.trailing_stop.severe_pct            (默认 0.25 = 回撤 25% 直接升 sell)
# ─────────────────────────────────────────────────────────────────

# P0a 分级硬止损：把"单一硬切 sell"拆成三道防线 + 大盘 regime 软联动。
#
# 设计动机：
#   - 单一阈值（如 A 股 10%）容易在"跌停 + 短期回踩"场景被误触发，强制 sell
#     在地板上；事实上 A 股跌停后第二天反弹概率不低，趋势走坏需要更深跌幅佐证
#   - 高位接盘 + 短期回踩 10-15% 是牛股常见结构，硬切 sell 会切掉后续上涨
#   - 真正"已经深套、不再幻想"的硬底线应该比 10% 更深（A 股 18%、港股 25%）
#
# T1（首道防线）：减半锁损，给修复机会。多数情况下应停留在 trim 状态
# T2（深防线）：再减一半，最多留 25% 仓位观察，等待反弹或继续止损
# T3（硬底线）：强制全退，已经损失惨重不再幻想，sell @ 0.85
#
# A 股波动 < 港股 < ETF（行业 ETF）；阈值梯度按市场特性设。
_HARD_STOP_T1_A = Decimal("0.08")   # 8%
_HARD_STOP_T2_A = Decimal("0.12")   # 12%
_HARD_STOP_T3_A = Decimal("0.18")   # 18%

_HARD_STOP_T1_HK = Decimal("0.12")  # 12%
_HARD_STOP_T2_HK = Decimal("0.18")  # 18%
_HARD_STOP_T3_HK = Decimal("0.25")  # 25%

_HARD_STOP_T1_ETF = Decimal("0.10")  # 10%
_HARD_STOP_T2_ETF = Decimal("0.15")  # 15%
_HARD_STOP_T3_ETF = Decimal("0.22")  # 22%

# P0b 分级止盈：浮盈达到不同档位触发 trim / sell
_TAKE_PROFIT_T1 = Decimal("0.20")   # 第一级：≥ 20% 浮盈
_TAKE_PROFIT_T2 = Decimal("0.50")   # 第二级：≥ 50% 浮盈
_TAKE_PROFIT_T3 = Decimal("1.00")   # 第三级：≥ 100% 浮盈

# P1a 加仓：单票权重达到上限的多少时不再建议加仓
_ADD_POSITION_HEADROOM_RATIO = Decimal("0.85")

# P2b trailing stop：默认 15% 触发 trim、25% 升 sell
_TRAILING_STOP_PCT = Decimal("0.15")
_TRAILING_STOP_SEVERE_PCT = Decimal("0.25")


def _account_decimal_setting(conn, account_id: int, key: str, default: Decimal) -> Decimal:
    """从 account_settings 读 Decimal，缺失则用默认。

    解耦：本模块尽量保持 pure，但实在需要拿 account 配置时通过 setter 注入。
    解析失败（脏数据）回退到默认值，避免决策因配置错误整体崩。
    """
    if conn is None or account_id is None:
        return default
    try:
        from spc_core.settings import get_account_setting
        raw = get_account_setting(conn, account_id, key)
    except Exception:  # noqa: BLE001
        return default
    if raw is None or raw == "":
        return default
    try:
        return to_decimal(raw, key)
    except Exception:  # noqa: BLE001
        return default


def _resolve_decision_params(conn, account_id: int) -> dict:
    """读取本账户的策略参数，返回 dict 供 _decision_from_analysis 使用。

    所有参数都有兜底默认值，缺配置时使用代码里的常量。
    """
    return {
        # P0a 分级硬止损：三档 × 三市场。旧的 decision.hard_stop_pct.{a_stock,hk_stock,etf}
        # 单 key 已废弃（被分档替代）；建议用户改用 .t1/.t2/.t3 后缀。
        "hard_stop_a_t1": _account_decimal_setting(
            conn, account_id, "decision.hard_stop_pct.a_stock.t1", _HARD_STOP_T1_A,
        ),
        "hard_stop_a_t2": _account_decimal_setting(
            conn, account_id, "decision.hard_stop_pct.a_stock.t2", _HARD_STOP_T2_A,
        ),
        "hard_stop_a_t3": _account_decimal_setting(
            conn, account_id, "decision.hard_stop_pct.a_stock.t3", _HARD_STOP_T3_A,
        ),
        "hard_stop_hk_t1": _account_decimal_setting(
            conn, account_id, "decision.hard_stop_pct.hk_stock.t1", _HARD_STOP_T1_HK,
        ),
        "hard_stop_hk_t2": _account_decimal_setting(
            conn, account_id, "decision.hard_stop_pct.hk_stock.t2", _HARD_STOP_T2_HK,
        ),
        "hard_stop_hk_t3": _account_decimal_setting(
            conn, account_id, "decision.hard_stop_pct.hk_stock.t3", _HARD_STOP_T3_HK,
        ),
        "hard_stop_etf_t1": _account_decimal_setting(
            conn, account_id, "decision.hard_stop_pct.etf.t1", _HARD_STOP_T1_ETF,
        ),
        "hard_stop_etf_t2": _account_decimal_setting(
            conn, account_id, "decision.hard_stop_pct.etf.t2", _HARD_STOP_T2_ETF,
        ),
        "hard_stop_etf_t3": _account_decimal_setting(
            conn, account_id, "decision.hard_stop_pct.etf.t3", _HARD_STOP_T3_ETF,
        ),
        "tp_t1": _account_decimal_setting(
            conn, account_id, "decision.take_profit.t1_pct", _TAKE_PROFIT_T1,
        ),
        "tp_t2": _account_decimal_setting(
            conn, account_id, "decision.take_profit.t2_pct", _TAKE_PROFIT_T2,
        ),
        "tp_t3": _account_decimal_setting(
            conn, account_id, "decision.take_profit.t3_pct", _TAKE_PROFIT_T3,
        ),
        "add_headroom": _account_decimal_setting(
            conn, account_id, "decision.add_position.weight_headroom_ratio",
            _ADD_POSITION_HEADROOM_RATIO,
        ),
        "trail_pct": _account_decimal_setting(
            conn, account_id, "decision.trailing_stop.pct", _TRAILING_STOP_PCT,
        ),
        "trail_severe_pct": _account_decimal_setting(
            conn, account_id, "decision.trailing_stop.severe_pct", _TRAILING_STOP_SEVERE_PCT,
        ),
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
    """挑出本次 analyze 要跑的标的。

    持仓侧过滤策略：``qty <= 0`` 的清仓快照不再算"在持"。
    这跟 ``portfolio show`` 默认隐藏 qty=0 的行为对齐——保留 trade_ledger 已实现盈亏，
    但不在每次 analyze 里再把它们当作"持仓"分析（既费时间也产生噪声）。

    单标的查询（``market + code`` 同时给）走 ``single`` 通道：不论持仓状态都允许 spot-check，
    用户可能就是想看一只已清仓标的现在跌没跌透 / 适不适合重新建仓。
    """
    targets: list[tuple[str, str, str]] = []
    if market and code:
        return [(market, code, "single")]
    if scope in {"holdings", "all"}:
        for snap in latest_snapshots(conn, account_id):
            qty = to_decimal(snap.get("qty") or "0", "snapshot.qty")
            if qty <= 0:
                continue
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
    # 标的元数据
    market: str
    code: str
    is_etf: bool
    etf_category: str | None
    # 行情
    current: object | None
    change_pct: object | None
    amount_yi: float | None       # 当日成交额（亿元），ETF 流动性判断用
    market_cap_yi: float | None   # 总市值 / ETF 规模（亿元）
    # 价格 regime
    regime: str | None
    # 公告关键词命中数（ETF 永远为 0）
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
    ff_time_label: str | None
    ff_1d: float | None
    ff_3d: float | None
    ff_5d: float | None
    ff_10d: float | None
    ff_20d: float | None
    # 多周期交叉验证（来自 shared/stock_core/fund_flow.py::cross_validate）
    # 缺数据 / 老缓存没这个字段时为 None；决策树读到 None 时退化为只看 regime/reversal。
    ff_cross: dict | None
    # 大盘
    market_regime: str | None
    # 持仓期间最高价（trailing stop 用）。无持仓 / 缺数据时为 None。
    peak_price: Decimal | None
    # 当前账户对该标的的 active execution_plan 的预案价位（planned / partially_filled）。
    # 无预案时为 None。如果有多条非终态 plan，取最近一条。
    plan_stop_loss_price: Decimal | None
    plan_take_profit_price: Decimal | None
    # P0a 分档幂等性（schema v5）：上次已触发的 trim tier 标记，避免重复建议同档减仓。
    # None 表示尚未在本 lifecycle 触发过 trim；"T1" / "T2" 表示已触发。
    # T3 (sell) 不进 last_trim_tier，因为 sell 守卫天然防重复。
    last_trim_tier: str | None = None
    last_trim_price: Decimal | None = None
    last_trim_time: str | None = None
    # 持仓豁免硬止损（P0a / L4），用于历史遗留深套仓位等场景
    exempt_hard_stop: bool = False


def _extract_features(
    snapshot: dict | None,
    analysis: dict,
    capital_total: Decimal,
    market_regime: str | None,
    *,
    peak_price: Decimal | None = None,
    plan_stop_loss_price: Decimal | None = None,
    plan_take_profit_price: Decimal | None = None,
    last_trim_tier: str | None = None,
    last_trim_price: Decimal | None = None,
    last_trim_time: str | None = None,
    exempt_hard_stop: bool = False,
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

    # 标的市场 / 代码（ETF 识别用）
    target_market = snapshot.get("market") if snapshot else str(analysis.get("market") or "")
    target_code = snapshot.get("code") if snapshot else str(analysis.get("code") or "")
    etf_flag = is_etf_fn(target_market, target_code)
    etf_cat = etf_category_fn(target_code) if etf_flag else None

    # 成交额 / 总市值（元 → 亿元，用于 ETF 流动性 / 规模判断）
    amount = quote.get("amount")
    market_cap = quote.get("market_capital")
    amount_yi = (amount / 1e8) if isinstance(amount, (int, float)) and amount else None
    market_cap_yi = (market_cap / 1e8) if isinstance(market_cap, (int, float)) and market_cap else None

    return Features(
        market=target_market,
        code=target_code,
        is_etf=etf_flag,
        etf_category=etf_cat,
        current=quote.get("current"),
        change_pct=quote.get("percent"),
        amount_yi=amount_yi,
        market_cap_yi=market_cap_yi,
        regime=price_history.get("regime"),
        risk_hits=risk_hits,
        positive_hits=positive_hits,
        qty=qty,
        avg_cost=avg_cost,
        weight_pct=weight_pct,
        ff_available=ff_available,
        ff_regime=fund_flow.get("regime") if ff_available else None,
        ff_reversal=fund_flow.get("reversal") if ff_available else None,
        ff_time_label=fund_flow.get("flow_label") if ff_available else None,
        ff_1d=_ff_yi("1d"),
        ff_3d=_ff_yi("3d"),
        ff_5d=_ff_yi("5d"),
        ff_10d=_ff_yi("10d"),
        ff_20d=_ff_yi("20d"),
        ff_cross=(fund_flow.get("cross_validation") if ff_available else None),
        market_regime=market_regime,
        peak_price=peak_price,
        plan_stop_loss_price=plan_stop_loss_price,
        plan_take_profit_price=plan_take_profit_price,
        last_trim_tier=last_trim_tier,
        last_trim_price=last_trim_price,
        last_trim_time=last_trim_time,
        exempt_hard_stop=exempt_hard_stop,
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
    # ETF 没有上市公司公告，跳过这个维度
    if f.is_etf:
        return reasons, risks
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
    ff_suffix = f"（资金流口径：{f.ff_time_label}）" if f.ff_time_label else ""
    if f.ff_regime == FUND_PERSISTENT_INFLOW:
        reasons.append(f"主力 20 日持续净流入 {f.ff_20d:+.2f} 亿{ff_suffix}")
    elif f.ff_regime == FUND_PERSISTENT_OUTFLOW:
        risks.append(f"主力 20 日持续净流出 {f.ff_20d:+.2f} 亿{ff_suffix}")
    if f.ff_reversal == FUND_REVERSAL_DOWN:
        risks.append(f"近 5 日主力资金由流入转为流出，趋势可能在切换{ff_suffix}")
    elif f.ff_reversal == FUND_REVERSAL_UP:
        reasons.append(f"近 5 日主力资金由流出转为流入，下跌动能在衰竭{ff_suffix}")
    return reasons, risks


def _ff_suffix(label: str | None) -> str:
    return f"（资金流口径：{label}）" if label else ""


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


# ── ETF 专用决策路径 ─────────────────────────────────────────────
# ETF 没有公告 / 管理层 / 股东 / 同业等维度，沿用股票决策树会导致
# positive_hits / risk_hits 永远为 0，所有 ETF 永远卡在 watch / hold。
# 这里给 ETF 一套独立决策规则，只用：
#   - 价格 regime（K 线驱动）
#   - 主力资金（1d/3d/5d/20d + regime + reversal）
#   - 大盘 regime（A 股 RISK_ON/OFF）
#   - 成交额（流动性硬检查）
#   - ETF 子类型（跨境 QDII 资金面参考价值低 / 商品 / 债券资金面无意义）

# 流动性阈值：成交额低于此值时直接 avoid（流动性陷阱）
_ETF_TURNOVER_AVOID_YI = 0.3       # 0.3 亿（3000 万）
# 流动性阈值：成交额低于此值时给提示但不阻止决策
_ETF_TURNOVER_WARN_YI = 1.0        # 1 亿
# 残股识别：摊薄成本 > 现价 1.5 倍时打 RESIDUAL 标签
_ETF_RESIDUAL_THRESHOLD = Decimal("1.5")


def _evaluate_etf_self_select_buy(f: Features) -> tuple[str, Decimal, str] | None:
    """ETF 自选侧 buy 决策。返回 (action, confidence, reason) 或 None。

    None 表示不构成 buy 候选，调用方应继续走 focus / watch / avoid 分支。

    决策树：
      1. 流动性硬否决（成交额 < 0.3 亿）→ avoid（不会被这里返回，由 caller 处理）
      2. LOW_REGIMES 创新低 → 永远禁止 buy（返回 None，让 caller 走 avoid）
      3. HIGH_REGIMES + 资金齐声 → trend buy / 跨境只看趋势
      4. MID_REGIMES + PERSISTENT_INFLOW + 3d/5d 正向 → reversal buy
      5. 大盘 RISK_OFF 时 buy 自动降为 focus（同股票逻辑）
    """
    # LOW_REGIMES 永远禁止 buy
    if f.regime in LOW_REGIMES:
        return None

    # 当日过热（≥ 8%）：降级 focus 等回调
    if _is_extended_intraday_gain(f.change_pct):
        return ("focus", Decimal("0.62"),
                f"ETF 信号成立但日涨幅 {f.change_pct}% 过热，降级 focus 等回调")

    cross_border = (f.etf_category == ETF_CATEGORY_CROSS_BORDER)
    commodity_or_bond = (f.etf_category in (ETF_CATEGORY_COMMODITY, ETF_CATEGORY_BOND))

    # ── 趋势 ETF buy（HIGH_REGIMES）──
    if f.regime in HIGH_REGIMES:
        if cross_border:
            # 跨境 QDII：资金面参考价值低，仅看价格趋势 + 大盘
            base_msg = (
                "【ETF 趋势路径-跨境】跨境 QDII 价格创新高，A 股主力资金面参考价值低，"
                "仅依据价格趋势 + 大盘判断"
            )
            if f.market_regime == MARKET_REGIME_RISK_OFF:
                return ("focus", Decimal("0.62"),
                        base_msg + _ff_suffix(f.ff_time_label) + "；大盘 RISK_OFF，降级为 focus 等大盘修复")
            return ("buy", Decimal("0.68"), base_msg + _ff_suffix(f.ff_time_label) + "；跨境 ETF 置信度低于本地 ETF")

        if commodity_or_bond:
            # 商品 / 债券 ETF：资金面也无意义
            base_msg = (
                f"【ETF 趋势路径-{f.etf_category}】商品 / 债券 ETF 价格创新高，"
                "资金面分析意义有限，主要看价格突破"
            )
            if f.market_regime == MARKET_REGIME_RISK_OFF:
                return ("focus", Decimal("0.62"), base_msg + _ff_suffix(f.ff_time_label) + "；大盘 RISK_OFF，降级 focus")
            return ("buy", Decimal("0.66"), base_msg + _ff_suffix(f.ff_time_label))

        # 本地主题 ETF（科创 / 创业 / 行业）：要求资金面齐声
        if f.ff_regime == FUND_PERSISTENT_OUTFLOW:
            return ("focus", Decimal("0.62"),
                    "【ETF 趋势路径】价格创新高但主力 PERSISTENT_OUTFLOW，降级 focus" + _ff_suffix(f.ff_time_label))
        # 1d/3d/5d 任一为负即降级（量级齐声）
        ff_neg = [(name, v) for name, v in (("1d", f.ff_1d), ("3d", f.ff_3d), ("5d", f.ff_5d))
                  if v is not None and v < 0]
        if ff_neg:
            details = ", ".join(f"{n}={v:+.2f}yi" for n, v in ff_neg)
            return ("focus", Decimal("0.63"),
                    f"【ETF 趋势路径】价格创新高但近期资金有反向迹象（{details}），降级 focus" + _ff_suffix(f.ff_time_label))
        msg = (
            f"【ETF 趋势路径】价格 {f.regime} + 1d/3d/5d 资金齐正"
            f"（1d={f.ff_1d:+.2f}yi, 3d={f.ff_3d:+.2f}yi, 5d={f.ff_5d:+.2f}yi）→ 趋势买入"
        )
        if f.market_regime == MARKET_REGIME_RISK_OFF:
            return ("focus", Decimal("0.65"), msg + _ff_suffix(f.ff_time_label) + "；大盘 RISK_OFF 降级 focus")
        return ("buy", Decimal("0.72"), msg + _ff_suffix(f.ff_time_label))

    # ── 反转 ETF buy（MID_REGIMES）──
    # 跨境 / 商品 / 债券：不做反转买入（资金面没意义）
    if cross_border or commodity_or_bond:
        return None
    if f.ff_regime != FUND_PERSISTENT_INFLOW:
        return None
    if (f.ff_3d is None or f.ff_3d <= 0) or (f.ff_5d is None or f.ff_5d <= 0):
        return None
    msg = (
        f"【ETF 反转路径】价格 {f.regime} + PERSISTENT_INFLOW + "
        f"3d={f.ff_3d:+.2f}yi/5d={f.ff_5d:+.2f}yi 正向 → 反转买入"
    )
    if f.market_regime == MARKET_REGIME_RISK_OFF:
        return ("focus", Decimal("0.62"), msg + _ff_suffix(f.ff_time_label) + "；大盘 RISK_OFF 降级 focus")
    return ("buy", Decimal("0.68"), msg + _ff_suffix(f.ff_time_label) + "（左侧反转，置信度低于趋势）")


def _decide_etf_for_watching(
    f: Features,
) -> tuple[str, Decimal, list[str], list[str], list[dict]]:
    """ETF 自选侧主决策。"""
    extra_reasons: list[str] = []
    extra_risks: list[str] = []
    trace = _new_trace(Decimal("0.55"), "watch", "ETF 自选默认起点 watch @ 0.55")
    record, _raise_to, _current = _make_recorder(trace)

    # 流动性硬检查（成交额过低 → avoid）
    if f.amount_yi is not None and f.amount_yi < _ETF_TURNOVER_AVOID_YI:
        record("etf_low_liquidity", "avoid", Decimal("0.70"),
               f"ETF 成交额 {f.amount_yi:.2f} 亿过低（< {_ETF_TURNOVER_AVOID_YI}），流动性陷阱")
        extra_risks.append(
            f"ETF 当日成交额 {f.amount_yi:.2f} 亿过低，存在流动性陷阱，避免追买"
        )
        return "avoid", Decimal("0.70"), extra_reasons, extra_risks, trace
    if f.amount_yi is not None and f.amount_yi < _ETF_TURNOVER_WARN_YI:
        extra_risks.append(f"⚠️ ETF 当日成交额 {f.amount_yi:.2f} 亿偏低，注意滑点风险")

    # LOW_REGIMES：avoid
    if f.regime in LOW_REGIMES:
        record("etf_low_regime", "avoid", Decimal("0.70"),
               f"ETF 价格 {f.regime} 创新低 → avoid")
        extra_risks.append(f"ETF 价格 {f.regime} 创新低，不追买")
        return "avoid", Decimal("0.70"), extra_reasons, extra_risks, trace

    # ETF buy 候选评估
    buy_eval = _evaluate_etf_self_select_buy(f)
    if buy_eval is not None:
        action, confidence, reason = buy_eval
        record("etf_buy_path", action, confidence, reason[:120])
        extra_reasons.append(reason)
        return action, confidence, extra_reasons, extra_risks, trace

    # 跨境 ETF 加提示
    if f.etf_category == ETF_CATEGORY_CROSS_BORDER:
        extra_risks.append("跨境 QDII ETF：A 股主力资金面参考价值低，请结合外盘判断")

    # 默认 watch
    extra_reasons.append("ETF 信号不足，继续观察")
    return "watch", Decimal("0.55"), extra_reasons, extra_risks, trace


def _decide_etf_for_holding(
    f: Features,
    max_single_pct: Decimal,
    params: dict | None = None,
) -> tuple[str, Decimal, list[str], list[dict]]:
    """ETF 持仓侧主决策。

    沿用股票持仓侧的"硬止损 / 分级止盈 / 预案价位 / trailing stop"四类风控逻辑，
    并保留 ETF 专属的"创新低 + 资金流出"和"创新高 + 资金分歧"等独有规则。

    ETF 不开启 add 加仓路径——ETF 加仓更适合用户基于宏观 / 主题判断，
    而非系统单标的信号。
    """
    p = params or _resolve_decision_params(None, None)
    extra_reasons: list[str] = []
    trace = _new_trace(Decimal("0.55"), "hold", "ETF 持仓默认起点 hold @ 0.55")
    record, raise_to, current = _make_recorder(trace)

    cross_border = (f.etf_category == ETF_CATEGORY_CROSS_BORDER)
    commodity_or_bond = (f.etf_category in (ETF_CATEGORY_COMMODITY, ETF_CATEGORY_BOND))

    # ── 1. ETF 专属：破位 + 资金 OUTFLOW → sell ─────────────────
    if (not cross_border and not commodity_or_bond
            and f.regime in LOW_REGIMES and f.ff_regime == FUND_PERSISTENT_OUTFLOW):
        record("etf_low_regime+outflow", "sell", Decimal("0.78"),
               f"ETF 创新低（{f.regime}）+ 主力 PERSISTENT_OUTFLOW → sell")
        extra_reasons.append("ETF 创新低 + 主力持续流出，建议止损退出" + _ff_suffix(f.ff_time_label))

    # ── 2. ETF 专属：创新高 + 资金流出（顶部分歧） → trim ───────
    elif (not cross_border and not commodity_or_bond
            and f.regime in HIGH_REGIMES and f.ff_regime == FUND_PERSISTENT_OUTFLOW):
        record("etf_high+outflow", "trim", Decimal("0.68"),
               f"ETF 创新高（{f.regime}）+ 主力 PERSISTENT_OUTFLOW → trim（顶部分歧）")
        extra_reasons.append("ETF 价格创新高但主力持续流出，顶部分歧明显" + _ff_suffix(f.ff_time_label))

    # ── 3. ETF 专属：创新高 + 5d 资金反向（趋势末段） → trim ───
    elif (not cross_border and not commodity_or_bond
            and f.regime in HIGH_REGIMES and f.ff_reversal == FUND_REVERSAL_DOWN):
        record("etf_high+reversal_down", "trim", Decimal("0.65"),
               f"ETF 创新高 + 5 日资金转流出 → trim")
        extra_reasons.append("ETF 创新高但 5 日资金已转流出，趋势可能末段" + _ff_suffix(f.ff_time_label))

    # ── 4-8. 通用价格风控（与股票持仓侧对齐） ─────────────────
    if f.current is not None and f.avg_cost > 0:
        cur_price = to_decimal(f.current, "current")
        t1 = p["hard_stop_etf_t1"]
        t2 = p["hard_stop_etf_t2"]
        t3 = p["hard_stop_etf_t3"]

        # ── 4. P0a 分级硬止损（与股票分支对齐，三档 + 大盘联动 + 分档幂等） ──
        # ETF 没有"风险公告 + 跌 8% = sell"那条 L4 规则的对应分支
        # （ETF 不会有 risk_hits），所以这里就是纯三档。
        if cur_price < f.avg_cost:
            loss_ratio = (f.avg_cost - cur_price) / f.avg_cost
            loss_pct = loss_ratio * Decimal("100")
            regime = f.market_regime
            prev_tier = f.last_trim_tier
            if loss_ratio >= t3:
                conf = Decimal("0.80") if regime == MARKET_REGIME_RISK_OFF else Decimal("0.85")
                record("etf_hard_stop_t3", "sell", conf,
                       f"ETF 浮亏 {loss_pct:.1f}% ≥ T3 阈值 {t3 * 100:.0f}% (硬底线) → sell")
                extra_reasons.append(
                    f"ETF 已跌破硬底线 T3（{t3 * 100:.0f}% 浮亏，当前 -{loss_pct:.1f}%），"
                    "建议强制全退"
                )
            elif loss_ratio >= t2 and current()[0] != "sell":
                if prev_tier == "T2":
                    extra_reasons.append(
                        f"ETF 仍处 T2 深防线区间（浮亏 -{loss_pct:.1f}%），"
                        f"且系统已在 {f.last_trim_price} 提示过 T2 减仓，幂等保护已生效"
                    )
                else:
                    if regime == MARKET_REGIME_RISK_OFF:
                        conf = Decimal("0.72")
                    elif regime == MARKET_REGIME_RISK_ON:
                        conf = Decimal("0.80")
                    else:
                        conf = Decimal("0.78")
                    raise_to("etf_hard_stop_t2", "trim", conf,
                             f"ETF 浮亏 {loss_pct:.1f}% ≥ T2 阈值 {t2 * 100:.0f}% (深防线) → 再减半仓")
                    extra_reasons.append(
                        f"ETF 已触发 T2 深防线（浮亏 {loss_pct:.1f}% ≥ {t2 * 100:.0f}%），"
                        "建议再减一半，最多保留约 25% 仓位观察"
                    )
            elif loss_ratio >= t1 and current()[0] != "sell":
                if prev_tier in ("T1", "T2"):
                    extra_reasons.append(
                        f"ETF 仍处 T1 首道防线区间（浮亏 -{loss_pct:.1f}%），"
                        f"且系统已在 {f.last_trim_price} 提示过 {prev_tier} 减仓，幂等保护已生效"
                    )
                else:
                    if regime == MARKET_REGIME_RISK_OFF:
                        conf = Decimal("0.65")
                    elif regime == MARKET_REGIME_RISK_ON:
                        conf = Decimal("0.72")
                    else:
                        conf = Decimal("0.70")
                    raise_to("etf_hard_stop_t1", "trim", conf,
                             f"ETF 浮亏 {loss_pct:.1f}% ≥ T1 阈值 {t1 * 100:.0f}% (首道防线) → 减半锁损")
                    extra_reasons.append(
                        f"ETF 已触发 T1 首道防线（浮亏 {loss_pct:.1f}% ≥ {t1 * 100:.0f}%），"
                        "建议减半仓锁定损失，给行业方向留出修复机会"
                    )

        # ── 5. P2a execution_plan 预案价位 ────────────────────
        if f.plan_stop_loss_price is not None and cur_price <= f.plan_stop_loss_price:
            record("etf_plan_stop_loss", "sell", Decimal("0.82"),
                   f"ETF 现价 {cur_price} ≤ 预案止损价 {f.plan_stop_loss_price} → "
                   "按计划 sell")
            extra_reasons.append(
                f"ETF 现价已触及预案止损价 {f.plan_stop_loss_price}，按既定纪律退出"
            )
        if (
            f.plan_take_profit_price is not None
            and cur_price >= f.plan_take_profit_price
            and current()[0] != "sell"
        ):
            raise_to("etf_plan_take_profit", "trim", Decimal("0.78"),
                     f"ETF 现价 {cur_price} ≥ 预案止盈价 {f.plan_take_profit_price} → "
                     "按计划 trim")
            extra_reasons.append(
                f"ETF 现价已达到预案止盈价 {f.plan_take_profit_price}，建议至少分批兑现"
            )

        # ── 6. 仓位超限 + 浮盈 → trim ─────────────────────────
        if (
            f.weight_pct > max_single_pct
            and cur_price > f.avg_cost
            and current()[0] != "sell"
        ):
            raise_to("etf_weight_over_cap", "trim", Decimal("0.70"),
                     f"ETF 持仓权重 {f.weight_pct}% > 单票上限 {max_single_pct}% 且浮盈 → trim")
            extra_reasons.append("ETF 仓位超过单票上限且已有浮盈，建议减仓")

        # ── 7. P0b 分级止盈（ETF 同股票） ─────────────────────
        if cur_price > f.avg_cost and current()[0] != "sell":
            gain_pct = (cur_price - f.avg_cost) / f.avg_cost
            top_signal = (
                f.regime in HIGH_REGIMES
                and f.ff_regime == FUND_PERSISTENT_OUTFLOW
            )
            ff_turning_down = (f.ff_reversal == FUND_REVERSAL_DOWN)
            top_or_turning = top_signal or ff_turning_down

            if gain_pct >= p["tp_t3"]:
                pct_disp = gain_pct * Decimal("100")
                if top_or_turning:
                    record("etf_take_profit_t3+top", "sell", Decimal("0.82"),
                           f"ETF 浮盈 ≥{p['tp_t3'] * 100:.0f}%（{pct_disp:.1f}%）+ 顶部信号 → sell")
                    extra_reasons.append(
                        f"ETF 浮盈已超过 {p['tp_t3'] * 100:.0f}% 且出现顶部 / 资金反转，"
                        "建议大幅止盈退出"
                    )
                else:
                    raise_to("etf_take_profit_t3", "trim", Decimal("0.78"),
                             f"ETF 浮盈 ≥{p['tp_t3'] * 100:.0f}%（{pct_disp:.1f}%）→ 大幅止盈")
                    extra_reasons.append(
                        f"ETF 浮盈已超过 {pct_disp:.1f}%，建议至少减半锁定利润"
                    )
            elif gain_pct >= p["tp_t2"]:
                pct_disp = gain_pct * Decimal("100")
                if top_or_turning:
                    record("etf_take_profit_t2+top", "sell", Decimal("0.80"),
                           f"ETF 浮盈 ≥{p['tp_t2'] * 100:.0f}%（{pct_disp:.1f}%）+ 顶部信号 → sell")
                    extra_reasons.append(
                        f"ETF 浮盈已达 {pct_disp:.1f}% + 顶部信号，建议升级为 sell"
                    )
                else:
                    raise_to("etf_take_profit_t2", "trim", Decimal("0.75"),
                             f"ETF 浮盈 ≥{p['tp_t2'] * 100:.0f}%（{pct_disp:.1f}%）→ 分批止盈")
                    extra_reasons.append(
                        f"ETF 浮盈已达 {pct_disp:.1f}%，建议分批止盈"
                    )
            elif gain_pct >= p["tp_t1"]:
                pct_disp = gain_pct * Decimal("100")
                if f.regime in HIGH_REGIMES and ff_turning_down:
                    raise_to("etf_take_profit_t1+reversal", "trim", Decimal("0.70"),
                             f"ETF 浮盈 ≥{p['tp_t1'] * 100:.0f}% + 创新高 + 资金反转 → 局部止盈")
                    extra_reasons.append(
                        f"ETF 浮盈已达 {pct_disp:.1f}%，叠加创新高 + 资金反转，建议先止盈一部分"
                    )
                else:
                    raise_to("etf_take_profit_t1", "trim", Decimal("0.65"),
                             f"ETF 浮盈 ≥{p['tp_t1'] * 100:.0f}%（{pct_disp:.1f}%）→ 温和止盈")
                    extra_reasons.append(
                        f"ETF 浮盈已达 {pct_disp:.1f}%，可考虑分批兑现部分利润"
                    )

        # ── 8. P2b trailing stop（ETF 同股票） ────────────────
        if (
            f.peak_price is not None
            and f.peak_price > 0
            and cur_price > f.avg_cost
            and current()[0] != "sell"
        ):
            drawdown = (f.peak_price - cur_price) / f.peak_price
            if drawdown >= p["trail_severe_pct"]:
                record("etf_trailing_stop_severe", "sell", Decimal("0.78"),
                       f"ETF 持仓期间最高 {f.peak_price} → 现价 {cur_price} 回撤 "
                       f"{drawdown * 100:.1f}% ≥ {p['trail_severe_pct'] * 100:.0f}% → sell")
                extra_reasons.append(
                    f"ETF 现价已从持仓期间最高 {f.peak_price} 回撤 {drawdown * 100:.1f}%，"
                    "趋势已走坏，建议退出剩余仓位"
                )
            elif drawdown >= p["trail_pct"]:
                raise_to("etf_trailing_stop", "trim", Decimal("0.72"),
                         f"ETF 持仓期间最高 {f.peak_price} → 现价 {cur_price} 回撤 "
                         f"{drawdown * 100:.1f}% ≥ {p['trail_pct'] * 100:.0f}% → trim")
                extra_reasons.append(
                    f"ETF 现价已从持仓期间最高 {f.peak_price} 回撤 {drawdown * 100:.1f}%，"
                    "建议先减仓锁定一部分利润"
                )

        # ── 残股识别（摊薄成本 >> 现价） ─────────────────────
        if f.avg_cost > cur_price * _ETF_RESIDUAL_THRESHOLD:
            offset_pct = float((f.avg_cost - cur_price) / cur_price * 100)
            extra_reasons.append(
                f"⚠️ 残股识别：ETF 摊薄成本 {f.avg_cost} vs 现价 {cur_price}（偏离 +{offset_pct:.0f}%），"
                "属于历史追涨杀跌后剩余仓位；trim/sell 信号对其操作意义有限，建议分批退出而非死守"
            )

    # ── 流动性预警 ───────────────────────────────────────
    if f.amount_yi is not None and f.amount_yi < _ETF_TURNOVER_WARN_YI:
        extra_reasons.append(
            f"⚠️ ETF 当日成交额 {f.amount_yi:.2f} 亿偏低，减仓时分批避免冲击成本"
        )

    # ── 跨境 / 商品 / 债券提示 ────────────────────────────
    if cross_border:
        extra_reasons.append(
            "跨境 QDII ETF：A 股主力资金面参考价值低，请结合外盘（如纳指 / 标普）判断"
        )
    elif commodity_or_bond:
        extra_reasons.append(
            f"{f.etf_category} 类 ETF：资金面 / 主题分析意义有限，"
            "决策应结合标的资产价格（黄金 / 国债收益率等）"
        )

    action, confidence = current()
    return action, confidence, extra_reasons, trace


# ── 决策分支：持仓 vs 自选 ───────────────────────────────────────

def _decide_for_holding(
    f: Features,
    max_single_pct: Decimal,
    params: dict | None = None,
) -> tuple[str, Decimal, list[str], list[dict]]:
    """qty > 0：从 hold 出发，按以下优先级处理：

    1. **风险/破位** → trim @ 0.72
    2. **资金面（PERSISTENT_OUTFLOW + 短期续出）** → sell / trim
    3. **L4 旧规则（跌 8% + 风险公告）** → sell @ 0.80（有公告佐证时跨档直接 sell）
    4. **P0a 分级硬止损**（T1/T2 trim、T3 sell；confidence 按大盘 regime 软联动）
    5. **P2a execution_plan 预案止损/止盈**（用户手填的硬阈值）→ sell @ 0.82 / trim @ 0.78
    6. **仓位超限 + 浮盈** → trim @ 0.70
    7. **P0b 分级止盈**（按 gain_pct 分三档；叠加顶部信号 / 资金反转升级 sell）
    8. **P2b trailing stop**（持仓期间最高价回撤）→ trim / sell
    9. **P1a 加仓**（前面都未触发、weight 有空间、未亏损、且自选 buy 条件成立）→ add
    10. **P1b cross_validation 软提示**（acceleration / reversal_confirmed）

    设计原则：
      - sell 一旦触发，后续止盈 / 加仓不会把它降级（用 ``current()[0] != "sell"`` 守卫）
      - trim 之间用 ``raise_to`` 升档，避免大数被小数覆盖
      - P0a / P2a 用 ``record`` 强制改 confidence，因为它们是预案 / 风控硬条件
    """
    p = params or _resolve_decision_params(None, None)
    extra_reasons: list[str] = []
    trace = _new_trace(Decimal("0.55"), "hold", "持仓默认起点 hold @ 0.55")
    record, raise_to, current = _make_recorder(trace)

    # ── 1. 风险/破位 → trim ─────────────────────────────────────
    if f.risk_hits >= 2 or f.regime in LOW_REGIMES:
        record("risk_or_low_regime", "trim", Decimal("0.72"),
               f"risk_hits={f.risk_hits} or regime={f.regime} → trim")

    # ── 2. 主力资金分层使用 ─────────────────────────────────────
    #   - 20d 定背景（偏强 / 偏弱）
    #   - 5d/3d 定动作（是否真的要减仓 / 回避）
    #   - 1d 只做提示，不单独触发动作
    if f.ff_regime == FUND_PERSISTENT_OUTFLOW:
        if f.regime in LOW_REGIMES and _ff_negative(f.ff_5d):
            raise_to("ff_outflow+low+5d_neg", "sell", Decimal("0.78"),
                     f"20d 资金弱 + 5d 续出({f.ff_5d:+.2f}yi) + 价格破位({f.regime}) → 升 sell")
            extra_reasons.append("20 日资金背景偏弱，且近 5 日继续流出并叠加价格破位，建议优先退出" + _ff_suffix(f.ff_time_label))
        elif current()[0] == "hold" and _ff_negative(f.ff_5d) and _ff_negative(f.ff_3d):
            raise_to("ff_outflow+5d+3d_neg", "trim", Decimal("0.65"),
                     f"20d 资金弱 + 5d({f.ff_5d:+.2f}) + 3d({f.ff_3d:+.2f}) 续出 → 升 trim")
            extra_reasons.append("20 日资金背景偏弱，且近 5/3 日继续流出，建议先减仓" + _ff_suffix(f.ff_time_label))

    # ── 3-8. 价格驱动的止损/止盈/加仓/trailing 都需要现价 + 成本 ─
    if f.current is not None and f.avg_cost > 0:
        cur_price = to_decimal(f.current, "current")
        if f.is_etf:
            t1 = p["hard_stop_etf_t1"]
            t2 = p["hard_stop_etf_t2"]
            t3 = p["hard_stop_etf_t3"]
        elif f.market == MARKET_HK:
            t1 = p["hard_stop_hk_t1"]
            t2 = p["hard_stop_hk_t2"]
            t3 = p["hard_stop_hk_t3"]
        else:
            t1 = p["hard_stop_a_t1"]
            t2 = p["hard_stop_a_t2"]
            t3 = p["hard_stop_a_t3"]

        # ── 3. L4 旧规则升档：跌 8% + 风险公告 → sell @ 0.80 ─────
        # 旧规则保留触发条件（跌 8% + 风险公告），但 confidence 从 0.78 升到 0.80：
        # 有公告佐证时这条 sell 应"跨过"P0a 的 T1/T2 trim 防线直接到位，
        # 等价于"普通 8% 跌 是 T1 trim；8% 跌 + 风险公告 = 立刻全退"。
        if f.exempt_hard_stop:
            extra_reasons.append(
                "⏸️ 该标的已豁免硬止损（P0a / L4），系统不自动发出止损/卖出信号；"
                "止损纪律由人工判断"
            )
        elif cur_price < f.avg_cost * Decimal("0.92") and f.risk_hits:
            record("price_loss+risk_announce", "sell", Decimal("0.80"),
                   f"现价 {cur_price} < 92% 成本 {f.avg_cost} 且有风险公告 → 跨档 sell")
            extra_reasons.append(
                "现价已明显低于持仓成本且伴随风险公告，跳过分档 trim 直接全退"
            )

        # ── 4. P0a 分级硬止损：T1/T2 trim、T3 sell ──────────────
        # 仅当当前价低于成本（即"浮亏"）时才考虑硬止损，避免数值边界 bug。
        # 三档互斥（elif），从最严重的 T3 开始判断，保证一档对应一条 trace step。
        # confidence 按大盘 regime 软联动：RISK_OFF 时下调（弱市留更多人工判断空间），
        # RISK_ON 时上调（强市同样跌幅更值得警惕，可能是个股专属风险）。
        #
        # P0a 分档幂等性（schema v5）：
        #   - last_trim_tier == "T1" 且仍在 T1 区间 → 只给软提示，不重发 trim
        #   - last_trim_tier == "T1" 且跌到 T2 → 正常触发并升档为 "T2"
        #   - last_trim_tier == "T2" 且仍在 T2 区间 → 软提示
        #   - T3 (sell) 永不静默：硬底线，宁可重复 sell 也不能漏
        #   - exempt_hard_stop：豁免该标的硬止损（历史遗留仓位等场景），只加提示不触发动作
        if cur_price < f.avg_cost and not f.exempt_hard_stop:
            loss_ratio = (f.avg_cost - cur_price) / f.avg_cost
            loss_pct = loss_ratio * Decimal("100")
            regime = f.market_regime
            prev_tier = f.last_trim_tier  # None / "T1" / "T2"
            if loss_ratio >= t3:
                # 已经深套到不再幻想的硬底线 → sell @ 0.85（RISK_OFF 时 0.80）
                conf = Decimal("0.80") if regime == MARKET_REGIME_RISK_OFF else Decimal("0.85")
                record("hard_stop_t3", "sell", conf,
                       f"浮亏 {loss_pct:.1f}% ≥ T3 阈值 {t3 * 100:.0f}% (硬底线) → sell")
                extra_reasons.append(
                    f"现价已跌破硬底线 T3（{t3 * 100:.0f}% 浮亏，当前 -{loss_pct:.1f}%），"
                    "建议强制全退；如打算重新建仓请先复盘原 thesis"
                )
            elif loss_ratio >= t2 and current()[0] != "sell":
                if prev_tier == "T2":
                    # 同档幂等：上次 T2 已建议过减仓，本次只软提示
                    extra_reasons.append(
                        f"现价仍处 T2 深防线区间（浮亏 -{loss_pct:.1f}%），"
                        f"且系统已在 {f.last_trim_price} 提示过 T2 减仓。"
                        "如已按计划减半，请等待价格突破回 T1 阈值以上再做下一步；"
                        "如尚未执行，请确认意图后再补仓位调整"
                    )
                else:
                    # 首次 T2（含从 T1 升档）
                    if regime == MARKET_REGIME_RISK_OFF:
                        conf = Decimal("0.72")
                    elif regime == MARKET_REGIME_RISK_ON:
                        conf = Decimal("0.80")
                    else:
                        conf = Decimal("0.78")
                    raise_to("hard_stop_t2", "trim", conf,
                             f"浮亏 {loss_pct:.1f}% ≥ T2 阈值 {t2 * 100:.0f}% (深防线) → 再减半仓")
                    extra_reasons.append(
                        f"现价已触发 T2 深防线（浮亏 {loss_pct:.1f}% ≥ {t2 * 100:.0f}%），"
                        f"建议再减一半，最多保留约 25% 仓位观察修复"
                    )
            elif loss_ratio >= t1 and current()[0] != "sell":
                if prev_tier in ("T1", "T2"):
                    # 同档幂等：上次 T1/T2 已建议过减仓，本次只软提示
                    extra_reasons.append(
                        f"现价仍处 T1 首道防线区间（浮亏 -{loss_pct:.1f}%），"
                        f"且系统已在 {f.last_trim_price} 提示过 {prev_tier} 减仓。"
                        "幂等保护已生效：不重复发出 trim，请关注是否跌至下一档"
                    )
                else:
                    # 首次 T1
                    if regime == MARKET_REGIME_RISK_OFF:
                        conf = Decimal("0.65")
                    elif regime == MARKET_REGIME_RISK_ON:
                        conf = Decimal("0.72")
                    else:
                        conf = Decimal("0.70")
                    raise_to("hard_stop_t1", "trim", conf,
                             f"浮亏 {loss_pct:.1f}% ≥ T1 阈值 {t1 * 100:.0f}% (首道防线) → 减半锁损")
                    extra_reasons.append(
                        f"现价已触发 T1 首道防线（浮亏 {loss_pct:.1f}% ≥ {t1 * 100:.0f}%），"
                        f"建议减半仓锁定损失，给标的留出修复机会"
                    )

        # ── 5. P2a execution_plan 预案价位 ────────────────────
        # 用户事前手填的 stop_loss / take_profit 拥有较高优先级：
        # 一旦触发，trace 会显式标注 "from_execution_plan"，避免和系统规则混淆。
        if f.plan_stop_loss_price is not None and cur_price <= f.plan_stop_loss_price:
            record("plan_stop_loss", "sell", Decimal("0.82"),
                   f"现价 {cur_price} ≤ 预案止损价 {f.plan_stop_loss_price} → "
                   f"按计划 sell（execution_plan 配置）")
            extra_reasons.append(
                f"现价已触及预案止损价 {f.plan_stop_loss_price}，按既定纪律退出，"
                "事后请走 exec review 复盘"
            )
        if (
            f.plan_take_profit_price is not None
            and cur_price >= f.plan_take_profit_price
            and current()[0] != "sell"
        ):
            raise_to("plan_take_profit", "trim", Decimal("0.78"),
                     f"现价 {cur_price} ≥ 预案止盈价 {f.plan_take_profit_price} → "
                     f"按计划 trim（execution_plan 配置）")
            extra_reasons.append(
                f"现价已达到预案止盈价 {f.plan_take_profit_price}，建议至少分批兑现一部分利润"
            )

        # ── 6. 仓位超限 + 浮盈 → trim（保留旧规则） ─────────────
        if (
            f.weight_pct > max_single_pct
            and cur_price > f.avg_cost
            and current()[0] != "sell"
        ):
            raise_to("weight_over_cap", "trim", Decimal("0.70"),
                     f"持仓权重 {f.weight_pct}% > 单票上限 {max_single_pct}% 且浮盈 → trim")
            extra_reasons.append("当前仓位超过单票上限且已有浮盈")

        # ── 7. P0b 分级止盈 ────────────────────────────────────
        # gain_pct 用 (cur - avg) / avg；只在浮盈状态下评估，亏损交给止损路径处理
        if cur_price > f.avg_cost and current()[0] != "sell":
            gain_pct = (cur_price - f.avg_cost) / f.avg_cost
            # 顶部 / 资金反转叠加信号：让止盈从 trim 升级为 sell
            top_signal = (
                f.regime in HIGH_REGIMES
                and f.ff_regime == FUND_PERSISTENT_OUTFLOW
            )
            ff_turning_down = (f.ff_reversal == FUND_REVERSAL_DOWN)
            top_or_turning = top_signal or ff_turning_down

            if gain_pct >= p["tp_t3"]:
                pct_disp = gain_pct * Decimal("100")
                if top_or_turning:
                    record("take_profit_t3+top", "sell", Decimal("0.82"),
                           f"浮盈 ≥{p['tp_t3'] * 100:.0f}%（实际 {pct_disp:.1f}%）+ "
                           f"顶部/资金反转信号 → 升级 sell")
                    extra_reasons.append(
                        f"浮盈已超过 {p['tp_t3'] * 100:.0f}% 且出现顶部 / 资金反转信号，"
                        "建议大幅止盈（如 2/3 以上）退出"
                    )
                else:
                    raise_to("take_profit_t3", "trim", Decimal("0.78"),
                             f"浮盈 ≥{p['tp_t3'] * 100:.0f}%（实际 {pct_disp:.1f}%）→ "
                             "分批止盈锁定利润")
                    extra_reasons.append(
                        f"浮盈已超过 {p['tp_t3'] * 100:.0f}%（实际 {pct_disp:.1f}%），"
                        "建议至少减半锁定利润，剩余仓位用 trailing stop 跟踪"
                    )
            elif gain_pct >= p["tp_t2"]:
                pct_disp = gain_pct * Decimal("100")
                if top_or_turning:
                    record("take_profit_t2+top", "sell", Decimal("0.80"),
                           f"浮盈 ≥{p['tp_t2'] * 100:.0f}%（实际 {pct_disp:.1f}%）+ "
                           "顶部/资金反转信号 → 升级 sell")
                    extra_reasons.append(
                        f"浮盈已达 {pct_disp:.1f}% 且出现顶部 / 资金反转信号，"
                        "建议升级为 sell 锁定大部分利润"
                    )
                else:
                    raise_to("take_profit_t2", "trim", Decimal("0.75"),
                             f"浮盈 ≥{p['tp_t2'] * 100:.0f}%（实际 {pct_disp:.1f}%）→ "
                             "分批止盈")
                    extra_reasons.append(
                        f"浮盈已达 {pct_disp:.1f}%，建议分批止盈（如 1/3），剩余继续持有"
                    )
            elif gain_pct >= p["tp_t1"]:
                pct_disp = gain_pct * Decimal("100")
                if f.regime in HIGH_REGIMES and ff_turning_down:
                    raise_to("take_profit_t1+reversal", "trim", Decimal("0.70"),
                             f"浮盈 ≥{p['tp_t1'] * 100:.0f}%（实际 {pct_disp:.1f}%）+ "
                             "创新高 + 资金反转 → 局部止盈")
                    extra_reasons.append(
                        f"浮盈已达 {pct_disp:.1f}%，价格创新高叠加资金反转，"
                        "建议先止盈一部分（如 1/4），剩余跟趋势"
                    )
                else:
                    raise_to("take_profit_t1", "trim", Decimal("0.65"),
                             f"浮盈 ≥{p['tp_t1'] * 100:.0f}%（实际 {pct_disp:.1f}%）→ "
                             "温和止盈提示")
                    extra_reasons.append(
                        f"浮盈已达 {pct_disp:.1f}%，可考虑分批兑现部分利润，"
                        "但趋势良好时不必急于全退"
                    )

        # ── 8. P2b trailing stop（持仓期间最高价回撤） ──────────
        # 仅在浮盈状态下生效（亏损交给硬止损），用持仓期间最高价作锚
        if (
            f.peak_price is not None
            and f.peak_price > 0
            and cur_price > f.avg_cost
            and current()[0] != "sell"
        ):
            drawdown = (f.peak_price - cur_price) / f.peak_price
            if drawdown >= p["trail_severe_pct"]:
                record("trailing_stop_severe", "sell", Decimal("0.78"),
                       f"持仓期间最高 {f.peak_price} → 现价 {cur_price} "
                       f"回撤 {drawdown * 100:.1f}% ≥ {p['trail_severe_pct'] * 100:.0f}% → sell")
                extra_reasons.append(
                    f"现价已从持仓期间最高价 {f.peak_price} 回撤 {drawdown * 100:.1f}%，"
                    "趋势已实质走坏，建议止盈退出剩余仓位"
                )
            elif drawdown >= p["trail_pct"]:
                raise_to("trailing_stop", "trim", Decimal("0.72"),
                         f"持仓期间最高 {f.peak_price} → 现价 {cur_price} "
                         f"回撤 {drawdown * 100:.1f}% ≥ {p['trail_pct'] * 100:.0f}% → trim")
                extra_reasons.append(
                    f"现价已从持仓期间最高价 {f.peak_price} 回撤 {drawdown * 100:.1f}%，"
                    "建议先减仓锁定一部分利润，避免回吐过多"
                )

        # ── 9. P1a 加仓（仅在没有任何减仓/止损/止盈信号时考虑） ──
        # 触发条件：
        #   - 当前 action 仍为 hold（说明 1-8 都未触发）
        #   - 权重 < 单票上限 × headroom_ratio（默认 85%）
        #   - 现价 ≥ 摊薄成本（不在亏损中加仓）
        #   - 自选侧 trend / reversal buy 条件依然成立
        if (
            current()[0] == "hold"
            and not f.is_etf  # ETF 加仓走独立分支
            and f.weight_pct < max_single_pct * p["add_headroom"]
            and cur_price >= f.avg_cost
        ):
            add_eval = _evaluate_holding_add(f, market_regime=f.market_regime)
            if add_eval is not None:
                add_action, add_conf, add_reason = add_eval
                record("holding_add_path", add_action, add_conf, add_reason[:120])
                extra_reasons.append(add_reason)

    # ── 10. P1b cross_validation 软提示（仅在 hold 时补提示） ───
    if current()[0] == "hold" and isinstance(f.ff_cross, dict):
        accel = f.ff_cross.get("acceleration")
        if accel == "decelerating_inflow":
            extra_reasons.append(
                "⚠️ cross_validation.acceleration=decelerating_inflow，"
                "主力虽仍流入但日均在变小，趋势动能在弱化，注意防守，不建议主动加仓"
                + _ff_suffix(f.ff_time_label)
            )
        elif accel == "accelerating_outflow":
            extra_reasons.append(
                "⚠️ cross_validation.acceleration=accelerating_outflow，"
                "主力流出在加速，若未来 1-2 个交易日仍未修复，考虑被动减仓"
                + _ff_suffix(f.ff_time_label)
            )
        if (
            f.ff_reversal == FUND_REVERSAL_UP
            and f.ff_cross.get("reversal_confirmed") is False
        ):
            extra_reasons.append(
                "⚠️ cross_validation.reversal_confirmed=False，"
                "短期资金尚未为反转背书，不可作为加仓依据"
                + _ff_suffix(f.ff_time_label)
            )

    action, confidence = current()
    return action, confidence, extra_reasons, trace


def _evaluate_holding_add(
    f: Features,
    market_regime: str | None,
) -> tuple[str, Decimal, str] | None:
    """持仓侧加仓评估：复用自选侧 trend/reversal 信号，但置信度低于自选 buy（已建仓加仓更激进，
    对纪律要求更高）。

    返回 ``(action, confidence, reason)`` 或 None。
    None 表示加仓条件不成立，调用方保留 hold。

    与 ``_evaluate_self_select_buy`` 的差异：
      - 自选 buy: trend=0.72, reversal=0.68；本函数 trend=0.68, reversal=0.64
      - RISK_OFF：本函数直接返回 None（持仓侧不在弱市里主动加仓），让 caller 保留 hold
      - 大盘 RISK_OFF 时不再走 probe 通道（probe 是首仓概念，不适合加仓）
    """
    buy_ok, buy_path = _is_strict_buy_candidate(
        f.regime, f.risk_hits, f.positive_hits, f.change_pct,
        f.ff_regime, f.ff_3d, f.ff_5d, f.ff_reversal, f.ff_cross,
    )
    if not buy_ok:
        return None

    if market_regime == MARKET_REGIME_RISK_OFF:
        return None

    if buy_path == "trend":
        return (
            "add",
            Decimal("0.68"),
            "【持仓加仓-趋势】持仓未亏损 + 趋势仍成立（创新高 + 资金不撤离 + 正向公告）"
            "+ 仓位距上限仍有空间 → 可分批加仓，但不要一次顶满"
            + _ff_suffix(f.ff_time_label),
        )
    if buy_path == "reversal":
        return (
            "add",
            Decimal("0.64"),
            "【持仓加仓-反转】持仓未亏损 + 反转买入条件依然成立（资金已反向流入 + 多重正向公告）"
            "+ 仓位距上限仍有空间 → 可分批加仓，反转加仓更激进，建议比趋势加仓再保守 1/2"
            + _ff_suffix(f.ff_time_label),
        )
    return None


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
        f.market, f.regime, f.risk_hits, f.positive_hits, f.change_pct,
        f.ff_regime, f.ff_3d, f.ff_5d, f.ff_reversal, f.ff_cross, f.market_regime, f.ff_time_label,
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
    """组装审计用 sources：最近 3 条公告 PDF + price_history.regime + fund_flow + market_regime。

    ETF 的 sources 略有不同：不挂公告（ETF 没有上市公司公告），但会附 ETF 元数据
    （category / 当日成交额 / 规模），方便人 / Agent 反查决策依据。
    """
    sources: list[str] = []
    if not f.is_etf:
        # 股票：公告 + 价格 + 资金 + 大盘
        announcements = analysis.get("announcements") or []
        for item in announcements[:3]:
            title = item.get("title") or "公告"
            pdf = item.get("pdf_url") or "-"
            sources.append(f"{item.get('date')}: {title} ({pdf})")
        if not sources:
            sources.append("analyze_company.quote")
    else:
        # ETF：元数据 + 价格 + 资金 + 大盘
        sources.append(f"etf.category={f.etf_category or '-'}")
        if f.amount_yi is not None:
            sources.append(f"etf.turnover={f.amount_yi:.2f}yi（当日成交额）")
        if f.market_cap_yi is not None:
            sources.append(f"etf.size={f.market_cap_yi:.1f}yi（规模）")

    sources.append(f"price_history.regime={f.regime or '-'}")
    if f.ff_available:
        if f.ff_time_label:
            sources.append(f"fund_flow.time={f.ff_time_label}")
        def _fmt(v: float | None) -> str:
            return "-" if v is None else f"{v:+.2f}yi"
        sources.append(
            f"fund_flow.regime={f.ff_regime} "
            f"(1d={_fmt(f.ff_1d)}, 3d={_fmt(f.ff_3d)}, 5d={_fmt(f.ff_5d)}, "
            f"10d={_fmt(f.ff_10d)}, 20d={_fmt(f.ff_20d)})"
            + _ff_suffix(f.ff_time_label)
        )
        if isinstance(f.ff_cross, dict) and f.ff_cross.get("verdict"):
            verdict = f.ff_cross.get("verdict")
            confirmed = f.ff_cross.get("reversal_confirmed")
            conflict = f.ff_cross.get("short_long_conflict")
            sources.append(
                f"fund_flow.cross_validation={verdict} "
                f"(reversal_confirmed={confirmed}, short_long_conflict={conflict})"
            )
    if f.market_regime is not None:
        sources.append(f"market_regime={f.market_regime}")
    return sources


# ── 主入口（调度器）──────────────────────────────────────────────

def _maintain_trim_tier_state(
    conn,
    account_id: int,
    market: str,
    code: str,
    *,
    decision: dict,
    snapshot: dict,
    decision_params: dict,
    prev_tier: str | None,
) -> None:
    """根据本次决策结果维护 ``position_peak.last_trim_tier`` 状态。

    规则：
      - 本次 trace 出现 ``hard_stop_t1`` step → 写入 last_trim_tier="T1"
      - 本次 trace 出现 ``hard_stop_t2`` step → 写入 last_trim_tier="T2"（升档）
      - T3 (sell) 触发但 sell 已让"清仓-删除 peak"承担收尾，这里不写
      - 本次价格让浮亏回升到 T1 以下（< t1）且 prev_tier 不为 None → 清空 tier

    设计取舍：
      - 信任"系统建议过 = 用户即将执行"。如果用户没真减仓，下次 analyze 仍会触发
        软提示（reason 里），但不会重复发 trim action
      - 价格回升至 T1 以下时无论 prev_tier 是 T1 还是 T2 都清空，让"修复后再跌"
        重新走完整三档流程
    """
    try:
        from spc_core.ledger import (
            set_position_trim_tier,
            clear_position_trim_tier,
        )
    except Exception:  # noqa: BLE001
        return

    trace = decision.get("confidence_trace") or []
    steps = [s.get("step") for s in trace]

    # 解析当前现价 + 成本，算 loss_ratio 用于"回升重置"
    try:
        avg_cost = to_decimal(snapshot.get("avg_cost_price") or "0", "avg_cost_price")
        last_price = to_decimal(snapshot.get("last_price") or "0", "last_price")
    except Exception:  # noqa: BLE001
        return
    if avg_cost <= 0 or last_price <= 0:
        return
    loss_ratio = (avg_cost - last_price) / avg_cost  # 正值=浮亏；负值=浮盈

    # 写入：T2 触发 → "T2"；T1 触发（无 T2 同时）→ "T1"
    # 注意 hard_stop_t2 和 etf_hard_stop_t2 都算 T2
    has_t2 = ("hard_stop_t2" in steps) or ("etf_hard_stop_t2" in steps)
    has_t1 = ("hard_stop_t1" in steps) or ("etf_hard_stop_t1" in steps)
    if has_t2:
        try:
            set_position_trim_tier(
                conn, account_id, market, code, tier="T2", price=last_price,
            )
        except Exception:  # noqa: BLE001
            pass
        return
    if has_t1:
        try:
            set_position_trim_tier(
                conn, account_id, market, code, tier="T1", price=last_price,
            )
        except Exception:  # noqa: BLE001
            pass
        return

    # 回升重置：当前浮亏已低于 T1 阈值 → 清空（无论 prev_tier 是 T1 还是 T2）
    if prev_tier is not None:
        # 选择对应市场的 T1 阈值
        target_market = snapshot.get("market") or market
        target_code = snapshot.get("code") or code
        is_etf = is_etf_fn(target_market, target_code)
        if is_etf:
            t1 = decision_params["hard_stop_etf_t1"]
        elif target_market == MARKET_HK:
            t1 = decision_params["hard_stop_hk_t1"]
        else:
            t1 = decision_params["hard_stop_a_t1"]
        if loss_ratio < t1:
            try:
                clear_position_trim_tier(conn, account_id, market, code)
            except Exception:  # noqa: BLE001
                pass


def _decision_from_analysis(
    target_market: str,
    target_code: str,
    snapshot: dict | None,
    analysis: dict,
    capital_total: Decimal,
    max_single_pct: Decimal,
    market_regime: str | None = None,
    *,
    peak_price: Decimal | None = None,
    plan_stop_loss_price: Decimal | None = None,
    plan_take_profit_price: Decimal | None = None,
    last_trim_tier: str | None = None,
    last_trim_price: Decimal | None = None,
    last_trim_time: str | None = None,
    decision_params: dict | None = None,
    exempt_hard_stop: bool = False,
) -> dict:
    """市场风险偏好软联动（``market_regime``）规则：
    - RISK_OFF：
      - A 股自选侧 buy 候选自动降为 focus（在 ``_evaluate_self_select_buy`` 内处理）
      - 港股自选侧若属于反转修复型 buy，可降档为 probe（试探买入，小仓位首仓）
      持仓侧 hold 仅加风险提示，不强制降仓；已经在 trim / sell 的 confidence + 0.05。
    - RISK_ON：在 reasons 头部加一句宏观正向提示，但不会主动加仓 / 升档。
    - NEUTRAL / 缺数据：完全不影响 action。

    ``peak_price`` / ``plan_stop_loss_price`` / ``plan_take_profit_price`` 来自 caller
    （``analyze_now``）从 ``position_peak`` 表和 ``execution_plan`` 查询得到，用于
    P2a / P2b 的持仓侧风控规则。
    """
    analysis = {**analysis, "market": target_market, "code": target_code}
    f = _extract_features(
        snapshot, analysis, capital_total, market_regime,
        peak_price=peak_price,
        plan_stop_loss_price=plan_stop_loss_price,
        plan_take_profit_price=plan_take_profit_price,
        last_trim_tier=last_trim_tier,
        last_trim_price=last_trim_price,
        last_trim_time=last_trim_time,
        exempt_hard_stop=exempt_hard_stop,
    )

    # 1. 各维度信号收集（只贡献 reasons / risks 文案，不触发 action）
    reasons: list[str] = []
    risks: list[str] = []
    for collector in _SIGNAL_COLLECTORS:
        r, k = collector(f)
        reasons.extend(r)
        risks.extend(k)

    # 2. 决策分支：持仓侧 vs 自选侧（ETF 走专用决策树，不依赖公告维度）
    if f.qty > 0:
        if f.is_etf:
            action, confidence, more_reasons, trace = _decide_etf_for_holding(
                f, max_single_pct, params=decision_params,
            )
        else:
            action, confidence, more_reasons, trace = _decide_for_holding(
                f, max_single_pct, params=decision_params,
            )
        reasons.extend(more_reasons)
        if action == "hold" and not reasons:
            reasons.append("当前没有触发明显的减仓或卖出信号")
    else:
        if f.is_etf:
            action, confidence, more_reasons, more_risks, trace = _decide_etf_for_watching(f)
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
    ff_cross: dict | None = None,
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
    - **多周期交叉验证（cross_validation）**：
      - 当 ff_reversal == OUTFLOW_TO_INFLOW 时，``cross.reversal_confirmed``
        必须为 True（即 1d/5d 同向流入背书），否则视为"掉头未确认"，本路径不触发
      - cross 字段缺失（老缓存）时退化为旧行为，仅靠 ff_3d/ff_5d 判断
      - 注：``cross.short_long_conflict`` 在 PERSISTENT_INFLOW 分支下逻辑上不可达
        （本函数硬要求 ff_5d > 0，与 conflict 要求 5d 与 20d 反向矛盾），故不在
        本层做该字段的硬门控；它仍会出现在 cross.verdict 与渲染层供审计

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
    # 多周期交叉验证（cross 缺失时跳过该层硬约束，保持向后兼容）
    if ff_cross:
        # 走"reversal 字段触发"路径时，必须 1d/5d 同向背书
        if ff_reversal == FUND_REVERSAL_UP and ff_cross.get("reversal_confirmed") is False:
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
    ff_cross: dict | None = None,
) -> tuple[bool, str | None]:
    """聚合调度：返回 (是否 buy 候选, 触发路径名)。

    路径名：``"trend"`` / ``"reversal"`` / ``None``。caller 据此区分 reasons 文案，
    让人 / LLM 一眼知道是"突破跟随买入"还是"左侧反转买入"——两者风险性质不同。
    """
    if _is_trend_buy_candidate(regime, risk_hits, positive_hits, change_pct, ff_regime, ff_3d, ff_5d):
        return True, "trend"
    if _is_reversal_buy_candidate(
        regime, risk_hits, positive_hits, change_pct, ff_regime, ff_3d, ff_5d, ff_reversal,
        ff_cross,
    ):
        return True, "reversal"
    return False, None


def _evaluate_self_select_buy(
    market: str,
    regime: str | None,
    risk_hits: int,
    positive_hits: int,
    change_pct: object,
    ff_regime: str | None,
    ff_3d: float | None,
    ff_5d: float | None,
    ff_reversal: str | None,
    ff_cross: dict | None,
    market_regime: str | None,
    ff_time_label: str | None,
) -> tuple[str, Decimal, str] | None:
    """自选侧 buy 决策评估，返回 (action, confidence, reason_msg) 或 None。

    None 表示不构成 buy 候选，调用方应继续往下走 focus / watch 分支。
    返回非 None 时：
      - 大盘 RISK_OFF：
        - A 股 / 非港股：action 自动降为 ``focus``，置信度更低
        - 港股：趋势追高仍降为 ``focus``；反转修复路径允许降档为 ``probe``（试探买入）
      - 否则：action 为 ``buy``，trend 路径置信度更高（0.72），reversal 较低（0.68）
      - 趋势路径下 ``cross.short_long_conflict`` 触发时：confidence -0.05，
        reasons 标注"多周期未共振"；但仍走 buy（不一票否决，避免好趋势被噪音误杀）
    """
    buy_ok, buy_path = _is_strict_buy_candidate(
        regime, risk_hits, positive_hits, change_pct, ff_regime, ff_3d, ff_5d, ff_reversal,
        ff_cross,
    )
    if not buy_ok:
        return None
    # 趋势路径的"动能减弱"软扣分（不一票否决）：
    #   trend_buy 已经硬卡 ff_3d/ff_5d ≥ 0，所以这里不会用 short_long_conflict
    #   （那个要求 5d 与 20d 反向，trend 路径下永远不成立）。
    #   改用 ``acceleration == decelerating_inflow``：同向流入但日均在变小，
    #   说明买盘动能在弱化，confidence -0.05；reversal 路径不享受此扣分（因为
    #   reversal 自身已经被 reversal_confirmed/short_long_conflict 严格门控）。
    trend_decelerating = (
        buy_path == "trend"
        and isinstance(ff_cross, dict)
        and ff_cross.get("acceleration") == "decelerating_inflow"
    )
    if buy_path == "trend":
        trend_msg = (
            "【趋势跟随路径】强趋势（创新高）+ 正向信号 + 主力资金短中期不撤离 + 不过热涨幅同时满足"
        )
        if trend_decelerating:
            trend_msg += (
                "；⚠️ 但 cross_validation.acceleration=decelerating_inflow，"
                "同向流入但日均在变小，动能在弱化 → confidence -0.05"
            )
        if market_regime == MARKET_REGIME_RISK_OFF:
            return (
                "focus",
                Decimal("0.65"),
                trend_msg + _ff_suffix(ff_time_label) + "；但大盘 RISK_OFF，趋势追高先降级为 focus，等大盘修复后再考虑放大仓位",
            )
        base = Decimal("0.72")
        if trend_decelerating:
            base = Decimal("0.67")
        return "buy", base, trend_msg + _ff_suffix(ff_time_label)
    if buy_path == "reversal":
        reversal_msg = (
            "【反转买入路径】非破位区间 + 资金已反向流入（近 3/5 日累计转正）+ 多重正向公告 + 不过热涨幅；"
            "属于左侧 / 反转型 buy，置信度低于趋势型"
        )
        if isinstance(ff_cross, dict) and ff_cross.get("reversal_confirmed") is True:
            reversal_msg += "；cross_validation.reversal_confirmed=True，1d/5d 同向背书"
        if market_regime == MARKET_REGIME_RISK_OFF:
            if market == MARKET_HK:
                return (
                    "probe",
                    Decimal("0.60"),
                    reversal_msg + _ff_suffix(ff_time_label) + "；港股大盘仍 RISK_OFF，但允许用常规仓位 1/4-1/3 试探首仓，确认修复后再加第二笔",
                )
            return (
                "focus",
                Decimal("0.62"),
                reversal_msg + _ff_suffix(ff_time_label) + "；大盘 RISK_OFF，先降级为 focus 等大盘修复",
            )
        return "buy", Decimal("0.68"), reversal_msg + _ff_suffix(ff_time_label)
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


def analyze_now(
    conn,
    account_id: int,
    account_slug: str,
    account_display_name: str,
    scope: str,
    market: str | None = None,
    code: str | None = None,
    analysis_provider=None,
    *,
    llm_review_enabled: bool = False,
    llm_review_backend: str | None = None,
    llm_review_timeout: int = 180,
    llm_review_progress=None,
) -> dict:
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
    # P0/P1/P2 策略参数：读 account_settings，缺配置回落到代码默认值
    decision_params = _resolve_decision_params(conn, account_id)

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

        # P2a: 查 execution_plan 上的预案价位（仅持仓侧用）
        plan_stop_loss = None
        plan_take_profit = None
        # P2b: 查持仓期间最高价
        peak_price = None
        # P0a 分档幂等性：读上次已触发的 trim tier 标记
        last_trim_tier = None
        last_trim_price = None
        last_trim_time = None
        peak_row = None
        if snapshot is not None:
            try:
                from spc_core.ledger import get_active_plan_levels
                plan_levels = get_active_plan_levels(conn, account_id, tgt_market, tgt_code)
                if plan_levels:
                    plan_stop_loss = plan_levels.get("stop_loss_price")
                    plan_take_profit = plan_levels.get("take_profit_price")
            except Exception:  # noqa: BLE001
                # 老库 / 还没实现该 helper 时，退化为不带 plan 价位
                pass
            try:
                from spc_core.ledger import get_position_peak
                peak_row = get_position_peak(conn, account_id, tgt_market, tgt_code)
                if peak_row:
                    peak_price = to_decimal(peak_row["peak_price"], "peak_price")
                    last_trim_tier = peak_row.get("last_trim_tier")
                    if peak_row.get("last_trim_price"):
                        try:
                            last_trim_price = to_decimal(
                                peak_row["last_trim_price"], "last_trim_price"
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    last_trim_time = peak_row.get("last_trim_time")
            except Exception:  # noqa: BLE001
                pass

        # 豁免检查：decision.exempt.hard_stop.{market}.{code} = "1" 时跳过硬止损
        exempt_hard_stop = False
        try:
            row = conn.execute(
                "SELECT value FROM account_settings "
                "WHERE account_id = ? AND key = ?",
                (account_id, f"decision.exempt.hard_stop.{tgt_market}.{tgt_code}"),
            ).fetchone()
            if row and row[0] == "1":
                exempt_hard_stop = True
        except Exception:  # noqa: BLE001
            pass

        decision = _decision_from_analysis(
            tgt_market,
            tgt_code,
            snapshot,
            analysis,
            capital_total,
            max_single_pct,
            market_regime=market_regime_label.get(tgt_market),
            peak_price=peak_price,
            plan_stop_loss_price=plan_stop_loss,
            plan_take_profit_price=plan_take_profit,
            last_trim_tier=last_trim_tier,
            last_trim_price=last_trim_price,
            last_trim_time=last_trim_time,
            decision_params=decision_params,
            exempt_hard_stop=exempt_hard_stop,
        )

        # ── P0a 分档幂等性：根据本次 trace 维护 last_trim_tier 状态 ──
        # 触发或升档时：写入新 tier；浮亏回升至 T1 以下时：清空
        if snapshot is not None and peak_row is not None:
            _maintain_trim_tier_state(
                conn, account_id, tgt_market, tgt_code,
                decision=decision, snapshot=snapshot,
                decision_params=decision_params,
                prev_tier=last_trim_tier,
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

    # ── LLM 复核（L1 旁路）：对 add/trim/sell/buy/probe 类敏感 action 做二次确认 ──
    # 默认后端为 "prompt"——由当前会话 agent 直接复核，不新开 subprocess。
    # fail-open：任何失败只在结果里打 warning，不影响主决策。
    if llm_review_enabled:
        try:
            from spc_core.llm_review import attach_review_to_results
            # 不在这里强制默认 backend——交给 attach_review_to_results 内部走
            # detect_llm_backend()，这样 SPC_LLM_BACKEND=none 才能真正禁用复核。
            attach_review_to_results(
                payload,
                analysis_cache=analysis_cache,
                backend=llm_review_backend,
                enabled=True,
                timeout=llm_review_timeout,
                progress=llm_review_progress,
            )
        except Exception as e:  # noqa: BLE001
            payload["llm_review_meta"] = {
                "enabled": True,
                "backend": None,
                "reviewed": 0,
                "prompted": 0,
                "failed": 0,
                "skipped": 0,
                "unavailable": False,
                "error": f"LLM review module failed: {type(e).__name__}: {e!s:.200s}",
            }
    else:
        payload["llm_review_meta"] = {
            "enabled": False,
            "backend": None,
            "reviewed": 0,
            "prompted": 0,
            "failed": 0,
            "skipped": 0,
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
        lines.append("软联动：A 股 RISK_OFF 时，自选侧本应 buy 的标的会降为 focus；港股 RISK_OFF 下仅反转修复型标的可降档为试探买入（小仓位首仓）；持仓侧 hold 仅加风险提示，不强制减仓。")
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
        # LLM 复核（如有）
        review = decision.get("llm_review") or {}
        if review:
            status = review.get("status")
            if status == "ok":
                verdict = review.get("verdict", "?")
                conf = review.get("confidence")
                verdict_emoji = {
                    "confirm": "✅",
                    "question": "⚠️",
                    "reject": "🚫",
                }.get(verdict, "•")
                lines.append(
                    f"LLM 复核（{review.get('backend','?')}，{review.get('elapsed_ms', 0)/1000:.1f}s）："
                    f"{verdict_emoji} {verdict}"
                    + (f" @ {conf}" if conf is not None else "")
                )
                for c in (review.get("concerns") or []):
                    lines.append(f"  • {c}")
                if review.get("missing_context"):
                    lines.append("  需要补充的数据：")
                    for m in review["missing_context"]:
                        lines.append(f"  - {m}")
                if review.get("execution_hint"):
                    lines.append(f"  执行建议：{review['execution_hint']}")
            elif status == "prompted":
                lines.append(
                    "🤖 **LLM 复核请求**：请当前 agent 基于以上系统建议与数据来源，"
                    "结合 stock-market-hub 拉取最新公告/资金流/新闻，"
                    "给出独立复核结论（verdict / confidence / concerns / execution_hint）。"
                )
            elif status == "failed":
                lines.append(
                    f"⚠️ LLM 复核失败（{review.get('backend','?')}）：{review.get('error', '?')[:120]}；"
                    "请按规则系统信号处理"
                )
            elif status == "unavailable":
                lines.append(
                    f"⚠️ LLM 复核未启用：{review.get('message', '未检测到 LLM 后端')}"
                )
            # status == "skipped" 不渲染（非敏感 action，复核被跳过是正常的）
        lines.append("")
    opportunities = payload.get("opportunities") or []
    if opportunities:
        lines.append("可额外关注的标的：")
        for item in opportunities:
            lines.append(f"- {item['market'].upper()} {item['code']} {item['name']}：{'；'.join(item['reasons'])}")

    # ── LLM 复核建议清单（仅在复核未启用时显示）─────────────────
    # 设计动机：复核单标的耗时 90-200s，全开太慢；默认关闭后让用户自己挑想细看的标的。
    # 这里在分析末尾给出"哪些标的建议复核 + 可直接复制的命令"。
    meta = payload.get("llm_review_meta") or {}
    if not meta.get("enabled"):
        from spc_core.llm_review import should_review
        review_candidates: list[dict] = []
        for item in payload.get("results", []):
            dec = item.get("decision") or {}
            action = dec.get("action")
            if not should_review(action):
                continue
            review_candidates.append({
                "market": item.get("market"),
                "code": item.get("code"),
                "name": item.get("name") or "",
                "action": action,
                "action_label": dec.get("action_label") or action,
                "confidence": dec.get("confidence"),
            })
        if review_candidates:
            account_slug = (payload.get("account") or {}).get("slug") or "default"
            lines.append("")
            lines.append("== LLM 复核建议（默认未开启）==")
            lines.append(
                f"以下 {len(review_candidates)} 个标的的建议涉及仓位变化，"
                f"建议在执行前对**信心不足 / 跟你判断不一致**的标的做一次人工复核："
            )
            for c in review_candidates:
                lines.append(
                    f"- {c['market'].upper()} {c['code']} {c['name']}：{c['action_label']}（{c['action']}）"
                    + (f" @ {c['confidence']}" if c['confidence'] is not None else "")
                )
            lines.append("")
            lines.append("直接告诉 agent 你要复核哪个标的即可，例如：")
            lines.append(f"  \"帮我对 {review_candidates[0]['name']}（{review_candidates[0]['code']}）做一下人工复核\"")
            lines.append("（agent 会调 stock-market-hub 拉最新数据，在当前 session 内给出复核结论）")
    return "\n".join(lines).strip()

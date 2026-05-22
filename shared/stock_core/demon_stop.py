"""妖股止盈规则：独立于买卖判断的仓位提醒指标。

规则（从股票自身底部算，不是从买入成本算）：
  1. 找近期 swing 底部作为"启动点"
  2. 从底部起算累计涨幅，+30% 时减仓 1/3
  3. 之后每涨 +15%，减仓 10%
  4. 遇到大跌日（单日跌幅 < -5%），减半

这是纯仓位管理提醒，不触发买卖决策，不修改 action。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DemonThreshold:
    trigger_pct: float        # 触发涨幅（从底部算，例如 30.0）
    reduce_pct: float         # 减仓比例（例如 33.3 表示减 1/3）
    label: str                # 描述
    triggered: bool = False


@dataclass
class DemonStopResult:
    symbol: str
    name: str
    bottom_price: float         # 股票自身近期底部
    bottom_date: str            # 底部的日期
    current_price: float
    cumulative_gain_pct: float  # 从底部以来的累计涨幅 %
    day_change_pct: float | None  # 今日涨跌幅 %
    position_qty: float
    position_value: float
    account_slug: str

    stage: str  # "normal" / "trigger_30" / "trigger_45" / ... / "crash_half"
    triggered_thresholds: list[DemonThreshold] = field(default_factory=list)

    suggested_action: str = ""
    suggested_reduce_qty: float = 0.0


def find_swing_bottom(kline: list[dict], lookback: int = 120) -> tuple[float, str]:
    """从 K 线中找近期 swing 底部（启动点）。

    算法：在 lookback 根 K 线范围找最低收盘价。
    同时向前后各检查 10 根 K 线确认是否为局部低谷。

    Returns (bottom_price, bottom_date).
    """
    if not kline:
        return 0.0, ""

    recent = kline[-lookback:] if len(kline) > lookback else kline
    if len(recent) < 20:
        return 0.0, ""

    closes = [k["close"] for k in recent]
    dates = [k["date"] for k in recent]

    # 找局部最低点：最低收盘价出现的位置
    min_idx = closes.index(min(closes))

    # 确认是局部低谷（前后至少 5 天价格更高）
    left = closes[max(0, min_idx - 5):min_idx]
    right = closes[min_idx + 1:min(min_idx + 6, len(closes))]

    if left and right:
        avg_left = sum(left) / len(left)
        avg_right = sum(right) / len(right)
        if closes[min_idx] < avg_left and closes[min_idx] < avg_right:
            return closes[min_idx], dates[min_idx]

    # 不满足局部低谷条件，直接返回最低点
    return closes[min_idx], dates[min_idx]


_THRESHOLDS = [
    DemonThreshold(30.0, 33.33, "底部起涨30%：减仓1/3"),
    DemonThreshold(45.0, 10.0, "再涨15%(累计45%)：减仓10%"),
    DemonThreshold(60.0, 10.0, "再涨15%(累计60%)：减仓10%"),
    DemonThreshold(75.0, 10.0, "再涨15%(累计75%)：减仓10%"),
    DemonThreshold(90.0, 10.0, "再涨15%(累计90%)：减仓10%"),
    DemonThreshold(105.0, 10.0, "再涨15%(累计105%)：减仓10%"),
    DemonThreshold(120.0, 10.0, "再涨15%(累计120%)：减仓10%"),
    DemonThreshold(135.0, 10.0, "再涨15%(累计135%)：减仓10%"),
    DemonThreshold(150.0, 10.0, "再涨15%(累计150%)：减仓10%"),
]


def compute_demon_stop(
    bottom_price: float,
    bottom_date: str,
    current_price: float,
    current_qty: float,
    day_change_pct: float | None,
) -> DemonStopResult:
    """计算妖股止盈提醒（从底部算）。

    Parameters
    ----------
    bottom_price: 近期 swing 底部价格
    bottom_date: 底部的日期
    current_price: 当前价格
    current_qty: 当前持仓数量
    day_change_pct: 今日涨跌幅（如 -5.3 表示跌 5.3%）
    """
    cumulative = (current_price - bottom_price) / bottom_price * 100 if bottom_price > 0 else 0.0

    triggered = []
    stage = "normal"
    suggested_action = ""
    suggested_reduce_qty = 0.0

    # 大跌日规则（最高优先级）
    if day_change_pct is not None and day_change_pct <= -5.0:
        suggested_action = "大跌日减半"
        suggested_reduce_qty = current_qty * 0.5
        stage = "crash_half"

    # 阶梯止盈
    for t in _THRESHOLDS:
        if cumulative >= t.trigger_pct:
            t.triggered = True
            triggered.append(t)
            stage = f"trigger_{int(t.trigger_pct)}"

    if triggered and not suggested_action:
        latest = triggered[-1]
        suggested_action = latest.label
        suggested_reduce_qty = current_qty * latest.reduce_pct / 100.0

    return DemonStopResult(
        symbol="",
        name="",
        bottom_price=bottom_price,
        bottom_date=bottom_date,
        current_price=current_price,
        cumulative_gain_pct=cumulative,
        day_change_pct=day_change_pct,
        position_qty=current_qty,
        position_value=current_qty * current_price,
        account_slug="",
        stage=stage,
        triggered_thresholds=triggered,
        suggested_action=suggested_action,
        suggested_reduce_qty=suggested_reduce_qty,
    )


def render_demon_stop_text(results: list[DemonStopResult]) -> str:
    """将一批妖股止盈结果渲染为文本报告。"""
    if not results:
        return "无持仓数据，无法生成妖股止盈提醒。"

    lines = [
        "=" * 72,
        "  妖股止盈提醒（从股票自身底部算，非买入成本）",
        "=" * 72,
        "",
    ]

    sorted_results = sorted(results, key=lambda r: r.cumulative_gain_pct, reverse=True)

    has_alert = False
    for r in sorted_results:
        name_part = f"{r.name}({r.symbol[2:]})" if r.name else r.symbol
        if r.suggested_action:
            has_alert = True
            lines.append(f"[!] [{r.account_slug}] {name_part}")
            lines.append(f"   底部={r.bottom_price:.2f} ({r.bottom_date})  "
                          f"现价={r.current_price:.2f}  "
                          f"从底部涨幅={r.cumulative_gain_pct:+.1f}%  "
                          f"今日={r.day_change_pct:+.1f}%")
            lines.append(f"   持仓={r.position_qty:,.0f}股  市值={r.position_value:,.0f}")
            lines.append(f"   !! {r.suggested_action}（建议减{r.suggested_reduce_qty:,.0f}股）")
            lines.append("")

    if not has_alert:
        lines.append("[OK] 当前所有持仓均未触发妖股止盈阈值。")
        lines.append("")
        lines.append("从底部涨幅监控（最接近阈值的）：")
        shown = 0
        for r in sorted_results:
            if shown >= 8:
                break
            if r.cumulative_gain_pct > 5:
                name_part = f"{r.name}({r.symbol[2:]})" if r.name else r.symbol
                next_threshold = 30.0
                remaining = next_threshold - r.cumulative_gain_pct
                lines.append(f"  [{r.account_slug}] {name_part}  "
                              f"底部={r.bottom_price:.2f} ({r.bottom_date})  "
                              f"从底部+{r.cumulative_gain_pct:+.1f}%  "
                              f"距30%阈值还需+{remaining:.1f}%")
                shown += 1
        if shown == 0:
            lines.append("  （无涨幅>5%的持仓）")

    lines.append("")
    lines.append("规则说明（从股票底部算）：")
    lines.append("  第1步：从底部起涨30% → 减仓1/3")
    lines.append("  第2步：之后每涨15% → 减仓10%")
    lines.append("  第3步：遇到大跌日(<-5%) → 减半")
    lines.append("  本模块仅提醒，不自动执行交易。")
    return "\n".join(lines)

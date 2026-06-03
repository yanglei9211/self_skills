"""
前向追踪器：从 T+1 开始逐日追踪实际走势，计算前向收益、最大回撤、止损/止盈触发。

关键逻辑：
  - 从 T+1 开始逐日追踪
  - 计算 5/10/20/60 日前向收益
  - 每日检查止损/止盈/trailing stop
  - 数据不足时 fwd_returns 对应持有期为 None
  - max_drawdown 从 T 日收盘价起算
"""

from __future__ import annotations


def track_forward(
    klines: list[dict],
    entry_idx: int,
    entry_price: float,
    action: str = "buy",
    confidence: float = 1.0,
    entry_date: str = "",
    hold_days: list[int] | None = None,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    trailing_stop_pct: float | None = None,
) -> "TradeResult":
    """追踪单次入场后的前向表现。

    Args:
        klines: 完整日线数据（包含 T 之后的数据用于前向追踪）。
                每条 dict 需包含 close, high, low, date 字段。
        entry_idx: T 日在 klines 中的索引（基于 0）。
        entry_price: 入场价格（T 日收盘价）。
        action: 动作类型（buy / focus / watch 等）。
        confidence: 信号置信度（0~1）。
        entry_date: T 日日期字符串。
        hold_days: 要计算的前向持有期天数列表，默认 [5, 10, 20, 60]。
        stop_loss_pct: 止损百分比（如 8 表示 -8%），None 表示不启用。
        take_profit_pct: 止盈百分比（如 20 表示 +20%），None 表示不启用。
        trailing_stop_pct: 移动止损回撤百分比（从持仓峰值的最大回撤），None 表示不启用。

    Returns:
        TradeResult 对象。
    """
    from .models import TradeResult

    if hold_days is None:
        hold_days = [5, 10, 20, 60]

    fwd_returns = {}
    max_drawdown = 0.0
    hit_stop_loss = False
    hit_take_profit = False
    data_incomplete = False

    # T 日收盘价作为参考基准
    t_close = entry_price
    position_peak = t_close  # trailing stop 用的持仓峰值
    peak_drawdown_val = 0.0  # 最大回撤（从 T 日收盘起，正数表示回撤幅度）

    # 从 T+1 开始逐日追踪
    tracking_active = True
    max_day = max(hold_days) if hold_days else 60

    for i in range(entry_idx + 1, len(klines)):
        day = klines[i]
        offset = i - entry_idx  # 距 T 日的天数

        if offset > max_day:
            break

        close = day.get("close", 0)
        if close <= 0:
            continue

        # 更新回撤（从 T 日收盘起算）
        day_return = (close - t_close) / t_close
        current_drawdown = -min(0, day_return)  # 正值表示回撤幅度
        if current_drawdown > peak_drawdown_val:
            peak_drawdown_val = current_drawdown

        if tracking_active:
            # 更新持仓峰值（用于 trailing stop）
            if close > position_peak:
                position_peak = close

            # 检查止损
            if stop_loss_pct is not None:
                loss_pct = (close - entry_price) / entry_price * 100
                if loss_pct <= -stop_loss_pct:
                    hit_stop_loss = True
                    tracking_active = False

            # 检查止盈
            if take_profit_pct is not None and not hit_stop_loss:
                profit_pct = (close - entry_price) / entry_price * 100
                if profit_pct >= take_profit_pct:
                    hit_take_profit = True
                    tracking_active = False

            # 检查 trailing stop（从峰值回撤）
            if trailing_stop_pct is not None and not hit_stop_loss and not hit_take_profit:
                drawdown_from_peak = (position_peak - close) / position_peak * 100
                if drawdown_from_peak >= trailing_stop_pct:
                    hit_stop_loss = True  # trailing stop 触发出场
                    tracking_active = False

        # 记录持有期收益
        if offset in hold_days:
            fwd_returns[offset] = round(day_return * 100, 2)

    # 检查是否有持有期数据不足
    for hd in hold_days:
        if hd not in fwd_returns:
            fwd_returns[hd] = None
            data_incomplete = True

    return TradeResult(
        entry_date=entry_date,
        entry_price=entry_price,
        action=action,
        confidence=confidence,
        fwd_returns=fwd_returns,
        max_drawdown=round(-peak_drawdown_val * 100, 2),  # 转为负数百分比
        hit_stop_loss=hit_stop_loss,
        hit_take_profit=hit_take_profit,
        data_incomplete=data_incomplete,
    )


def batch_track(
    klines: list[dict],
    entries: list[dict],
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    trailing_stop_pct: float | None = None,
) -> list["TradeResult"]:
    """批量追踪多个入场点。

    Args:
        klines: 完整日线数据。
        entries: 入场点列表，每条 dict 需包含：
            - entry_idx: T 日在 klines 中的索引
            - entry_price: 入场价格
            - entry_date: T 日日期
            - action: 动作类型
            - confidence: 置信度
            - hold_days: 持有期列表（可选）

    Returns:
        list[TradeResult]
    """
    results = []
    for entry in entries:
        result = track_forward(
            klines=klines,
            entry_idx=entry["entry_idx"],
            entry_price=entry["entry_price"],
            action=entry.get("action", "buy"),
            confidence=entry.get("confidence", 1.0),
            entry_date=entry.get("entry_date", ""),
            hold_days=entry.get("hold_days", [5, 10, 20, 60]),
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_stop_pct=trailing_stop_pct,
        )
        results.append(result)
    return results

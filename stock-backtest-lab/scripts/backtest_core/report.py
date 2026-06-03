"""
报告输出：Markdown / JSON 格式的回测报告。

报告中必须标注：
  - adjustment=qfq
  - 信号数 N
  - N < 20 时不输出"最优"
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def render_report(
    stats: dict,
    symbol: str,
    strategy: str,
    from_date: str,
    to_date: str,
    adjustment: str = "qfq",
) -> str:
    """生成 Markdown 格式的回测报告。

    Args:
        stats: aggregate_results 返回的统计 dict
        symbol: 股票代码
        strategy: 策略名称
        from_date: 回测起始日期
        to_date: 回测结束日期
        adjustment: K 线复权方式

    Returns:
        Markdown 字符串
    """
    lines = []
    total = stats.get("total_signals", 0)

    # 标题
    lines.append(f"# {symbol} — {strategy} 策略回测报告")
    lines.append("")

    # 元信息
    lines.append(f"- 回测区间：{from_date} ~ {to_date}")
    lines.append(f"- K 线口径：{adjustment}（前复权）")
    lines.append(f"- 信号总数：**{total}** 次")
    lines.append(f"- 生成时间：{datetime.now().isoformat()}")
    lines.append("")

    # 样本量警告
    warning = stats.get("sample_warning")
    if warning:
        lines.append(f"> **注意**：{warning}")
        lines.append("")

    # 总体统计
    lines.append("## 总体统计")
    lines.append("")

    win_rate = stats.get("win_rate_20d_pct")
    if win_rate is not None:
        lines.append(f"- 20 日胜率：**{win_rate}%**")

    avg_returns = stats.get("avg_returns", {})
    if avg_returns:
        for period in [5, 10, 20, 60]:
            val = avg_returns.get(period)
            if val is not None:
                lines.append(f"- {period} 日平均收益：**{val:+.2f}%**")

    avg_max_dd = stats.get("avg_max_drawdown")
    max_dd = stats.get("max_drawdown")
    if avg_max_dd is not None:
        lines.append(f"- 平均最大回撤：**{avg_max_dd:.2f}%**")
    if max_dd is not None:
        lines.append(f"- 最差单笔回撤：**{max_dd:.2f}%**")

    sharpe = stats.get("sharpe_20d")
    if sharpe is not None:
        lines.append(f"- 20 日 Sharpe 比率：**{sharpe}**")

    stop_loss = stats.get("stop_loss_hits", 0)
    take_profit = stats.get("take_profit_hits", 0)
    if stop_loss > 0 or take_profit > 0:
        lines.append(f"- 止损触发：{stop_loss} / 止盈触发：{take_profit}")
    lines.append("")

    # 按年份分组
    by_year = stats.get("by_year", {})
    if by_year:
        lines.append("## 按年份分组")
        lines.append("")
        lines.append("| 年份 | 信号数 | 20d 胜率 | 5d 收益 | 10d 收益 | 20d 收益 | 60d 收益 | 备注 |")
        lines.append("|------|--------|----------|---------|----------|----------|----------|------|")
        for year in sorted(by_year.keys()):
            ys = by_year[year]
            n = ys.get("n", 0)
            wr = ys.get("win_rate_20d_pct")
            wr_str = f"{wr}%" if wr is not None else "-"
            ar = ys.get("avg_returns", {})
            d5 = f"{ar.get(5, 0):+.2f}%" if ar.get(5) is not None else "-"
            d10 = f"{ar.get(10, 0):+.2f}%" if ar.get(10) is not None else "-"
            d20 = f"{ar.get(20, 0):+.2f}%" if ar.get(20) is not None else "-"
            d60 = f"{ar.get(60, 0):+.2f}%" if ar.get(60) is not None else "-"
            note = ys.get("sample_warning", "")
            lines.append(f"| {year} | {n} | {wr_str} | {d5} | {d10} | {d20} | {d60} | {note} |")
        lines.append("")

    # 按大盘 regime 分组
    by_regime = stats.get("by_regime", {})
    if by_regime:
        lines.append("## 按大盘 regime 分组")
        lines.append("")
        lines.append("| Regime | 信号数 | 20d 胜率 | 5d 收益 | 10d 收益 | 20d 收益 | 60d 收益 |")
        lines.append("|--------|--------|----------|---------|----------|----------|----------|")
        for regime in ["RISK_ON", "NEUTRAL", "RISK_OFF"]:
            if regime in by_regime:
                rs = by_regime[regime]
                n = rs.get("n", 0)
                wr = rs.get("win_rate_20d_pct")
                wr_str = f"{wr}%" if wr is not None else "-"
                ar = rs.get("avg_returns", {})
                d5 = f"{ar.get(5, 0):+.2f}%" if ar.get(5) is not None else "-"
                d10 = f"{ar.get(10, 0):+.2f}%" if ar.get(10) is not None else "-"
                d20 = f"{ar.get(20, 0):+.2f}%" if ar.get(20) is not None else "-"
                d60 = f"{ar.get(60, 0):+.2f}%" if ar.get(60) is not None else "-"
                lines.append(f"| {regime} | {n} | {wr_str} | {d5} | {d10} | {d20} | {d60} |")
        lines.append("")

    return "\n".join(lines)


def render_json(stats: dict) -> str:
    """生成 JSON 格式的统计报告。

    使用 ensure_ascii=False 保持中文可读。
    处理 float 和 None。
    """
    return json.dumps(stats, ensure_ascii=False, indent=2, default=str)


def render_data_coverage(
    coverage: dict,
) -> str:
    """生成数据覆盖情况说明。

    Args:
        coverage: check_coverage 返回的 dict
    """
    if not coverage.get("covered"):
        return (
            f"数据不足：{coverage.get('symbol', '?')} 在 "
            f"{coverage.get('from_date', '?')} ~ {coverage.get('to_date', '?')} 区间内 "
            f"仅有 {coverage.get('total_rows', 0)} 条数据，"
            f"最早日期 {coverage.get('first_date', '?')}，"
            f"最晚日期 {coverage.get('last_date', '?')}。"
            f"请运行 sync 命令同步数据。"
        )
    return (
        f"数据覆盖 OK：{coverage.get('symbol', '?')} "
        f"{coverage.get('from_date', '?')} ~ {coverage.get('to_date', '?')}，"
        f"共 {coverage.get('total_rows', 0)} 条。"
    )

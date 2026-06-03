"""
统计聚合：从 TradeResult 列表计算胜率、平均收益、Sharpe、最大回撤等。

关键规则：
  - N < 20 时输出"样本不足，供观察"而非"最优"
  - 按年份分组、按 market_regime_a 分组
"""

from __future__ import annotations

import math
from typing import Any


def aggregate_results(
    results: list["TradeResult"],
    klines_df: list[dict] | None = None,
) -> dict:
    """聚合多个 TradeResult，输出统计摘要。

    Args:
        results: TradeResult 列表
        klines_df: 完整日线数据，用于构建时间索引（可选，用于按年份分组）

    Returns:
        统计 dict，包含：
          - total_signals: 总信号数
          - win_rate: 总体胜率
          - avg_returns: 各持有期的平均收益
          - max_drawdown: 最大回撤
          - sharpe: Sharpe 比率
          - by_year: 按年份分组统计
          - by_regime: 按市场 regime 分组统计
          - sample_warning: 样本量不足的提示
    """
    n = len(results)
    sample_warning = None
    if n < 20:
        sample_warning = f"样本不足（N={n}），仅供观察，不给出最优结论"

    # 总体胜率（以 20 日收益 > 0 为基准）
    win_count = 0
    valid_count = 0
    for r in results:
        ret_20 = r.fwd_returns.get(20)
        if ret_20 is not None:
            valid_count += 1
            if ret_20 > 0:
                win_count += 1
    win_rate = round(win_count / valid_count * 100, 1) if valid_count > 0 else None

    # 各持有期平均收益
    hold_periods = [5, 10, 20, 60]
    avg_returns = {}
    for hd in hold_periods:
        vals = [r.fwd_returns.get(hd) for r in results if r.fwd_returns.get(hd) is not None]
        if vals:
            avg_returns[hd] = round(sum(vals) / len(vals), 2)
        else:
            avg_returns[hd] = None

    # 最大回撤
    drawdowns = [r.max_drawdown for r in results if r.max_drawdown is not None]
    max_dd = min(drawdowns) if drawdowns else None  # 最差的一个

    # 平均最大回撤
    avg_dd = round(sum(drawdowns) / len(drawdowns), 2) if drawdowns else None

    # Sharpe（以 20 日收益计算，简化版无风险利率 = 0）
    sharpe = _calculate_sharpe(results, period=20)

    # 止损/止盈触发统计
    stop_loss_count = sum(1 for r in results if r.hit_stop_loss)
    take_profit_count = sum(1 for r in results if r.hit_take_profit)

    # 年份分组（通过 entry_date 推断）
    by_year = _group_by_year(results)

    # 按 regime 分组（需要通过 signals 获取，这里先返回占位）
    # 注：regime 分组需要在策略回测循环中注入，这里先留接口
    by_regime = {}

    stats = {
        "total_signals": n,
        "win_rate_20d_pct": win_rate,
        "avg_returns": avg_returns,
        "max_drawdown": max_dd,
        "avg_max_drawdown": avg_dd,
        "sharpe_20d": sharpe,
        "stop_loss_hits": stop_loss_count,
        "take_profit_hits": take_profit_count,
        "by_year": by_year,
        "by_regime": by_regime,
    }

    if sample_warning:
        stats["sample_warning"] = sample_warning

    return stats


def aggregate_with_regime(
    results: list["TradeResult"],
    regime_labels: list[str],
    klines_df: list[dict] | None = None,
) -> dict:
    """与 aggregate_results 相同，但增加按 market_regime 分组统计。

    Args:
        results: TradeResult 列表
        regime_labels: 与 results 一一对应的 regime 标签列表
        klines_df: 完整日线数据
    """
    base_stats = aggregate_results(results, klines_df)

    # 按 regime 分组
    by_regime = {}
    for r, label in zip(results, regime_labels):
        if label not in by_regime:
            by_regime[label] = {"results": [], "n": 0}
        by_regime[label]["results"].append(r)
        by_regime[label]["n"] += 1

    regime_stats = {}
    for label, group in by_regime.items():
        sub = aggregate_results(group["results"])
        regime_stats[label] = {
            "n": group["n"],
            "win_rate_20d_pct": sub["win_rate_20d_pct"],
            "avg_returns": sub["avg_returns"],
        }

    base_stats["by_regime"] = regime_stats
    return base_stats


def _calculate_sharpe(results: list["TradeResult"], period: int = 20) -> float | None:
    """计算简化版 Sharpe 比率（无风险利率 = 0）。

    使用指定持有期的前向收益率序列。
    """
    returns = [r.fwd_returns.get(period) for r in results if r.fwd_returns.get(period) is not None]
    if len(returns) < 2:
        return None

    # 转换为小数
    returns_decimal = [ret / 100.0 for ret in returns]

    mean_ret = sum(returns_decimal) / len(returns_decimal)
    if mean_ret == 0:
        return 0.0

    variance = sum((r - mean_ret) ** 2 for r in returns_decimal) / (len(returns_decimal) - 1)
    std = math.sqrt(variance) if variance > 0 else 0

    if std == 0:
        return None

    # 年化（假设 period 天等同于 period/252 年）
    annual_factor = math.sqrt(252 / period)
    sharpe = (mean_ret / std) * annual_factor

    return round(sharpe, 2)


def _group_by_year(results: list["TradeResult"]) -> dict:
    """按年份分组统计。

    从 entry_date 中提取年份。
    """
    by_year = {}
    for r in results:
        if not r.entry_date:
            continue
        year = r.entry_date[:4]
        if year not in by_year:
            by_year[year] = []
        by_year[year].append(r)

    year_stats = {}
    for year, group in sorted(by_year.items()):
        n = len(group)
        win_count = sum(1 for r in group if r.fwd_returns.get(20, 0) is not None and r.fwd_returns.get(20, 0) > 0)
        valid = sum(1 for r in group if r.fwd_returns.get(20) is not None)
        win_rate = round(win_count / valid * 100, 1) if valid > 0 else None

        avg_returns = {}
        for hd in [5, 10, 20, 60]:
            vals = [r.fwd_returns.get(hd) for r in group if r.fwd_returns.get(hd) is not None]
            avg_returns[hd] = round(sum(vals) / len(vals), 2) if vals else None

        year_stats[year] = {
            "n": n,
            "win_rate_20d_pct": win_rate,
            "avg_returns": avg_returns,
            "sample_warning": f"样本不足（N={n}）" if n < 20 else None,
        }

    return year_stats


def _group_by_regime(
    results: list["TradeResult"],
    regime_map: dict[str, str],
) -> dict:
    """按市场 regime 分组统计。

    Args:
        results: TradeResult 列表
        regime_map: entry_date -> regime 的映射
    """
    by_regime = {}
    for r in results:
        regime = regime_map.get(r.entry_date, "UNKNOWN")
        if regime not in by_regime:
            by_regime[regime] = []
        by_regime[regime].append(r)

    regime_stats = {}
    for regime, group in by_regime.items():
        sub = aggregate_results(group)
        regime_stats[regime] = {
            "n": sub["total_signals"],
            "win_rate_20d_pct": sub["win_rate_20d_pct"],
            "avg_returns": sub["avg_returns"],
        }

    return regime_stats

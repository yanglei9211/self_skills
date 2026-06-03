"""
CLI 入口：stockbt sync / sync-index / run 命令。

P0 暴露：
  - stockbt sync SYMBOL --from 2020-01-01
  - stockbt sync-index --from 2020-01-01
  - stockbt run SYMBOL --strategy boll_squeeze_entry --from 2020-01-01 --to 2026-05-31
"""

from __future__ import annotations

import argparse
import sys
import os

from .store import (
    get_connection,
    init_schema,
    check_coverage,
    read_daily_bars,
)
from .sync import sync_daily_kline, sync_all_indices
from .snapshot import build_snapshot
from .signals import calculate_signals
from .strategies import STRATEGIES, boll_squeeze_entry, ma_breakout, price_new_high
from .tracker import track_forward
from .stats import aggregate_with_regime
from .report import render_report, render_json, render_data_coverage


def _parse_hold_days(value: str) -> list[int]:
    """解析持有期参数，如 '5,10,20,60' -> [5, 10, 20, 60]。"""
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def cmd_sync(args) -> None:
    """执行 sync 命令。"""
    conn = get_connection()
    init_schema(conn)
    try:
        result = sync_daily_kline(
            conn,
            symbol=args.symbol,
            market=args.market,
            from_date=args.from_date,
            count=args.count,
        )
        _print_sync_result(result)
    finally:
        conn.close()


def cmd_sync_index(args) -> None:
    """执行 sync-index 命令。"""
    conn = get_connection()
    init_schema(conn)
    try:
        results = sync_all_indices(
            conn,
            from_date=args.from_date,
            count=args.count,
        )
        for r in results:
            _print_sync_result(r)
    finally:
        conn.close()


def cmd_run(args) -> None:
    """执行 run 命令：完整回测流程。"""
    conn = get_connection()
    init_schema(conn)

    try:
        # 1. 检查数据覆盖
        coverage = check_coverage(conn, args.symbol, args.from_date, args.to_date)
        if not coverage.get("covered"):
            print(render_data_coverage(coverage), file=sys.stderr)
            print("提示：请先运行 stockbt sync 同步数据", file=sys.stderr)
            sys.exit(1)

        # 2. 获取策略
        strategy_name = args.strategy
        strategy_fn = STRATEGIES.get(strategy_name)
        if strategy_fn is None:
            print(f"未知策略: {strategy_name}", file=sys.stderr)
            print(f"可用策略: {', '.join(STRATEGIES.keys())}", file=sys.stderr)
            sys.exit(1)

        # 3. 读取完整日线数据（用于前向追踪）
        all_klines = read_daily_bars(conn, args.symbol)
        if not all_klines:
            print("无日线数据，请先运行 stockbt sync", file=sys.stderr)
            sys.exit(1)

        hold_days = _parse_hold_days(args.hold_days)

        # 4. 遍历每个交易日，构造 snapshot 并计算信号
        results = []
        regime_labels = []

        for i in range(60, len(all_klines)):  # 从第 60 天开始（保证有足够历史数据）
            k = all_klines[i]
            t_date = k["trade_date"]

            # 检查日期范围
            if t_date < args.from_date or t_date > args.to_date:
                continue

            # 构造 T 日快照
            snapshot = build_snapshot(conn, args.symbol, t_date, args.market)
            if snapshot is None:
                continue

            # 计算信号
            signals = calculate_signals(snapshot)

            # 应用策略
            decisions = strategy_fn(snapshot, signals)

            # 对每个决策进行前向追踪
            # 提前构建 tracker 所需格式（避免内循环重复创建）
            kline_for_tracker = [
                {"close": r["close"], "high": r["high"], "low": r["low"], "date": r["trade_date"]}
                for r in all_klines
            ]
            for dec in decisions:
                trade = track_forward(
                    kline_for_tracker,
                    entry_idx=i,
                    entry_price=dec.entry_price,
                    action=dec.action,
                    confidence=dec.confidence,
                    entry_date=t_date,
                    hold_days=hold_days,
                    stop_loss_pct=args.stop_loss,
                    take_profit_pct=args.take_profit,
                    trailing_stop_pct=args.trailing_stop,
                )
                results.append(trade)
                regime_labels.append(signals.market_regime_a or "NEUTRAL")

        # 5. 聚合并输出报告
        if not results:
            print(f"在 {args.from_date} ~ {args.to_date} 区间内无信号产生。")
            return

        stats = aggregate_with_regime(results, regime_labels)

        if args.format == "json":
            print(render_json(stats))
        else:
            report = render_report(
                stats,
                symbol=args.symbol,
                strategy=args.strategy,
                from_date=args.from_date,
                to_date=args.to_date,
            )
            print(report)

    finally:
        conn.close()


def _print_sync_result(result: dict) -> None:
    """打印同步结果。"""
    status = result.get("status", "unknown")
    if status == "ok":
        symbol_key = result.get("symbol") or result.get("index_code", "?")
        synced = result.get("synced", 0)
        first = result.get("first_date", "?")
        last = result.get("last_date", "?")
        total = result.get("total", 0)
        print(f"OK  {symbol_key}: 同步 {synced} 条 ({first} ~ {last})，总计 {total} 条")
    else:
        symbol_key = result.get("symbol") or result.get("index_code", "?")
        error = result.get("error", "未知错误")
        print(f"FAIL  {symbol_key}: {error}", file=sys.stderr)


def main() -> None:
    """CLI 主入口。"""
    parser = argparse.ArgumentParser(
        description="stockbt — Point-in-Time 回测系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  stockbt sync SZ000333 --from 2020-01-01
  stockbt sync-index --from 2020-01-01
  stockbt run SZ000333 --strategy boll_squeeze_entry --from 2020-01-01 --to 2026-05-31
  stockbt run SZ000333 --strategy ma_breakout --from 2020-01-01 --format json
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # sync 命令
    sync_parser = subparsers.add_parser("sync", help="同步个股日K线到本地数据库")
    sync_parser.add_argument("symbol", help="股票代码（如 SZ000333, HK01810）")
    sync_parser.add_argument("--market", default="a", choices=["a", "hk", "us"], help="市场（默认 a）")
    sync_parser.add_argument("--from", dest="from_date", default="2020-01-01", help="起始日期（默认 2020-01-01）")
    sync_parser.add_argument("--count", type=int, default=2000, help="拉取 K 线数量（默认 2000）")
    sync_parser.set_defaults(func=cmd_sync)

    # sync-index 命令
    sync_index_parser = subparsers.add_parser("sync-index", help="同步指数日K线到本地数据库")
    sync_index_parser.add_argument("--from", dest="from_date", default="2020-01-01", help="起始日期（默认 2020-01-01）")
    sync_index_parser.add_argument("--count", type=int, default=2000, help="拉取 K 线数量（默认 2000）")
    sync_index_parser.set_defaults(func=cmd_sync_index)

    # run 命令
    run_parser = subparsers.add_parser("run", help="运行回测")
    run_parser.add_argument("symbol", help="股票代码（如 SZ000333）")
    run_parser.add_argument("--strategy", default="boll_squeeze_entry", help="策略名称（默认 boll_squeeze_entry）")
    run_parser.add_argument("--from", dest="from_date", default="2020-01-01", help="回测起始日期（默认 2020-01-01）")
    run_parser.add_argument("--to", dest="to_date", default="2026-05-31", help="回测结束日期（默认 2026-05-31）")
    run_parser.add_argument("--market", default="a", help="市场（默认 a）")
    run_parser.add_argument("--hold-days", default="5,10,20,60", help="持有期（默认 5,10,20,60）")
    run_parser.add_argument("--stop-loss", type=float, default=None, help="止损百分比（如 8）")
    run_parser.add_argument("--take-profit", type=float, default=None, help="止盈百分比（如 20）")
    run_parser.add_argument("--trailing-stop", type=float, default=None, help="移动止损百分比（如 15）")
    run_parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式（默认 text）")
    run_parser.set_defaults(func=cmd_run)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()

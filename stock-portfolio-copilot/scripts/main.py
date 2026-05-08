#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from spc_core.db import connect
from spc_core.decision import analyze_now, render_analysis_text
from spc_core.ledger import (
    add_position_seed,
    add_trade,
    add_watch,
    delete_trade,
    delete_watch,
    latest_analysis_run,
    latest_snapshots,
    list_position_seed,
    list_trades,
    list_watch,
)
from spc_core.portfolio import pnl_summary, sync_portfolio
from spc_core.settings import capital_settings, set_capital, show_settings_json
from spc_core.utils import pretty_json, render_table, to_local_display


def cmd_position_init(args, conn) -> None:
    add_position_seed(conn, args.market, args.code, args.qty, args.cost, args.currency, args.time, args.note)
    print(f"已初始化持仓：{args.market} {args.code}")


def cmd_position_list(args, conn) -> None:
    rows = list_position_seed(conn, args.market)
    if not rows:
        print("暂无初始持仓")
        return
    print(
        render_table(
            ["市场", "代码", "数量", "成本价", "币种", "时间", "备注"],
            [
                [
                    row["market"],
                    row["code"],
                    row["qty"],
                    row["cost_price"],
                    row["currency"],
                    to_local_display(row["seed_time"]),
                    row["note"],
                ]
                for row in rows
            ],
        )
    )


def cmd_trade_add(args, conn) -> None:
    trade_id = add_trade(
        conn,
        args.market,
        args.code,
        args.side,
        args.qty,
        args.price,
        args.time,
        args.currency,
        args.fx_rate,
        args.fee_commission,
        args.fee_platform,
        args.fee_transfer,
        args.tax_stamp,
        args.note,
    )
    print(f"已记录成交，trade id={trade_id}")


def cmd_trade_delete(args, conn) -> None:
    delete_trade(conn, args.id)
    print(f"已删除成交记录 id={args.id}")


def cmd_trade_list(args, conn) -> None:
    rows = list_trades(conn, args.market, args.code, args.all)
    if not rows:
        print("暂无成交记录")
        return
    print(
        render_table(
            ["ID", "时间", "市场", "代码", "方向", "数量", "价格", "币种", "佣金", "平台费", "过户费", "印花税", "FX", "状态"],
            [
                [
                    row["id"],
                    to_local_display(row["trade_time"]),
                    row["market"],
                    row["code"],
                    row["side"],
                    row["qty"],
                    row["price"],
                    row["currency"],
                    row["fee_commission"],
                    row["fee_platform"],
                    row["fee_transfer"],
                    row["tax_stamp"],
                    row["fx_rate"] or "-",
                    "deleted" if row["is_deleted"] else "active",
                ]
                for row in rows
            ],
        )
    )


def cmd_watch_add(args, conn) -> None:
    add_watch(conn, args.market, args.code, args.note)
    print(f"已加入自选：{args.market} {args.code}")


def cmd_watch_delete(args, conn) -> None:
    delete_watch(conn, args.market, args.code)
    print(f"已移除自选：{args.market} {args.code}")


def cmd_watch_list(_args, conn) -> None:
    rows = list_watch(conn)
    if not rows:
        print("暂无自选股")
        return
    print(
        render_table(
            ["市场", "代码", "备注", "创建时间"],
            [[row["market"], row["code"], row["note"], to_local_display(row["created_at"])] for row in rows],
        )
    )


def cmd_capital_set(args, conn) -> None:
    set_capital(conn, args.total, args.max_single_pct)
    print("资金约束已更新")


def cmd_capital_show(_args, conn) -> None:
    print(pretty_json(capital_settings(conn)))


def cmd_portfolio_sync(args, conn) -> None:
    rows = sync_portfolio(conn, args.market, args.code)
    if not rows:
        print("没有可同步的持仓")
        return
    print(f"已同步 {len(rows)} 个标的")


def cmd_portfolio_show(args, conn) -> None:
    rows = latest_snapshots(conn, args.market)
    if not rows:
        print("暂无持仓快照，请先运行 portfolio sync")
        return
    print(
        render_table(
            ["市场", "代码", "持仓", "摊薄成本", "币种", "最新价", "未实现盈亏", "已实现盈亏", "市值(CNY)", "快照时间"],
            [
                [
                    row["market"],
                    row["code"],
                    row["qty"],
                    row["avg_cost_price"],
                    row["currency"],
                    row["last_price"] or "-",
                    row["unrealized_pnl_ccy"] or "-",
                    row["realized_pnl_ccy"] or "-",
                    row["position_value_cny"] or "-",
                    to_local_display(row["snapshot_time"]),
                ]
                for row in rows
            ],
        )
    )


def cmd_analyze_now(args, conn) -> None:
    payload = analyze_now(conn, args.scope, args.market, args.code)
    if args.format == "json":
        print(pretty_json(payload))
    else:
        print(render_analysis_text(payload))


def cmd_report_snapshot(args, conn) -> None:
    cmd_portfolio_show(args, conn)


def cmd_report_pnl(_args, conn) -> None:
    print(pretty_json(pnl_summary(conn)))


def cmd_report_decision(_args, conn) -> None:
    row = latest_analysis_run(conn)
    if not row:
        print("暂无分析记录")
        return
    print(pretty_json(row))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Stock Portfolio Copilot")
    sub = ap.add_subparsers(dest="group", required=True)

    p_position = sub.add_parser("position")
    p_position_sub = p_position.add_subparsers(dest="action", required=True)
    p_position_init = p_position_sub.add_parser("init")
    p_position_init.add_argument("--market", required=True)
    p_position_init.add_argument("--code", required=True)
    p_position_init.add_argument("--qty", required=True)
    p_position_init.add_argument("--cost", required=True)
    p_position_init.add_argument("--currency")
    p_position_init.add_argument("--time")
    p_position_init.add_argument("--note", default="")
    p_position_init.set_defaults(func=cmd_position_init)
    p_position_list = p_position_sub.add_parser("list")
    p_position_list.add_argument("--market")
    p_position_list.set_defaults(func=cmd_position_list)

    p_trade = sub.add_parser("trade")
    p_trade_sub = p_trade.add_subparsers(dest="action", required=True)
    p_trade_add = p_trade_sub.add_parser("add")
    p_trade_add.add_argument("--market", required=True)
    p_trade_add.add_argument("--code", required=True)
    p_trade_add.add_argument("--side", required=True)
    p_trade_add.add_argument("--qty", required=True)
    p_trade_add.add_argument("--price", required=True)
    p_trade_add.add_argument("--time", required=True)
    p_trade_add.add_argument("--currency")
    p_trade_add.add_argument("--fx-rate")
    p_trade_add.add_argument("--fee-commission")
    p_trade_add.add_argument("--fee-platform")
    p_trade_add.add_argument("--fee-transfer")
    p_trade_add.add_argument("--tax-stamp")
    p_trade_add.add_argument("--note", default="")
    p_trade_add.set_defaults(func=cmd_trade_add)
    p_trade_delete = p_trade_sub.add_parser("delete")
    p_trade_delete.add_argument("--id", type=int, required=True)
    p_trade_delete.set_defaults(func=cmd_trade_delete)
    p_trade_list = p_trade_sub.add_parser("list")
    p_trade_list.add_argument("--market")
    p_trade_list.add_argument("--code")
    p_trade_list.add_argument("--all", action="store_true")
    p_trade_list.set_defaults(func=cmd_trade_list)

    p_watch = sub.add_parser("watch")
    p_watch_sub = p_watch.add_subparsers(dest="action", required=True)
    p_watch_add = p_watch_sub.add_parser("add")
    p_watch_add.add_argument("--market", required=True)
    p_watch_add.add_argument("--code", required=True)
    p_watch_add.add_argument("--note", default="")
    p_watch_add.set_defaults(func=cmd_watch_add)
    p_watch_delete = p_watch_sub.add_parser("delete")
    p_watch_delete.add_argument("--market", required=True)
    p_watch_delete.add_argument("--code", required=True)
    p_watch_delete.set_defaults(func=cmd_watch_delete)
    p_watch_list = p_watch_sub.add_parser("list")
    p_watch_list.set_defaults(func=cmd_watch_list)

    p_capital = sub.add_parser("capital")
    p_capital_sub = p_capital.add_subparsers(dest="action", required=True)
    p_capital_set = p_capital_sub.add_parser("set")
    p_capital_set.add_argument("--total")
    p_capital_set.add_argument("--max-single-pct")
    p_capital_set.set_defaults(func=cmd_capital_set)
    p_capital_show = p_capital_sub.add_parser("show")
    p_capital_show.set_defaults(func=cmd_capital_show)

    p_portfolio = sub.add_parser("portfolio")
    p_portfolio_sub = p_portfolio.add_subparsers(dest="action", required=True)
    p_portfolio_sync = p_portfolio_sub.add_parser("sync")
    p_portfolio_sync.add_argument("--market")
    p_portfolio_sync.add_argument("--code")
    p_portfolio_sync.set_defaults(func=cmd_portfolio_sync)
    p_portfolio_show = p_portfolio_sub.add_parser("show")
    p_portfolio_show.add_argument("--market")
    p_portfolio_show.set_defaults(func=cmd_portfolio_show)

    p_analyze = sub.add_parser("analyze")
    p_analyze_sub = p_analyze.add_subparsers(dest="action", required=True)
    p_analyze_now = p_analyze_sub.add_parser("now")
    p_analyze_now.add_argument("--scope", choices=["holdings", "watchlist", "all"], default="holdings")
    p_analyze_now.add_argument("--market")
    p_analyze_now.add_argument("--code")
    p_analyze_now.add_argument("--format", choices=["text", "json"], default="text")
    p_analyze_now.set_defaults(func=cmd_analyze_now)

    p_report = sub.add_parser("report")
    p_report_sub = p_report.add_subparsers(dest="action", required=True)
    p_report_snapshot = p_report_sub.add_parser("snapshot")
    p_report_snapshot.add_argument("--market")
    p_report_snapshot.set_defaults(func=cmd_report_snapshot)
    p_report_pnl = p_report_sub.add_parser("pnl")
    p_report_pnl.set_defaults(func=cmd_report_pnl)
    p_report_decision = p_report_sub.add_parser("decision")
    p_report_decision.set_defaults(func=cmd_report_decision)
    return ap


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    conn = connect()
    try:
        args.func(args, conn)
        return 0
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"失败：{exc}", file=sys.stderr)
        return 5
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

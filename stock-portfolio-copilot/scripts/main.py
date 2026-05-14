#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from spc_core.audit import render_diff, render_explain, render_log, render_show
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
from spc_core.portfolio import check_portfolio_consistency, pnl_summary, sync_portfolio
from spc_core.settings import (
    capital_settings,
    resolve_account,
    set_capital,
    show_settings_json,
)
from spc_core.utils import pretty_json, render_table, to_local_display, utc_now_iso


# ── helpers ─────────────────────────────────────────────────────────

def _add_account_arg(parser, required: bool = True) -> None:
    parser.add_argument("--account", required=required, help="账户 slug")


def _resolve(conn, args) -> dict:
    return resolve_account(conn, args.account)


# ── account commands ────────────────────────────────────────────────

def cmd_account_create(args, conn) -> None:
    slug = args.slug.strip().lower()
    if not slug or " " in slug:
        raise ValueError("slug 不能为空或包含空格")
    existing = conn.execute("SELECT id FROM accounts WHERE slug = ?", (slug,)).fetchone()
    if existing:
        raise ValueError(f"账户 slug 已存在: {slug}")
    now = utc_now_iso()
    is_default = 1 if args.set_default else 0
    if args.set_default:
        conn.execute("UPDATE accounts SET is_default = 0")
    conn.execute(
        """
        INSERT INTO accounts(slug, display_name, broker, base_currency, note, is_default, is_archived, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (slug, args.name, args.broker or "", args.currency or "CNY", args.note or "", is_default, now, now),
    )
    conn.commit()
    print(f"已创建账户：{slug} ({args.name})")


def cmd_account_list(args, conn) -> None:
    rows = conn.execute(
        "SELECT slug, display_name, broker, base_currency, is_default, is_archived, created_at FROM accounts ORDER BY is_default DESC, slug"
    ).fetchall()
    if not rows:
        print("暂无账户")
        return
    print(
        render_table(
            ["slug", "名称", "券商", "币种", "默认", "归档", "创建时间"],
            [
                [
                    row["slug"],
                    row["display_name"],
                    row["broker"] or "-",
                    row["base_currency"],
                    "yes" if row["is_default"] else "",
                    "yes" if row["is_archived"] else "",
                    to_local_display(row["created_at"]),
                ]
                for row in rows
            ],
        )
    )


def cmd_account_show(args, conn) -> None:
    acct = _resolve(conn, args)
    print(pretty_json(acct))


def cmd_account_update(args, conn) -> None:
    acct = _resolve(conn, args)
    updates = []
    params = []
    if args.name:
        updates.append("display_name = ?")
        params.append(args.name)
    if args.broker is not None:
        updates.append("broker = ?")
        params.append(args.broker)
    if args.currency:
        updates.append("base_currency = ?")
        params.append(args.currency)
    if args.note is not None:
        updates.append("note = ?")
        params.append(args.note)
    if not updates:
        print("没有需要更新的字段")
        return
    updates.append("updated_at = ?")
    params.append(utc_now_iso())
    params.append(acct["id"])
    conn.execute(f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    print(f"已更新账户：{acct['slug']}")


def cmd_account_archive(args, conn) -> None:
    acct = _resolve(conn, args)
    conn.execute("UPDATE accounts SET is_archived = 1, updated_at = ? WHERE id = ?", (utc_now_iso(), acct["id"]))
    conn.commit()
    print(f"已归档账户：{acct['slug']}")


def cmd_account_unarchive(args, conn) -> None:
    acct = _resolve(conn, args)
    conn.execute("UPDATE accounts SET is_archived = 0, updated_at = ? WHERE id = ?", (utc_now_iso(), acct["id"]))
    conn.commit()
    print(f"已取消归档：{acct['slug']}")


def cmd_account_set_default(args, conn) -> None:
    acct = _resolve(conn, args)
    conn.execute("UPDATE accounts SET is_default = 0")
    conn.execute("UPDATE accounts SET is_default = 1, updated_at = ? WHERE id = ?", (utc_now_iso(), acct["id"]))
    conn.commit()
    print(f"已设为默认账户：{acct['slug']}")


# ── position commands ───────────────────────────────────────────────

def cmd_position_init(args, conn) -> None:
    acct = _resolve(conn, args)
    add_position_seed(
        conn,
        acct["id"],
        args.market,
        args.code,
        args.qty,
        args.cost,
        args.currency,
        args.time,
        args.note,
        force=args.force,
    )
    suffix = "（覆盖已有 seed）" if args.force else ""
    print(f"已初始化持仓：{args.market} {args.code}（账户 {acct['slug']}）{suffix}")


def cmd_position_list(args, conn) -> None:
    acct = _resolve(conn, args)
    rows = list_position_seed(conn, acct["id"], args.market)
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


# ── trade commands ──────────────────────────────────────────────────

def cmd_trade_add(args, conn) -> None:
    acct = _resolve(conn, args)
    trade_id = add_trade(
        conn,
        acct["id"],
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
    print(f"已记录成交，trade id={trade_id}（账户 {acct['slug']}）")


def cmd_trade_delete(args, conn) -> None:
    acct = _resolve(conn, args)
    delete_trade(conn, acct["id"], args.id)
    print(f"已删除成交记录 id={args.id}（账户 {acct['slug']}）")


def cmd_trade_list(args, conn) -> None:
    acct = _resolve(conn, args)
    rows = list_trades(conn, acct["id"], args.market, args.code, args.all)
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


# ── watch commands ──────────────────────────────────────────────────

def cmd_watch_add(args, conn) -> None:
    acct = _resolve(conn, args)
    add_watch(conn, acct["id"], args.market, args.code, args.note)
    print(f"已加入自选：{args.market} {args.code}（账户 {acct['slug']}）")


def cmd_watch_delete(args, conn) -> None:
    acct = _resolve(conn, args)
    delete_watch(conn, acct["id"], args.market, args.code)
    print(f"已移除自选：{args.market} {args.code}（账户 {acct['slug']}）")


def cmd_watch_list(args, conn) -> None:
    acct = _resolve(conn, args)
    rows = list_watch(conn, acct["id"])
    if not rows:
        print("暂无自选股")
        return
    print(
        render_table(
            ["市场", "代码", "备注", "创建时间"],
            [[row["market"], row["code"], row["note"], to_local_display(row["created_at"])] for row in rows],
        )
    )


# ── capital commands ────────────────────────────────────────────────

def cmd_capital_set(args, conn) -> None:
    acct = _resolve(conn, args)
    set_capital(conn, acct["id"], args.total, args.max_single_pct)
    print(f"资金约束已更新（账户 {acct['slug']}）")


def cmd_capital_show(args, conn) -> None:
    acct = _resolve(conn, args)
    caps = capital_settings(conn, acct["id"])
    print(pretty_json({"account": acct["slug"], **caps}))


# ── portfolio commands ──────────────────────────────────────────────

def cmd_portfolio_sync(args, conn) -> None:
    acct = _resolve(conn, args)
    rows = sync_portfolio(conn, acct["id"], args.market, args.code)
    if not rows:
        print("没有可同步的持仓")
        return
    print(f"已同步 {len(rows)} 个标的（账户 {acct['slug']}）")


def cmd_portfolio_check(args, conn) -> None:
    """检查每只标的的 seed + trade + snapshot 一致性，识别 NO_TRADES / 残股等问题。"""
    acct = _resolve(conn, args)
    reports = check_portfolio_consistency(conn, acct["id"])
    if not reports:
        print(f"账户 {acct['slug']}：没有 seed 也没有 trade，无需检查")
        return

    print(f"账户 {acct['slug']} 一致性检查（{len(reports)} 个标的）")
    print()

    status_counts = {"OK": 0, "WARN": 0, "FAIL": 0}
    rows = []
    for r in reports:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
        flag = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(r["status"], r["status"])
        snap_qty = r["snapshot_qty"] or "-"
        rows.append([
            flag,
            r["market"],
            r["code"],
            r["seed_qty"] or "-",
            str(r["trade_count"]),
            r["derived_qty"],
            snap_qty,
            "\n".join(r["messages"]) if r["messages"] else "-",
        ])
    print(
        render_table(
            ["状态", "市场", "代码", "seed_qty", "trades", "推算 qty", "snapshot qty", "问题"],
            rows,
        )
    )
    print()
    print(
        f"汇总：✅ OK {status_counts.get('OK', 0)} / "
        f"⚠️ WARN {status_counts.get('WARN', 0)} / "
        f"❌ FAIL {status_counts.get('FAIL', 0)}"
    )


def cmd_portfolio_show(args, conn) -> None:
    acct = _resolve(conn, args)
    rows = latest_snapshots(conn, acct["id"], args.market)
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


# ── analyze ─────────────────────────────────────────────────────────

def cmd_analyze_now(args, conn) -> None:
    acct = _resolve(conn, args)
    payload = analyze_now(conn, acct["id"], acct["slug"], acct["display_name"], args.scope, args.market, args.code)
    if args.format == "json":
        print(pretty_json(payload))
    else:
        print(render_analysis_text(payload))


# ── report commands ─────────────────────────────────────────────────

def cmd_report_snapshot(args, conn) -> None:
    cmd_portfolio_show(args, conn)


def cmd_report_pnl(args, conn) -> None:
    acct = _resolve(conn, args)
    summary = pnl_summary(conn, acct["id"])
    print(pretty_json({"account": acct["slug"], **summary}))


def cmd_report_decision(args, conn) -> None:
    acct = _resolve(conn, args)
    row = latest_analysis_run(conn, acct["id"])
    if not row:
        print("暂无分析记录")
        return
    print(pretty_json(row))


# ── audit commands（spc explain / log / show / diff） ───────────────

def cmd_explain(args, conn) -> None:
    acct = _resolve(conn, args)
    text = render_explain(
        conn, acct["id"], acct["slug"],
        analysis_id=args.analysis_id,
        market=args.market,
        code=args.code,
    )
    print(text)


def cmd_log(args, conn) -> None:
    acct = _resolve(conn, args)
    text = render_log(
        conn, acct["id"], acct["slug"],
        market=args.market, code=args.code,
        since=args.since, until=args.until,
        limit=args.limit,
    )
    print(text)


def cmd_show(args, conn) -> None:
    acct = _resolve(conn, args)
    text = render_show(conn, acct["id"], acct["slug"], args.analysis_id)
    print(text)


def cmd_diff(args, conn) -> None:
    acct = _resolve(conn, args)
    between: tuple[str, str] | None = None
    if args.between:
        if len(args.between) != 2:
            raise ValueError("--between 需要两个日期，例：--between 2026-05-06 2026-05-13")
        between = (args.between[0], args.between[1])
    text = render_diff(
        conn, acct["id"], acct["slug"],
        market=args.market, code=args.code,
        since=args.since, until=args.until,
        between=between,
    )
    print(text)


# ── parser ──────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Stock Portfolio Copilot")
    sub = ap.add_subparsers(dest="group", required=True)

    # account
    p_account = sub.add_parser("account")
    p_account_sub = p_account.add_subparsers(dest="action", required=True)

    p_account_create = p_account_sub.add_parser("create")
    p_account_create.add_argument("--slug", required=True)
    p_account_create.add_argument("--name", required=True)
    p_account_create.add_argument("--broker", default="")
    p_account_create.add_argument("--currency", default="CNY")
    p_account_create.add_argument("--note", default="")
    p_account_create.add_argument("--set-default", action="store_true")
    p_account_create.set_defaults(func=cmd_account_create)

    p_account_list = p_account_sub.add_parser("list")
    p_account_list.set_defaults(func=cmd_account_list)

    p_account_show = p_account_sub.add_parser("show")
    _add_account_arg(p_account_show)
    p_account_show.set_defaults(func=cmd_account_show)

    p_account_update = p_account_sub.add_parser("update")
    _add_account_arg(p_account_update)
    p_account_update.add_argument("--name")
    p_account_update.add_argument("--broker")
    p_account_update.add_argument("--currency")
    p_account_update.add_argument("--note")
    p_account_update.set_defaults(func=cmd_account_update)

    p_account_archive = p_account_sub.add_parser("archive")
    _add_account_arg(p_account_archive)
    p_account_archive.set_defaults(func=cmd_account_archive)

    p_account_unarchive = p_account_sub.add_parser("unarchive")
    _add_account_arg(p_account_unarchive)
    p_account_unarchive.set_defaults(func=cmd_account_unarchive)

    p_account_set_default = p_account_sub.add_parser("set-default")
    _add_account_arg(p_account_set_default)
    p_account_set_default.set_defaults(func=cmd_account_set_default)

    # position
    p_position = sub.add_parser("position")
    p_position_sub = p_position.add_subparsers(dest="action", required=True)
    p_position_init = p_position_sub.add_parser("init")
    _add_account_arg(p_position_init)
    p_position_init.add_argument("--market", required=True)
    p_position_init.add_argument("--code", required=True)
    p_position_init.add_argument("--qty", required=True)
    p_position_init.add_argument("--cost", required=True)
    p_position_init.add_argument("--currency")
    p_position_init.add_argument("--time")
    p_position_init.add_argument("--note", default="")
    p_position_init.add_argument(
        "--force",
        action="store_true",
        help="覆盖已有 seed（仅适用于残股摊薄等罕见场景；有 trade 时仍会被拒绝）",
    )
    p_position_init.set_defaults(func=cmd_position_init)
    p_position_list = p_position_sub.add_parser("list")
    _add_account_arg(p_position_list)
    p_position_list.add_argument("--market")
    p_position_list.set_defaults(func=cmd_position_list)

    # trade
    p_trade = sub.add_parser("trade")
    p_trade_sub = p_trade.add_subparsers(dest="action", required=True)
    p_trade_add = p_trade_sub.add_parser("add")
    _add_account_arg(p_trade_add)
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
    _add_account_arg(p_trade_delete)
    p_trade_delete.add_argument("--id", type=int, required=True)
    p_trade_delete.set_defaults(func=cmd_trade_delete)
    p_trade_list = p_trade_sub.add_parser("list")
    _add_account_arg(p_trade_list)
    p_trade_list.add_argument("--market")
    p_trade_list.add_argument("--code")
    p_trade_list.add_argument("--all", action="store_true")
    p_trade_list.set_defaults(func=cmd_trade_list)

    # watch
    p_watch = sub.add_parser("watch")
    p_watch_sub = p_watch.add_subparsers(dest="action", required=True)
    p_watch_add = p_watch_sub.add_parser("add")
    _add_account_arg(p_watch_add)
    p_watch_add.add_argument("--market", required=True)
    p_watch_add.add_argument("--code", required=True)
    p_watch_add.add_argument("--note", default="")
    p_watch_add.set_defaults(func=cmd_watch_add)
    p_watch_delete = p_watch_sub.add_parser("delete")
    _add_account_arg(p_watch_delete)
    p_watch_delete.add_argument("--market", required=True)
    p_watch_delete.add_argument("--code", required=True)
    p_watch_delete.set_defaults(func=cmd_watch_delete)
    p_watch_list = p_watch_sub.add_parser("list")
    _add_account_arg(p_watch_list)
    p_watch_list.set_defaults(func=cmd_watch_list)

    # capital
    p_capital = sub.add_parser("capital")
    p_capital_sub = p_capital.add_subparsers(dest="action", required=True)
    p_capital_set = p_capital_sub.add_parser("set")
    _add_account_arg(p_capital_set)
    p_capital_set.add_argument("--total")
    p_capital_set.add_argument("--max-single-pct")
    p_capital_set.set_defaults(func=cmd_capital_set)
    p_capital_show = p_capital_sub.add_parser("show")
    _add_account_arg(p_capital_show)
    p_capital_show.set_defaults(func=cmd_capital_show)

    # portfolio
    p_portfolio = sub.add_parser("portfolio")
    p_portfolio_sub = p_portfolio.add_subparsers(dest="action", required=True)
    p_portfolio_sync = p_portfolio_sub.add_parser("sync")
    _add_account_arg(p_portfolio_sync)
    p_portfolio_sync.add_argument("--market")
    p_portfolio_sync.add_argument("--code")
    p_portfolio_sync.set_defaults(func=cmd_portfolio_sync)
    p_portfolio_show = p_portfolio_sub.add_parser("show")
    _add_account_arg(p_portfolio_show)
    p_portfolio_show.add_argument("--market")
    p_portfolio_show.set_defaults(func=cmd_portfolio_show)
    p_portfolio_check = p_portfolio_sub.add_parser(
        "check",
        help="一致性审计：seed + trade + snapshot 是否对齐，识别 NO_TRADES / 残股 / 数据漂移",
    )
    _add_account_arg(p_portfolio_check)
    p_portfolio_check.set_defaults(func=cmd_portfolio_check)

    # analyze
    p_analyze = sub.add_parser("analyze")
    p_analyze_sub = p_analyze.add_subparsers(dest="action", required=True)
    p_analyze_now = p_analyze_sub.add_parser("now")
    _add_account_arg(p_analyze_now)
    p_analyze_now.add_argument("--scope", choices=["holdings", "watchlist", "all"], default="holdings")
    p_analyze_now.add_argument("--market")
    p_analyze_now.add_argument("--code")
    p_analyze_now.add_argument("--format", choices=["text", "json"], default="text")
    p_analyze_now.set_defaults(func=cmd_analyze_now)

    # report
    p_report = sub.add_parser("report")
    p_report_sub = p_report.add_subparsers(dest="action", required=True)
    p_report_snapshot = p_report_sub.add_parser("snapshot")
    _add_account_arg(p_report_snapshot)
    p_report_snapshot.add_argument("--market")
    p_report_snapshot.set_defaults(func=cmd_report_snapshot)
    p_report_pnl = p_report_sub.add_parser("pnl")
    _add_account_arg(p_report_pnl)
    p_report_pnl.set_defaults(func=cmd_report_pnl)
    p_report_decision = p_report_sub.add_parser("decision")
    _add_account_arg(p_report_decision)
    p_report_decision.set_defaults(func=cmd_report_decision)

    # ── audit: explain / log / show / diff ────────────────────────
    p_explain = sub.add_parser(
        "explain",
        help="展开某次 analyze 的 confidence_trace（'为什么 0.78'）",
    )
    _add_account_arg(p_explain)
    p_explain.add_argument(
        "--analysis-id", type=int,
        help="目标 analysis_run id；不传则取该账户最新一次",
    )
    p_explain.add_argument("--market", help="只展开 results 里指定 market 的标的")
    p_explain.add_argument("--code", help="配合 --market 锁定单只标的")
    p_explain.set_defaults(func=cmd_explain)

    p_log = sub.add_parser(
        "log",
        help="列出最近 N 次 analyze_now（可按 symbol / 时间窗口过滤）",
    )
    _add_account_arg(p_log)
    p_log.add_argument("--market", help="按 market 过滤")
    p_log.add_argument("--code", help="按 code 过滤（必须配合 --market）")
    p_log.add_argument("--since", help="起点；支持 '7d'/'12h'/'2w'/'2026-05-06'")
    p_log.add_argument("--until", help="终点（不含）")
    p_log.add_argument("--limit", type=int, default=20)
    p_log.set_defaults(func=cmd_log)

    p_show = sub.add_parser(
        "show",
        help="按 analysis-id 显示某次 analyze 的完整结构化输出",
    )
    _add_account_arg(p_show)
    p_show.add_argument("--analysis-id", type=int, required=True)
    p_show.set_defaults(func=cmd_show)

    p_diff = sub.add_parser(
        "diff",
        help="对比同一只标的在两个时点的决策（默认窗口内首尾两次）",
    )
    _add_account_arg(p_diff)
    p_diff.add_argument("--market", required=True)
    p_diff.add_argument("--code", required=True)
    p_diff.add_argument("--since", help="窗口起点；支持 '7d'/'2026-05-06' 等")
    p_diff.add_argument("--until", help="窗口终点（不含）")
    p_diff.add_argument(
        "--between", nargs=2, metavar=("FROM", "TO"),
        help="显式指定两个时点附近的 run 对比，覆盖 --since/--until",
    )
    p_diff.set_defaults(func=cmd_diff)

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

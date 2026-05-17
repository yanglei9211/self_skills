#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from spc_core.audit import render_diff, render_explain, render_log, render_show
from spc_core.db import connect
from spc_core.decision import analyze_now, render_analysis_text
from spc_core.ledger import (
    EXEC_PLAN_LIST_DEFAULT_LIMIT,
    add_execution_review,
    add_position_seed,
    add_trade,
    add_watch,
    attach_trade_to_plan,
    cancel_execution_plan,
    create_execution_plan,
    delete_trade,
    delete_watch,
    get_execution_plan_detail,
    latest_analysis_run,
    latest_snapshots,
    list_execution_plans,
    list_position_seed,
    list_trades,
    list_watch,
    update_execution_plan,
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


def _parse_optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    text = value.strip().lower()
    if text in {"yes", "y", "true", "1"}:
        return True
    if text in {"no", "n", "false", "0"}:
        return False
    raise ValueError(f"无法识别的布尔值: {value}；请用 yes/no")


def _render_execution_plan(detail: dict) -> str:
    fill = detail.get("fill_summary") or {}
    completion = f"{fill['completion_pct']}%" if fill.get("completion_pct") else "-"
    lines = [
        f"执行计划 #{detail['id']}",
        f"标的：{str(detail['market']).upper()} {detail['code']}",
        f"方向：{detail['side']}",
        f"动作：{detail['action_type']}",
        f"状态：{detail['status']}",
    ]
    if detail.get("source_ref_id"):
        source_desc = f"analysis_run #{detail['source_ref_id']}"
        if detail.get("source_action"):
            source_desc += f" / source_action={detail['source_action']}"
        if detail.get("confidence"):
            source_desc += f" / confidence={detail['confidence']}"
        lines.append(f"来源：{source_desc}")
    lines.extend(
        [
            "",
            "计划：",
            f"- thesis：{detail['thesis']}",
            f"- invalidation：{detail.get('invalidation') or '-'}",
            f"- target_qty：{detail.get('target_qty') or '-'}",
            f"- target_cash_cny：{detail.get('target_cash_cny') or '-'}",
            f"- target_position_pct：{detail.get('target_position_pct') or '-'}",
            f"- 价格区间：{(detail.get('price_limit_low') or '-')}" +
            f" - {(detail.get('price_limit_high') or '-')}",
            f"- 止损/止盈：{detail.get('stop_loss_price') or '-'} / {detail.get('take_profit_price') or '-'}",
            f"- 加仓条件：{detail.get('add_condition') or '-'}",
            f"- 减仓条件：{detail.get('reduce_condition') or '-'}",
            f"- 标签：{detail.get('tags') or '-'}",
        ]
    )
    lines.extend(
        [
            "",
            "成交：",
            f"- 已成交笔数：{fill.get('trade_count', 0)}",
            f"- 已成交数量：{fill.get('filled_qty', '-')}",
            f"- 成交均价：{fill.get('avg_price') or '-'}",
            f"- 完成度：{completion}",
        ]
    )

    trades = detail.get("trades") or []
    if trades:
        lines.extend(
            [
                "",
                render_table(
                    ["trade_id", "时间", "方向", "数量", "价格", "币种", "role", "备注"],
                    [
                        [
                            row["id"],
                            to_local_display(row["trade_time"]),
                            row["side"],
                            row["qty"],
                            row["price"],
                            row["currency"],
                            row["role"],
                            row["note"] or "-",
                        ]
                        for row in trades
                    ],
                ),
            ]
        )

    reviews = detail.get("reviews") or []
    if reviews:
        lines.extend(
            [
                "",
                render_table(
                    ["review_id", "时间", "horizon", "outcome", "纪律", "执行", "假设", "lesson"],
                    [
                        [
                            row["id"],
                            to_local_display(row["review_time"]),
                            row["horizon"],
                            row["outcome"],
                            row["discipline_score"] if row["discipline_score"] is not None else "-",
                            row["execution_score"] if row["execution_score"] is not None else "-",
                            row["thesis_score"] if row["thesis_score"] is not None else "-",
                            row["lesson"],
                        ]
                        for row in reviews
                    ],
                ),
            ]
        )
    return "\n".join(lines)


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
        args.plan_id,
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

    # 默认隐藏 qty=0 的清零仓位（仍有 realized_pnl，但已无操作意义），加 --include-cleared 显示全部
    if not args.include_cleared:
        from decimal import Decimal as _D
        active = [r for r in rows if r["qty"] is not None and _D(str(r["qty"])) != 0]
        hidden = len(rows) - len(active)
        rows = active
        if hidden > 0:
            print(f"（已隐藏 {hidden} 个 qty=0 的已清零标的，用 --include-cleared 显示全部）")
            if not rows:
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


# ── execution commands ──────────────────────────────────────────────

def cmd_exec_plan_create(args, conn) -> None:
    acct = _resolve(conn, args)
    plan_id = create_execution_plan(
        conn,
        acct["id"],
        args.market,
        args.code,
        args.side,
        args.action_type,
        args.thesis,
        status=args.status,
        source_ref_id=args.source_analysis_id,
        source_action=args.source_action,
        invalidation=args.invalidation,
        target_qty=args.target_qty,
        target_cash_cny=args.target_cash_cny,
        target_position_pct=args.target_position_pct,
        price_limit_low=args.price_limit_low,
        price_limit_high=args.price_limit_high,
        stop_loss_price=args.stop_loss_price,
        take_profit_price=args.take_profit_price,
        add_condition=args.add_condition,
        reduce_condition=args.reduce_condition,
        time_window_start=args.time_window_start,
        time_window_end=args.time_window_end,
        confidence=args.confidence,
        risk_level=args.risk_level,
        tags=args.tags,
        note=args.note,
        force=args.force,
    )
    print(f"已创建执行计划 id={plan_id}（账户 {acct['slug']}）")


def cmd_exec_plan_list(args, conn) -> None:
    acct = _resolve(conn, args)
    rows = list_execution_plans(
        conn,
        acct["id"],
        status=args.status,
        market=args.market,
        code=args.code,
        limit=args.limit,
    )
    if not rows:
        print("暂无执行计划")
        return
    table_rows = []
    for row in rows:
        fill = get_execution_plan_detail(conn, acct["id"], row["id"])["fill_summary"]
        table_rows.append(
            [
                row["id"],
                row["status"],
                row["market"],
                row["code"],
                row["side"],
                row["action_type"],
                row["target_qty"] or "-",
                fill["filled_qty"],
                fill["completion_pct"] or "-",
                to_local_display(row["created_at"]),
            ]
        )
    print(
        render_table(
            ["ID", "状态", "市场", "代码", "方向", "动作", "目标数量", "已成交", "完成度%", "创建时间"],
            table_rows,
        )
    )


def cmd_exec_plan_show(args, conn) -> None:
    acct = _resolve(conn, args)
    detail = get_execution_plan_detail(conn, acct["id"], args.id)
    print(_render_execution_plan(detail))


def cmd_exec_plan_cancel(args, conn) -> None:
    acct = _resolve(conn, args)
    status = "expired" if args.expire else "cancelled"
    cancel_execution_plan(
        conn,
        acct["id"],
        args.id,
        reason=args.reason,
        new_status=status,
    )
    print(f"已将执行计划 id={args.id} 标记为 {status}（账户 {acct['slug']}）")


def cmd_exec_plan_update(args, conn) -> None:
    acct = _resolve(conn, args)
    update_fields = [
        "action_type",
        "thesis",
        "invalidation",
        "target_qty",
        "target_cash_cny",
        "target_position_pct",
        "price_limit_low",
        "price_limit_high",
        "stop_loss_price",
        "take_profit_price",
        "add_condition",
        "reduce_condition",
        "time_window_start",
        "time_window_end",
        "confidence",
        "risk_level",
        "tags",
        "note",
    ]
    updates = {
        field: getattr(args, field)
        for field in update_fields
        if getattr(args, field) is not None
    }
    detail = update_execution_plan(conn, acct["id"], args.id, updates=updates)
    print(f"已更新执行计划 id={args.id}（状态 {detail['status']}，账户 {acct['slug']}）")


def cmd_exec_attach(args, conn) -> None:
    acct = _resolve(conn, args)
    attach_trade_to_plan(
        conn,
        acct["id"],
        args.plan_id,
        args.trade_id,
        role=args.role,
        note=args.note,
        force=args.force,
    )
    print(f"已关联 plan id={args.plan_id} 和 trade id={args.trade_id}（账户 {acct['slug']}）")


def cmd_exec_review_add(args, conn) -> None:
    acct = _resolve(conn, args)
    review_id = add_execution_review(
        conn,
        acct["id"],
        plan_id=args.plan_id,
        trade_id=args.trade_id,
        review_time=args.time,
        horizon=args.horizon,
        outcome=args.outcome,
        discipline_score=args.discipline_score,
        execution_score=args.execution_score,
        thesis_score=args.thesis_score,
        plan_followed=_parse_optional_bool(args.plan_followed),
        mistake_tags=args.mistake_tags,
        good_tags=args.good_tags,
        pnl_snapshot_cny=args.pnl_snapshot_cny,
        max_favorable_excursion_pct=args.max_favorable_excursion_pct,
        max_adverse_excursion_pct=args.max_adverse_excursion_pct,
        lesson=args.lesson,
        next_rule=args.next_rule,
        note=args.note,
    )
    print(f"已记录执行复盘 id={review_id}（账户 {acct['slug']}）")


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
    p_trade_add.add_argument("--plan-id", type=int)
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
    p_portfolio_show.add_argument(
        "--include-cleared",
        action="store_true",
        help="同时显示 qty=0 的已清零仓位（默认隐藏，仅展示活仓）",
    )
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

    # execution
    p_exec = sub.add_parser("exec")
    p_exec_sub = p_exec.add_subparsers(dest="section", required=True)

    p_exec_plan = p_exec_sub.add_parser("plan")
    p_exec_plan_sub = p_exec_plan.add_subparsers(dest="action", required=True)

    p_exec_plan_create = p_exec_plan_sub.add_parser("create")
    _add_account_arg(p_exec_plan_create)
    p_exec_plan_create.add_argument("--market", required=True)
    p_exec_plan_create.add_argument("--code", required=True)
    p_exec_plan_create.add_argument("--side", required=True)
    p_exec_plan_create.add_argument("--action-type", required=True)
    p_exec_plan_create.add_argument("--thesis", required=True)
    p_exec_plan_create.add_argument("--status", default="planned")
    p_exec_plan_create.add_argument("--source-analysis-id", type=int)
    p_exec_plan_create.add_argument("--source-action")
    p_exec_plan_create.add_argument("--invalidation")
    p_exec_plan_create.add_argument("--target-qty")
    p_exec_plan_create.add_argument("--target-cash-cny")
    p_exec_plan_create.add_argument("--target-position-pct")
    p_exec_plan_create.add_argument("--price-limit-low")
    p_exec_plan_create.add_argument("--price-limit-high")
    p_exec_plan_create.add_argument("--stop-loss-price")
    p_exec_plan_create.add_argument("--take-profit-price")
    p_exec_plan_create.add_argument("--add-condition")
    p_exec_plan_create.add_argument("--reduce-condition")
    p_exec_plan_create.add_argument("--time-window-start")
    p_exec_plan_create.add_argument("--time-window-end")
    p_exec_plan_create.add_argument("--confidence")
    p_exec_plan_create.add_argument("--risk-level")
    p_exec_plan_create.add_argument("--tags")
    p_exec_plan_create.add_argument("--note", default="")
    p_exec_plan_create.add_argument("--force", action="store_true")
    p_exec_plan_create.set_defaults(func=cmd_exec_plan_create)

    p_exec_plan_list = p_exec_plan_sub.add_parser("list")
    _add_account_arg(p_exec_plan_list)
    p_exec_plan_list.add_argument("--status")
    p_exec_plan_list.add_argument("--market")
    p_exec_plan_list.add_argument("--code")
    p_exec_plan_list.add_argument("--limit", type=int, default=EXEC_PLAN_LIST_DEFAULT_LIMIT)
    p_exec_plan_list.set_defaults(func=cmd_exec_plan_list)

    p_exec_plan_show = p_exec_plan_sub.add_parser("show")
    _add_account_arg(p_exec_plan_show)
    p_exec_plan_show.add_argument("--id", type=int, required=True)
    p_exec_plan_show.set_defaults(func=cmd_exec_plan_show)

    p_exec_plan_cancel = p_exec_plan_sub.add_parser("cancel")
    _add_account_arg(p_exec_plan_cancel)
    p_exec_plan_cancel.add_argument("--id", type=int, required=True)
    p_exec_plan_cancel.add_argument("--reason", default="")
    p_exec_plan_cancel.add_argument("--expire", action="store_true", help="标记为 expired 而不是 cancelled")
    p_exec_plan_cancel.set_defaults(func=cmd_exec_plan_cancel)

    p_exec_plan_update = p_exec_plan_sub.add_parser("update")
    _add_account_arg(p_exec_plan_update)
    p_exec_plan_update.add_argument("--id", type=int, required=True)
    p_exec_plan_update.add_argument("--action-type", dest="action_type")
    p_exec_plan_update.add_argument("--thesis")
    p_exec_plan_update.add_argument("--invalidation")
    p_exec_plan_update.add_argument("--target-qty", dest="target_qty")
    p_exec_plan_update.add_argument("--target-cash-cny", dest="target_cash_cny")
    p_exec_plan_update.add_argument("--target-position-pct", dest="target_position_pct")
    p_exec_plan_update.add_argument("--price-limit-low", dest="price_limit_low")
    p_exec_plan_update.add_argument("--price-limit-high", dest="price_limit_high")
    p_exec_plan_update.add_argument("--stop-loss-price", dest="stop_loss_price")
    p_exec_plan_update.add_argument("--take-profit-price", dest="take_profit_price")
    p_exec_plan_update.add_argument("--add-condition", dest="add_condition")
    p_exec_plan_update.add_argument("--reduce-condition", dest="reduce_condition")
    p_exec_plan_update.add_argument("--time-window-start", dest="time_window_start")
    p_exec_plan_update.add_argument("--time-window-end", dest="time_window_end")
    p_exec_plan_update.add_argument("--confidence")
    p_exec_plan_update.add_argument("--risk-level", dest="risk_level")
    p_exec_plan_update.add_argument("--tags")
    p_exec_plan_update.add_argument("--note")
    p_exec_plan_update.set_defaults(func=cmd_exec_plan_update)

    p_exec_attach = p_exec_sub.add_parser("attach")
    _add_account_arg(p_exec_attach)
    p_exec_attach.add_argument("--plan-id", type=int, required=True)
    p_exec_attach.add_argument("--trade-id", type=int, required=True)
    p_exec_attach.add_argument("--role", default="fill")
    p_exec_attach.add_argument("--note", default="")
    p_exec_attach.add_argument("--force", action="store_true")
    p_exec_attach.set_defaults(func=cmd_exec_attach)

    p_exec_review = p_exec_sub.add_parser("review")
    p_exec_review_sub = p_exec_review.add_subparsers(dest="action", required=True)
    p_exec_review_add = p_exec_review_sub.add_parser("add")
    _add_account_arg(p_exec_review_add)
    p_exec_review_add.add_argument("--plan-id", type=int)
    p_exec_review_add.add_argument("--trade-id", type=int)
    p_exec_review_add.add_argument("--time")
    p_exec_review_add.add_argument("--horizon", default="manual")
    p_exec_review_add.add_argument("--outcome", required=True)
    p_exec_review_add.add_argument("--discipline-score", type=int)
    p_exec_review_add.add_argument("--execution-score", type=int)
    p_exec_review_add.add_argument("--thesis-score", type=int)
    p_exec_review_add.add_argument("--plan-followed")
    p_exec_review_add.add_argument("--mistake-tags")
    p_exec_review_add.add_argument("--good-tags")
    p_exec_review_add.add_argument("--pnl-snapshot-cny")
    p_exec_review_add.add_argument("--max-favorable-excursion-pct")
    p_exec_review_add.add_argument("--max-adverse-excursion-pct")
    p_exec_review_add.add_argument("--lesson", required=True)
    p_exec_review_add.add_argument("--next-rule")
    p_exec_review_add.add_argument("--note", default="")
    p_exec_review_add.set_defaults(func=cmd_exec_review_add)

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

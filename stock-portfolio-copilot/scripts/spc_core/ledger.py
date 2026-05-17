from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal

from spc_core.settings import ensure_defaults
from spc_core.utils import (
    decimal_str,
    default_currency,
    normalize_code,
    normalize_market,
    parse_user_time,
    q_money,
    q_pct,
    q_price,
    q_qty,
    to_decimal,
    utc_now_iso,
)


# ── 执行计划 / 复盘相关的状态机常量 ───────────────────────────────
# 集中在这里维护，避免散落 string literal 引发拼写不一致。

PLAN_STATUS_PLANNED = "planned"
PLAN_STATUS_PARTIAL = "partially_filled"
PLAN_STATUS_FILLED = "filled"
PLAN_STATUS_CANCELLED = "cancelled"
PLAN_STATUS_EXPIRED = "expired"

# 全部允许出现在 execution_plan.status 字段里的值。
PLAN_STATUSES_ALL: frozenset[str] = frozenset({
    PLAN_STATUS_PLANNED,
    PLAN_STATUS_PARTIAL,
    PLAN_STATUS_FILLED,
    PLAN_STATUS_CANCELLED,
    PLAN_STATUS_EXPIRED,
})

# 创建时允许显式指定的初始状态白名单。
# 注意：`partially_filled` / `filled` 必须由 attach 状态机推导出来，
# 用户不能凭空把一个新 plan 直接写成"已完成"，否则审计无法解释。
PLAN_STATUSES_INITIAL_ALLOWED: frozenset[str] = frozenset({
    PLAN_STATUS_PLANNED,
    PLAN_STATUS_CANCELLED,
    PLAN_STATUS_EXPIRED,
})

# 终态：不再接受 attach / 不再被状态机自动刷新。
# review 不再视为终态——review 是 plan 的"事后笔记"，不影响 execution 生命周期。
PLAN_STATUSES_TERMINAL: frozenset[str] = frozenset({
    PLAN_STATUS_CANCELLED,
    PLAN_STATUS_EXPIRED,
})

EXEC_PLAN_LIST_DEFAULT_LIMIT = 50

OUTCOME_VALUES: frozenset[str] = frozenset({
    "win", "loss", "breakeven", "stopped", "pending",
})

HORIZON_VALUES: frozenset[str] = frozenset({
    "intraday", "next_day", "five_day", "twenty_day", "manual",
})

SOURCE_TYPE_VALUES: frozenset[str] = frozenset({
    "manual", "analysis_run", "external", "replan",
})

ROLE_VALUES: frozenset[str] = frozenset({
    "fill", "partial_fill", "stop_loss", "take_profit", "manual",
})


@contextmanager
def _txn(conn):
    """让"组合写入"这种事务有原子性 —— 任意一步抛错就 rollback。

    使用 SAVEPOINT 而非顶层 BEGIN / COMMIT，可以嵌套也不会和 sqlite3 的隐式事务打架。
    """
    sp = "_spc_txn"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        yield
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        conn.commit()


def add_position_seed(
    conn,
    account_id: int,
    market: str,
    code: str,
    qty: str,
    cost: str,
    currency: str | None,
    time_text: str | None,
    note: str,
    *,
    force: bool = False,
) -> None:
    """写入或覆盖 position_seed。

    护栏（防止 agent / 用户错把买卖当成 init）:

    1. **已有 trade_ledger 记录时**：硬拒绝（force 都救不了）。
       买入/卖出请用 ``trade add``。如确实要清零重置，先 ``trade delete --id <id>``
       清空所有 trade 再 init。这条护栏防的是「sell 误用 position init 覆盖 qty」
       这种导致已实现盈亏归零、交易历史丢失的破坏性场景。

    2. **已有 seed 但无 trade**：默认拒绝，``--force`` 允许覆盖（UPDATE）。
       典型场景：残股摊薄成本调整、手动改正录入错误。

    3. **首次 init**：直接 INSERT。

    Raises:
        ValueError: 触发护栏 1 / 2 时抛出，带可执行的修复提示。
    """
    ensure_defaults(conn)
    norm_market = normalize_market(market)
    norm_code = normalize_code(norm_market, code)

    # 护栏 1：检查 trade_ledger（含 buy + sell）
    trade_count = conn.execute(
        "SELECT COUNT(*) FROM trade_ledger "
        "WHERE account_id = ? AND market = ? AND code = ? AND is_deleted = 0",
        (account_id, norm_market, norm_code),
    ).fetchone()[0]
    if trade_count > 0:
        raise ValueError(
            f"{norm_market.upper()} {norm_code} 已有 {trade_count} 条 trade 记录，"
            f"禁止用 position init（会破坏盈亏统计 / 交易历史）。\n"
            f"  - 买入/卖出请用：spc trade add --account ... --side buy/sell ...\n"
            f"  - 若真的要重置 seed：先 'spc trade list --account ... --market ... --code ...' "
            f"找到对应 trade，逐条 'spc trade delete --id <id>'，再 init --force"
        )

    # 护栏 2：检查已有 seed
    existing = conn.execute(
        "SELECT id, qty, cost_price FROM position_seed "
        "WHERE account_id = ? AND market = ? AND code = ?",
        (account_id, norm_market, norm_code),
    ).fetchone()
    if existing and not force:
        raise ValueError(
            f"{norm_market.upper()} {norm_code} 已有 seed（qty={existing['qty']}, "
            f"cost={existing['cost_price']}）。\n"
            f"  - 如果是「买入/卖出」操作：请用 spc trade add\n"
            f"  - 如果是「残股摊薄成本调整」等罕见场景：加 --force 才能覆盖\n"
            f"  - 不要把 init --force 当成日常成交记账工具"
        )

    qty_d = q_qty(to_decimal(qty, "qty"))
    cost_d = q_price(to_decimal(cost, "cost"))
    curr = (currency or default_currency(norm_market)).upper()
    now = utc_now_iso()

    if existing:
        # force 覆盖：UPDATE 而不是 DELETE+INSERT，避免 id 跳号
        conn.execute(
            """
            UPDATE position_seed
               SET qty = ?, cost_price = ?, currency = ?, seed_time = ?, note = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                decimal_str(qty_d),
                decimal_str(cost_d),
                curr,
                parse_user_time(time_text),
                note or "",
                now,
                existing["id"],
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO position_seed(account_id, market, code, qty, cost_price, currency, seed_time, note, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                norm_market,
                norm_code,
                decimal_str(qty_d),
                decimal_str(cost_d),
                curr,
                parse_user_time(time_text),
                note or "",
                now,
                now,
            ),
        )
    conn.commit()


def list_position_seed(conn, account_id: int, market: str | None = None) -> list[dict]:
    ensure_defaults(conn)
    params = [account_id]
    sql = "SELECT market, code, qty, cost_price, currency, seed_time, note FROM position_seed WHERE account_id = ?"
    if market:
        sql += " AND market = ?"
        params.append(normalize_market(market))
    sql += " ORDER BY market, code"
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def add_trade(
    conn,
    account_id: int,
    market: str,
    code: str,
    side: str,
    qty: str,
    price: str,
    time_text: str,
    currency: str | None,
    fx_rate: str | None,
    fee_commission: str | None,
    fee_platform: str | None,
    fee_transfer: str | None,
    tax_stamp: str | None,
    note: str,
    plan_id: int | None = None,
) -> int:
    ensure_defaults(conn)
    norm_market = normalize_market(market)
    norm_code = normalize_code(norm_market, code)
    side_norm = side.strip().lower()
    if side_norm not in {"buy", "sell"}:
        raise ValueError("side 只能是 buy 或 sell")
    qty_d = q_qty(to_decimal(qty, "qty"))
    price_d = q_price(to_decimal(price, "price"))
    fx_value = None if fx_rate in (None, "") else decimal_str(to_decimal(fx_rate, "fx-rate"))
    curr = (currency or default_currency(norm_market)).upper()

    # 提前校验 plan / trade 是否匹配——如果不匹配就在 INSERT 之前抛错，
    # 让前置 fail-fast 兜底。但即便提前校验通过，下面把
    # INSERT trade + INSERT link + UPDATE plan 仍然必须放在同一个事务里：
    # 任意一步出错（比如 UNIQUE 冲突）都不能让 trade_ledger 留下幽灵记录。
    plan_row: dict | None = None
    if plan_id is not None:
        plan_row = _require_execution_plan(conn, account_id, plan_id)
        _validate_plan_trade_link(
            plan_row,
            trade_market=norm_market,
            trade_code=norm_code,
            trade_side=side_norm,
        )
        if plan_row["status"] in PLAN_STATUSES_TERMINAL:
            raise ValueError(
                f"执行计划 id={plan_id} 已是终态（{plan_row['status']}），"
                f"不能再 attach 成交。请新建一条 plan 或重新激活后再录入。"
            )

    now = utc_now_iso()
    with _txn(conn):
        cur = conn.execute(
            """
            INSERT INTO trade_ledger(
              account_id, market, code, side, qty, price, currency, trade_time,
              fee_commission, fee_platform, fee_transfer, tax_stamp,
              fx_rate, note, is_deleted, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                account_id,
                norm_market,
                norm_code,
                side_norm,
                decimal_str(qty_d),
                decimal_str(price_d),
                curr,
                parse_user_time(time_text),
                decimal_str(q_money(to_decimal(fee_commission or "0", "fee-commission"))),
                decimal_str(q_money(to_decimal(fee_platform or "0", "fee-platform"))),
                decimal_str(q_money(to_decimal(fee_transfer or "0", "fee-transfer"))),
                decimal_str(q_money(to_decimal(tax_stamp or "0", "tax-stamp"))),
                fx_value,
                note or "",
                now,
                now,
            ),
        )
        trade_id = int(cur.lastrowid)
        if plan_id is not None:
            _attach_trade_to_plan_unsafe(
                conn, account_id, plan_id, trade_id, "fill", "",
            )
    return trade_id


def list_trades(conn, account_id: int, market: str | None = None, code: str | None = None, include_deleted: bool = False) -> list[dict]:
    ensure_defaults(conn)
    clauses = ["account_id = ?"]
    params = [account_id]
    if market:
        norm_market = normalize_market(market)
        clauses.append("market = ?")
        params.append(norm_market)
        if code:
            clauses.append("code = ?")
            params.append(normalize_code(norm_market, code))
    elif code:
        raise ValueError("只传 code 时必须同时传 market")
    if not include_deleted:
        clauses.append("is_deleted = 0")
    sql = """
    SELECT id, account_id, market, code, side, qty, price, currency, trade_time, fee_commission,
           fee_platform, fee_transfer, tax_stamp, fx_rate, note, is_deleted
      FROM trade_ledger
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY trade_time, id"
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def delete_trade(conn, account_id: int, trade_id: int) -> None:
    """软删除一笔成交，并把所有关联的执行计划状态刷一次。

    如果删掉这笔成交后，某个 plan 的累计 filled_qty 降到 0，则状态会从
    `filled` / `partially_filled` 回退到 `planned`；累计仍未填满 target 但 > 0
    时回退到 `partially_filled`。这避免了"删除一笔成交但 plan 仍显示 filled"
    的状态机不一致。
    """
    # 找出所有受影响的 plan_id，准备删完之后逐个 refresh。
    affected_plan_ids = [
        int(row[0])
        for row in conn.execute(
            "SELECT DISTINCT plan_id FROM trade_execution_link "
            " WHERE account_id = ? AND trade_id = ?",
            (account_id, trade_id),
        ).fetchall()
    ]

    now = utc_now_iso()
    with _txn(conn):
        cur = conn.execute(
            "UPDATE trade_ledger SET is_deleted = 1, updated_at = ? "
            " WHERE id = ? AND account_id = ? AND is_deleted = 0",
            (now, trade_id, account_id),
        )
        if cur.rowcount == 0:
            raise ValueError(
                f"找不到可删除的 trade id={trade_id}（可能不属于该账户或已被删除）"
            )
        for pid in affected_plan_ids:
            _refresh_execution_plan_status(conn, account_id, pid)


def add_watch(conn, account_id: int, market: str, code: str, note: str) -> None:
    ensure_defaults(conn)
    norm_market = normalize_market(market)
    norm_code = normalize_code(norm_market, code)
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO watchlist(account_id, market, code, note, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, market, code) DO UPDATE SET note = excluded.note, updated_at = excluded.updated_at
        """,
        (account_id, norm_market, norm_code, note or "", now, now),
    )
    conn.commit()


def delete_watch(conn, account_id: int, market: str, code: str) -> None:
    norm_market = normalize_market(market)
    norm_code = normalize_code(norm_market, code)
    cur = conn.execute(
        "DELETE FROM watchlist WHERE account_id = ? AND market = ? AND code = ?",
        (account_id, norm_market, norm_code),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise ValueError(f"自选股不存在: {norm_market} {norm_code}")


def list_watch(conn, account_id: int) -> list[dict]:
    ensure_defaults(conn)
    rows = conn.execute(
        "SELECT market, code, note, created_at FROM watchlist WHERE account_id = ? ORDER BY market, code",
        (account_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _normalize_optional_decimal(value: str | None, field: str, quantize_fn) -> str | None:
    if value in (None, ""):
        return None
    return decimal_str(quantize_fn(to_decimal(value, field)))


def _normalize_optional_text(value: str | None) -> str:
    return (value or "").strip()


def _require_execution_plan(conn, account_id: int, plan_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM execution_plan WHERE id = ? AND account_id = ?",
        (plan_id, account_id),
    ).fetchone()
    if not row:
        raise ValueError(f"找不到执行计划 id={plan_id}（可能不属于该账户）")
    return dict(row)


def _get_trade_row(conn, account_id: int, trade_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM trade_ledger WHERE id = ? AND account_id = ?",
        (trade_id, account_id),
    ).fetchone()
    if not row:
        raise ValueError(f"找不到成交记录 id={trade_id}（可能不属于该账户）")
    out = dict(row)
    if out.get("is_deleted"):
        raise ValueError(f"成交记录 id={trade_id} 已被删除，不能再关联执行计划")
    return out


def _validate_plan_trade_link(
    plan: dict,
    *,
    trade_market: str,
    trade_code: str,
    trade_side: str,
    force: bool = False,
) -> None:
    mismatches: list[str] = []
    if plan["market"] != trade_market:
        mismatches.append(f"market 不一致（plan={plan['market']} trade={trade_market}）")
    if plan["code"] != trade_code:
        mismatches.append(f"code 不一致（plan={plan['code']} trade={trade_code}）")
    if plan["side"] != trade_side:
        mismatches.append(f"side 不一致（plan={plan['side']} trade={trade_side}）")
    if mismatches and not force:
        raise ValueError("执行计划与成交不匹配：" + "；".join(mismatches))


def _refresh_execution_plan_status(conn, account_id: int, plan_id: int) -> None:
    """根据 trade_execution_link + trade_ledger 当前的真实状况，重算 plan.status。

    支持双向流转（关键变化）：
      - 累计 filled_qty == 0  → ``planned``（trade 全删了也能回退）
      - 0 < filled_qty < target → ``partially_filled``
      - filled_qty >= target  → ``filled``

    终态（cancelled / expired）不会被自动改写——只有显式 cancel / expire 的命令能写。
    """
    plan = _require_execution_plan(conn, account_id, plan_id)
    if plan["status"] in PLAN_STATUSES_TERMINAL:
        return

    summary = execution_plan_fill_summary(conn, account_id, plan_id)
    filled_qty = to_decimal(summary["filled_qty"], "filled-qty")
    target_qty_str = summary["target_qty"]

    if filled_qty <= 0:
        new_status = PLAN_STATUS_PLANNED
    elif target_qty_str is not None and filled_qty >= to_decimal(target_qty_str, "target-qty"):
        new_status = PLAN_STATUS_FILLED
    else:
        new_status = PLAN_STATUS_PARTIAL

    if new_status == plan["status"]:
        return  # 没变化，避免无意义的 updated_at 漂移

    conn.execute(
        "UPDATE execution_plan SET status = ?, updated_at = ? "
        " WHERE id = ? AND account_id = ?",
        (new_status, utc_now_iso(), plan_id, account_id),
    )


def create_execution_plan(
    conn,
    account_id: int,
    market: str,
    code: str,
    side: str,
    action_type: str,
    thesis: str,
    *,
    status: str = "planned",
    source_type: str | None = None,
    source_ref_id: int | None = None,
    source_action: str | None = None,
    invalidation: str | None = None,
    target_qty: str | None = None,
    target_cash_cny: str | None = None,
    target_position_pct: str | None = None,
    price_limit_low: str | None = None,
    price_limit_high: str | None = None,
    stop_loss_price: str | None = None,
    take_profit_price: str | None = None,
    add_condition: str | None = None,
    reduce_condition: str | None = None,
    time_window_start: str | None = None,
    time_window_end: str | None = None,
    confidence: str | None = None,
    risk_level: str | None = None,
    tags: str | None = None,
    note: str | None = None,
    force: bool = False,
) -> int:
    ensure_defaults(conn)
    norm_market = normalize_market(market)
    norm_code = normalize_code(norm_market, code)
    side_norm = side.strip().lower()
    if side_norm not in {"buy", "sell"}:
        raise ValueError("side 只能是 buy 或 sell")
    action = action_type.strip().lower()
    if not action:
        raise ValueError("action-type 不能为空")
    thesis_text = thesis.strip()
    if not thesis_text:
        raise ValueError("thesis 不能为空")
    if not any(v not in (None, "") for v in (target_qty, target_cash_cny, target_position_pct)):
        raise ValueError("target_qty / target_cash_cny / target_position_pct 至少要传一个")

    status_norm = (status or PLAN_STATUS_PLANNED).strip().lower()
    if status_norm not in PLAN_STATUSES_INITIAL_ALLOWED:
        raise ValueError(
            f"初始 status 只能是 {sorted(PLAN_STATUSES_INITIAL_ALLOWED)}；"
            f"partially_filled / filled 必须由 attach 状态机推导，不能凭空设置"
        )

    resolved_source_type = (
        source_type or ("analysis_run" if source_ref_id is not None else "manual")
    ).strip().lower()
    if resolved_source_type not in SOURCE_TYPE_VALUES:
        raise ValueError(
            f"source_type 必须是 {sorted(SOURCE_TYPE_VALUES)}，收到: {resolved_source_type}"
        )
    if source_ref_id is not None:
        run = get_analysis_run_by_id(conn, account_id, source_ref_id)
        if not run:
            raise ValueError(f"找不到 analysis_run id={source_ref_id}（可能不属于该账户）")
        payload = run.get("payload") or {}
        results = payload.get("results") or []
        if not any(it.get("market") == norm_market and it.get("code") == norm_code for it in results):
            raise ValueError(
                f"analysis_run id={source_ref_id} 里没有 {norm_market.upper()} {norm_code} 这只标的"
            )
    if source_action and source_action.strip().lower() == "probe" and action != "probe" and not force:
        raise ValueError("source_action=probe 时 action_type 默认也应为 probe；如需覆盖请加 --force")

    now = utc_now_iso()
    with _txn(conn):
        cur = conn.execute(
            """
            INSERT INTO execution_plan(
              account_id, market, code, side, action_type, status, source_type, source_ref_id,
              source_action, thesis, invalidation, target_qty, target_cash_cny, target_position_pct,
              price_limit_low, price_limit_high, stop_loss_price, take_profit_price,
              add_condition, reduce_condition, time_window_start, time_window_end,
              confidence, risk_level, tags, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                norm_market,
                norm_code,
                side_norm,
                action,
                status_norm,
                resolved_source_type,
                source_ref_id,
                _normalize_optional_text(source_action),
                thesis_text,
                _normalize_optional_text(invalidation),
                _normalize_optional_decimal(target_qty, "target-qty", q_qty),
                _normalize_optional_decimal(target_cash_cny, "target-cash-cny", q_money),
                _normalize_optional_decimal(target_position_pct, "target-position-pct", q_pct),
                _normalize_optional_decimal(price_limit_low, "price-limit-low", q_price),
                _normalize_optional_decimal(price_limit_high, "price-limit-high", q_price),
                _normalize_optional_decimal(stop_loss_price, "stop-loss-price", q_price),
                _normalize_optional_decimal(take_profit_price, "take-profit-price", q_price),
                _normalize_optional_text(add_condition),
                _normalize_optional_text(reduce_condition),
                parse_user_time(time_window_start) if time_window_start else None,
                parse_user_time(time_window_end) if time_window_end else None,
                _normalize_optional_decimal(confidence, "confidence", q_pct),
                _normalize_optional_text(risk_level),
                _normalize_optional_text(tags),
                _normalize_optional_text(note),
                now,
                now,
            ),
        )
    return int(cur.lastrowid)


def cancel_execution_plan(
    conn,
    account_id: int,
    plan_id: int,
    *,
    reason: str = "",
    new_status: str = PLAN_STATUS_CANCELLED,
) -> None:
    """显式把 plan 标记为 cancelled / expired。

    只允许从非终态切换到终态，避免误把 cancelled 改回 cancelled 这种无意义动作。
    """
    if new_status not in {PLAN_STATUS_CANCELLED, PLAN_STATUS_EXPIRED}:
        raise ValueError(
            f"cancel/expire 的目标状态只能是 cancelled / expired，收到: {new_status}"
        )
    plan = _require_execution_plan(conn, account_id, plan_id)
    if plan["status"] in PLAN_STATUSES_TERMINAL:
        raise ValueError(
            f"执行计划 id={plan_id} 已经是终态（{plan['status']}），不能再次取消/过期"
        )
    reason_text = (reason or "").strip()
    new_note = plan.get("note") or ""
    if reason_text:
        suffix = f"[{new_status}] {reason_text}"
        new_note = (new_note + "\n" + suffix).strip() if new_note else suffix
    now = utc_now_iso()
    conn.execute(
        "UPDATE execution_plan SET status = ?, note = ?, updated_at = ? "
        " WHERE id = ? AND account_id = ?",
        (new_status, new_note, now, plan_id, account_id),
    )
    conn.commit()


# 哪些字段可以通过 update_execution_plan 改。
# 故意排除 market / code / side / source_ref_id / source_type / created_at：
#   - 前三个改了意味着这是另一条计划，应当新建；
#   - 后两个是审计来源，不允许覆盖。
_UPDATABLE_PLAN_FIELDS: dict[str, str] = {
    "action_type": "text",
    "thesis": "text_required",
    "invalidation": "text",
    "target_qty": "qty",
    "target_cash_cny": "money",
    "target_position_pct": "pct",
    "price_limit_low": "price",
    "price_limit_high": "price",
    "stop_loss_price": "price",
    "take_profit_price": "price",
    "add_condition": "text",
    "reduce_condition": "text",
    "time_window_start": "time",
    "time_window_end": "time",
    "confidence": "pct",
    "risk_level": "text",
    "tags": "text",
    "note": "text",
}


def update_execution_plan(
    conn,
    account_id: int,
    plan_id: int,
    *,
    updates: dict,
) -> dict:
    """更新一条 plan 的可变字段。

    1. 终态（cancelled / expired）禁止编辑；想恢复请新建一条 plan。
    2. ``filled`` / ``partially_filled`` 已经有成交：只允许调"事后参考"字段（止损/止盈/
       条件/标签/备注 等），target / 价格区间 / action_type / thesis 一旦有成交就锁定，
       避免事后篡改 thesis 让审计失真。
    3. ``planned`` 状态下所有 _UPDATABLE_PLAN_FIELDS 都可改。
    """
    plan = _require_execution_plan(conn, account_id, plan_id)
    if plan["status"] in PLAN_STATUSES_TERMINAL:
        raise ValueError(
            f"执行计划 id={plan_id} 已是终态（{plan['status']}），不能编辑；"
            f"如需重新规划请新建 plan。"
        )

    has_fills = plan["status"] in {PLAN_STATUS_PARTIAL, PLAN_STATUS_FILLED}
    locked_after_fill: frozenset[str] = frozenset({
        "action_type", "thesis", "target_qty",
        "target_cash_cny", "target_position_pct",
        "price_limit_low", "price_limit_high",
    })

    set_clauses: list[str] = []
    params: list = []
    for field, raw in updates.items():
        if field not in _UPDATABLE_PLAN_FIELDS:
            raise ValueError(f"不支持更新字段 {field}（或不允许通过 update 修改）")
        if has_fills and field in locked_after_fill:
            raise ValueError(
                f"该 plan 已经有成交，{field} 字段被锁定，不能事后修改；"
                f"如需调整止损/止盈/标签/备注，请只传这些字段。"
            )
        kind = _UPDATABLE_PLAN_FIELDS[field]
        normalized = _normalize_update_value(field, raw, kind)
        set_clauses.append(f"{field} = ?")
        params.append(normalized)

    if not set_clauses:
        raise ValueError("update 至少要传一个字段")

    now = utc_now_iso()
    set_clauses.append("updated_at = ?")
    params.extend([now, plan_id, account_id])
    conn.execute(
        f"UPDATE execution_plan SET {', '.join(set_clauses)} "
        f" WHERE id = ? AND account_id = ?",
        params,
    )
    conn.commit()
    return _require_execution_plan(conn, account_id, plan_id)


def _normalize_update_value(field: str, raw, kind: str):
    """根据字段类型做单值归一化（供 update_execution_plan 使用）。"""
    if raw in (None, ""):
        if kind == "text_required":
            raise ValueError(f"{field} 不能为空")
        return "" if kind == "text" else None
    if kind == "text":
        return str(raw).strip()
    if kind == "text_required":
        text = str(raw).strip()
        if not text:
            raise ValueError(f"{field} 不能为空")
        return text
    if kind == "qty":
        return decimal_str(q_qty(to_decimal(raw, field)))
    if kind == "price":
        return decimal_str(q_price(to_decimal(raw, field)))
    if kind == "money":
        return decimal_str(q_money(to_decimal(raw, field)))
    if kind == "pct":
        return decimal_str(q_pct(to_decimal(raw, field)))
    if kind == "time":
        return parse_user_time(str(raw))
    raise ValueError(f"unknown update kind: {kind}")


def list_execution_plans(
    conn,
    account_id: int,
    *,
    status: str | None = None,
    market: str | None = None,
    code: str | None = None,
    limit: int = 50,
) -> list[dict]:
    clauses = ["account_id = ?"]
    params: list = [account_id]
    if status:
        clauses.append("status = ?")
        params.append(status.strip().lower())
    if market:
        norm_market = normalize_market(market)
        clauses.append("market = ?")
        params.append(norm_market)
        if code:
            clauses.append("code = ?")
            params.append(normalize_code(norm_market, code))
    elif code:
        raise ValueError("只传 code 时必须同时传 market")
    sql = (
        "SELECT * FROM execution_plan "
        f"WHERE {' AND '.join(clauses)} ORDER BY id DESC"
    )
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def execution_plan_fill_summary(conn, account_id: int, plan_id: int) -> dict:
    plan = _require_execution_plan(conn, account_id, plan_id)
    rows = conn.execute(
        """
        SELECT t.id, t.qty, t.price
          FROM trade_execution_link l
          JOIN trade_ledger t
            ON t.id = l.trade_id
         WHERE l.account_id = ? AND l.plan_id = ? AND t.is_deleted = 0
         ORDER BY t.trade_time, t.id
        """,
        (account_id, plan_id),
    ).fetchall()

    filled_qty = Decimal("0")
    filled_amount = Decimal("0")
    for row in rows:
        qty_d = to_decimal(row["qty"], "qty")
        price_d = to_decimal(row["price"], "price")
        filled_qty += qty_d
        filled_amount += qty_d * price_d
    avg_price = None
    if filled_qty > 0:
        avg_price = decimal_str(q_price(filled_amount / filled_qty))

    completion_pct = None
    if plan["target_qty"]:
        target_qty = to_decimal(plan["target_qty"], "target-qty")
        if target_qty > 0:
            completion_pct = decimal_str(q_price((filled_qty / target_qty) * Decimal("100")))

    return {
        "trade_count": len(rows),
        "filled_qty": decimal_str(q_qty(filled_qty)),
        "avg_price": avg_price,
        "target_qty": plan["target_qty"],
        "completion_pct": completion_pct,
    }


def get_execution_plan_detail(conn, account_id: int, plan_id: int) -> dict:
    plan = _require_execution_plan(conn, account_id, plan_id)
    trades = conn.execute(
        """
        SELECT t.id, t.market, t.code, t.side, t.qty, t.price, t.currency, t.trade_time,
               t.note, l.role, l.created_at AS linked_at
          FROM trade_execution_link l
          JOIN trade_ledger t
            ON t.id = l.trade_id
         WHERE l.account_id = ? AND l.plan_id = ?
         ORDER BY t.trade_time, t.id
        """,
        (account_id, plan_id),
    ).fetchall()
    reviews = conn.execute(
        """
        SELECT id, trade_id, review_time, horizon, outcome, discipline_score,
               execution_score, thesis_score, plan_followed, lesson, next_rule,
               mistake_tags, good_tags, note
          FROM execution_review
         WHERE account_id = ? AND plan_id = ?
         ORDER BY review_time DESC, id DESC
        """,
        (account_id, plan_id),
    ).fetchall()
    out = dict(plan)
    out["trades"] = [dict(row) for row in trades]
    out["reviews"] = [dict(row) for row in reviews]
    out["fill_summary"] = execution_plan_fill_summary(conn, account_id, plan_id)
    if plan.get("source_ref_id"):
        source_run = get_analysis_run_by_id(conn, account_id, int(plan["source_ref_id"]))
        if source_run:
            out["source_run"] = {
                "id": source_run["id"],
                "run_time": source_run["run_time"],
                "scope": source_run["scope"],
            }
    return out


def _attach_trade_to_plan_unsafe(
    conn,
    account_id: int,
    plan_id: int,
    trade_id: int,
    role: str,
    note: str,
    *,
    force: bool = False,
) -> None:
    plan = _require_execution_plan(conn, account_id, plan_id)
    if plan["status"] in PLAN_STATUSES_TERMINAL:
        raise ValueError(
            f"执行计划 id={plan_id} 已是终态（{plan['status']}），不能再关联成交"
        )
    trade = _get_trade_row(conn, account_id, trade_id)
    _validate_plan_trade_link(
        plan,
        trade_market=trade["market"],
        trade_code=trade["code"],
        trade_side=trade["side"],
        force=force,
    )
    role_norm = (role or "fill").strip().lower()
    if role_norm not in ROLE_VALUES:
        raise ValueError(f"role 必须是 {sorted(ROLE_VALUES)}，收到: {role_norm}")
    existing = conn.execute(
        "SELECT id FROM trade_execution_link "
        " WHERE account_id = ? AND plan_id = ? AND trade_id = ?",
        (account_id, plan_id, trade_id),
    ).fetchone()
    if existing:
        raise ValueError(f"trade id={trade_id} 已经关联到 plan id={plan_id}")
    try:
        conn.execute(
            """
            INSERT INTO trade_execution_link(account_id, plan_id, trade_id, role, note, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (account_id, plan_id, trade_id, role_norm, note or "", utc_now_iso()),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"trade id={trade_id} 已经关联到 plan id={plan_id}") from exc
    _refresh_execution_plan_status(conn, account_id, plan_id)


def _attach_trade_to_plan(
    conn,
    account_id: int,
    plan_id: int,
    trade_id: int,
    role: str,
    note: str,
    *,
    force: bool = False,
    commit: bool = True,
) -> None:
    if commit:
        with _txn(conn):
            _attach_trade_to_plan_unsafe(
                conn, account_id, plan_id, trade_id, role, note, force=force,
            )
        return
    _attach_trade_to_plan_unsafe(
        conn, account_id, plan_id, trade_id, role, note, force=force,
    )


def attach_trade_to_plan(
    conn,
    account_id: int,
    plan_id: int,
    trade_id: int,
    *,
    role: str = "fill",
    note: str = "",
    force: bool = False,
) -> None:
    _attach_trade_to_plan(conn, account_id, plan_id, trade_id, role, note, force=force, commit=True)


def add_execution_review(
    conn,
    account_id: int,
    *,
    plan_id: int | None = None,
    trade_id: int | None = None,
    review_time: str | None = None,
    horizon: str = "manual",
    outcome: str,
    discipline_score: int | None = None,
    execution_score: int | None = None,
    thesis_score: int | None = None,
    plan_followed: bool | None = None,
    mistake_tags: str | None = None,
    good_tags: str | None = None,
    pnl_snapshot_cny: str | None = None,
    max_favorable_excursion_pct: str | None = None,
    max_adverse_excursion_pct: str | None = None,
    lesson: str = "",
    next_rule: str | None = None,
    note: str | None = None,
) -> int:
    if plan_id is None and trade_id is None:
        raise ValueError("plan_id 和 trade_id 至少要传一个")
    if plan_id is not None:
        _require_execution_plan(conn, account_id, plan_id)
    if trade_id is not None:
        _get_trade_row(conn, account_id, trade_id)
    lesson_text = lesson.strip()
    if not lesson_text:
        raise ValueError("lesson 不能为空")
    horizon_norm = (horizon or "manual").strip().lower()
    if horizon_norm not in HORIZON_VALUES:
        raise ValueError(f"horizon 必须是 {sorted(HORIZON_VALUES)}，收到: {horizon_norm}")
    outcome_norm = outcome.strip().lower()
    if outcome_norm not in OUTCOME_VALUES:
        raise ValueError(f"outcome 必须是 {sorted(OUTCOME_VALUES)}，收到: {outcome_norm}")
    now = utc_now_iso()
    with _txn(conn):
        cur = conn.execute(
            """
            INSERT INTO execution_review(
              account_id, plan_id, trade_id, review_time, horizon, outcome,
              discipline_score, execution_score, thesis_score, plan_followed,
              mistake_tags, good_tags, pnl_snapshot_cny,
              max_favorable_excursion_pct, max_adverse_excursion_pct,
              lesson, next_rule, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                plan_id,
                trade_id,
                parse_user_time(review_time),
                horizon_norm,
                outcome_norm,
                discipline_score,
                execution_score,
                thesis_score,
                None if plan_followed is None else int(plan_followed),
                _normalize_optional_text(mistake_tags),
                _normalize_optional_text(good_tags),
                _normalize_optional_decimal(pnl_snapshot_cny, "pnl-snapshot-cny", q_money),
                _normalize_optional_decimal(max_favorable_excursion_pct, "max-favorable-excursion-pct", q_pct),
                _normalize_optional_decimal(max_adverse_excursion_pct, "max-adverse-excursion-pct", q_pct),
                lesson_text,
                _normalize_optional_text(next_rule),
                _normalize_optional_text(note),
                now,
                now,
            ),
        )
    return int(cur.lastrowid)


def save_analysis_run(conn, account_id: int, scope: str, market: str | None, code: str | None, payload: dict) -> int:
    now = utc_now_iso()
    cur = conn.execute(
        """
        INSERT INTO analysis_run(account_id, scope, market, code, run_time, payload_json)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (account_id, scope, market, code, now, json.dumps(payload, ensure_ascii=False, default=str)),
    )
    conn.commit()
    return int(cur.lastrowid)


def latest_analysis_run(conn, account_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, scope, market, code, run_time, payload_json FROM analysis_run WHERE account_id = ? ORDER BY id DESC LIMIT 1",
        (account_id,),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["payload"] = json.loads(out.pop("payload_json"))
    return out


def list_analysis_runs(
    conn,
    account_id: int,
    market: str | None = None,
    code: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """列出 analyze_now 历史记录（按 run_time 倒序）。

    返回**不带 payload_json**的轻量列表（仅 id / scope / market / code / run_time），
    用于 ``spc log`` 这种概览。要拿完整负载请用 ``get_analysis_run_by_id``。

    Args:
        market / code: 可选过滤。注意 ``analysis_run.market`` 是该次 ``analyze_now``
            的 ``--market`` 参数（可能为 None），不是结果里某只标的的 market。
            按 symbol 过滤实际要靠 payload 里的 results 数组——这里只过滤外层。
        since / until: ISO 8601 字符串（如 '2026-05-06' 或 '2026-05-06T10:00:00+00:00'），
            包含 since、不含 until。
        limit: 上限，0 表示不限制。
    """
    clauses = ["account_id = ?"]
    params: list = [account_id]
    if market:
        norm_market = normalize_market(market)
        clauses.append("market = ?")
        params.append(norm_market)
        if code:
            clauses.append("code = ?")
            params.append(normalize_code(norm_market, code))
    elif code:
        raise ValueError("只传 code 时必须同时传 market")
    if since:
        clauses.append("run_time >= ?")
        params.append(since)
    if until:
        clauses.append("run_time < ?")
        params.append(until)
    sql = (
        "SELECT id, scope, market, code, run_time FROM analysis_run "
        f"WHERE {' AND '.join(clauses)} ORDER BY id DESC"
    )
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_analysis_run_by_id(conn, account_id: int, run_id: int) -> dict | None:
    """按 id 拉单次 analyze_now 的完整负载（含 payload）。"""
    row = conn.execute(
        "SELECT id, scope, market, code, run_time, payload_json "
        "FROM analysis_run WHERE id = ? AND account_id = ?",
        (run_id, account_id),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["payload"] = json.loads(out.pop("payload_json"))
    return out


def find_analysis_runs_covering_symbol(
    conn,
    account_id: int,
    market: str,
    code: str,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """找到所有"results 数组里包含指定 symbol"的 analyze_now 记录。

    ``analyze_now`` 既可能传 ``--market/--code`` 锁定单只（外层 market/code 字段就是它），
    也可能 ``--scope holdings`` 不传外层 market/code，让 results 内含多只标的。
    单只标的的复盘场景要查"曾经分析过 SZ300750 的所有 run"，必须同时扫这两种情况。

    实现：先按外层 market/code 精确匹配召回；再扫"外层无过滤"的 run，
    检查 payload_json LIKE 匹配（廉价的 substring 检查，结果再精确反序列化验证）。
    """
    norm_market = normalize_market(market)
    norm_code = normalize_code(norm_market, code)
    clauses = ["account_id = ?"]
    params: list = [account_id]
    if since:
        clauses.append("run_time >= ?")
        params.append(since)
    if until:
        clauses.append("run_time < ?")
        params.append(until)
    where = " AND ".join(clauses)
    # 精确召回 + 粗 LIKE 召回，最后在 Python 侧验证
    needle = f'"market": "{norm_market}", "code": "{norm_code}"'
    sql = (
        "SELECT id, scope, market, code, run_time, payload_json FROM analysis_run "
        f"WHERE {where} AND "
        "  ((market = ? AND code = ?) OR payload_json LIKE ?) "
        "ORDER BY id DESC"
    )
    if limit and limit > 0:
        sql += f" LIMIT {int(limit * 2)}"  # LIKE 召回多一倍，过滤后裁剪
    rows = conn.execute(
        sql, params + [norm_market, norm_code, f"%{needle}%"]
    ).fetchall()

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            payload = json.loads(d.pop("payload_json"))
        except Exception:
            continue
        # 精确验证 results 里确实有这个 symbol（避免 LIKE 误命中）
        if (d.get("market") == norm_market and d.get("code") == norm_code):
            d["payload"] = payload
            out.append(d)
            continue
        results = payload.get("results") or []
        if any(it.get("market") == norm_market and it.get("code") == norm_code for it in results):
            d["payload"] = payload
            out.append(d)
        if limit and len(out) >= limit:
            break
    return out


def latest_snapshots(conn, account_id: int, market: str | None = None) -> list[dict]:
    params = [account_id]
    filter_sql = ""
    if market:
        filter_sql = " AND s.market = ?"
        params.append(normalize_market(market))
    rows = conn.execute(
        f"""
        SELECT s.*
          FROM portfolio_snapshot s
          JOIN (
            SELECT account_id, market, code, MAX(id) AS max_id
              FROM portfolio_snapshot
             WHERE account_id = ?
             GROUP BY account_id, market, code
          ) latest ON latest.max_id = s.id
         WHERE 1=1
        {filter_sql}
         ORDER BY s.market, s.code
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def latest_snapshot_for_symbol(conn, account_id: int, market: str, code: str) -> dict | None:
    norm_market = normalize_market(market)
    norm_code = normalize_code(norm_market, code)
    row = conn.execute(
        """
        SELECT *
          FROM portfolio_snapshot
         WHERE account_id = ? AND market = ? AND code = ?
         ORDER BY id DESC
         LIMIT 1
        """,
        (account_id, norm_market, norm_code),
    ).fetchone()
    return dict(row) if row else None

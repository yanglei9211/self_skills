from __future__ import annotations

import json
from decimal import Decimal

from spc_core.settings import ensure_defaults
from spc_core.utils import (
    decimal_str,
    default_currency,
    normalize_code,
    normalize_market,
    parse_user_time,
    q_money,
    q_price,
    q_qty,
    to_decimal,
    utc_now_iso,
)


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


def add_trade(conn, account_id: int, market: str, code: str, side: str, qty: str, price: str, time_text: str, currency: str | None, fx_rate: str | None, fee_commission: str | None, fee_platform: str | None, fee_transfer: str | None, tax_stamp: str | None, note: str) -> int:
    ensure_defaults(conn)
    norm_market = normalize_market(market)
    norm_code = normalize_code(norm_market, code)
    side_norm = side.strip().lower()
    if side_norm not in {"buy", "sell"}:
        raise ValueError("side 只能是 buy 或 sell")
    qty_d = q_qty(to_decimal(qty, "qty"))
    price_d = q_price(to_decimal(price, "price"))
    fx_value = None if fx_rate in (None, "") else decimal_str(to_decimal(fx_rate, "fx-rate"))
    now = utc_now_iso()
    curr = (currency or default_currency(norm_market)).upper()
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
    conn.commit()
    return int(cur.lastrowid)


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
    now = utc_now_iso()
    cur = conn.execute(
        "UPDATE trade_ledger SET is_deleted = 1, updated_at = ? WHERE id = ? AND account_id = ? AND is_deleted = 0",
        (now, trade_id, account_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise ValueError(f"找不到可删除的 trade id={trade_id}（可能不属于该账户或已被删除）")


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

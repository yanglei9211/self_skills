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


def add_position_seed(conn, account_id: int, market: str, code: str, qty: str, cost: str, currency: str | None, time_text: str | None, note: str) -> None:
    ensure_defaults(conn)
    norm_market = normalize_market(market)
    norm_code = normalize_code(norm_market, code)
    qty_d = q_qty(to_decimal(qty, "qty"))
    cost_d = q_price(to_decimal(cost, "cost"))
    curr = (currency or default_currency(norm_market)).upper()
    now = utc_now_iso()
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

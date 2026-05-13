from __future__ import annotations

from decimal import Decimal

from spc_core.ledger import latest_snapshots
from spc_core.market_bridge import FXRateProvider, StockMarketHubProvider
from spc_core.settings import ensure_defaults, get_decimal_setting
from spc_core.utils import (
    decimal_str,
    default_currency,
    normalize_code,
    normalize_market,
    q_money,
    q_price,
    q_qty,
    to_decimal,
    utc_now_iso,
    hk_stamp_round,
)


def _derive_trade_fees(conn, trade: dict) -> tuple[Decimal, Decimal]:
    market = trade["market"]
    side = trade["side"]
    amount = q_money(to_decimal(trade["qty"], "qty") * to_decimal(trade["price"], "price"))
    commission = to_decimal(trade["fee_commission"], "fee_commission")
    platform = to_decimal(trade["fee_platform"], "fee_platform")
    transfer = to_decimal(trade["fee_transfer"], "fee_transfer")
    stamp = to_decimal(trade["tax_stamp"], "tax_stamp")

    if market == "a":
        if commission == 0:
            commission = q_money(amount * get_decimal_setting(conn, "rules.a.share.commission_rate", "0"))
        if side == "sell" and stamp == 0:
            stamp = q_money(amount * get_decimal_setting(conn, "rules.a.share.stamp_tax_sell_rate", "0.0005"))
    elif market == "hk":
        if commission == 0:
            commission = q_money(amount * get_decimal_setting(conn, "rules.hk.commission_rate", "0"))
        if stamp == 0:
            stamp_rate = get_decimal_setting(conn, "rules.hk.stamp_tax_rate", "0.001")
            stamp = hk_stamp_round(amount * stamp_rate)
        if platform == 0:
            trading_fee = amount * get_decimal_setting(conn, "rules.hk.trading_fee_rate", "0.0000565")
            sfc_levy = amount * get_decimal_setting(conn, "rules.hk.sfc_levy_rate", "0.000027")
            afrc_levy = amount * get_decimal_setting(conn, "rules.hk.afrc_levy_rate", "0.0000015")
            platform = q_money(trading_fee + sfc_levy + afrc_levy)
        if transfer == 0:
            transfer = q_money(amount * get_decimal_setting(conn, "rules.hk.settlement_fee_rate", "0"))

    total_fees = q_money(commission + platform + transfer + stamp)
    return total_fees, stamp


def _symbol_universe(conn, account_id: int, market: str | None, code: str | None) -> list[tuple[str, str]]:
    if code and not market:
        raise ValueError("只传 code 时必须同时传 market")
    clauses = ["account_id = ?"]
    params: list = [account_id]
    if market:
        norm_market = normalize_market(market)
        clauses.append("market = ?")
        params.append(norm_market)
        if code:
            clauses.append("code = ?")
            params.append(normalize_code(norm_market, code))
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT market, code FROM (
            SELECT market, code FROM position_seed WHERE {where}
            UNION
            SELECT market, code FROM trade_ledger WHERE is_deleted = 0 AND {where}
        ) ORDER BY market, code
        """,
        params * 2,
    ).fetchall()
    return [(row["market"], row["code"]) for row in rows]


def sync_portfolio(conn, account_id: int, market: str | None = None, code: str | None = None, analysis_provider=None, fx_rate_provider=None) -> list[dict]:
    ensure_defaults(conn)
    provider = analysis_provider or StockMarketHubProvider()
    fx_provider = fx_rate_provider or FXRateProvider()
    symbols = _symbol_universe(conn, account_id, market, code)
    snapshots = []
    for sym_market, sym_code in symbols:
        seed = conn.execute(
            "SELECT * FROM position_seed WHERE account_id = ? AND market = ? AND code = ?",
            (account_id, sym_market, sym_code),
        ).fetchone()
        trades = conn.execute(
            """
            SELECT * FROM trade_ledger
             WHERE account_id = ? AND market = ? AND code = ? AND is_deleted = 0
             ORDER BY trade_time, id
            """,
            (account_id, sym_market, sym_code),
        ).fetchall()
        qty = Decimal(seed["qty"]) if seed else Decimal("0")
        avg_cost = Decimal(seed["cost_price"]) if seed else Decimal("0")
        cost_basis = q_money(qty * avg_cost)
        realized_pnl = Decimal("0")
        total_fees = Decimal("0")
        currency = seed["currency"] if seed else default_currency(sym_market)

        for trade_row in trades:
            trade = dict(trade_row)
            t_qty = to_decimal(trade["qty"], "qty")
            t_price = to_decimal(trade["price"], "price")
            amount = q_money(t_qty * t_price)
            fees, _ = _derive_trade_fees(conn, trade)
            total_fees += fees
            if trade["side"] == "buy":
                cost_basis = q_money(cost_basis + amount + fees)
                qty = q_qty(qty + t_qty)
                avg_cost = Decimal("0") if qty == 0 else q_price(cost_basis / qty)
            else:
                if t_qty > qty:
                    raise ValueError(
                        f"卖出数量 {decimal_str(t_qty)} 大于当前可用持仓 {decimal_str(qty)}: {sym_market} {sym_code}"
                    )
                removed_cost = q_money(avg_cost * t_qty)
                proceeds_net = q_money(amount - fees)
                realized_pnl = q_money(realized_pnl + proceeds_net - removed_cost)
                qty = q_qty(qty - t_qty)
                cost_basis = q_money(cost_basis - removed_cost)
                if qty == 0:
                    avg_cost = Decimal("0")
                    cost_basis = Decimal("0")
                else:
                    avg_cost = q_price(cost_basis / qty)

        quote = {}
        try:
            quote = provider.fetch_quote(sym_market, sym_code) or {}
        except Exception:  # noqa: BLE001
            quote = {}

        last_price = None
        last_price_time = None
        unrealized = None
        fx_rate_to_cny = Decimal("1")
        position_value_cny = None
        if quote.get("current") is not None:
            last_price = q_price(to_decimal(quote["current"], "last_price"))
            last_price_time = quote.get("fetched_at")
            if qty > 0:
                unrealized = q_money((last_price - avg_cost) * qty)
            else:
                unrealized = Decimal("0")
        if sym_market == "hk":
            fx_rate_to_cny = fx_provider.get_rate(conn, currency, "CNY")
        if last_price is not None:
            position_value = q_money(last_price * qty)
            position_value_cny = q_money(position_value * fx_rate_to_cny)

        snapshot = {
            "account_id": account_id,
            "market": sym_market,
            "code": sym_code,
            "qty": decimal_str(qty),
            "avg_cost_price": decimal_str(avg_cost),
            "currency": currency,
            "gross_cost_ccy": decimal_str(q_money(cost_basis)),
            "total_fees_ccy": decimal_str(q_money(total_fees)),
            "realized_pnl_ccy": decimal_str(q_money(realized_pnl)),
            "last_price": decimal_str(last_price) if last_price is not None else None,
            "last_price_time": last_price_time,
            "unrealized_pnl_ccy": decimal_str(q_money(unrealized)) if unrealized is not None else None,
            "fx_rate_to_cny": decimal_str(fx_rate_to_cny),
            "position_value_cny": decimal_str(position_value_cny) if position_value_cny is not None else None,
            "snapshot_time": utc_now_iso(),
            "source": "sync",
        }
        conn.execute(
            """
            INSERT INTO portfolio_snapshot(
              account_id, market, code, qty, avg_cost_price, currency, gross_cost_ccy, total_fees_ccy,
              realized_pnl_ccy, last_price, last_price_time, unrealized_pnl_ccy,
              fx_rate_to_cny, position_value_cny, snapshot_time, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["account_id"],
                snapshot["market"],
                snapshot["code"],
                snapshot["qty"],
                snapshot["avg_cost_price"],
                snapshot["currency"],
                snapshot["gross_cost_ccy"],
                snapshot["total_fees_ccy"],
                snapshot["realized_pnl_ccy"],
                snapshot["last_price"],
                snapshot["last_price_time"],
                snapshot["unrealized_pnl_ccy"],
                snapshot["fx_rate_to_cny"],
                snapshot["position_value_cny"],
                snapshot["snapshot_time"],
                snapshot["source"],
            ),
        )
        snapshots.append(snapshot)
    conn.commit()
    return snapshots


def pnl_summary(conn, account_id: int) -> dict:
    snaps = latest_snapshots(conn, account_id)
    total_value = Decimal("0")
    total_realized = Decimal("0")
    total_unrealized = Decimal("0")
    total_fees = Decimal("0")
    for snap in snaps:
        total_value += to_decimal(snap["position_value_cny"] or "0", "position_value_cny")
        total_realized += to_decimal(snap["realized_pnl_ccy"] or "0", "realized_pnl_ccy")
        total_unrealized += to_decimal(snap["unrealized_pnl_ccy"] or "0", "unrealized_pnl_ccy")
        total_fees += to_decimal(snap["total_fees_ccy"] or "0", "total_fees_ccy")
    return {
        "positions": len(snaps),
        "total_position_value_cny": decimal_str(q_money(total_value)),
        "total_realized_pnl_ccy": decimal_str(q_money(total_realized)),
        "total_unrealized_pnl_ccy": decimal_str(q_money(total_unrealized)),
        "total_fees_ccy": decimal_str(q_money(total_fees)),
    }

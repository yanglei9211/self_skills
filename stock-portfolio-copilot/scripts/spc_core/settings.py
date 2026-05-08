from __future__ import annotations

import json
from decimal import Decimal

from spc_core.utils import decimal_str, to_decimal, utc_now_iso


DEFAULT_SETTINGS = {
    "default.base_currency": "CNY",
    "capital.total_cny": "",
    "capital.max_single_position_pct": "20",
    "capital.max_sector_position_pct": "35",
    "rules.a.share.commission_rate": "0",
    "rules.a.share.stamp_tax_sell_rate": "0.0005",
    "rules.hk.commission_rate": "0",
    "rules.hk.stamp_tax_rate": "0.001",
    "rules.hk.trading_fee_rate": "0.0000565",
    "rules.hk.sfc_levy_rate": "0.000027",
    "rules.hk.afrc_levy_rate": "0.0000015",
    "rules.hk.settlement_fee_rate": "0",
    "fx.hkd_cny": "0.92",
}


def ensure_defaults(conn) -> None:
    now = utc_now_iso()
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (key, value, now),
        )
    conn.commit()


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    return default


def set_setting(conn, key: str, value: str) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO settings(key, value, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, now),
    )
    conn.commit()


def get_decimal_setting(conn, key: str, default: str = "0") -> Decimal:
    raw = get_setting(conn, key, default) or default
    return to_decimal(raw, key)


def capital_settings(conn) -> dict:
    total = get_setting(conn, "capital.total_cny", "") or ""
    max_single = get_setting(conn, "capital.max_single_position_pct", "20") or "20"
    max_sector = get_setting(conn, "capital.max_sector_position_pct", "35") or "35"
    return {
        "total_cny": total,
        "max_single_position_pct": max_single,
        "max_sector_position_pct": max_sector,
    }


def show_settings_json(conn) -> str:
    rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
    data = {row["key"]: row["value"] for row in rows}
    return json.dumps(data, ensure_ascii=False, indent=2)


def set_capital(conn, total: str | None, max_single_pct: str | None) -> None:
    if total is not None:
        total_cny = to_decimal(total, "total")
        set_setting(conn, "capital.total_cny", decimal_str(total_cny))
    if max_single_pct is not None:
        pct = to_decimal(max_single_pct, "max-single-pct")
        set_setting(conn, "capital.max_single_position_pct", decimal_str(pct))

from __future__ import annotations

import json
from decimal import Decimal

from spc_core.utils import decimal_str, to_decimal, utc_now_iso


DEFAULT_SETTINGS = {
    "default.base_currency": "CNY",
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


# ── account resolution ──────────────────────────────────────────────

def resolve_account(conn, slug: str) -> dict:
    row = conn.execute("SELECT * FROM accounts WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise ValueError(f"账户不存在: {slug}")
    return dict(row)


# ── account-level settings ──────────────────────────────────────────

def get_account_setting(conn, account_id: int, key: str, default: str | None = None) -> str | None:
    row = conn.execute(
        "SELECT value FROM account_settings WHERE account_id = ? AND key = ?",
        (account_id, key),
    ).fetchone()
    if row:
        return row["value"]
    return default


def set_account_setting(conn, account_id: int, key: str, value: str) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO account_settings(account_id, key, value, updated_at)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(account_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (account_id, key, value, now),
    )
    conn.commit()


def capital_settings(conn, account_id: int) -> dict:
    total = get_account_setting(conn, account_id, "capital.total_cny") or ""
    if not total:
        raise ValueError(
            f"账户 (id={account_id}) 未设置 capital.total_cny，请先运行 spc capital set --account <slug> --total <金额>"
        )
    max_single = get_account_setting(conn, account_id, "capital.max_single_position_pct") or "20"
    max_sector = get_account_setting(conn, account_id, "capital.max_sector_position_pct") or "35"
    return {
        "total_cny": total,
        "max_single_position_pct": max_single,
        "max_sector_position_pct": max_sector,
    }


def set_capital(conn, account_id: int, total: str | None, max_single_pct: str | None) -> None:
    if total is not None:
        total_cny = to_decimal(total, "total")
        set_account_setting(conn, account_id, "capital.total_cny", decimal_str(total_cny))
    if max_single_pct is not None:
        pct = to_decimal(max_single_pct, "max-single-pct")
        set_account_setting(conn, account_id, "capital.max_single_position_pct", decimal_str(pct))


# ── global settings (rules / fx) ────────────────────────────────────

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


def show_settings_json(conn) -> str:
    rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
    data = {row["key"]: row["value"] for row in rows}
    return json.dumps(data, ensure_ascii=False, indent=2)


# ── deprecated shims (for tests transition) ─────────────────────────

def _capital_settings_global(conn) -> dict:
    """Deprecated: use capital_settings(conn, account_id) instead."""
    total = get_setting(conn, "capital.total_cny", "") or ""
    max_single = get_setting(conn, "capital.max_single_position_pct", "20") or "20"
    max_sector = get_setting(conn, "capital.max_sector_position_pct", "35") or "35"
    return {
        "total_cny": total,
        "max_single_position_pct": max_single,
        "max_sector_position_pct": max_sector,
    }


def _set_capital_global(conn, total: str | None, max_single_pct: str | None) -> None:
    """Deprecated: use set_capital(conn, account_id, ...) instead."""
    if total is not None:
        total_cny = to_decimal(total, "total")
        set_setting(conn, "capital.total_cny", decimal_str(total_cny))
    if max_single_pct is not None:
        pct = to_decimal(max_single_pct, "max-single-pct")
        set_setting(conn, "capital.max_single_position_pct", decimal_str(pct))

"""Eastmoney helpers shared across stock-market-hub modules.

This module centralizes low-churn Eastmoney endpoints so different business
functions can reuse the same cached payloads instead of re-requesting the same
source data independently.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any

from stock_core.cache import cached
from stock_core.http import fetch
from stock_core.tz import CN_TZ


def eastmoney_a_code(code: str) -> str:
    """Convert a 6-digit A-share/BJ code to Eastmoney F10 code format."""
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("4", "8")):
        return f"BJ{code}"
    return f"SZ{code}"


@cached(ttl=24 * 3600, key_prefix="em_core_concept", schema_version=1)
def fetch_a_core_conception_raw(em_code: str) -> dict[str, Any]:
    """Fetch Eastmoney A-share CoreConception raw payload.

    The payload contains both:
      - ``ssbk``: attached boards / industries / styles with ``BOARD_CODE``
      - ``hxtc``: core concepts
    """
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax?code={em_code}"
    r = fetch(url, retries=1, timeout=10)
    return r.json() or {}


def _ttl_for_board_constituents(board_code: str, cached_data: list[str] | None = None) -> float:  # noqa: ARG001
    """Board constituents are low-churn intraday; keep them warm but not realtime."""
    now = datetime.now(CN_TZ)
    if now.weekday() >= 5:
        return 24 * 3600.0
    if time(9, 30) <= now.time() <= time(15, 0):
        return 3600.0
    return 24 * 3600.0


@cached(ttl=_ttl_for_board_constituents, key_prefix="em_board_const", schema_version=1)
def fetch_board_constituents(board_code: str) -> list[str]:
    """Fetch and cache the board constituents head list from Eastmoney.

    We intentionally fetch a reasonably large first page once per board and let
    callers slice locally, so different ``top`` values don't fan out into
    repeated Eastmoney requests for the same board.
    """
    normalized = board_code if board_code.startswith("BK") else f"BK{board_code.zfill(4)}"
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1,
        "pz": 200,
        "po": 1,
        "np": 1,
        "fields": "f12,f14,f3,f6,f20",
        "fs": f"b:{normalized}",
    }
    r = fetch(url, params=params, timeout=10, retries=1)
    data = r.json() or {}
    rows = (data.get("data") or {}).get("diff") or {}
    if isinstance(rows, dict):
        rows = list(rows.values())
    return [row.get("f12") for row in rows if row.get("f12")]

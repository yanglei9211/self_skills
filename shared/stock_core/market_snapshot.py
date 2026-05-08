from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


def _ensure_stock_market_hub_path() -> None:
    current = Path(__file__).resolve()
    repo_root = current.parents[2]
    hub_scripts = repo_root / "stock-market-hub" / "scripts"
    if str(hub_scripts) not in sys.path:
        sys.path.insert(0, str(hub_scripts))


_ensure_stock_market_hub_path()

from core.xueqiu import XueqiuClient  # type: ignore  # noqa: E402

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


CN_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else timezone.utc

BOARDS = {
    "gainers": ("涨幅榜", "percent", "desc"),
    "losers": ("跌幅榜", "percent", "asc"),
    "amount": ("成交额榜", "amount", "desc"),
    "turnover": ("换手率榜", "turnover_rate", "desc"),
    "main_inflow": ("主力净流入榜", "main_net_inflows", "desc"),
    "followers": ("雪球关注度榜", "followers", "desc"),
}


def _fmt_amount(v: float | None) -> float:
    return (v or 0) / 1e8


def enrich(item: dict) -> dict:
    out = dict(item)
    out["amount_yi"] = _fmt_amount(out.get("amount"))
    out["main_yi"] = _fmt_amount(out.get("main_net_inflows"))
    out["market_cap_yi"] = _fmt_amount(out.get("market_capital"))
    return out


def market_board(market: str = "all_a", board: str = "gainers", top: int = 10) -> dict:
    if board not in BOARDS:
        raise ValueError(f"不支持的 board: {board}")
    _label, order_by, order = BOARDS[board]
    cli = XueqiuClient()
    items = cli.screener(market, order_by, order, top).get("list", [])
    return {
        "market": market,
        "board": board,
        "fetched_at": datetime.now(CN_TZ).isoformat(),
        "items": [enrich(item) for item in items],
    }

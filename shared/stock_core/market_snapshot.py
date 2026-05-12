"""共享市场榜单封装：雪球 screener 多榜单统一抽象。

供两个调用方共用：
  - ``stock-market-hub/scripts/xueqiu_market.py``：CLI 直出涨跌榜/资金榜/热度榜
  - ``shared/stock_core/stock_market_hub.fetch_market_board``：封装供 spc 等下游使用

榜单定义（``BOARDS``）原本散落两份，行为完全一致；这里集中维护。
"""
from __future__ import annotations

from datetime import datetime

from stock_core.tz import CN_TZ
from stock_core.xueqiu import XueqiuClient


BOARDS: dict[str, tuple[str, str, str, str]] = {
    "gainers": ("涨幅榜", "percent", "desc", "{percent:+.2f}%"),
    "losers": ("跌幅榜", "percent", "asc", "{percent:+.2f}%"),
    "amount": ("成交额榜", "amount", "desc", "{amount_yi:.1f}亿"),
    "turnover": ("换手率榜", "turnover_rate", "desc", "{turnover_rate:.1f}%"),
    "main_inflow": ("主力净流入榜", "main_net_inflows", "desc", "{main_yi:+.2f}亿"),
    "followers": ("雪球关注度榜", "followers", "desc", "{followers}人关注"),
}


def fmt_amount(v: float | None) -> float:
    """把以"元"为单位的金额转换为以"亿"为单位的数值。"""
    return (v or 0) / 1e8


def enrich(item: dict) -> dict:
    """补 ``amount_yi`` / ``main_yi`` / ``market_cap_yi`` 等便利字段。

    会同时返回新对象（保留原 item 不变）。
    """
    out = dict(item)
    out["amount_yi"] = fmt_amount(out.get("amount"))
    out["main_yi"] = fmt_amount(out.get("main_net_inflows"))
    out["market_cap_yi"] = fmt_amount(out.get("market_capital"))
    return out


def market_board(market: str = "all_a", board: str = "gainers", top: int = 10) -> dict:
    """雪球 screener 单榜单一站式封装：解析 BOARDS -> 调 screener -> enrich。"""
    if board not in BOARDS:
        raise ValueError(f"不支持的 board: {board}")
    _label, order_by, order, _vfmt = BOARDS[board]
    cli = XueqiuClient()
    items = cli.screener(market, order_by, order, top).get("list", [])
    return {
        "market": market,
        "board": board,
        "fetched_at": datetime.now(CN_TZ).isoformat(),
        "items": [enrich(item) for item in items],
    }

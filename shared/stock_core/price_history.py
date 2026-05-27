"""Backward-compatible price-history helpers.

历史上有一些脚本 / prompt 会写：

    from stock_core.price_history import get_price_history

后来价格历史能力收敛到了 ``stock_core.kline`` 和
``stock_core.company_analysis._get_price_history`` 的内部流程里，独立模块名被删掉了。
为了兼容旧调用，这里恢复一个薄封装：负责规范化 symbol、抓 K 线并返回
``summarize_price_history`` 的结构化摘要。
"""
from __future__ import annotations

from stock_core.kline import fetch_daily_kline, summarize_price_history
from stock_core.symbols import normalize_symbol


def get_price_history(
    symbol: str,
    *,
    market: str | None = None,
    count: int = 1500,
    current_price: float | None = None,
) -> dict:
    """获取并汇总历史价格数据。

    参数：
      - ``symbol``: ``SZ300750`` / ``HK01810`` / ``BABA`` / ``300750`` 等
      - ``market``: 可选；缺省时从 ``symbol`` 自动推断
      - ``count``: 拉取的 K 线天数上限
      - ``current_price``: 可选；覆盖摘要里的当前价

    返回值与 ``stock_core.kline.summarize_price_history`` 一致；无数据时返回
    ``{"error": "no_kline_data", ...}``，方便旧调用方做降级。
    """
    inferred_market, code, xq_symbol = normalize_symbol(symbol)
    effective_market = market or inferred_market
    kline = fetch_daily_kline(xq_symbol, effective_market, count=count)
    if not kline:
        return {
            "symbol": symbol,
            "market": effective_market,
            "code": code,
            "error": "no_kline_data",
            "coverage": {"total_days": 0},
        }
    return summarize_price_history(kline, current_price=current_price or kline[-1]["close"])


__all__ = ["get_price_history"]

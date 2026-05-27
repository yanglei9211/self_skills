#!/usr/bin/env python3
"""A 股 1-4 周中短线初筛：趋势延续 + 筑底反转双路径。

默认扫描全 A 中的大中盘/活跃股池，输出两类候选：
  - 趋势延续：强资金 + 均线强度 + 不过热
  - 筑底反转：资金转向 + 低位修复 + RSI 回升
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from math import ceil
from typing import Any

import _path_setup  # noqa: F401,E402
from stock_core.swing_screen import (  # noqa: E402
    Candidate,
    ScreenFeatures,
    build_features,
    render_text,
    screen_candidates,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--horizon",
        choices=["swing"],
        default="swing",
        help="筛选周期；swing 表示 1-4 周（默认）",
    )
    parser.add_argument("--top", type=int, default=15, help="每条路径输出数量（默认 15）")
    parser.add_argument(
        "--pool-size",
        type=int,
        default=600,
        help="初始股池规模上限；市值池和成交额池去重后截断（默认 600）",
    )
    parser.add_argument("--workers", type=int, default=8, help="并发抓取线程数（默认 8）")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--debug", action="store_true", help="遇到单票异常时输出 traceback")
    return parser


def render_json(result: dict[str, list[Candidate]], *, scanned: int) -> str:
    payload = {
        "scanned": scanned,
        "buckets": {
            key: [candidate.to_dict() for candidate in items]
            for key, items in result.items()
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _is_a_share_symbol(symbol: str) -> bool:
    symbol = (symbol or "").upper()
    if not (symbol.startswith("SH") or symbol.startswith("SZ")):
        return False
    digits = "".join(ch for ch in symbol if ch.isdigit())
    return len(digits) == 6 and not digits.startswith(("4", "8"))


def _collect_universe(pool_size: int) -> list[dict[str, Any]]:
    """从雪球 screener 拉市值池 + 成交额池，去重得到扫描股池。"""
    from stock_core.xueqiu import XueqiuClient

    cli = XueqiuClient()
    seen: set[str] = set()
    stocks: list[dict[str, Any]] = []
    page_size = 90
    pages_needed = max(1, ceil(pool_size / page_size))
    # 市值池保证覆盖中大盘核心，成交额池补充盘面活跃票。
    plans = [
        ("market_capital", "desc", pages_needed),
        ("amount", "desc", max(1, ceil(pages_needed / 2))),
    ]
    for order_by, order, pages in plans:
        for page in range(1, pages + 1):
            data = cli.screener("all_a", order_by, order, page_size, page=page)
            for item in data.get("list") or []:
                symbol = str(item.get("symbol") or "").upper()
                if symbol in seen or not _is_a_share_symbol(symbol):
                    continue
                seen.add(symbol)
                stocks.append(item)
                if len(stocks) >= pool_size:
                    return stocks
    return stocks


def _analyze_one(quote: dict[str, Any], *, debug: bool = False) -> ScreenFeatures | None:
    from stock_core.fund_flow import fetch_daily_fund_flow, summarize_fund_flow
    from stock_core.kline import fetch_daily_kline
    from stock_core.symbols import normalize_symbol

    symbol = str(quote.get("symbol") or "")
    if not _is_a_share_symbol(symbol):
        return None
    try:
        market, code, xq_symbol = normalize_symbol(symbol)
        if market != "a":
            return None
        fund_rows = fetch_daily_fund_flow(market, code)
        if len(fund_rows) < 20:
            return None
        fund_summary = summarize_fund_flow(fund_rows)
        kline = fetch_daily_kline(xq_symbol, market, count=160)
        if len(kline) < 60:
            return None
        return build_features(quote, fund_summary, kline)
    except Exception as exc:  # noqa: BLE001
        print(f"[screen] {symbol} 跳过：{type(exc).__name__}: {exc}", file=sys.stderr)
        if debug:
            traceback.print_exc(file=sys.stderr)
        return None


def run_screen(*, top: int, pool_size: int, workers: int, debug: bool = False) -> tuple[dict[str, list[Candidate]], int]:
    universe = _collect_universe(pool_size)
    features: list[ScreenFeatures] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(_analyze_one, quote, debug=debug) for quote in universe]
        for idx, future in enumerate(as_completed(futures), 1):
            item = future.result()
            if item is not None:
                features.append(item)
            if idx % 100 == 0:
                print(f"[screen] 进度 {idx}/{len(universe)}，有效 {len(features)}", file=sys.stderr)
    return screen_candidates(features, top=top), len(features)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    result, scanned = run_screen(top=args.top, pool_size=args.pool_size, workers=args.workers, debug=args.debug)
    if args.format == "json":
        print(render_json(result, scanned=scanned))
    else:
        print(render_text(result, title="A 股 1-4 周中短线初筛"))
        print(f"\n数据覆盖说明：本次有效扫描 {scanned} 只；结果用于缩小研究范围，不构成买卖建议。")


if __name__ == "__main__":
    main()

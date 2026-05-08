#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _ensure_shared_path() -> None:
    current = Path(__file__).resolve()
    shared_dir = current.parents[2] / "shared"
    if str(shared_dir) not in sys.path:
        sys.path.insert(0, str(shared_dir))


_ensure_shared_path()

from stock_core.stock_market_hub import analyze_symbol, render_analysis_text  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Programmatic API wrapper for analyze_company.py")
    ap.add_argument("--symbol", required=True, help="股票代码：SZ300750 / SH600519 / HK00700 / BABA")
    ap.add_argument("--top-managers", type=int, default=10)
    ap.add_argument("--top-holders", type=int, default=10)
    ap.add_argument("--ann-days", type=int, default=30)
    ap.add_argument("--ann-limit", type=int, default=20)
    ap.add_argument("--kline-count", type=int, default=1500)
    ap.add_argument("--with-peers", action="store_true")
    ap.add_argument("--skip", default="")
    ap.add_argument("--format", choices=["json", "text"], default="text")
    args = ap.parse_args()

    data = analyze_symbol(
        args.symbol,
        top_managers=args.top_managers,
        top_holders=args.top_holders,
        ann_days=args.ann_days,
        ann_limit=args.ann_limit,
        kline_count=args.kline_count,
        with_peers=args.with_peers,
        skip=args.skip,
    )
    if args.format == "json":
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
    else:
        print(render_analysis_text(data))


if __name__ == "__main__":
    main()

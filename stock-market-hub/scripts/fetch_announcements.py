#!/usr/bin/env python3
"""
公告抓取 CLI：巨潮（A股权威披露源） + 披露易（港股权威披露源）。

业务逻辑已搬到 `shared/stock_core/announcements.py`，本文件只保留 CLI 入口。
其它脚本如果想用公告抓取，**请直接 import shared 那份**，不要再 import 本文件，
也不要再依赖本文件的私有符号。

Usage:
  # A 股按代码（自动查 orgId 内部代码）
  python3 fetch_announcements.py --symbol SZ300750
  python3 fetch_announcements.py --symbol SH600519 --days 90

  # 港股按代码（5 位补零）
  python3 fetch_announcements.py --symbol HK00700
  python3 fetch_announcements.py --symbol HK00700 --from 2026-04-01 --to 2026-04-30

  # 关键词查询（不锁单只股票）
  python3 fetch_announcements.py --keyword "立案调查" --days 30
  python3 fetch_announcements.py --keyword "回购" --category stock

  # 公告类别（仅 A 股有效，常用类别已封装）
  python3 fetch_announcements.py --symbol SZ300750 --category annual
  # category: annual / quarterly / semi / risk / bond / acquisition
  #         / shareholder / equity / pledge / performance / irregular

  # 输出格式
  python3 fetch_announcements.py --symbol SZ300750 --format text
  python3 fetch_announcements.py --symbol SZ300750 --format json   # 默认

输出：JSON 数组，每条含 symbol / name / title / date / pdf_url / category / source
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime

import _path_setup  # noqa: F401,E402  把 <repo>/shared 加入 sys.path
from stock_core.announcements import (  # noqa: E402
    CNINFO_CATEGORY,
    _parse_a_share_symbol,
    cninfo_announcements,
    cninfo_lookup_orgid,
    hkex_announcements,
    hkex_lookup_stock_id,
)
from stock_core.tz import CN_TZ  # noqa: E402

# Re-export so any legacy `from fetch_announcements import xxx` callers keep working.
# 新代码请直接从 stock_core.announcements 引入，不要依赖这个 re-export。
__all__ = [
    "CNINFO_CATEGORY",
    "_parse_a_share_symbol",
    "cninfo_announcements",
    "cninfo_lookup_orgid",
    "hkex_announcements",
    "hkex_lookup_stock_id",
]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--symbol", help="股票代码：SZ300750 / SH600519 / HK00700")
    ap.add_argument("--keyword", help="关键词搜索（仅 A 股巨潮支持）")
    ap.add_argument("--category", help=f"公告类别：{','.join(CNINFO_CATEGORY)}")
    ap.add_argument("--days", type=int, default=30, help="时间范围（天，默认 30）")
    ap.add_argument("--from", dest="from_date", help="起始日期 YYYY-MM-DD（覆盖 --days）")
    ap.add_argument("--to", dest="to_date", help="结束日期 YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=30, help="每个源最多条数（默认 30）")
    ap.add_argument("--format", choices=["json", "text"], default="json")
    args = ap.parse_args()

    if not args.symbol and not args.keyword:
        ap.error("--symbol 或 --keyword 至少提供一个")

    if args.from_date:
        try:
            d_from = datetime.strptime(args.from_date, "%Y-%m-%d")
            d_to = datetime.strptime(args.to_date or datetime.now(CN_TZ).strftime("%Y-%m-%d"), "%Y-%m-%d")
            args.days = max(1, (d_to - d_from).days + 1)
        except ValueError:
            ap.error("--from / --to 格式错误，应为 YYYY-MM-DD")

    items: list[dict] = []
    sym = (args.symbol or "").upper()

    if sym.startswith("HK"):
        code = re.sub(r"\D", "", sym)
        try:
            items = hkex_announcements(code, days=args.days, rows=args.limit)
        except Exception as e:  # noqa: BLE001
            print(f"[fetch_announcements] 披露易抓取失败: {type(e).__name__}: {e}", file=sys.stderr)
            print("[fetch_announcements] 提示：披露易接口不稳定，可重试或减少 --days", file=sys.stderr)
            items = []
    elif sym and not sym.startswith(("US:", "BABA", "JD", "PDD", "BIDU")):
        try:
            stock_code, column = _parse_a_share_symbol(sym)
        except ValueError as e:
            ap.error(str(e))
        items = cninfo_announcements(
            stock_code=stock_code,
            category=args.category,
            days=args.days,
            page_size=args.limit,
            column=column,
        )
    elif args.keyword:
        items = cninfo_announcements(
            keyword=args.keyword,
            category=args.category,
            days=args.days,
            page_size=args.limit,
        )
    else:
        ap.error(f"暂不支持中概股 {sym} 的公告查询（v2 用 SEC EDGAR）")

    print(f"[fetch_announcements] got {len(items)} items", file=sys.stderr)

    if args.format == "text":
        for it in items:
            print(f"{it['date']:>16}  {it['symbol']} {it['name']}  {it['title'][:60]}")
            if it.get("pdf_url"):
                print(f"                  PDF: {it['pdf_url']}")
    else:
        json.dump(items, sys.stdout, ensure_ascii=False, indent=2)
        print()


if __name__ == "__main__":
    main()

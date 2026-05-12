#!/usr/bin/env python3
"""
雪球市场速览：基于雪球公开 screener API（无需登录），输出多维榜单。

Usage:
  # 默认：全 A 涨幅榜 / 跌幅榜 / 成交额榜 / 换手率榜 / 主力净流入榜 / 雪球热度榜
  python3 xueqiu_market.py

  # 限定市场
  python3 xueqiu_market.py --market hk        # 港股
  python3 xueqiu_market.py --market us        # 美股
  python3 xueqiu_market.py --market gem       # 创业板
  python3 xueqiu_market.py --market kcb       # 科创板
  python3 xueqiu_market.py --market st        # ST 风险股

  # 单榜单 + 控制条数
  python3 xueqiu_market.py --board gainers --top 20
  python3 xueqiu_market.py --board losers   --market hk --top 10

  # 输出 JSON（给 agent 进一步分析用）
  python3 xueqiu_market.py --format json

  # 同时输出多市场对比
  python3 xueqiu_market.py --markets all_a,hk,us --board gainers --top 5
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# 让本脚本无论是通过 bin/smh 还是直接 `python3 scripts/xueqiu_market.py` 执行，
# 都能 import 到 ``shared/stock_core``。
_SHARED = Path(__file__).resolve().parents[2] / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from stock_core.market_snapshot import BOARDS, enrich, fmt_amount  # noqa: E402,F401
from stock_core.tz import CN_TZ  # noqa: E402
from stock_core.xueqiu import XueqiuClient  # noqa: E402


MARKET_LABEL = {
    "all_a": "全 A 股",
    "kcb": "科创板",
    "gem": "创业板",
    "stib": "北交所",
    "st": "ST 风险股",
    "hk": "港股",
    "us": "美股",
}


def render_table(title: str, items: list[dict], value_fmt: str) -> str:
    """生成一个文本表格。"""
    if not items:
        return f"\n## {title}\n(无数据)\n"
    lines = [f"\n## {title}", ""]
    lines.append("| 排名 | 代码 | 名称 | 现价 | 涨跌幅 | 指标 | 总市值 |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, it in enumerate(items, 1):
        try:
            value = value_fmt.format(**it)
        except (KeyError, TypeError, ValueError):
            value = "-"
        sym = it.get("symbol", "")
        name = (it.get("name") or "").strip()
        if not name:
            name = it.get("symbol", "")
        cur = it.get("current")
        pct = it.get("percent")
        cap = it.get("market_cap_yi", 0)
        lines.append(
            f"| {i} | `{sym}` | {name} | {cur} | {pct:+.2f}% | {value} | {cap:.0f}亿 |"
            if isinstance(pct, (int, float)) and isinstance(cur, (int, float))
            else f"| {i} | `{sym}` | {name} | {cur} | {pct} | {value} | {cap:.0f}亿 |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--market",
        choices=list(MARKET_LABEL),
        default="all_a",
        help="市场（默认 all_a）",
    )
    ap.add_argument(
        "--markets",
        default="",
        help="多市场对比，逗号分隔（如 all_a,hk,us）",
    )
    ap.add_argument(
        "--board",
        choices=list(BOARDS) + ["all"],
        default="all",
        help="榜单类型（默认 all 输出全部）",
    )
    ap.add_argument("--top", type=int, default=10, help="每榜单条数（默认 10）")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    markets = [m.strip() for m in (args.markets or args.market).split(",") if m.strip()]
    boards = list(BOARDS) if args.board == "all" else [args.board]

    cli = XueqiuClient()

    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M (%Z)")

    if args.format == "json":
        result: dict = {"timestamp": now, "markets": {}}
        for mk in markets:
            result["markets"][mk] = {}
            for b in boards:
                _label, ob, order, _vfmt = BOARDS[b]
                items = cli.screener(mk, ob, order, args.top).get("list", [])
                items = [enrich(x) for x in items]
                result["markets"][mk][b] = items
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return

    out = [f"# 雪球市场速览 — {now}", ""]
    for mk in markets:
        out.append(f"\n# 📊 {MARKET_LABEL.get(mk, mk)}")
        for b in boards:
            label, ob, order, vfmt = BOARDS[b]
            try:
                items = cli.screener(mk, ob, order, args.top).get("list", [])
            except Exception as e:  # noqa: BLE001
                print(
                    f"[xueqiu_market] {mk}/{b} 失败: {type(e).__name__}: {e}",
                    file=sys.stderr,
                )
                continue
            items = [enrich(x) for x in items]
            out.append(render_table(label, items, vfmt))
    print("\n".join(out))


if __name__ == "__main__":
    main()

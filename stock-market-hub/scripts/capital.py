#!/usr/bin/env python3
"""雪球资金流三接口 CLI（capital/assort + capital/flow + capital/history）。

这是对东财 fund_flow 的**补充信息源**，不替代 fund_flow 主源（决策树仍以东财为主）：
  - assort  当日资金分层（A 股有效；超大/大/中/小档买卖金额）
  - flow    当日分钟级主力净流入流水（约 240 条/交易日）
  - history 日级历史 + 雪球已聚合好的 sum3 / sum5 / sum10 / sum20

⚠️ 雪球 ``amount`` 与东财 ``main`` 字段语义可能不一致（实测同标的 20 日累计差
2 倍量级）。本工具用于人工审计 / 对照，不直接进决策树。

需要 ``~/.config/stock-market-hub/xueqiu.cookie`` 已配置（详见 SKILL.md §0）。
cookie 过期时 stderr 会打多行醒目告警，结果里也带 ``warnings`` 字段。

Usage:
  python3 capital.py --symbol SZ300750                # 默认输出全部三个接口（text）
  python3 capital.py --symbol SH600519 --view assort
  python3 capital.py --symbol SZ300750 --view history --count 60
  python3 capital.py --symbol SZ300750 --view intraday
  python3 capital.py --symbol SZ300750 --format json  # 整体 JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta

import _path_setup  # noqa: F401,E402  把 <repo>/shared 加入 sys.path
from stock_core.symbols import normalize_symbol  # noqa: E402
from stock_core.xueqiu import XueqiuClient  # noqa: E402


CN_TZ = timezone(timedelta(hours=8))


def _to_xq_symbol(market: str, code: str) -> str | None:
    """转换为雪球 capital/* 接受的 symbol。仅 A 股有效；港股雪球资金流返回 data=None。"""
    if market != "a":
        return None
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("0", "3")):
        return f"SZ{code}"
    return None


def _fmt_yi(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{value / 1e8:+.2f} 亿"


def _fmt_ts(ms: int | float | None) -> str:
    if ms in (None, 0):
        return "-"
    return datetime.fromtimestamp(int(ms) / 1000, tz=CN_TZ).strftime("%Y-%m-%d %H:%M")


def render_assort(data: dict, symbol: str) -> str:
    if data is None:
        return f"## 当日资金分层 {symbol}\n\n（雪球 capital/assort 返回 None，可能是港股或停牌）"

    def net(b: str, s: str) -> float | None:
        bv, sv = data.get(b), data.get(s)
        if bv is None or sv is None:
            return None
        return float(bv) - float(sv)

    xlarge = net("buy_xlarge", "sell_xlarge")
    large = net("buy_large", "sell_large")
    medium = net("buy_medium", "sell_medium")
    small = net("buy_small", "sell_small")
    main = (large or 0) + (xlarge or 0)
    grand = float(data.get("buy_total") or 0) + float(data.get("sell_total") or 0)

    def pct(v: float | None) -> str:
        if v is None or grand <= 0:
            return "-"
        return f"{v / grand * 100:+.2f}%"

    lines = [
        f"## 当日资金分层 {symbol}",
        f"_来源：雪球 capital/assort；时点：{_fmt_ts(data.get('timestamp'))}_",
        "",
        "| 档位 | 买入(亿) | 卖出(亿) | 净额(亿) | 占总成交 |",
        "|---|---|---|---|---|",
    ]
    rows = [
        ("超大单", "buy_xlarge", "sell_xlarge", xlarge),
        ("大单", "buy_large", "sell_large", large),
        ("中单", "buy_medium", "sell_medium", medium),
        ("小单", "buy_small", "sell_small", small),
    ]
    for label, kb, ks, n in rows:
        lines.append(
            f"| {label} | {_fmt_yi(data.get(kb))} | {_fmt_yi(data.get(ks))} | "
            f"{'-' if n is None else f'{n/1e8:+.2f} 亿'} | {pct(n)} |"
        )
    lines.append(
        f"| **主力合计**（超大+大） | - | - | "
        f"**{main/1e8:+.2f} 亿** | {pct(main)} |"
    )
    lines.append(
        f"| 总成交 | {_fmt_yi(data.get('buy_total'))} | "
        f"{_fmt_yi(data.get('sell_total'))} | - | - |"
    )
    return "\n".join(lines)


def render_history(data: dict, symbol: str, recent: int = 10) -> str:
    if data is None:
        return f"## 日级历史 {symbol}\n\n（雪球 capital/history 返回 None）"
    items = data.get("items") or []
    lines = [
        f"## 日级主力净流入 {symbol}（雪球 capital/history，共 {len(items)} 天）",
        "",
        "### 雪球已聚合的滚动累计",
        "| 周期 | 主力净额 |",
        "|---|---|",
    ]
    for k in ("sum3", "sum5", "sum10", "sum20"):
        v = data.get(k)
        lines.append(f"| {k} | {_fmt_yi(v)} |")
    if items:
        lines.append("")
        lines.append(f"### 最近 {min(recent, len(items))} 天（金额单位：亿）")
        lines.append("| 日期 | 主力净流入 |")
        lines.append("|---|---|")
        for it in items[-recent:]:
            lines.append(f"| {_fmt_ts(it.get('timestamp'))[:10]} | {_fmt_yi(it.get('amount'))} |")
    return "\n".join(lines)


def render_intraday(items: list[dict], symbol: str) -> str:
    if not items:
        return f"## 当日分钟级主力流速 {symbol}\n\n（雪球 capital/flow 无数据）"
    total = sum((it.get("amount") or 0) for it in items)
    lines = [
        f"## 当日分钟级主力净流入 {symbol}（共 {len(items)} 分钟）",
        f"_累计：**{total/1e8:+.2f} 亿**（雪球口径，跟东财 main 不一定对齐）_",
        "",
        "### 头尾抽样",
        "| 时点 | 分钟净流入(亿) |",
        "|---|---|",
    ]
    for it in items[:3] + ([{"timestamp": "...", "amount": None}] if len(items) > 6 else []) + items[-3:]:
        ts = it.get("timestamp")
        ts_str = ts if ts == "..." else _fmt_ts(ts)
        amt = it.get("amount")
        amt_str = "..." if ts == "..." else (f"{amt/1e8:+.2f}" if amt is not None else "-")
        lines.append(f"| {ts_str} | {amt_str} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="雪球资金流三接口（assort / flow / history）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--symbol", required=True, help="股票代码：SZ300750 / SH600519 / HK00700")
    ap.add_argument("--view", choices=["all", "assort", "intraday", "history"], default="all")
    ap.add_argument("--count", type=int, default=60, help="history 拉的天数（默认 60）")
    ap.add_argument("--recent", type=int, default=10, help="history 显示最近 N 天 detail（默认 10）")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    market, code, _ = normalize_symbol(args.symbol)
    xq_sym = _to_xq_symbol(market, code)
    if not xq_sym:
        msg = (
            f"雪球资金流仅 A 股有效（{args.symbol} 是 {market}）。\n"
            "港股主力资金流请用 smh flow 走东财 fflow 主源；北交所 / 美股两边都没有。"
        )
        if args.format == "json":
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 2

    cli = XueqiuClient()
    if not cli.is_logged_in:
        msg = (
            "未配置雪球登录 cookie，capital/* 三接口均无法调用。\n"
            "配置方法：浏览器登录 https://xueqiu.com/ → Cookie-Editor 插件\n"
            "  → Export Header String → 粘到 ~/.config/stock-market-hub/xueqiu.cookie\n"
            "  → chmod 600 ~/.config/stock-market-hub/xueqiu.cookie\n"
            "详细见 stock-market-hub/SKILL.md §0"
        )
        if args.format == "json":
            print(json.dumps({"error": "no_cookie", "message": msg}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 3

    result: dict = {"symbol": args.symbol, "xq_symbol": xq_sym}
    if args.view in ("all", "assort"):
        result["assort"] = cli.capital_assort(xq_sym)
    if args.view in ("all", "intraday"):
        result["intraday"] = cli.capital_intraday(xq_sym)
    if args.view in ("all", "history"):
        result["history"] = cli.capital_history_daily(xq_sym, count=args.count)
    if cli.cookie_expired:
        result["warnings"] = [
            "雪球登录 cookie 已过期；请按 SKILL.md §0 重新导出 cookie。"
        ]

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    blocks: list[str] = []
    if args.view in ("all", "assort"):
        blocks.append(render_assort(result["assort"], args.symbol))
    if args.view in ("all", "history"):
        blocks.append(render_history(result["history"], args.symbol, recent=args.recent))
    if args.view in ("all", "intraday"):
        blocks.append(render_intraday(result.get("intraday") or [], args.symbol))
    print("\n\n".join(blocks))
    if result.get("warnings"):
        print("\n---\n⚠️ **警告**")
        for w in result["warnings"]:
            print(f"- {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

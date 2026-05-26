#!/usr/bin/env python3
"""
事件时间轴：把"为什么涨/跌"的多源信息按时间合并成一条线。

用户场景："小米最近为什么大跌"、"宁德时代上周发生了什么"

数据来源：
  1. K 线（标记单日大涨/大跌、创新高/新低）
  2. 公告（巨潮 / 披露易）
  3. 财经新闻（财联社+海外财经，按公司/关键词过滤）

输出：按日期倒序的事件流，标注 类型 / 严重度 / 链接

Usage:
  python3 event_timeline.py --symbol HK01810 --days 30
  python3 event_timeline.py --symbol SZ300750 --days 60 --format text
  python3 event_timeline.py --symbol SZ300750 --days 60 --news-keywords "宁德时代,宁王,CATL"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from typing import Any

import _path_setup  # noqa: F401,E402  把 <repo>/shared 加入 sys.path
from stock_core.http import fetch  # noqa: E402
from stock_core.kline import fetch_daily_kline  # noqa: E402
from stock_core.symbols import normalize_symbol as _normalize_symbol_impl  # noqa: E402
from stock_core.tz import CN_TZ  # noqa: E402


def _normalize_symbol(symbol: str) -> tuple[str, str, str]:
    """共享 symbol 解析。"""
    return _normalize_symbol_impl(symbol)


# ============ 1. K 线大涨大跌事件 ============ #

def kline_events(symbol: str, market: str, kline_sym: str, days: int) -> list[dict]:
    """从最近 days 天 K 线提取事件：
      - 单日大涨大跌（A/港 ≤±5%，美股 ≤±5%）
      - 创出 N 日新高/新低
      - 涨停/跌停（A 股 ±9.9%/±19.9%）
    """
    kline = fetch_daily_kline(kline_sym, market, count=max(days * 2, 365))
    if not kline:
        return []
    cutoff = (datetime.now(CN_TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    sub = [k for k in kline if k["date"] >= cutoff]
    if len(sub) < 2:
        return []

    events = []
    n_days = len(sub)
    full_dates = [k["date"] for k in kline]
    full_idx = {d: i for i, d in enumerate(full_dates)}

    # 预计算每日的"年内累计 min/max"和"60 日窗口 min/max"。
    # 旧实现里每天都重新 `[x for x in kline if x.date[:4]==yr and x.date<=d]` 是 O(n²)；
    # 这里改成两次 O(n) 累计扫描，把内层每天的工作量降到 O(1)。
    ytd_low_at: dict[str, float] = {}
    ytd_high_at: dict[str, float] = {}
    cur_year: str | None = None
    ymin: float | None = None
    ymax: float | None = None
    for x in kline:  # kline 已按日期升序
        yr = x["date"][:4]
        if yr != cur_year:
            cur_year = yr
            ymin = x["low"]
            ymax = x["high"]
        else:
            if x["low"] < ymin:  # type: ignore[operator]
                ymin = x["low"]
            if x["high"] > ymax:  # type: ignore[operator]
                ymax = x["high"]
        ytd_low_at[x["date"]] = ymin  # type: ignore[assignment]
        ytd_high_at[x["date"]] = ymax  # type: ignore[assignment]

    # 60 日窗口 min/max：用单调 deque 做 O(n)。
    # 旧实现 `kline[max(0,idx-60):idx+1]` + `min/max` 单点是 O(60)，整体 O(60n)——
    # 已经是线性，但既然在重构就一起换成更明确的滑动窗口。
    from collections import deque
    win60_low_at: dict[str, float] = {}
    win60_high_at: dict[str, float] = {}
    low_dq: deque[int] = deque()   # 存索引，低值单调递增
    high_dq: deque[int] = deque()  # 存索引，高值单调递减
    for i, x in enumerate(kline):
        while low_dq and low_dq[0] < i - 60:
            low_dq.popleft()
        while high_dq and high_dq[0] < i - 60:
            high_dq.popleft()
        while low_dq and kline[low_dq[-1]]["low"] >= x["low"]:
            low_dq.pop()
        while high_dq and kline[high_dq[-1]]["high"] <= x["high"]:
            high_dq.pop()
        low_dq.append(i)
        high_dq.append(i)
        win60_low_at[x["date"]] = kline[low_dq[0]]["low"]
        win60_high_at[x["date"]] = kline[high_dq[0]]["high"]

    for i, k in enumerate(sub):
        d = k["date"]
        idx = full_idx[d]
        if idx == 0:
            continue
        prev = kline[idx - 1]
        pct = (k["close"] / prev["close"] - 1) * 100 if prev["close"] else 0

        threshold = 5
        is_big_move = abs(pct) >= threshold
        is_limit = (
            pct <= -9.5 and (kline_sym.startswith("SZ") or kline_sym.startswith("SH"))
        ) or (
            pct >= 9.5 and (kline_sym.startswith("SZ") or kline_sym.startswith("SH"))
        )
        is_gem_limit = (
            (kline_sym.startswith("SZ30") or kline_sym.startswith("SH68"))
            and abs(pct) >= 19.5
        )

        # 创年内新高/新低（预计算好的累计 min/max）
        ytd_low = ytd_low_at.get(d)
        ytd_high = ytd_high_at.get(d)
        if ytd_low is not None and ytd_high is not None:
            new_ytd_low = k["low"] <= ytd_low + 1e-6
            new_ytd_high = k["high"] >= ytd_high - 1e-6
        else:
            new_ytd_low = new_ytd_high = False

        # 60 日新高/新低（滑动窗口预计算）
        win_low = win60_low_at.get(d)
        win_high = win60_high_at.get(d)
        if win_low is not None and win_high is not None:
            new_60d_low = k["low"] <= win_low + 1e-6
            new_60d_high = k["high"] >= win_high - 1e-6
        else:
            new_60d_low = new_60d_high = False

        if not (is_big_move or new_ytd_low or new_ytd_high or new_60d_low or new_60d_high):
            continue

        tags = []
        severity = 2
        if is_gem_limit:
            tags.append(f"创业板/科创{'涨停' if pct > 0 else '跌停'}")
            severity = 4
        elif is_limit:
            tags.append("涨停" if pct > 0 else "跌停")
            severity = 4
        elif is_big_move:
            tags.append(f"大{'涨' if pct > 0 else '跌'} {pct:+.2f}%")
            severity = 3
        if new_ytd_low:
            tags.append("创年内新低")
            severity = max(severity, 4)
        if new_ytd_high:
            tags.append("创年内新高")
            severity = max(severity, 3)
        if new_60d_low and not new_ytd_low:
            tags.append("创 60 日新低")
        if new_60d_high and not new_ytd_high:
            tags.append("创 60 日新高")

        events.append({
            "date": d,
            "type": "PRICE",
            "title": " / ".join(tags),
            "detail": f"开 {k['open']} 高 {k['high']} 低 {k['low']} 收 {k['close']}（涨跌 {pct:+.2f}%）",
            "severity": severity,
            "link": "",
        })
    return events


# ============ 2. 公告事件 ============ #

def announcement_events(market: str, code: str, xq_sym: str, days: int) -> list[dict]:
    """调 fetch_announcements 拿公告。"""
    try:
        from fetch_announcements import (
            cninfo_announcements,
            hkex_announcements,
            _parse_a_share_symbol,
        )
    except ImportError:
        return []
    items = []
    if market == "a":
        try:
            stock_code, column = _parse_a_share_symbol(xq_sym)
        except ValueError:
            return []
        items = cninfo_announcements(stock_code=stock_code, days=days, page_size=50, column=column)
    elif market == "hk":
        try:
            items = hkex_announcements(code, days=days, rows=50)
        except Exception as e:  # noqa: BLE001
            print(f"[event_timeline] 披露易失败: {e}", file=sys.stderr)
            return []
    else:
        return []

    keywords_high_severity = (
        "立案调查", "终止上市", "退市风险", "风险提示", "实际控制人变更",
        "业绩预亏", "商誉减值", "重大诉讼", "暂停上市", "重组",
        "停牌", "复牌", "更名", "ST", "*ST",
    )
    keywords_medium = (
        "回购", "增持", "减持", "分红", "派息", "股权激励", "员工持股",
        "并购", "收购", "重大合同", "中标", "新产品",
        "年度报告", "半年度报告", "季度报告", "业绩快报", "业绩预告",
    )
    out = []
    for it in items:
        title = it.get("title") or ""
        sev = 2
        if any(k in title for k in keywords_high_severity):
            sev = 4
        elif any(k in title for k in keywords_medium):
            sev = 3
        else:
            # 低优先级公告（翌日披露报表/月报表 等）跳过
            if any(noise in title for noise in ("翌日披露", "月報表", "通知信函", "代表委任")):
                continue
        out.append({
            "date": it.get("date", "")[:10],
            "type": "ANNOUNCEMENT",
            "title": title,
            "detail": "",
            "severity": sev,
            "link": it.get("pdf_url") or "",
        })
    return out


# ============ 3. 新闻事件 ============ #

def news_events(symbol: str, market: str, days: int, keywords: list[str]) -> list[dict]:
    """从 fetch_market_news 拉，再按关键词过滤。"""
    try:
        from fetch_market_news import (
            fetch_cls_telegraph,
            fetch_overseas_finance_rss,
        )
    except ImportError:
        return []

    all_news = []
    try:
        all_news.extend(fetch_cls_telegraph(150))
    except Exception:
        pass
    try:
        all_news.extend(fetch_overseas_finance_rss(50))
    except Exception:
        pass

    cutoff = datetime.now(CN_TZ) - timedelta(days=days)
    out = []
    for it in all_news:
        text = (it.get("title") or "") + " " + (it.get("content") or "")
        if not any(kw in text for kw in keywords):
            continue
        pub_date = it.get("pubDate") or ""
        if pub_date:
            try:
                d = datetime.fromisoformat(pub_date)
                if d < cutoff:
                    continue
                date_str = d.strftime("%Y-%m-%d %H:%M")
            except Exception:
                date_str = pub_date[:16]
        else:
            date_str = ""
        out.append({
            "date": date_str[:10] or "?",
            "datetime": date_str,
            "type": "NEWS",
            "title": it.get("title", "")[:120],
            "detail": (it.get("content") or "")[:200],
            "severity": 4 if it.get("level") == "A" else 2,
            "source": it.get("source", ""),
            "link": it.get("link", ""),
        })
    return out


# ============ 主流程 ============ #

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument(
        "--news-keywords",
        default="",
        help="新闻过滤关键词，逗号分隔。默认根据公司名自动生成",
    )
    ap.add_argument("--format", choices=["json", "text"], default="text")
    args = ap.parse_args()

    market, code, xq_sym = _normalize_symbol(args.symbol)
    kline_sym = xq_sym if market == "a" else code

    # 拿公司名（用于新闻关键词）
    company_name = ""
    try:
        from stock_core.xueqiu import XueqiuClient
        cli = XueqiuClient()
        qs = cli.quotes([kline_sym])
        if qs:
            company_name = (qs[0].get("name") or "").strip()
    except Exception:
        pass

    if args.news_keywords:
        keywords = [k.strip() for k in args.news_keywords.split(",") if k.strip()]
    else:
        keywords = []
        if company_name:
            keywords.append(company_name)
            # 取 2-3 字简称
            if len(company_name) >= 4:
                keywords.append(company_name[:2])
        keywords.append(args.symbol)
        keywords.append(kline_sym)
        # 去重
        keywords = list(dict.fromkeys(keywords))

    print(f"[event_timeline] {args.symbol} ({company_name}) 近 {args.days} 天，关键词={keywords}", file=sys.stderr)

    events: list[dict] = []
    events.extend(kline_events(args.symbol, market, kline_sym, args.days))
    print(f"[event_timeline] 价格异动 {sum(1 for e in events if e['type']=='PRICE')} 条", file=sys.stderr)
    events.extend(announcement_events(market, code, xq_sym, args.days))
    print(f"[event_timeline] 公告 {sum(1 for e in events if e['type']=='ANNOUNCEMENT')} 条", file=sys.stderr)
    events.extend(news_events(args.symbol, market, args.days, keywords))
    print(f"[event_timeline] 新闻 {sum(1 for e in events if e['type']=='NEWS')} 条", file=sys.stderr)

    # 按日期倒序（最近的在前）+ 同日内按 severity 倒序
    events.sort(key=lambda e: (e.get("date", ""), e.get("severity", 0)), reverse=True)

    out = {
        "symbol": args.symbol,
        "name": company_name,
        "days": args.days,
        "fetched_at": datetime.now(CN_TZ).isoformat(),
        "events": events,
        "summary": {
            "total": len(events),
            "by_type": {
                t: sum(1 for e in events if e["type"] == t)
                for t in ("PRICE", "ANNOUNCEMENT", "NEWS")
            },
        },
    }

    if args.format == "json":
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
        return

    type_emoji = {"PRICE": "📈", "ANNOUNCEMENT": "📋", "NEWS": "📰"}
    sev_emoji = {4: "🔴", 3: "🟠", 2: "🟡", 1: "⚪️"}
    print(f"# {company_name or args.symbol} ({args.symbol}) — 近 {args.days} 天事件时间轴")
    print(f"_共 {out['summary']['total']} 件事，价格 {out['summary']['by_type']['PRICE']} / 公告 {out['summary']['by_type']['ANNOUNCEMENT']} / 新闻 {out['summary']['by_type']['NEWS']}_")
    print()
    cur_date = ""
    for e in events:
        d = e.get("date", "?")
        if d != cur_date:
            print(f"\n## {d}")
            cur_date = d
        emoji = type_emoji.get(e.get("type", ""), "")
        sev = sev_emoji.get(e.get("severity", 2), "")
        title = e.get("title", "")[:110]
        line = f"- {sev} {emoji} **{title}**"
        if e.get("type") == "NEWS" and e.get("source"):
            line += f"  _{e['source']}_"
        print(line)
        if e.get("detail"):
            print(f"  _{e['detail'][:200]}_")
        if e.get("link"):
            print(f"  → {e['link']}")


if __name__ == "__main__":
    main()

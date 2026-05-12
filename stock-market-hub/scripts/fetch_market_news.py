#!/usr/bin/env python3
"""
当日 A股/港股/中概 财经新闻聚合。

数据源（多源聚合 + 失败容忍）：
  - 财联社电报（cls.cn）：最权威的 A 股盘中新闻流，分钟级更新，重要消息标 level=A
  - 新浪财经滚动（feed.mix.sina.com.cn）：覆盖更广，国内+海外财经
  - 雪球热门帖（xueqiu.com）：散户/大V观点，盘后情绪
  -（可选）复用 newsboat-news-hub 的财经源（CNBC、MarketWatch、WSJ）以覆盖海外视角

Usage:
  # 默认：拉取所有源最近 N 条（不过滤日期）
  python3 fetch_market_news.py

  # 限定单日（按 Asia/Shanghai 时区）
  python3 fetch_market_news.py --date 2026-04-30

  # 限定数量、排除某些源
  python3 fetch_market_news.py --limit 30 --skip xueqiu

  # 输出格式：json (默认) / text
  python3 fetch_market_news.py --format text

输出：JSON 数组到 stdout，每条含 title / source / link / pubDate / level / category
进度 + 统计 → stderr。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[2] / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from stock_core.http import fetch_json, fetch_text  # noqa: E402
from stock_core.tz import CN_TZ  # noqa: E402


# ---------- 各源抓取函数 ---------- #

def fetch_cls_telegraph(limit: int = 50) -> list[dict]:
    """财联社电报 https://www.cls.cn/telegraph"""
    url = "https://www.cls.cn/nodeapi/updateTelegraphList"
    params = {
        "app": "CailianpressWeb",
        "os": "web",
        "sv": "7.7.5",
        "rn": min(limit, 50),
        "lastTime": 0,
        "last_time": 0,
    }
    data = fetch_json(url, params=params, headers={"Referer": "https://www.cls.cn/telegraph"})
    items = []
    for it in data.get("data", {}).get("roll_data", []):
        ctime = it.get("ctime") or it.get("modified_time")
        try:
            dt = datetime.fromtimestamp(int(ctime), tz=CN_TZ)
        except Exception:
            dt = None
        title = (it.get("title") or "").strip() or (it.get("brief") or "").strip()
        if not title:
            content = (it.get("content") or "").strip()
            title = content[:80] + ("..." if len(content) > 80 else "")
        link_id = it.get("id")
        items.append({
            "title": title,
            "content": (it.get("content") or "").strip(),
            "source": "财联社电报",
            "link": f"https://www.cls.cn/detail/{link_id}" if link_id else "",
            "pubDate": dt.isoformat() if dt else None,
            "level": it.get("level") or "B",  # A=红色重磅 B=普通
            "category": "电报",
        })
    return items


FINANCE_KEYWORDS = (
    # A 股 / 港股 / 美股核心
    "股", "市", "债", "金融", "财经", "财报", "营收", "净利", "毛利", "降息", "加息",
    "通胀", "通缩", "GDP", "CPI", "PMI", "PPI", "央行", "央行会议", "Fed", "美联储",
    "汇率", "外汇", "原油", "黄金", "白银", "加密", "比特币", "以太坊",
    "证券", "基金", "ETF", "期货", "期权", "REITs", "可转债",
    "IPO", "上市", "退市", "ST", "并购", "重组", "收购", "回购", "增持", "减持",
    "分红", "派息", "送股", "配股", "增发", "再融资", "定增",
    "业绩", "财务", "审计", "披露", "公告", "财季", "Q1", "Q2", "Q3", "Q4",
    "经济", "贸易", "关税", "出口", "进口", "制造业", "消费", "PPI", "M2",
    # 板块/行业
    "新能源", "光伏", "储能", "电池", "半导体", "芯片", "AI", "人工智能", "算力",
    "机器人", "汽车", "白酒", "医药", "生物", "地产", "房地产", "煤炭", "钢铁",
    # 公司动态
    "腾讯", "阿里", "百度", "字节", "美团", "京东", "拼多多", "苹果", "Apple", "OpenAI",
    "Microsoft", "微软", "Nvidia", "英伟达", "Tesla", "特斯拉", "Meta",
    # 监管
    "证监会", "银监会", "SEC", "央行", "外管局", "立案", "调查",
    # 国际市场
    "纳斯达克", "Nasdaq", "Dow", "S&P", "标普", "恒生", "Hang Seng",
)


def _is_finance_related(text: str) -> bool:
    """判断一段文本是否与财经相关（用于过滤新浪滚动里的非财经新闻）"""
    if not text:
        return False
    return any(kw in text for kw in FINANCE_KEYWORDS)


def fetch_sina_finance_roll(limit: int = 50) -> list[dict]:
    """新浪财经滚动：https://feed.mix.sina.com.cn/api/roll/get

    NOTE 2026-04: lid=1686 已下线。lid=2509/2510/2519 内容混杂（财经+体育+社会）。
    这里命中 lid 后再用 FINANCE_KEYWORDS 关键词过滤，只保留财经相关。
    """
    candidates = [
        (153, 2509),  # 综合滚动
        (153, 2510),  # 综合滚动
        (384, 2519),  # 财经主页滚动（pageid=384）
        (153, 2511),
    ]
    url = "https://feed.mix.sina.com.cn/api/roll/get"
    items_for_lid: list = []
    for pageid, lid in candidates:
        params = {"pageid": pageid, "lid": lid, "num": min(limit, 100), "page": 1}
        try:
            raw = fetch_text(
                url,
                params=params,
                headers={"Referer": "https://finance.sina.com.cn/"},
                retries=0,
            )
        except Exception:
            continue
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            continue
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if data.get("result", {}).get("status", {}).get("code") != 0:
            continue
        rows = data.get("result", {}).get("data", []) or []
        if not rows:
            continue
        items_for_lid = rows
        print(
            f"[fetch_market_news] sina 命中 pageid={pageid} lid={lid} ({len(rows)} 条)",
            file=sys.stderr,
        )
        break
    else:
        print(
            "[fetch_market_news] sina: 所有候选 lid 都返回空",
            file=sys.stderr,
        )
        return []

    items: list[dict] = []
    skipped = 0
    for it in items_for_lid:
        title = (it.get("title") or "").strip()
        content = (it.get("intro") or "").strip()
        # 财经相关性过滤（剔除体育/社会/娱乐等混杂内容）
        if not _is_finance_related(title + " " + content):
            skipped += 1
            continue
        ts = it.get("ctime") or it.get("intime")
        try:
            dt = datetime.fromtimestamp(int(ts), tz=CN_TZ) if ts else None
        except Exception:
            dt = None
        items.append({
            "title": title,
            "content": content,
            "source": f"新浪财经-{it.get('media_name') or '未知'}",
            "link": it.get("url") or "",
            "pubDate": dt.isoformat() if dt else None,
            "level": "B",
            "category": "新闻",
        })
    if skipped:
        print(
            f"[fetch_market_news] sina 财经过滤：保留 {len(items)} / 跳过 {skipped}（非财经）",
            file=sys.stderr,
        )
    return items


def fetch_xueqiu_hot(limit: int = 30) -> list[dict]:
    """雪球热门帖。

    自动检测 ~/.config/stock-market-hub/xueqiu.cookie：
      - 有 cookie：调 XueqiuClient.hot_topics
      - 无 cookie：返回空 + stderr 提示
    """
    from stock_core.xueqiu import XueqiuClient
    cli = XueqiuClient()
    if not cli.is_logged_in:
        print(
            "[fetch_market_news] xueqiu: 未配置登录 cookie，跳过热门帖。"
            "如需启用：~/.config/stock-market-hub/xueqiu.cookie 写入浏览器拷贝的 cookie",
            file=sys.stderr,
        )
        return []
    raw = cli.hot_topics(size=limit)
    items = []
    for it in raw:
        meta = it.get("data") or {}
        title = (meta.get("title") or meta.get("text") or "").strip()
        title = re.sub(r"<[^>]+>", "", title)
        ts = meta.get("created_at") or meta.get("timeBefore")
        dt = None
        if isinstance(ts, (int, float)) and ts > 0:
            try:
                dt = datetime.fromtimestamp(int(ts) / 1000, tz=CN_TZ)
            except Exception:
                dt = None
        items.append({
            "title": title[:120],
            "content": "",
            "source": f"雪球-{(meta.get('user') or {}).get('screen_name', '匿名')}",
            "link": f"https://xueqiu.com{meta.get('target')}" if meta.get("target") else "",
            "pubDate": dt.isoformat() if dt else None,
            "level": "C",  # 情绪类
            "category": "情绪",
        })
    return items


def fetch_overseas_finance_rss(limit: int = 30) -> list[dict]:
    """复用 newsboat-news-hub 的财经 RSS 源（CNBC / MarketWatch / AP business）。

    这些源对港股 + 中概股 + 全球宏观影响很大，是国内信息流的重要补充。
    """
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    sources = [
        ("CNBC 头条", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
        ("MarketWatch 头条", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
        ("MarketWatch 实时", "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"),
        ("AP 商业", "https://apnews.com/hub/business?output=rss"),
    ]
    items = []
    for source_name, url in sources:
        try:
            text = fetch_text(url, retries=0, timeout=8)
        except Exception:
            continue
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = item.findtext("pubDate") or ""
            try:
                dt = parsedate_to_datetime(pub).astimezone(CN_TZ) if pub else None
            except Exception:
                dt = None
            if not title:
                continue
            items.append({
                "title": title[:200],
                "content": "",
                "source": source_name,
                "link": link,
                "pubDate": dt.isoformat() if dt else None,
                "level": "B",
                "category": "海外财经",
            })
            if len(items) >= limit * len(sources):
                break
    return items


SOURCES = {
    "cls": fetch_cls_telegraph,
    "sina": fetch_sina_finance_roll,
    "overseas": fetch_overseas_finance_rss,
    "xueqiu": fetch_xueqiu_hot,
}


# ---------- 过滤 + 去重 ---------- #

def in_date_window(item: dict, target_date: str | None) -> bool:
    if not target_date:
        return True
    if not item.get("pubDate"):
        return True  # 无时间戳的保留
    try:
        dt = datetime.fromisoformat(item["pubDate"])
    except ValueError:
        return True
    return dt.strftime("%Y-%m-%d") == target_date


def dedupe(items: list[dict]) -> list[dict]:
    seen_titles: set[str] = set()
    out = []
    for it in items:
        key = re.sub(r"\s+", "", it.get("title", ""))[:50]
        if not key or key in seen_titles:
            continue
        seen_titles.add(key)
        out.append(it)
    return out


# ---------- 主流程 ---------- #

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="过滤日期 YYYY-MM-DD（按 Asia/Shanghai）")
    ap.add_argument("--limit", type=int, default=50, help="每个源最多拉取条数（默认 50）")
    ap.add_argument("--skip", default="", help="跳过的源（逗号分隔，可选 cls/sina/xueqiu）")
    ap.add_argument("--format", choices=["json", "text"], default="json")
    args = ap.parse_args()

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    sources = {k: v for k, v in SOURCES.items() if k not in skip}

    all_items: list[dict] = []
    stats: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        future_map = {ex.submit(fn, args.limit): name for name, fn in sources.items()}
        for fut in as_completed(future_map):
            name = future_map[fut]
            try:
                items = fut.result()
                stats[name] = f"OK ({len(items)})"
                all_items.extend(items)
            except Exception as e:  # noqa: BLE001
                stats[name] = f"FAIL: {type(e).__name__}: {str(e)[:80]}"

    print(f"[fetch_market_news] sources: {stats}", file=sys.stderr)

    before_filter = len(all_items)
    if args.date:
        all_items = [it for it in all_items if in_date_window(it, args.date)]
    after_filter = len(all_items)

    all_items = dedupe(all_items)
    after_dedupe = len(all_items)

    all_items.sort(
        key=lambda x: (x.get("pubDate") or "", x.get("level") == "A"),
        reverse=True,
    )

    print(
        f"[fetch_market_news] before_filter={before_filter} "
        f"after_date_filter={after_filter} after_dedupe={after_dedupe}",
        file=sys.stderr,
    )

    if args.format == "text":
        for it in all_items:
            star = "★" if it.get("level") == "A" else " "
            t = (it.get("pubDate") or "")[11:16]
            print(f"{star} {t} [{it['source']}] {it['title']}")
            if it.get("link"):
                print(f"    {it['link']}")
    else:
        json.dump(all_items, sys.stdout, ensure_ascii=False, indent=2)
        print()


if __name__ == "__main__":
    main()

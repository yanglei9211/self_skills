#!/usr/bin/env python3
"""
Fetch and categorize news from Newsboat RSS sources.

Usage:
    python3 fetch_news.py                      # use urls-china (default)
    python3 fetch_news.py --urls /path/to/urls # use custom urls file
    python3 fetch_news.py --proxy http://127.0.0.1:7890

Output: JSON to stdout, progress to stderr.
"""

import urllib.request
import xml.etree.ElementTree as ET
import json
import sys
import re
import os
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

CATEGORY_RULES = [
    ("伊朗局势/中东", ["iran", "hormuz", "tehran", "strait", "persian", "hezbollah", "lebanon"]),
    ("美国政治", ["trump", "white house", "republican", "democrat", "gop", "congress", "senate", "house speaker", "biden", "oval office"]),
    ("教皇/宗教", ["pope", "leo ", "vatican", "catholic"]),
    ("俄乌冲突", ["ukraine", "russia", "moscow", "kyiv", "putin", "zelensky", "crimea"]),
    ("中国相关", ["china", "chinese", "beijing", "taiwan", "xi jinping"]),
    ("以巴冲突", ["israel", "gaza", "hamas", "palestinian", "netanyahu", "west bank"]),
    ("经济/市场", ["tariff", "trade war", "stock", "market", "economy", "gdp", "inflation", "fed ", "interest rate", "wall street", "dow", "nasdaq", "s&p", "earnings"]),
    ("能源", ["oil", "gas price", "energy", "opec", "petroleum"]),
    ("科技/AI", ["ai ", "artificial intel", "openai", "google", "apple", "microsoft", "meta ", "nvidia", "robot", "chatgpt", "llm", "machine learn"]),
    ("亚太", ["north korea", "south korea", "japan", "asia", "asean", "pacific"]),
    ("欧洲", ["europe", "france", "germany", "uk ", "britain", "london", "paris", "italy", "eu ", "nato"]),
    ("非洲", ["africa", "sudan", "nigeria", "congo", "kenya", "ethiopia"]),
    ("社会/犯罪", ["shooting", "gun", "murder", "kill", "arrest", "crime", "police", "fbi"]),
    ("气候/天气", ["climate", "weather", "tornado", "hurricane", "flood", "wildfire", "ocean"]),
    ("选举", ["election", "vote", "poll", "ballot", "primary", "midterm"]),
    ("健康/医疗", ["health", "drug", "cannabis", "medical", "vaccine", "disease", "alzheimer"]),
    ("体育", ["nba", "nfl", "sport", "game", "playoff", "champion", "athletic", "soccer", "football"]),
]

CATEGORY_ORDER = [c[0] for c in CATEGORY_RULES] + ["其他"]

SOURCE_FALLBACK_CATEGORIES = {
    "tech": "科技/AI", "verge": "科技/AI", "ars": "科技/AI",
    "market": "经济/市场", "cnbc": "经济/市场",
}


def classify(title, source):
    t = title.lower()
    for cat, keywords in CATEGORY_RULES:
        if any(w in t for w in keywords):
            return cat
    src = source.lower()
    for key, cat in SOURCE_FALLBACK_CATEGORIES.items():
        if key in src:
            return cat
    return "其他"


def parse_urls_file(path):
    """Parse Newsboat urls file into {name: url} dict."""
    feeds = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r'^(\S+)\s+"~([^"]+)"', line)
            if match:
                url, name = match.group(1), match.group(2)
                feeds[name] = url
            else:
                parts = line.split()
                if parts:
                    feeds[parts[0][:40]] = parts[0]
    return feeds


def fetch_feed(name, url, proxy=None):
    """Fetch and parse a single RSS/Atom feed. Returns list of items."""
    items = []
    try:
        if proxy:
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            opener = urllib.request.build_opener(handler)
        else:
            opener = urllib.request.build_opener()

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=10) as resp:
            data = resp.read()

        root = ET.fromstring(data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//atom:entry", ns)

        for entry in entries[:25]:
            title = entry.findtext("title") or entry.findtext("atom:title", namespaces=ns) or ""
            link = entry.findtext("link") or ""
            if not link:
                link_el = entry.find("atom:link", ns)
                if link_el is not None:
                    link = link_el.get("href", "")
            pub = entry.findtext("pubDate") or entry.findtext("atom:updated", namespaces=ns) or ""
            items.append({
                "source": name,
                "title": title.strip(),
                "link": link.strip(),
                "pubDate": pub.strip(),
            })

        print(f"OK {name}: {len(entries)} items", file=sys.stderr)
    except Exception as e:
        print(f"FAIL {name}: {e}", file=sys.stderr)
    return items


def dedup_and_categorize(all_items, max_per_cat=8):
    """Deduplicate by title similarity and group into categories."""
    seen = set()
    categorized = defaultdict(list)

    for item in all_items:
        title = item["title"]
        if not title or len(title) < 10:
            continue
        key = re.sub(r"[^a-zA-Z0-9]", "", title.lower())[:60]
        if key in seen:
            continue
        seen.add(key)

        cat = classify(title, item["source"])
        short_source = item["source"].split(" - ")[0].strip()

        real_source = short_source
        display_title = title
        if "Google News" in short_source and " - " in title:
            parts = title.rsplit(" - ", 1)
            display_title = parts[0].strip()
            real_source = parts[1].strip()

        categorized[cat].append({
            "title": display_title,
            "source": real_source,
            "link": item["link"],
        })

    output = {}
    for cat in CATEGORY_ORDER:
        if cat in categorized:
            output[cat] = categorized[cat][:max_per_cat]
    return output


def main():
    parser = argparse.ArgumentParser(description="Fetch and categorize RSS news")
    parser.add_argument("--urls", help="Path to Newsboat urls file")
    parser.add_argument("--proxy", help="HTTP proxy URL (e.g. http://127.0.0.1:7890)")
    parser.add_argument("--max-per-category", type=int, default=8)
    args = parser.parse_args()

    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    urls_path = args.urls or os.path.join(skill_dir, "urls-china")
    if not os.path.exists(urls_path):
        urls_path = os.path.expanduser("~/.newsboat/urls")

    feeds = parse_urls_file(urls_path)
    print(f"Loaded {len(feeds)} feeds from {urls_path}", file=sys.stderr)

    all_items = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(fetch_feed, name, url, args.proxy): name for name, url in feeds.items()}
        for future in as_completed(futures):
            all_items.extend(future.result())

    result = dedup_and_categorize(all_items, args.max_per_category)

    total = sum(len(v) for v in result.values())
    print(f"\nDone: {len(all_items)} raw → {total} categorized across {len(result)} topics", file=sys.stderr)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

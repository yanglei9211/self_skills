#!/usr/bin/env python3
"""
Fetch and categorize news from Newsboat RSS sources.

Usage:
    # Default: last 24h in US Eastern time
    python3 fetch_news.py

    # Strict single-day filter (recommended for daily briefings)
    python3 fetch_news.py --date 2026-04-20               # ET by default
    python3 fetch_news.py --date 2026-04-20 --tz America/Los_Angeles
    python3 fetch_news.py --date 2026-04-20 --tz Asia/Shanghai

    # Custom window
    python3 fetch_news.py --since 2026-04-20 --until 2026-04-21 --tz UTC

    # Keep items that have no / unparseable pubDate
    python3 fetch_news.py --date 2026-04-20 --allow-no-date

    # Retry on 429 / 403 / 5xx / timeouts (recommended when AP / Fox misbehave)
    python3 fetch_news.py --date 2026-04-20 --retry-failed 3

    # Other
    python3 fetch_news.py --urls /path/to/urls
    python3 fetch_news.py --proxy http://127.0.0.1:7890

Output: JSON to stdout, progress + coverage report to stderr.

Each output item preserves `pubDate` (ISO 8601, in target timezone) so downstream
consumers can verify / re-filter without re-fetching.
"""

import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import json
import sys
import re
import os
import random
import time
import argparse
from html.parser import HTMLParser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

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
    "cgtn": "中国相关", "sixth tone": "中国相关",
    "pandaily": "中国相关", "technode": "中国相关",
    "scmp": "中国相关", "nikkei asia": "亚太",
    "the diplomat": "亚太", "bbc - 亚洲": "亚太",
}


# ---------------------------------------------------------------------------
# Date / timezone helpers
# ---------------------------------------------------------------------------

def resolve_tz(name):
    """Return a tzinfo object for the given zone name.

    Falls back to a fixed offset UTC when zoneinfo is unavailable. Common
    shortcuts like "ET", "PT", "CT" are mapped to standard IANA names.
    """
    if name is None:
        name = "America/New_York"
    alias = {
        "ET": "America/New_York",
        "EST": "America/New_York",
        "EDT": "America/New_York",
        "PT": "America/Los_Angeles",
        "PST": "America/Los_Angeles",
        "PDT": "America/Los_Angeles",
        "CT": "America/Chicago",
        "MT": "America/Denver",
        "UTC": "UTC",
        "CN": "Asia/Shanghai",
        "CST": "Asia/Shanghai",
        "BJT": "Asia/Shanghai",
    }
    name = alias.get(name, name)
    if ZoneInfo is None:
        if name == "UTC":
            return timezone.utc
        # Best-effort: treat unknown zones as UTC so the script still runs.
        print(
            f"WARN: Python zoneinfo unavailable; falling back to UTC for '{name}'",
            file=sys.stderr,
        )
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception as e:
        print(f"WARN: unknown timezone '{name}' ({e}); using UTC", file=sys.stderr)
        return timezone.utc


def parse_pubdate(raw):
    """Parse a feed's pubDate / updated string to an aware datetime.

    Returns None if unparseable.
    """
    if not raw:
        return None
    raw = raw.strip()

    # Try RFC 2822 first (standard RSS pubDate).
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except (TypeError, ValueError):
        pass

    # Sixth Tone / custom formats
    # "May 05, 2026"  (month name, day, year — no time, assume midnight UTC)
    try:
        dt = datetime.strptime(raw, "%B %d, %Y")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    # "2026 May 05   05:01:10 PDT" (year first, extra whitespace)
    cleaned = re.sub(r"\s+", " ", raw)
    try:
        dt = datetime.strptime(cleaned, "%Y %b %d %H:%M:%S %Z")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    # Try ISO 8601 (Atom <updated>). Handle trailing Z.
    iso = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def resolve_window(args, tz):
    """Return (start_dt, end_dt) inclusive-exclusive in the target tz.

    Priority:
      --date           → [date 00:00, date+1 00:00)
      --since/--until  → [since 00:00, until 00:00)   (until optional → since+1d)
      (none)           → last 24 hours ending now
    """
    if args.date:
        d = datetime.strptime(args.date, "%Y-%m-%d").date()
        start = datetime(d.year, d.month, d.day, tzinfo=tz)
        end = start + timedelta(days=1)
        return start, end

    if args.since:
        d = datetime.strptime(args.since, "%Y-%m-%d").date()
        start = datetime(d.year, d.month, d.day, tzinfo=tz)
        if args.until:
            d2 = datetime.strptime(args.until, "%Y-%m-%d").date()
            end = datetime(d2.year, d2.month, d2.day, tzinfo=tz)
        else:
            end = start + timedelta(days=1)
        return start, end

    now = datetime.now(tz)
    return now - timedelta(hours=24), now


# ---------------------------------------------------------------------------
# Classification / IO
# ---------------------------------------------------------------------------

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


# HTTP / network errors that should trigger a retry. XML parse errors and
# 4xx responses like 401/404 are NOT retried — they will not fix themselves.
RETRYABLE_HTTP_CODES = {403, 408, 425, 429, 500, 502, 503, 504}


def _is_retryable(exc):
    """Return (retryable: bool, retry_after: Optional[float]) for a given exception."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in RETRYABLE_HTTP_CODES:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                return True, float(retry_after) if retry_after else None
            except (TypeError, ValueError):
                return True, None
        return False, None
    if isinstance(exc, urllib.error.URLError):
        return True, None  # connection reset / timeout / DNS hiccup
    if isinstance(exc, TimeoutError):
        return True, None
    return False, None


def _fetch_once(url, proxy, timeout=10):
    """Single HTTP GET. Raises on network / HTTP failures."""
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(handler)
    else:
        opener = urllib.request.build_opener()

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def _looks_like_html_page(data):
    """Best-effort detection for HTML fallback sources like AP News hub pages."""
    head = data[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _millis_to_iso(raw):
    """Convert AP's millisecond timestamps to ISO 8601 in UTC."""
    if not raw:
        return ""
    try:
        dt = datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return ""
    return dt.isoformat()


class APHubHTMLParser(HTMLParser):
    """Extract article title/link/timestamp from AP hub HTML when RSS is unavailable."""

    def __init__(self, limit=25):
        super().__init__()
        self.limit = limit
        self.items = []
        self._seen_links = set()
        self._in_promo = False
        self._promo_div_depth = 0
        self._in_title = False
        self._title_parts = []
        self._current = None

    @staticmethod
    def _class_tokens(attrs):
        raw = attrs.get("class", "")
        return {part.strip() for part in raw.split() if part.strip()}

    def _start_promo(self, attrs):
        self._in_promo = True
        self._promo_div_depth = 1
        self._in_title = False
        self._title_parts = []
        self._current = {
            "title": "",
            "link": "",
            "pubDate": _millis_to_iso(
                attrs.get("data-updated-date-timestamp")
                or attrs.get("data-posted-date-timestamp")
            ),
        }

    def _finish_promo(self):
        self._in_promo = False
        self._promo_div_depth = 0
        self._in_title = False
        if not self._current:
            return

        title = " ".join("".join(self._title_parts).split())
        link = (self._current.get("link") or "").strip()
        pub = (self._current.get("pubDate") or "").strip()
        if link.startswith("/"):
            link = f"https://apnews.com{link}"

        if (
            title
            and link
            and "/article/" in link
            and link not in self._seen_links
            and len(self.items) < self.limit
        ):
            self._seen_links.add(link)
            self.items.append({
                "source": "",
                "title": title,
                "link": link,
                "pubDate": pub,
            })

        self._current = None
        self._title_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = self._class_tokens(attrs)

        if not self._in_promo:
            if tag == "div" and "PagePromo" in classes:
                self._start_promo(attrs)
            return

        if tag == "div":
            self._promo_div_depth += 1

        if tag in {"h1", "h2", "h3", "h4"} and "PagePromo-title" in classes:
            self._in_title = True

        if tag == "a":
            href = (attrs.get("href") or "").strip()
            if href and not self._current["link"] and "/article/" in href:
                self._current["link"] = href

        if tag == "bsp-timestamp" and not self._current["pubDate"]:
            self._current["pubDate"] = _millis_to_iso(attrs.get("data-timestamp"))

    def handle_endtag(self, tag):
        if not self._in_promo:
            return

        if self._in_title and tag in {"h1", "h2", "h3", "h4"}:
            self._in_title = False

        if tag == "div":
            self._promo_div_depth -= 1
            if self._promo_div_depth <= 0:
                self._finish_promo()

    def handle_data(self, data):
        if self._in_promo and self._in_title and data.strip():
            self._title_parts.append(data)


def _parse_ap_html_fallback(data, name, limit=25):
    """Parse AP hub HTML pages that no longer return RSS XML."""
    try:
        text = data.decode("utf-8", errors="replace")
        parser = APHubHTMLParser(limit=limit)
        parser.feed(text)
        parser.close()
        for item in parser.items:
            item["source"] = name
        return parser.items
    except Exception as e:
        print(f"FAIL {name}: AP HTML fallback error: {e}", file=sys.stderr)
        return []


def fetch_feed(name, url, proxy=None, limit=25, max_retries=0, base_backoff=2.0):
    """Fetch and parse a single RSS/Atom feed with optional retry on 429/403/timeouts.

    Retry policy:
      - Retries only on retryable HTTP codes (403/408/425/429/5xx) and
        transient network errors (URLError / TimeoutError).
      - Exponential backoff: base_backoff * 2**attempt (+ jitter).
      - Honors `Retry-After` header when present (capped at 60s).
      - XML parse errors are NOT retried.
    """
    data = None
    attempt = 0
    last_exc = None

    while attempt <= max_retries:
        try:
            data = _fetch_once(url, proxy)
            break
        except Exception as e:
            last_exc = e
            retryable, retry_after = _is_retryable(e)
            if not retryable or attempt == max_retries:
                err_code = f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else type(e).__name__
                retry_note = f" (after {attempt} retries)" if attempt > 0 else ""
                print(f"FAIL {name}: {err_code}: {e}{retry_note}", file=sys.stderr)
                return []
            # Backoff: honor Retry-After if present, else exponential with jitter.
            if retry_after is not None:
                delay = min(retry_after, 60.0)
            else:
                delay = base_backoff * (2 ** attempt) + random.uniform(0, 1.0)
            err_code = f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else type(e).__name__
            print(
                f"RETRY {name}: {err_code}, waiting {delay:.1f}s "
                f"(attempt {attempt + 1}/{max_retries})",
                file=sys.stderr,
            )
            time.sleep(delay)
            attempt += 1

    if data is None:
        return []

    if "apnews.com" in url and _looks_like_html_page(data):
        items = _parse_ap_html_fallback(data, name, limit)
        if items:
            tag = f"OK {name}: {len(items)} items (AP HTML fallback)"
            if attempt > 0:
                tag += f" (recovered after {attempt} retries)"
            print(tag, file=sys.stderr)
            return items
        print(f"FAIL {name}: AP returned HTML page without parseable article cards", file=sys.stderr)
        return []

    items = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        print(f"FAIL {name}: XML parse error: {e}", file=sys.stderr)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall(".//item") or root.findall(".//atom:entry", ns)

    for entry in entries[:limit]:
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

    tag = f"OK {name}: {len(entries)} items"
    if attempt > 0:
        tag += f" (recovered after {attempt} retries)"
    print(tag, file=sys.stderr)
    return items


def filter_by_window(items, start, end, tz, allow_no_date=False):
    """Drop items whose pubDate is outside [start, end). Returns (kept, stats)."""
    kept = []
    stats = {"in_window": 0, "before": 0, "after": 0, "no_date": 0, "unparseable": 0}

    for it in items:
        raw = it.get("pubDate", "")
        dt = parse_pubdate(raw)
        if dt is None:
            if not raw:
                stats["no_date"] += 1
            else:
                stats["unparseable"] += 1
            if allow_no_date:
                it = dict(it)
                it["pubDate_iso"] = None
                kept.append(it)
            continue

        local = dt.astimezone(tz)
        if local < start:
            stats["before"] += 1
            continue
        if local >= end:
            stats["after"] += 1
            continue

        stats["in_window"] += 1
        it = dict(it)
        it["pubDate_iso"] = local.isoformat()
        kept.append(it)

    return kept, stats


def dedup_and_categorize(all_items, max_per_cat=8):
    """Deduplicate by title similarity and group into categories."""
    seen = set()
    categorized = defaultdict(list)

    for item in all_items:
        title = item["title"]
        if not title or len(title) < 10:
            continue
        # 去重 key：保留 unicode 字母/数字（含中日韩文字），仅剥离空白与标点。
        # 之前用 [^a-zA-Z0-9] 会把中文标题全部剥光，导致所有中文新闻被误判为同一条。
        key = re.sub(r"[\W_]+", "", title.lower(), flags=re.UNICODE)[:60]
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
            "pubDate": item.get("pubDate_iso") or item.get("pubDate", ""),
        })

    output = {}
    for cat in CATEGORY_ORDER:
        if cat in categorized:
            output[cat] = categorized[cat][:max_per_cat]
    return output


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch and categorize RSS news with strict date filtering",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--urls", help="Path to Newsboat urls file")
    parser.add_argument("--proxy", help="HTTP proxy URL (e.g. http://127.0.0.1:7890)")
    parser.add_argument("--max-per-category", type=int, default=8)
    parser.add_argument(
        "--retry-failed",
        type=int,
        default=0,
        metavar="N",
        help="Retry up to N times on 429/403/5xx/timeout errors with exponential backoff "
             "(default: 0 = no retry). Recommended: 2 or 3 for noisy sources like AP News / Fox News.",
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=2.0,
        metavar="SEC",
        help="Base backoff seconds between retries (default: 2.0). "
             "Actual wait = base * 2^attempt + jitter, capped by Retry-After if provided.",
    )

    date_group = parser.add_argument_group("date filter")
    date_group.add_argument(
        "--date",
        help="Fetch news for a specific date YYYY-MM-DD (window: 00:00 → next 00:00 in --tz)",
    )
    date_group.add_argument("--since", help="Start date YYYY-MM-DD (inclusive)")
    date_group.add_argument(
        "--until",
        help="End date YYYY-MM-DD (exclusive, defaults to --since + 1 day)",
    )
    date_group.add_argument(
        "--tz",
        default="America/New_York",
        help="IANA timezone for interpreting --date / --since / --until (default: America/New_York). "
             "Shortcuts: ET / PT / CT / MT / UTC / CN",
    )
    date_group.add_argument(
        "--allow-no-date",
        action="store_true",
        help="Also keep items whose pubDate is missing / unparseable (default: drop them)",
    )
    date_group.add_argument(
        "--no-date-filter",
        action="store_true",
        help="Disable date filtering entirely (legacy behavior)",
    )

    args = parser.parse_args()

    if args.date and (args.since or args.until):
        parser.error("--date cannot be combined with --since / --until")

    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    urls_path = args.urls or os.path.join(skill_dir, "urls-china")
    if not os.path.exists(urls_path):
        urls_path = os.path.expanduser("~/.newsboat/urls")

    tz = resolve_tz(args.tz)
    feeds = parse_urls_file(urls_path)
    print(f"Loaded {len(feeds)} feeds from {urls_path}", file=sys.stderr)

    all_items = []
    # Fetch more items per feed when date filtering, because the top-25 may not
    # all fall into the requested window (especially for busy feeds).
    per_feed_limit = 25 if args.no_date_filter else 60
    # When retries are enabled, lower concurrency a bit so a misbehaving host
    # (e.g. AP News 429'ing the whole pool) doesn't get hammered even during backoff.
    max_workers = 8 if args.retry_failed > 0 else 16
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                fetch_feed,
                name,
                url,
                args.proxy,
                per_feed_limit,
                args.retry_failed,
                args.retry_backoff,
            ): name
            for name, url in feeds.items()
        }
        for future in as_completed(futures):
            all_items.extend(future.result())

    if args.no_date_filter:
        print(f"\nDate filter: DISABLED", file=sys.stderr)
        kept = all_items
    else:
        start, end = resolve_window(args, tz)
        print(
            f"\nDate window: {start.isoformat()} → {end.isoformat()}",
            file=sys.stderr,
        )
        kept, stats = filter_by_window(all_items, start, end, tz, args.allow_no_date)
        print(
            "  in_window={in_window}  before={before}  after={after}  "
            "no_date={no_date}  unparseable={unparseable}".format(**stats),
            file=sys.stderr,
        )
        if stats["in_window"] == 0 and not args.allow_no_date:
            print(
                "WARN: 0 items in the requested window. "
                "RSS sources typically only expose the last ~24-48h — "
                "requesting a date older than ~2 days will likely return empty. "
                "Use --allow-no-date to keep items that lack timestamps.",
                file=sys.stderr,
            )

    result = dedup_and_categorize(kept, args.max_per_category)

    total = sum(len(v) for v in result.values())
    print(
        f"\nDone: {len(all_items)} raw → {len(kept)} in-window → "
        f"{total} categorized across {len(result)} topics",
        file=sys.stderr,
    )

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Generate reader-style news card images for Xiaohongshu.

Usage:
    python3 generate_cards.py -i news_cn.json -o ./cards/
    python3 fetch_news.py | python3 generate_cards.py -o ./cards/

Input JSON: { "分类名": [{"title": "...", "source": "..."}], ... }

Prerequisites:
    pip3 install playwright && playwright install chromium
"""

import json, sys, os, argparse
from datetime import datetime
from pathlib import Path
from html import escape as esc

W = 1080
MIN_H = 820
WEEKDAYS = "一二三四五六日"


def _today():
    n = datetime.now()
    return f"{n.year}年{n.month}月{n.day}日  周{WEEKDAYS[n.weekday()]}"


FONT = ('"Charter", "Georgia", "Noto Serif SC", "Songti SC", '
        '"Source Han Serif SC", "PingFang SC", serif')

RESET = f"""* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  width:{W}px;
  font-family: {FONT};
  background: #f8f4ed;
  color: #2c2c2c;
  -webkit-font-smoothing: antialiased;
}}
"""


def cover_html(data, date_str):
    items = ""
    for cat, entries in data.items():
        if not entries:
            continue
        t = esc(entries[0]["title"])
        if len(t) > 38:
            t = t[:37] + "…"
        items += f"""<div class="hl">
  <div class="hl-cat">{esc(cat)}</div>
  <div class="hl-t">{t}</div>
</div>\n"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
{RESET}
body {{ padding: 80px 72px 64px; }}
.title {{
  text-align: center; font-size: 40px; font-weight: 600;
  letter-spacing: 8px; color: #2c2c2c;
}}
.date {{
  text-align: center; font-size: 22px; color: #a09080;
  margin-top: 14px; letter-spacing: 1px;
}}
.rule {{
  border: none; border-top: 1px solid #ddd6cb;
  margin: 36px 0 32px;
}}
.hl {{ margin-bottom: 24px; line-height: 1.8; }}
.hl-cat {{
  font-size: 23px; font-weight: 600; color: #5a5040;
  margin-bottom: 2px;
}}
.hl-t {{ font-size: 23px; color: #6b6358; }}
.foot {{
  text-align: center; font-size: 19px; color: #c4b8a8;
  margin-top: 36px; letter-spacing: 3px;
}}
</style></head><body>
<div class="title">全球新闻速览</div>
<div class="date">{esc(date_str)}</div>
<hr class="rule">
{items}
<div class="foot">← 滑动查看详情 →</div>
</body></html>"""


def category_html(cat, items, page, total, date_str):
    rows = ""
    for i, it in enumerate(items[:8], 1):
        rows += f"""<div class="item">
  <div class="num">{i}</div>
  <div class="body">
    <div class="t">{esc(it['title'])}</div>
    <div class="src">{esc(it.get('source', ''))}</div>
  </div>
</div>\n"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
{RESET}
body {{ padding: 72px 72px 56px; }}
.cat {{ font-size: 34px; font-weight: 600; color: #2c2c2c; }}
.rule {{
  border: none; border-top: 1px solid #ddd6cb;
  margin: 24px 0 28px;
}}
.item {{ display: flex; gap: 14px; margin-bottom: 28px; }}
.num {{
  font-size: 21px; color: #c4b8a8; font-weight: 600;
  width: 28px; flex-shrink: 0; padding-top: 5px; text-align: right;
  font-family: "Georgia", serif;
}}
.body {{ flex: 1; }}
.t {{
  font-size: 25px; line-height: 1.8; color: #2c2c2c;
}}
.src {{
  font-size: 18px; color: #b0a594; margin-top: 4px;
  font-family: "Helvetica Neue", "PingFang SC", sans-serif;
}}
.foot {{
  display: flex; justify-content: space-between;
  font-size: 18px; color: #c4b8a8; margin-top: 32px;
}}
</style></head><body>
<div class="cat">{esc(cat)}</div>
<hr class="rule">
{rows}
<div class="foot">
  <span>{esc(date_str)}</span>
  <span>{page}/{total}</span>
</div>
</body></html>"""


def render(pages, out_dir):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("请先安装: pip3 install playwright && playwright install chromium",
              file=sys.stderr)
        sys.exit(1)

    if "sandbox-cache" in os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": W, "height": 2400}, device_scale_factor=2)

        for i, html in enumerate(pages):
            pg.set_content(html, wait_until="load")
            h = pg.evaluate("() => document.body.scrollHeight")
            h = max(MIN_H, h)
            pg.set_viewport_size({"width": W, "height": h})
            path = os.path.join(out_dir, f"card_{i:02d}.png")
            pg.screenshot(path=path, clip={"x": 0, "y": 0, "width": W, "height": h})
            print(f"  ✓ {path}  ({W}×{h})", file=sys.stderr)

        br.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", help="JSON file (default: stdin)")
    ap.add_argument("-o", "--output", default="./cards")
    ap.add_argument("--min-items", type=int, default=2)
    args = ap.parse_args()

    data = json.load(open(args.input) if args.input else sys.stdin)
    date_str = _today()

    cats = [(c, its) for c, its in data.items() if len(its) >= args.min_items]
    total = 1 + len(cats)

    pages = [cover_html(data, date_str)]
    for idx, (c, its) in enumerate(cats, 2):
        pages.append(category_html(c, its, idx, total, date_str))

    print(f"生成 {len(pages)} 张卡片 → {args.output}/", file=sys.stderr)
    render(pages, args.output)
    print(f"✓ 完成", file=sys.stderr)


if __name__ == "__main__":
    main()

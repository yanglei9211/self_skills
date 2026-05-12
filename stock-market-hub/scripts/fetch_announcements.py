#!/usr/bin/env python3
"""
公告抓取：巨潮（A股权威披露源） + 披露易（港股权威披露源）。

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
  # category: annual(年报) / quarterly(季报) / semi(半年报)
  #         / risk(风险提示) / bond(债券) / acquisition(并购重组)
  #         / shareholder(股东) / equity(股权激励) / pledge(质押)

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
from datetime import datetime, timedelta
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[2] / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from stock_core.cache import cached  # noqa: E402
from stock_core.http import fetch  # noqa: E402
from stock_core.tz import CN_TZ  # noqa: E402


# ============ 巨潮（A股） ============ #

CNINFO_CATEGORY = {
    "annual": "category_ndbg_szsh",       # 年度报告
    "semi": "category_bndbg_szsh",         # 半年报
    "quarterly": "category_yjdbg_szsh;category_sjdbg_szsh",  # 一季报+三季报
    "risk": "category_fxts_szsh",          # 风险提示
    "bond": "category_zj_szsh",            # 债券
    "acquisition": "category_zjjy_szsh",   # 并购重组
    "shareholder": "category_gddh_szsh",   # 股东大会
    "equity": "category_gqjl_szsh",        # 股权激励
    "pledge": "category_gqzy_szsh",        # 股权质押
    "performance": "category_yjygjxz_szsh",  # 业绩预告
    "irregular": "category_zf_szsh",       # 增发
}


def _parse_a_share_symbol(symbol: str) -> tuple[str, str]:
    """SZ300750 → (300750, szse); SH600519 → (600519, sse); BJ430047 → (430047, bj)"""
    s = symbol.upper().strip()
    if s.startswith("SZ"):
        return s[2:], "szse"
    if s.startswith("SH"):
        return s[2:], "sse"
    if s.startswith("BJ"):
        return s[2:], "bj"
    # 自动猜：6 开头 → sse，0/3 开头 → szse，4/8 开头 → bj
    digits = re.sub(r"\D", "", s)
    if digits.startswith("6"):
        return digits, "sse"
    if digits.startswith(("0", "3")):
        return digits, "szse"
    if digits.startswith(("4", "8")):
        return digits, "bj"
    raise ValueError(f"无法识别 A 股代码：{symbol}")


@cached(ttl=30 * 24 * 3600, key_prefix="orgid")  # 内部 ID 几乎不变
def cninfo_lookup_orgid(stock_code: str) -> str | None:
    """巨潮股票内部 orgId 查询。"""
    r = fetch(
        "http://www.cninfo.com.cn/new/information/topSearch/query",
        method="POST",
        data={"keyWord": stock_code, "maxNum": 5},
    )
    try:
        items = r.json()
    except Exception:
        return None
    for it in items:
        if it.get("code") == stock_code:
            return it.get("orgId")
    return None


def cninfo_announcements(
    stock_code: str | None = None,
    keyword: str | None = None,
    category: str | None = None,
    days: int = 30,
    page_size: int = 30,
    column: str = "szse",
) -> list[dict]:
    """巨潮公告查询。stock_code 和 keyword 二选一或并用。"""
    se_to = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    se_from = (datetime.now(CN_TZ) - timedelta(days=days)).strftime("%Y-%m-%d")

    data: dict = {
        "tabName": "fulltext",
        "pageSize": page_size,
        "pageNum": 1,
        "column": column,
        "seDate": f"{se_from}~{se_to}",
    }
    if category:
        data["category"] = CNINFO_CATEGORY.get(category, category)
    if keyword:
        data["searchkey"] = keyword

    if stock_code:
        org_id = cninfo_lookup_orgid(stock_code)
        if not org_id:
            print(f"[cninfo] 未找到 {stock_code} 的 orgId", file=sys.stderr)
            return []
        data["stock"] = f"{stock_code},{org_id}"

    r = fetch(
        "http://www.cninfo.com.cn/new/hisAnnouncement/query",
        method="POST",
        data=data,
        timeout=15,
    )
    try:
        payload = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[cninfo] JSON 解析失败: {e}", file=sys.stderr)
        return []

    items = []
    for ann in (payload.get("announcements") or []):
        ts_ms = ann.get("announcementTime")
        try:
            dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=CN_TZ)
            date_str = dt.strftime("%Y-%m-%d")
        except Exception:
            date_str = "?"
        adj = ann.get("adjunctUrl") or ""
        pdf_url = f"http://static.cninfo.com.cn/{adj}" if adj else ""
        items.append({
            "symbol": ann.get("secCode"),
            "name": ann.get("secName"),
            "title": ann.get("announcementTitle"),
            "date": date_str,
            "pdf_url": pdf_url,
            "category": ann.get("announcementType") or "",
            "source": "巨潮",
        })
    return items


# ============ 披露易（港股） ============ #

@cached(ttl=30 * 24 * 3600, key_prefix="hkex_id")
def hkex_lookup_stock_id(stock_code: str) -> int | None:
    """披露易内部 stockId 查询（关键步骤！直接用 stock_code 查不到结果）。

    例：'00700' → 7609 (腾讯控股)
    """
    code = stock_code.lstrip("0").zfill(5) if stock_code else ""
    r = fetch(
        "https://www1.hkexnews.hk/search/prefix.do",
        params={"callback": "cb", "lang": "ZH", "type": "A", "name": code, "market": "SEHK"},
    )
    m = re.search(r"callback\((.*)\)", r.text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        infos = data.get("stockInfo") or []
        if not infos:
            return None
        # 选 code 完全匹配的（可能有多个同名公司）
        for info in infos:
            if str(info.get("code", "")).lstrip("0") == code.lstrip("0"):
                return info.get("stockId")
        return infos[0].get("stockId")
    except Exception:
        return None


def hkex_announcements(
    stock_code: str,
    days: int = 30,
    rows: int = 50,
) -> list[dict]:
    """披露易公告查询。stock_code 例：'00700'（5 位补零）。

    流程：
      1. prefix.do  把 5 位股票代码 → 披露易内部 stockId
      2. titleSearchServlet.do  用 stockId 查公告
    """
    today = datetime.now(CN_TZ)
    from_date = (today - timedelta(days=days)).strftime("%Y%m%d")
    to_date = today.strftime("%Y%m%d")

    stock_id = hkex_lookup_stock_id(stock_code)
    if not stock_id:
        print(f"[hkex] 未找到 {stock_code} 的披露易内部 stockId", file=sys.stderr)
        return []

    params = {
        "lang": "ZH",
        "category": "0",
        "market": "SEHK",
        "searchType": "1",
        "documentType": "-1",
        "fromDate": from_date,
        "toDate": to_date,
        "stockId": str(stock_id),
        "rowRange": str(rows),
        "t1code": "-2",
        "t2Gcode": "-2",
        "t2code": "-2",
    }
    r = fetch(
        "https://www1.hkexnews.hk/search/titleSearchServlet.do",
        params=params,
        timeout=15,
    )
    try:
        payload = r.json()
        rows_data = json.loads(payload.get("result", "[]") or "[]")
    except Exception as e:  # noqa: BLE001
        print(f"[hkex] 解析失败: {e}", file=sys.stderr)
        return []

    def _strip_html(s: str) -> str:
        s = re.sub(r"<[^>]+>", " ", s or "")
        return re.sub(r"\s+", " ", s).strip()

    items = []
    for row in rows_data:
        date_raw = row.get("DATE_TIME") or ""  # "30/04/2026 16:31"
        date_iso = "?"
        try:
            d = datetime.strptime(date_raw, "%d/%m/%Y %H:%M")
            date_iso = d.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        file_link = row.get("FILE_LINK") or ""
        pdf_url = f"https://www1.hkexnews.hk{file_link}" if file_link.startswith("/") else file_link
        # STOCK_CODE 字段含 "00700<br/>80700"，取第一个
        sc = (row.get("STOCK_CODE") or "").split("<")[0].strip()
        sn = _strip_html(row.get("STOCK_NAME") or "").split(" ")[0]
        short_text = _strip_html(row.get("SHORT_TEXT") or "")
        cat = ""
        m = re.search(r"\[([^\]]+)\]", short_text)
        if m:
            cat = m.group(1)
        items.append({
            "symbol": "HK" + sc.zfill(5),
            "name": sn,
            "title": _strip_html(row.get("TITLE") or ""),
            "date": date_iso,
            "pdf_url": pdf_url,
            "category": cat,
            "source": "披露易",
        })
    return items


# ============ 主流程 ============ #

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

    # 计算 days
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
        # 港股
        code = re.sub(r"\D", "", sym)
        try:
            items = hkex_announcements(code, days=args.days, rows=args.limit)
        except Exception as e:  # noqa: BLE001
            print(f"[fetch_announcements] 披露易抓取失败: {type(e).__name__}: {e}", file=sys.stderr)
            print("[fetch_announcements] 提示：披露易接口不稳定，可重试或减少 --days", file=sys.stderr)
            items = []
    elif sym and not sym.startswith(("US:", "BABA", "JD", "PDD", "BIDU")):
        # A 股
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
        # 关键词搜索
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

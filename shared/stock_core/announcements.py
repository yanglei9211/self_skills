"""
公告抓取共享层：巨潮（A 股权威披露源）+ 披露易（港股权威披露源）。

这里只放可被多个 skill 复用的纯抓取/解析逻辑。
CLI 入口 + argparse 调度保留在各自的 hub 脚本里（`stock-market-hub/scripts/fetch_announcements.py`），
那个脚本现在只做"参数解析 → 调本模块函数 → 打印"的薄壳。

公共 API：
  - cninfo_announcements        巨潮公告查询（按代码 / 关键词 / 类别）
  - cninfo_lookup_orgid         巨潮内部 orgId 查询（30 天缓存）
  - hkex_announcements          披露易公告查询（按 5 位股票代码）
  - hkex_lookup_stock_id        披露易内部 stockId 查询（30 天缓存）
  - _parse_a_share_symbol       SZ300750 → ('300750', 'szse') 解析
  - CNINFO_CATEGORY             巨潮 category 名 → 实际 category 值的映射
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta

from stock_core.cache import cached
from stock_core.http import fetch
from stock_core.tz import CN_TZ


# ============ 巨潮（A 股） ============ #

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

"""
SEC EDGAR 客户端：中概股 / 美股财务数据（免费官方 API）。

接口：
  - https://www.sec.gov/files/company_tickers.json   ticker → CIK 映射
  - https://data.sec.gov/submissions/CIK{cik}.json    公司基本信息 + 历史 filings
  - https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json
                                                      指定财务指标的历年数据
  - https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json  全部财务数据

合规要求：
  SEC 规定 User-Agent 必须包含联系方式（email）。
  通过环境变量 SEC_USER_AGENT 配置；默认用一个保守的 stock-market-hub 标识。

NOTE：
  - 中概股年报通常是 20-F（外国发行人年报）而非 10-K
  - 季度报告：10-Q（境内）/ 6-K（外国发行人，含季报+其他重要事项）
"""
from __future__ import annotations

import json
import os
import sys
from typing import Iterable

from .http import fetch  # type: ignore
from .cache import cached  # type: ignore


SEC_UA = os.environ.get(
    "SEC_USER_AGENT",
    "stock-market-hub research/1.0 (please-set-SEC_USER_AGENT-env)",
)

_HEADERS = {
    "User-Agent": SEC_UA,
    "Accept-Encoding": "gzip, deflate",
    "Host-needed": "data.sec.gov",
}


# ============ ticker → CIK ============ #

@cached(ttl=7 * 24 * 3600, key_prefix="sec_tickers")
def _fetch_ticker_map() -> dict:
    """SEC 官方 ticker → CIK 映射表。每周更新一次足够。"""
    r = fetch(
        "https://www.sec.gov/files/company_tickers.json",
        headers=_HEADERS, timeout=20,
    )
    if r.status_code != 200:
        return {}
    raw = r.json()
    out = {}
    for _k, v in raw.items():
        ticker = (v.get("ticker") or "").upper()
        if ticker:
            out[ticker] = {
                "cik": str(v.get("cik_str") or "").zfill(10),
                "title": v.get("title"),
            }
    return out


def ticker_to_cik(ticker: str) -> str | None:
    """ticker → 10 位 CIK 编号。例：'BABA' → '0001577552'"""
    m = _fetch_ticker_map()
    info = m.get(ticker.upper())
    return info["cik"] if info else None


# ============ 公司提交记录（含 filings） ============ #

@cached(ttl=4 * 3600, key_prefix="sec_subs")
def get_submissions(cik: str) -> dict:
    """返回公司基本信息 + 历史 filings（最近）。"""
    cik = cik.zfill(10)
    r = fetch(
        f"https://data.sec.gov/submissions/CIK{cik}.json",
        headers=_HEADERS, timeout=15,
    )
    if r.status_code != 200:
        return {}
    return r.json()


def get_recent_filings(
    cik: str,
    forms: Iterable[str] = ("10-K", "10-Q", "20-F", "6-K", "8-K", "4"),
    limit: int = 30,
) -> list[dict]:
    """提取最近的 filings，按日期倒序。"""
    sub = get_submissions(cik)
    recent = sub.get("filings", {}).get("recent", {}) or {}
    if not recent:
        return []
    forms_set = {f.upper() for f in forms}
    n = len(recent.get("form") or [])
    items = []
    cik_int = int(cik)
    for i in range(n):
        form = recent["form"][i]
        if form.upper() not in forms_set:
            continue
        accession = recent["accessionNumber"][i]
        primary = recent.get("primaryDocument", [""] * n)[i]
        items.append({
            "form": form,
            "filing_date": recent["filingDate"][i],
            "report_date": recent.get("reportDate", [""] * n)[i],
            "accession": accession,
            "primary_doc": primary,
            "url": (
                f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
                f"{accession.replace('-', '')}/{primary}"
            ),
            "description": recent.get("primaryDocDescription", [""] * n)[i],
        })
        if len(items) >= limit:
            break
    return items


# ============ XBRL 财务数据 ============ #

# 常用财务指标（us-gaap / ifrs-full）
COMMON_CONCEPTS = {
    "Revenues": ("营业收入", "us-gaap"),
    "RevenueFromContractWithCustomerExcludingAssessedTax": ("营业收入(新口径)", "us-gaap"),
    "GrossProfit": ("毛利润", "us-gaap"),
    "OperatingIncomeLoss": ("营业利润", "us-gaap"),
    "NetIncomeLoss": ("净利润", "us-gaap"),
    "EarningsPerShareBasic": ("基本 EPS", "us-gaap"),
    "EarningsPerShareDiluted": ("稀释 EPS", "us-gaap"),
    "Assets": ("总资产", "us-gaap"),
    "Liabilities": ("总负债", "us-gaap"),
    "StockholdersEquity": ("股东权益", "us-gaap"),
    "CashAndCashEquivalentsAtCarryingValue": ("现金及等价物", "us-gaap"),
    "OperatingCashFlowsContinuingOperations": ("经营性现金流", "us-gaap"),
    "ResearchAndDevelopmentExpense": ("研发费用", "us-gaap"),
}


@cached(ttl=24 * 3600, key_prefix="sec_concept")
def get_concept(cik: str, concept: str, taxonomy: str = "us-gaap") -> dict:
    """拿单个财务指标的历年数据。"""
    cik = cik.zfill(10)
    r = fetch(
        f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{concept}.json",
        headers=_HEADERS, timeout=15, retries=1,
    )
    if r.status_code != 200:
        return {}
    return r.json()


def get_annual_revenues(cik: str, years: int = 5) -> list[dict]:
    """拿历年年度营收（FY），返回 [{end, fy, val, unit}]"""
    out = []
    # 优先用 RevenueFromContractWithCustomerExcludingAssessedTax（新会计准则）
    for concept in [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ]:
        data = get_concept(cik, concept)
        if not data:
            continue
        units = data.get("units", {})
        for unit, rows in units.items():
            annuals = [r for r in rows if r.get("fp") == "FY" or r.get("form") in ("10-K", "20-F")]
            if annuals:
                # 去重（同一财年可能有多条）
                seen = {}
                for r in annuals:
                    end = r.get("end")
                    if end and (end not in seen or r.get("filed", "") > seen[end].get("filed", "")):
                        seen[end] = {**r, "unit": unit, "concept": concept}
                out = sorted(seen.values(), key=lambda x: x.get("end", ""), reverse=True)[:years]
                return out
    return out


def get_company_summary(cik: str) -> dict:
    """汇总公司核心财务（最近年度营收/净利润/EPS/资产 等）"""
    summary = {}
    for concept, (label, tax) in COMMON_CONCEPTS.items():
        data = get_concept(cik, concept, tax)
        if not data:
            continue
        units = data.get("units", {})
        for unit, rows in units.items():
            annuals = [
                r for r in rows
                if r.get("fp") == "FY" or r.get("form") in ("10-K", "20-F")
            ]
            if annuals:
                # 取最近 5 年
                seen = {}
                for r in annuals:
                    end = r.get("end")
                    if end and (end not in seen or r.get("filed", "") > seen[end].get("filed", "")):
                        seen[end] = r
                top = sorted(seen.values(), key=lambda x: x.get("end", ""), reverse=True)[:5]
                summary[label] = {
                    "unit": unit,
                    "concept": concept,
                    "values": [
                        {"end": r.get("end"), "fy": r.get("fy"), "val": r.get("val")}
                        for r in top
                    ],
                }
                break
    return summary


# ============ 高级封装 ============ #

def get_company_card(ticker: str) -> dict:
    """中概股/美股公司一站式：基本信息 + 最近 filings + 财务摘要。"""
    cik = ticker_to_cik(ticker)
    if not cik:
        return {"error": f"未找到 {ticker} 的 CIK"}
    sub = get_submissions(cik)
    base = {
        "ticker": ticker.upper(),
        "cik": cik,
        "name": sub.get("name"),
        "name_cn": "",  # 雪球的 name 在 quote 里有
        "sic": sub.get("sic"),
        "industry": sub.get("sicDescription"),
        "category": sub.get("category"),
        "fiscal_year_end": sub.get("fiscalYearEnd"),
        "exchanges": sub.get("exchanges"),
        "addresses": sub.get("addresses"),
        "website": sub.get("website"),
        "phone": sub.get("phone"),
        "ein": sub.get("ein"),
        "former_names": sub.get("formerNames"),
    }
    base["recent_filings"] = get_recent_filings(cik, limit=15)
    base["financial_summary"] = get_company_summary(cik)
    return base

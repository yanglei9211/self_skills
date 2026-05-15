#!/usr/bin/env python3
"""
公司深度卡片：A股 / 港股 / 中概股 公司信息一站式聚合。

数据维度：
  - 行情快照 + 估值指标   (雪球 screener / quotec)
  - 公司基本信息          (新浪 corp/CorpInfo)
  - 核心高管              (新浪 corp/CorpManager)
  - 主要股东              (新浪 corp/StockHolder)
  - 概念归属              (同花顺 basic concept)
  - 近 N 天关键公告       (巨潮 / 披露易)

Usage:
  # A 股
  python3 analyze_company.py --symbol SZ300750
  python3 analyze_company.py --symbol SH600519

  # 港股
  python3 analyze_company.py --symbol HK00700

  # 中概股（功能受限，只有行情）
  python3 analyze_company.py --symbol BABA

  # 控制详细度
  python3 analyze_company.py --symbol SZ300750 --top-managers 8 --top-holders 10 --ann-days 60
  python3 analyze_company.py --symbol SZ300750 --skip announcements,concepts  # 跳过某些维度

  # 输出格式（默认 json，给 agent 用）
  python3 analyze_company.py --symbol SZ300750 --format text
  python3 analyze_company.py --symbol SZ300750 --format json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


def _ensure_hub_scripts_path() -> None:
    """让本模块在需要时能 ``from supply_chain import get_peers_from_concept``。

    历史包袱：`supply_chain.get_peers_from_concept` 内部跟 `scan_sector` 私有函数
    （`code_to_xueqiu` / `_get_board_constituents_em` / `_format_peers`）耦合，
    现阶段保留在 ``stock-market-hub/scripts/`` 下，shared 层暂未拆。等 supply_chain
    重构时一并搬过来，这个 sys.path 注入就可以删了。

    底层基础设施（announcements / http / xueqiu / cache / kline / sec_edgar / ...）
    已经全部搬到 ``shared/stock_core/``，无需再走 hub scripts 路径。
    """
    current = Path(__file__).resolve()
    repo_root = current.parents[2]
    hub_scripts = repo_root / "stock-market-hub" / "scripts"
    if str(hub_scripts) not in sys.path:
        sys.path.insert(0, str(hub_scripts))


_ensure_hub_scripts_path()

from stock_core.announcements import (
    cninfo_announcements,
    hkex_announcements,
    _parse_a_share_symbol,
)
from stock_core.cache import cached
from stock_core.enrichment import (
    fetch_sector_strength,
    fetch_stock_news_relevance,
    fetch_xueqiu_attention,
)
from stock_core.fund_flow import (
    get_fund_flow_summary,
    regime_label,
    reversal_label,
)
from stock_core.http import fetch
from stock_core.kline import fetch_daily_kline, summarize_price_history
from stock_core.symbols import normalize_symbol  # re-exported below for backward compat
from stock_core.tz import CN_TZ
from stock_core.xueqiu import XueqiuClient


__all__ = ["analyze", "render_text", "main", "normalize_symbol"]



# ============ 行情 / 估值 ============ #

def get_quote(symbol: str, market: str, xq_sym: str) -> dict:
    """雪球 quotec + screener 综合"""
    cli = XueqiuClient()
    out = {}
    try:
        qs = cli.quotes([xq_sym])
        if qs:
            q = qs[0]
            out.update({
                "current": q.get("current"),
                "percent": q.get("percent"),
                "chg": q.get("chg"),
                "high": q.get("high"),
                "low": q.get("low"),
                "open": q.get("open"),
                "last_close": q.get("last_close"),
                "amount": q.get("amount"),
                "volume": q.get("volume"),
                "turnover_rate": q.get("turnover_rate"),
                "market_capital": q.get("market_capital"),
                "float_market_capital": q.get("float_market_capital"),
                "current_year_percent": q.get("current_year_percent"),
            })
    except Exception as e:  # noqa: BLE001
        print(f"[quote] xueqiu quotec failed: {e}", file=sys.stderr)

    # 估值指标只对 A股+港股有，且要走 screener
    # 这里偷懒：给一个对应的市场，按 symbol 取该 market 的全榜单第一个匹配
    # 更稳妥的做法：scoreener 提供按 symbol 筛选的 sym_filter 参数
    return out


# ============ 同业横向对比 ============ #

def _get_peers(xq_sym: str, top: int = 8) -> list[dict]:
    """同业（同概念）公司横向对比：含 PE/PB/ROE/营收增速 等。

    ⚠️ 历史包袱：``get_peers_from_concept`` 内部跟 hub 私有模块
       （scan_sector.code_to_xueqiu / supply_chain._get_board_constituents_em / _format_peers）
       耦合，暂不搬到 shared。这里通过 ``_ensure_hub_scripts_path`` 注入
       hub scripts 路径后 try-import，拿不到就静默退化（peers 字段为空，
       不会阻塞 analyze_company 主流程）。下一轮 supply_chain 重构时把
       peers 抽到 shared/stock_core/peers.py，这段就可以删了。
    """
    try:
        from supply_chain import get_peers_from_concept  # type: ignore
    except ImportError as e:
        print(f"[peers] import failed (peers 字段将为空，不影响主分析): {e}", file=sys.stderr)
        return []
    try:
        return get_peers_from_concept(xq_sym, top=top)
    except Exception as e:  # noqa: BLE001
        print(f"[peers] failed: {e}", file=sys.stderr)
        return []


# ============ K 线 / 历年高低 ============ #

def _get_price_history(market: str, code: str, xq_sym: str, args) -> dict:
    """拉日 K + 摘要。current_price 优先用 quote 的最新价，但这里没法跨任务传，
    退一步用 K 线最后一根 close。"""
    if market == "a":
        kline_sym = xq_sym  # SH600519 / SZ300750
    elif market == "hk":
        kline_sym = code
    elif market == "us":
        kline_sym = code
    else:
        return {}
    kline = fetch_daily_kline(kline_sym, market, count=getattr(args, "kline_count", 1500))
    if not kline:
        return {"error": "K 线获取失败"}
    return summarize_price_history(kline, current_price=kline[-1]["close"])


# ============ 主力资金流 ============ #

def _get_fund_flow(market: str, code: str) -> dict:
    """薄包装：交给 :func:`stock_core.fund_flow.get_fund_flow_summary` 处理具体抓取与摘要。"""
    return get_fund_flow_summary(market, code)


# ============ A股 公司基本信息 / 高管 / 股东 ============ #

def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


@cached(ttl=24 * 3600, key_prefix="info_a")
def get_a_company_info(code: str) -> dict:
    """新浪 vCI_CorpInfo"""
    from lxml import html
    url = f"https://money.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/{code}.phtml"
    r = fetch(url)
    r.encoding = "gb2312"
    tree = html.fromstring(r.text)
    info = {}
    for table in tree.xpath("//table[@id='comInfo1']"):
        for tr in table.xpath(".//tr"):
            tds = [_strip(td.text_content()) for td in tr.xpath("./td")]
            # 一行可能是 [k1, v1, k2, v2]
            i = 0
            while i + 1 < len(tds):
                key = tds[i].rstrip("：:")
                val = tds[i + 1]
                if key and val and val not in ("--", "查看变化趋势"):
                    info[key] = val
                i += 2
    return info


@cached(ttl=24 * 3600, key_prefix="mgr_a")
def get_a_managers(code: str, top: int = 10) -> list[dict]:
    """新浪 vCI_CorpManager 高管列表"""
    from lxml import html
    url = f"https://money.finance.sina.com.cn/corp/go.php/vCI_CorpManager/stockid/{code}.phtml"
    r = fetch(url)
    r.encoding = "gb2312"
    tree = html.fromstring(r.text)
    out = []
    seen = set()
    # 找含表头"姓 名"或"姓名"的 table
    for t in tree.xpath("//table"):
        head = "".join(_strip(td.text_content()) for td in t.xpath(".//tr[1]/td"))
        if "姓" not in head or "职" not in head:
            continue
        for tr in t.xpath(".//tr[position()>1]"):
            tds = [_strip(td.text_content()) for td in tr.xpath("./td")]
            if len(tds) >= 4:
                name = tds[0]
                if not name or name in seen or name.startswith(("第", "起始")):
                    continue
                seen.add(name)
                out.append({
                    "name": name,
                    "title": tds[1],
                    "start_date": tds[2],
                    "end_date": tds[3] if tds[3] != "--" else "在任",
                })
                if len(out) >= top:
                    break
        if len(out) >= top:
            break
    return out


@cached(ttl=24 * 3600, key_prefix="holder_a")
def get_a_shareholders(code: str, top: int = 10) -> list[dict]:
    """新浪 vCI_StockHolder 主要股东 + 流通股东"""
    from lxml import html
    url = f"https://money.finance.sina.com.cn/corp/go.php/vCI_StockHolder/stockid/{code}.phtml"
    r = fetch(url)
    r.encoding = "gb2312"
    tree = html.fromstring(r.text)
    out = []
    # 通常多个表：股东数、十大股东、十大流通股东
    for t in tree.xpath("//table"):
        head = "".join(_strip(td.text_content()) for td in t.xpath(".//tr[1]/td")[:5])
        if "股东名称" not in head and "持股数" not in head and "持股" not in head:
            continue
        rows = t.xpath(".//tr[position()>1]")
        for tr in rows[:top]:
            tds = [_strip(td.text_content()) for td in tr.xpath("./td")]
            if len(tds) < 3:
                continue
            out.append({
                "name": tds[1] if len(tds) > 1 else "",
                "shares": tds[2] if len(tds) > 2 else "",
                "ratio": tds[3] if len(tds) > 3 else "",
                "type": tds[4] if len(tds) > 4 else "",
            })
        if out:
            break
    return out


# ============ 港股 ============ #

# 港股 emweb 字段中文映射
_HK_ZQZL_MAP = {
    "zqdm": "港股代码", "zqjc": "公司简称", "ssrq": "上市日期",
    "zqlx": "证券类型", "jys": "交易所", "bk": "板块",
    "mgmz": "每股面值", "zxjydw": "最小交易单位",
    "isin": "ISIN", "sfhgtbd": "沪港通", "sfsgtbd": "深港通",
}
_HK_GSZL_MAP = {
    "gsmc": "公司名称", "ywmc": "公司英文名称",
    "zcd": "注册地", "zcdz": "注册地址",
    "gsclrq": "成立日期", "bgdz": "办公地址",
    "dsz": "董事长", "gsms": "公司秘书",
    "gswz": "公司网址", "zczb": "注册资本",
    "njr": "财年截止日", "email": "联系邮箱",
    "ygrs": "员工人数", "lxdh": "联系电话",
    "hss": "审计师", "sshy": "所属行业",
    "gsjs": "公司简介",
}


@cached(ttl=24 * 3600, key_prefix="info_hk")
def get_hk_company_info(code: str) -> dict:
    """港股公司信息：东方财富 PC_HKF10 + 雪球行情兜底。

    code: 5 位补零代码（如 '00700'）
    """
    info: dict = {}
    # 1. 主源：东方财富 emweb
    try:
        url = f"https://emweb.securities.eastmoney.com/PC_HKF10/CompanyProfile/PageAjax?code={code}"
        r = fetch(url, retries=1, timeout=10)
        if r.status_code == 200:
            data = r.json()
            zqzl = data.get("zqzl") or {}
            gszl = data.get("gszl") or {}
            for k, label in _HK_ZQZL_MAP.items():
                v = zqzl.get(k)
                if v not in (None, "", "--"):
                    if k == "ssrq" and isinstance(v, str):
                        v = v.split(" ")[0].replace("/", "-")
                    info[label] = v
            for k, label in _HK_GSZL_MAP.items():
                v = gszl.get(k)
                if v not in (None, "", "--"):
                    if k == "gsjs" and isinstance(v, str):
                        v = v.strip()
                    info[label] = v
    except Exception as e:  # noqa: BLE001
        print(f"[hk_info] eastmoney F10 failed: {e}", file=sys.stderr)

    # 2. 兜底：雪球行情快照（即使主源失败也至少有现价/市值）
    cli = XueqiuClient()
    try:
        qs = cli.quotes([code])
        if qs:
            q = qs[0]
            info.setdefault("港股代码", code)
            info["现价(HKD)"] = q.get("current")
            info["总市值(HKD)"] = f"{(q.get('market_capital') or 0)/1e8:.0f}亿"
    except Exception:
        pass
    return info


# ============ 美股 / 中概股 (SEC EDGAR) ============ #

def get_us_company_info(ticker: str) -> dict:
    """从 SEC EDGAR 拿美股公司基本信息。"""
    try:
        from stock_core.sec_edgar import ticker_to_cik, get_submissions
    except ImportError:
        return {}
    cik = ticker_to_cik(ticker)
    if not cik:
        return {"warning": f"未找到 {ticker} 的 SEC CIK"}
    sub = get_submissions(cik)
    if not sub:
        return {}
    addresses = sub.get("addresses") or {}
    biz = addresses.get("business", {}) if isinstance(addresses, dict) else {}
    out = {
        "公司名称": sub.get("name"),
        "Ticker": ticker.upper(),
        "CIK": cik,
        "SIC 行业代码": sub.get("sic"),
        "所属行业": sub.get("sicDescription"),
        "filer 类别": sub.get("category"),
        "财年截止月": sub.get("fiscalYearEnd"),
        "交易所": ",".join(sub.get("exchanges") or []),
        "公司网址": sub.get("website"),
        "公司电话": sub.get("phone"),
        "EIN": sub.get("ein"),
        "曾用名": ",".join(
            n.get("name") for n in (sub.get("formerNames") or []) if n.get("name")
        ) or None,
    }
    if biz:
        addr = ", ".join(
            str(v) for v in (
                biz.get("street1"), biz.get("street2"),
                biz.get("city"), biz.get("stateOrCountry"), biz.get("zipCode"),
            ) if v
        )
        if addr:
            out["办公地址"] = addr
    return {k: v for k, v in out.items() if v not in (None, "", "None")}


def get_us_filings(ticker: str, limit: int = 20) -> list[dict]:
    """SEC 最近 N 份 filings。"""
    try:
        from stock_core.sec_edgar import ticker_to_cik, get_recent_filings
    except ImportError:
        return []
    cik = ticker_to_cik(ticker)
    if not cik:
        return []
    raw = get_recent_filings(
        cik,
        forms=("10-K", "10-Q", "20-F", "6-K", "8-K", "DEF 14A", "S-1"),
        limit=limit,
    )
    return [
        {
            "symbol": ticker.upper(),
            "name": "",
            "title": f"{f['form']} - {f.get('description','')}".strip(" -"),
            "date": f["filing_date"],
            "pdf_url": f["url"],
            "category": f["form"],
            "source": "SEC EDGAR",
        }
        for f in raw
    ]


def get_us_financial_summary(ticker: str) -> dict:
    """SEC XBRL 拿历年财务指标摘要。"""
    try:
        from stock_core.sec_edgar import ticker_to_cik, get_company_summary
    except ImportError:
        return {}
    cik = ticker_to_cik(ticker)
    if not cik:
        return {}
    return get_company_summary(cik)


# ============ 概念归属（同花顺） ============ #

@cached(ttl=24 * 3600, key_prefix="concept_a")
def get_a_concepts(code: str, top: int = 20) -> list[str]:
    """A 股所属板块 + 核心题材（东方财富 emweb F10）。

    返回示例：['电池', '锂电池', '电力设备', '新能源车', '储能', ...]
    """
    # 东财代码格式：300750 → SZ300750（深市）/ SH600519（沪市）/ BJ430047
    if code.startswith("6"):
        em_code = f"SH{code}"
    elif code.startswith(("4", "8")):
        em_code = f"BJ{code}"
    else:
        em_code = f"SZ{code}"
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax?code={em_code}"
    try:
        r = fetch(url, retries=1, timeout=10)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[concepts] eastmoney F10 failed: {e}", file=sys.stderr)
        return []
    out: list[str] = []
    seen = set()
    # 所属板块（行业 + 概念 + 地域 + 风格），按 BOARD_RANK 已有序
    for item in (data.get("ssbk") or []):
        n = (item.get("BOARD_NAME") or "").strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    # 核心题材（题材是公司业务侧重点，更精准但可能少）
    for item in (data.get("hxtc") or []):
        n = (item.get("CONCEPT_NAME") or item.get("KEYWORD") or "").strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out[:top]


def get_a_company_info_em(code: str) -> dict:
    """东财 F10 公司资料（含英文名 / 成立日期 / 上市日期 / 发行价等）。"""
    if code.startswith("6"):
        em_code = f"SH{code}"
    elif code.startswith(("4", "8")):
        em_code = f"BJ{code}"
    else:
        em_code = f"SZ{code}"
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={em_code}"
    try:
        r = fetch(url, retries=1, timeout=10)
        data = r.json()
    except Exception:
        return {}
    out: dict = {}
    jbzl = (data.get("jbzl") or [{}])[0]
    fxxg = (data.get("fxxg") or [{}])[0]
    if jbzl:
        out.update({
            "公司名称": jbzl.get("ORG_NAME"),
            "公司英文名称": jbzl.get("ORG_NAME_EN"),
            "曾用名": jbzl.get("FORMERNAME"),
            "组织代码": jbzl.get("ORG_CODE"),
        })
    if fxxg:
        for k, v in fxxg.items():
            if v in (None, ""):
                continue
            if k == "FOUND_DATE":
                out["成立日期"] = (v or "")[:10]
            elif k == "LISTING_DATE":
                out["上市日期"] = (v or "")[:10]
            elif k == "AFTER_ISSUE_PE":
                out["发行市盈率"] = v
            elif k == "PAR_VALUE":
                out["每股面值"] = v
            elif k == "ISSUE_WAY":
                out["发行方式"] = v
    return {k: v for k, v in out.items() if v not in (None, "")}


# ============ 公告 ============ #

def get_recent_announcements(market: str, code: str, xq_sym: str, days: int = 30, limit: int = 15) -> list[dict]:
    """统一公告入口：直接走 shared 层 announcements 模块（不再反向依赖 hub）。"""
    if market == "a":
        try:
            stock_code, column = _parse_a_share_symbol(xq_sym)
        except ValueError:
            return []
        return cninfo_announcements(stock_code=stock_code, days=days, page_size=limit, column=column)
    elif market == "hk":
        try:
            return hkex_announcements(code, days=days, rows=limit)
        except Exception as e:  # noqa: BLE001
            print(f"[announcements] hkex failed: {e}", file=sys.stderr)
            return []
    else:
        # 中概股暂不支持
        return []


# ============ 主流程 ============ #

def analyze(symbol: str, args: argparse.Namespace) -> dict:
    market, code, xq_sym = normalize_symbol(symbol)
    skip = {s.strip() for s in (args.skip or "").split(",") if s.strip()}

    out: dict = {
        "symbol": symbol,
        "normalized": {"market": market, "code": code, "xq_symbol": xq_sym},
        "fetched_at": datetime.now(CN_TZ).isoformat(),
    }

    # 并发抓取各维度
    tasks: dict[str, callable] = {}
    if "quote" not in skip:
        tasks["quote"] = lambda: get_quote(symbol, market, xq_sym)
    if "price_history" not in skip:
        tasks["price_history"] = lambda: _get_price_history(market, code, xq_sym, args)
    if "info" not in skip:
        if market == "a":
            tasks["info"] = lambda: get_a_company_info(code)
        elif market == "hk":
            tasks["info"] = lambda: get_hk_company_info(code)
        elif market == "us":
            tasks["info"] = lambda: get_us_company_info(code)
    if "filings" not in skip and market == "us":
        tasks["filings"] = lambda: get_us_filings(code, args.ann_limit)
    if "financial_summary" not in skip and market == "us":
        tasks["financial_summary"] = lambda: get_us_financial_summary(code)
    if "managers" not in skip and market == "a":
        tasks["managers"] = lambda: get_a_managers(code, args.top_managers)
    if "shareholders" not in skip and market == "a":
        tasks["shareholders"] = lambda: get_a_shareholders(code, args.top_holders)
    if "concepts" not in skip and market == "a":
        tasks["concepts"] = lambda: get_a_concepts(code)
    if "announcements" not in skip:
        tasks["announcements"] = lambda: get_recent_announcements(market, code, xq_sym, args.ann_days, args.ann_limit)
    if "peers" not in skip and getattr(args, "with_peers", False) and market == "a":
        tasks["peers"] = lambda: _get_peers(xq_sym)
    # fund_flow（主力资金流）：A 股沪深主板/创业板/科创板 + 港股；北交所、美股自动跳过
    _ff_eligible = (market == "hk") or (market == "a" and not code.startswith(("4", "8")))
    if "fund_flow" not in skip and _ff_eligible:
        tasks["fund_flow"] = lambda: _get_fund_flow(market, code)

    # ── Enrichment 维度（v1：news / sector / attention）──
    # 这 3 个是 spc decision 的辅助维度，不阻塞主分析；任一失败不影响其余字段。
    # attention 无依赖，可以放第一阶段并发跑；news / sector 依赖 quote/info/peers
    # 的结果，放第二阶段串行跑。
    if "attention" not in skip and market in ("a", "hk"):
        tasks["attention"] = lambda: fetch_xueqiu_attention(market, code, xq_sym)

    with ThreadPoolExecutor(max_workers=min(6, len(tasks))) as ex:
        future_map = {ex.submit(fn): name for name, fn in tasks.items()}
        for fut in as_completed(future_map):
            name = future_map[fut]
            try:
                out[name] = fut.result()
                stat = (
                    f"OK ({len(out[name])} items)" if isinstance(out[name], (list, dict))
                    else "OK"
                )
                print(f"[analyze_company] {name}: {stat}", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                out[name] = None
                print(f"[analyze_company] {name}: FAIL {type(e).__name__}: {e}", file=sys.stderr)

    # ── 第二阶段：依赖前序结果的 enrichment ──
    # 1) stock_news：用真实公司名 + 简称做关键词匹配（必须等 info 跑完）
    # 2) sector_strength：用 peers 同业 + quote.percent 算个股 vs 板块强弱
    if "stock_news" not in skip and market in ("a", "hk"):
        info = out.get("info") or {}
        if isinstance(info, dict):
            full_name = (
                info.get("公司名称") or info.get("中文名称")
                or info.get("公司全称") or info.get("名称") or ""
            )
            short_name = (
                info.get("公司简称") or info.get("证券简称") or info.get("中文简称") or ""
            )
            aliases: list[str] = []
            if isinstance(info.get("曾用名"), str) and info["曾用名"].strip():
                aliases.extend([s for s in re.split(r"[，,;\s]+", info["曾用名"]) if s.strip()])
        else:
            full_name = short_name = ""
            aliases = []
        try:
            out["stock_news"] = fetch_stock_news_relevance(
                market=market,
                code=code,
                name=str(full_name).strip(),
                short_name=str(short_name).strip(),
                aliases=aliases,
                days=7,
                pool_size=100,
            )
            cnt = (out["stock_news"] or {}).get("related_count", 0)
            print(f"[analyze_company] stock_news: OK (related={cnt})", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            out["stock_news"] = None
            print(f"[analyze_company] stock_news: FAIL {type(e).__name__}: {e}", file=sys.stderr)

    if "sector_strength" not in skip and market in ("a", "hk"):
        peers = out.get("peers") or []
        peer_syms: list[str] = []
        if isinstance(peers, list):
            for p in peers:
                if isinstance(p, dict):
                    sym = p.get("symbol") or p.get("xq_symbol") or p.get("code")
                    if sym:
                        peer_syms.append(str(sym))
        self_pct = None
        q = out.get("quote") or {}
        if isinstance(q, dict):
            try:
                self_pct = float(q.get("percent")) if q.get("percent") is not None else None
            except (TypeError, ValueError):
                self_pct = None
        try:
            out["sector_strength"] = fetch_sector_strength(
                market=market,
                code=code,
                self_xq_symbol=xq_sym,
                peer_xq_symbols=peer_syms,
                self_change_pct=self_pct,
            )
            label = (out["sector_strength"] or {}).get("label", "n/a")
            print(f"[analyze_company] sector_strength: OK ({label})", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            out["sector_strength"] = None
            print(f"[analyze_company] sector_strength: FAIL {type(e).__name__}: {e}", file=sys.stderr)

    return out


def render_text(data: dict) -> str:
    """生成 markdown 文本卡片。"""
    sym = data.get("symbol")
    market = data.get("normalized", {}).get("market")
    market_label = {"a": "A 股", "hk": "港股", "us": "美股 / 中概"}.get(market, market)

    lines = []
    name = ""
    info = data.get("info") or {}
    if isinstance(info, dict):
        name = info.get("公司名称") or info.get("中文名称") or info.get("公司简称") or ""
    lines.append(f"# {name or sym} ({sym}) — 公司深度卡片  [{market_label}]")
    lines.append(f"_数据抓取时间：{data.get('fetched_at')}_")
    lines.append("")

    # 行情
    q = data.get("quote") or {}
    if q:
        cap_yi = (q.get('market_capital') or 0) / 1e8
        lines.append("## 一、最新行情")
        lines.append(f"- 现价：**{q.get('current')}** ({q.get('percent', 0):+.2f}%)")
        lines.append(f"- 今日范围：{q.get('low')} ~ {q.get('high')}（开盘 {q.get('open')}，昨收 {q.get('last_close')}）")
        lines.append(f"- 成交额：{(q.get('amount') or 0)/1e8:.2f} 亿")
        lines.append(f"- 总市值：{cap_yi:,.0f} 亿")
        if q.get("turnover_rate") is not None:
            lines.append(f"- 换手率：{q.get('turnover_rate')}%")
        if q.get("current_year_percent") is not None:
            lines.append(f"- 年初至今：{q.get('current_year_percent', 0):+.2f}%")
        lines.append("")

    # 历史价位（K 线摘要）— 关键数据，给"技术分析"提供事实依据
    ph = data.get("price_history") or {}
    if ph and "ytd" in ph:
        ytd = ph["ytd"]
        pos = ph["position"]
        breakout = ph.get("breakout", {})
        regime = ph.get("regime", "")
        regime_label = {
            "NEW_ALL_TIME_HIGH": "🔥 创历史新高",
            "NEW_ALL_TIME_LOW": "❄️ 创历史新低",
            "NEW_YTD_HIGH": "🟢 创年内新高",
            "NEW_YTD_LOW": "🔴 **创年内新低**",
            "NEAR_YTD_HIGH": "🟢 接近年内高位",
            "NEAR_YTD_LOW": "🟡 接近年内低位",
            "IN_RANGE": "⚪️ 区间内运行",
        }.get(regime, regime)
        lines.append("## 二、历史价位摘要（K 线驱动，杜绝凭印象）")
        lines.append(f"- **当前 K 线状态**：{regime_label}")
        lines.append(
            f"- **YTD（{ytd['year']} 年初至今）**：起 {ytd['start_close']} → 当前 {ph.get('current_price')}，"
            f"涨跌 **{ytd['ytd_change_pct']:+.2f}%**（{ytd['trading_days']} 个交易日）"
        )
        lines.append(
            f"- **YTD 高/低**：高 {ytd['ytd_high']} ({ytd['ytd_high_date']}) / "
            f"低 {ytd['ytd_low']} ({ytd['ytd_low_date']})"
        )
        lines.append(
            f"- **52 周高/低**：高 {pos['high_52w']} ({pos['high_52w_date']}) / "
            f"低 {pos['low_52w']} ({pos['low_52w_date']})"
        )
        lines.append(
            f"- **当前距 52w 高**：{pos['from_52w_high_pct']:+.2f}%；"
            f"**距 52w 低**：{pos['from_52w_low_pct']:+.2f}%"
        )
        lines.append(
            f"- **历史最高/最低**：{pos['all_time_high']} ({pos['all_time_high_date']}) / "
            f"{pos['all_time_low']} ({pos['all_time_low_date']})；"
            f"距历史高 **{pos['from_all_time_high_pct']:+.2f}%**"
        )
        if ph.get("yearly"):
            lines.append("")
            lines.append("### 历年高低段")
            lines.append("| 年份 | 交易日 | 全年低 | 日期 | 全年高 | 日期 | 年涨跌 |")
            lines.append("|---|---|---|---|---|---|---|")
            for y in ph["yearly"][-6:]:
                lines.append(
                    f"| {y['year']} | {y['trading_days']} | {y['low']} | {y['low_date']} | "
                    f"{y['high']} | {y['high_date']} | {y['year_change_pct']:+.1f}% |"
                )
        thresholds = ph.get("thresholds") or []
        if thresholds:
            lines.append("")
            lines.append("### 关键价位倒查（上一次盘中跌到该水平的日期）")
            lines.append("| 价位 | 上次盘中触及 | 距今 |")
            lines.append("|---|---|---|")
            today_str = ph.get("coverage", {}).get("last_date", "")
            for th in thresholds:
                last = th.get("last_touched_below")
                if not last:
                    days_ago = "历史从未"
                else:
                    try:
                        from datetime import datetime as _dt
                        days_ago = f"{(_dt.fromisoformat(today_str) - _dt.fromisoformat(last)).days} 天前"
                    except Exception:
                        days_ago = "-"
                lines.append(f"| {th['level']} | {last or '从未'} | {days_ago} |")
        if any(breakout.get(k) for k in ("new_ytd_low", "new_52w_low", "new_all_time_low")):
            lines.append("")
            level = ("历史新低" if breakout.get("new_all_time_low")
                     else "52 周新低" if breakout.get("new_52w_low")
                     else "年内新低")
            lines.append(
                f"> ⚠️ **重要提示**：今日已盘中**创出{level}**，"
                "技术上属破位下行，而非支撑位震荡。"
            )
        elif any(breakout.get(k) for k in ("new_ytd_high", "new_52w_high", "new_all_time_high")):
            lines.append("")
            level = ("历史新高" if breakout.get("new_all_time_high")
                     else "52 周新高" if breakout.get("new_52w_high")
                     else "年内新高")
            lines.append(f"> 🚀 **重要提示**：今日已盘中**创出{level}**，处于强势突破阶段。")
        lines.append("")

    # 主力资金动向（仅 A 股沪深 + 港股有数据；北交所 / 美股自动跳过本章节）
    ff = data.get("fund_flow") or {}
    if ff and not ff.get("error") and ff.get("today"):
        today_ff = ff.get("today") or {}
        rolling = ff.get("rolling") or {}
        regime = ff.get("regime")
        reversal = ff.get("reversal")
        cross = ff.get("cross_validation") or {}

        lines.append(f"## 三、主力资金动向（截至 {ff.get('as_of')}，东方财富 fflow）")
        lines.append(f"- **regime**：{regime_label(regime, with_emoji=True)}")
        rev_zh = reversal_label(reversal, with_emoji=True)
        if rev_zh:
            lines.append(f"- **reversal**：{rev_zh}")
        if cross.get("verdict"):
            lines.append(
                f"- **cross_validation**：`{cross['verdict']}` — {cross.get('verdict_zh') or '-'}"
            )
        if market == "hk":
            lines.append("- _港股资金分级为东财根据成交单笔大小推算，仅供参考。_")
        lines.append("")
        lines.append("### 累计窗口")
        lines.append("| 周期 | 主力净额 | 净流入 / 流出天数 |")
        lines.append("|---|---|---|")
        for win in ("1d", "5d", "10d", "20d"):
            w = rolling.get(win) or {}
            amt = w.get("main_yi")
            amt_str = "-" if amt is None else f"{amt:+.2f} 亿"
            lines.append(
                f"| {win} | {amt_str} | "
                f"{w.get('inflow_days', 0)} / {w.get('outflow_days', 0)} (共 {w.get('days', 0)} 天) |"
            )
        lines.append("")
        # 多周期解读：把交叉验证结论铺开，避免 LLM 在 prompt 里手算 1d/5d/10d/20d
        if cross:
            lines.append("### 多周期解读（cross_validation）")
            dirs = cross.get("directions") or {}
            periods = cross.get("periods") or ["1d", "5d", "10d", "20d"]
            dir_str = " / ".join(f"{p}={dirs.get(p) or '-'}" for p in periods)
            lines.append(f"- **方向**：{dir_str}")
            lines.append(
                f"- **共振**：all_aligned={cross.get('all_aligned')}, "
                f"acceleration=`{cross.get('acceleration') or '-'}`, "
                f"is_resonance={cross.get('is_resonance')}"
            )
            if cross.get("short_long_conflict"):
                lines.append(
                    f"- **短长冲突**：⚠️ `{cross.get('conflict_kind')}`"
                    "（短期优先 → 信号偏弱）"
                )
            conc = cross.get("concentration_5d_in_20d")
            if conc is not None:
                tag = "（≥0.5，近期集中）" if conc >= 0.5 else ""
                lines.append(f"- **5d/20d 集中度**：{conc}{tag}")
            rc = cross.get("reversal_confirmed")
            if rc is True:
                lines.append("- **reversal 背书**：✅ 1d/5d 同向背书，反转已确认")
            elif rc is False:
                lines.append("- **reversal 背书**：❌ 1d/5d 未同向背书，反转未确认（不应据此 buy）")
            lines.append("")
        lines.append(
            f"### 当日资金分层（收盘 {today_ff.get('close')}，"
            f"涨跌 {today_ff.get('change_pct')}%）"
        )
        lines.append("| 档位 | 净额 | 占成交比例 |")
        lines.append("|---|---|---|")
        for label, amt_key, pct_key in (
            ("超大单", "super_big_yi", "super_big_pct"),
            ("大单", "big_yi", "big_pct"),
            ("中单", "mid_yi", "mid_pct"),
            ("小单", "small_yi", "small_pct"),
            ("**主力合计**", "main_yi", "main_pct"),
        ):
            amt = today_ff.get(amt_key)
            pct = today_ff.get(pct_key)
            amt_str = "-" if amt is None else f"{amt:+.2f} 亿"
            pct_str = "-" if pct is None else f"{pct:+.2f}%"
            lines.append(f"| {label} | {amt_str} | {pct_str} |")
        lines.append("")
    elif ff and ff.get("error"):
        lines.append(f"## 三、主力资金动向\n\n（暂无数据：{ff['error']}）\n")

    # 公司基本信息
    if isinstance(info, dict) and info:
        lines.append("## 四、公司基本信息")
        # 关键字段优先
        priority = ["公司名称", "公司英文名称", "上市市场", "成立日期", "上市日期",
                    "发行价格", "公司网址", "董事长", "总经理", "董事会秘书",
                    "邮政编码", "公司电话", "注册地址", "办公地址", "经营范围", "主营业务"]
        used = set()
        for k in priority:
            if k in info:
                lines.append(f"- **{k}**：{info[k]}")
                used.add(k)
        # 其余字段
        for k, v in info.items():
            if k not in used and v and k not in ("董秘电话", "董秘传真"):
                lines.append(f"- {k}：{v}")
        lines.append("")

    # 概念归属
    concepts = data.get("concepts") or []
    if concepts:
        lines.append("## 五、所属板块 / 概念题材（东方财富）")
        lines.append("`" + "` / `".join(concepts[:15]) + "`")
        lines.append("")

    # 高管
    mgrs = data.get("managers") or []
    if mgrs:
        lines.append("## 六、核心高管")
        lines.append("| 姓名 | 职务 | 起始日期 | 终止日期 |")
        lines.append("|---|---|---|---|")
        for m in mgrs:
            lines.append(f"| {m['name']} | {m['title']} | {m['start_date']} | {m['end_date']} |")
        lines.append("")

    # 主要股东
    shs = data.get("shareholders") or []
    if shs:
        lines.append("## 七、主要股东")
        lines.append("| 股东名称 | 持股数 | 比例 | 类型 |")
        lines.append("|---|---|---|---|")
        for s in shs:
            lines.append(f"| {s.get('name','')} | {s.get('shares','')} | {s.get('ratio','')} | {s.get('type','')} |")
        lines.append("")

    # 美股 / 中概 财务摘要（来自 SEC XBRL）
    fs = data.get("financial_summary") or {}
    if fs:
        lines.append("## 财务摘要（SEC XBRL 历年数据）")
        for label, info in fs.items():
            unit = info.get("unit", "")
            unit_div = 1e9 if unit in ("USD", "CNY") else 1
            unit_suffix = " B" if unit_div > 1 else ""
            values = info.get("values") or []
            if not values:
                continue
            row = f"- **{label}** ({unit}):"
            for v in values:
                amt = (v.get("val") or 0) / unit_div
                row += f" `{v.get('end','?')[:7]}={amt:,.2f}{unit_suffix}`"
            lines.append(row)
        lines.append("")

    # SEC filings（美股专用）
    us_filings = data.get("filings") or []
    if us_filings:
        lines.append(f"## SEC EDGAR 最近 {len(us_filings)} 份 filings")
        for f in us_filings[:15]:
            lines.append(f"- `{f.get('date')}` **{f.get('title')}** [文档]({f.get('pdf_url')})")
        lines.append("")

    # 同业横向对比
    peers = data.get("peers") or []
    if peers:
        lines.append("## 八、同业横向对比（同概念公司）")
        lines.append("| 代码 | 名称 | 现价 | 涨跌 | 市值(亿) | PE-TTM | PB | ROE-TTM | 营收增速 | 净利增速 | 主力(亿) | YTD |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        def _fmt(v, suffix=""):
            if v is None:
                return "-"
            if isinstance(v, (int, float)):
                return f"{v:.2f}{suffix}"
            return str(v)
        for p in peers:
            pct = p.get('percent')
            ytd = p.get('ytd_pct')
            lines.append(
                f"| `{p.get('symbol','')}` | {p.get('name','')} | "
                f"{_fmt(p.get('current'))} | "
                f"{(f'{pct:+.2f}%' if isinstance(pct,(int,float)) else '-')} | "
                f"{_fmt(p.get('market_cap_yi'))} | "
                f"{_fmt(p.get('pe_ttm'))} | "
                f"{_fmt(p.get('pb'))} | "
                f"{_fmt(p.get('roe_ttm'),'%')} | "
                f"{_fmt(p.get('income_cagr'),'%')} | "
                f"{_fmt(p.get('net_profit_cagr'),'%')} | "
                f"{_fmt(p.get('main_inflow_yi'))} | "
                f"{(f'{ytd:+.1f}%' if isinstance(ytd,(int,float)) else '-')} |"
            )
        lines.append("")

    # 公告
    anns = data.get("announcements") or []
    if anns:
        lines.append(f"## 九、近 {data.get('ann_days', 30)} 天关键公告（{len(anns)} 条）")
        for a in anns[:20]:
            lines.append(f"- `{a.get('date')}` **{a.get('title')}** [PDF]({a.get('pdf_url')})")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--symbol", required=True, help="股票代码：SZ300750 / SH600519 / HK00700 / BABA")
    ap.add_argument("--top-managers", type=int, default=10)
    ap.add_argument("--top-holders", type=int, default=10)
    ap.add_argument("--ann-days", type=int, default=30, help="公告时间范围天数")
    ap.add_argument("--ann-limit", type=int, default=20, help="公告条数上限")
    ap.add_argument("--kline-count", type=int, default=1500, help="K 线天数（默认 1500，约 6 年）")
    ap.add_argument("--with-peers", action="store_true", help="同业横向对比（PE/PB/ROE/营收增速）")
    ap.add_argument(
        "--skip",
        default="",
        help="跳过的维度，逗号分隔（quote,price_history,info,managers,shareholders,concepts,announcements）",
    )
    ap.add_argument("--format", choices=["json", "text"], default="text")
    args = ap.parse_args()

    data = analyze(args.symbol, args)
    data["ann_days"] = args.ann_days

    if args.format == "json":
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
    else:
        print(render_text(data))


if __name__ == "__main__":
    main()

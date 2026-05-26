#!/usr/bin/env python3
"""
上下游图谱：基于年报 PDF 抽取 + 同业公司列表 + 行业研究输出 公司所在产业链上下游图谱。

数据流：
  1. 调 fetch_announcements 找最新年报 PDF
  2. 调 pdf_extract 抽取 "主要客户" / "主要供应商" / "业务概要" / "管理层讨论" 章节
  3. 用规则 + 启发式从这些章节里抽取实体名（公司名、产品名）
  4. 调 scan_sector 找同板块的同业公司（用于 LLM 同业对比）
  5. 输出结构化 JSON，让 agent / LLM 整合成最终上下游图谱

注意：本脚本只做"原料抽取"，不做语义理解。最终的"上下游关系判定 / 商业模式总结"
由 agent / LLM 在拿到 JSON 后根据指令完成。

Usage:
  python3 supply_chain.py --symbol SZ300750
  python3 supply_chain.py --symbol SZ300750 --max-pdf-pages 100
  python3 supply_chain.py --symbol SZ300750 --report-type semi    # 半年报
  python3 supply_chain.py --symbol SZ300750 --format json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime

import _path_setup  # noqa: F401,E402  把 <repo>/shared 加入 sys.path
from stock_core.http import fetch  # noqa: E402
from stock_core.eastmoney import (  # noqa: E402
    eastmoney_a_code,
    fetch_a_core_conception_raw,
    fetch_board_constituents,
)
from stock_core.symbols import parts_to_symbol  # noqa: E402
from stock_core.tz import CN_TZ  # noqa: E402
from stock_core.xueqiu import XueqiuClient  # noqa: E402


# ============ 找最新年报 ============ #

def find_latest_report(symbol: str, report_type: str = "annual") -> dict | None:
    """从巨潮拿最新一份指定类型的报告（年报 / 半年报）。"""
    try:
        from fetch_announcements import (
            cninfo_announcements,
            _parse_a_share_symbol,
        )
    except ImportError as e:
        print(f"[supply_chain] import fetch_announcements failed: {e}", file=sys.stderr)
        return None

    sym = symbol.upper()
    if sym.startswith("HK"):
        # 港股暂不实现自动找年报（披露易接口不稳定）
        return None
    try:
        code, column = _parse_a_share_symbol(sym)
    except ValueError:
        return None

    category = "annual" if report_type == "annual" else "semi"
    items = cninfo_announcements(
        stock_code=code, category=category, days=540, page_size=10, column=column,
    )
    # 取标题里含"年度报告"或"半年度报告"且**不**含"摘要"/"取消"/"补充"的
    type_kw = "年度报告" if report_type == "annual" else "半年度报告"
    for it in items:
        title = it.get("title") or ""
        if type_kw in title and not any(
            x in title for x in ("摘要", "取消", "补充", "更正", "修订", "事前审核")
        ):
            return it
    return items[0] if items else None


# ============ 实体抽取（规则启发式） ============ #

CLIENT_SUPPLIER_PATTERNS = [
    # 表格行：客户一、客户二 / 第一名 / 单位一 这种通用表头
    r"(?:客户|供应商|单位)?\s*[一二三四五甲乙丙丁戊]\b[:：]?",
    r"第\s*[一二三四五]\s*名\b",
]

COMPANY_NAME_RX = re.compile(
    # 中文公司名：以"...公司"或"...集团"或"...股份"或"...有限"结尾
    r"[\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z0-9·\(\)（）]+?"
    r"(?:股份有限公司|有限公司|股份公司|集团有限公司|集团|科技公司|科技股份|公司|集团股份)"
)
ENGLISH_COMPANY_RX = re.compile(
    r"[A-Z][A-Za-z0-9 .,&-]+?\b(?:Corp\.?|Corporation|Inc\.?|Ltd\.?|Limited|Co\.?|Group)\b"
)


def extract_companies_from_text(text: str, max_n: int = 20) -> list[str]:
    """从文本里抽取看起来像公司名的实体。"""
    if not text:
        return []
    text = re.sub(r"[\r\n\u3000]+", " ", text)
    out = []
    seen = set()
    for rx in [COMPANY_NAME_RX, ENGLISH_COMPANY_RX]:
        for m in rx.finditer(text):
            name = m.group(0).strip()
            # 简单过滤
            if 4 <= len(name) <= 40 and name not in seen:
                # 排除明显是公司自身的（含"宁德时代"等）和明显是套话的
                seen.add(name)
                out.append(name)
                if len(out) >= max_n:
                    return out
    return out


def extract_amount_pairs(text: str) -> list[dict]:
    """从'前五大客户/供应商'章节抽取 (名称, 金额, 占比) 三元组。

    年报这类章节的常见格式：
      序号  客户名称        销售额（万元）   占年度销售总额比例
      1     XX 公司          XXXX             XX%
      ...
    """
    out = []
    if not text:
        return out
    lines = text.split("\n")
    for line in lines:
        # 匹配带有金额或百分比的行（中文公司名 + 数字 + %）
        if re.search(r"\d+\.\d+%", line) and re.search(r"\d{4,}|\d+,\d{3}", line):
            m_company = COMPANY_NAME_RX.search(line)
            m_pct = re.search(r"(\d+\.\d+)%", line)
            m_amt = re.search(r"(\d{4,}(?:\.\d+)?|\d+,\d{3}(?:,\d{3})?(?:\.\d+)?)", line)
            if m_company:
                out.append({
                    "name": m_company.group(0),
                    "amount_raw": m_amt.group(1) if m_amt else "",
                    "pct": m_pct.group(1) + "%" if m_pct else "",
                    "raw_line": line.strip()[:200],
                })
    return out


# ============ 同业公司（用于对比） ============ #

def _get_board_constituents_em(board_code: str, top: int = 30) -> list[str]:
    """东方财富板块成分股 push2 接口。返回 6 位股票代码列表。

    board_code: 'BK1033'（电池）等
    """
    try:
        rows = fetch_board_constituents(board_code)
    except Exception as e:  # noqa: BLE001
        print(f"[supply_chain] em board {board_code} failed: {e}", file=sys.stderr)
        return []
    return rows[:top]


def get_peers_from_concept(symbol: str, top: int = 8) -> list[dict]:
    """通过东财所属板块找同业公司，输出 PE/PB/ROE 等横向对比数据。"""
    try:
        from stock_core.symbols import normalize_symbol  # type: ignore
    except ImportError as e:
        print(f"[supply_chain] import peers deps failed: {e}", file=sys.stderr)
        return []

    market, code, _ = normalize_symbol(symbol)
    if market != "a":
        return []

    # 直接用东财 ssbk 拿所属板块（含 BOARD_CODE，可直接查成分股）
    em_code = eastmoney_a_code(code)
    try:
        f10 = fetch_a_core_conception_raw(em_code)
    except Exception as e:  # noqa: BLE001
        print(f"[supply_chain] em F10 failed: {e}", file=sys.stderr)
        return []

    boards = f10.get("ssbk") or []
    # 挑非"风格因子"类的板块（行业/概念优先）
    style_kw = ("大盘", "中盘", "小盘", "权重", "成长", "价值", "百元", "千元",
                "行业龙头", "破净", "高市", "低市", "ETF", "深证", "上证",
                "创业板综", "富时", "MSCI", "茅", "宁组合", "周期股", "深股通", "沪股通")
    target_board = None
    for b in boards:
        bn = b.get("BOARD_NAME") or ""
        if any(sk in bn for sk in style_kw):
            continue
        target_board = b
        break
    if not target_board:
        return []

    bcode = target_board.get("BOARD_CODE") or ""
    bname = target_board.get("BOARD_NAME") or ""
    # BOARD_CODE 通常是"1033"，要拼成"BK1033"
    if not bcode.startswith("BK"):
        bcode = f"BK{bcode.zfill(4)}"

    print(f"[supply_chain] peers 板块={bname} ({bcode})", file=sys.stderr)
    consts = _get_board_constituents_em(bcode, top=top + 5)
    consts = [c for c in consts if c != code][:top]
    if not consts:
        return []

    cli = XueqiuClient()
    syms = [parts_to_symbol("a", c) for c in consts]
    try:
        full = cli.screener_by_symbols(syms, market="all_a")
    except Exception as e:  # noqa: BLE001
        print(f"[supply_chain] peers screener failed: {e}", file=sys.stderr)
        return []
    return _format_peers(full)


def get_peers_legacy(symbol: str, top: int = 8) -> list[dict]:
    """旧版：通过同花顺映射找同业（保留作 fallback）。"""
    try:
        from stock_core.company_analysis import get_a_concepts  # type: ignore
        from stock_core.symbols import normalize_symbol  # type: ignore
        from scan_sector import get_sector_map, get_sector_constituents  # type: ignore
    except ImportError as e:
        print(f"[supply_chain] import peers deps failed: {e}", file=sys.stderr)
        return []

    market, code, _ = normalize_symbol(symbol)
    if market != "a":
        return []

    # 拿前 8 个概念（东财），过滤掉"风格因子"类（大盘股/权重股/行业龙头等）
    raw_concepts = get_a_concepts(code, top=20)
    style_keywords = ("大盘", "中盘", "小盘", "权重", "成长", "价值", "百元", "千元",
                      "行业龙头", "板块", "破净", "高市", "低市", "ETF", "深证", "上证",
                      "创业板综", "富时", "MSCI", "茅", "宁组合", "周期股")
    concepts = [
        c for c in raw_concepts
        if not any(sk in c for sk in style_keywords)
    ][:8]
    if not concepts:
        return []

    # 同花顺 concept_map 模糊匹配
    cmap = {}
    try:
        cmap = get_sector_map("concept")
    except Exception:
        return []

    target_code = None
    target_name = None
    sector_kind = "concept"
    # 完全匹配优先
    for c in concepts:
        if c in cmap:
            target_code = cmap[c]
            target_name = c
            break
    # 模糊匹配兜底
    if not target_code:
        for c in concepts:
            for k, v in cmap.items():
                if c in k or k in c:
                    target_code = v
                    target_name = k
                    break
            if target_code:
                break
    if not target_code:
        # 还是没匹配上，试 industry_map
        try:
            imap = get_sector_map("industry")
        except Exception:
            return []
        for c in concepts:
            if c in imap:
                target_code = imap[c]
                target_name = c
                break
        if target_code:
            sector_kind = "industry"
            print(f"[supply_chain] peers 用行业板块 fallback: {target_name}", file=sys.stderr)
            consts = get_sector_constituents(sector_kind, target_code)
            consts = [x for x in consts if x != code][:top]
            if not consts:
                return []
            cli = XueqiuClient()
            syms = [parts_to_symbol("a", x) for x in consts]
            try:
                full = cli.screener_by_symbols(syms, market="all_a")
            except Exception:
                return []
            return _format_peers(full)

    if not target_code:
        return []

    print(f"[supply_chain] 同业概念：{target_name} (code={target_code})", file=sys.stderr)
    consts = get_sector_constituents(sector_kind, target_code)
    consts = [x for x in consts if x != code][:top]
    print(f"[supply_chain] peers 概念={target_name} 成分={len(consts)}", file=sys.stderr)
    if not consts:
        return []

    cli = XueqiuClient()
    syms = [parts_to_symbol("a", c) for c in consts]
    try:
        full = cli.screener_by_symbols(syms, market="all_a")
    except Exception as e:  # noqa: BLE001
        print(f"[supply_chain] peers screener failed: {e}", file=sys.stderr)
        return []

    return _format_peers(full)


def _format_peers(full: list[dict]) -> list[dict]:
    return [
        {
            "symbol": q.get("symbol"),
            "name": q.get("name"),
            "current": q.get("current"),
            "percent": q.get("percent"),
            "market_cap_yi": round((q.get("market_capital") or 0) / 1e8, 2),
            "amount_yi": round((q.get("amount") or 0) / 1e8, 2),
            "pe_ttm": q.get("pe_ttm"),
            "pb": q.get("pb"),
            "ps": q.get("ps"),
            "roe_ttm": q.get("roe_ttm"),
            "net_profit_cagr": q.get("net_profit_cagr"),
            "income_cagr": q.get("income_cagr"),
            "dividend_yield": q.get("dividend_yield"),
            "turnover_rate": q.get("turnover_rate"),
            "main_inflow_yi": round((q.get("main_net_inflows") or 0) / 1e8, 2),
            "ytd_pct": q.get("current_year_percent"),
            "followers": q.get("followers"),
        }
        for q in full
    ]


# ============ 主流程 ============ #


# PDF 章节文本写进 payload 时的截断长度。LLM 拿这些再 summarize 已经够用；
# 真要原文可单独调 pdf_extract --format json --sections xxx。
_SECTION_TEXT_LIMIT = 6000


def build_supply_chain_payload(
    symbol: str,
    *,
    report_type: str = "annual",
    max_pdf_pages: int = 300,
    include_peers: bool = True,
    peers_top: int = 8,
) -> dict:
    """编排"找年报 → PDF 抽章节 → 实体抽取 → 同业 peers"的完整流程。

    供 ``main()`` 和 ``company_api.py --deep`` 共用。所有失败点都被吃掉，
    只在返回的 payload 里以 ``error`` / ``pdf_error`` / ``peers_error`` 标注，
    不会抛异常——保证 ``--deep`` 出问题时也不阻断 ``company_api`` 主流程。

    Returns:
        ``{symbol, fetched_at, report, sections, extracted_entities, amount_pairs, peers, [error/pdf_error/peers_error]}``
        当不支持的市场（HK / US）或没找到年报时仅返回 ``error``。
    """
    out: dict = {
        "symbol": symbol,
        "fetched_at": datetime.now(CN_TZ).isoformat(),
        "report": None,
        "sections": {},
        "extracted_entities": {
            "from_customers": [],
            "from_suppliers": [],
            "from_business": [],
        },
        "amount_pairs": {"customers": [], "suppliers": []},
        "peers": [],
    }

    # 1. 找最新年报（HK/US 当前会返回 None → out.error）
    print(f"[supply_chain] 找最新 {report_type} 报告 ({symbol})...", file=sys.stderr)
    try:
        report = find_latest_report(symbol, report_type)
    except Exception as e:  # noqa: BLE001
        # find_latest_report 自己已经吞了大部分异常，这里再兜一层防御
        print(f"[supply_chain] 报告查找异常: {type(e).__name__}: {e}", file=sys.stderr)
        report = None

    if not report:
        out["error"] = (
            f"未找到 {symbol} 的最新 {report_type} 报告（A 股以外或巨潮无对应记录）"
        )
        return out

    out["report"] = {
        "title": report.get("title"),
        "date": report.get("date"),
        "pdf_url": report.get("pdf_url"),
    }
    print(f"[supply_chain] 找到：{report['title']} ({report['date']})", file=sys.stderr)

    # 2. PDF 抽章节 + 3. 实体抽取
    pdf_url = report.get("pdf_url")
    if pdf_url:
        try:
            from pdf_extract import download_pdf, extract_full_text, find_section  # type: ignore

            pdf_path = download_pdf(pdf_url)
            pages_text, pages_num = extract_full_text(pdf_path, max_pages=max_pdf_pages)
            for sec in ("business", "customers", "suppliers", "mda", "risks"):
                info = find_section(pages_text, pages_num, sec)
                out["sections"][sec] = {
                    "label": info.get("label"),
                    "found": info.get("found", False),
                    "start_page": info.get("start_page"),
                    "end_page": info.get("end_page"),
                    "char_count": info.get("char_count", 0),
                    "text_snippet": (info.get("text") or "")[:_SECTION_TEXT_LIMIT],
                }

            cust_text = out["sections"].get("customers", {}).get("text_snippet", "")
            supp_text = out["sections"].get("suppliers", {}).get("text_snippet", "")
            biz_text = out["sections"].get("business", {}).get("text_snippet", "")

            out["extracted_entities"]["from_customers"] = extract_companies_from_text(cust_text)
            out["extracted_entities"]["from_suppliers"] = extract_companies_from_text(supp_text)
            out["extracted_entities"]["from_business"] = extract_companies_from_text(biz_text, max_n=15)

            out["amount_pairs"]["customers"] = extract_amount_pairs(cust_text)
            out["amount_pairs"]["suppliers"] = extract_amount_pairs(supp_text)

        except Exception as e:  # noqa: BLE001
            print(f"[supply_chain] PDF 解析失败: {type(e).__name__}: {e}", file=sys.stderr)
            out["pdf_error"] = f"{type(e).__name__}: {e}"

    # 4. 同业公司
    if include_peers:
        try:
            out["peers"] = get_peers_from_concept(symbol, top=peers_top)
        except Exception as e:  # noqa: BLE001
            print(f"[supply_chain] peers 失败: {e}", file=sys.stderr)
            out["peers"] = []
            out["peers_error"] = f"{type(e).__name__}: {e}"

    return out


def _render_supply_chain_text(payload: dict) -> str:
    """供 supply_chain.py CLI 直接打印的 text 渲染。

    `company_api.py --deep` 的渲染不走这里，那边走 ``render_deep_text``，
    以适应"卡片末尾追加 §6-§8"的版式。
    """
    symbol = payload.get("symbol", "")
    lines: list[str] = []
    lines.append(f"# {symbol} 上下游图谱原料\n")
    report = payload.get("report") or {}
    if payload.get("error"):
        lines.append(f"_错误：{payload['error']}_\n")
        return "\n".join(lines)
    lines.append(f"_最新报告：{report.get('title', '-')} ({report.get('date', '-')})_")
    lines.append(f"_PDF：{report.get('pdf_url', '-')}_\n")
    if payload.get("pdf_error"):
        lines.append(f"_⚠️ PDF 解析失败：{payload['pdf_error']}_\n")

    sections = payload.get("sections") or {}
    for sec_key in ("business", "customers", "suppliers", "mda"):
        sec = sections.get(sec_key, {})
        if sec.get("found"):
            lines.append(
                f"## {sec['label']}（第 {sec['start_page']}-{sec['end_page']} 页, "
                f"{sec['char_count']:,} 字）\n"
            )
            snippet = sec.get("text_snippet", "")
            lines.append(snippet[:2000])
            if sec.get("char_count", 0) > 2000:
                lines.append(
                    f"\n_...(已截断，完整 {sec['char_count']:,} 字请用 --format json)_\n"
                )
            lines.append("")
        else:
            lines.append(f"## {sec.get('label', sec_key)}: 未找到\n")
    entities = payload.get("extracted_entities") or {}
    lines.append(f"## 抽取的客户实体: {entities.get('from_customers', [])}\n")
    lines.append(f"## 抽取的供应商实体: {entities.get('from_suppliers', [])}\n")
    amount_pairs = payload.get("amount_pairs") or {}
    lines.append(f"## 客户金额对：{amount_pairs.get('customers', [])}\n")
    peers = payload.get("peers") or []
    lines.append("## 同业公司（前 8）:")
    for p in peers:
        try:
            lines.append(
                f"  {p['symbol']}  现价 {p['current']}  涨跌 {p['percent']:+.2f}%  "
                f"市值 {p['market_cap_yi']:.0f}亿"
            )
        except Exception:  # noqa: BLE001
            # peers 里偶有字段缺失，最多跳过这一行
            lines.append(f"  {p}")
    if payload.get("peers_error"):
        lines.append(f"_⚠️ peers 失败：{payload['peers_error']}_")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--symbol", required=True, help="股票代码（仅 A 股 v1）")
    ap.add_argument("--report-type", choices=["annual", "semi"], default="annual")
    ap.add_argument("--max-pdf-pages", type=int, default=300, help="PDF 解析最大页数")
    ap.add_argument("--format", choices=["json", "text"], default="json")
    args = ap.parse_args()

    payload = build_supply_chain_payload(
        args.symbol,
        report_type=args.report_type,
        max_pdf_pages=args.max_pdf_pages,
    )

    if args.format == "text":
        print(_render_supply_chain_text(payload))
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()


if __name__ == "__main__":
    main()

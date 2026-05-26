#!/usr/bin/env python3
"""Programmatic API wrapper for ``analyze_company.py`` plus an optional
"深度模式 (--deep)"。

速查模式（默认）：调用 ``analyze_symbol`` 输出基本信息 / 财务 / 高管 / 主要股东 /
近期公告等卡片字段。

深度模式（``--deep``）：在速查输出基础上追加 §6/§7/§8 三个章节——
  - §6 商业模式 / 上下游：来自年报 PDF 的"业务概要 / 主要客户 / 主要供应商 / MD&A"
  - §7 风险提示：来自年报 PDF 的"风险因素"章节
  - §8 同业对比：自动开启 ``--with-peers`` + ``supply_chain.get_peers_from_concept``

降级保证（深度模式专属）：
  - 非 A 股 / 找不到年报 → 仅返回 ``deep.error``，速查输出仍然完整
  - PDF 下载或解析失败 → ``deep.pdf_error``，已成功的章节仍保留
  - 同业 peers 失败 → ``deep.peers_error``，其余字段不受影响
  - **任何深度模式失败都不会让 ``company_api`` 整体退出非 0**
"""
from __future__ import annotations

import argparse
import json
import sys

import _path_setup  # noqa: F401,E402  把 <repo>/shared 和 scripts/ 加入 sys.path
from stock_core.stock_market_hub import analyze_symbol, render_analysis_text  # noqa: E402


def _render_deep_text(deep: dict) -> str:
    """把 ``deep`` payload 渲染成追加在速查卡片后面的 §6/§7/§8 章节。

    与 SKILL.md 里的输出模板对齐：六（商业模式 / 上下游）、七（风险提示）、八（同业对比）。
    所有失败都以提示行展示，不抛异常。
    """
    if not deep:
        return ""
    lines: list[str] = ["", "---", ""]
    if deep.get("error") and not deep.get("report"):
        lines.append(f"## 六~八 深度模式：跳过\n_{deep['error']}_")
        return "\n".join(lines)

    report = deep.get("report") or {}
    sections = deep.get("sections") or {}
    entities = deep.get("extracted_entities") or {}
    amount_pairs = deep.get("amount_pairs") or {}
    peers = deep.get("peers") or []

    def _section_block(sec_key: str, fallback_title: str) -> list[str]:
        sec = sections.get(sec_key, {})
        if not sec.get("found"):
            return [f"_({fallback_title}：未找到对应章节)_"]
        head = (
            f"_位置：第 {sec.get('start_page')} 页 ~ 第 {sec.get('end_page')} 页，"
            f"{sec.get('char_count', 0):,} 字_\n"
        )
        snippet = (sec.get("text_snippet") or "")[:2000]
        suffix = ""
        if sec.get("char_count", 0) > 2000:
            suffix = f"\n\n_...(已截断到 2000 字；完整文本走 `smh company {deep.get('symbol', '')} --deep --format json`)_"
        return [head, snippet + suffix]

    lines.append(f"## 六、商业模式 / 上下游（来自 {report.get('title', '年报')}）\n")
    if deep.get("pdf_error"):
        lines.append(f"_⚠️ PDF 解析失败：{deep['pdf_error']}（章节内容缺失）_\n")
    lines.append("### 业务概要")
    lines.extend(_section_block("business", "业务概要"))
    lines.append("\n### 主要客户")
    lines.extend(_section_block("customers", "主要客户"))
    if amount_pairs.get("customers"):
        lines.append("\n**前 5 大客户金额（启发式抽取）：**")
        for p in amount_pairs["customers"][:5]:
            lines.append(
                f"- {p.get('name', '?')}：金额 {p.get('amount_raw', '-')}，占比 {p.get('pct', '-')}"
            )
    lines.append("\n### 主要供应商")
    lines.extend(_section_block("suppliers", "主要供应商"))
    if amount_pairs.get("suppliers"):
        lines.append("\n**前 5 大供应商金额（启发式抽取）：**")
        for p in amount_pairs["suppliers"][:5]:
            lines.append(
                f"- {p.get('name', '?')}：金额 {p.get('amount_raw', '-')}，占比 {p.get('pct', '-')}"
            )
    biz_ents = entities.get("from_business") or []
    if biz_ents:
        lines.append(f"\n_业务文本中识别到的公司实体（启发式）：{', '.join(biz_ents)}_")

    lines.append("\n## 七、风险提示\n")
    lines.extend(_section_block("risks", "风险因素"))

    lines.append("\n## 八、同业对比（板块成分股，前 8）\n")
    if not peers:
        if deep.get("peers_error"):
            lines.append(f"_⚠️ peers 拉取失败：{deep['peers_error']}_")
        else:
            lines.append("_未找到同业 peers（可能板块匹配失败或非 A 股）_")
    else:
        lines.append(
            "| symbol | 名称 | 现价 | 涨跌% | 市值(亿) | PE_TTM | PB | ROE_TTM | 营收 CAGR | 净利 CAGR |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for p in peers[:8]:
            try:
                lines.append(
                    f"| {p.get('symbol', '-')} | {p.get('name', '-')} | "
                    f"{p.get('current', '-')} | {p.get('percent', '-')} | "
                    f"{p.get('market_cap_yi', '-')} | {p.get('pe_ttm', '-')} | "
                    f"{p.get('pb', '-')} | {p.get('roe_ttm', '-')} | "
                    f"{p.get('income_cagr', '-')} | {p.get('net_profit_cagr', '-')} |"
                )
            except Exception:  # noqa: BLE001
                lines.append(f"| {p} |")

    return "\n".join(lines)


def _build_deep_payload(
    symbol: str,
    *,
    report_type: str,
    max_pdf_pages: int,
) -> dict:
    """调 supply_chain 拿"年报章节 + 实体 + 同业 peers"。失败一律退化为 dict。

    任何异常都被吞并转成 ``{"error": "..."}``，确保 ``--deep`` 不会让主流程退出非 0。
    """
    try:
        from supply_chain import build_supply_chain_payload  # type: ignore
    except ImportError as e:  # noqa: BLE001
        return {"symbol": symbol, "error": f"无法加载 supply_chain：{e}"}

    try:
        return build_supply_chain_payload(
            symbol,
            report_type=report_type,
            max_pdf_pages=max_pdf_pages,
            include_peers=True,
        )
    except Exception as e:  # noqa: BLE001
        # build_supply_chain_payload 内部已经吃异常，但这里再兜一层兜底
        print(
            f"[company_api] deep payload 编排异常: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return {"symbol": symbol, "error": f"deep 编排异常：{type(e).__name__}: {e}"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Programmatic API wrapper for analyze_company.py")
    ap.add_argument("--symbol", required=True, help="股票代码：SZ300750 / SH600519 / HK00700 / BABA")
    ap.add_argument("--top-managers", type=int, default=10)
    ap.add_argument("--top-holders", type=int, default=10)
    ap.add_argument("--ann-days", type=int, default=30)
    ap.add_argument("--ann-limit", type=int, default=20)
    ap.add_argument("--kline-count", type=int, default=1500)
    ap.add_argument("--with-peers", action="store_true")
    ap.add_argument("--skip", default="")
    ap.add_argument("--format", choices=["json", "text"], default="text")
    ap.add_argument(
        "--deep",
        action="store_true",
        help=(
            "深度模式：在速查卡片末尾追加 §6 商业模式/上下游、§7 风险提示、"
            "§8 同业对比（自动启用 --with-peers）。"
            "当前仅 A 股年报抽取链路成熟；HK/US 会退化为'仅速查 + deep.error 提示'。"
        ),
    )
    ap.add_argument(
        "--deep-report-type",
        choices=["annual", "semi"],
        default="annual",
        help="深度模式抓哪份报告：annual=年报（默认）/ semi=半年报",
    )
    ap.add_argument(
        "--deep-max-pdf-pages",
        type=int,
        default=300,
        help="深度模式 PDF 解析最大页数（默认 300，覆盖绝大多数年报）",
    )
    args = ap.parse_args()

    # --deep 隐式启用 --with-peers（同业对比是 §8 的核心数据源之一）
    with_peers = args.with_peers or args.deep

    data = analyze_symbol(
        args.symbol,
        top_managers=args.top_managers,
        top_holders=args.top_holders,
        ann_days=args.ann_days,
        ann_limit=args.ann_limit,
        kline_count=args.kline_count,
        with_peers=with_peers,
        skip=args.skip,
    )

    if args.deep:
        data["deep"] = _build_deep_payload(
            args.symbol,
            report_type=args.deep_report_type,
            max_pdf_pages=args.deep_max_pdf_pages,
        )

    if args.format == "json":
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
    else:
        text = render_analysis_text(data)
        if args.deep:
            text = text + "\n" + _render_deep_text(data.get("deep") or {})
        print(text)


if __name__ == "__main__":
    main()

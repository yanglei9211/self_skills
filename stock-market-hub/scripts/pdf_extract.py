#!/usr/bin/env python3
"""
年报 / 招股书 PDF 关键章节抽取。

针对 A 股 / 港股年报，按目录关键词定位以下章节并抽取文本：
  - 公司业务概要 / 业务概述
  - 主要经营情况 / 行业情况
  - 主要客户 / 主要供应商（前 5 大）
  - 风险因素
  - 管理层讨论与分析（MD&A）
  - 重要事项
  - 主要财务数据

输出 JSON：每个章节一段（可能 5-50KB 文本，给 LLM 做上下游/商业模式分析）。

Usage:
  # 输入 PDF URL（自动下载并解析）
  python3 pdf_extract.py --url "http://static.cninfo.com.cn/finalpage/2026-03-10/1225002214.PDF"

  # 输入本地 PDF
  python3 pdf_extract.py --file ~/Downloads/300750_2025年度报告.pdf

  # 只抽特定章节
  python3 pdf_extract.py --url URL --sections business,risks,customers

  # 输出格式
  python3 pdf_extract.py --url URL --format text   # markdown 格式
  python3 pdf_extract.py --url URL --format json   # 结构化（默认）

依赖：pdfplumber（已在 requirements）
缓存：下载到 ~/.cache/stock-market-hub/pdfs/，TTL 不限（年报基本不变）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from core.http import fetch  # type: ignore


CACHE_DIR = Path.home() / ".cache" / "stock-market-hub" / "pdfs"


# 章节定位关键词（A 股 / 港股年报通用）
SECTION_PATTERNS = {
    "business": {
        "label": "业务概要 / 主营业务",
        "starts": [
            r"公司业务概要", r"业务概要", r"业务概述",
            r"主要业务情况", r"主营业务情况", r"主要业务", r"主要产品及用途",
            r"行业经营情况", r"报告期内公司从事的主要业务",
            # 繁体（港股年报常用）
            r"公司業務概要", r"業務概要", r"業務概述",
            r"主要業務", r"主要產品", r"行業經營情況",
            # 英文
            r"Business Overview", r"Principal Activities",
        ],
        "ends": [
            r"^第[一二三四五六七八九十]+节", r"^第[一二三四五六七八九十]+節",
            r"管理层讨论与分析", r"管理層討論", r"经营情况讨论与分析", r"經營情況討論",
            r"风险因素", r"風險因素", r"重要事项", r"重要事項",
        ],
    },
    "mda": {
        "label": "管理层讨论与分析 (MD&A)",
        "starts": [
            r"^第[一二三四五六七八九十]+节\s*[（(]?\s*(管理层讨论与分析|经营情况讨论与分析)",
            r"管理层讨论与分析", r"经营情况讨论与分析",
            # 繁体
            r"管理層討論[及與与]?分析", r"管理層討論及分析",
            r"主席報告", r"主席報告書",
            r"Management Discussion and Analysis",
        ],
        "ends": [
            r"^第[一二三四五六七八九十]+节", r"^第[一二三四五六七八九十]+節",
            r"重要事项", r"重要事項",
            r"股份变动及股东情况", r"股份變動",
            r"董事(?:及高級管理人員)?報告",
        ],
    },
    "customers": {
        "label": "前五名客户 / 主要客户",
        "starts": [
            r"前\s*五?\s*名?\s*客户", r"主要客户", r"前\s*五\s*大\s*客户",
            r"前\s*五?\s*名?\s*客戶", r"主要客戶", r"前\s*五\s*大\s*客戶",
            r"Top.*Customer", r"Major Customer",
        ],
        "ends": [
            r"前\s*五?\s*名?\s*供应商", r"前\s*五?\s*名?\s*供應商",
            r"^第[一二三四五六七八九十]+节", r"^第[一二三四五六七八九十]+節",
            r"主要供应商", r"主要供應商",
        ],
    },
    "suppliers": {
        "label": "前五名供应商 / 主要供应商",
        "starts": [
            r"前\s*五?\s*名?\s*供应商", r"主要供应商", r"前\s*五\s*大\s*供应商",
            r"前\s*五?\s*名?\s*供應商", r"主要供應商",
            r"Top.*Supplier", r"Major Supplier",
        ],
        "ends": [
            r"^第[一二三四五六七八九十]+节", r"^第[一二三四五六七八九十]+節",
            r"研发投入", r"研發投入", r"现金流", r"現金流",
        ],
    },
    "risks": {
        "label": "风险因素",
        "starts": [
            r"^第[一二三四五六七八九十]+节\s*[（(]?\s*(风险因素|可能面对的风险)",
            r"风险因素", r"可能面对的风险", r"主要风险",
            # 繁体
            r"風險因素", r"主要風險", r"可能面對的風險", r"風險管理",
            r"Risk Factors",
        ],
        "ends": [
            r"^第[一二三四五六七八九十]+节", r"^第[一二三四五六七八九十]+節",
            r"重要事项", r"重要事項",
            r"公司治理", r"董事(?:及高級管理人員)?報告",
        ],
    },
    "important": {
        "label": "重要事项",
        "starts": [
            r"^第[一二三四五六七八九十]+节\s*[（(]?\s*重要事项",
            r"重要事项", r"重要事項",
            r"Significant Events",
        ],
        "ends": [
            r"^第[一二三四五六七八九十]+节", r"^第[一二三四五六七八九十]+節",
            r"股份变动", r"股份變動", r"股东大会情况", r"股東大會情況",
        ],
    },
    "finance_summary": {
        "label": "主要财务数据 / 财务摘要",
        "starts": [
            r"主要会计数据和财务指标", r"主要财务数据", r"财务摘要", r"五年财务概要",
            # 繁体
            r"主要會計數據和財務指標", r"主要財務數據", r"財務摘要", r"五年財務概要",
            r"財務摘要", r"五年財務概要",
            r"Financial Highlights", r"Five-Year Financial Summary",
        ],
        "ends": [
            r"^第[一二三四五六七八九十]+节", r"^第[一二三四五六七八九十]+節",
            r"会计师事务所", r"會計師事務所",
            r"董事会报告", r"董事會報告", r"主席報告",
        ],
    },
}


# ============ 下载 PDF ============ #

def download_pdf(url: str) -> Path:
    """下载 PDF 到缓存。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    fname = re.sub(r"[^A-Za-z0-9_.-]", "_", url.rsplit("/", 1)[-1])[:80]
    path = CACHE_DIR / f"{h}_{fname}"
    if not path.suffix.lower().endswith("pdf"):
        path = path.with_suffix(path.suffix + ".pdf")
    if path.exists() and path.stat().st_size > 1024:
        print(f"[pdf_extract] cached: {path}", file=sys.stderr)
        return path
    print(f"[pdf_extract] downloading: {url}", file=sys.stderr)
    r = fetch(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"PDF 下载失败 HTTP {r.status_code}")
    path.write_bytes(r.content)
    print(f"[pdf_extract] saved: {path} ({path.stat().st_size/1024:.0f} KB)", file=sys.stderr)
    return path


# ============ PDF 文本提取（双重 fallback：pdfplumber → OCR） ============ #

CID_PATTERN = re.compile(r"\(cid:\d+\)")


def _is_text_garbled(txt: str, min_chars: int = 100) -> bool:
    """检测文本是否充满 CID 编码（港股年报常见问题）"""
    if not txt or len(txt) < min_chars:
        return False
    cid_count = len(CID_PATTERN.findall(txt))
    real_chars = len(re.sub(r"\(cid:\d+\)|\s", "", txt))
    if real_chars == 0:
        return cid_count > 0
    # 如果 CID 标记数 > 真实字符数，认为是图片化字体
    return cid_count > real_chars * 0.3


def _extract_with_pdfplumber(pdf_path: Path, max_pages: int) -> tuple[list[str], list[int], int]:
    """pdfplumber 文本抽取。返回 (texts, page_nums, garbled_pages_count)"""
    import pdfplumber

    pages_text = []
    pages_num = []
    garbled = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        print(f"[pdf_extract] {total} 页（pdfplumber）", file=sys.stderr)
        for i, page in enumerate(pdf.pages):
            if i >= max_pages:
                break
            try:
                txt = page.extract_text() or ""
            except Exception as e:  # noqa: BLE001
                print(f"[pdf_extract] page {i+1} pdfplumber failed: {e}", file=sys.stderr)
                txt = ""
            if _is_text_garbled(txt):
                garbled += 1
            pages_text.append(txt)
            pages_num.append(i + 1)
    return pages_text, pages_num, garbled


def _extract_with_ocr(
    pdf_path: Path,
    max_pages: int,
    lang: str = "chi_sim+chi_tra+eng",
    dpi: int = 200,
) -> tuple[list[str], list[int]]:
    """用 pdf2image + pytesseract 做 OCR fallback。

    依赖：
      - poppler （brew install poppler）
      - tesseract + chi_sim/chi_tra 语言包（brew install tesseract tesseract-lang）
      - pip install pdf2image pytesseract
    """
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as e:
        print(
            f"[pdf_extract] OCR fallback 需要 pdf2image + pytesseract（{e}）",
            file=sys.stderr,
        )
        return [], []

    print(
        f"[pdf_extract] 启用 OCR fallback（lang={lang}, dpi={dpi}），"
        f"OCR 较慢，{max_pages} 页约需 {max_pages * 2}s",
        file=sys.stderr,
    )

    pages_text = []
    pages_num = []
    # 流式按页 OCR（一次性 convert 全 PDF 内存炸）
    try:
        # convert_from_path 支持 first_page/last_page，逐批处理
        batch = 10
        for start in range(1, max_pages + 1, batch):
            end = min(start + batch - 1, max_pages)
            try:
                images = convert_from_path(
                    str(pdf_path),
                    dpi=dpi,
                    first_page=start,
                    last_page=end,
                )
            except Exception as e:  # noqa: BLE001
                print(f"[pdf_extract] pdf2image page {start}-{end} failed: {e}", file=sys.stderr)
                continue
            for offset, img in enumerate(images):
                page_num = start + offset
                try:
                    txt = pytesseract.image_to_string(img, lang=lang) or ""
                except Exception as e:  # noqa: BLE001
                    print(f"[pdf_extract] OCR p.{page_num} failed: {e}", file=sys.stderr)
                    txt = ""
                pages_text.append(txt)
                pages_num.append(page_num)
                if page_num % 20 == 0:
                    print(f"[pdf_extract] OCR 进度 {page_num}/{max_pages}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[pdf_extract] OCR 主循环失败: {e}", file=sys.stderr)

    return pages_text, pages_num


def extract_full_text(
    pdf_path: Path,
    max_pages: int = 500,
    force_ocr: bool = False,
) -> tuple[list[str], list[int]]:
    """返回 (per_page_text_list, page_numbers)。

    流程：
      1. 先 pdfplumber 抽取
      2. 检测是否大量 CID 乱码（港股年报通病）
      3. 乱码占比 > 30% 时自动启用 OCR fallback（如果系统装了 tesseract+poppler）

    force_ocr=True：跳过 pdfplumber，直接用 OCR（用户主动指定）
    """
    if force_ocr:
        return _extract_with_ocr(pdf_path, max_pages)

    pages_text, pages_num, garbled = _extract_with_pdfplumber(pdf_path, max_pages)
    total = len(pages_text)
    if total > 0 and garbled / total > 0.3:
        print(
            f"[pdf_extract] 检测到 {garbled}/{total} 页 CID 乱码（占比 {garbled/total:.0%}），"
            "切换到 OCR fallback",
            file=sys.stderr,
        )
        ocr_texts, ocr_nums = _extract_with_ocr(pdf_path, max_pages)
        if ocr_texts:
            return ocr_texts, ocr_nums
        else:
            print(
                "[pdf_extract] OCR 失败，回退到原始 pdfplumber 输出（含 CID 乱码）",
                file=sys.stderr,
            )
    return pages_text, pages_num


# ============ 章节定位 ============ #

def find_section(pages_text: list[str], pages_num: list[int], section_key: str) -> dict:
    """按章节关键词定位，截取从 start 到 end 之间的文本（含跨页）。"""
    cfg = SECTION_PATTERNS[section_key]

    # 找 start：在每页全文（含换行）中搜起始正则
    start_page_idx = None
    start_line_idx = None
    for i, txt in enumerate(pages_text):
        lines = txt.split("\n")
        for j, line in enumerate(lines):
            line_strip = line.strip()
            for pat in cfg["starts"]:
                if re.search(pat, line_strip, re.IGNORECASE):
                    start_page_idx = i
                    start_line_idx = j
                    break
            if start_page_idx is not None:
                break
        if start_page_idx is not None:
            break

    if start_page_idx is None:
        return {"label": cfg["label"], "found": False, "text": "", "start_page": None}

    # 找 end：从 start 之后开始搜
    end_page_idx = None
    end_line_idx = None
    for i in range(start_page_idx, len(pages_text)):
        lines = pages_text[i].split("\n")
        start_j = (start_line_idx + 1) if i == start_page_idx else 0
        for j in range(start_j, len(lines)):
            line_strip = lines[j].strip()
            for pat in cfg["ends"]:
                if re.search(pat, line_strip):
                    # 排除：end 应在 start 之后至少 5 行（防止误命中）
                    if i == start_page_idx and j - start_line_idx < 5:
                        continue
                    end_page_idx = i
                    end_line_idx = j
                    break
            if end_page_idx is not None:
                break
        if end_page_idx is not None:
            break

    # 拼接文本
    out_lines = []
    for i in range(start_page_idx, (end_page_idx if end_page_idx is not None else len(pages_text))):
        lines = pages_text[i].split("\n")
        s = (start_line_idx) if i == start_page_idx else 0
        e = (end_line_idx) if (end_page_idx is not None and i == end_page_idx) else len(lines)
        out_lines.extend(lines[s:e])

    text = "\n".join(line for line in out_lines if line.strip())
    # 截断超长（防止单章节几十页 MD&A）
    if len(text) > 30000:
        text = text[:30000] + "\n\n[...章节过长，已截断到 30000 字符...]"

    return {
        "label": cfg["label"],
        "found": True,
        "text": text,
        "start_page": pages_num[start_page_idx],
        "end_page": pages_num[end_page_idx] if end_page_idx is not None else None,
        "char_count": len(text),
    }


# ============ 主流程 ============ #

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", help="PDF URL（巨潮 / 披露易）")
    ap.add_argument("--file", help="本地 PDF 路径")
    ap.add_argument(
        "--sections",
        default="all",
        help=f"逗号分隔的章节 key（all / {','.join(SECTION_PATTERNS)}）",
    )
    ap.add_argument("--max-pages", type=int, default=500)
    ap.add_argument("--force-ocr", action="store_true", help="强制走 OCR（跳过 pdfplumber）")
    ap.add_argument("--format", choices=["json", "text"], default="json")
    args = ap.parse_args()

    if not args.url and not args.file:
        ap.error("--url 或 --file 至少提供一个")

    # 拿 PDF 文件
    if args.file:
        pdf_path = Path(args.file).expanduser()
        if not pdf_path.exists():
            ap.error(f"找不到文件：{pdf_path}")
    else:
        pdf_path = download_pdf(args.url)

    # 抽全文
    pages_text, pages_num = extract_full_text(
        pdf_path, max_pages=args.max_pages, force_ocr=args.force_ocr
    )

    # 抽各章节
    if args.sections == "all":
        sections = list(SECTION_PATTERNS)
    else:
        sections = [s.strip() for s in args.sections.split(",") if s.strip() in SECTION_PATTERNS]

    result: dict = {
        "pdf": str(pdf_path),
        "url": args.url or "",
        "total_pages": len(pages_text),
        "sections": {},
    }
    for sec in sections:
        info = find_section(pages_text, pages_num, sec)
        result["sections"][sec] = info
        status = (
            f"OK p.{info['start_page']}-{info['end_page']} ({info['char_count']:,} 字)"
            if info.get("found")
            else "未找到"
        )
        print(f"[pdf_extract] {sec:20s} → {status}", file=sys.stderr)

    if args.format == "json":
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(f"# 年报关键章节抽取结果\n\n_PDF: {result['pdf']}_\n_共 {result['total_pages']} 页_\n")
        for sec, info in result["sections"].items():
            print(f"## {info['label']}")
            if info.get("found"):
                print(f"_位置：第 {info['start_page']} 页 ~ 第 {info['end_page']} 页_\n")
                # 只打印前 2000 字
                txt = info["text"]
                if len(txt) > 2000:
                    print(txt[:2000])
                    print(f"\n_...(已截断，完整 {info['char_count']:,} 字请查看 JSON 输出)_")
                else:
                    print(txt)
            else:
                print("_(未找到该章节)_")
            print()


if __name__ == "__main__":
    main()

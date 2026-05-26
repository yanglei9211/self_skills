#!/usr/bin/env python3
"""
板块扫描：输入板块名 → 输出板块整体表现、龙头股、热门股、风险股。

数据流：
  1. 同花顺 q.10jqka.com.cn 拿到 板块名 → 板块代码 映射（concept_map / industry_map）
  2. 同花顺板块详情页拿成分股代码列表
  3. 雪球 quotec 批量拉所有成分股的实时行情
  4. 按不同维度排序：
     - 龙头股：按总市值降序（前 N）
     - 涨幅榜：按 percent 降序
     - 热门股：按成交额降序
     - 资金流榜：按主力净流入降序（如果数据有）
     - 风险股：按 percent 升序，含 ST 名标记

Usage:
  # 列出所有可用板块（涨跌幅排序）
  python3 scan_sector.py --list
  python3 scan_sector.py --list --type concept

  # 扫描板块
  python3 scan_sector.py --sector "AI PC"
  python3 scan_sector.py --sector "白酒概念" --top 10
  python3 scan_sector.py --sector "半导体" --type industry

  # 输出格式
  python3 scan_sector.py --sector "AI PC" --format json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[2] / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from stock_core.http import fetch  # noqa: E402
from stock_core.symbols import parts_to_symbol  # noqa: E402
from stock_core.tz import CN_TZ  # noqa: E402
from stock_core.xueqiu import XueqiuClient  # noqa: E402


# ============ 板块名 → 代码 映射 ============ #

def get_sector_map(kind: str = "concept") -> dict:
    """同花顺板块映射。kind: concept(概念) / industry(行业)"""
    from lxml import html
    if kind == "concept":
        url = "https://q.10jqka.com.cn/gn/"
    else:
        url = "https://q.10jqka.com.cn/thshy/"
    r = fetch(url)
    r.encoding = "gbk"
    tree = html.fromstring(r.text)
    out: dict = {}
    selectors = [
        "//a[contains(@href,'detail/code/')]",
        "//a[contains(@href,'index/code/')]",
    ]
    for sel in selectors:
        for a in tree.xpath(sel):
            name = (a.text or "").strip()
            href = a.get("href", "")
            m = re.search(r"code/(\d+)", href)
            if m and name and 1 < len(name) <= 30:
                out[name] = m.group(1)
    return out


def find_sector_code(sector_name: str) -> tuple[str, str] | None:
    """在 concept + industry 映射里模糊查找板块。返回 (kind, code) 或 None。"""
    for kind in ("concept", "industry"):
        try:
            m = get_sector_map(kind)
        except Exception as e:  # noqa: BLE001
            print(f"[scan_sector] get_{kind}_map failed: {e}", file=sys.stderr)
            continue
        if sector_name in m:
            return kind, m[sector_name]
        # 模糊匹配
        for k, v in m.items():
            if sector_name in k or k in sector_name:
                print(
                    f"[scan_sector] 模糊匹配：'{sector_name}' → '{k}' (code={v})",
                    file=sys.stderr,
                )
                return kind, v
    return None


# ============ 板块成分股 ============ #

def get_sector_constituents(kind: str, code: str, max_pages: int = 5) -> list[str]:
    """
    抓同花顺板块详情页的成分股代码列表。
    URL 模式：https://q.10jqka.com.cn/{kind}/detail/code/{code}/
    或者：    https://q.10jqka.com.cn/{kind}/detail/order/desc/ajax/1/code/{code}
    """
    from lxml import html
    kind_path = "gn" if kind == "concept" else "thshy"
    url_templates = [
        f"https://q.10jqka.com.cn/{kind_path}/detail/code/{code}/",
        f"https://q.10jqka.com.cn/{kind_path}/detail/page/{{page}}/order/desc/ajax/1/code/{code}/",
        f"https://q.10jqka.com.cn/{kind_path}/index/code/{code}/",
    ]
    codes: list[str] = []
    seen = set()
    for tpl in url_templates:
        for page in range(1, max_pages + 1):
            url = tpl.format(page=page) if "{page}" in tpl else tpl
            try:
                r = fetch(url, retries=1)
                r.encoding = "gbk"
            except Exception:
                continue
            # 成分股的代码通常在 <td> 文本里（6 位数字，sh/sz）
            tree = html.fromstring(r.text)
            for tr in tree.xpath("//table//tr"):
                tds = tr.xpath("./td")
                if len(tds) < 3:
                    continue
                # 第一个或第二个 td 是 6 位数字代码
                for td in tds[:3]:
                    txt = td.text_content().strip()
                    m = re.match(r"^(\d{6})$", txt)
                    if m and m.group(1) not in seen:
                        seen.add(m.group(1))
                        codes.append(m.group(1))
                        break
            if "ajax" not in tpl:
                # 静态页只爬一遍
                break
            if len(codes) >= 200:
                break
        if codes:
            break
    return codes


# 历史包袱：本文件原有一个 ``code_to_xueqiu`` 私有函数，规则不完整
# （漏了 5/9/1 开头的上交所 ETF / B 股 / 深交所 ETF/LOF 前缀），
# 已统一改用 ``shared.stock_core.symbols.parts_to_symbol("a", code)``，
# supply_chain.py 也跟着切换；后续新代码请直接用 parts_to_symbol。


# ============ 板块扫描主流程 ============ #

def scan_sector(sector_name: str, kind: str | None = None, top: int = 10) -> dict:
    """完整板块扫描流程。"""
    found = find_sector_code(sector_name)
    if not found:
        return {"error": f"未找到板块：{sector_name}"}
    sector_kind, code = found
    print(f"[scan_sector] {sector_name} → kind={sector_kind} code={code}", file=sys.stderr)

    constituents = get_sector_constituents(sector_kind, code)
    print(f"[scan_sector] 成分股: {len(constituents)} 只", file=sys.stderr)

    if not constituents:
        return {"error": f"未抓到 {sector_name} 的成分股"}

    # 雪球批量拿行情（一次最多 50 个，分批）
    cli = XueqiuClient()
    batch_size = 50
    quotes: list[dict] = []
    for i in range(0, len(constituents), batch_size):
        batch = constituents[i : i + batch_size]
        symbols = [parts_to_symbol("a", c) for c in batch]
        try:
            qs = cli.quotes(symbols)
            quotes.extend(qs)
        except Exception as e:  # noqa: BLE001
            print(f"[scan_sector] quotes batch fail: {e}", file=sys.stderr)

    # 雪球 quotec 没有 name 字段，需要再查 screener 或同花顺成分页拿名字
    # 临时方案：从同花顺成分股表里抽 (code, name) 映射
    name_map = _get_name_map_from_ths(sector_kind, code)
    for q in quotes:
        sym = q.get("symbol", "")
        digits = re.sub(r"\D", "", sym)
        q["name"] = name_map.get(digits, "")
        q["amount_yi"] = (q.get("amount") or 0) / 1e8
        q["market_cap_yi"] = (q.get("market_capital") or 0) / 1e8
        q["is_st"] = "ST" in (q.get("name") or "")

    # 排序生成各榜单
    by_cap = sorted(quotes, key=lambda x: x.get("market_capital") or 0, reverse=True)
    by_pct = sorted(quotes, key=lambda x: x.get("percent") or -999, reverse=True)
    by_amount = sorted(quotes, key=lambda x: x.get("amount") or 0, reverse=True)
    by_loss = sorted(quotes, key=lambda x: x.get("percent") if x.get("percent") is not None else 999)
    risk_stocks = [
        q for q in quotes
        if q.get("is_st") or (q.get("percent") is not None and q["percent"] <= -7)
    ]

    # 板块整体统计
    pcts = [q.get("percent") for q in quotes if q.get("percent") is not None]
    avg_pct = sum(pcts) / len(pcts) if pcts else 0
    up_count = sum(1 for p in pcts if p > 0)
    down_count = sum(1 for p in pcts if p < 0)
    flat_count = len(pcts) - up_count - down_count

    return {
        "sector": sector_name,
        "kind": sector_kind,
        "code": code,
        "fetched_at": datetime.now(CN_TZ).isoformat(),
        "constituents_count": len(constituents),
        "quoted_count": len(quotes),
        "summary": {
            "avg_percent": round(avg_pct, 2),
            "up": up_count,
            "down": down_count,
            "flat": flat_count,
        },
        "leaders_by_cap": by_cap[:top],          # 龙头股（按市值）
        "top_gainers": by_pct[:top],             # 涨幅榜
        "top_amount": by_amount[:top],           # 成交额榜（市场关注度）
        "top_losers": by_loss[:top],             # 跌幅榜
        "risk_stocks": risk_stocks[:top],        # 风险股（ST + 大跌）
    }


def _get_name_map_from_ths(kind: str, code: str) -> dict[str, str]:
    """从同花顺板块成分页抽 (代码, 名称) 映射。"""
    from lxml import html
    kind_path = "gn" if kind == "concept" else "thshy"
    url = f"https://q.10jqka.com.cn/{kind_path}/detail/code/{code}/"
    try:
        r = fetch(url, retries=1)
        r.encoding = "gbk"
    except Exception:
        return {}
    tree = html.fromstring(r.text)
    out: dict[str, str] = {}
    for tr in tree.xpath("//table//tr"):
        tds = [td.text_content().strip() for td in tr.xpath("./td")]
        if len(tds) >= 3 and re.match(r"^\d{6}$", tds[1] if len(tds) > 1 else ""):
            out[tds[1]] = tds[2]
        elif len(tds) >= 3 and re.match(r"^\d{6}$", tds[0]):
            out[tds[0]] = tds[1]
    return out


# ============ 列出所有板块 ============ #

def list_sectors(kind: str = "concept", top: int = 50) -> None:
    """列出可用板块（按涨幅排序就 OK）。"""
    m = get_sector_map(kind)
    print(f"# {kind} 板块共 {len(m)} 个", file=sys.stderr)
    for name, code in sorted(m.items())[:top]:
        print(f"  {name:30s} → {code}")


# ============ 输出渲染 ============ #

def render_text(data: dict, top: int = 10) -> str:
    if "error" in data:
        return f"❌ {data['error']}"

    s = data["summary"]
    out = []
    out.append(f"# 📊 板块扫描：{data['sector']} ({data['kind']} / {data['code']})")
    out.append(f"_数据抓取时间：{data['fetched_at']}_")
    out.append("")
    out.append("## 一、板块概览")
    out.append(f"- 成分股：**{data['constituents_count']} 只**（成功取到行情 {data['quoted_count']} 只）")
    out.append(f"- 平均涨跌：**{s['avg_percent']:+.2f}%**")
    out.append(f"- 涨/跌/平：**{s['up']} / {s['down']} / {s['flat']}**")
    out.append("")

    def _table(title: str, items: list[dict], cols: list[tuple[str, str]]):
        if not items:
            return
        out.append(f"## {title}")
        out.append("| " + " | ".join(c[0] for c in cols) + " |")
        out.append("|" + "|".join("---" for _ in cols) + "|")
        for it in items[:top]:
            row = []
            for label, key in cols:
                v = it.get(key)
                if v is None:
                    row.append("-")
                elif key == "percent":
                    row.append(f"{v:+.2f}%")
                elif key in ("amount_yi", "market_cap_yi"):
                    row.append(f"{v:.0f}亿" if v >= 1 else f"{v:.2f}亿")
                else:
                    row.append(str(v))
            out.append("| " + " | ".join(row) + " |")
        out.append("")

    cols_basic = [
        ("代码", "symbol"),
        ("名称", "name"),
        ("现价", "current"),
        ("涨跌幅", "percent"),
        ("成交额", "amount_yi"),
        ("市值", "market_cap_yi"),
    ]
    _table("二、龙头股（按总市值）", data["leaders_by_cap"], cols_basic)
    _table("三、涨幅榜", data["top_gainers"], cols_basic)
    _table("四、成交额榜（资金关注）", data["top_amount"], cols_basic)
    _table("五、跌幅榜（弱势股）", data["top_losers"], cols_basic)
    _table("六、⚠️ 风险股（ST + 大跌）", data["risk_stocks"], cols_basic)

    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--sector", help="板块名（必需，除非 --list）")
    ap.add_argument("--type", dest="kind", choices=["concept", "industry"], help="只搜某一种")
    ap.add_argument("--list", action="store_true", help="列出所有可用板块")
    ap.add_argument("--top", type=int, default=10, help="每榜单条数（默认 10）")
    ap.add_argument("--format", choices=["json", "text"], default="text")
    args = ap.parse_args()

    if args.list:
        list_sectors(args.kind or "concept")
        return

    if not args.sector:
        ap.error("--sector 必须提供")

    data = scan_sector(args.sector, args.kind, top=args.top)

    if args.format == "json":
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
    else:
        print(render_text(data, top=args.top))


if __name__ == "__main__":
    main()

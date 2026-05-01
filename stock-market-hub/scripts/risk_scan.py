#!/usr/bin/env python3
"""
风险信号规则引擎：扫描全市场或指定股票池，输出高风险股票及命中信号。

规则集（多个命中即列入观察）：
  R1. 名称含 ST / *ST / SST       (从雪球 screener st 市场拿)
  R2. 单日跌停（A 股 ≤ -10%；创业板/科创板 ≤ -20%；ST ≤ -5%）
  R3. 短期暴跌：5 日跌幅 ≤ -15%
  R4. 财务恶化：roe_ttm ≤ -10% 或 net_profit_cagr ≤ -50%
  R5. 异常资金外流：main_net_inflows < -5 亿
  R6. 立案调查 / 风险提示公告（巨潮关键词搜索）
  R7. 退市预警：searchkey 包含 "终止上市" "退市风险警示"
  R8. 大股东高比例减持：searchkey "减持" 且时间窗口 7 天

Usage:
  # 全市场扫描（默认走 R1+R2，最快）
  python3 risk_scan.py

  # 指定规则
  python3 risk_scan.py --rules R1,R2,R5

  # 全规则（含基于公告的，慢）
  python3 risk_scan.py --all

  # 指定市场
  python3 risk_scan.py --market all_a   # 沪深主板 (default)
  python3 risk_scan.py --market st       # 仅 ST 股

  # 输出格式
  python3 risk_scan.py --format json
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from core.xueqiu import XueqiuClient  # type: ignore

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


CN_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else timezone.utc


# ============ 各规则实现 ============ #

def rule_st_stocks(market: str = "all_a") -> list[dict]:
    """R1: ST 风险股名单。

    NOTE 雪球 'st' 市场分类宽泛地包含一些"风险警示板"股票（如太极股份/中创智领等
    被风险警示但名字不带 ST 的）。这里用 name 真实校验：
      - 名字含 *ST → severity=5（极高，含退市预警）
      - 名字含 ST  → severity=4（高，戴帽中）
      - 来自 st 市场但 name 不含 → severity=3 标 RISK_BOARD（风险警示板）
    """
    cli = XueqiuClient()
    items = cli.screener("st", "percent", "asc", size=200).get("list", [])
    out = []
    for q in items:
        name = (q.get("name") or "").upper()
        clean_name = name.replace(" ", "")
        if "*ST" in clean_name:
            signals = ["ST_NAME"]
            severity = 5
        elif "ST" in clean_name and not clean_name.startswith("STAR"):
            # 排除 starbucks/star 这种以 ST 开头的英文名
            signals = ["ST_NAME"]
            severity = 4
        else:
            # 雪球 st 市场命中但名字不含 ST → 风险警示板（非 ST 戴帽）
            signals = ["RISK_BOARD"]
            severity = 3
        out.append({
            **q,
            "_signals": signals,
            "_severity": severity,
        })
    return out


def rule_limit_down(market: str = "all_a") -> list[dict]:
    """R2: 当日跌停（A 股 ≤ -9.9%；创业板/科创板 ≤ -19.9%）"""
    cli = XueqiuClient()
    out = []

    # 主板：跌幅 ≤ -9.9%
    items = cli.screener(market, "percent", "asc", 50).get("list", [])
    for q in items[:50]:
        pct = q.get("percent")
        if pct is None:
            continue
        is_gem_kcb = (q.get("symbol") or "").startswith(("SZ30", "SH68"))
        threshold = -19.5 if is_gem_kcb else -9.5
        if pct <= threshold:
            q["_signals"] = ["LIMIT_DOWN"]
            q["_severity"] = 4
            out.append(q)
    return out


def rule_short_term_crash(market: str = "all_a") -> list[dict]:
    """R3: 短期暴跌 — 用 current_year_percent 做近似（更准确需要 K 线）"""
    cli = XueqiuClient()
    out = []
    # 雪球 screener 没有"近 5 日"字段，但有 current_year_percent
    # 用一个粗略的代理：当日跌 ≤ -7% 的（不够暴跌但有迹象）
    items = cli.screener(market, "percent", "asc", 30).get("list", [])
    for q in items:
        pct = q.get("percent")
        if pct is not None and -9.5 < pct <= -5:
            q["_signals"] = ["SHARP_DROP"]
            q["_severity"] = 3
            out.append(q)
    return out


def rule_financial_decay(market: str = "all_a") -> list[dict]:
    """R4: 财务恶化 — ROE 极差 / 净利润大幅下滑"""
    cli = XueqiuClient()
    # 拉一个大池子，按 net_profit_cagr 升序
    items = cli.screener(market, "net_profit_cagr", "asc", 30).get("list", [])
    out = []
    for q in items:
        cagr = q.get("net_profit_cagr")
        roe = q.get("roe_ttm")
        signals = []
        if cagr is not None and cagr <= -50:
            signals.append("PROFIT_CAGR_NEGATIVE")
        if roe is not None and roe <= -10:
            signals.append("ROE_NEGATIVE")
        if signals:
            q["_signals"] = signals
            q["_severity"] = 3
            out.append(q)
    return out


def rule_money_outflow(market: str = "all_a") -> list[dict]:
    """R5: 主力资金大额净流出"""
    cli = XueqiuClient()
    items = cli.screener(market, "main_net_inflows", "asc", 20).get("list", [])
    out = []
    for q in items:
        flow = q.get("main_net_inflows")
        if flow is not None and flow <= -5e8:  # ≤ -5 亿
            q["_signals"] = ["MAIN_OUTFLOW"]
            q["_severity"] = 3
            out.append(q)
    return out


def rule_announcement_keyword(keyword: str, days: int = 7) -> list[dict]:
    """R6/R7/R8: 公告关键词搜索（依赖 fetch_announcements）"""
    try:
        from fetch_announcements import cninfo_announcements  # type: ignore
    except ImportError as e:
        print(f"[risk_scan] 无法导入 fetch_announcements: {e}", file=sys.stderr)
        return []

    items = cninfo_announcements(keyword=keyword, days=days, page_size=50)
    # 把公告聚合成"按股票"
    by_stock: dict = {}
    for ann in items:
        sym = ann.get("symbol")
        if not sym:
            continue
        if sym not in by_stock:
            by_stock[sym] = {
                "symbol": "SH" + sym if sym.startswith("6") else "SZ" + sym,
                "name": ann.get("name"),
                "_signals": [],
                "_announcements": [],
                "_severity": 0,
            }
        by_stock[sym]["_announcements"].append({
            "date": ann["date"],
            "title": ann["title"],
            "pdf_url": ann["pdf_url"],
        })

    severity_map = {
        "立案调查": 5, "终止上市": 5, "退市": 5,
        "风险提示": 4, "实际控制人变更": 3,
        "业绩预亏": 3, "商誉减值": 3, "减持": 2,
    }
    out = []
    for sym, item in by_stock.items():
        item["_signals"].append(f"ANNOUNCEMENT:{keyword}")
        item["_severity"] = severity_map.get(keyword, 3)
        out.append(item)
    return out


# ============ 主流程 ============ #

RULES = {
    "R1": ("ST 风险股名单", rule_st_stocks),
    "R2": ("当日跌停", rule_limit_down),
    "R3": ("短期暴跌(-5%~-9.5%)", rule_short_term_crash),
    "R4": ("财务恶化(ROE/净利润)", rule_financial_decay),
    "R5": ("主力资金净流出 > 5亿", rule_money_outflow),
    "R6": ("公告:立案调查", lambda m="": rule_announcement_keyword("立案调查", 30)),
    "R7": ("公告:退市风险警示", lambda m="": rule_announcement_keyword("终止上市", 30)),
    "R8": ("公告:风险提示", lambda m="": rule_announcement_keyword("风险提示", 14)),
}


def merge_signals(all_results: dict[str, list[dict]]) -> list[dict]:
    """同一股票被多规则命中时合并 _signals。"""
    merged: dict[str, dict] = {}
    for rule_id, items in all_results.items():
        for q in items:
            sym = q.get("symbol")
            if not sym:
                continue
            if sym not in merged:
                merged[sym] = {
                    "symbol": sym,
                    "name": q.get("name"),
                    "current": q.get("current"),
                    "percent": q.get("percent"),
                    "market_cap_yi": (q.get("market_capital") or 0) / 1e8,
                    "main_inflow_yi": (q.get("main_net_inflows") or 0) / 1e8,
                    "roe_ttm": q.get("roe_ttm"),
                    "net_profit_cagr": q.get("net_profit_cagr"),
                    "_signals": [],
                    "_announcements": [],
                    "_severity": 0,
                    "_rules_hit": [],
                }
            merged[sym]["_signals"].extend(q.get("_signals", []))
            merged[sym]["_announcements"].extend(q.get("_announcements", []))
            merged[sym]["_severity"] = max(merged[sym]["_severity"], q.get("_severity", 0))
            merged[sym]["_rules_hit"].append(rule_id)
    return sorted(
        merged.values(),
        key=lambda x: (-x["_severity"], -len(x["_rules_hit"])),
    )


def render_text(results: list[dict], rules_run: list[str]) -> str:
    out = []
    out.append(f"# 🚨 全市场风险扫描报告 — {datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M (%Z)')}")
    out.append(f"_启用规则：{', '.join(rules_run)} ({len(rules_run)} 条)_")
    out.append(f"_命中股票：**{len(results)}** 只_")
    out.append("")

    # 按 severity 分组
    groups: dict[int, list] = {}
    for it in results:
        groups.setdefault(it["_severity"], []).append(it)

    severity_label = {
        5: "🔴 极高风险（立案调查/退市/*ST）",
        4: "🟠 高风险（ST/跌停/风险提示）",
        3: "🟡 中风险（暴跌/资金大额流出/财务恶化）",
        2: "🟢 关注（减持/温和异动）",
    }

    for sev in sorted(groups.keys(), reverse=True):
        out.append(f"## {severity_label.get(sev, f'级别 {sev}')}")
        out.append("")
        out.append("| 代码 | 名称 | 现价 | 涨跌幅 | 市值 | 命中信号 |")
        out.append("|---|---|---|---|---|---|")
        for it in groups[sev][:30]:
            name = it.get("name") or ""
            cur = it.get("current") or "-"
            pct = it.get("percent")
            pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "-"
            cap = it.get("market_cap_yi") or 0
            sigs = ",".join(set(it.get("_signals") or []))[:60]
            out.append(f"| `{it['symbol']}` | {name} | {cur} | {pct_str} | {cap:.0f}亿 | {sigs} |")
        out.append("")

        # 显示其中包含公告的样例
        anns_items = [it for it in groups[sev] if it.get("_announcements")]
        if anns_items[:3]:
            out.append("### 关键公告示例")
            for it in anns_items[:5]:
                out.append(f"- **{it.get('name')} ({it['symbol']})**:")
                for ann in (it.get("_announcements") or [])[:2]:
                    out.append(f"  - `{ann['date']}` {ann['title'][:60]}  [PDF]({ann['pdf_url']})")
            out.append("")

    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--rules", default="R1,R2,R5", help="启用的规则，逗号分隔（默认 R1,R2,R5 最快）")
    ap.add_argument("--all", action="store_true", help="启用全部规则（R1-R8）")
    ap.add_argument("--market", default="all_a", help="默认全 A 股（all_a/hk/us/st/...）")
    ap.add_argument("--format", choices=["json", "text"], default="text")
    args = ap.parse_args()

    if args.all:
        rules_run = list(RULES.keys())
    else:
        rules_run = [r.strip().upper() for r in args.rules.split(",") if r.strip()]
        rules_run = [r for r in rules_run if r in RULES]

    if not rules_run:
        ap.error(f"无有效规则。可选：{','.join(RULES)}")

    print(f"[risk_scan] 启用规则: {rules_run}", file=sys.stderr)
    all_results: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        future_map = {
            ex.submit(RULES[r][1], args.market): r for r in rules_run
        }
        for fut in as_completed(future_map):
            r = future_map[fut]
            try:
                items = fut.result()
                all_results[r] = items
                print(f"[risk_scan] {r}: {len(items)} 命中", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                all_results[r] = []
                print(f"[risk_scan] {r}: FAIL {type(e).__name__}: {e}", file=sys.stderr)

    merged = merge_signals(all_results)

    if args.format == "json":
        json.dump(
            {"timestamp": datetime.now(CN_TZ).isoformat(), "rules_run": rules_run, "results": merged},
            sys.stdout, ensure_ascii=False, indent=2, default=str,
        )
        print()
    else:
        print(render_text(merged, rules_run))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""靶向扫描：找出所有 RESONANCE_INFLOW + 位置不过高的标的。

与 scan_fund_flow.py 的区别：
  - 直接使用 cross_validation.verdict 做筛选，不只是看 10d/20d 正负
  - 扩大股池：按市值 10 页 + 按成交额 3 页，交叉去重
  - 评分体系更精细：共振流入 > 持续流入 > 其他
"""
from __future__ import annotations

import sys
sys.path.insert(0, "stock-portfolio-copilot/scripts")
sys.path.insert(0, "shared")

from concurrent.futures import ThreadPoolExecutor, as_completed
from stock_core.xueqiu import XueqiuClient
from stock_core.fund_flow import (
    fetch_daily_fund_flow, summarize_fund_flow,
    _window_summary, cross_validate,
)
from stock_core.kline import fetch_daily_kline
from stock_core.symbols import normalize_symbol


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = sum(max(closes[i] - closes[i - 1], 0) for i in range(-period, 0))
    losses = sum(abs(min(closes[i] - closes[i - 1], 0)) for i in range(-period, 0))
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def analyze_one(stock: dict) -> dict | None:
    symbol = stock.get("symbol", "")
    try:
        mkt, code, xq_sym = normalize_symbol(symbol)
    except Exception:
        return None
    if mkt != "a" or code.startswith(("4", "8")):
        return None

    name = stock.get("name", "")
    mcap = (stock.get("market_capital") or 0) / 1e8
    pe = stock.get("pe_ttm")

    # 1. 资金流 + 交叉验证
    try:
        rows = fetch_daily_fund_flow(mkt, code)
        if not rows or len(rows) < 20:
            return None
        summary = summarize_fund_flow(rows)
        rolling = summary.get("rolling") or {}
        cross = summary.get("cross_validation") or {}

        f10 = (rolling.get("10d") or {}).get("main_yi")
        f20 = (rolling.get("20d") or {}).get("main_yi")
        f5 = (rolling.get("5d") or {}).get("main_yi")
        verdict = cross.get("verdict", "")
        all_aligned = cross.get("all_aligned", False)
        acceleration = cross.get("acceleration", "")
        is_resonance = cross.get("is_resonance", False)
        directions = cross.get("directions", {})
        conflict = cross.get("short_long_conflict", False)
        concentration = cross.get("concentration_5d_in_20d")
    except Exception:
        return None

    # 2. K线位置
    try:
        kline = fetch_daily_kline(xq_sym, mkt, count=120)
        if not kline or len(kline) < 60:
            return None
        closes = [k["close"] for k in kline if k.get("close")]
        if len(closes) < 60:
            return None
        current = closes[-1]
        high_60d = max(closes[-60:])
        high_120d = max(closes) if len(closes) >= 120 else high_60d
        pos_60 = current / high_60d if high_60d > 0 else 1.0
        pos_120 = current / high_120d if high_120d > 0 else 1.0
        rsi = compute_rsi(closes)
        chg_20d = (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 else None
    except Exception:
        return None

    # 3. 评分
    score = 0.0

    # 资金流基础分（20d 金额，亿）
    if f20:
        score += max(0, f20) * 2  # 每亿 2 分
    if f10 and f20 and f10 > f20 / 2:
        score += 2  # 10日占比高 = 近期加速

    # 交叉验证加分
    verdict_bonus = {
        "RESONANCE_INFLOW": 15,
        "PERSISTENT_INFLOW_STEADY": 8,
        "REVERSAL_INFLOW_CONFIRMED": 10,
        "DECELERATING_INFLOW": 3,
        "WEAKENING_OUTFLOW": 5,
        "MIXED": 0,
    }
    score += verdict_bonus.get(verdict, 0)

    # 位置加分（越低越好）
    if pos_60 and pos_60 <= 0.75:
        score += 8
    elif pos_60 and pos_60 <= 0.80:
        score += 5
    elif pos_60 and pos_60 <= 0.85:
        score += 3
    elif pos_60 and pos_60 <= 0.90:
        score += 1

    # RSI加分（不是过热）
    if rsi and rsi < 30:
        score += 4
    elif rsi and rsi < 40:
        score += 2
    elif rsi and rsi < 50:
        score += 1

    # 冲突扣分
    if conflict:
        score -= 8
    if concentration and concentration >= 0.5:
        score -= 3  # 集中度过高

    # 近期涨幅扣分（已经涨了很多的，追高风险）
    if chg_20d and chg_20d > 15:
        score -= 5
    elif chg_20d and chg_20d > 10:
        score -= 2

    return {
        "symbol": symbol, "code": code, "name": name,
        "f5": f5, "f10": f10, "f20": f20,
        "verdict": verdict, "acceleration": acceleration,
        "all_aligned": all_aligned, "is_resonance": is_resonance,
        "conflict": conflict, "concentration": concentration,
        "directions": directions,
        "current": current, "pos_60": pos_60, "pos_120": pos_120,
        "rsi": rsi, "chg_20d": chg_20d,
        "market_cap_yi": mcap, "pe_ttm": pe,
        "score": score,
    }


def main():
    cli = XueqiuClient()

    # 扩充股池：按市值 10 页 + 按成交额 3 页，交叉去重
    seen = set()
    stocks = []

    print("拉取股池...")
    for page in range(1, 11):
        try:
            r = cli.screener("all_a", "market_capital", "desc", size=90, page=page)
            for item in r.get("list", []):
                sym = item.get("symbol", "")
                if sym not in seen:
                    seen.add(sym)
                    stocks.append(item)
        except Exception as e:
            print(f"  市值第{page}页失败: {e}")
            break

    # 按成交额补一批活跃股
    for page in range(1, 4):
        try:
            r = cli.screener("all_a", "amount", "desc", size=90, page=page)
            for item in r.get("list", []):
                sym = item.get("symbol", "")
                if sym not in seen:
                    seen.add(sym)
                    stocks.append(item)
        except Exception as e:
            print(f"  成交额第{page}页失败: {e}")
            break

    print(f"股池: {len(stocks)} 只（去重后），开始并发扫描...\n")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(analyze_one, s): s for s in stocks}
        for i, fut in enumerate(as_completed(futures)):
            r = fut.result()
            if r:
                results.append(r)
            if (i + 1) % 100 == 0:
                print(f"  进度: {i+1}/{len(stocks)}, 有效: {len(results)}")

    # 只保留资金面过关的（至少 f20 > 0, f10 > 0, !conflict）
    qualified = [
        r for r in results
        if r["f20"] and r["f10"] and r["f5"] is not None
        and r["f20"] > 0 and r["f10"] > 0
        and not r["conflict"]
        and r["pos_60"] and r["pos_60"] <= 0.92
        and (r["rsi"] is None or r["rsi"] < 70)
    ]

    # 按评分排序
    qualified.sort(key=lambda r: r["score"], reverse=True)

    # ─── 分组输出 ───
    resonance = [r for r in qualified if r["is_resonance"]]
    others = [r for r in qualified if not r["is_resonance"]]

    def print_group(title, items, limit=30):
        print(f"\n{'=' * 120}")
        print(f"  {title} ({len(items)} 只)")
        print(f"{'=' * 120}")
        print(f"{'代码':<12} {'名称':<10} {'现价':>8} {'RSI':>5} {'距60高':>7} {'20日涨':>8} {'5日资金':>9} {'10日资金':>9} {'20日资金':>9} {'市值(亿)':>9} {'verdict':<32} {'评分':>5}")
        print(f"{'-' * 120}")
        for r in items[:limit]:
            pos_s = f"{r['pos_60']:.0%}" if r['pos_60'] else "-"
            chg_s = f"{r['chg_20d']:+.1f}%" if r['chg_20d'] else "-"
            print(
                f"{r['symbol']:<12} {r['name']:<10} {r['current']:>8.2f} "
                f"{r['rsi']:>5.0f} {pos_s:>7} {chg_s:>8} "
                f"{r['f5']:>+8.2f}亿 {r['f10']:>+8.2f}亿 {r['f20']:>+8.2f}亿 "
                f"{r['market_cap_yi']:>9.0f} {r['verdict']:<32} {r['score']:>5.1f}"
            )

    print_group("🟢 RESONANCE_INFLOW — 四周期共振流入", resonance)
    print_group("🟡 其他资金流入标的（非共振）", others)

    # ─── TOP 20 总榜 ───
    print(f"\n\n{'=' * 120}")
    print(f"  TOP 20 综合评分")
    print(f"{'=' * 120}")
    for i, r in enumerate(qualified[:20]):
        dirs = r.get("directions", {})
        dir_str = "/".join(f"{dirs.get(p, '-')}" for p in ("1d", "5d", "10d", "20d"))
        flags = []
        if r["is_resonance"]:
            flags.append("共振")
        if r["all_aligned"]:
            flags.append("对齐")
        if r.get("acceleration") == "accelerating_inflow":
            flags.append("加速")
        flag_s = "|".join(flags) if flags else "-"
        print(
            f"  {i+1:>2}. {r['symbol']:<12} {r['name']:<10} "
            f"现价{r['current']:>8.2f}  "
            f"资金: 5d{r['f5']:+.2f} 10d{r['f10']:+.2f} 20d{r['f20']:+.2f}亿  "
            f"距高{r['pos_60']:.0%} RSI{r['rsi']:.0f}  "
            f"{r['verdict']} [{flag_s}]  score={r['score']:.1f}"
        )

    print(f"\n总扫描: {len(results)} 有效 | 合格: {len(qualified)} | 共振: {len(resonance)}")


if __name__ == "__main__":
    main()

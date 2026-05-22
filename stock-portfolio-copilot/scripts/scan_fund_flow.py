#!/usr/bin/env python3
"""全市场扫描：10日/20日主力资金流双正 + 位置不高的标的。

筛选逻辑：
  1. 10 日累计主力净流入 > 0
  2. 20 日累计主力净流入 > 0
  3. 现价距 60 日高点 ≥ 10%（即 pos_60 ≤ 0.90）—— 位置不那么高
  4. RSI-14 < 70 —— 不过热

数据源：雪球 screener（全 A 股池）+ 东财 fflow（资金流）+ 腾讯 K 线（价位）
"""
from __future__ import annotations

import sys
sys.path.insert(0, "stock-portfolio-copilot/scripts")
sys.path.insert(0, "shared")

from concurrent.futures import ThreadPoolExecutor, as_completed
from stock_core.xueqiu import XueqiuClient
from stock_core.fund_flow import fetch_daily_fund_flow, _window_summary
from stock_core.kline import fetch_daily_kline
from stock_core.symbols import normalize_symbol


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """简版 RSI-14，不依赖 tech_indicators 模块避免重复拉 K 线。"""
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += abs(diff)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def analyze_one(stock: dict) -> dict | None:
    symbol = stock.get("symbol", "")
    try:
        mkt, code, xq_sym = normalize_symbol(symbol)
    except Exception:
        return None

    if mkt != "a" or code.startswith(("4", "8")):
        return None

    name = stock.get("name", "")
    mcap = stock.get("market_capital") or 0
    pe = stock.get("pe_ttm")

    try:
        rows = fetch_daily_fund_flow(mkt, code)
        if not rows or len(rows) < 20:
            return None

        flow_10d = _window_summary(rows, 10)
        flow_20d = _window_summary(rows, 20)
        f10 = flow_10d.get("main_yi")
        f20 = flow_20d.get("main_yi")
        if f10 is None or f20 is None:
            return None
        if f10 <= 0 or f20 <= 0:
            return None
    except Exception:
        return None

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
        low_60d = min(closes[-60:])

        pos_60 = current / high_60d if high_60d > 0 else 1.0
        pos_120 = current / high_120d if high_120d > 0 else 1.0

        rsi = compute_rsi(closes)
        if rsi is None:
            return None
    except Exception:
        return None

    if pos_60 > 0.90:
        return None
    if rsi is not None and rsi >= 70:
        return None

    return {
        "symbol": symbol,
        "code": code,
        "name": name,
        "current": current,
        "flow_10d": f10,
        "flow_20d": f20,
        "pos_60": pos_60,
        "pos_120": pos_120,
        "high_60d": high_60d,
        "rsi": rsi,
        "market_cap_yi": mcap / 1e8,
        "pe_ttm": pe,
    }


def main():
    cli = XueqiuClient()

    # 拉全 A 按市值排序前 ~500 只（覆盖各行业龙头 + 中盘）
    print("拉取全 A 股池（按市值排序，5 页 × 90 = 450 只）...")
    stocks = []
    for page in range(1, 6):
        try:
            result = cli.screener("all_a", "market_capital", "desc", size=90, page=page)
            items = result.get("list", [])
            if not items:
                break
            stocks.extend(items)
        except Exception as e:
            print(f"  第 {page} 页失败: {e}")
            break

    print(f"股池: {len(stocks)} 只，开始并发拉取资金流 + K 线...\n")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(analyze_one, s): s for s in stocks}
        for i, fut in enumerate(as_completed(futures)):
            r = fut.result()
            if r:
                results.append(r)
            if (i + 1) % 50 == 0:
                print(f"  进度: {i+1}/{len(stocks)}, 已筛选: {len(results)}")

    # 排序：按 20d 资金流从大到小
    results.sort(key=lambda r: (r["flow_20d"], r["flow_10d"]), reverse=True)

    print(f"\n{'=' * 120}")
    print(f"  筛选结果: {len(results)} 只（条件：10d/20d 主力净流入 > 0，距60日高点 ≥ 10%，RSI < 70）")
    print(f"{'=' * 120}")
    print(f"{'代码':<12} {'名称':<10} {'现价':>8} {'RSI':>5} {'距60高':>7} {'距120高':>7} {'10d资金':>10} {'20d资金':>10} {'市值(亿)':>10} {'PE':>8}")
    print(f"{'-' * 120}")

    for r in results:
        pos60_s = f"{r['pos_60']:.0%}" if r['pos_60'] else "-"
        pos120_s = f"{r['pos_120']:.0%}" if r['pos_120'] else "-"
        pe_s = f"{r['pe_ttm']:.1f}" if r['pe_ttm'] else "-"
        print(
            f"{r['symbol']:<12} {r['name']:<10} {r['current']:>8.2f} "
            f"{r['rsi']:>5.0f} {pos60_s:>7} {pos120_s:>7} "
            f"{r['flow_10d']:>+9.2f}亿 {r['flow_20d']:>+9.2f}亿 "
            f"{r['market_cap_yi']:>10.0f} {pe_s:>8}"
        )

    if not results:
        print("  (无符合条件标的)")

    print(f"\n{'=' * 120}")
    print(f"共 {len(results)} 只符合条件 | 筛选基数 {len(stocks)} 只（全 A 按市值前 {len(stocks)}）")
    print(f"条件：10d 主力净流入 > 0 | 20d 主力净流入 > 0 | 现价 ≤ 60日高点 × 0.90 | RSI < 70")


if __name__ == "__main__":
    main()

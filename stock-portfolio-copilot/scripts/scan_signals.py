#!/usr/bin/env python3
"""Quick-scan all xiaoan watchlist stocks for top/bottom technical signals."""
from __future__ import annotations

import sys
sys.path.insert(0, "stock-portfolio-copilot/scripts")
sys.path.insert(0, "shared")

from concurrent.futures import ThreadPoolExecutor, as_completed
from spc_core.db import connect
from spc_core.ledger import list_watch
from stock_core.xueqiu import XueqiuClient
from stock_core.kline import fetch_daily_kline
from stock_core.tech_indicators import summarize_tech_indicators
from stock_core.symbols import normalize_symbol

conn = connect()
acct = conn.execute("SELECT id FROM accounts WHERE slug=?", ("xiaoan",)).fetchone()
watches = list_watch(conn, acct["id"])
conn.close()

print(f"共 {len(watches)} 只自选股，正在拉取行情+技术指标...")

# Phase 1: batch quotes
cli = XueqiuClient()
xq_map = {}
xq_syms = []
for w in watches:
    mkt, code = w["market"], w["code"]
    try:
        _, _, xq_sym = normalize_symbol(code)
        xq_syms.append(xq_sym)
        xq_map[xq_sym] = (mkt, code)
    except Exception:
        pass

all_quotes = {}
for i in range(0, len(xq_syms), 50):
    batch = xq_syms[i:i+50]
    try:
        for q in cli.quotes(batch):
            all_quotes[q.get("symbol", "")] = q
    except Exception as e:
        print(f"  quote batch failed: {e}")

print(f"行情: {len(all_quotes)}/{len(xq_syms)}")

# Phase 2: kline + tech indicators
def analyze_one(mkt, code, xq_sym):
    try:
        q = all_quotes.get(xq_sym, {})
        current = q.get("current")
        change_pct = q.get("percent")
        name = q.get("name", "")
        amount = q.get("amount", 0)
        turnover = q.get("turnover_rate", 0)

        kline_sym = xq_sym if mkt == "a" else code
        kline = fetch_daily_kline(kline_sym, mkt, count=1500)
        if not kline or len(kline) < 60:
            return (mkt, code, name, current, change_pct, amount, turnover, None)
        ti = summarize_tech_indicators(kline)
        return (mkt, code, name, current, change_pct, amount, turnover, ti)
    except Exception as e:
        return (mkt, code, "", None, None, 0, 0, None)

results = []
with ThreadPoolExecutor(max_workers=8) as ex:
    futures = {}
    for xq_sym, (mkt, code) in xq_map.items():
        futures[ex.submit(analyze_one, mkt, code, xq_sym)] = (mkt, code)
    for i, fut in enumerate(as_completed(futures)):
        results.append(fut.result())
        if (i + 1) % 15 == 0:
            print(f"  进度: {i+1}/{len(watches)}")

print(f"分析完成: {len(results)} 只\n")

# Phase 3: classify
top_signals = []
bottom_signals = []
neutral = []

for mkt, code, name, current, change_pct, amount, turnover, ti in results:
    if current is None or ti is None:
        continue

    macd = ti.get("macd", {})
    rsi = ti.get("rsi", {})
    boll = ti.get("bollinger", {})
    vol = ti.get("volume", {})
    pat = ti.get("patterns", {})

    div = macd.get("divergence")
    rsi_val = rsi.get("rsi")
    rsi_level = rsi.get("level", "neutral")
    boll_pos = boll.get("position", "")
    vol_div = vol.get("divergence")
    vol_ratio = vol.get("vol_ratio", 1.0)
    pattern = pat.get("main")
    squeeze = boll.get("squeeze", False)

    top_score = 0
    top_reasons = []
    if div == "bearish_top":
        top_score += 3; top_reasons.append("MACD顶背离")
    if rsi_level == "overbought":
        top_score += 2; top_reasons.append(f"RSI={rsi_val:.0f}超买")
    elif rsi_level == "near_overbought":
        top_score += 1; top_reasons.append(f"RSI={rsi_val:.0f}近超买")
    if boll_pos == "above_upper":
        top_score += 1; top_reasons.append("突破布林上轨")
    if vol_div == "bearish":
        top_score += 2; top_reasons.append("价涨量缩")
    if pattern in ("shooting_star", "bearish_engulfing", "long_upper_shadow"):
        top_score += 2
        cn = {"shooting_star": "射击之星", "bearish_engulfing": "看跌吞没", "long_upper_shadow": "长上影"}
        top_reasons.append(cn.get(pattern, pattern))

    bottom_score = 0
    bottom_reasons = []
    if div == "bullish_bottom":
        bottom_score += 3; bottom_reasons.append("MACD底背离")
    if rsi_level == "oversold":
        bottom_score += 2; bottom_reasons.append(f"RSI={rsi_val:.0f}超卖")
    elif rsi_level == "near_oversold":
        bottom_score += 1; bottom_reasons.append(f"RSI={rsi_val:.0f}近超卖")
    if boll_pos == "below_lower":
        bottom_score += 2; bottom_reasons.append("跌破布林下轨")
    elif boll_pos == "near_lower":
        bottom_score += 1; bottom_reasons.append("接近布林下轨")
    if vol_div == "bullish_shrink":
        bottom_score += 2; bottom_reasons.append("地量地价")
    if vol_ratio is not None and vol_ratio < 0.35 and vol_div is None:
        bottom_score += 1; bottom_reasons.append(f"极端缩量({vol_ratio:.1f}x)")
    if pattern in ("hammer", "bullish_engulfing", "long_lower_shadow"):
        bottom_score += 2
        cn = {"hammer": "锤子线", "bullish_engulfing": "看涨吞没", "long_lower_shadow": "长下影"}
        bottom_reasons.append(cn.get(pattern, pattern))
    if squeeze:
        bottom_reasons.append("布林收窄(变盘)")

    entry = (mkt, code, name, current, change_pct, amount, turnover, rsi_val,
             div, boll_pos, vol_ratio, vol_div, pattern, top_score, bottom_score,
             top_reasons, bottom_reasons)

    if top_score >= 2:
        top_signals.append(entry)
    elif bottom_score >= 2:
        bottom_signals.append(entry)
    else:
        neutral.append(entry)

top_signals.sort(key=lambda x: x[13], reverse=True)
bottom_signals.sort(key=lambda x: x[14], reverse=True)

def fmt_pct(v):
    return "-" if v is None else f"{v:+.2f}%"

def fmt_rsi(v):
    return "-" if v is None else f"{v:.0f}"

print("=" * 100)
print("🔴 顶部风险信号")
print("=" * 100)
print(f"{'代码':<10} {'名称':<10} {'现价':>8} {'涨跌':>8} {'RSI':>5} {'布林':<12} {'量比':>5} {'形态':<14} {'得分':>4}  {'信号'}")
print("-" * 100)
for mkt, code, name, cur, chg, amt, tover, rsi_v, div, boll_p, vol_r, vol_d, pat, ts, bs, tr, br in top_signals:
    label = f"{mkt.upper()} {code}"
    name_s = (name or "")[:8]
    cur_s = f"{cur:.2f}" if cur else "-"
    sig = " + ".join(tr[:3])
    print(f"{label:<10} {name_s:<10} {cur_s:>8} {fmt_pct(chg):>8} {fmt_rsi(rsi_v):>5} {boll_p or '-':<12} {vol_r or 1.0:>4.1f}x {pat or '-':<14} {ts:>4}  {sig}")
if not top_signals:
    print("  (无显著顶部信号)")

print()
print("=" * 100)
print("🟢 底部/反转信号")
print("=" * 100)
print(f"{'代码':<10} {'名称':<10} {'现价':>8} {'涨跌':>8} {'RSI':>5} {'布林':<12} {'量比':>5} {'形态':<14} {'得分':>4}  {'信号'}")
print("-" * 100)
for mkt, code, name, cur, chg, amt, tover, rsi_v, div, boll_p, vol_r, vol_d, pat, ts, bs, tr, br in bottom_signals:
    label = f"{mkt.upper()} {code}"
    name_s = (name or "")[:8]
    cur_s = f"{cur:.2f}" if cur else "-"
    sig = " + ".join(br[:3])
    print(f"{label:<10} {name_s:<10} {cur_s:>8} {fmt_pct(chg):>8} {fmt_rsi(rsi_v):>5} {boll_p or '-':<12} {vol_r or 1.0:>4.1f}x {pat or '-':<14} {bs:>4}  {sig}")
if not bottom_signals:
    print("  (无显著底部信号)")

print()
print(f"🔴 顶部风险: {len(top_signals)} 只 | 🟢 底部/反转: {len(bottom_signals)} 只 | ⚪️ 中性: {len(neutral)} 只")
print(f"总计: {len(results)} 只")

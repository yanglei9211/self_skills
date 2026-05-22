#!/usr/bin/env python3
"""盘中实时检查：主力榜排名 + 行情快照"""
import sys, json
sys.path.insert(0, '.')
from shared.stock_core.xueqiu import XueqiuClient

client = XueqiuClient()
codes = sys.argv[1:] if len(sys.argv) > 1 else []

# 1. 主力榜前 500 检索
print("=== 主力净流入榜 ===")
all_stocks = client.screener('all_a', 'main_inflow', 500)
targets = set(codes)
found = {}
rank = 0
for s in all_stocks:
    rank += 1
    sym = s if isinstance(s, str) else s.get('symbol', '')
    for t in targets:
        if t in sym:
            found[t] = rank

for code in targets:
    if code in found:
        print(f"  {code}: 排名 #{found[code]}")
    else:
        print(f"  {code}: 不在前 500 (当日无明显主力流入)")

# 2. 行情快照
print("\n=== 实时行情 ===")
symbols = [f"SH{code}" if code.startswith('6') else f"SZ{code}" for code in codes]
quotes = client.quotes(symbols)
for q in (quotes or []):
    if isinstance(q, dict):
        print(f"  {q.get('symbol','')}: {q.get('current','')} | {q.get('percent','')}% | 振幅{q.get('amplitude','')}% | 成交{q.get('amount',0)/1e8:.1f}亿")

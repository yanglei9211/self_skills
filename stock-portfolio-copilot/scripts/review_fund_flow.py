#!/usr/bin/env python3
"""复核脚本：对初筛标的逐只深查资金流交叉验证 + K线位置 + 技术指标。"""
from __future__ import annotations

import sys
sys.path.insert(0, "stock-portfolio-copilot/scripts")
sys.path.insert(0, "shared")

from concurrent.futures import ThreadPoolExecutor, as_completed
from stock_core.fund_flow import (
    fetch_daily_fund_flow, summarize_fund_flow, get_fund_flow_summary,
    _window_summary, cross_validate, regime_label, reversal_label,
)
from stock_core.kline import fetch_daily_kline
from stock_core.symbols import normalize_symbol
from stock_core.xueqiu import XueqiuClient

TARGETS = [
    # 资金流最强 20d > 3亿
    ("SH601728", "中国电信"),
    ("SH688498", "源杰科技"),
    ("SH600015", "华夏银行"),
    ("SH600050", "中国联通"),
    ("SH603501", "豪威集团"),
    ("SZ000425", "徐工机械"),
    ("SH600019", "宝钢股份"),
    ("SZ000100", "TCL科技"),
    ("SZ000157", "中联重科"),
    ("SH600066", "宇通客车"),
]


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
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


def review_one(symbol: str) -> dict | None:
    try:
        mkt, code, xq_sym = normalize_symbol(symbol)
    except Exception:
        return {"symbol": symbol, "error": "无法解析代码"}

    if mkt != "a":
        return {"symbol": symbol, "error": "非A股"}

    result = {"symbol": symbol, "code": code, "market": mkt}

    # 1. 完整资金流摘要（含 cross_validation）
    try:
        summary = get_fund_flow_summary(mkt, code)
        if summary.get("error"):
            result["ff_error"] = summary["error"]
            return result

        rolling = summary.get("rolling") or {}
        flow_5d = (rolling.get("5d") or {}).get("main_yi")
        flow_10d = (rolling.get("10d") or {}).get("main_yi")
        flow_20d = (rolling.get("20d") or {}).get("main_yi")

        cross = summary.get("cross_validation") or {}
        result.update({
            "flow_5d": flow_5d,
            "flow_10d": flow_10d,
            "flow_20d": flow_20d,
            "flow_5d_days": (rolling.get("5d") or {}).get("inflow_days", 0),
            "flow_10d_days": (rolling.get("10d") or {}).get("inflow_days", 0),
            "flow_20d_days": (rolling.get("20d") or {}).get("inflow_days", 0),
            "regime": summary.get("regime"),
            "reversal": summary.get("reversal"),
            "verdict": cross.get("verdict"),
            "verdict_zh": cross.get("verdict_zh"),
            "all_aligned": cross.get("all_aligned"),
            "acceleration": cross.get("acceleration"),
            "short_long_conflict": cross.get("short_long_conflict"),
            "conflict_kind": cross.get("conflict_kind"),
            "concentration": cross.get("concentration_5d_in_20d"),
            "is_resonance": cross.get("is_resonance"),
            "reversal_confirmed": cross.get("reversal_confirmed"),
            "directions": cross.get("directions"),
        })
        # warnings
        if summary.get("warnings"):
            result["ff_warnings"] = summary["warnings"]
    except Exception as e:
        result["ff_error"] = str(e)
        return result

    # 2. K线位置 + RSI
    try:
        kline = fetch_daily_kline(xq_sym, mkt, count=120)
        if not kline or len(kline) < 60:
            result["kl_error"] = "K线不足60条"
            return result

        closes = [k["close"] for k in kline if k.get("close")]
        if len(closes) < 60:
            result["kl_error"] = "收盘价数据不足"
            return result

        current = closes[-1]
        high_60d = max(closes[-60:])
        high_120d = max(closes) if len(closes) >= 120 else high_60d
        low_60d = min(closes[-60:])
        low_120d = min(closes) if len(closes) >= 120 else low_60d

        # MA20 / MA60
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None

        # 52周高低（取全部120条）
        high_52w = max(closes)
        low_52w = min(closes)

        rsi = compute_rsi(closes)

        # 近期走势：看最近20天是涨还是跌
        chg_5d = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else None
        chg_20d = (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 else None

        result.update({
            "current": current,
            "high_60d": high_60d,
            "high_120d": high_120d,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "pos_60": current / high_60d,
            "pos_120": current / high_120d,
            "pos_52w": current / high_52w,
            "ma20": ma20,
            "ma60": ma60,
            "rsi": rsi,
            "chg_5d": chg_5d,
            "chg_20d": chg_20d,
        })
    except Exception as e:
        result["kl_error"] = str(e)

    return result


def main():
    print("逐只复核中...\n")

    results = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(review_one, s): s for s, _ in TARGETS}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results[r["symbol"]] = r

    # ─── 逐只打印 ───
    for symbol, name in TARGETS:
        r = results.get(symbol)
        if not r:
            print(f"\n{'=' * 80}")
            print(f"  {symbol} {name}  — 无数据")
            continue

        print(f"\n{'=' * 80}")
        print(f"  {symbol} {name}")
        print(f"{'=' * 80}")

        if r.get("ff_error"):
            print(f"  ⚠️ 资金流错误: {r['ff_error']}")
        if r.get("kl_error"):
            print(f"  ⚠️ K线错误: {r['kl_error']}")
        if r.get("ff_warnings"):
            for w in r["ff_warnings"]:
                print(f"  ⚠️ {w}")

        # 资金流
        f5 = r.get("flow_5d")
        f10 = r.get("flow_10d")
        f20 = r.get("flow_20d")
        print(f"  资金流: 5日 {f5:+.2f}亿 | 10日 {f10:+.2f}亿 | 20日 {f20:+.2f}亿" if all(
            v is not None for v in (f5, f10, f20)
        ) else "  资金流: 数据不全")
        print(f"  流入天数: 5日 {r.get('flow_5d_days', '-')}天 | 10日 {r.get('flow_10d_days', '-')}天 | 20日 {r.get('flow_20d_days', '-')}天")

        # 交叉验证
        dirs = r.get("directions") or {}
        dir_str = " / ".join(f"{p}={dirs.get(p) or '-'}" for p in ("1d", "5d", "10d", "20d"))
        print(f"  方向: {dir_str}")
        print(f"  verdict: {r.get('verdict')} — {r.get('verdict_zh')}")
        print(f"  acceleration: {r.get('acceleration') or '-'}")
        print(f"  all_aligned: {r.get('all_aligned')} | is_resonance: {r.get('is_resonance')}")
        if r.get("short_long_conflict"):
            print(f"  ⚠️ 短长冲突: {r.get('conflict_kind')}")
        conc = r.get("concentration")
        if conc is not None:
            tag = " ⚠️ 近期集中度高" if conc >= 0.5 else ""
            print(f"  5d/20d集中度: {conc}{tag}")

        # reversal
        rev = r.get("reversal")
        rev_c = r.get("reversal_confirmed")
        if rev:
            status = "✅ 已确认" if rev_c is True else ("❌ 未确认" if rev_c is False else "? 待定")
            print(f"  reversal: {rev} {status}")

        # 价位
        cur = r.get("current")
        if cur:
            print(f"  现价: {cur:.2f}")
            print(f"  距60日高: {r.get('pos_60', 0):.0%} (高={r.get('high_60d', 0):.2f})")
            print(f"  距120日高: {r.get('pos_120', 0):.0%} (高={r.get('high_120d', 0):.2f})")
            print(f"  距52周高: {r.get('pos_52w', 0):.0%} (高={r.get('high_52w', 0):.2f})")
            ma20 = r.get("ma20")
            ma60 = r.get("ma60")
            if ma20 and ma60:
                above_ma20 = "↑" if cur > ma20 else "↓"
                above_ma60 = "↑" if cur > ma60 else "↓"
                print(f"  MA20: {ma20:.2f} {above_ma20} | MA60: {ma60:.2f} {above_ma60}")
            print(f"  RSI-14: {r.get('rsi', 0):.0f}")
            chg5 = r.get("chg_5d")
            chg20 = r.get("chg_20d")
            if chg5 is not None and chg20 is not None:
                print(f"  近5日: {chg5:+.2f}% | 近20日: {chg20:+.2f}%")

        # ─── 综合判定 ───
        flags = []
        verdict = r.get("verdict", "")
        is_res = r.get("is_resonance")

        # 红线检查
        if verdict == "RESONANCE_OUTFLOW":
            flags.append("🔴 共振流出！不应买入")
        if is_res and "INFLOW" in str(verdict):
            flags.append("🟢 共振流入，资金面强劲")
        if r.get("short_long_conflict"):
            flags.append("🟡 短长方向冲突，信号减弱")
        if verdict in ("DECELERATING_INFLOW", "WEAKENING_INFLOW"):
            flags.append("🟡 流入动能减弱")
        if r.get("acceleration") == "accelerating_inflow":
            flags.append("🟢 流入加速中")

        # 价位检查
        pos60 = r.get("pos_60", 1)
        rsi_v = r.get("rsi", 50)
        if pos60 and pos60 <= 0.80:
            flags.append("🟢 位置较低（距60高≤80%）")
        elif pos60 and pos60 >= 0.95:
            flags.append("🔴 接近60日高点")
        if rsi_v and rsi_v < 30:
            flags.append("🟢 RSI超卖区域")
        elif rsi_v and rsi_v > 65:
            flags.append("🟡 RSI偏高")

        if flags:
            print(f"  综合: {' | '.join(flags)}")
        else:
            print(f"  综合: 中性")

    # ─── 汇总排序 ───
    print(f"\n\n{'=' * 80}")
    print(f"  汇总排序（按20日资金流）")
    print(f"{'=' * 80}")

    scored = []
    for symbol, name in TARGETS:
        r = results.get(symbol)
        if not r or r.get("ff_error"):
            continue
        f20 = r.get("flow_20d") or 0
        f10 = r.get("flow_10d") or 0
        pos60 = r.get("pos_60") or 1
        rsi_v = r.get("rsi") or 50
        verdict = r.get("verdict", "")
        conflict = r.get("short_long_conflict", False)
        is_res = r.get("is_resonance", False)

        # 扣分项
        demerit = 0
        if verdict == "RESONANCE_OUTFLOW":
            demerit += 100
        if conflict:
            demerit += 2
        if verdict in ("DECELERATING_INFLOW", "WEAKENING_INFLOW"):
            demerit += 1
        if rsi_v and rsi_v > 65:
            demerit += 1
        if pos60 and pos60 > 0.92:
            demerit += 1

        # 加分项
        bonus = 0
        if is_res and "INFLOW" in str(verdict):
            bonus += 3
        if r.get("acceleration") == "accelerating_inflow":
            bonus += 2

        score = f20 + bonus - demerit

        scored.append((symbol, name, r, score))

    scored.sort(key=lambda x: x[3], reverse=True)

    for symbol, name, r, score in scored:
        f20 = r.get("flow_20d") or 0
        f10 = r.get("flow_10d") or 0
        pos60 = r.get("pos_60") or 0
        verdict = r.get("verdict") or "-"
        conflict = "⚠️冲突" if r.get("short_long_conflict") else ""
        print(
            f"  {symbol} {name:<8} "
            f"20日 {f20:+.2f}亿  10日 {f10:+.2f}亿  "
            f"距高 {pos60:.0%}  RSI {r.get('rsi', 0):.0f}  "
            f"{verdict} {conflict}"
        )


if __name__ == "__main__":
    main()

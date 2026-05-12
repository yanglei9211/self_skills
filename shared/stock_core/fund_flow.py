"""个股主力资金流：东方财富 fflow daykline 接口。

为什么用东财而不是雪球？
  - 雪球 screener 的 ``main_net_inflows`` 只有"今日累计"一个数字，无分层、无历史
  - 东财 ``push2his.eastmoney.com/api/qt/stock/fflow/daykline/get`` 提供：
        近 ~120 个交易日的逐日「主力 / 超大单 / 大单 / 中单 / 小单」净额 + 占比
    且无登录、无反爬封禁（与已知被封的 push2 实时行情接口是不同 host）。

支持范围
  - A 股沪深主板 / 创业板 / 科创板：✅
  - 港股：✅（东财根据成交单笔大小推算分级，参考价值低于 A 股）
  - 北交所（4/8 开头）：❌（``eastmoney_secid`` 会抛 ValueError）
  - 美股：❌（"主力资金"不是美股的标准市场指标）

字段对应（接口返回 ``klines`` 的逗号分隔字符串）：
  f51 日期 / f52 主力净额(元) / f53 小单 / f54 中单 / f55 大单 / f56 超大单
  f57 主力净占比(%) / f58 小单占比 / f59 中单占比 / f60 大单占比 / f61 超大单占比
  f62 收盘价 / f63 涨跌幅(%)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from typing import Any

from .cache import cached
from .http import fetch
from .symbols import eastmoney_secid, normalize_symbol
from .tz import CN_TZ, is_market_open


_FFLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_FFLOW_FIELDS1 = "f1,f2,f3,f7"
_FFLOW_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63"
_FFLOW_UT = "b2884a393a59ad64002292a3e90d46a5"


def _ttl_for_moment(market: str, now: datetime) -> float:
    """按给定时刻计算资金流缓存 TTL。

    默认规则：
      - 盘中：60s
      - 收盘后短窗口：继续 60s，确保 ``1d`` 尽快切到当天最终资金流
      - 其后：4h
    """
    if is_market_open(market, now):
        return 60.0
    if now.weekday() >= 5:
        return 4 * 3600.0

    refresh_until: time | None = None
    if market == "a":
        refresh_until = time(17, 0)
    elif market == "hk":
        refresh_until = time(18, 0)

    if refresh_until is not None:
        close_hour = 15 if market == "a" else 16
        if time(close_hour, 0) < now.time() <= refresh_until:
            return 60.0
    return 4 * 3600.0


def _ttl_for_call(market: str, code: str, cached_data: list[dict] | None = None) -> float:  # noqa: ARG001
    """缓存 TTL 策略：当天短缓存，历史日线长缓存。

    背景：
      - 东财 fflow 日资金流在收盘后会补出"当天"这一根日线
      - 如果简单按 ``is_market_open`` 切到盘后 4h，14:xx 抓到的盘中缓存可能会在
        15:xx / 16:xx 继续被复用，导致看不到当天最终资金流
      - 一旦缓存里的最后日期已经不是今天，说明这份数据只含历史日线，可放心拉长 TTL
    """
    now = datetime.now(CN_TZ)
    if cached_data:
        last_date = str((cached_data[-1] or {}).get("date") or "")
        today = now.date().isoformat()
        if last_date and last_date != today:
            return 4 * 3600.0
    return _ttl_for_moment(market, now)


def _to_float(s: str) -> float | None:
    try:
        if s in (None, "", "-"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_kline_row(row: str) -> dict[str, Any] | None:
    parts = row.split(",")
    if len(parts) < 13:
        return None
    return {
        "date": parts[0],
        "main": _to_float(parts[1]),
        "small": _to_float(parts[2]),
        "mid": _to_float(parts[3]),
        "big": _to_float(parts[4]),
        "super_big": _to_float(parts[5]),
        "main_pct": _to_float(parts[6]),
        "small_pct": _to_float(parts[7]),
        "mid_pct": _to_float(parts[8]),
        "big_pct": _to_float(parts[9]),
        "super_big_pct": _to_float(parts[10]),
        "close": _to_float(parts[11]),
        "change_pct": _to_float(parts[12]),
    }


@cached(ttl=_ttl_for_call, key_prefix="ff")
def fetch_daily_fund_flow(market: str, code: str) -> list[dict]:
    """拉东财个股资金流日 K（约 120 个交易日）。

    market: ``'a'`` / ``'hk'``；其它（``'us'`` / 北交所）由 :func:`eastmoney_secid`
    抛 ValueError，调用方应自己跳过这两类。
    返回按日期升序排列的 list；金额单位元，占比单位 %。
    """
    secid = eastmoney_secid(market, code)
    params = {
        "lmt": 0,
        "klt": 101,
        "fields1": _FFLOW_FIELDS1,
        "fields2": _FFLOW_FIELDS2,
        "secid": secid,
        "ut": _FFLOW_UT,
    }
    r = fetch(_FFLOW_URL, params=params, timeout=10)
    payload = r.json() or {}
    klines = ((payload.get("data") or {}).get("klines") or [])
    rows = [_parse_kline_row(k) for k in klines]
    return [r for r in rows if r is not None and r.get("date")]


def _to_yi(value: float | None) -> float | None:
    """元 -> 亿元，保留 4 位小数。"""
    if value is None:
        return None
    return round(value / 1e8, 4)


def _window_summary(rows: list[dict], n: int) -> dict[str, Any]:
    """对最近 ``n`` 个交易日计算累计金额、流入/流出天数。"""
    window = rows[-n:] if n > 0 else rows
    if not window:
        return {"main_yi": None, "outflow_days": 0, "inflow_days": 0, "days": 0}
    total = sum((r["main"] or 0.0) for r in window)
    outflow_days = sum(1 for r in window if (r["main"] or 0.0) < 0)
    inflow_days = sum(1 for r in window if (r["main"] or 0.0) > 0)
    return {
        "main_yi": _to_yi(total),
        "outflow_days": outflow_days,
        "inflow_days": inflow_days,
        "days": len(window),
    }


def _classify_regime(roll_20d: dict[str, Any]) -> str:
    """20 日窗口 regime：
    - PERSISTENT_INFLOW: 累计金额 > 0 且流入天数 ≥ 12
    - PERSISTENT_OUTFLOW: 累计金额 < 0 且流出天数 ≥ 12
    - 其他: OSCILLATING
    """
    total = roll_20d.get("main_yi")
    if total is None:
        return "OSCILLATING"
    if total > 0 and (roll_20d.get("inflow_days") or 0) >= 12:
        return "PERSISTENT_INFLOW"
    if total < 0 and (roll_20d.get("outflow_days") or 0) >= 12:
        return "PERSISTENT_OUTFLOW"
    return "OSCILLATING"


def _classify_reversal(roll_5d: dict[str, Any], roll_20d: dict[str, Any]) -> str | None:
    """近 5 日方向与前 15 日相反时给出转向标签。"""
    short = roll_5d.get("main_yi")
    long_ = roll_20d.get("main_yi")
    if short is None or long_ is None:
        return None
    earlier = long_ - short  # 前 15 日累计
    if short < 0 and earlier > 0:
        return "INFLOW_TO_OUTFLOW"
    if short > 0 and earlier < 0:
        return "OUTFLOW_TO_INFLOW"
    return None


def summarize_fund_flow(rows: list[dict]) -> dict[str, Any]:
    """把日 K 列表压成"今日 + 1d/3d/5d/10d/20d 累计 + regime + reversal"摘要。"""
    if not rows:
        return {"as_of": None, "today": None, "rolling": {}, "regime": None, "reversal": None}

    today = rows[-1]
    today_view = {
        "main_yi": _to_yi(today.get("main")),
        "super_big_yi": _to_yi(today.get("super_big")),
        "big_yi": _to_yi(today.get("big")),
        "mid_yi": _to_yi(today.get("mid")),
        "small_yi": _to_yi(today.get("small")),
        "main_pct": today.get("main_pct"),
        "super_big_pct": today.get("super_big_pct"),
        "big_pct": today.get("big_pct"),
        "mid_pct": today.get("mid_pct"),
        "small_pct": today.get("small_pct"),
        "close": today.get("close"),
        "change_pct": today.get("change_pct"),
    }
    rolling = {
        "1d": _window_summary(rows, 1),
        "3d": _window_summary(rows, 3),
        "5d": _window_summary(rows, 5),
        "10d": _window_summary(rows, 10),
        "20d": _window_summary(rows, 20),
    }
    regime = _classify_regime(rolling["20d"])
    reversal = _classify_reversal(rolling["5d"], rolling["20d"])
    return {
        "as_of": today.get("date"),
        "today": today_view,
        "rolling": rolling,
        "regime": regime,
        "reversal": reversal,
    }


def get_fund_flow_summary(market: str, code: str) -> dict[str, Any]:
    """组合调用：拉日 K + 生成摘要。失败时返回带 ``error`` 字段的占位字典。"""
    try:
        rows = fetch_daily_fund_flow(market, code)
    except ValueError as e:
        return {"error": str(e), "as_of": None, "today": None, "rolling": {}, "regime": None, "reversal": None}
    if not rows:
        return {"error": "fflow 接口返回空", "as_of": None, "today": None, "rolling": {}, "regime": None, "reversal": None}
    summary = summarize_fund_flow(rows)
    summary["fetched_at"] = datetime.now(CN_TZ).isoformat()
    return summary


# ============ CLI ============ #

def _render_text(market: str, code: str, summary: dict[str, Any]) -> str:
    if summary.get("error"):
        return f"# 主力资金动向 {market.upper()} {code}\n\n（暂无数据：{summary['error']}）"
    lines: list[str] = []
    lines.append(f"# 主力资金动向 {market.upper()} {code} — 截至 {summary.get('as_of')}")
    regime = summary.get("regime") or "-"
    regime_zh = {
        "PERSISTENT_INFLOW": "持续净流入",
        "PERSISTENT_OUTFLOW": "持续净流出",
        "OSCILLATING": "震荡 / 进出反复",
    }.get(regime, regime)
    lines.append(f"\n> regime: **{regime}** ({regime_zh})")
    reversal = summary.get("reversal")
    if reversal:
        rev_zh = {
            "INFLOW_TO_OUTFLOW": "近 5 日由流入转为流出",
            "OUTFLOW_TO_INFLOW": "近 5 日由流出转为流入",
        }.get(reversal, reversal)
        lines.append(f"> reversal: **{reversal}** ({rev_zh})")
    if market == "hk":
        lines.append("> _港股资金分级为东财根据成交单笔大小推算，仅供参考。_")

    lines.append("")
    lines.append("## 累计窗口")
    lines.append("| 周期 | 主力净额 | 净流入天数 / 流出天数 |")
    lines.append("|---|---|---|")
    rolling = summary.get("rolling") or {}
    for win in ("1d", "3d", "5d", "10d", "20d"):
        w = rolling.get(win) or {}
        amount = w.get("main_yi")
        amount_str = "-" if amount is None else f"{amount:+.2f} 亿"
        lines.append(
            f"| {win} | {amount_str} | "
            f"{w.get('inflow_days', 0)} / {w.get('outflow_days', 0)} (共 {w.get('days', 0)} 天) |"
        )

    today = summary.get("today") or {}
    lines.append("")
    lines.append(f"## 当日资金分层（{summary.get('as_of')}，收盘 {today.get('close')}，涨跌 {today.get('change_pct')}%）")
    lines.append("| 档位 | 净额 | 占成交比例 |")
    lines.append("|---|---|---|")
    for label, amt_key, pct_key in (
        ("超大单", "super_big_yi", "super_big_pct"),
        ("大单", "big_yi", "big_pct"),
        ("中单", "mid_yi", "mid_pct"),
        ("小单", "small_yi", "small_pct"),
        ("**主力合计**", "main_yi", "main_pct"),
    ):
        amt = today.get(amt_key)
        pct = today.get(pct_key)
        amt_str = "-" if amt is None else f"{amt:+.2f} 亿"
        pct_str = "-" if pct is None else f"{pct:+.2f}%"
        lines.append(f"| {label} | {amt_str} | {pct_str} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="个股主力资金流（东方财富 fflow daykline）")
    ap.add_argument(
        "--symbol",
        required=True,
        help="股票代码：SZ300750 / SH600519 / HK00700（暂不支持北交所与美股）",
    )
    ap.add_argument("--format", choices=["json", "text"], default="text")
    args = ap.parse_args()
    market, code, _xq = normalize_symbol(args.symbol)
    if market not in ("a", "hk"):
        print(
            f"market={market!r} 不支持主力资金流（当前仅支持 A 股沪深主板 / 创业板 / 科创板 + 港股）",
            file=sys.stderr,
        )
        sys.exit(2)
    if market == "a" and code.startswith(("4", "8")):
        print("北交所代码暂不支持主力资金流（东财 fflow secid 规则未公开稳定）", file=sys.stderr)
        sys.exit(2)
    summary = get_fund_flow_summary(market, code)
    if args.format == "json":
        json.dump(summary, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
    else:
        print(_render_text(market, code, summary))


if __name__ == "__main__":
    main()

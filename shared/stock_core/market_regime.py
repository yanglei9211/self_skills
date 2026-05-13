"""A 股 / 港股大盘风险偏好评估。

设计原则：尽量轻、尽量保守，让结果可解释。
对外只暴露三档 ``RISK_OFF / NEUTRAL / RISK_ON``，但内部保留每个代表指数的原始读数，
供 LLM 或调用方做更细致的判断。

代表指数：
  - A 股：沪深300（``sh000300``）+ 创业板指（``sz399006``）
        前者覆盖大盘股 / 价值；后者覆盖成长 / 科技
  - 港股：恒生指数（``hkHSI``）+ 恒生科技指数（``hkHSTECH``）

为什么不复用 ``fetch_daily_kline``？
  - 现有 ``_build_kline_url`` 对港股做 ``zfill(5)`` 和 ``lower()``，
    会把 ``HKHSI`` 变成 ``hk00000`` 报空。
  - 对 A 股 ``SH000300`` 会先按 SH 前缀分对，但 ``SZ399006`` 0 开头被路由到深圳也对；
    本模块为了排错与实现简单，直接走腾讯指数 K 线接口，不再走"市场+代码"的间接路径。

regime 规则（保守、信号优先）：
  - **RISK_OFF**：任一代表指数 距 52w 高 ≤ -15%  AND  当日收盘 < MA200（年线）
  - **RISK_ON** ：所有代表指数 距 52w 高 ≥  -3%  AND  当日收盘 ≥ MA200
  - **NEUTRAL** ：其余所有情形

阈值理由：
  - -15%：传统熊市定义中"大幅回撤"的下沿，叠加破年线后才算 RISK_OFF，避免反复抖动
  - -3%：最近 52w 高的"邻域"，叠加站上年线表示中长期趋势完整
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from typing import Any

from .cache import cached
from .http import fetch
from .tz import CN_TZ, is_market_open


REGIME_RISK_OFF = "RISK_OFF"
REGIME_RISK_ON = "RISK_ON"
REGIME_NEUTRAL = "NEUTRAL"

INDICES: dict[str, list[dict[str, str]]] = {
    "a": [
        {"tcode": "sh000300", "name": "沪深300", "kind": "a_share"},
        {"tcode": "sz399006", "name": "创业板指", "kind": "a_share"},
    ],
    "hk": [
        {"tcode": "hkHSI", "name": "恒生指数", "kind": "hk"},
        {"tcode": "hkHSTECH", "name": "恒生科技", "kind": "hk"},
    ],
}


def _ttl_for_index(tcode: str, count: int = 400) -> float:  # noqa: ARG001
    """盘中 60s / 盘后 4h；港股指数也按市场时段判断。"""
    market = "hk" if tcode.startswith("hk") else "a"
    return 60.0 if is_market_open(market) else 4 * 3600.0


@cached(ttl=_ttl_for_index, key_prefix="idx")
def fetch_index_daily(tcode: str, count: int = 400) -> list[dict]:
    """拉指数日 K（最近约 ``count`` 个交易日）。

    ``tcode`` 必须严格保持大小写：
      - A 股指数：``sh000300`` / ``sz399006`` / ``sh000001``
      - 港股指数：``hkHSI`` / ``hkHSTECH`` / ``hkHSCEI``
    """
    if tcode.startswith("hk"):
        url = (
            "https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get"
            f"?_var=k&param={tcode},day,,,{count},qfq"
        )
    else:
        url = (
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?_var=k&param={tcode},day,,,{count},qfq"
        )
    r = fetch(url, timeout=15, retries=2)
    text = r.text or ""
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.DOTALL) or re.search(r"(\{.*\})", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    container = (data.get("data") or {})
    payload = container.get(tcode) or next(iter(container.values()), None)
    if not isinstance(payload, dict):
        return []
    raw = payload.get("qfqday") or payload.get("day") or payload.get("fqday") or []
    out: list[dict] = []
    for row in raw:
        if not row or len(row) < 5:
            continue
        try:
            out.append({
                "date": row[0],
                "open": float(row[1]),
                "close": float(row[2]),
                "high": float(row[3]),
                "low": float(row[4]),
            })
        except (TypeError, ValueError):
            continue
    return out


def _ma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def summarize_index(tcode: str, name: str) -> dict[str, Any]:
    """对单个指数做 K 线摘要：

    - 当前 close、距 52w 高 / 低的百分比、YTD %、距 200MA（年线）的偏离
    - ``broke_ma200`` ：当日 close 是否跌破年线
    - ``error`` 字段在拉数据失败时存在
    """
    rows = fetch_index_daily(tcode)
    if not rows:
        return {"tcode": tcode, "name": name, "error": "K 线获取失败"}
    last = rows[-1]
    closes = [r["close"] for r in rows]
    last_close = closes[-1]

    window_252 = rows[-252:] if len(rows) >= 252 else rows
    high_52w = max(r["high"] for r in window_252)
    low_52w = min(r["low"] for r in window_252)
    high_52w_date = next(r["date"] for r in window_252 if r["high"] == high_52w)
    low_52w_date = next(r["date"] for r in window_252 if r["low"] == low_52w)

    from_high_pct = round((last_close / high_52w - 1) * 100, 2) if high_52w > 0 else None
    from_low_pct = round((last_close / low_52w - 1) * 100, 2) if low_52w > 0 else None

    cur_year = last["date"][:4]
    ytd_rows = [r for r in rows if r["date"].startswith(cur_year)]
    ytd_pct: float | None
    if ytd_rows:
        ytd_start_close = ytd_rows[0]["close"]
        ytd_pct = round((last_close / ytd_start_close - 1) * 100, 2) if ytd_start_close else None
    else:
        ytd_pct = None

    ma200 = _ma(closes, 200)
    broke_ma200 = (ma200 is not None) and (last_close < ma200)
    above_ma200 = (ma200 is not None) and (last_close >= ma200)
    ma200_dev_pct = round((last_close / ma200 - 1) * 100, 2) if ma200 else None

    last5_change_pct = None
    if len(closes) >= 6:
        last5_change_pct = round((closes[-1] / closes[-6] - 1) * 100, 2)

    return {
        "tcode": tcode,
        "name": name,
        "as_of": last["date"],
        "close": last_close,
        "high_52w": high_52w,
        "high_52w_date": high_52w_date,
        "low_52w": low_52w,
        "low_52w_date": low_52w_date,
        "from_52w_high_pct": from_high_pct,
        "from_52w_low_pct": from_low_pct,
        "ytd_pct": ytd_pct,
        "ma200": round(ma200, 2) if ma200 else None,
        "ma200_dev_pct": ma200_dev_pct,
        "broke_ma200": broke_ma200,
        "above_ma200": above_ma200,
        "last5_change_pct": last5_change_pct,
    }


def _classify(summaries: list[dict]) -> tuple[str, list[str]]:
    """根据多个指数 summary 给出 regime 和理由列表。"""
    valid = [s for s in summaries if not s.get("error")]
    if not valid:
        return REGIME_NEUTRAL, ["所有代表指数 K 线获取失败，回退为 NEUTRAL"]

    risk_off_hits: list[str] = []
    risk_on_hits: list[str] = []
    for s in valid:
        from_high = s.get("from_52w_high_pct")
        broke = s.get("broke_ma200")
        above = s.get("above_ma200")
        if from_high is not None and from_high <= -15 and broke:
            risk_off_hits.append(
                f"{s['name']} 距 52w 高 {from_high:+.2f}% 且跌破年线（close={s['close']} < MA200={s['ma200']}）"
            )
        if from_high is not None and from_high >= -3 and above:
            risk_on_hits.append(
                f"{s['name']} 距 52w 高 {from_high:+.2f}% 且站上年线（close={s['close']} ≥ MA200={s['ma200']}）"
            )

    if risk_off_hits:
        return REGIME_RISK_OFF, risk_off_hits
    if len(risk_on_hits) == len(valid):
        return REGIME_RISK_ON, risk_on_hits
    reasons = []
    for s in valid:
        from_high = s.get("from_52w_high_pct")
        ma200_dev = s.get("ma200_dev_pct")
        reasons.append(
            f"{s['name']} 距 52w 高 {from_high:+.2f}%，年线偏离 {ma200_dev:+.2f}%"
        )
    return REGIME_NEUTRAL, reasons


def classify_market_regime(market: str) -> dict[str, Any]:
    """评估指定市场（``a`` / ``hk``）的整体 regime。

    返回字段：
      - ``market``: ``a`` / ``hk``
      - ``regime``: ``RISK_OFF`` / ``NEUTRAL`` / ``RISK_ON``
      - ``reasons``: 落到该 regime 的具体理由（每条对应一个代表指数的解读）
      - ``indices``: list[dict]，每个代表指数的完整 summary
      - ``fetched_at``: ISO 时间戳
    """
    if market not in INDICES:
        return {"market": market, "regime": REGIME_NEUTRAL, "reasons": [f"未知市场 {market!r}"], "indices": []}
    summaries = [summarize_index(idx["tcode"], idx["name"]) for idx in INDICES[market]]
    regime, reasons = _classify(summaries)
    return {
        "market": market,
        "regime": regime,
        "reasons": reasons,
        "indices": summaries,
        "fetched_at": datetime.now(CN_TZ).isoformat(),
    }


def get_a_share_regime() -> dict[str, Any]:
    return classify_market_regime("a")


def get_hk_regime() -> dict[str, Any]:
    return classify_market_regime("hk")


# ============ CLI ============ #

def _render_text(payload: dict, *, market_label: str) -> str:
    if not payload or not payload.get("indices"):
        return f"# {market_label} 大盘 regime\n\n（无数据）"
    regime = payload.get("regime", "-")
    regime_label = {
        REGIME_RISK_OFF: "🔴 RISK_OFF（避险）",
        REGIME_NEUTRAL: "⚪️ NEUTRAL（中性）",
        REGIME_RISK_ON: "🟢 RISK_ON（进攻）",
    }.get(regime, regime)
    lines = [f"# {market_label} 大盘 regime — {regime_label}"]
    lines.append("")
    lines.append("**判定理由**：")
    for r in payload.get("reasons", []):
        lines.append(f"- {r}")
    lines.append("")
    lines.append("## 代表指数读数")
    lines.append("| 指数 | 截至 | 收盘 | 距 52w 高 | 距 52w 低 | YTD | MA200 偏离 | 年线 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for s in payload.get("indices", []):
        if s.get("error"):
            lines.append(
                f"| {s.get('name')} | - | - | - | - | - | - | {s.get('error')} |"
            )
            continue
        ma_status = "✅ 站上" if s.get("above_ma200") else "❌ 跌破"
        lines.append(
            f"| {s['name']} | {s['as_of']} | {s['close']} | "
            f"{s['from_52w_high_pct']:+.2f}% | {s['from_52w_low_pct']:+.2f}% | "
            f"{(s['ytd_pct'] or 0):+.2f}% | {(s['ma200_dev_pct'] or 0):+.2f}% | {ma_status} |"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="A 股 / 港股大盘风险 regime")
    ap.add_argument(
        "--market",
        choices=["a", "hk", "all"],
        default="all",
        help="评估哪个市场；all = A 股 + 港股各出一份",
    )
    ap.add_argument("--format", choices=["json", "text"], default="text")
    args = ap.parse_args()

    if args.market in ("a", "all"):
        a_payload = classify_market_regime("a")
    else:
        a_payload = None
    if args.market in ("hk", "all"):
        hk_payload = classify_market_regime("hk")
    else:
        hk_payload = None

    if args.format == "json":
        out = {}
        if a_payload is not None:
            out["a"] = a_payload
        if hk_payload is not None:
            out["hk"] = hk_payload
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        if a_payload is not None:
            print(_render_text(a_payload, market_label="A 股"))
        if a_payload is not None and hk_payload is not None:
            print()
        if hk_payload is not None:
            print(_render_text(hk_payload, market_label="港股"))


if __name__ == "__main__":
    main()

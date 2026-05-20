"""
K 线数据 + 历史价位统计（基于腾讯免费接口）。

接口模式：
  - A 股：https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600519,day,,,365,qfq
  - 港股：https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get?param=hk01810,day,,,365,qfq
  - 美股：https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get?param=usBABA.OQ,day,,,365,qfq

返回格式：JSONP 包装，data.<symbol>.qfqday 或 day 是 K 线数组：
  [date, open, close, high, low, volume, ...]

本模块设计原则：
  - 不依赖登录 token（雪球 K 线需要 token，腾讯不需要）
  - 给出"历年高低"+"当前价 vs 历史阈值"的结构化摘要，让 agent / LLM 不再凭印象写技术分析
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime
from typing import Any

from .http import fetch  # type: ignore
from .cache import cached  # type: ignore


def _build_kline_url(symbol: str, market: str, count: int = 1500, period: str = "day") -> tuple[str, str]:
    """返回 (url, kline_key)，kline_key 用于从返回 JSON 里取 K 线列表。"""
    s = symbol.upper().strip()
    if market == "a":
        # A 股：sh600519 / sz000001 / bj430047
        digits = re.sub(r"\D", "", s)
        if s.startswith("SH") or digits.startswith("6"):
            tcode = f"sh{digits}"
        elif s.startswith("BJ") or digits.startswith(("4", "8")):
            tcode = f"bj{digits}"
        else:
            tcode = f"sz{digits}"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=k&param={tcode},{period},,,{count},qfq"
        return url, tcode
    elif market == "hk":
        digits = re.sub(r"\D", "", s).zfill(5)
        tcode = f"hk{digits}"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get?_var=k&param={tcode},{period},,,{count},qfq"
        return url, tcode
    elif market == "us":
        # 美股代码映射：BABA → usBABA.N（NYSE）/ AAPL → usAAPL.OQ（Nasdaq）
        # 腾讯需要后缀，但同时支持不带后缀的 fallback
        ticker = re.sub(r"[^A-Z0-9.]", "", s)
        # 多次尝试不同后缀
        tcode = f"us{ticker}"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get?_var=k&param={tcode}.OQ,{period},,,{count},qfq"
        return url, f"{tcode}.OQ"
    else:
        raise ValueError(f"unknown market: {market}")


def _parse_kline_response(text: str) -> tuple[dict, list, str | None]:
    """解析腾讯 jsonp 返回，返回 (payload_dict, raw_kline_list, error_kind).

    error_kind: None / "rate_limit" / "no_data" / "parse_failed"
    """
    m = re.search(r"=\s*({.*})\s*;?\s*$", text, re.DOTALL)
    if not m:
        m = re.search(r"({.*})", text, re.DOTALL)
    if not m:
        return {}, [], "parse_failed"
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}, [], "parse_failed"
    # 检测限流：{"code":-1,"msg":"limit error","data":[]}
    if data.get("code") == -1 and "limit" in (data.get("msg") or "").lower():
        return {}, [], "rate_limit"
    container = data.get("data", {}) or {}
    if not container:
        return {}, [], "no_data"
    for _k, payload in container.items():
        if not isinstance(payload, dict):
            continue
        raw = payload.get("qfqday") or payload.get("day") or payload.get("fqday") or []
        if raw:
            return payload, raw, None
    return {}, [], "no_data"


@cached(ttl=4 * 3600, key_prefix="kline")
def fetch_daily_kline(symbol: str, market: str, count: int = 1500) -> list[dict]:
    """拿日 K 线。返回 [{date, open, high, low, close, volume}]。

    美股代码后缀 fallback：.OQ (Nasdaq) → .N (NYSE) → 无后缀
    缓存 4 小时（盘后稳定，盘中大部分情况下当天最后一根仍准确到分钟级）。
    """
    raw_kline: list = []
    import time as _time

    def _request_kline(url: str, max_rate_limit_retries: int = 3):
        """带腾讯 limit-error 自动退避的请求。"""
        for attempt in range(max_rate_limit_retries + 1):
            try:
                r = fetch(url, timeout=15, retries=2)
            except Exception as e:  # noqa: BLE001
                return [], f"req_error:{e}"
            _, raw, err = _parse_kline_response(r.text)
            if err == "rate_limit" and attempt < max_rate_limit_retries:
                wait = 3 + attempt * 5
                print(
                    f"[kline] 腾讯限频，等 {wait}s 重试（{attempt+1}/{max_rate_limit_retries}）",
                    file=sys.stderr,
                )
                _time.sleep(wait)
                continue
            return raw, err
        return [], "rate_limit"

    if market == "us":
        ticker = re.sub(r"[^A-Z0-9]", "", symbol.upper())
        candidates = [f"us{ticker}.OQ", f"us{ticker}.N", f"us{ticker}"]
        best: list = []
        for tcode in candidates:
            url = (
                f"https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get"
                f"?_var=k&param={tcode},day,,,{count},qfq"
            )
            raw, err = _request_kline(url)
            if err:
                print(f"[kline] 美股 {tcode}: {err}", file=sys.stderr)
            if len(raw) > len(best):
                best = raw
            if len(best) >= count // 2:
                break
        raw_kline = best
        if not raw_kline:
            print(f"[kline] 美股 {symbol} 无 K 线数据", file=sys.stderr)
            return []
    else:
        url, _ = _build_kline_url(symbol, market, count=count, period="day")
        raw_kline, err = _request_kline(url)
        if err and not raw_kline:
            print(f"[kline] {symbol} ({market}) {err}", file=sys.stderr)
            return []

    out = []
    for k in raw_kline:
        try:
            out.append({
                "date": k[0],
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]) if len(k) > 5 and k[5] else 0,
            })
        except (ValueError, IndexError, TypeError):
            continue
    return out


def compute_bollinger_bands(
    kline: list[dict],
    period: int = 20,
    multiplier: float = 2.0,
    lookback: int = 60,
) -> dict | None:
    """计算 BOLL 指标。返回带宽、当前位置、是否挤压。

    返回字段：
      middle:         MA(period) 中轨
      upper:          middle + multiplier * std
      lower:          middle - multiplier * std
      bandwidth_pct:  (upper - lower) / middle * 100  —— 带宽百分比
      position_pct:   当前收盘价在带内的相对位置（0=下轨, 100=上轨）
      squeeze:        当前带宽是否处于 lookback 日内的最低位（即将变盘）
      volatility_pct: std / middle * 100（年化前日波动率）

    kline 不足 period 根时返回 None。
    """
    if len(kline) < period:
        return None
    close_prices = [k["close"] for k in kline[-lookback:]] if len(kline) >= lookback else [k["close"] for k in kline]
    recent = close_prices[-period:]
    middle_val = sum(recent) / period
    if middle_val <= 0:
        return None
    variance = sum((c - middle_val) ** 2 for c in recent) / period
    std_val = math.sqrt(variance)
    upper = middle_val + multiplier * std_val
    lower = middle_val - multiplier * std_val
    bandwidth_pct = (upper - lower) / middle_val * 100
    current_close = close_prices[-1]
    position_pct = (current_close - lower) / (upper - lower) * 100 if upper > lower else 50.0
    position_pct = max(0.0, min(100.0, position_pct))

    squeeze = False
    if len(close_prices) >= lookback:
        historical_bandwidths = []
        for i in range(period, len(close_prices) + 1):
            window = close_prices[i - period:i]
            m = sum(window) / period
            if m <= 0:
                continue
            v = sum((c - m) ** 2 for c in window) / period
            s = math.sqrt(v)
            bw = (m + multiplier * s - (m - multiplier * s)) / m * 100
            historical_bandwidths.append(bw)
        if historical_bandwidths:
            min_bw = min(historical_bandwidths)
            squeeze = bandwidth_pct <= min_bw * 1.05

    volatility_pct = std_val / middle_val * 100

    return {
        "middle": round(middle_val, 4),
        "upper": round(upper, 4),
        "lower": round(lower, 4),
        "bandwidth_pct": round(bandwidth_pct, 2),
        "position_pct": round(position_pct, 1),
        "squeeze": squeeze,
        "volatility_pct": round(volatility_pct, 2),
    }


def compute_volume_analysis(kline: list[dict]) -> dict | None:
    """量价关系分析。返回成交量趋势、量价配合判断。

    返回字段：
      vol_5d_avg:       近 5 日均量
      vol_20d_avg:      近 20 日均量
      vol_ratio:        近 5 日均量 / 近 20 日均量
      price_5d_chg_pct: 近 5 日价格涨跌
      label:            量价配合标签（见下文）
      recent_days:      可用数据天数

    label 可选值：
      rising_with_volume:  放量上涨（最健康）
      rising_shrinking:    缩量上涨（动能减弱，警惕）
      falling_with_volume: 放量下跌（真实出货，最危险）
      falling_shrinking:   缩量下跌（抛压不足，可能是假摔）
      sideways:            量价平（无明显方向）
      insufficient:        数据不足
    """
    if len(kline) < 5:
        return {"label": "insufficient", "recent_days": len(kline)}
    recent_5 = kline[-5:]
    recent_20 = kline[-20:] if len(kline) >= 20 else kline
    vol_5 = [k.get("volume", 0) for k in recent_5]
    vol_20 = [k.get("volume", 0) for k in recent_20]
    avg_vol_5 = sum(vol_5) / len(vol_5) if vol_5 else 0
    avg_vol_20 = sum(vol_20) / len(vol_20) if vol_20 else 0
    vol_ratio_val = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 1.0
    price_5d_chg = (kline[-1]["close"] / kline[-5]["close"] - 1) * 100 if len(kline) >= 5 and kline[-5]["close"] > 0 else 0
    price_up = price_5d_chg > 1.0
    price_down = price_5d_chg < -1.0
    vol_expanding = vol_ratio_val > 1.2
    vol_shrinking = vol_ratio_val < 0.8
    if vol_expanding and price_up:
        label = "rising_with_volume"
    elif vol_shrinking and price_up:
        label = "rising_shrinking"
    elif vol_expanding and price_down:
        label = "falling_with_volume"
    elif vol_shrinking and price_down:
        label = "falling_shrinking"
    else:
        label = "sideways"
    return {
        "label": label,
        "vol_5d_avg": round(avg_vol_5, 0),
        "vol_20d_avg": round(avg_vol_20, 0),
        "vol_ratio": round(vol_ratio_val, 2),
        "price_5d_chg_pct": round(price_5d_chg, 2),
        "recent_days": len(kline),
    }


def summarize_price_history(kline: list[dict], current_price: float | None = None) -> dict:
    """
    输出结构化的"历史价位摘要"，专门给 agent/LLM 写技术分析时提供事实依据。

    返回字段：
      coverage:        K 线覆盖区间 first_date / last_date / total_days
      yearly:          每年 [year, low, low_date, high, high_date, close_first, close_last, change_pct]
      ytd:             年初至今 [start_date, start_price, ytd_high, ytd_low, ytd_high_date, ytd_low_date, days_in_window]
      windows:         {30d/60d/90d/180d/365d} 各窗口的 high/low/avg
      thresholds:      给定一组关键价位（例：[今日价, 整数关口, 历史均价]）每个的"上一次盘中触及/跌破"日期
      position:        当前价相对：年内高/低、历史 52w 高/低、历史最低/最高 的相对位置 % + 绝对差
      regime:          'NEAR_YTD_HIGH' / 'NEAR_YTD_LOW' / 'IN_RANGE' / 'NEW_LOW' / 'NEW_HIGH'
      breakout:        当前价是否盘中创出 YTD 新低/新高（True/False + 类型）
      boll:            BOLL(20,2) 指标 {middle, upper, lower, bandwidth_pct, position_pct, squeeze, volatility_pct}
      volume:          量价分析 {label, vol_5d_avg, vol_20d_avg, vol_ratio, price_5d_chg_pct, recent_days}
    """
    if not kline:
        return {"error": "kline empty"}

    # 用 current_price（最新分钟级行情），如果没传就用最后一根 K 线的 close
    cur = current_price if current_price is not None else kline[-1]["close"]

    out: dict = {"current_price": cur}
    out["coverage"] = {
        "first_date": kline[0]["date"],
        "last_date": kline[-1]["date"],
        "total_days": len(kline),
    }

    # 历年高低
    by_year: dict[str, list[dict]] = {}
    for k in kline:
        y = k["date"][:4]
        by_year.setdefault(y, []).append(k)
    yearly = []
    for y in sorted(by_year):
        rows = by_year[y]
        low = min(rows, key=lambda x: x["low"])
        high = max(rows, key=lambda x: x["high"])
        first_close = rows[0]["close"]
        last_close = rows[-1]["close"]
        change = (last_close / first_close - 1) * 100 if first_close > 0 else 0
        yearly.append({
            "year": y,
            "trading_days": len(rows),
            "low": low["low"],
            "low_date": low["date"],
            "high": high["high"],
            "high_date": high["date"],
            "open_close": first_close,
            "close": last_close,
            "year_change_pct": round(change, 2),
        })
    out["yearly"] = yearly

    # YTD 统计
    today_year = kline[-1]["date"][:4]
    ytd_rows = by_year.get(today_year, [])
    if ytd_rows:
        ytd_high = max(ytd_rows, key=lambda x: x["high"])
        ytd_low = min(ytd_rows, key=lambda x: x["low"])
        out["ytd"] = {
            "year": today_year,
            "start_date": ytd_rows[0]["date"],
            "start_close": ytd_rows[0]["close"],
            "ytd_high": ytd_high["high"],
            "ytd_high_date": ytd_high["date"],
            "ytd_low": ytd_low["low"],
            "ytd_low_date": ytd_low["date"],
            "trading_days": len(ytd_rows),
            "ytd_change_pct": round((cur / ytd_rows[0]["close"] - 1) * 100, 2),
        }

    # 时间窗口
    windows: dict = {}
    for label, days in [("30d", 30), ("60d", 60), ("90d", 90), ("180d", 180), ("365d", 365)]:
        sub = kline[-days:] if len(kline) >= days else kline
        if not sub:
            continue
        h = max(sub, key=lambda x: x["high"])
        l = min(sub, key=lambda x: x["low"])
        avg = sum(k["close"] for k in sub) / len(sub)
        windows[label] = {
            "high": h["high"], "high_date": h["date"],
            "low": l["low"], "low_date": l["date"],
            "avg_close": round(avg, 4),
            "from_date": sub[0]["date"], "to_date": sub[-1]["date"],
        }
    out["windows"] = windows

    # 关键阈值倒查（为了给"破位"提供事实证据）
    # 当前价附近的整数关口（向下 5 级、向上 5 级），步长根据价格自适应
    if cur < 1:
        step = 0.1
    elif cur < 10:
        step = 0.5
    elif cur < 50:
        step = 1
    elif cur < 200:
        step = 5
    else:
        step = 10
    base = int(cur / step) * step
    levels = sorted(set([round(base + i * step, 2) for i in range(-5, 6)]))
    threshold_info = []
    today = kline[-1]["date"]
    for th in levels:
        # 上一次盘中触及（low ≤ th），不含今天
        hits_low = [k for k in kline if k["low"] <= th and k["date"] < today]
        # 上一次盘中突破到上方（high ≥ th）
        hits_high = [k for k in kline if k["high"] >= th and k["date"] < today]
        info: dict = {"level": th}
        if hits_low:
            last_low = hits_low[-1]
            info["last_touched_below"] = last_low["date"]
            info["last_touched_low_value"] = last_low["low"]
        else:
            info["last_touched_below"] = None  # 历史从未跌到 th 以下
        if hits_high:
            last_high = hits_high[-1]
            info["last_touched_above"] = last_high["date"]
            info["last_touched_high_value"] = last_high["high"]
        threshold_info.append(info)
    out["thresholds"] = threshold_info

    # 当前价相对位置
    all_high = max(kline, key=lambda x: x["high"])
    all_low = min(kline, key=lambda x: x["low"])
    last_52w = kline[-260:] if len(kline) >= 260 else kline
    h52 = max(last_52w, key=lambda x: x["high"])
    l52 = min(last_52w, key=lambda x: x["low"])

    def pct(a: float, b: float) -> float:
        if b == 0:
            return 0
        return round((a / b - 1) * 100, 2)

    out["position"] = {
        "all_time_high": all_high["high"], "all_time_high_date": all_high["date"],
        "all_time_low": all_low["low"], "all_time_low_date": all_low["date"],
        "from_all_time_high_pct": pct(cur, all_high["high"]),
        "from_all_time_low_pct": pct(cur, all_low["low"]),
        "high_52w": h52["high"], "high_52w_date": h52["date"],
        "low_52w": l52["low"], "low_52w_date": l52["date"],
        "from_52w_high_pct": pct(cur, h52["high"]),
        "from_52w_low_pct": pct(cur, l52["low"]),
    }
    if "ytd" in out:
        out["position"]["from_ytd_high_pct"] = pct(cur, out["ytd"]["ytd_high"])
        out["position"]["from_ytd_low_pct"] = pct(cur, out["ytd"]["ytd_low"])

    # regime / breakout 判断
    last_low = kline[-1]["low"]
    last_high = kline[-1]["high"]
    is_new_ytd_low = "ytd" in out and last_low <= out["ytd"]["ytd_low"] + 1e-6
    is_new_ytd_high = "ytd" in out and last_high >= out["ytd"]["ytd_high"] - 1e-6
    is_new_52w_low = last_low <= l52["low"] + 1e-6
    is_new_52w_high = last_high >= h52["high"] - 1e-6
    is_new_all_low = last_low <= all_low["low"] + 1e-6
    is_new_all_high = last_high >= all_high["high"] - 1e-6

    out["breakout"] = {
        "new_ytd_low": is_new_ytd_low,
        "new_ytd_high": is_new_ytd_high,
        "new_52w_low": is_new_52w_low,
        "new_52w_high": is_new_52w_high,
        "new_all_time_low": is_new_all_low,
        "new_all_time_high": is_new_all_high,
    }

    if is_new_all_high:
        regime = "NEW_ALL_TIME_HIGH"
    elif is_new_all_low:
        regime = "NEW_ALL_TIME_LOW"
    elif is_new_ytd_high:
        regime = "NEW_YTD_HIGH"
    elif is_new_ytd_low:
        regime = "NEW_YTD_LOW"
    else:
        # 在区间内
        if "ytd" in out:
            yh = out["ytd"]["ytd_high"]
            yl = out["ytd"]["ytd_low"]
            mid = (yh + yl) / 2
            regime = "NEAR_YTD_HIGH" if cur > mid else "NEAR_YTD_LOW"
        else:
            regime = "IN_RANGE"
    out["regime"] = regime

    boll = compute_bollinger_bands(kline)
    if boll:
        out["boll"] = boll

    vol = compute_volume_analysis(kline)
    if vol:
        out["volume"] = vol

    return out

"""个股主力资金流：优先东方财富，必要时显式降级雪球。

为什么主用东财而不是雪球？
  - 雪球 screener 的 ``main_net_inflows`` 只有"今日累计"一个数字，无分层、无历史
  - 东财 ``push2his.eastmoney.com/api/qt/stock/fflow/kline/get`` 提供：
        近 ~120 个交易日的逐日「主力 / 超大单 / 大单 / 中单 / 小单」净额
    且无登录（与已知被封的 push2 实时行情接口是不同 host）。

支持范围
  - A 股沪深主板 / 创业板 / 科创板：✅
  - 港股：✅（东财根据成交单笔大小推算分级，参考价值低于 A 股）
  - 北交所（4/8 开头）：❌（``eastmoney_secid`` 会抛 ValueError）
  - 美股：❌（"主力资金"不是美股的标准市场指标）

接口路径迭代历史
  - 2026-05 之前：``/api/qt/stock/fflow/daykline/get`` 返回 13 列（含占比 + 收盘价 + 涨跌幅）
  - 2026-05-初：``/daykline/get`` 在 push2his 被反爬封禁（TLS 后服务端立刻 RST）；
    切到 ``/api/qt/stock/fflow/kline/get`` 仍能拿到 ~120 天数据，但接口只返回前
    6 列（日期 + 主力 / 小 / 中 / 大 / 超大 金额），占比 / 收盘价 / 涨跌幅都没了。
  - 2026-05-14：东财把反爬规则反过来 —— ``/kline/get`` 被封，``/daykline/get`` 复活，
    后者继续返回完整 13 列。本模块从此改为 **双路径自动 fallback**：
    先试 daykline（更丰富的 13 列），失败再 fallback 到 kline，再失败 raise。
    上层 ``_render_text`` 和决策树都已能容忍 6 列输入（缺的字段为 ``None``），
    因此 fallback 安全；只是 ``render_analysis_text`` 的 "当日资金分层占比" 表
    在走 kline 路径时会显示 "-"。

盘中优先级（2026-05-20 起）：
  1. 东方财富分时资金流（push2 ``fflow/kline/get`` + ``klt=1``）
  2. 东方财富日资金流（上一交易日完整 / 收盘后当天最终日线）
  3. 雪球 ``capital/assort`` 仅作为 **显式兜底**
     - 只在 A 股且东财盘中分时不可用时使用
     - 必须在输出里明确标注“雪球兜底，数据不完全准确”
     - 不允许静默把雪球口径混成东财口径给上游决策

字段对应（接口返回 ``klines`` 的逗号分隔字符串）：
  f51 日期 / f52 主力净额(元) / f53 小单 / f54 中单 / f55 大单 / f56 超大单
  ── 以下字段只在 daykline 路径才返回 ──
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
from .xueqiu import XueqiuClient


_FFLOW_URLS: tuple[str, ...] = (
    # 主路径：daykline（13 列，含占比 / 收盘价 / 涨跌幅）—— 2026-05-14 复活
    "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
    # 备用路径：kline（6 列）—— 2026-05 初切上来的，目前被反爬封禁
    "https://push2his.eastmoney.com/api/qt/stock/fflow/kline/get",
)
_FFLOW_INTRADAY_URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
# 仍按 13 列向后端要，6 列路径解析层会容忍少列。
_FFLOW_FIELDS1 = "f1,f2,f3,f7"
_FFLOW_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63"
_FFLOW_UT = "b2884a393a59ad64002292a3e90d46a5"


def _market_session_start(market: str) -> time | None:
    if market == "a":
        return time(9, 30)
    if market == "hk":
        return time(9, 30)
    return None


def _market_session_close(market: str) -> time | None:
    if market == "a":
        return time(15, 0)
    if market == "hk":
        return time(16, 0)
    return None


def _market_day_started(market: str, now: datetime) -> bool:
    start = _market_session_start(market)
    if start is None:
        return False
    if now.weekday() >= 5:
        return False
    return now.time() >= start


def _market_close_stamp(market: str, date_text: str) -> str | None:
    close_t = _market_session_close(market)
    if close_t is None or not date_text:
        return None
    try:
        day = datetime.fromisoformat(str(date_text)).date()
    except ValueError:
        return None
    return datetime.combine(day, close_t, tzinfo=CN_TZ).isoformat()


def _infer_today_flow_mode(market: str, now: datetime) -> str:
    """返回默认资金流口径。

    - ``previous_close``：盘前，默认上一交易日完整资金流
    - ``intraday_live``：盘中（含午休），默认今日盘中累计
    - ``today_close``：盘后，默认今日收盘资金流
    """
    if not _market_day_started(market, now):
        return "previous_close"
    close_t = _market_session_close(market)
    if close_t is None:
        return "previous_close"
    if now.time() <= close_t:
        return "intraday_live"
    return "today_close"


def _flow_label(flow_mode: str, flow_as_of: str | None, flow_source: str | None = None) -> str:
    as_of = flow_as_of or "-"
    if flow_source == "xueqiu_intraday_fallback":
        return f"今日盘中累计（雪球兜底，东财盘中不可用，数据不完全准确），截至 {as_of}"
    if flow_mode == "intraday_live":
        return f"今日盘中累计（东财分时），截至 {as_of}"
    if flow_mode == "today_close":
        return f"今日收盘资金流（东财日线），截至 {as_of}"
    return f"上一交易日完整资金流（东财日线），截至 {as_of}"


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
        if " " in last_date:
            last_date = last_date.split(" ", 1)[0]
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
    """容忍 6-13 列两种返回。

    - 2026-05 之前的 ``/daykline/get`` 返回 13 列（含占比 + 收盘价 + 涨跌幅）
    - 2026-05 起的 ``/kline/get`` 只返回 6 列（日期 + 主力 + 4 个分层金额）

    缺的列填 ``None``，上层 (``summarize_fund_flow`` / ``_render_text``) 已有
    None 兜底；决策树只用 ``main`` 金额，不受影响。
    """
    parts = row.split(",")
    if len(parts) < 6:
        return None

    def at(i: int) -> float | None:
        return _to_float(parts[i]) if i < len(parts) else None

    return {
        "date": parts[0],
        "main": at(1),
        "small": at(2),
        "mid": at(3),
        "big": at(4),
        "super_big": at(5),
        "main_pct": at(6),
        "small_pct": at(7),
        "mid_pct": at(8),
        "big_pct": at(9),
        "super_big_pct": at(10),
        "close": at(11),
        "change_pct": at(12),
    }


# schema_version=3: 2026-05-14 重新启用 daykline 双路径 fallback，bump 让旧缓存失效，
# 让下一次抓取能拿到更丰富的 13 列数据（含 close / change_pct / *_pct）。
@cached(ttl=_ttl_for_call, key_prefix="ff", schema_version=3)
def fetch_daily_fund_flow(market: str, code: str) -> list[dict]:
    """拉东财个股资金流日 K（约 120 个交易日）。

    **双路径 fallback**：依次尝试 ``_FFLOW_URLS`` 里的每个 URL，
    任意一个返回非空 klines 即返回。东财的反爬规则会在 daykline / kline
    之间来回切换，这种 fallback 让本接口对反爬规则变化具有韧性。

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
    last_err: Exception | None = None
    for url in _FFLOW_URLS:
        try:
            r = fetch(url, params=params, timeout=10)
            payload = r.json() or {}
            klines = ((payload.get("data") or {}).get("klines") or [])
            if not klines:
                # 接口返回空，尝试下一个路径（rc=102 或 data:null 都会走这里）
                continue
            rows = [_parse_kline_row(k) for k in klines]
            return [row for row in rows if row is not None and row.get("date")]
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    # 所有路径都失败，抛最后一个错误（让上层走 retry / cache 兜底）
    if last_err is not None:
        raise last_err
    return []


# 港股东财分时接口返回的金额单位异常（约 A 股的 1000 倍），需做市场特定缩放。
# 已验证：01810 分时主力 -648 亿 vs 实际约 -0.65 亿（1000x）；
# 01276 分时主力 -3.74 亿 vs 日线量级 0.03-0.17 亿（同样偏大约 1000x）。
_HK_INTRADAY_SCALE = 1000.0
_AMOUNT_FIELDS = ("main", "small", "mid", "big", "super_big")


@cached(
    ttl=lambda market, code: _ttl_for_moment(market, datetime.now(CN_TZ)),
    key_prefix="ffi",
    schema_version=2,
)
def fetch_intraday_fund_flow(market: str, code: str) -> list[dict]:
    """拉东财分时资金流（分钟级累计）。"""
    secid = eastmoney_secid(market, code)
    params = {
        "lmt": 0,
        "klt": 1,
        "fields1": _FFLOW_FIELDS1,
        "fields2": _FFLOW_FIELDS2,
        "secid": secid,
        "ut": _FFLOW_UT,
    }
    r = fetch(_FFLOW_INTRADAY_URL, params=params, timeout=10)
    payload = r.json() or {}
    klines = ((payload.get("data") or {}).get("klines") or [])
    rows = [_parse_kline_row(k) for k in klines]
    rows = [row for row in rows if row is not None and row.get("date")]
    # 港股分时接口金额单位异常（~1000x），缩放至元
    if market == "hk":
        for row in rows:
            for field in _AMOUNT_FIELDS:
                val = row.get(field)
                if val is not None:
                    row[field] = val / _HK_INTRADAY_SCALE
    return rows


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


# ────────────────────────────────────────────────────────────────
# 多周期交叉验证（cross_validate）
# ────────────────────────────────────────────────────────────────
# 单看 20d `regime` 标签会有滞后性：标签可能停留在 PERSISTENT_INFLOW，
# 但近 5d 已经转流出。本模块把"短期优先 / 共振确认 / 加速衰竭 / reversal 背书"
# 这套人肉判读固化成结构化字段，让上游（报告渲染 + spc 决策树 + LLM prompt）
# 都拿同一份结论，不再各自手算。

# 主判断采用 1d / 5d / 10d / 20d 四周期；3d 留给 buy 路径阈值检查。
_CROSS_PERIODS: tuple[str, ...] = ("1d", "5d", "10d", "20d")

# 共振 / 加速判定的最小窗口净额阈值（亿元）：低于此值视为"接近 0，方向不可靠"。
_DIR_ZERO_EPS_YI = 0.05

# concentration_5d_in_20d 的"近期集中"判定阈值。
_CONCENTRATION_THRESHOLD = 0.5


def _direction(value: float | None) -> str | None:
    """把净额映射为方向标签。``None`` 透传，绝对值过小视为 ``"flat"``。"""
    if value is None:
        return None
    if abs(value) < _DIR_ZERO_EPS_YI:
        return "flat"
    return "in" if value > 0 else "out"


def _daily_avg(window: dict[str, Any]) -> float | None:
    """窗口日均净额（亿元）。"""
    total = window.get("main_yi")
    days = window.get("days")
    if total is None or not days:
        return None
    return total / days


def _classify_acceleration(rolling: dict[str, dict]) -> str | None:
    """比较 10d→5d→1d 日均净额变化，输出加速 / 衰竭 / 平稳。

    判定逻辑：以 5d 方向为主轴
      - 5d 流入：1d 日均 > 5d 日均 > 10d 日均 → ``accelerating_inflow``
                 1d 日均 < 5d 日均（同向变小）→ ``decelerating_inflow``
                 1d 反向（变流出）→ ``decelerating_inflow``（已经在变坏）
      - 5d 流出：对称
      - 5d ~ 0：``stable``
      - 任一周期日均缺失：``None``
    """
    avg_1d = _daily_avg(rolling.get("1d") or {})
    avg_5d = _daily_avg(rolling.get("5d") or {})
    avg_10d = _daily_avg(rolling.get("10d") or {})
    if avg_1d is None or avg_5d is None or avg_10d is None:
        return None
    if abs(avg_5d) < _DIR_ZERO_EPS_YI:
        return "stable"
    if avg_5d > 0:
        if avg_1d > avg_5d > avg_10d:
            return "accelerating_inflow"
        if avg_1d < avg_5d:
            return "decelerating_inflow"
        return "stable"
    # avg_5d < 0
    if avg_1d < avg_5d < avg_10d:
        return "accelerating_outflow"
    if avg_1d > avg_5d:
        return "decelerating_outflow"
    return "stable"


def _classify_verdict(
    *,
    directions: dict[str, str | None],
    all_aligned: bool,
    short_long_conflict: bool,
    acceleration: str | None,
    reversal_label: str | None,
    reversal_confirmed: bool | None,
) -> tuple[str, str]:
    """综合给一个枚举 + 一句中文解读。

    优先级（高 → 低）：
      1. 共振流入 / 共振流出（all_aligned + accelerating）
      2. reversal 已确认（被短期数据背书）
      3. 短长冲突（短期与 20d 方向反转）
      4. 衰竭（同向但日均在变小）
      5. 持续 / 震荡（兜底）
    """
    accel = acceleration or ""
    if all_aligned and accel == "accelerating_inflow":
        return "RESONANCE_INFLOW", "四周期一致流入且日均加速，主力共振进场"
    if all_aligned and accel == "accelerating_outflow":
        return "RESONANCE_OUTFLOW", "四周期一致流出且日均加速，主力共振撤离"

    if reversal_label == "OUTFLOW_TO_INFLOW" and reversal_confirmed:
        return "REVERSAL_INFLOW_CONFIRMED", "1d/5d 同向流入背书 OUTFLOW_TO_INFLOW，反转已确认"
    if reversal_label == "INFLOW_TO_OUTFLOW" and reversal_confirmed:
        return "REVERSAL_OUTFLOW_CONFIRMED", "1d/5d 同向流出背书 INFLOW_TO_OUTFLOW，趋势切换已确认"
    if reversal_label and reversal_confirmed is False:
        return "REVERSAL_UNCONFIRMED", "20d 标签提示反转，但 1d/5d 未同向背书，反转未确认"

    if short_long_conflict:
        long_dir = directions.get("20d")
        if long_dir == "in":
            return "WEAKENING_INFLOW", "20d 仍流入但短期已转流出，趋势在退潮"
        if long_dir == "out":
            return "WEAKENING_OUTFLOW", "20d 仍流出但短期已转流入，下跌动能在衰竭"
        return "MIXED", "短长方向冲突"

    if accel == "decelerating_inflow":
        return "DECELERATING_INFLOW", "同向流入但日均在变小，动能减弱"
    if accel == "decelerating_outflow":
        return "DECELERATING_OUTFLOW", "同向流出但日均在变小，抛压减弱"

    long_dir = directions.get("20d")
    if long_dir == "in":
        return "PERSISTENT_INFLOW_STEADY", "持续净流入，节奏平稳"
    if long_dir == "out":
        return "PERSISTENT_OUTFLOW_STEADY", "持续净流出，节奏平稳"
    return "MIXED", "进出反复 / 数据混乱"


def cross_validate(rolling: dict[str, dict] | None,
                   reversal: str | None = None) -> dict[str, Any]:
    """对 1d/5d/10d/20d 四周期做交叉验证，输出结构化结论。

    设计目标：
      - 让"短期优先 / 共振确认 / 加速衰竭 / reversal 背书"四项判读只在这里算一次
      - 上游（报告 / 决策树 / prompt）只引用字段，不再各自重写算法
      - 输入残缺（缺 1d 或 10d）时也能给出能用的字段，缺什么置 ``None``

    Args:
        rolling: ``summarize_fund_flow`` 输出的 rolling 子树
        reversal: ``summarize_fund_flow`` 输出的 reversal 标签（用于判断
                  ``reversal_confirmed`` 是否被短期数据背书）

    Returns: 见模块文档；任何字段都允许为 ``None``，调用方需自己 None 兜底。
    """
    rolling = rolling or {}
    nets = {p: (rolling.get(p) or {}).get("main_yi") for p in _CROSS_PERIODS}
    directions = {p: _direction(nets[p]) for p in _CROSS_PERIODS}

    # all_aligned: 四周期都有数据且方向都是 in（或都是 out）；flat 不算"对齐"
    nonnull_dirs = [d for d in directions.values() if d is not None]
    if len(nonnull_dirs) == len(_CROSS_PERIODS):
        all_in = all(d == "in" for d in nonnull_dirs)
        all_out = all(d == "out" for d in nonnull_dirs)
        all_aligned = all_in or all_out
    else:
        all_aligned = False

    # short_long_conflict：1d 与 20d 反向，且 5d 与 20d 反向（避免单日噪音误判）
    d1, d5, d20 = directions.get("1d"), directions.get("5d"), directions.get("20d")
    short_long_conflict = (
        d20 in ("in", "out")
        and d5 in ("in", "out")
        and d1 in ("in", "out")
        and d5 != d20
        and d1 != d20
    )
    if short_long_conflict:
        conflict_kind = (
            "short_outflow_long_inflow" if d20 == "in" else "short_inflow_long_outflow"
        )
    else:
        conflict_kind = None

    # concentration_5d_in_20d：仅在 5d / 20d 方向一致时定义；否则为 None
    concentration: float | None = None
    n5, n20 = nets.get("5d"), nets.get("20d")
    if (
        n5 is not None and n20 is not None
        and abs(n20) >= _DIR_ZERO_EPS_YI
        and ((n5 > 0 and n20 > 0) or (n5 < 0 and n20 < 0))
    ):
        concentration = round(abs(n5) / abs(n20), 3)

    acceleration = _classify_acceleration(rolling)

    # reversal_confirmed：reversal 标签是否被短期数据背书
    #   - OUTFLOW_TO_INFLOW 需要 1d ≥ 0 且 5d > 0
    #   - INFLOW_TO_OUTFLOW 需要 1d ≤ 0 且 5d < 0
    #   - 无 reversal 标签 → None
    #   - 有 reversal 但 1d/5d 缺数据 → None
    reversal_confirmed: bool | None = None
    if reversal in ("OUTFLOW_TO_INFLOW", "INFLOW_TO_OUTFLOW"):
        if n5 is None or nets.get("1d") is None:
            reversal_confirmed = None
        elif reversal == "OUTFLOW_TO_INFLOW":
            reversal_confirmed = (nets["1d"] >= 0) and (n5 > 0)
        else:
            reversal_confirmed = (nets["1d"] <= 0) and (n5 < 0)

    is_resonance = all_aligned and (
        acceleration in ("accelerating_inflow", "accelerating_outflow")
    )

    verdict, verdict_zh = _classify_verdict(
        directions=directions,
        all_aligned=all_aligned,
        short_long_conflict=short_long_conflict,
        acceleration=acceleration,
        reversal_label=reversal,
        reversal_confirmed=reversal_confirmed,
    )

    return {
        "periods": list(_CROSS_PERIODS),
        "directions": directions,
        "all_aligned": all_aligned,
        "short_long_conflict": short_long_conflict,
        "conflict_kind": conflict_kind,
        "acceleration": acceleration,
        "concentration_5d_in_20d": concentration,
        "is_resonance": is_resonance,
        "reversal_confirmed": reversal_confirmed,
        "verdict": verdict,
        "verdict_zh": verdict_zh,
    }


# ────────────────────────────────────────────────────────────────
# regime / reversal 中文标签（供 fund_flow.py / company_analysis.py 等共享）
# ────────────────────────────────────────────────────────────────
# 之前 fund_flow.py::_render_text 写过一份纯文字版，company_analysis.py
# 又写过一份带 emoji 版，规则改了要改两处。这里固化成模块级常量，调用方
# 选 ``with_emoji=True`` 决定是否带前缀 emoji。

REGIME_LABEL_ZH: dict[str, str] = {
    "PERSISTENT_INFLOW": "持续净流入",
    "PERSISTENT_OUTFLOW": "持续净流出",
    "OSCILLATING": "震荡 / 进出反复",
}

REGIME_EMOJI: dict[str, str] = {
    "PERSISTENT_INFLOW": "🟢",
    "PERSISTENT_OUTFLOW": "🔴",
    "OSCILLATING": "⚪️",
}

REVERSAL_LABEL_ZH: dict[str, str] = {
    "INFLOW_TO_OUTFLOW": "近 5 日由流入转为流出",
    "OUTFLOW_TO_INFLOW": "近 5 日由流出转为流入",
}

REVERSAL_EMOJI: dict[str, str] = {
    "INFLOW_TO_OUTFLOW": "⚠️",
    "OUTFLOW_TO_INFLOW": "🟡",
}


def regime_label(regime: str | None, *, with_emoji: bool = False) -> str:
    """统一渲染 regime 标签。``None`` 返回 ``"-"``。"""
    if not regime:
        return "-"
    zh = REGIME_LABEL_ZH.get(regime, regime)
    if with_emoji:
        return f"{REGIME_EMOJI.get(regime, '')} {zh}".strip()
    return zh


def reversal_label(reversal: str | None, *, with_emoji: bool = False) -> str | None:
    """统一渲染 reversal 标签。``None`` 返回 ``None``（调用方可省略本行）。"""
    if not reversal:
        return None
    zh = REVERSAL_LABEL_ZH.get(reversal, reversal)
    if with_emoji:
        return f"{REVERSAL_EMOJI.get(reversal, '')} {zh}".strip()
    return zh


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
        # 多周期交叉验证结论：消费方（报告渲染 + spc 决策树）直接读字段，
        # 不要再各自手算 1d/5d/10d/20d 方向、加速、共振。
        "cross_validation": cross_validate(rolling, reversal),
    }


def _xueqiu_symbol(market: str, code: str) -> str | None:
    """转换为雪球资金流接口接受的 symbol；港股 / 北交所雪球资金流无效，返回 None。"""
    if market != "a":
        return None  # 雪球 capital/assort 港股返回 data=None，没意义
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("0", "3")):
        return f"SZ{code}"
    return None  # 北交所 4/8 也跳过


def _normalize_flow_timestamp(date_text: str | None) -> tuple[str | None, str | None]:
    """把日线 / 分时日期统一拆成 ``(YYYY-MM-DD, ISO时间戳)``。"""
    if not date_text:
        return None, None
    raw = str(date_text).strip()
    try:
        if " " in raw:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=CN_TZ)
            return dt.date().isoformat(), dt.isoformat()
        dt = datetime.fromisoformat(raw).replace(tzinfo=CN_TZ)
        return dt.date().isoformat(), dt.isoformat()
    except ValueError:
        if " " in raw:
            return raw.split(" ", 1)[0], None
        return raw, None


def _validate_flow_balance(row: dict[str, Any]) -> bool:
    """校验资金流分档平衡：主力/中单/小单不能全部同号。

    每一笔成交都有买卖双方，三个档位（主力=超大+大单、中单、小单）
    的净额加起来应趋近于 0。三个档位全部同号（全正或全负）在物理上
    不可能，说明接口返回了垃圾数据（多见于港股 push2.eastmoney.com
    分时接口字段映射错误）。
    """
    main = row.get("main")
    mid = row.get("mid")
    small = row.get("small")
    if main is None or mid is None or small is None:
        return False  # 缺字段，不可靠
    if main > 0 and mid > 0 and small > 0:
        return False
    if main < 0 and mid < 0 and small < 0:
        return False
    return True


def _live_today_row_from_eastmoney(
    market: str,
    code: str,
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """优先实时主源：东财分时累计资金流最后一个分钟点。

    港股 push2.eastmoney.com 分时接口字段映射不可靠，会返回全正/全负
    的物理不可能数据，因此增加 _validate_flow_balance 校验；
    校验失败时自动拒绝，由上层 fallback 到雪球兜底或上一交易日。
    """
    warnings: list[str] = []
    try:
        rows = fetch_intraday_fund_flow(market, code)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"东财盘中资金流不可用：{e}")
        return None, None, warnings
    if not rows:
        warnings.append("东财盘中资金流接口返回空。")
        return None, None, warnings

    last = rows[-1]
    today_date, timestamp_iso = _normalize_flow_timestamp(last.get("date"))
    if not today_date:
        warnings.append("东财盘中资金流最后一条时间戳缺失。")
        return None, None, warnings
    row = dict(last)
    row["date"] = today_date

    if not _validate_flow_balance(row):
        warnings.append(
            "东财盘中资金流分档失衡（主力/中单/小单全同号，物理不可能），"
            "疑似接口字段映射错误，已拒绝此数据。"
        )
        return None, None, warnings

    return row, timestamp_iso, warnings


def _live_today_row_from_xueqiu(
    market: str,
    code: str,
    *,
    xq: XueqiuClient | None = None,
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """构造一条"今日累计" synthetic 资金流日线。

    仅 A 股可用；返回 ``(row, timestamp_iso, warnings)``。
    ``timestamp_iso`` 优先取分钟级资金流最后一个点，拿不到再退回 assort 的日期戳。
    """
    warnings: list[str] = []
    xq_sym = _xueqiu_symbol(market, code)
    if not xq_sym:
        return None, None, warnings

    cli = xq or XueqiuClient()
    if not cli.is_logged_in and not cli.cookie_expired:
        return None, None, warnings

    assort = cli.capital_assort(xq_sym)
    if assort is None:
        if cli.cookie_expired:
            warnings.append(
                "雪球登录 cookie 已过期，今日盘中资金流不可用；已回退到上一交易日完整资金流。"
            )
        return None, None, warnings

    intraday_items = cli.capital_intraday(xq_sym)
    last_ts_ms = None
    if intraday_items:
        last_ts_ms = intraday_items[-1].get("timestamp")
    if not last_ts_ms:
        last_ts_ms = assort.get("timestamp")

    timestamp_iso = None
    if last_ts_ms:
        try:
            timestamp_iso = datetime.fromtimestamp(float(last_ts_ms) / 1000, CN_TZ).isoformat()
        except Exception:  # noqa: BLE001
            timestamp_iso = None

    def net(k_buy: str, k_sell: str) -> float | None:
        b = assort.get(k_buy)
        s = assort.get(k_sell)
        if b is None or s is None:
            return None
        return float(b) - float(s)

    big = net("buy_large", "sell_large")
    xlarge = net("buy_xlarge", "sell_xlarge")
    mid = net("buy_medium", "sell_medium")
    small = net("buy_small", "sell_small")
    main = (big or 0.0) + (xlarge or 0.0)
    buy_total = assort.get("buy_total") or 0
    sell_total = assort.get("sell_total") or 0
    grand_total = float(buy_total) + float(sell_total)

    def _pct(v: float | None) -> float | None:
        if v is None or grand_total <= 0:
            return None
        return round(v / grand_total * 100, 2)

    today_date = None
    if timestamp_iso:
        today_date = timestamp_iso[:10]
    else:
        stamp_ms = assort.get("timestamp")
        if stamp_ms:
            try:
                today_date = datetime.fromtimestamp(float(stamp_ms) / 1000, CN_TZ).date().isoformat()
            except Exception:  # noqa: BLE001
                today_date = None
    if not today_date:
        today_date = datetime.now(CN_TZ).date().isoformat()

    row = {
        "date": today_date,
        "main": main,
        "small": small,
        "mid": mid,
        "big": big,
        "super_big": xlarge,
        "main_pct": _pct(main),
        "small_pct": _pct(small),
        "mid_pct": _pct(mid),
        "big_pct": _pct(big),
        "super_big_pct": _pct(xlarge),
        "close": None,
        "change_pct": None,
    }
    return row, timestamp_iso, warnings


def _enrich_with_xueqiu_assort(summary: dict[str, Any], market: str, code: str,
                                xq: XueqiuClient | None = None) -> None:
    """用雪球 capital/assort 补全 ``summary['today']`` 里东财新接口缺的占比 / 分层。

    数据契约：
      - 雪球 ``buy_*`` / ``sell_*`` 是当日累计买入 / 卖出金额（元）；净额 = buy - sell
      - 主力 = xlarge + large（A 股的 ``xlarge`` 通常为 None，此时退化为只算 large）
      - 占比分母 = buy_total + sell_total（即当日总成交额，与东财 main_pct 算法一致）
      - cookie 失效 / 港股 / 北交所 时直接 no-op；如失效会把告警写到 summary['warnings']
    """
    today = summary.get("today")
    if not today:
        return  # 主源没数据，没什么可 enrich 的

    xq_sym = _xueqiu_symbol(market, code)
    if not xq_sym:
        return  # 港股 / 北交所：雪球 assort 无效

    cli = xq or XueqiuClient()
    if not cli.is_logged_in and not cli.cookie_expired:
        # 用户未配 cookie。这是"未配置"而不是"失效"，不打告警（避免对非雪球用户骚扰）
        return

    data = cli.capital_assort(xq_sym)
    if data is None:
        # 拿不到（cookie 过期 / WAF）；如果是 cookie 过期，cli 已经打过告警
        if cli.cookie_expired:
            warnings = summary.setdefault("warnings", [])
            warnings.append(
                "雪球登录 cookie 已过期，当日资金分层占比 / 收盘 / 涨跌幅 补全失败；"
                "请按 SKILL.md §0 重新导出 cookie 到 ~/.config/stock-market-hub/xueqiu.cookie"
            )
        return

    def net(k_buy: str, k_sell: str) -> float | None:
        b = data.get(k_buy)
        s = data.get(k_sell)
        if b is None or s is None:
            return None
        return float(b) - float(s)

    big = net("buy_large", "sell_large")
    xlarge = net("buy_xlarge", "sell_xlarge")
    mid = net("buy_medium", "sell_medium")
    small = net("buy_small", "sell_small")
    main = (big or 0) + (xlarge or 0)
    buy_total = data.get("buy_total") or 0
    sell_total = data.get("sell_total") or 0
    grand_total = float(buy_total) + float(sell_total)  # 总成交（元）

    def _pct(v: float | None) -> float | None:
        if v is None or grand_total <= 0:
            return None
        return round(v / grand_total * 100, 2)

    # 仅补"东财新接口缺失的字段"，不覆盖东财已有的非 None 值
    enriched_keys = {
        "super_big_yi": _to_yi(xlarge),
        "big_yi": _to_yi(big),
        "mid_yi": _to_yi(mid),
        "small_yi": _to_yi(small),
        "main_pct": _pct(main),
        "super_big_pct": _pct(xlarge),
        "big_pct": _pct(big),
        "mid_pct": _pct(mid),
        "small_pct": _pct(small),
    }
    for k, v in enriched_keys.items():
        if v is not None and today.get(k) is None:
            today[k] = v

    # 标注来源，便于 audit
    summary.setdefault("sources", {})
    summary["sources"]["assort"] = "雪球 capital/assort"
    summary["sources"].setdefault("kline_main", "东方财富 fflow/kline")


def get_fund_flow_summary(market: str, code: str) -> dict[str, Any]:
    """组合调用：东财日线 + 东财分时优先 + 雪球显式兜底。"""
    now = datetime.now(CN_TZ)
    flow_mode = _infer_today_flow_mode(market, now)

    try:
        rows = fetch_daily_fund_flow(market, code)
    except ValueError as e:
        return {"error": str(e), "as_of": None, "today": None, "rolling": {}, "regime": None, "reversal": None}
    if not rows:
        return {"error": "fflow 接口返回空", "as_of": None, "today": None, "rolling": {}, "regime": None, "reversal": None}
    effective_rows = rows
    flow_as_of = _market_close_stamp(market, rows[-1].get("date") or "")
    warnings: list[str] = []
    today = now.date().isoformat()
    have_today_daily_row = bool(rows and rows[-1].get("date") == today)
    flow_source = "eastmoney_previous_close"

    if flow_mode == "intraday_live":
        live_row, live_ts, live_warnings = _live_today_row_from_eastmoney(market, code)
        warnings.extend(live_warnings)
        if live_row is not None:
            flow_source = "eastmoney_intraday"
            today_date = live_row["date"]
            if rows and rows[-1].get("date") == today_date:
                effective_rows = rows[:-1] + [live_row]
            else:
                effective_rows = rows + [live_row]
            flow_as_of = live_ts or now.isoformat()
        else:
            live_row, live_ts, live_warnings = _live_today_row_from_xueqiu(market, code)
            warnings.extend(live_warnings)
            if live_row is not None:
                flow_source = "xueqiu_intraday_fallback"
                warnings.append("当前盘中资金流已降级为雪球兜底口径，数据不完全准确。")
                today_date = live_row["date"]
                if rows and rows[-1].get("date") == today_date:
                    effective_rows = rows[:-1] + [live_row]
                else:
                    effective_rows = rows + [live_row]
                flow_as_of = live_ts or now.isoformat()
            else:
                flow_mode = "previous_close"
                flow_source = "eastmoney_previous_close"
                warnings.append("东财盘中资金流不可用，雪球兜底也不可用；已回退到上一交易日完整资金流。")
    elif flow_mode == "today_close":
        if have_today_daily_row:
            flow_source = "eastmoney_today_close"
            flow_as_of = _market_close_stamp(market, rows[-1].get("date") or "") or now.isoformat()
        else:
            live_row, live_ts, live_warnings = _live_today_row_from_eastmoney(market, code)
            warnings.extend(live_warnings)
            if live_row is not None:
                flow_source = "eastmoney_intraday"
                effective_rows = rows + [live_row]
                flow_as_of = live_ts or now.isoformat()
            else:
                live_row, live_ts, live_warnings = _live_today_row_from_xueqiu(market, code)
                warnings.extend(live_warnings)
                if live_row is not None:
                    flow_source = "xueqiu_intraday_fallback"
                    warnings.append("东财收盘日线尚未刷新，暂用雪球盘中资金兜底，数据不完全准确。")
                    effective_rows = rows + [live_row]
                    flow_as_of = live_ts or now.isoformat()
                else:
                    flow_mode = "previous_close"
                    flow_source = "eastmoney_previous_close"
                    warnings.append("东财收盘日线尚未刷新，实时兜底也不可用；已回退到上一交易日完整资金流。")

    summary = summarize_fund_flow(effective_rows)
    summary["rolling_as_of"] = summary.get("as_of")
    summary["fetched_at"] = now.isoformat()
    summary["flow_mode"] = flow_mode
    summary["flow_source"] = flow_source
    summary["flow_as_of"] = flow_as_of
    summary["flow_label"] = _flow_label(flow_mode, flow_as_of, flow_source)
    if warnings:
        summary.setdefault("warnings", [])
        summary["warnings"].extend(warnings)
    return summary


# ============ CLI ============ #

def _render_text(market: str, code: str, summary: dict[str, Any]) -> str:
    if summary.get("error"):
        return f"# 主力资金动向 {market.upper()} {code}\n\n（暂无数据：{summary['error']}）"
    lines: list[str] = []
    lines.append(f"# 主力资金动向 {market.upper()} {code}")
    if summary.get("flow_label"):
        lines.append(f"\n> 资金流口径：**{summary.get('flow_label')}**")
    regime = summary.get("regime")
    lines.append(f"\n> regime: **{regime or '-'}** ({regime_label(regime)})")
    reversal = summary.get("reversal")
    rev_zh = reversal_label(reversal)
    if reversal and rev_zh:
        lines.append(f"> reversal: **{reversal}** ({rev_zh})")
    if market == "hk":
        lines.append("> _港股资金分级为东财根据成交单笔大小推算，仅供参考。_")
    cross = summary.get("cross_validation") or {}
    if cross.get("verdict"):
        lines.append(f"> cross_validation: **{cross['verdict']}** ({cross.get('verdict_zh') or '-'})")

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

    # 多周期解读：交叉验证结论一并铺开，让 LLM 不用再现算
    if cross:
        lines.append("")
        lines.append("## 多周期解读")
        lines.append(f"- **verdict**：`{cross.get('verdict')}` — {cross.get('verdict_zh') or '-'}")
        dirs = cross.get("directions") or {}
        dir_str = " / ".join(f"{p}={dirs.get(p) or '-'}" for p in cross.get("periods") or _CROSS_PERIODS)
        lines.append(f"- **方向**：{dir_str}")
        lines.append(
            f"- **共振**：all_aligned={cross.get('all_aligned')}, "
            f"acceleration={cross.get('acceleration') or '-'}, "
            f"is_resonance={cross.get('is_resonance')}"
        )
        if cross.get("short_long_conflict"):
            lines.append(f"- **短长冲突**：⚠️ {cross.get('conflict_kind')}（短期优先 → 信号偏弱）")
        conc = cross.get("concentration_5d_in_20d")
        if conc is not None:
            tag = "（≥0.5，近期集中）" if conc >= _CONCENTRATION_THRESHOLD else ""
            lines.append(f"- **5d/20d 集中度**：{conc}{tag}")
        rc = cross.get("reversal_confirmed")
        if rc is True:
            lines.append("- **reversal 背书**：✅ 1d/5d 同向背书，反转已确认")
        elif rc is False:
            lines.append("- **reversal 背书**：❌ 1d/5d 未同向背书，反转未确认（不应据此 buy）")

    today = summary.get("today") or {}
    close = today.get("close")
    chg = today.get("change_pct")
    close_str = "-" if close is None else f"{close}"
    chg_str = "-" if chg is None else f"{chg:+.2f}%"
    lines.append("")
    lines.append(
        f"## 当日资金分层（{summary.get('flow_label') or summary.get('as_of')}，收盘 {close_str}，涨跌 {chg_str}）"
    )
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

    # 数据源标注
    sources = summary.get("sources") or {}
    if sources:
        lines.append("")
        lines.append("> _数据来源：" + " + ".join(sorted(set(sources.values()))) + "_")

    # ⚠️ warnings —— 雪球 cookie 过期等需要用户行动的情况
    warnings = summary.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("---")
        lines.append("⚠️ **警告**")
        for w in warnings:
            lines.append(f"- {w}")

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

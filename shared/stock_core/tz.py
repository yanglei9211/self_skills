"""Shared timezone constants & 简易交易时段判断。

避免每个脚本都重复写 ``try: from zoneinfo import ZoneInfo`` 的样板。
统一只暴露 ``CN_TZ`` 一个对象；如果运行环境不带 zoneinfo（极少见），
退化为 UTC，行为与原各处 fallback 保持一致。

``is_market_open(market)`` 仅给"动态缓存 TTL"这种宽松场景用，
不考虑节假日（节假日里行情接口本来也不会更新，缓存即便 60s 也无副作用）。
"""
from __future__ import annotations

from datetime import datetime, time, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


CN_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else timezone.utc

LOCAL_TZ = CN_TZ


# ---- 交易时段（东八区，无节假日校验）---- #
# A 股：09:30-11:30 + 13:00-15:00
_A_SHARE_SESSIONS: tuple[tuple[time, time], ...] = (
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
)
# 港股：09:30-12:00 + 13:00-16:00（与北京同时区，不需要单独时区换算）
_HK_SESSIONS: tuple[tuple[time, time], ...] = (
    (time(9, 30), time(12, 0)),
    (time(13, 0), time(16, 0)),
)


def _within_sessions(now_t: time, sessions: tuple[tuple[time, time], ...]) -> bool:
    return any(start <= now_t <= end for start, end in sessions)


def is_market_open(market: str, now: datetime | None = None) -> bool:
    """简单交易时段判断（不校验节假日）。

    适用场景：盘中给行情/资金流类抓取选用更短的缓存 TTL，盘后回到长 TTL。
    支持 ``market`` 取值：``'a'`` / ``'hk'``；其它（``'us'`` 等）返回 ``False``。
    """
    if market not in ("a", "hk"):
        return False
    if now is None:
        now = datetime.now(CN_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    else:
        now = now.astimezone(CN_TZ)
    if now.weekday() >= 5:  # 周六、周日
        return False
    sessions = _A_SHARE_SESSIONS if market == "a" else _HK_SESSIONS
    return _within_sessions(now.time(), sessions)

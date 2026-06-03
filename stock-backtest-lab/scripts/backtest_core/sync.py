"""
数据同步器：将个股日 K 线和指数日 K 线从腾讯接口同步到本地 SQLite。

设计原则：
  - 同步与回测分离：sync 负责拉取/增量更新，回测只读本地库
  - 增量同步：读取 sync_state 跳过已覆盖日期
  - 支持断点续传：sync_state 记录最后 sync 日期
  - 指数 tcode 严格保持大小写：sh000300, sz399006, hkHSI, hkHSTECH
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from .store import (
    get_connection,
    init_schema,
    insert_daily_bars,
    insert_index_bars,
    read_sync_state,
    upsert_instrument,
    upsert_sync_state,
)


def sync_daily_kline(
    conn,
    symbol: str,
    market: str,
    from_date: str = "2020-01-01",
    count: int = 2000,
    name: str | None = None,
) -> dict:
    """同步单只股票的日 K 线到本地 SQLite。

    复用 shared/stock_core/kline.py::fetch_daily_kline。
    支持增量同步：如果 sync_state 中 last_trade_date 已覆盖则跳过。
    过滤 trade_date >= from_date 的数据。

    返回 dict 包含 synced, skipped, total, status, error 等字段。
    """
    # 检查是否已经是最新（增量同步）
    state = read_sync_state(conn, "daily", symbol)
    if state and state["status"] == "ok" and state.get("last_trade_date"):
        last_date = state["last_trade_date"]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # 如果上次同步日期距今不超过 2 个自然日，数据已是最新
        try:
            last_dt = datetime.strptime(last_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            today_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if (today_dt - last_dt).days <= 2:
                # 数据已是最新，跳过网络请求
                existing = conn.execute(
                    "SELECT COUNT(*) FROM daily_bars WHERE symbol = ?", (symbol,)
                ).fetchone()
                total = existing[0] if existing else 0
                return {
                    "symbol": symbol,
                    "status": "ok",
                    "synced": 0,
                    "skipped": total,
                    "total": total,
                    "first_date": last_date,
                    "last_date": last_date,
                }
        except (ValueError, TypeError):
            pass  # 日期解析失败，继续全量同步

    try:
        from shared.stock_core.kline import fetch_daily_kline
    except ImportError:
        return {
            "symbol": symbol,
            "status": "error",
            "error": "无法导入 shared.stock_core.kline",
            "synced": 0,
        }

    # 拉取 K 线
    try:
        raw_bars = fetch_daily_kline(symbol, market, count=count)
    except Exception as e:
        upsert_sync_state(conn, "daily", symbol, None, "error", str(e))
        return {
            "symbol": symbol,
            "status": "error",
            "error": str(e),
            "synced": 0,
        }

    if not raw_bars:
        upsert_sync_state(conn, "daily", symbol, None, "error", "无数据返回")
        return {
            "symbol": symbol,
            "status": "error",
            "error": "无数据返回",
            "synced": 0,
        }

    # 过滤 from_date 之后的数据
    filtered = [b for b in raw_bars if b["date"] >= from_date]

    if not filtered:
        return {
            "symbol": symbol,
            "status": "ok",
            "synced": 0,
            "skipped": len(raw_bars),
            "total": len(raw_bars),
            "first_date": None,
            "last_date": None,
        }

    # 写入数据库
    init_schema(conn)
    if name:
        upsert_instrument(conn, symbol, market, name)
    else:
        upsert_instrument(conn, symbol, market)

    n = insert_daily_bars(conn, filtered, symbol)

    # 更新同步状态
    last_date = filtered[-1]["date"]
    upsert_sync_state(conn, "daily", symbol, last_date, "ok")

    return {
        "symbol": symbol,
        "status": "ok",
        "synced": n,
        "skipped": len(raw_bars) - n,
        "total": len(raw_bars),
        "first_date": filtered[0]["date"],
        "last_date": last_date,
    }


def sync_index_kline(
    conn,
    index_code: str,
    name: str | None = None,
    from_date: str = "2020-01-01",
    count: int = 2000,
) -> dict:
    """同步单个指数的日 K 线到本地 SQLite。

    复用 shared/stock_core/market_regime.py::fetch_index_daily。
    tcode 严格保持大小写：sh000300, sz399006, hkHSI, hkHSTECH。

    返回 dict 包含 synced, total, status, error 等字段。
    """
    # 增量同步检查
    state = read_sync_state(conn, "index", index_code)
    if state and state["status"] == "ok" and state.get("last_trade_date"):
        last_date = state["last_trade_date"]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            last_dt = datetime.strptime(last_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            today_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if (today_dt - last_dt).days <= 2:
                existing = conn.execute(
                    "SELECT COUNT(*) FROM index_bars WHERE index_code = ?", (index_code,)
                ).fetchone()
                total = existing[0] if existing else 0
                return {
                    "index_code": index_code,
                    "status": "ok",
                    "synced": 0,
                    "skipped": total,
                    "total": total,
                    "first_date": last_date,
                    "last_date": last_date,
                }
        except (ValueError, TypeError):
            pass  # 日期解析失败，继续全量同步

    try:
        from shared.stock_core.market_regime import fetch_index_daily
    except ImportError:
        return {
            "index_code": index_code,
            "status": "error",
            "error": "无法导入 shared.stock_core.market_regime",
            "synced": 0,
        }

    try:
        raw_bars = fetch_index_daily(index_code, count=count)
    except Exception as e:
        upsert_sync_state(conn, "index", index_code, None, "error", str(e))
        return {
            "index_code": index_code,
            "status": "error",
            "error": str(e),
            "synced": 0,
        }

    if not raw_bars:
        upsert_sync_state(conn, "index", index_code, None, "error", "无数据返回")
        return {
            "index_code": index_code,
            "status": "error",
            "error": "无数据返回",
            "synced": 0,
        }

    # 过滤 from_date 之后的数据
    filtered = [b for b in raw_bars if b["date"] >= from_date]

    if not filtered:
        return {
            "index_code": index_code,
            "status": "ok",
            "synced": 0,
            "skipped": len(raw_bars),
            "total": len(raw_bars),
            "first_date": None,
            "last_date": None,
        }

    # 写入数据库
    init_schema(conn)
    n = insert_index_bars(conn, filtered, index_code)

    # 更新同步状态
    last_date = filtered[-1]["date"]
    upsert_sync_state(conn, "index", index_code, last_date, "ok")

    return {
        "index_code": index_code,
        "status": "ok",
        "synced": n,
        "total": len(raw_bars),
        "first_date": filtered[0]["date"],
        "last_date": last_date,
    }


# 默认要同步的指数列表
DEFAULT_INDICES = [
    {"tcode": "sh000300", "name": "沪深300"},
    {"tcode": "sz399006", "name": "创业板指"},
    {"tcode": "hkHSI", "name": "恒生指数"},
    {"tcode": "hkHSTECH", "name": "恒生科技指数"},
]


def sync_all_indices(
    conn,
    from_date: str = "2020-01-01",
    count: int = 2000,
) -> list[dict]:
    """同步所有默认指数到本地 SQLite。

    默认指数：沪深300、创业板指、恒生指数、恒生科技指数。
    返回 list[dict]，每条是 sync_index_kline 的结果。
    """
    results = []
    for idx in DEFAULT_INDICES:
        result = sync_index_kline(conn, idx["tcode"], idx["name"], from_date=from_date, count=count)
        results.append(result)
        # 避免请求过快
        if len(results) < len(DEFAULT_INDICES):
            time.sleep(0.3)
    return results

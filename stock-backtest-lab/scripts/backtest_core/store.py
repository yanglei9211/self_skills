"""
SQLite 存储层：建表、写入、读取、同步状态管理。

设计原则：
  - 回测只读本地库，不访问网络
  - 使用 WAL 模式 + 外键约束
  - INSERT OR REPLACE 保证幂等同步
  - 原始数据优先：OHLCV 原始行必须落库，指标可从原始行重算
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def get_db_path() -> Path:
    """返回数据库路径 ~/.cache/stock-backtest-lab/history.sqlite，自动创建目录。"""
    cache_dir = Path.home() / ".cache" / "stock-backtest-lab"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "history.sqlite"


def get_connection() -> sqlite3.Connection:
    """获取数据库连接（WAL 模式，外键启用）。"""
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """建表：instruments, daily_bars, index_bars, sync_state。使用 IF NOT EXISTS。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS instruments (
            symbol TEXT PRIMARY KEY,
            market TEXT NOT NULL,
            name TEXT,
            type TEXT NOT NULL DEFAULT 'stock',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_bars (
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            amount REAL,
            source TEXT NOT NULL,
            adjustment TEXT NOT NULL DEFAULT 'qfq',
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (symbol, trade_date, adjustment)
        );

        CREATE TABLE IF NOT EXISTS index_bars (
            index_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (index_code, trade_date)
        );

        CREATE TABLE IF NOT EXISTS sync_state (
            dataset TEXT NOT NULL,
            symbol TEXT NOT NULL,
            last_trade_date TEXT,
            last_fetched_at TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            PRIMARY KEY (dataset, symbol)
        );
    """)
    conn.commit()


def upsert_instrument(
    conn: sqlite3.Connection,
    symbol: str,
    market: str,
    name: str | None = None,
    type: str = "stock",
) -> None:
    """插入或更新标的元信息。"""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO instruments (symbol, market, name, type, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            market = excluded.market,
            name = COALESCE(excluded.name, instruments.name),
            type = excluded.type
        """,
        (symbol, market, name, type, now),
    )
    conn.commit()


def insert_daily_bars(
    conn: sqlite3.Connection,
    bars: list[dict],
    symbol: str,
    source: str = "tencent",
    adjustment: str = "qfq",
) -> int:
    """写入日线 OHLCV，INSERT OR REPLACE 幂等。返回写入行数。

    bars 中每条 dict 的 key 映射：date -> trade_date。
    其余字段直接使用：open, high, low, close, volume。
    """
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for bar in bars:
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_bars
                (symbol, trade_date, open, high, low, close, volume, amount, source, adjustment, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                bar["date"],  # bar 的 key 是 date，映射到 trade_date
                bar.get("open", 0),
                bar.get("high", 0),
                bar.get("low", 0),
                bar.get("close", 0),
                bar.get("volume", 0),
                bar.get("amount"),
                source,
                adjustment,
                now,
            ),
        )
        count += 1
    conn.commit()
    return count


def insert_index_bars(
    conn: sqlite3.Connection,
    bars: list[dict],
    index_code: str,
    source: str = "tencent",
) -> int:
    """写入指数日线，INSERT OR REPLACE 幂等。返回写入行数。"""
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for bar in bars:
        conn.execute(
            """
            INSERT OR REPLACE INTO index_bars
                (index_code, trade_date, open, high, low, close, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                index_code,
                bar["date"],
                bar.get("open", 0),
                bar.get("high", 0),
                bar.get("low", 0),
                bar.get("close", 0),
                source,
                now,
            ),
        )
        count += 1
    conn.commit()
    return count


def read_daily_bars(
    conn: sqlite3.Connection,
    symbol: str,
    from_date: str | None = None,
    to_date: str | None = None,
    adjustment: str = "qfq",
) -> list[dict]:
    """读取个股日线，按 trade_date 升序。返回 list[dict]。"""
    conditions = ["symbol = ?", "adjustment = ?"]
    params: list[Any] = [symbol, adjustment]
    if from_date:
        conditions.append("trade_date >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("trade_date <= ?")
        params.append(to_date)
    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT trade_date, open, high, low, close, volume, amount, source FROM daily_bars WHERE {where} ORDER BY trade_date",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def read_index_bars(
    conn: sqlite3.Connection,
    index_code: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """读取指数日线，按 trade_date 升序。返回 list[dict]。"""
    conditions = ["index_code = ?"]
    params: list[Any] = [index_code]
    if from_date:
        conditions.append("trade_date >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("trade_date <= ?")
        params.append(to_date)
    where = " AND ".join(conditions)
    rows = conn.execute(
        f"SELECT index_code, trade_date, open, high, low, close, source FROM index_bars WHERE {where} ORDER BY trade_date",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def check_coverage(
    conn: sqlite3.Connection,
    symbol: str,
    from_date: str,
    to_date: str,
) -> dict:
    """检查数据覆盖情况。返回 dict 包含 first_date, last_date, total_rows, has_gap 等信息。"""
    rows = conn.execute(
        """
        SELECT MIN(trade_date) as first_date, MAX(trade_date) as last_date, COUNT(*) as total_rows
        FROM daily_bars
        WHERE symbol = ? AND trade_date >= ? AND trade_date <= ?
        """,
        (symbol, from_date, to_date),
    ).fetchone()
    if not rows or not rows["first_date"]:
        return {
            "symbol": symbol,
            "from_date": from_date,
            "to_date": to_date,
            "first_date": None,
            "last_date": None,
            "total_rows": 0,
            "covered": False,
        }
    return {
        "symbol": symbol,
        "from_date": from_date,
        "to_date": to_date,
        "first_date": rows["first_date"],
        "last_date": rows["last_date"],
        "total_rows": rows["total_rows"],
        "covered": _date_within_tolerance(rows["first_date"], from_date, 5)
        and _date_within_tolerance(rows["last_date"], to_date, 5, reverse=True),
    }


def _date_within_tolerance(actual: str, target: str, tolerance_days: int = 5, reverse: bool = False) -> bool:
    """检查 actual 日期是否在 target 日期的 tolerance_days 容忍范围内。

    用于覆盖检查：from_date/to_date 可能是非交易日，first_date/last_date 是最近的交易日。
    允许日期偏差最多 tolerance_days 个自然日。
    当 reverse=True 时检查结束日：检查 target 是否在 actual 之后 tolerance_days 内。
    """
    try:
        a = date.fromisoformat(actual)
        t = date.fromisoformat(target)
        if reverse:
            return -tolerance_days <= (t - a).days <= tolerance_days
        return -tolerance_days <= (a - t).days <= tolerance_days
    except (ValueError, TypeError):
        if reverse:
            return actual >= target
        return actual <= target


def upsert_sync_state(
    conn: sqlite3.Connection,
    dataset: str,
    symbol: str,
    last_trade_date: str | None,
    status: str,
    error: str | None = None,
) -> None:
    """更新同步状态。"""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO sync_state
            (dataset, symbol, last_trade_date, last_fetched_at, status, error)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (dataset, symbol, last_trade_date, now, status, error),
    )
    conn.commit()


def read_sync_state(
    conn: sqlite3.Connection,
    dataset: str,
    symbol: str,
) -> dict | None:
    """读取同步状态。返回 dict 或 None。"""
    row = conn.execute(
        "SELECT * FROM sync_state WHERE dataset = ? AND symbol = ?",
        (dataset, symbol),
    ).fetchone()
    return dict(row) if row else None

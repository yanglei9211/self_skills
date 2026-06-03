"""
Snapshot Store：给定回测日期 T，从 SQLite 中读取 T 及之前的数据，构造 BacktestSnapshot。

核心约束：
  - 所有 SQL 查询使用 WHERE trade_date <= t_date
  - 不访问网络，只读本地 SQLite
  - K 线不足 60 天返回 data_insufficient
"""

from __future__ import annotations

from .models import BacktestSnapshot
from .store import read_daily_bars, read_index_bars


def build_snapshot(
    conn,
    symbol: str,
    t_date: str,
    market: str = "a",
) -> BacktestSnapshot | None:
    """构造回测快照。

    从 SQLite 读取 symbol 截止到 t_date 的所有日线数据。
    同时读取所有默认指数的日线（截止到 t_date）。

    Args:
        conn: SQLite 连接
        symbol: 股票代码（如 "SZ000333"）
        t_date: 回测日期 T（格式 "YYYY-MM-DD"），所有数据截止到此日期
        market: 市场（"a" 或 "hk"）

    Returns:
        BacktestSnapshot 或 None（数据不足以计算信号，即 K 线 < 60 天）
    """
    # 读取个股 K 线（截止到 t_date）
    stock_klines = read_daily_bars(conn, symbol, to_date=t_date)

    if len(stock_klines) < 60:
        # K 线不足 60 天，无法可靠计算技术指标
        return None

    # 读取指数 K 线（截止到 t_date）
    index_codes = ["sh000300", "sz399006", "hkHSI", "hkHSTECH"]
    index_klines = {}
    for code in index_codes:
        rows = read_index_bars(conn, code, to_date=t_date)
        if rows:
            index_klines[code] = rows

    return BacktestSnapshot(
        date=t_date,
        symbol=symbol,
        market=market,
        stock_klines=stock_klines,
        index_klines=index_klines,
        data_sufficient=True,
    )

"""
数据模型：回测系统中使用的结构化数据类。

所有数据类使用 @dataclass，保持简单、可序列化。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BollInfo:
    """BOLL 指标信息。"""
    middle: float
    upper: float
    lower: float
    bandwidth_pct: float
    position_pct: float
    squeeze: bool
    volatility_pct: float


@dataclass
class VolumePriceLabel:
    """量价配合标签。"""
    label: str  # rising_with_volume / falling_with_volume / sideways / ...
    vol_5d_avg: float
    vol_20d_avg: float
    vol_ratio: float
    price_5d_chg_pct: float
    recent_days: int


@dataclass
class BacktestSnapshot:
    """回测日期 T 时可获取的全部数据。

    核心约束：所有字段的数据截止到 trade_date <= T。
    """
    date: str  # T 日，格式 YYYY-MM-DD
    symbol: str
    market: str  # 'a' 或 'hk'
    stock_klines: list[dict]  # T 及之前的所有日线，按 date 升序
    index_klines: dict[str, list[dict]]  # 各指数 K 线，key 为 tcode
    data_sufficient: bool = True  # K 线是否足够计算指标（>= 60 天）


@dataclass
class SignalVector:
    """T 日所有计算出的信号。

    所有信号只基于 T 及之前的数据计算。
    None 表示数据不足以计算该信号。
    """
    date: str
    symbol: str
    price_regime: str | None = None  # NEW_YTD_HIGH / NEAR_YTD_LOW / ...
    market_regime_a: str | None = None  # RISK_ON / NEUTRAL / RISK_OFF
    market_regime_hk: str | None = None
    boll: dict | None = None
    volume_price: dict | None = None
    ma_values: dict[str, float] | None = None  # {'MA5': 50.0, 'MA10': 49.5, ...}
    rsi: float | None = None
    # P1 预留
    fund_flow_regime: str | None = None
    fund_flow_cross: dict | None = None


@dataclass
class Decision:
    """策略引擎产生的决策。"""
    action: str  # buy / focus / watch / avoid / sell
    confidence: float  # 0.0 ~ 1.0
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    path: str | None = None  # trend / reversal / None
    entry_date: str = ""
    entry_price: float = 0.0


@dataclass
class TradeResult:
    """前向追踪的结果。"""
    entry_date: str
    entry_price: float
    action: str
    confidence: float
    fwd_returns: dict[int, float | None] = field(default_factory=dict)  # {5: +1.2, 10: +3.4, ...}
    max_drawdown: float = 0.0
    hit_stop_loss: bool = False
    hit_take_profit: bool = False
    data_incomplete: bool = False  # 前向数据不足

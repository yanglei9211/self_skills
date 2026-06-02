# Point-in-Time 回测与历史数据仓设计

> 创建日期：2026-06-01
> 状态：需求设计
> 关联技能：`stock-market-hub`、`stock-portfolio-copilot`

---

## 1. 背景与动机

当前分析流程（`SPC analyze now` + 人工判断 + LLM 复核）缺乏严格的量化验证：

- 我们对美的集团做了一次 BOLL squeeze 回测，发现"等突破确认再动手"策略的收益不如"挤压日直接入场"——这是之前没有意识到的
- 目前所有的交易规则（趋势买入、反转买入、止损/止盈阈值、trailing stop）的阈值都是**经验设定**，从未用历史数据验证过
- `stock-market-hub` 的 `smh screen` 的评分权重、`spc analyze` 的 confidence 数值，同样缺乏回测校准

需要一个**严格基于回测日期之前数据**的策略回测系统，用历史实际走势来验证和校准规则。同时，历史行情必须本地持久化，不能每次回测都临时在线拉取。

### 核心约束：任何回测日期 T，只能用 T 及之前的数据

这是本系统与"拿全量数据算指标"的最本质区别。违规 = 回测结果不可信。

---

## 2. 数据可用性与分层目标

### 2.1 P0：2020 至今的长周期 K 线 / 技术回测

第一阶段先做**不依赖资金流**的长周期回测，目标覆盖 2020-01-01 至今：

| 数据 | 当前来源 | 本地存储 | 回测用途 | 备注 |
|---|---|---|---|---|
| **个股日线 OHLCV** | `shared/stock_core/kline.py::fetch_daily_kline`（腾讯免费接口） | SQLite `daily_bars` | price_regime、BOLL、MA、RSI、量价、前向收益 | 当前函数默认 `count=1500`，大致覆盖 6 年交易日，足够覆盖 2020 至今 |
| **指数日线** | `shared/stock_core/market_regime.py::fetch_index_daily` | SQLite `index_bars` | 大盘 regime、分层统计 | 现有实现默认约 400 根，需扩展 count 到可覆盖 2020 |
| **技术指标** | 从本地 OHLCV 计算 | SQLite 可选缓存 `technical_factors` | BOLL squeeze、MA cross、量价结构 | 指标必须可从原始 K 线重算，缓存只为提速 |

P0 不使用资金流、新闻、雪球热度和公告作为入场条件。这样可以先得到一个严格、稳定、覆盖时间足够长的技术/价格回测底座。

### 2.2 P1：最近约 120 个交易日的资金流短窗验证

东财资金流接口当前只能返回最近约 120 个交易日，不能按任意历史日期 T 拉取当时可见的历史资金流。因此资金流相关策略不能声称支持 2020 至今完整 point-in-time 回测。

| 数据 | 当前来源 | 可回测范围 | 用法 |
|---|---|---|---|
| **日线资金流** | `shared/stock_core/fund_flow.py::fetch_daily_fund_flow` | 最近约 120 个交易日 | 只做短窗验证：fund_flow regime、reversal、cross_validation 对技术信号是否有增益 |
| **本地沉淀资金流** | SQLite `fund_flow_daily` | 从系统上线后开始累积 | 等未来沉淀足够长后，再纳入长期 PIT 回测 |

资金流的定位改为“增强验证”，不是 P0 的硬依赖。

### 2.3 P2：公告 point-in-time，先设计边界，后实现

公告理论上可以按发布时间过滤，但现有 `shared/stock_core/announcements.py` 主要是“从当前往前 N 天”查询，还缺少任意历史区间、分页拉全、原始时间戳保留等能力。

公告进入回测前必须先补齐：

- 巨潮 / 披露易支持 `from_date` / `to_date` 参数，而不是只用 `days`
- 支持分页拉全，避免只取第一页导致历史公告漏样本
- 保留原始发布时间戳，并区分交易时段内公告、盘后公告、非交易日公告
- 固定正向 / 风险关键词表，关键词表版本写入回测报告

在这些能力完成前，公告只作为未来扩展，不进入 P0 长周期回测的默认策略。

### 2.4 ❌ 暂不纳入回测的数据

| 数据 | 原因 |
|---|---|
| 财联社电报/新浪财经新闻 | 当前接口只能拉最近数据，缺历史日期查询 |
| 雪球关注者数 | 只有当前值，无历史序列 |
| 雪球热门帖/评论情绪 | 同上 |
| 公司深度分析 PDF | 太重，且 PDF 发布时间与市场实际消化时间不好定义 |
| 卖方研报评级 | 需要付费接口，发布时间和覆盖范围不稳定 |
| 当前概念/题材归属 | 当前归属不等于历史归属，容易引入 look-ahead bias |

### 2.5 覆盖率评估

SPC `analyze now` 的核心决策维度及其回测覆盖率：

| 决策维度 | SPC 中权重 | P0 长周期可回测？ | 说明 |
|---|---|---|---|
| 价格 regime（新高/新低/区间） | 核心 | ✅ | 从本地日线 K 线精确计算 |
| BOLL squeeze / MA / RSI / 量价配合 | 技术面 | ✅ | 从本地 OHLCV 计算 |
| 大盘 regime（RISK_ON/OFF） | 软联动 | ✅ | 扩展指数 K 线到 2020 后可计算 |
| 持仓侧风控（止损/止盈/trailing stop） | 核心 | ✅ | 基于价格和模拟成本计算 |
| 主力资金 regime + 多周期交叉验证 | 核心 | ⚠️ | 仅最近约 120 个交易日，不能做 2020 至今完整回测 |
| 公告正向/风险关键词 | 重要 | ⚠️ | 需要先补历史区间查询与时间戳处理 |
| 板块强弱（sector_strength） | 辅助 | ⚠️ | 需先确认历史板块成分和板块 K 线来源，否则只可近似 |
| 雪球关注度（crowded/接飞刀） | 辅助 | ❌ | 无历史序列 |
| 财联社新闻命中（stock_news） | 辅助 | ❌ | 无可用历史接口 |
| execution_plan 联动 | 事前 | ⚠️ | 无法回测人填预案本身，但可以回测预案价位触发后的收益 |

**阶段性结论：P0 先做“2020 至今 K 线 / 技术 / 价格风控”的可靠长周期回测；P1 再做最近 120 日资金流短窗增益验证；P2 才扩展公告 PIT。**

---

## 3. 系统架构

```
┌───────────────────────────────────────────────────────────────┐
│              Point-in-Time Backtest + SQLite Store             │
├─────────────────┬─────────────────────────────────────────────┤
│  Data Sync      │  增量同步并落库历史数据                        │
│                 │  - 个股日线（2020 至今，后续每日增量）          │
│                 │  - 指数日线（2020 至今，后续每日增量）          │
│                 │  - 资金流日线（仅最近约 120 日 + 上线后沉淀）   │
│                 │  输出：SQLite historical store                 │
├─────────────────┼─────────────────────────────────────────────┤
│  Snapshot Store │  给定 T，从 SQLite 读取 T 及之前的数据          │
│                 │  - 不在回测循环里访问外部 API                  │
│                 │  - 缺数据时显式报错或跳过，不静默在线补拉       │
│                 │  输出：BacktestSnapshot                       │
├─────────────────┼─────────────────────────────────────────────┤
│  Signal Calc    │  从 BacktestSnapshot 计算所有信号             │
│                 │  - price_regime / thresholds                  │
│                 │  - BOLL / MA / RSI / 量价配合                  │
│                 │  - market_regime (A/HK)                       │
│                 │  - P1: fund_flow regime / reversal / cross_validate│
│                 │  - P2: 公告关键词命中                          │
│                 │  输出：SignalVector                           │
├─────────────────┼─────────────────────────────────────────────┤
│  Strategy Engine │  将 SignalVector 输入策略规则，产生决策       │
│                 │  - P0: boll_squeeze / ma_breakout / price_new_high│
│                 │  - 持仓侧：hold / add / trim / sell           │
│                 │  - P1: fund_confirmed_breakout                │
│                 │  - P2: SPC 复合规则                           │
│                 │  输出：Decision( action, confidence, reasons ) │
├─────────────────┼─────────────────────────────────────────────┤
│  Forward Tracker │  从 T+1 开始追踪实际走势                      │
│                 │  - N 日收益率（5/10/20/60）                    │
│                 │  - 最大回撤                                   │
│                 │  - 是否触及止损/止盈                          │
│                 │  输出：TradeResult                            │
├─────────────────┼─────────────────────────────────────────────┤
│  Statistics      │  聚合所有 TradeResult                        │
│                 │  - 胜率 / 平均收益 / 夏普 / 最大回撤           │
│                 │  - 按市场环境分组（RISK_ON/NEUTRAL/RISK_OFF） │
│                 │  - 按年份分组                                 │
│                 │  - 参数扫描（阈值优化）                        │
│                 │  输出：BacktestReport                         │
└─────────────────┴─────────────────────────────────────────────┘
```

### 3.1 SQLite 历史数据仓

本地数据仓使用 SQLite，默认路径：

```
~/.cache/stock-backtest-lab/history.sqlite
```

核心原则：

- **回测只读本地库**：回测循环中不访问网络，避免结果随外部接口波动
- **同步与回测分离**：`sync` 负责拉取 / 增量更新，`backtest` 负责读取 / 计算
- **原始数据优先**：OHLCV、资金流原始行必须落库；技术指标可缓存，但必须能从原始行重算
- **数据版本可追溯**：记录 `source`、`adjustment`、`fetched_at`、`schema_version`

建议表结构：

```sql
CREATE TABLE instruments (
  symbol TEXT PRIMARY KEY,
  market TEXT NOT NULL,
  name TEXT,
  type TEXT NOT NULL DEFAULT 'stock',
  created_at TEXT NOT NULL
);

CREATE TABLE daily_bars (
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

CREATE TABLE index_bars (
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

CREATE TABLE fund_flow_daily (
  symbol TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  main REAL,
  small REAL,
  mid REAL,
  big REAL,
  super_big REAL,
  main_pct REAL,
  close REAL,
  change_pct REAL,
  source TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE sync_state (
  dataset TEXT NOT NULL,
  symbol TEXT NOT NULL,
  last_trade_date TEXT,
  last_fetched_at TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT,
  PRIMARY KEY (dataset, symbol)
);
```

P0 必需表：`instruments`、`daily_bars`、`index_bars`、`sync_state`。  
P1 再启用 `fund_flow_daily`。公告表等到 P2 设计。

### 3.2 关键模块接口

```python
# Snapshot Store
class BacktestSnapshot:
    """回测日期 T 时可获取的全部数据"""
    date: date
    stock_klines: pd.DataFrame       # SQLite 中 T 及之前的所有日线
    fund_flow: pd.DataFrame | None   # P1：SQLite 中 T 及之前的资金流；P0 为空
    index_klines: dict[str, pd.DataFrame]  # 各指数 K 线
    announcements: list[Announcement] | None  # P2：notice_date <= T 的公告

# Signal Calculator
class SignalVector:
    """T 日所有计算出的信号"""
    price_regime: str                # NEW_YTD_HIGH / NEAR_YTD_LOW / ...
    fund_flow_regime: str | None     # P1：PERSISTENT_INFLOW / ...
    fund_flow_cross: CrossValidation | None
    market_regime_a: str             # RISK_ON / NEUTRAL / RISK_OFF
    market_regime_hk: str
    boll: BollInfo
    volume_price: VolumePriceLabel
    ann_positive_hits: int | None
    ann_risk_hits: int | None

# Strategy Engine
class Decision:
    action: str                      # buy / focus / watch / avoid / hold / add / trim / sell
    confidence: float
    reasons: list[str]
    risks: list[str]
    path: str | None                 # "trend" / "reversal" / None

# Forward Tracker
class TradeResult:
    entry_date: date
    entry_price: float
    action: str
    confidence: float
    fwd_returns: dict[int, float]    # {5: +1.2, 10: +3.4, 20: +5.6, 60: +12.3}
    max_drawdown: float
    hit_stop_loss: bool
    hit_take_profit: bool
```

### 3.3 数据同步策略

初始化同步：

```
stockbt sync SZ000333 --from 2020-01-01
stockbt sync-index --from 2020-01-01
```

增量同步：

- 对单标的：读取 `sync_state.last_trade_date`，只拉缺失日期之后的数据
- 对指数：默认同步沪深300、创业板指、恒生指数、恒生科技指数
- 对资金流：每次同步都只会得到最近约 120 日；用 `INSERT OR REPLACE` 合并，长期历史靠上线后逐日沉淀
- 对缺口：`backtest` 启动前检查回测区间是否有足够 K 线；缺数据直接提示运行 `sync`

### 3.4 防 Look-Ahead 的具体措施

1. **回测只读 SQLite**：外部 API 只在 sync 阶段调用，避免回测中混入当前接口状态
2. **K 线数据对 T 截断**：计算 price_regime 的 YTD 高/低、52w 高/低、history high/low 时，全部基于 T 及之前的收盘价
3. **滚动指标窗口终点严格 ≤ T**：BOLL/MA/RSI/量价只使用 T 及之前的日线
4. **资金流窗口严格对 T 截断**：P1 中计算 1d/5d/10d/20d 汇总时，只使用 SQLite 中 T 及之前的数据行
5. **公告时间过滤**：P2 中只允许 `published_at <= decision_time` 的公告进入信号
6. **股票池必须按日期定义**：全市场批量回测不能默认使用“当前 top600”，否则会有幸存者偏差和未来成分偏差

---

## 4. 可回测的策略清单

### 4.1 P0：长周期技术 / 价格策略（2020 至今）

| 策略 ID | 描述 | 核心信号 | 可回测程度 |
|---|---|---|---|
| `boll_squeeze_entry` | BOLL 挤压后入场 | BW 处于低位 + 当日收盘/次日突破规则 | 🟢 100%，只依赖 OHLCV |
| `ma_breakout` | 均线突破 / 趋势跟随 | MA5/MA10/MA20/MA60 排列与突破 | 🟢 100%，只依赖 OHLCV |
| `price_new_high` | 新高趋势策略 | NEW_YTD_HIGH / NEW_52W_HIGH 后的 5/10/20/60 日收益 | 🟢 100%，只依赖 OHLCV |
| `volume_price_breakout` | 量价突破 | 放量上涨、缩量回踩、突破前高 | 🟢 100%，只依赖 OHLCV |
| `market_risk_filter` | 大盘 regime 过滤 | 技术信号 + RISK_ON/OFF 分层 | 🟢 100%，需本地指数 K 线覆盖 2020 至今 |

### 4.2 P0：持仓侧价格风控（模拟持仓）

| 策略 ID | 描述 | 核心信号 | 可回测程度 |
|---|---|---|---|
| `hard_stop_p0a` | P0a 分级硬止损（T1/T2/T3） | 浮亏% vs 分档阈值 | 🟢 100%，需模拟持仓成本 |
| `take_profit_p0b` | P0b 分级止盈 | 浮盈% vs tp_t1/t2/t3 | 🟢 100% |
| `trailing_stop_p2b` | P2b trailing stop | 从 peak 回撤% | 🟢 100%，需在回测中维护 position_peak |
| `price_break_trim` | 价格破位 → trim/sell | regime ∈ LOW / 跌破关键均线 | 🟢 100% |
| `add_position_price_only` | 价格侧加仓 | weight < max × headroom + price ≥ cost + 技术买点成立 | 🟢 100% |

### 4.3 P1：资金流短窗增强验证（最近约 120 个交易日）

| 策略 ID | 描述 | 核心信号 | 可回测程度 |
|---|---|---|---|
| `fund_confirmed_breakout` | 技术突破 + 资金确认 | 技术买点 + fund_flow != PERSISTENT_OUTFLOW | 🟡 仅最近约 120 日 |
| `fund_reversal_filter` | 反转过滤 | 技术反转 + reversal_confirmed | 🟡 仅最近约 120 日 |
| `fund_weak_trim` | 主力资金弱 + 短期续出 → trim/sell | ff_regime=PERSISTENT_OUTFLOW + 5d < 0 | 🟡 仅最近约 120 日 |

### 4.4 P2：SPC 复合策略（公告 / 资金流 / 技术）

| 策略 ID | 描述 | 可回测程度 |
|---|---|---|
| `spc_technical_only` | SPC 自选 / 持仓决策的价格技术子集 | 🟢 P0 可做 |
| `spc_fund_short_window` | 技术 + 资金流的短窗 SPC 子集 | 🟡 P1 可做，只覆盖最近约 120 日 |
| `spc_full` | SPC `analyze now` 完整决策链路 | 🟠 P2 后再做，仍缺新闻和雪球热度 |
| `screen_funnel` | `smh screen` 初筛 + SPC 精选的完整漏斗 | 🔴 暂不做，股票池和新闻/热度均有历史偏差 |

---

## 5. 策略验证用例：BOLL squeeze 入场规则

以美的集团 BOLL squeeze 为例，说明 P0 回测如何验证：

```
条件（boll_squeeze_entry）：
  BOLL bandwidth_pct 处于过去 N 日低分位
  AND 收盘价接近中轨 / 上轨
  AND volume_price 不出现明显放量下跌

入场版本：
  A. squeeze 当日收盘买入
  B. 次日突破 squeeze 日高点买入
  C. 突破上轨后买入
  D. squeeze 后 3 个交易日未突破则放弃

回测问题：
  1. 历史上有多少次满足全部条件？
  2. 满足后 5/10/20/60 日的平均收益和胜率？
  3. A/B/C/D 四种入场方式哪种收益回撤比最好？
  4. 如果按 RISK_ON / NEUTRAL / RISK_OFF 分层，差异多大？
  5. squeeze 阈值从 4% 到 10% 扫描时是否稳定？
```

同样的方法可以用于：
- 验证止损阈值（T1 8% / T2 12% / T3 18% 是否最优）
- 验证 trailing stop 的 15%/25% 阈值
- 在 P1 短窗中验证 DECELERATING_INFLOW 软扣分是否应该存在
- 验证 RISK_OFF 降级规则是否减少了亏损

---

## 6. 参数扫描能力

回测系统应支持对关键阈值做网格扫描，找到最优参数：

| 参数 | 当前默认值 | 扫描范围建议 |
|---|---|---|
| 硬止损 T1（A股） | 8% | 5% ~ 12%，步长 1% |
| 硬止损 T3（A股） | 18% | 14% ~ 25%，步长 2% |
| 止盈 T1 | 20% | 15% ~ 30%，步长 5% |
| Trailing stop trim | 15% | 10% ~ 20%，步长 2.5% |
| BOLL squeeze 阈值 | — | 4% ~ 10%，步长 1% |
| BOLL squeeze 低分位窗口 | — | 60 / 120 / 252 日 |
| MA 趋势过滤 | — | MA20 / MA60 / MA120 |
| RISK_OFF 过滤 | — | 不过滤 / 降仓 / 禁止开仓 |

最优标准不能只看全样本最佳值。参数扫描必须输出：

- 全样本收益回撤比 / Sharpe / 胜率 / 最大回撤
- 按年份分组表现
- train/test 或 walk-forward 表现
- 信号数 N，N 太小时不得给“最优”结论，只能标注“样本不足”

---

## 7. 实现路径

### Phase 1：SQLite 数据仓 + P0 单标的技术回测（优先，约 5-7 小时）

- [ ] **F1: SQLite schema** — 新增 `stock-backtest-lab/scripts/backtest_core/store.py`
  - 建库路径：`~/.cache/stock-backtest-lab/history.sqlite`
  - 创建 `instruments` / `daily_bars` / `index_bars` / `sync_state`
  - 提供内部能力：写入日线、读取指定区间、检查本地数据覆盖范围
- [ ] **F2: 历史 K 线同步器** — 新增 `stockbt sync`
  - 复用 `stock_core/kline.py::fetch_daily_kline`
  - 默认 `--from 2020-01-01`
  - 同步后写 SQLite，不在回测时在线拉取
- [ ] **F3: 指数 K 线同步器** — 新增 `stockbt sync-index`
  - 扩展 `market_regime.py::fetch_index_daily` 支持足够 count
  - 默认同步沪深300、创业板指、恒生指数、恒生科技指数
- [ ] **F4: Snapshot Store** — 给定 T 从 SQLite 读取 T 及之前数据，构造 `BacktestSnapshot`
- [ ] **F5: P0 Signal Calculator** — 只计算价格 / 技术 / 大盘信号
  - price_regime
  - BOLL / MA / RSI / 量价
  - market_regime
- [ ] **F6: Forward Tracker** — 给定信号日期和入场规则，计算后续 5/10/20/60 日收益、最大回撤、止损/止盈触发
- [ ] **F7: 美的 BOLL squeeze 完整回测** — 用新系统重跑今天的手工回测，验证“挤压日入场 vs 突破确认入场”

### Phase 2：参数扫描 + 报告（P0 完整闭环，约 3-5 小时）

- [ ] **F8: 参数扫描器** — 对 BOLL 阈值、MA 过滤、止损/止盈/trailing stop 做网格扫描
- [ ] **F9: 报告生成** — 输出 Markdown / JSON 报告，含收益、胜率、最大回撤、按年份分组、按大盘 regime 分组
- [ ] **F10: 单标的 CLI 闭环** — `stockbt run SZ000333 --strategy boll_squeeze_entry --from 2020-01-01`
- [ ] **F11: 数据覆盖检查** — `stockbt run` 启动前自动检查 SQLite 是否覆盖区间；缺数据时提示具体 sync 命令

### Phase 3：资金流短窗验证（P1，约 3-4 小时）

- [ ] **F12: 资金流落库** — 新增 `fund_flow_daily` 同步，明确只覆盖最近约 120 个交易日 + 未来沉淀
- [ ] **F13: fund_flow Signal Calculator** — 从 SQLite 资金流行复用 `summarize_fund_flow` / `cross_validate`
- [ ] **F14: 技术信号 + 资金流增强对照** — 比较“纯技术”与“技术 + 资金确认”的短窗表现
- [ ] **F15: SPC 资金流规则短窗校准** — 验证 DECELERATING_INFLOW、PERSISTENT_OUTFLOW 等软扣分是否有统计依据

### Phase 4：多标的与 SPC 集成（P2，后置）

- [ ] **F16: 多标的批量回测** — 只允许使用明确日期定义的股票池，不能默认使用当前 screen_top600
- [ ] **F17: 公告 PIT 能力** — 补历史区间查询、分页、发布时间戳处理后再纳入复合策略
- [ ] **F18: SPC 参数建议报告** — 回测结果只生成建议，不自动改 `decision.py` 的 confidence 和阈值

---

## 8. CLI 接口设计

P0 只暴露最小闭环命令：

```bash
# 初始化 / 增量同步历史 K 线
$STOCKBT sync SZ000333 --from 2020-01-01

# 同步指数 K 线（大盘 regime 分层用）
$STOCKBT sync-index --from 2020-01-01

# 单标的单策略回测
$STOCKBT run SZ000333 \
  --strategy boll_squeeze_entry \
  --from 2020-01-01 \
  --to 2026-05-31 \
  --hold-days 5,10,20,60

# 多策略对比
$STOCKBT run SZ000333 \
  --strategy boll_squeeze_entry,ma_breakout,price_new_high \
  --from 2020-01-01

# 参数扫描
$STOCKBT scan SZ000333 \
  --strategy boll_squeeze_entry \
  --scan-param squeeze_threshold --from 4 --to 10 --step 1
```

P1 / P2 后续再扩展：

```bash
# 资金流短窗验证
$STOCKBT sync-flow SZ000333
$STOCKBT run SZ000333 \
  --strategy fund_confirmed_breakout \
  --from auto \
  --require-fund-flow

# 多标的批量回测，必须指定日期定义的股票池
$STOCKBT batch \
  --strategy boll_squeeze_entry \
  --pool-file data/pools/a_mid_large_2020_2026.csv \
  --from 2020-01-01
```

输出示例：

```
# SZ000333 美的集团 — boll_squeeze_entry 策略回测 (2020-01-01 ~ 2026-05-31)

数据覆盖:
  daily_bars: 2020-01-02 ~ 2026-05-31, 1558 根
  index_bars: OK

总信号: 24 次
胜率: 58.3% (14/24)
平均收益: 5d +0.6% / 10d +1.4% / 20d +2.8% / 60d +6.5%
最大回撤: -14.1%
收益回撤比: 0.46

按大盘分组:
  RISK_ON (9次):   胜率 66.7%, 60d +10.2%
  NEUTRAL (12次):  胜率 58.3%, 60d +4.1%
  RISK_OFF (3次):  胜率 33.3%, 60d -3.5%

参数敏感性:
  squeeze_threshold=4%: 信号 8,  60d +8.9%, max_dd -10.2%
  squeeze_threshold=6%: 信号 24, 60d +6.5%, max_dd -14.1%
  squeeze_threshold=8%: 信号 41, 60d +3.2%, max_dd -18.6%
```

---

## 9. 关键设计决策

### 9.1 K 线数据口径

P0 回测以本地 SQLite 中的日线 OHLCV 为唯一事实来源。当前 `fetch_daily_kline` 使用前复权（`qfq`）口径，适合做技术形态和相对收益验证，但严格交易回测还需要注意：

- 前复权价格可能随未来分红送转调整，存在“价格序列回写”的问题
- P0 报告必须标注 `adjustment=qfq`
- 如果后续要模拟真实成交价，应增加不复权 / 后复权口径对照

第一版先接受 `qfq`，因为目标是验证技术规则相对有效性，不是精确复盘每一笔真实成交。

### 9.2 公告关键词表

使用**固定的、预先定义的**正向/风险关键词表。在回测中不随日期变化。

正向关键词（约 30 个）：`回购, 增持, 分红, 业绩预增, 增长, 突破, 中标, 获得订单, 新产品, 获批, 上市, 产能释放, 扩产, 合作, 战略合作, 投资, 专利, 认证, 通过, 注册, 员工持股, 股权激励, 行权, 科创板, 转板, 调入, 纳入, MSCI, 富时罗素, 标准普尔`

风险关键词（约 20 个）：`立案, 调查, 警示函, 监管函, 处罚, 诉讼, 减持, 预减, 预亏, 退市, ST, *ST, 破产, 重组失败, 终止上市, 重大资产重组终止, 业绩修正向下, 商誉减值, 计提减值, 暂停上市`

公告暂不进入 P0；等 P2 补齐历史区间查询和时间戳处理后再启用。

### 9.3 回测时间窗口

- P0 默认窗口：`2020-01-01 ~ 最新`
- 个股 K 线：通过 `fetch_daily_kline(count=1500+)` 初始化后落 SQLite；如果 1500 根不足以覆盖 2020，则 sync 层需要增大 count 或分段拉取
- 指数 K 线：现有 `fetch_index_daily(count=400)` 不够覆盖 2020，必须扩展 count
- 资金流：只覆盖最近约 120 个交易日；P1 自动把 `--from auto` 解析为本地资金流最早日期
- 公告：P2 再处理，不影响 P0 时间窗口

### 9.4 样本量问题

某些策略（如 BOLL squeeze 极低分位、严格 MA 多头排列、或 P1/P2 复合过滤条件）在单只股票上可能只产生少量信号。**单股回测的统计显著性是固有的局限。** 解决方案：
- P0 先输出单标的结果，但必须按年份和大盘 regime 分层展示，避免被少数年份误导
- P2 多标的回测必须使用明确日期定义的股票池，不能用当前 screen 结果倒灌历史
- 报告中明确标注"信号数=N，统计显著性有限"

P0 技术策略的信号数通常多于复合策略，但仍必须在报告中标注 N。任何 N < 20 的参数组合不得输出“最优”，只能输出“样本不足，供观察”。

### 9.5 执行成本

回测中不模拟滑点和手续费。原因：对于 5-60 日的持仓周期，滑点/手续费对收益的影响 < 0.5%，远小于信号质量的不确定性。

### 9.6 数据存储成本

SQLite 足够支撑第一版：

- 单只股票 2020 至今约 1500 行，1000 只股票约 150 万行，SQLite 可轻松承载
- 日线回测读多写少，索引 `(symbol, trade_date)` 足够
- 后续如果扩展到分钟线，再考虑 DuckDB / Parquet；日线阶段不需要引入更重的数据栈

---

## 10. 文件结构规划

```
self_skills/
├── stock-backtest-lab/              # 独立 skill：回测系统真身
│   ├── SKILL.md
│   ├── bin/
│   │   └── stockbt                  # 回测 CLI 主入口
│   ├── scripts/
│   │   └── backtest_core/
│   │       ├── __init__.py
│   │       ├── cli.py               # 解析 stockbt sync/run/scan 命令
│   │       ├── store.py             # SQLite 表结构、写入、读取、覆盖检查
│   │       ├── sync.py              # 历史 K 线 / 指数 / 资金流同步
│   │       ├── snapshot.py          # 从 SQLite 构造回测快照
│   │       ├── signals.py           # 价格 / 技术 / 大盘信号计算
│   │       ├── strategies.py        # BOLL / MA / 新高等策略定义
│   │       ├── tracker.py           # 前向收益、回撤、止损止盈追踪
│   │       ├── stats.py             # 胜率、收益、Sharpe、分组统计
│   │       ├── scanner.py           # 参数扫描
│   │       └── report.py            # Markdown / JSON 报告
│   ├── tests/
│   │   └── test_*.py
│   └── references/
│       └── sqlite_schema.md         # 表结构和数据口径说明
├── shared/stock_core/
│   ├── kline.py                     # 复用：个股 K 线抓取
│   ├── market_regime.py             # 复用：指数 K 线 / 大盘 regime
│   └── fund_flow.py                 # P1 复用：资金流抓取与摘要
├── stock-market-hub/
│   └── bin/
│       └── smh                      # 可选：未来加 `smh backtest` 薄封装，不放核心实现
└── todolist/
    └── 2026-06-01_point_in_time_backtesting_system.md   # 本文档
```

不建议把回测核心放进 `shared/stock_core/backtest/` 或 `stock-market-hub/` 里。原因：

- 回测已经有独立数据仓、策略引擎、报告、参数扫描和 CLI，边界接近一个完整 skill
- `stock-market-hub` 主要负责“当前市场/个股分析”，回测负责“历史验证”，生命周期不同
- `shared/stock_core` 应继续保持为基础数据能力层，不承载业务级回测编排
- 后续如果希望入口统一，可以在 `smh` 中加薄封装调用 `stockbt`，但核心代码仍归 `stock-backtest-lab`

按照本工作区 skill 布局约定，部署时应建立软链接：

```bash
ln -s /Users/dp/Documents/local2/self_skills/stock-backtest-lab ~/.cursor/skills/stock-backtest-lab
```

本地产物：

```
~/.cache/stock-backtest-lab/
└── history.sqlite                   # 本地历史数据仓，不入 git
```

---

## 11. 与现有系统的关系

| 现有组件 | 回测系统角色 |
|---|---|
| `shared/stock_core/kline.py` | P0 同步个股日线 K 线 |
| `shared/stock_core/market_regime.py` | P0 同步指数 K 线，并复用大盘 regime 判定思路 |
| `shared/stock_core/fund_flow.py` | P1 复用 `fetch_daily_fund_flow` / `summarize_fund_flow` / `cross_validate`，但只做短窗验证 |
| `shared/stock_core/company_analysis.py` | 可参考 price_regime / 公告关键词逻辑，但 P0 不直接依赖 company 全量分析 |
| `spc_core/decision.py` | P2 才作为复合规则的被验证对象；P0 先验证可独立定义的技术 / 价格规则 |
| `spc_core/ledger.py` | 持仓侧回测需要用 `position_peak` 的概念 |
| `stock-backtest-lab/bin/stockbt` | 回测主入口：P0 暴露 `sync` / `sync-index` / `run` / `scan`；P1+ 再加 `sync-flow` / `batch` |
| `stock-market-hub/bin/smh` | 可选薄封装：未来可转调 `stockbt`，但不承载核心实现 |

**核心原则**：回测系统**复用**现有数据管线，但把在线数据先同步到 SQLite；回测阶段只读本地数据。第一版不修改 SPC 决策代码，不自动校准 confidence，只输出证据和建议。

---

_文档维护：Phase 1 实现过程中补充具体 API 调用细节_
_关联文档：`2026-06-01_focus_trading_rule_and_auto_verification.md`（前向验证，与回测互补）_

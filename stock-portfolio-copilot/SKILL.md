---
name: stock-portfolio-copilot
description: >-
  A股 / 港股 持仓与交易决策助手：记录初始持仓、买卖成交、自选股、资金上限，
  重算摊薄成本与盈亏，结合现有 stock-market-hub 的行情/公告/公司分析结果，
  对持仓与自选股给出当前操作建议。当用户提到持仓、成本价、成交记录、同步持仓、
  自选股、资金上限、仓位分析、是否加仓减仓卖出、交易日志 时使用。
---

# Stock Portfolio Copilot

面向个人账户管理的股票持仓 skill。

## 适用场景

- 初始化持仓：A 股 / 港股代码、数量、成本价
- 记录成交：买入 / 卖出、时间、价格、数量
- 同步持仓：根据初始持仓 + 交易流水重算当前仓位、均价、盈亏
- 管理自选：新增 / 删除 / 查看关注标的
- 设置约束：总资金上限、单票仓位上限
  - `单票仓位上限` / `max-single-pct` 的口径是：**单只股票或 ETF 的持仓市值，占账户总资金上限（`capital.total_cny`）的百分比上限**
  - 它**不是**“占当前持仓市值的百分比上限”；例如总资金上限 100 万、`max-single-pct=30`，表示单票目标上限是 30 万市值
- 实时分析：结合 `stock-market-hub` 的分析结果，输出 `buy / focus / add / hold / trim / sell / probe / avoid / watch`
  - `focus` 表示重点关注：宽松信号较好，适合加入盯盘清单，但不等同于直接买入
  - `buy` 表示买入候选：可由两条独立路径触发（详见下文「buy 候选双路径」），任一满足即可
  - `add` 表示**持仓加仓候选**：仅在已持仓 + 未亏损 + 仓位距上限仍有 ≥ 15% 空间 + 趋势/反转 buy 条件依然成立时触发，置信度低于自选 `buy`
  - `trim` 表示减仓：触发来源包括"风险公告/破位"、"主力持续流出"、"仓位超限"、"分级止盈"、"trailing stop"、"execution_plan 止盈价"、"P0a T1/T2 分级硬止损"
  - `sell` 表示卖出：触发来源包括"硬止损 T3 硬底线"、"L4 风险公告直接卖出"、"execution_plan 止损价"、"分级止盈 + 顶部信号"、"trailing stop 重度回撤"等
  - `probe` 仅港股 RISK_OFF + 反转买入路径下出现，是首仓概念，不适用于已持仓的加仓

## CLI 入口

skill 在多台机器上部署时，请按下面任一方式定位 `spc` 入口（**不要写死绝对路径**）：

```bash
# 方式 A：通过 Cursor / Claude skills 软链入口（推荐）
SPC=$(python3 -c "import os; print(os.path.realpath(os.path.expanduser('~/.cursor/skills/stock-portfolio-copilot/bin/spc')))")
# Claude 用户把 ~/.cursor 换成 ~/.claude 即可

# 方式 B：已经 cd 到仓库根目录
SPC=./stock-portfolio-copilot/bin/spc

# 方式 C：用环境变量指向仓库根
export SELF_SKILLS_HOME=/path/to/self_skills
SPC="$SELF_SKILLS_HOME/stock-portfolio-copilot/bin/spc"
```

`bin/spc` 内部已经做了"先在父目录找 .venv，找不到再解析软链回真身找 .venv"的兜底，所以同一台机器上软链路径或真身路径调用结果一致。

## ⚠️ position init vs trade add：红线纪律

**这是最容易被 agent 用错的命令对，错一次就破坏整个账户的盈亏历史。** 必须严格区分：

| 场景 | 正确命令 | 备注 |
|---|---|---|
| 首次建仓（db 完全没记录过该标的）| `position init` | 一次性，未来不应再 init |
| **任何买入、卖出、加仓、减仓、止损、清仓** | **`trade add --side buy/sell`** | 走 trade_ledger，自动算成本和已实现盈亏 |
| 残股摊薄成本调整（多次止损后剩余的小仓位，按"残股法"重新核算）| `position init --force` | 仅当**已无 trade 记录**时，且 note 必须写明原因 |
| 已有 trade 但想重置 seed | **不允许**，需先 `trade delete` 全部清空 | 破坏性操作，避免 |

代码层面已经加了双重护栏（`spc_core/ledger.py:add_position_seed`）：

1. 该标的 **已有 trade 记录** → `position init` 直接拒绝（`--force` 也救不了）
2. 该标的 **已有 seed 但无 trade** → 默认拒绝，必须 `--force` 才能覆盖

⚠️ **agent 务必牢记**：当用户说「卖出/买入/止损/加仓某只股票」时，无论是否要更新持仓数量，**唯一正确的命令是 `trade add`**，**永远不要走 `position init` 的快捷路径**。后者只用于：
- 工具初次启用时录入历史持仓基线
- 残股法成本核算（罕见，且必须配 `--force --note`）

错误的副作用：trade_ledger 留空 → `report pnl` 已实现盈亏归零 → 历史无法复盘 → 数据不可逆。

## 常用命令

下面示例都假设你已经按上文设好了 `$SPC` 变量。

```bash
$SPC position init --account default --market a --code 300750 --qty 1000 --cost 245.30
$SPC trade add --account default --market hk --code 01810 --side buy --qty 500 --price 19.10 --time "2026-05-08 10:32:00"
$SPC trade add --account default --market a --code 600584 --side sell --qty 300 --price 54.58 --time "2026-05-14 14:55:02"
$SPC portfolio sync --account default
$SPC portfolio show --account default
$SPC portfolio check --account default   # 检查 seed + trade + snapshot 一致性（含残股识别）
$SPC watch add --account default --market hk --code 01810
$SPC capital set --account default --total 500000 --max-single-pct 20
$SPC analyze now --account default --scope holdings
$SPC report pnl --account default
```

其中：

- `--total 500000` 表示账户总资金上限为 50 万 CNY
- `--max-single-pct 20` 表示**单票持仓市值上限 = 总资金上限的 20%**，即 10 万 CNY
- 不表示“单票最多占当前已持仓市值的 20%”

> 注意：v2 引入了多账户结构，几乎所有命令都需要 `--account <slug>`。首次使用先 `$SPC account create --slug default --name "默认账户" --set-default`，旧版数据库会自动迁移到 `default` 账户。

## 执行计划 / 成交复盘

当用户要把一次分析结论落成交易预案、把真实成交关联到预案、或复盘某次执行时，使用 `exec` 子命令。不要把复盘文字塞进 `trade add --note` 里替代结构化记录。

常用流程：

```bash
# 1. 建执行计划。至少要传 target-qty / target-cash-cny / target-position-pct 之一
$SPC exec plan create --account default \
  --market a --code 300750 --side buy --action-type open \
  --thesis "趋势强，回踩不破则建仓" \
  --target-qty 100 --price-limit-low 248 --price-limit-high 255 \
  --stop-loss-price 238 --tags "trend,core"

# 2. 真实成交时直接关联 plan（推荐）
$SPC trade add --account default \
  --market a --code 300750 --side buy --qty 100 --price 251.20 \
  --time "2026-05-08 10:32:00" --plan-id 1

# 3. 或把已有成交补关联到 plan
$SPC exec attach --account default --plan-id 1 --trade-id 12

# 4. 查看 / 列表
$SPC exec plan show --account default --id 1
$SPC exec plan list --account default --status planned

# 5. 更新未成交计划；已有成交后只能改止损/止盈/条件/标签/备注等事后参考字段
$SPC exec plan update --account default --id 1 --stop-loss-price 240 --note "止损随结构上移"

# 6. 条件失效时取消或过期
$SPC exec plan cancel --account default --id 1 --reason "跌破 invalidation"
$SPC exec plan cancel --account default --id 1 --expire --reason "时间窗口结束未触发"

# 7. 复盘不改变计划生命周期状态，只写 execution_review
$SPC exec review add --account default --plan-id 1 --trade-id 12 \
  --horizon five_day --outcome win --discipline-score 4 --execution-score 4 \
  --thesis-score 4 --plan-followed yes --lesson "按计划成交，未追高"
```

状态规则：

- 新 plan 只能显式创建为 `planned` / `cancelled` / `expired`；`partially_filled` / `filled` 必须由成交关联自动推导。
- 删除成交会回算关联计划状态：成交清零回到 `planned`，部分成交为 `partially_filled`，达到 `target_qty` 为 `filled`。
- `cancelled` / `expired` 是终态，不能再 attach 成交；需要重新规划时新建一条 plan。
- `exec review add` 只记录复盘，不再把 plan 改成 `reviewed`，避免复盘状态覆盖执行生命周期。

## buy 候选双路径

自选侧 `buy` 候选**不再唯一要求"创新高"**，而是两条独立路径**任一满足**即可被触发；
两条路径在 reasoning 里会显式标注为 `【趋势跟随路径】` 或 `【反转买入路径】`，
让人 / LLM 一眼区分两种风险性质。

| 维度 | 趋势跟随路径（trend） | 反转买入路径（reversal） |
|---|---|---|
| 价格 regime | 必须 ∈ `{NEW_YTD_HIGH, NEW_52W_HIGH, NEW_ALL_TIME_HIGH}`（创新高） | 必须 ∈ `{NEAR_YTD_LOW, IN_RANGE, NEAR_YTD_HIGH}`（非破位非新高） |
| 公告正向 | ≥ 1 条 | ≥ 2 条（更严） |
| 公告风险 | 0 条 | 0 条 |
| 主力资金 regime | ≠ `PERSISTENT_OUTFLOW`（缺数据视为 OK） | ≠ `PERSISTENT_OUTFLOW`，且**必须**有数据 |
| 主力资金方向 | 近 3/5 日累计 ≥ 0（None 视为 OK） | 必须有"掉头"硬证据：`reversal == OUTFLOW_TO_INFLOW` 或 `regime == PERSISTENT_INFLOW`，且**近 3/5 日累计 > 0** |
| 当日涨幅 | 不过热（< 8%） | 不过热（< 8%） |
| 默认 confidence | 0.72 | 0.68（左侧反转风险更高，置信度低于趋势型） |
| LOW_REGIMES（创新低） | ❌ 不允许 | ❌ 不允许（**永远禁止**，再强反转信号也压不过破位） |

**为什么要两条路径**：
- 单一"必须创新高"会把"接近年内低位 + 资金已掉头 + 多重正向公告"这种典型反转好买点
  永远卡在 focus，无法升档为 buy
- 但反转路径对资金证据要求更严（必须有"已经掉头"证据，且近 3/5 日累计**严格 > 0**），
  避免变成"系统主动建议抄底"
- 大盘 RISK_OFF 时**两条路径都自动降级为 focus**（仅 reasoning 里说明降级原因），与现有大盘联动一致

**输出审计**：决策的 `sources` 末尾会带上：
- `market_regime=...`
- `fund_flow.regime=... (1d=..., 3d=..., 5d=..., 10d=..., 20d=...)`
- `fund_flow.cross_validation=<verdict> (reversal_confirmed=..., short_long_conflict=...)`

任何 buy / focus 升档都能反查到具体路径、四周期数据与多周期交叉验证结论；`spc explain` 也会展开 `confidence_trace`。

### 主力资金多周期交叉验证（硬约束）

> **下沉提示**：判定算法已经在 `shared/stock_core/fund_flow.py::cross_validate` 实现，
> `summarize_fund_flow` 会自动把结论挂在 `fund_flow.cross_validation` 字段。
> 决策树（本 skill）+ 个股报告（stock-market-hub）+ LLM prompt（agent-constraints.md）
> **统一引用同一份字段**，不要再各自手算 1d/5d/10d/20d 方向 / 加速 / 共振。

代码层硬门控（`spc_core/decision.py`）：

1. **reversal 必须被短期数据背书**：`fund_flow.reversal == OUTFLOW_TO_INFLOW` 时，
   `cross_validation.reversal_confirmed` 必须为 True（即 1d ≥ 0 且 5d > 0）
   才允许走反转路径；False 时反转路径直接被否决，落回 focus。
   决策代码：`_is_reversal_buy_candidate` 中的 `if ff_reversal == FUND_REVERSAL_UP and cross.reversal_confirmed is False: return False`
2. **趋势路径动能减弱软扣分**：`cross_validation.acceleration == decelerating_inflow` 时，
   trend buy confidence 由 0.72 降到 0.67，并在 reasons 中标注 `decelerating_inflow`。
   决策代码：`_evaluate_self_select_buy` 中的 trend 分支。

LLM 在 prompt 里只需引用 `cross_validation` 字段做结论（短长冲突 / 共振 / 加速），
完整字段语义见 `stock-market-hub/references/agent-constraints.md §3 第 6 条`。

**违规**：仅引用 `regime` 标签或仅检查 `3/5d 累计 ≥ 0` 而不复述 `cross_validation`
字段的结论 → 视为 `stock-market-hub/references/agent-constraints.md §3 第 6 条` 违规。

**特殊降档：港股 RISK_OFF 下的 `probe`（试探买入）**

| 触发条件 | 输出 action | 默认 confidence | 仓位建议 |
|---|---|---|---|
| 港股 + 大盘 RISK_OFF + 反转买入路径全部条件满足 | `probe` | 0.60 | 常规仓位的 1/4-1/3 建首仓，确认修复后再加第二笔 |
| A 股 + 大盘 RISK_OFF + 反转买入路径全部条件满足 | `focus` | 0.62 | 不入场，等大盘修复 |
| 任意市场 + 大盘 RISK_OFF + 趋势追高路径 | `focus` | 0.65 | 不追高，等大盘修复 |

> `probe` 通道**只对港股开放**（A 股 / 其它市场永远走 focus），原因：港股流动性 / 估值结构和 A 股不同，弱市里完全不让进场会错过左侧反转修复型机会；但 A 股 RISK_OFF 区间普遍有更明显的"集中下跌"特征，宁可保守。
>
> `probe` 仓位的**后续生命周期不由本 skill 自动管理**（持仓侧 `_decide_for_holding` 不感知 entry_kind）。一旦 probe 入场后，加仓 / 止损 / 转正常仓位由人或 LLM 决定。建议把 probe 仓位的初始计划（止损价、加仓条件）记录在交易日志里。
>
> 注意 `confidence` 与"动作激进度"并不正相关：probe（0.60）激进度高于 focus（0.62），但 confidence 反而更低——因为 confidence 反映"系统对该建议的把握"，弱市抢反转把握自然更低。

参考实现位置：`spc_core/decision.py` 的 `_is_trend_buy_candidate` / `_is_reversal_buy_candidate` / `_evaluate_self_select_buy`。


## 持仓侧风控（止损 / 止盈 / 加仓 / trailing stop）

`spc analyze now` 在持仓侧（``qty > 0``）按以下优先级评估，每只标的最终汇成 `hold / add / trim / sell` 之一。所有阈值都有"代码默认值 + account_settings 覆盖"两层；不配置直接生效，按账户配置就按账户来。

### 决策优先级（持仓侧）

| # | 规则 | 触发条件 | 输出 | 默认置信度 | 备注 |
|---|---|---|---|---|---|
| 1 | 风险/破位 | `risk_hits ≥ 2` **或** `regime ∈ LOW_REGIMES` | trim | 0.72 | 旧规则 |
| 2 | 主力资金弱 + 短期续出 | `ff_regime=PERSISTENT_OUTFLOW` + `5d<0` （+ `3d<0` / `regime ∈ LOW`） | trim / sell | 0.65 / 0.78 | 旧规则 |
| 3 | L4 风险公告规则 | 命中 ≥ 1 条风险公告 → 跨过分档 trim 直接 sell | sell | 0.80 |
| **4** | **P0a 分级硬止损** | 浮亏命中 T1/T2/T3 三档之一；confidence 按大盘 regime 软联动 | **trim** (T1/T2) / **sell** (T3) | **见下表** | 新规则 |
| **5** | **P2a 预案止损/止盈** | active `execution_plan` 的 `stop_loss_price` / `take_profit_price` 被现价穿越 | **sell** / **trim** | **0.82 / 0.78** | 新规则 |
| 6 | 仓位超限 + 浮盈 | `weight_pct > max_single_pct` 且现价 > 成本 | trim | 0.70 | 旧规则 |
| **7** | **P0b 分级止盈** | 浮盈达 `tp_t1 / t2 / t3`；叠加顶部 (`HIGH_REGIMES + PERSISTENT_OUTFLOW`) 或资金反转 (`INFLOW_TO_OUTFLOW`) 升级 sell | **trim** / **sell** | **0.65 / 0.75 / 0.78**；叠加升 **0.80 / 0.82** | 新规则 |
| **8** | **P2b trailing stop** | 现价从持仓期间最高价回撤 ≥ `trail_pct`（重度 ≥ `trail_severe_pct`） | **trim** / **sell** | **0.72 / 0.78** | 新规则 |
| **9** | **P1a 加仓** | 上面都未触发 + 权重 < `max_single_pct × add_headroom` + 现价 ≥ 成本 + 自选 trend/reversal buy 条件成立 + 大盘非 RISK_OFF | **add** | **trend 0.68 / reversal 0.64** | 新规则 |
| **10** | **P1b cross_validation 软提示** | `cross.acceleration ∈ {decelerating_inflow, accelerating_outflow}` 或 `reversal_confirmed=False` | 不改 action，加 reasons | — | 新规则 |
| **11** | **LLM 复核（旁路，默认 OFF）** | 主决策落在 `add`/`trim`/`sell`/`buy`/`probe` 时被列入"建议复核清单"；用户可直接让 agent 复核，或用 `--llm-review` 显式触发 | 不改主 action，附 `decision.llm_review.{verdict,concerns,execution_hint}` | — | 新规则；fail-open；详见下方"LLM 复核（L1 旁路）" 章节 |

### P0a 分级硬止损（三档 × 三市场 × 大盘 regime 联动）

设计动机：单一硬阈值（A 股 10% 一刀切 sell）容易在"跌停后回踩"被误触发，把仓位切在地板上；而真正"已经深套、不再幻想"的硬底线应当比 10% 更深。本档把硬止损拆成三道防线，并按市场波动率分别配置：

**阈值表**（默认值；可被 `account_settings` 覆盖）

| 档位 | A 股 | 港股 | ETF | 默认动作 | 含义 |
|---|---|---|---|---|---|
| **T1**（首道防线） | 8% | 12% | 10% | trim | 减半锁损，给标的修复机会 |
| **T2**（深防线） | 12% | 18% | 15% | trim | 再减一半，最多留 25% 仓位 |
| **T3**（硬底线） | 18% | 25% | 22% | sell | 强制全退，不再幻想反弹 |

**confidence 按大盘 regime 联动**（弱市下调，强市上调）

| 档位 | RISK_ON | NEUTRAL | RISK_OFF |
|---|---|---|---|
| T1 trim | 0.72 | 0.70 | 0.65 |
| T2 trim | 0.80 | 0.78 | 0.72 |
| T3 sell | 0.85 | 0.85 | 0.80 |

> RISK_OFF 时所有档 confidence 下调，因为弱市集中下跌往往是 beta 而非 alpha 问题，留更多人工判断空间；RISK_ON 时同样跌幅更可能是个股专属风险，confidence 反而上调。
>
> L4 规则（风险公告 → sell @ 0.80）：不做分级，不依赖跌幅。只要有风险公告就直接跨过 T1/T2/T3 卖出，

**设计原则**：

- 三档互斥（if/elif），从最严重的 T3 开始判断，保证一档对应一条 trace step
- `sell` 一旦触发，后续 T1/T2 trim 不会把它降级（用 `current()[0] != "sell"` 守卫）
- T3 出现时即便 L4 已经 sell @ 0.80，T3 仍会 record 至 0.85（更深跌幅，置信度更高）
- `trim` 之间用 `raise_to` 升档，避免大数被小数覆盖
- P2a 用 `record` 强制改 confidence，因为它是用户事前手填的硬预案
- ETF 持仓侧沿用同一套分档逻辑（参数走 `hard_stop_etf_*`），但**不开启 add 路径**（ETF 加仓更适合基于宏观/主题判断，而非系统单标的信号）

### P0a 分档幂等性（schema v5）

**问题背景**：T1/T2 触发的本意是"价格继续往下走时仓位继续减"，但纯静态条件 `loss_ratio >= t1` 会在"价格停在 T1 区间不动"时**每次 analyze 都触发减仓建议**——因为浮亏百分比与持仓数量无关，用户按建议减半后下次 analyze 仍然 -12%，又会建议再减半，陷入过度交易。

**解决方案**：在 `position_peak` 表（v5）增加 `last_trim_tier` / `last_trim_price` / `last_trim_time` 三列，记录已触发档位。决策逻辑：

| 当前状态 | 价格穿越 T1 | 价格穿越 T2 | 价格穿越 T3 |
|---|---|---|---|
| `last_trim_tier=NULL` | trim + 写入 `T1` | trim + 写入 `T2` | sell |
| `last_trim_tier=T1` | **软提示**（不重发 trim） | trim 升档 + 写入 `T2` | sell |
| `last_trim_tier=T2` | **软提示** | **软提示** | sell |
| 任意，浮亏回升至 `< T1` | **清空** tier | — | — |

**关键设计点**：

- **T3 永不静默**：硬底线即便重复触发也是 sell，宁可重复也不能漏
- **允许升档**：T1 已记 + 跌到 T2 → 正常 trim 并写入 T2（不被"已记过 T1"挡住）
- **回升重置**：浮亏从 T1 区间反弹到 T1 阈值以下 → 自动清空 tier，让标的进入"修复后再跌"的新一轮分档流程
- **清仓自动清理**：`portfolio sync` 检测清仓后 DELETE 整条 `position_peak` 记录，间接清空 tier
- **信任原则**：系统假设"建议过 = 用户即将执行"。如果用户没真减仓，下次 analyze 仍会给软提示（reasoning 里），但不会重复发 trim action

**实现位置**：
- 写入：`spc_core/decision.py::_maintain_trim_tier_state`（由 `analyze_now` 在决策完成后调用）
- 读：`spc_core/ledger.py::get_position_peak` 返回字段 + `_extract_features` 接入 Features
- 触发：`_decide_for_holding` 和 `_decide_etf_for_holding` 在 T1/T2 分支检查 `last_trim_tier` 决定走 trim 还是软提示

### LLM 复核（L1 旁路）

**问题背景**：规则系统强在确定性 + 可审计性，弱在"看不见 features 之外的世界"——比如新出的研报修正、同板块兄弟标的强弱、公告里规则没抓到的语义信号。对 `add` / `trim` / `sell` / `buy` / `probe` 这五类"仓位有创口"的动作，可以叠加一层 LLM 复核补足这些维度。

**默认状态：OFF**（按需开启）。

由于单次复核需要 agent 调 stock-market-hub 拉数据 + 分析（~2-3 min），全开 30 个持仓太久，所以默认关闭。改成"普通 analyze 跑完后，末尾列出**建议复核标的清单**，由用户挑哪些标的值得二次确认"。

**复核后端**：

1. **prompt（默认）**——由**当前会话的 agent** 直接做复核。不再新开 subprocess / subagent，agent 利用当前 session 已有的 `stock-market-hub` skill 拉最新公告/资金流/新闻，给出独立复核结论。这是最稳定、最省时的路径（无 subprocess 启动开销，无超时风险）。
2. **codex**（gpt-5.4 + reasoning_effort=high）— 可选，通过 `--llm-backend codex` 走 subprocess 调用
3. **claude**（sonnet + effort=high）— 可选，通过 `--llm-backend claude` 走 subprocess 调用

**推荐工作流**：

```bash
# 1. 先跑普通 analyze（秒级），看规则系统的建议
$SPC analyze now --account public

# 末尾会看到类似：
# == LLM 复核建议（默认未开启）==
# 以下 3 个标的的建议涉及仓位变化，建议在执行前做人工复核：
# - HK 01810 小米集团-W：卖出（sell） @ 0.85
# - A 510300 沪深300ETF：加仓（add） @ 0.68
# - HK 00700 腾讯控股：试探买入（probe） @ 0.62
# 直接告诉 agent 你要复核哪个标的即可，例如：
#   "帮我对 小米集团-W（01810）做一下人工复核"

# 2. 在对话里直接让 agent 复核（不需要额外命令）
# agent 会调 stock-market-hub 拉最新数据，在当前 session 内给出复核结论

# 3. 也可以显式用 --llm-review（prompt 后端，效果同上）
$SPC analyze now --account public --market hk --code 01810 --llm-review

# 4. 如果想走 subprocess 后端（需要 codex/claude CLI 在 PATH 上）：
$SPC analyze now --account public --market hk --code 01810 --llm-review --llm-backend codex
```

**触发条件**：

| action | 是否在"建议复核清单"里 | 备注 |
|---|---|---|
| `add` / `trim` / `sell` / `buy` / `probe` | ✅ | 涉及仓位变化，值得二次确认 |
| `hold` / `watch` / `avoid` / `focus` | ❌ | 维持现状的动作不需要 LLM 二次验证 |

**Agent 复核规范**：

当 agent 收到复核请求时，应按以下流程操作：

1. 调用 `stock-market-hub` 的 `smh company` / `smh ann` / `smh flow` 获取标的的最新数据
2. 重点关注：近期公告语义（回购/减持/业绩预告）、资金流多周期交叉验证、大盘 regime 联动
3. 输出结构化复核结论：
   - **Verdict**: `confirm`（同意）/ `question`（有疑虑）/ `reject`（反对）
   - **Confidence**: 0.0-1.0（对复核结论的把握）
   - **Concerns**: 具体顾虑列表
   - **Execution Hint**: 如果执行，具体怎么做（仓位/时机/风控）

**输出契约**（复核结果挂 `decision.llm_review`）：

```json
{
  "verdict": "confirm | question | reject",
  "confidence": 0.0-1.0,
  "concerns": ["顾虑 1", ...],
  "missing_context": ["features 里没给到的关键数据"],
  "execution_hint": "如果按系统建议执行，具体怎么做"
}
```

**集成深度**（当前版本：L1 旁路）：
- LLM 复核结果挂在 `decision.llm_review` 字段下，**不改变 action / confidence**
- 渲染层把 verdict / concerns / execution_hint 显示给用户
- 用户自己判断要不要采纳 LLM 的二次意见（系统建议依然是 single source of truth）

**失败处理**（fail-open）：
- prompt 后端无网络/超时风险，agent 在同一个 session 内完成
- codex/claude subprocess 超时/错误 → `status: failed` + `error` 字段，主决策不变
- 单次 subprocess 超时默认 180s

**给复核的输入数据**（"给足数据"原则）：
- 标的基本信息（market / code / name / 持仓 / 现价 / 浮盈）
- 系统建议（action / confidence / reasoning / risks / sources）
- 完整的 confidence_trace（决策每一步演变）
- 大盘 regime（A 股 + 港股，含 reasons / 指数明细）
- 标的 features 摘要（quote / fund_flow / 公告头 10 条 / company info）

**CLI flag**：

```bash
# 默认（关闭 LLM 复核，规则系统建议 + 末尾给"建议复核清单"）
$SPC analyze now --account public

# 显式开启 LLM 复核（prompt 后端，当前 agent 直接复核）
$SPC analyze now --account public --market hk --code 01810 --llm-review

# 强制使用 codex 或 claude subprocess（需要对应 CLI 在 PATH 上）
$SPC analyze now --account public --market hk --code 01810 --llm-review --llm-backend codex
$SPC analyze now --account public --market hk --code 01810 --llm-review --llm-backend claude

# 调 subprocess 超时（默认 180s）
$SPC analyze now --account public --market hk --code 01810 --llm-review --llm-backend codex --llm-review-timeout 300
```

**环境变量**：
- `SPC_LLM_BACKEND=prompt|codex|claude|none`：强制后端选择（测试/CI 用 `none` 可绕开 LLM）

**输出渲染示例**（prompt 后端）：

```
🤖 LLM 复核请求：请当前 agent 基于以上系统建议与数据来源，
结合 stock-market-hub 拉取最新公告/资金流/新闻，
给出独立复核结论（verdict / confidence / concerns / execution_hint）。
```

**输出渲染示例**（codex/claude 后端，复核完成后）：

```
LLM 复核（codex，190.8s）：⚠️ question @ 0.78
  • 系统把 trim 主要建立在 -12% 机械止损上，但仓位仅占 6.28%，并非被动去杠杆场景
  • 管理层在当前价附近持续回购，2026-05-15 回购 325 万股 @ HK$30.62-30.82
  • 2026-05-26 即将披露 2026Q1 业绩，先观察更稳
  执行建议：若一定要先防守，建议在业绩前只减 25%-33%，而非 50%；
            业绩后若仍 RISK_OFF + 跌破 30.4 一带，再扩大减仓
```

**实现位置**：
- 核心模块：`spc_core/llm_review.py`（后端探测 / prompt 构造 / subprocess 调用 / JSON 解析）
- 集成点：`spc_core/decision.py::analyze_now`（在 results 完成 + opportunities 之后 attach 复核）
- 渲染：`spc_core/decision.py::render_analysis_text`
- CLI：`scripts/main.py::p_analyze_now` 增加 `--llm-review` / `--no-llm-review` / `--llm-backend` / `--llm-review-timeout`

**性能预算**：
- prompt 后端（agent 直接复核）：~2-3 min 单标的（含 stock-market-hub 数据拉取 + 分析），无 subprocess 开销
- codex subprocess：≈ 90-200s（含 web search + 启动）
- 30 个持仓全跑：prompt 后端约 60-90 min，建议仅 spot-check 关注标的

### 默认参数与 account_settings 覆盖

所有阈值都可通过 `account_settings` 按账户覆盖；缺配置时使用代码默认值：

| account_settings key | 默认值 | 用途 |
|---|---|---|
| `decision.hard_stop_pct.a_stock.t1` | `0.08` | A 股 T1 首道防线（默认 trim） |
| `decision.hard_stop_pct.a_stock.t2` | `0.12` | A 股 T2 深防线 |
| `decision.hard_stop_pct.a_stock.t3` | `0.18` | A 股 T3 硬底线（默认 sell） |
| `decision.hard_stop_pct.hk_stock.t1` | `0.12` | 港股 T1 首道防线 |
| `decision.hard_stop_pct.hk_stock.t2` | `0.18` | 港股 T2 深防线 |
| `decision.hard_stop_pct.hk_stock.t3` | `0.25` | 港股 T3 硬底线 |
| `decision.hard_stop_pct.etf.t1` | `0.10` | ETF T1 首道防线 |
| `decision.hard_stop_pct.etf.t2` | `0.15` | ETF T2 深防线 |
| `decision.hard_stop_pct.etf.t3` | `0.22` | ETF T3 硬底线 |
| `decision.take_profit.t1_pct` | `0.20` | 第一级止盈阈值（trim @ 0.65） |
| `decision.take_profit.t2_pct` | `0.50` | 第二级止盈阈值（trim @ 0.75，叠加顶部信号升 sell） |
| `decision.take_profit.t3_pct` | `1.00` | 第三级止盈阈值（trim @ 0.78，叠加升 sell） |
| `decision.add_position.weight_headroom_ratio` | `0.85` | 加仓时单票上限留 15% 缓冲 |
| `decision.trailing_stop.pct` | `0.15` | trailing trim 阈值（从持仓期间最高回撤 15%） |
| `decision.trailing_stop.severe_pct` | `0.25` | trailing sell 阈值（重度回撤 25%） |

> 旧的 `decision.hard_stop_pct.{a_stock|hk_stock|etf}`（无 `.t1/.t2/.t3` 后缀）单 key 已废弃，请改用新 key。

设置方式（CLI）：

```bash
# 例：把某账户的 A 股 T1 首道防线改成 6%（更早开始减仓）
$SPC config set --account default --key decision.hard_stop_pct.a_stock.t1 --value 0.06

# 也可以直接 SQL（同等效果）
# INSERT INTO account_settings VALUES (acct_id, 'decision.hard_stop_pct.a_stock.t1', '0.06', now())
```

> 如果当前 CLI 还没暴露 `config set`，可以临时用 Python 调 `spc_core.settings.set_account_setting`，或者直接写 SQL。

### trailing stop 的工作机制（P2b）

新增 `position_peak` 表（schema v4 引入）记录"当前持仓阶段"的最高价：

- 首次 `portfolio sync` + `qty > 0` 时初始化为 `max(avg_cost, last_price)`（避免刚买入时 peak 锚在低于成本的位置）
- 后续 sync 仅在 `current > peak` 时更新 peak（"只升不降"）
- 清仓（`qty == 0`）时 DELETE 整条 peak 记录，下次再建仓重新初始化
- 决策侧用 `(peak - current) / peak` 算 drawdown，超过阈值触发 trim / sell

**精度提示**：trailing stop 的准确度受 `portfolio sync` 频率约束。盘中如果想抓更精确的高点回撤，可以多跑几次 `spc analyze now`（内部已经会调 sync），或者直接 `spc portfolio sync`。

### execution_plan 与持仓决策的联动（P2a）

`spc exec plan create` 时填的 `--stop-loss-price` / `--take-profit-price` **不再只是事后参考**，而是直接参与持仓决策：

- `status ∈ {planned, partially_filled}` 的最近一条 plan 的价位会被加载进 features
- 现价跌破 `stop_loss_price` → **直接 sell @ 0.82**（优先级高于 P0a T3 硬底线 0.85 之外的所有档）
- 现价突破 `take_profit_price` → **trim @ 0.78**
- `cancelled / expired / filled` 的 plan 价位**不**生效

用法：用户在建仓时把 thesis + 退出条件一起写进 plan，后续 analyze 会自动按 plan 给信号。

```bash
# 建仓时一并写好风控价位
$SPC exec plan create --account default \
  --market a --code 300750 --side buy --action-type open \
  --thesis "突破回踩不破 248 则建仓" \
  --target-qty 100 \
  --price-limit-low 248 --price-limit-high 255 \
  --stop-loss-price 235 \
  --take-profit-price 320

# 之后每次 analyze 都会按这个止损 / 止盈价给信号
$SPC analyze now --account default --scope holdings
```


## 大盘风险偏好软联动

`spc analyze now` 在分析每只标的之前，会先按**目标涉及的市场**评估"大盘 regime"
（来自 `stock-market-hub` 的 `smh regime`，数据源：腾讯指数日 K）：

| regime | 含义 | A 股代表指数 | 港股代表指数 |
|---|---|---|---|
| `RISK_ON` | 全部代表指数距 52w 高 ≥ -3% **且**站上 200MA（年线） | 沪深300 + 创业板指 | 恒生 + 恒生科技 |
| `RISK_OFF` | 任一代表指数距 52w 高 ≤ -15% **且**跌破 200MA | 同上 | 同上 |
| `NEUTRAL` | 其它一切情形 | — | — |

**联动规则**（软联动，不强制改仓）：

1. **市场隔离**：A 股标的只看 A 股 regime，港股标的只看港股 regime（两者常常分化）
2. **RISK_OFF + 自选侧**：
   - A 股：本应严格触发 buy 候选的标的，自动降级为 focus，不进入"今日可买入清单"
   - 港股：趋势追高型 buy 同样降级为 focus；但**反转修复型 buy** 允许降档为 `probe`
     （试探买入），只能做常规仓位的 `1/4-1/3` 首仓，确认修复后再加第二笔
3. **RISK_OFF + 持仓侧**：hold **不会**自动降为 trim；只是给 risks 列表追加
   "大盘 RISK_OFF，宏观防御为主"提示，最终是否减仓由人或 LLM 决定
4. **RISK_OFF + 已 trim/sell**：confidence + 0.05（防御信号得到宏观背书）
5. **RISK_ON / NEUTRAL**：reasons 里出现一句备注，但**不会**主动加仓 / 升档
6. **缺数据**：完全不影响，与 `market_regime` 字段缺失等价

**输出位置**：`render_analysis_text` 顶部会拼一段「大盘风险偏好」总览，
列出 A 股 / 港股 各自的 regime + 代表指数读数（距 52w 高、YTD、年线状态），
让人和 LLM 都能先看到再判断个股。

`payload["market_regime"]` 字段保留每个市场的完整 regime 详情，
`decision.sources` 里也会追加一行 `market_regime=<regime>` 作为审计痕迹。


## 默认盘中分析时间表

当前默认采用“手动触发，但时间表固定”的方式，适用于同时关注 A 股和港股的普通交易日。

- `09:22`：盘前检查，更新隔夜公告与开盘预案
- `09:45`：开盘后第一轮，过滤掉最初几分钟噪音
- `11:00`：上午中后段，确认强弱是否延续
- `13:30`：午后第一轮，观察资金回流与方向选择
- `14:50`：A 股尾盘决策点
- `15:30`：港股尾盘复核点

使用原则：

- 普通交易日：按上面固定时间点手动触发
- 特殊交易日（财报日 / 重大公告日 / 计划重点交易日）：允许临时增加时间点
- 高频盯盘不是当前 skill 的目标；更适合 15-60 分钟级别决策，而不是 1-5 分钟抢反应

## 数据与配置

- 默认数据库：`~/.local/share/stock-portfolio-copilot/portfolio.db`
- 可通过环境变量 `SPC_DATA_DIR` 覆盖数据目录
- 港股汇率优先使用显式传入的 `fx_rate`；没有时使用配置或实时汇率 fallback
- 分析输出里的"单票上限 X%"默认都沿用同一口径：**相对账户总资金上限，不相对当前持仓市值**
- Schema 版本：当前 v4。升级路径 v1→v2（多账户）→ v3（execution_plan / review）→ v4（position_peak / trailing stop），全部自动迁移
- 持仓侧风控参数（止损 / 止盈 / 加仓 / trailing）见上一节「持仓侧风控」

## 输出约定

- 在任何面向用户的分析、汇总、建议、清单输出中，只要出现股票或 ETF 代码，必须同时给出对应名称。
- 推荐格式：`名称（代码）`，例如 `阿里巴巴-W（09988）`、`恒瑞医药（600276）`、`沪深300ETF（510300）`。
- 如果同一段里需要多次提到同一标的，首次出现时仍必须使用 `名称（代码）`；后续可在不引起歧义时简写为名称，但不要只输出纯代码。
- 只要输出里提到资金流描述，或基于资金流做判断（如流入/流出、资金转向、共振、加仓/减仓/买入理由等），必须补充这份资金流的**具体获取时间**与口径（如“今日盘中累计，截至 …”或“上一交易日完整资金流，截至 …”）。
- 如果当次数据源暂时无法解析名称，应明确说明”名称缺失”，不要只输出纯代码。
- **每手股数硬约束**：给出任何”买入 X 股”、”卖出 X 股”、”试探 N 股”等具体数量建议之前，**必须先确认该标的的每手股数**。
  - A 股固定 100 股/手，但 ETF/可转债可能不同
  - 港股每手股数不固定（常见 100/200/500/1000/2000 等），**必须从 `spc analyze now` 输出的”每手”行或 company info 的”最小交易单位”字段获取，不得猜测**
  - 建议的数量必须是每手股数的整数倍，且不能小于 1 手
  - **港股 1 手持仓的特殊约束**：当港股标的当前持仓恰好等于 1 手（`qty == lot_size`）时，"减半仓"、"减一部分"等部分减仓建议**不可执行**。此时只有两个有效选项：
    - 清仓全部（`qty` 股，恰好 1 手）
    - 不动（hold）
    - **绝对禁止**给出 `lot_size / N` 的碎股数量建议（如"减 1000 股"而每手 2000 股），这是数学上不可能成交的订单
  - 违反此约束会导致建议无法执行（港股拒绝零股订单）

## 依赖关系

本 skill 不复制 `stock-market-hub` 的分析代码，而是通过脚本桥接复用：

- `stock-market-hub/scripts/analyze_company.py`

因此推荐把两个 skill 放在同一个仓库里，并分别在客户端 skill 目录中做软链接。

## 共享模块演进约定

- 当前优先复用已经被两个 skill 同时依赖的能力；现阶段主要是 `company` 分析链路。
- `stock-market-hub` 中的 `news / sector / risk / ann / timeline / supply / pdf` 不要求一次性全部抽到共享层。
- 后续开发本 skill 时，如果新增功能明确需要调用 `stock-market-hub` 的某个能力，再把对应能力抽到 `shared/stock_core`。
- 抽取顺序遵循“先有真实复用，再做共享沉淀”，避免为了预防性重构把两个 skill 一起拖重。

## 决策辅助维度（v1.7+）

除了核心 5 维度（价格 regime / 资金流 / 公告 / 大盘 regime / 持仓状态）外，
现已加入 3 个**辅助维度**，由 `shared/stock_core/enrichment.py` 提供，
通过 `analyze_company` 自动抓取。这些维度**只贡献 reasons/risks 文案 + sources
展示，不直接改 action 触发条件**，目的是让人 / LLM 能看到更全面的归因信息。

| 维度 | Features 字段 | 命中阈值 | 输出示例 |
|---|---|---|---|
| **stock_news** | `news_related`, `news_important`, `news_top_titles` | 财联社电报最近 50 条点名命中 | reasons：「近期财联社命中 X 条相关电报」 |
| **sector_strength** | `sector_label`, `sector_diff_pct`, `sector_avg_pct` | leader/stronger/weaker/laggard | reasons/risks：「显著跑赢同板块（+3.2% vs 板块 -1.0%）」 |
| **xueqiu_attention** | `attention_followers`, `attention_level`, `attention_crowded` | hot ≥ 50 万 / very_hot ≥ 150 万 | risks：「散户关注度极高（雪球 155 万）+ 价格破位，警惕散户接飞刀」 |

**实战命中示例**（2026-05-15 验证）：
- A 000568 泸州老窖（**sell**）：散户 154.8 万 + 价格 NEW_ALL_TIME_LOW → 触发"散户接飞刀"风险
- A 600276 恒瑞医药（**avoid**）：散户 197.9 万 + 主力 20d -17.92 亿 → 触发"市场情绪密集"风险
- HK 01810 小米：散户 163.7 万 + RISK_OFF + NEAR_YTD_LOW → 触发"散户接飞刀"
- HK 09988 阿里：散户 55.3 万 + crowded → 触发"市场情绪密集"

**已知局限（v1）**：
- `stock_news` 池子只有 50 条（cls.cn 单源 + 不支持分页），命中率约 5-10%
- `sector_strength` 依赖 `analyze.peers`，A 股 peers 接口（东财 push2）经常被封，
  大多数 A 股 标的返回 `n/a`；港股本来就没 peers 数据
- 后续 v2 计划：cls + 新浪滚动 + 巨潮搜索融合扩大新闻池；sector_strength 自己拉同业不依赖 peers

详见 `shared/stock_core/enrichment.py` 和 `stock-market-hub/references/data_sources.md` §6.6。

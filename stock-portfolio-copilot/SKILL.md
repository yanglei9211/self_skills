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
- 实时分析：结合 `stock-market-hub` 的分析结果，输出 `buy / focus / add / hold / trim / sell / avoid / watch`
  - `focus` 表示重点关注：宽松信号较好，适合加入盯盘清单，但不等同于直接买入
  - `buy` 表示买入候选：可由两条独立路径触发（详见下文「buy 候选双路径」），任一满足即可

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

> 注意：v2 引入了多账户结构，几乎所有命令都需要 `--account <slug>`。首次使用先 `$SPC account create --slug default --name "默认账户" --set-default`，旧版数据库会自动迁移到 `default` 账户。

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

## 输出约定

- 在任何面向用户的分析、汇总、建议、清单输出中，只要出现股票代码，必须同时给出股票名称。
- 推荐格式：`股票名称（代码）`，例如 `阿里巴巴-W（09988）`、`恒瑞医药（600276）`。
- 如果当次数据源暂时无法解析名称，应明确说明“名称缺失”，不要只输出纯代码。

## 依赖关系

本 skill 不复制 `stock-market-hub` 的分析代码，而是通过脚本桥接复用：

- `stock-market-hub/scripts/analyze_company.py`

因此推荐把两个 skill 放在同一个仓库里，并分别在客户端 skill 目录中做软链接。

## 共享模块演进约定

- 当前优先复用已经被两个 skill 同时依赖的能力；现阶段主要是 `company` 分析链路。
- `stock-market-hub` 中的 `news / sector / risk / ann / timeline / supply / pdf` 不要求一次性全部抽到共享层。
- 后续开发本 skill 时，如果新增功能明确需要调用 `stock-market-hub` 的某个能力，再把对应能力抽到 `shared/stock_core`。
- 抽取顺序遵循“先有真实复用，再做共享沉淀”，避免为了预防性重构把两个 skill 一起拖重。

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

```bash
/Users/yanglei/Documents/project/self_skills/stock-portfolio-copilot/bin/spc
```

## 常用命令

```bash
spc position init --market a --code 300750 --qty 1000 --cost 245.30
spc trade add --market hk --code 01810 --side buy --qty 500 --price 19.10 --time "2026-05-08 10:32:00"
spc portfolio sync
spc portfolio show
spc watch add --market hk --code 01810
spc capital set --total 500000 --max-single-pct 20
spc analyze now --scope holdings
spc report pnl
```

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

**输出审计**：决策的 `sources` 末尾会带上 `market_regime=...` 与 `fund_flow.regime=... (1d=..., 3d=..., 5d=..., 20d=...)`，
任何 buy / focus 升档都能反查到具体路径与触发数据。

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
2. **RISK_OFF + 自选侧**：本应严格触发 buy 候选的标的，自动降级为 focus，
   不进入"今日可买入清单"，理由会写明"等大盘修复"
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

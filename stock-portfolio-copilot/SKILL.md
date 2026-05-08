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
- 实时分析：结合 `stock-market-hub` 的分析结果，输出 `buy / add / hold / trim / sell / avoid / watch`

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

## 依赖关系

本 skill 不复制 `stock-market-hub` 的分析代码，而是通过脚本桥接复用：

- `stock-market-hub/scripts/analyze_company.py`

因此推荐把两个 skill 放在同一个仓库里，并分别在客户端 skill 目录中做软链接。

## 共享模块演进约定

- 当前优先复用已经被两个 skill 同时依赖的能力；现阶段主要是 `company` 分析链路。
- `stock-market-hub` 中的 `news / sector / risk / ann / timeline / supply / pdf` 不要求一次性全部抽到共享层。
- 后续开发本 skill 时，如果新增功能明确需要调用 `stock-market-hub` 的某个能力，再把对应能力抽到 `shared/stock_core`。
- 抽取顺序遵循“先有真实复用，再做共享沉淀”，避免为了预防性重构把两个 skill 一起拖重。

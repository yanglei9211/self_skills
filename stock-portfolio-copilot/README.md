# stock-portfolio-copilot

A 股 / 港股持仓与交易决策助手。用于记录初始持仓、买卖成交、自选股、资金约束，并结合 `stock-market-hub` 的行情和公司分析结果给出当前操作建议。

## 适用场景

- 初始化历史持仓。
- 记录真实买入、卖出、加仓、减仓、止损、清仓。
- 根据 seed + trade ledger 同步当前持仓、均价、盈亏。
- 管理自选股和资金上限。
- 结合市场数据输出 `buy / focus / add / hold / trim / sell / probe / avoid / watch`。

## 不适用场景

- 不自动下单。
- 不替代券商账户真值；当用户提供持仓截图并声明以截图为准时，应以截图为当前持仓真值。
- 不应把分析结论直接当成交易指令。

## 快速开始

```bash
cd /path/to/self_skills
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

SPC=./stock-portfolio-copilot/bin/spc
$SPC account create --slug default --name "默认账户" --set-default
$SPC position init --account default --market a --code 300750 --qty 1000 --cost 245.30
$SPC portfolio sync --account default
$SPC portfolio show --account default
```

## 常用命令

| 功能 | 示例 |
|---|---|
| 创建账户 | `spc account create --slug default --name "默认账户" --set-default` |
| 初始化持仓 | `spc position init --account default --market a --code 300750 --qty 1000 --cost 245.30` |
| 记录买入 | `spc trade add --account default --market hk --code 01810 --side buy --qty 500 --price 19.10 --time "2026-05-08 10:32:00"` |
| 记录卖出 | `spc trade add --account default --market a --code 600584 --side sell --qty 300 --price 54.58 --time "2026-05-14 14:55:02"` |
| 同步持仓 | `spc portfolio sync --account default` |
| 展示持仓 | `spc portfolio show --account default` |
| 一致性检查 | `spc portfolio check --account default` |
| 添加自选 | `spc watch add --account default --market hk --code 01810` |
| 设置资金约束 | `spc capital set --account default --total 500000 --max-single-pct 20` |
| 当前分析 | `spc analyze now --account default --scope holdings` |
| 盈亏报告 | `spc report pnl --account default` |

## 关键红线

`position init` 只用于首次录入历史持仓基线。任何真实买入、卖出、加仓、减仓、止损、清仓都必须用：

```bash
spc trade add --side buy
spc trade add --side sell
```

不要用 `position init` 覆盖真实交易历史。错误使用会破坏 trade ledger，导致已实现盈亏和复盘时间线不可追溯。

## 依赖与配置

- Python 依赖复用根目录 `.venv` 和 `requirements.txt`。
- 行情和公司分析复用 `stock-market-hub` 与 `shared/stock_core`。
- 多数命令需要 `--account <slug>`；旧数据会迁移到默认账户。

## 数据与产物

- 持仓数据库为本地个人数据，不应入库。
- 可通过环境变量或配置覆盖数据目录。
- 交易预案和成交复盘应使用结构化 `exec` 子命令，不要只写在 note 中。

## 注意事项

- `max-single-pct` 是单票市值占账户总资金上限的比例，不是占当前持仓市值的比例。
- 用户提供券商持仓截图且声明以截图为准时，当前持仓真值应服从截图。
- 所有分析建议都应区分事实、规则触发和主观判断。

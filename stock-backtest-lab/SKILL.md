# stock-backtest-lab

Point-in-Time 回测系统：基于本地 SQLite 历史数据仓，严格按回测日期 T 之前的数据计算信号，验证交易策略的历史表现。

## Quick Start

```bash
# 1. 同步个股 K 线到本地数据库
stockbt sync SZ000333 --from 2020-01-01

# 2. 同步指数 K 线（大盘 regime 分层用）
stockbt sync-index --from 2020-01-01

# 3. 运行回测
stockbt run SZ000333 --strategy boll_squeeze_entry --from 2020-01-01 --to 2026-05-31
```

## Commands

| 命令 | 说明 |
|------|------|
| `stockbt sync SYMBOL` | 同步个股日 K 线到本地数据库 |
| `stockbt sync-index` | 同步 4 个默认指数日 K 线 |
| `stockbt run SYMBOL` | 运行回测并输出 Markdown/JSON 报告 |

## Available Strategies (P0)

| 策略 | 说明 |
|------|------|
| `boll_squeeze_entry` | BOLL 挤压入场 |
| `ma_breakout` | 均线突破/趋势跟随 |
| `price_new_high` | 新高趋势策略 |

## Data Storage

本地数据库：`~/.cache/stock-backtest-lab/history.sqlite`

## Key Design

- 回测只读本地 SQLite，不访问网络
- 所有信号计算严格基于 T 及之前的数据（Point-in-Time 约束）
- K 线口径：前复权（qfq）
- 信号数 N < 20 时不输出"最优"，只标注"样本不足"

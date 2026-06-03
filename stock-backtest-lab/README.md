# stock-backtest-lab

Point-in-Time 回测系统。目标是把股票历史 K 线同步到本地 SQLite，并在回测日期 T 只使用 T 及之前的数据计算信号，验证策略历史表现。

## 适用场景

- 验证 BOLL squeeze、均线突破、新高趋势等技术策略。
- 用本地历史数据仓做单标的 P0 回测。
- 检查策略在不同年份、不同大盘 regime 下的表现。
- 为后续资金流、公告 PIT、多标的回测打基础。

## 不适用场景

- 当前版本不做资金流、公告、新闻、雪球热度等复杂特征回测。
- 不做分钟线、滑点、手续费、真实成交撮合。
- 不自动修改 `stock-portfolio-copilot` 的交易决策参数。

## 快速开始

```bash
# 在 self_skills 根目录准备 venv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 同步个股和指数 K 线
./stock-backtest-lab/bin/stockbt sync SZ000333 --from 2020-01-01
./stock-backtest-lab/bin/stockbt sync-index --from 2020-01-01

# 运行回测
./stock-backtest-lab/bin/stockbt run SZ000333 \
  --strategy boll_squeeze_entry \
  --from 2020-01-01 \
  --to 2026-05-31
```

## 主要命令

| 命令 | 说明 |
|---|---|
| `stockbt sync SYMBOL` | 同步个股日 K 线到本地 SQLite |
| `stockbt sync-index` | 同步沪深 300、创业板指、恒生指数、恒生科技指数 |
| `stockbt run SYMBOL` | 运行单标的回测并输出 Markdown/JSON 报告 |

## 可用策略

| 策略 | 说明 |
|---|---|
| `boll_squeeze_entry` | BOLL 挤压入场 |
| `ma_breakout` | 均线突破 / 趋势跟随 |
| `price_new_high` | 新高趋势策略 |

## 依赖与配置

- Python 依赖走仓库根目录 `.venv`。
- sync 阶段复用 `shared/stock_core`，需要 `curl_cffi`。
- 回测阶段只读本地 SQLite，不访问网络。

## 数据与产物

- 默认数据库：`~/.cache/stock-backtest-lab/history.sqlite`
- 数据口径：前复权 `qfq`
- 本地数据库不入 git。

## 测试

```bash
for f in stock-backtest-lab/tests/run_test_*.py; do
  .venv/bin/python3 "$f" || exit 1
done
```

## 注意事项

- 真实 sync 能否覆盖 2020 至今取决于上游接口返回数量，需要实际验证。
- point-in-time 回测必须防止 T 之后数据进入信号计算。
- 默认日期和交易日覆盖检查是高风险点，改动后必须跑端到端测试。

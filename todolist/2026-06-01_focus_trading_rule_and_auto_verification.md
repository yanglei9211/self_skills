# Focus 结构化交易规则 & 定时复核自动通知

> 创建日期：2026-06-01
> 状态：需求草案
> 关联技能：`stock-market-hub`、`stock-portfolio-copilot`、`lark-im`

---

## 1. 背景与动机

当前 `stock-market-hub` 的 focus 分析流程是"对话式"的：Agent 输出 T / T+1 / T+2 盯盘条件后，依赖用户**人工盯盘**来在对应时间点手动触发复核。以东鹏饮料 (605499) 的本次分析为例，输出包括：

- 条件 A（缩量回踩 140~143，日量 < 350 万股）→ 观察信号
- 条件 B1（突破 146.60 + 量 > 690 万股）→ 标准买点，建仓 1/3
- 条件 B2（突破 150.90 + 量 > 966 万股）→ 强势买点
- T+1 确认（最低 ≥ 146.60 + 收盘高于 T 日）→ 加仓 1/3
- T+2~3 验证（回踩不破 146.60 + MA5 上穿 MA10）→ 加最后 1/3

这些条件是**有时效性的**——T+1 过了没看，窗口就错过了。需要把"人工记得到时候看"升级为"系统到点自动复核 + 主动通知"。

---

## 2. 目标

将 Agent 输出的自然语言交易规则，转成**结构化、可执行**的规则文件，并**注册为定时任务**，在指定交易日的市场时段自动：

1. 拉取最新行情 / 资金流数据（通过 `stock-market-hub`）
2. 逐条评估触发条件是否满足
3. 将复核结果**主动推送**给用户（Lark IM / 其他渠道）

用户不需要记"明天收盘要看东鹏"，系统到点自己查、自己判、自己通知。

---

## 3. 核心概念

### 3.1 Structured Trading Rule（结构化交易规则）

一个 JSON / YAML 文件，包含一只 focus 标的的完整交易计划：

```
focus-{symbol}-{date}.json
```

字段概要：

| 字段 | 说明 |
|---|---|
| `meta` | 标的代码、名称、创建时间、状态（active / triggered / expired / stopped） |
| `baseline` | 创建时的基准数据：当日价格、均线、BOLL、均量、成交量分位、资金流 regime |
| `phases[]` | 分阶段触发条件列表，每个 phase 包含 day_offset、价格阈值、量能阈值、K 线形态要求等 |
| `stop_loss` | 止损条件（价格、量能确认、时间止损） |
| `position_plan` | 分仓计划（1/3 → 1/3 → 1/3） |
| `notification` | 通知偏好（渠道、触发即通知 vs 每日汇总、是否需要确认操作） |

### 3.2 Phase（交易阶段）

每个 Phase 是一个独立的触发条件组，包含：

```json
{
  "phase_id": "B1",
  "name": "标准买点",
  "day_offset": 0,
  "action": "buy_1_3",
  "requires_phase": ["A"],
  "conditions": {
    "price": { "operator": "gte", "value": 146.60 },
    "volume": { "operator": "gte", "value": 6900000 },
    "volume_ratio": { "operator": "gte", "value": 1.0 },
    "candle": { "body_pct_min": 1.5, "lower_shadow_lte_upper": true }
  }
}
```

- `day_offset`：相对 T 日的交易日偏移（0 = 当日，1 = 下一交易日，2~3 = 后续）
- `requires_phase`：前置阶段必须已触发（如 B1 依赖 A 已满足）
- `conditions`：所有条件为 **AND** 关系，全部满足才触发

### 3.3 Verification（复核）

在每个交易日收盘后（或盘中关键时点），Verifier 执行：

1. 用 `smh company {symbol}` 拉最新行情 + 资金流
2. 用 K 线 API 拉最近 N 日量价
3. 逐 Phase 评估条件 → 输出 `triggered` / `pending` / `failed` / `expired`
4. 将评估结果写入规则文件（追加 `verifications[]` 记录）
5. 如有触发或关键变化 → 推送通知

---

## 4. 系统架构

```
┌──────────────────────────────────────────────────────────┐
│                    Focus Rule Engine                      │
├───────────────┬──────────────────────────────────────────┤
│  Rule Writer  │  对话结束后，Agent 或模板引擎将自然语言    │
│               │  规则转为 JSON 规则文件                    │
├───────────────┼──────────────────────────────────────────┤
│  Scheduler    │  解析规则中的 day_offset，计算交易日历    │
│               │  日期，注册 Cron 定时任务                  │
├───────────────┼──────────────────────────────────────────┤
│  Verifier     │  到时间后触发：拉数据 → 评估条件 → 写结果  │
│               │  核心依赖：stock-market-hub（行情/资金流） │
├───────────────┼──────────────────────────────────────────┤
│  Notifier     │  推送复核结果：Lark IM / 邮件 / CLI 输出   │
├───────────────┼──────────────────────────────────────────┤
│  Rule Store   │  ~/.config/stock-market-hub/focus/        │
│               │  每个规则一个 JSON 文件 + 复核历史         │
└───────────────┴──────────────────────────────────────────┘
```

### 4.1 与现有技能的集成

| 组件 | 依赖技能 / 工具 | 用途 |
|---|---|---|
| 行情拉取 | `stock-market-hub` (`smh company`, `smh flow`) | 获取最新价、量、均线、资金流 |
| 交易日历 | `stock-market-hub` 已有交易日判断逻辑 | 计算 T+1 / T+2 的真实日期 |
| 定时调度 | Claude Code `CronCreate`（durable）| 注册/管理定时任务 |
| 通知推送 | `lark-im` | 飞书消息推送复核结果 |
| 持仓同步 | `stock-portfolio-copilot` | 触发买入后自动记录成交 |

---

## 5. 功能清单

### P0 — 最小可用闭环

- [ ] **F1: 规则生成** — `smh focus create SH605499`，交互式生成结构化 JSON 规则（基于对话分析结果，由 Agent 填充）
- [ ] **F2: 规则存储** — 写入 `~/.config/stock-market-hub/focus/{id}.json`，含完整基线数据和 Phase 条件
- [ ] **F3: 手动复核** — `smh focus verify {id}`，立即拉最新数据，评估所有 Phase，输出触发状态
- [ ] **F4: 定时复核（收盘后）** — 每个交易日 15:30 自动运行所有 active 规则的 Verifier
- [ ] **F5: 通知推送** — Phase 触发或止损触发时，通过 Lark IM 推送摘要（标的、触发条件、建议操作、当前数据）

### P1 — 完整体验

- [ ] **F6: 分时点复核** — 不只在收盘，按 Phase 的 `day_offset` 精确调度（T 日盘中 14:50 初判 + 15:10 收盘确认，T+1 同样）
- [ ] **F7: 自然语言 → 规则** — Agent 在 focus 分析对话末尾自动生成规则 JSON，用户确认后一键创建
- [ ] **F8: 规则状态仪表盘** — `smh focus list` 列出所有活跃规则及其当前状态
- [ ] **F9: 复核历史** — 每次 Verification 结果追加写入规则文件，可追溯
- [ ] **F10: 止损自动提醒强化** — 不止收盘，盘中破止损位也推送（需盘中 Cron，15 分钟间隔）

### P2 — 进阶

- [ ] **F11: 规则模板库** — 常见交易形态的预置模板（趋势回踩、筑底反转、突破追入），减少每次手写
- [ ] **F12: 多标的并发** — 同时追踪多只 focus 标的的规则
- [ ] **F13: 与 portfolio-copilot 联动** — Phase 触发后自动生成买入预填单（价格、数量、理由），用户一键确认
- [ ] **F14: 复盘报告** — 规则过期后自动生成复盘：哪些 Phase 触发了、实际走势 vs 预期、假突破记录

---

## 6. 关键设计决策（待讨论）

### 6.1 交易日历

Cron 是日历驱动的（"每个工作日 15:30"），但 A 股有特殊休市日（春节、国庆等）。方案：

- **方案 A**：`CronCreate` 注册"每个工作日 15:30"，Verifier 启动后先查交易日历，非交易日直接退出
- **方案 B**：在规则创建时计算出 T+1 / T+2 的具体日历日期，注册为 one-shot Cron

建议先用方案 A（简单，不会漏），后续优化为方案 B（减少无效唤醒）。

### 6.2 盘中 vs 收盘复核

盘中复核（14:50）能更早发现信号，但数据不完整（成交额非全天、资金流非最终）。建议：

- **14:50 盘中初判**：仅评估价格和量比（实时可得），发出 "preliminary" 级别通知
- **15:10 收盘确认**：完整数据评估，发出 "confirmed" 级别通知（以这个为准）

### 6.3 通知频率控制

同一标的可能在同一天多次触发不同 Phase。需要：

- 去重：同一 Phase 不重复通知
- 聚合：收盘后发一封汇总而非 3 条碎片消息
- 分级：止损 > 买入触发 > 观察信号 > 日常无变化

---

## 7. 文件结构规划

```
~/.config/stock-market-hub/focus/
├── active/
│   └── focus-SH605499-20260601.json    # 活跃规则
├── history/
│   └── focus-SH605499-20260601.json    # 过期 / 完成的规则
└── templates/
    ├── trend_pullback.json             # 趋势回踩模板
    ├── reversal_bottom.json            # 筑底反转模板
    └── breakout_chase.json             # 突破追入模板
```

规则 JSON 文件内嵌 `verifications` 数组，记录每次复核的时间戳和结果。

---

## 8. 示例：东鹏饮料规则骨架

```json
{
  "id": "focus-SH605499-20260601",
  "meta": {
    "symbol": "SH605499",
    "name": "东鹏饮料",
    "created": "2026-06-01T15:30:00+08:00",
    "status": "active",
    "expires": "2026-06-13T15:00:00+08:00"
  },
  "baseline": {
    "price": 149.17,
    "ma5": 141.84,
    "ma10": 143.78,
    "ma20": 170.85,
    "ma60": 200.66,
    "boll_mid": 146.59,
    "boll_upper": 155.37,
    "vol_5d_avg": 7476216,
    "vol_p25": 3494777,
    "vol_p50": 6138932,
    "vol_p75": 6922072,
    "vol_p90": 9655781,
    "fund_flow_regime": "RESONANCE_INFLOW",
    "market_regime": "NEUTRAL"
  },
  "phases": [
    {
      "phase_id": "A",
      "name": "缩量回踩",
      "day_offset": 0,
      "action": "observe",
      "requires_phase": [],
      "conditions": {
        "price_min": 140.00,
        "price_max": 143.00,
        "volume_max": 3500000,
        "volume_ratio_max": 0.5
      }
    },
    {
      "phase_id": "B1",
      "name": "标准买点",
      "day_offset": 0,
      "action": "buy_1_3",
      "requires_phase": ["A"],
      "conditions": {
        "price_min": 146.60,
        "volume_min": 6920000,
        "volume_ratio_min": 1.0,
        "candle_body_pct_min": 1.5,
        "lower_shadow_lte_upper": true
      }
    },
    {
      "phase_id": "B2",
      "name": "强势买点",
      "day_offset": 0,
      "action": "buy_1_3",
      "requires_phase": [],
      "conditions": {
        "price_min": 150.90,
        "volume_min": 9660000,
        "volume_ratio_min": 1.3,
        "candle_body_pct_min": 2.0
      }
    },
    {
      "phase_id": "T1",
      "name": "T+1确认",
      "day_offset": 1,
      "action": "add_1_3",
      "requires_phase": ["B1"],
      "conditions": {
        "low_min": 146.60,
        "close_above_prev_close": true,
        "volume_min_p50": true
      }
    },
    {
      "phase_id": "T2T3",
      "name": "T+2~3验证",
      "day_offset_min": 2,
      "day_offset_max": 3,
      "action": "add_1_3",
      "requires_phase": ["T1"],
      "conditions": {
        "pullback_low_min": 146.60,
        "volume_above_p50": true,
        "ma5_above_ma10": true
      }
    }
  ],
  "stop_loss": {
    "close_below": 136.50,
    "intraday_break": 137.30,
    "intraday_break_vol_ratio_min": 0.8,
    "time_stop_trading_days": 5,
    "time_stop_condition": "close_below_ma10"
  },
  "notification": {
    "channel": "lark-im",
    "on_trigger": true,
    "on_stop_loss": true,
    "daily_summary": true
  },
  "verifications": []
}
```

---

## 9. 实现路径建议

推荐分两期：

**一期（P0）**：手动复核 + 收盘定时
- 先做 `smh focus create` + `smh focus verify`（手动挡），验证规则引擎和条件评估的正确性
- 再加 Cron 定时（自动挡），每个交易日 15:30 统一跑一次
- 通知走 `lark-im` 发一条消息

**二期（P1）**：自然语言自动转规则 + 分时点精确调度
- Agent 在 focus 对话末尾直接输出 JSON 规则
- 按 `day_offset` 精确计算交易日历日期，注册 one-shot Cron
- 盘中 14:50 + 收盘 15:10 双重复核

---

## 10. 风险与边界

- **非实时**：Cron 最小间隔 60s，做不到 tick 级触发；日线级别交易足够，盘中破位无法秒级响应
- **交易日历依赖**：需要可靠的 A 股交易日历；春节/国庆长假可能导致 T+1 实际间隔 7-10 天
- **不执行交易**：本系统只做复核 + 通知，不会自动下单。操作始终由用户决策
- **规则过期**：规则应设 TTL，超过 time_stop_trading_days 自动标记 expired，避免僵尸任务堆积

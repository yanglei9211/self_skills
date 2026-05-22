# 强势标的池

> **池子定位**：从小安自选池（44只）+ 全市场扫描中，筛选资金面+价格面双强的标的。池内标的比池外优先级高。
> **与自选池关系**：自选池 = 长期关注（SPC watchlist），强势池 = 当下最强、接近可操作，**每天动态调整**。验证短期启动不了就降回自选池。
> **使用规则**：小安和 A2 执行买卖时，优先确认池内标的信号。

## 最近更新：2026-05-22 周五全扫（T1 4只 / T2 3只 / 港股 2只 / 验证 3只）

### A 股 Tier 1 — 主力+价格双强

| 代码 | 名称 | 现价 | 涨跌 | 主力 Regime | 交叉验证 | 市值 | 持仓 | 主线 |
|------|------|------|------|------|------|------|------|------|
| 300274 | 阳光电源 | 164.41 | -0.59% | OSCILLATING | REVERSAL_INFLOW_CONFIRMED | 3,467亿 | A2 100股 | decelerating+今日流出，降为WATCH但保留T1 |
| 601689 | 拓普集团 | 71.59 | -0.73% | OSCILLATING | **RESONANCE_INFLOW** | 1,262亿 | 小安 200股 | **BUY** 四周期共振+accelerating，信号最稳 |
| 603501 | 豪威集团 | 103.91 | +1.22% | OSCILLATING | **RESONANCE_INFLOW** | 1,295亿 | — | **BUY** CIS龙头YTD-20%+NEAR_YTD_LOW，回购+回售密集 |
| 000725 | 京东方A | 5.15 | +9.81% | PERSISTENT_INFLOW | **RESONANCE_INFLOW** | 2,105亿 | — | 🆕 **NEW_ALL_TIME_HIGH**，20d+27.32亿，面板龙头突破。⚠️当日+9.8%不追，等回调 |

### A 股 Tier 2 — 强势待确认

| 代码 | 名称 | 现价 | 涨跌 | 主力 Regime | 交叉验证 | 市值 | 持仓 | 主线 |
|------|------|------|------|------|------|------|------|------|
| 300124 | 汇川技术 | 78.85 | +1.39% | ⚠️ PERSISTENT_OUTFLOW | **REVERSAL_INFLOW_CONFIRMED** | 2,082亿 | — | FOCUS，1d/5d反转确认+回购+H股上市，等20d转正升T1 |
| 600699 | 均胜电子 | 30.15 | -0.10% | ⚠️ PERSISTENT_OUTFLOW | **REVERSAL_INFLOW_CONFIRMED** | 468亿 | — | FOCUS，1d+3.50亿(9.67%)极强，等20d转正 |
| 002156 | 通富微电 | 63.41 | +2.67% | PERSISTENT_OUTFLOW | REVERSAL_UNCONFIRMED | 937亿 | A2 400股 | ALL_TIME_HIGH追高风险，A2浮亏，止损58.64 |

### 港股通

| 代码 | 名称 | 现价 | 涨跌 | 主力 Regime | 交叉验证 | 市值 | 持仓 | 主线 |
|------|------|------|------|------|------|------|------|------|
| 01276 | 恒瑞医药 | 61.75 | -1.04% | PERSISTENT_INFLOW | PERSISTENT_INFLOW_STEADY | — | A2 1000股 | FOCUS，唯一盈利港股+7.4%，港股RISK_OFF压制但创新药逻辑不变 |
| 01810 | 小米 | 30.08 | +1.42% | PERSISTENT_INFLOW | DECELERATING_INFLOW | 7,643亿 | A2 12600股 | 20d+65.90亿仍最强，但DECELERATING+RISK_OFF+仓位过重，反弹减仓 |

### 验证期观察

| 代码 | 名称 | 现价 | 涨跌 | 主力 Regime | 交叉验证 | 观察要点 |
|------|------|------|------|------|------|------|
| 688012 | 中微公司 | 469.57 | -1.31% | PERSISTENT_INFLOW | **REVERSAL_INFLOW_CONFIRMED** | 20d+25.18亿，半导体设备龙头，sector laggard需改善 |
| HK 06160 | 百济神州 | 184.90 | +0.98% | PERSISTENT_INFLOW | **RESONANCE_INFLOW** | 20d+4.94亿，创新药双龙头，港股RISK_OFF压制 |
| HK 02628 | 中国人寿 | 29.06 | +1.18% | PERSISTENT_INFLOW | **RESONANCE_INFLOW** | 20d+13.22亿，保险龙头，港股RISK_OFF压制 |

---

### 周五全扫结果

**自选池扫描**：44 只 → 系统评级分布：

| 评级 | 数量 | 说明 |
|------|------|------|
| FOCUS | 7 只 | 钒钛/京东方/汇川/新天/豪威/百合花/恒瑞H |
| WATCH | 22 只 | 继续跟踪 |
| AVOID | 11 只 | 中兴/泸州老窖/中钨/锡业/沃尔/科大/立讯/英维克/鼎龙/蓝标/中际/润泽/宁德/特变/恒瑞A/汾酒/工业富联/中铝/人寿A/卫通/长飞/紫金A/曙光/洛钼A/华特/佰维/华海/阿里 |
| HOLD | 4 只 | 三花/拓普(持仓中) |

**FOCUS 7只筛选结果**：

| 标的 | 资金流 | 入池? | 理由 |
|------|------|------|------|
| 京东方A(000725) | PERSISTENT_INFLOW + RESONANCE_INFLOW | ✅ T1 | NEW_ALL_TIME_HIGH，面板龙头突破 |
| 豪威集团(603501) | RESONANCE_INFLOW | ✅ T1 | 已在池，维持 |
| 恒瑞医药H(01276) | PERSISTENT_INFLOW_STEADY | ✅ 港股 | 已在池，维持 |
| 汇川技术(300124) | REVERSAL_INFLOW_CONFIRMED | ✅ T2 | 已在池，20d待转正 |
| 钒钛股份(000629) | PERSISTENT_OUTFLOW 20d-2.73亿 | ❌ | 资金流不健康 |
| 新天绿能(600956) | REVERSAL_OUTFLOW_CONFIRMED + laggard | ❌ | 资金流出+跑输板块 |
| 百合花(603823) | 缺资金流数据 | ❌ | 上次已标误判，顶部出货 |

### 降级/轮出记录

| 代码 | 名称 | 原Tier | 降级原因 | 去向 |
|------|------|------|------|------|
| 000988 | 华工科技 | T1 | RESONANCE_OUTFLOW -21.81亿崩塌 | 自选池观察 |
| 600584 | 长电科技 | T1 | 主力-9.70亿+ALL_TIME_HIGH动能衰减 | 自选池观察 |
| 000568 | 泸州老窖 | T2 | NEW_ALL_TIME_LOW 白酒崩塌 | 自选池观察 |
| 09988 | 阿里巴巴 | 港股 | RESONANCE_OUTFLOW -19.75亿反转破裂 | 自选池观察 |

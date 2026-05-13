---
name: stock-market-hub
description: >-
  A股 / 港股 / 中概股 金融市场分析中心：当日财经新闻聚合、板块龙头/热门/风险股扫描、
  公司深度尽调（基本面、财报、高管、主要股东、上下游、近期公告、财报 PDF 解析）。
  当用户提到 A股、港股、中概、股票分析、个股分析、板块扫描、行情、涨幅榜、跌幅榜、
  主力净流入、雪球热度、ST 风险股、上市公司、公司分析、商业模式、上下游、
  财报、年报、季报、招股书、巨潮、披露易、HKEX、CSRC、董事变更、立案调查、
  重大诉讼、财联社电报、券商研报 时使用。
---

# Stock Market Hub — 股票金融市场分析中心

一套面向 A 股 + 港股 + 中概股的市场情报与个股深度分析工具。
覆盖三个核心场景：当日金融市场新闻、板块/榜单扫描、公司深度尽调。

## 0. 安装与依赖

```bash
# Python 依赖（推荐：用仓库根目录 self_skills/.venv 同时服务两个股票 skill）
cd "$SELF_SKILLS_HOME"     # 或者你 clone 仓库的根目录
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 系统依赖（用于港股年报 OCR fallback）
brew install tesseract tesseract-lang poppler

# 可选：SEC EDGAR 合规 UA（中概股查询时会用）
export SEC_USER_AGENT="your-name your-email@example.com"

# 可选：雪球登录 cookie（启用热门帖 / 个股新闻流）
mkdir -p ~/.config/stock-market-hub
# 把浏览器登录后的雪球 cookie 字符串写到 ~/.config/stock-market-hub/xueqiu.cookie
```

## 0.1 统一 CLI 入口（推荐）

所有功能都可以通过 `bin/smh` 调用。多机部署请用下面任一方式定位入口，**不要写死绝对路径**：

```bash
# 方式 A：通过 Cursor / Claude skills 软链入口（推荐）
SMH=$(python3 -c "import os; print(os.path.realpath(os.path.expanduser('~/.cursor/skills/stock-market-hub/bin/smh')))")
# Claude 用户把 ~/.cursor 换成 ~/.claude 即可

# 方式 B：已经 cd 到仓库根目录
SMH=./stock-market-hub/bin/smh

# 方式 C：用环境变量指向仓库根
export SELF_SKILLS_HOME=/path/to/self_skills
SMH="$SELF_SKILLS_HOME/stock-market-hub/bin/smh"
```

下面示例都假设 `$SMH` 已设好：

```bash

# 公司深度卡片
$SMH company SZ300750               # A 股
$SMH company HK01810 --with-peers   # 港股 + 同业横向对比
$SMH company BABA --ann-days 60     # 中概股（SEC EDGAR）

# 板块扫描 / 市场速览 / 风险扫描
$SMH sector "AI PC"
$SMH market --board gainers --top 10
$SMH risk --rules R1,R2,R5          # 默认快速扫描
$SMH risk --rules R1,R2,R5,R9      # 加上重大诉讼扫描
$SMH risk --all                    # 全规则 R1-R9

# 公告 / 时间轴 / PDF / 上下游 / 主力资金流
$SMH ann SZ300750 --days 60
$SMH timeline HK01810 --days 30 --news-keywords "小米,Xiaomi,SU7,YU7"
$SMH pdf URL --sections business,risks
$SMH supply SZ300750
$SMH flow SZ300750                  # 单独看主力资金流（也会自动并入 smh company）
$SMH flow HK01810 --format json

# 大盘风险偏好（A 股 + 港股；RISK_OFF / NEUTRAL / RISK_ON）
$SMH regime                         # A 股 + 港股各出一份
$SMH regime --market hk             # 仅港股（恒生 + 恒生科技）
$SMH regime --format json           # 机器可读，被 stock-portfolio-copilot 自动消费

# 缓存管理
$SMH cache stats
$SMH cache clear --prefix kline
```

直接调脚本也可以（每个脚本独立 `argparse --help`）。venv 解释器走仓库根目录：

```bash
VPY="$SELF_SKILLS_HOME/.venv/bin/python3"   # 或：./.venv/bin/python3
$VPY scripts/analyze_company.py --symbol SZ300750 --with-peers
```

## 1. 数据源全景

详见 [references/data_sources.md](references/data_sources.md)。要点：

- **核心源（公开免费）**：财联社电报、雪球 screener、新浪行情/财报、巨潮（A股权威公告）、披露易（港股权威公告）、同花顺板块
- **被反爬封禁**：东方财富 push2 接口（即使 Chrome 指纹也立即断开）→ 已绕开，不依赖
- **WAF 受限**：雪球热门帖 → v1 不启用，要稳定使用需用户提供已登录 cookie
- **统一调用层**：所有 HTTP 请求走 `scripts/core/http.py`（curl_cffi + Chrome 指纹 + 自动重试）

## 2. 三大场景及调用契约

### 场景 A：当日金融市场情报

> 用户语境："今天 A 股发生了什么"、"现在的市场行情怎么样"、"今天有什么大新闻"

**核心脚本**：`xueqiu_market.py` + `fetch_market_news.py`

```bash
# 1. 市场速览（涨跌榜 / 成交额 / 主力资金 / 散户热度 / ST 风险）
$VPY scripts/xueqiu_market.py --board all --top 5
$VPY scripts/xueqiu_market.py --markets all_a,hk,us --board gainers --top 5

# 2. 当日新闻流（财联社电报为主）
$VPY scripts/fetch_market_news.py --limit 50 --format json
```

**Agent 输出格式（中文 Markdown）**：

```
# YYYY 年 M 月 D 日 A 股 / 港股 / 中概 市场速览

## 一、市场情报
- `HH:MM` ★ **重点新闻一句话摘要**。 — [来源](url)
- `HH:MM` 普通新闻一句话摘要。 — [来源](url)

## 二、涨跌榜
### A 股涨幅榜 Top 10
| 代码 | 名称 | 现价 | 涨跌幅 | 成交额 | 总市值 |

## 三、资金动向
### 主力净流入榜 Top 5
（同上表格）

## 四、风险预警
### ST 跌幅榜 / 连续跌停股
（同上）

## 五、雪球散户热度榜
（同上）
```

### 场景 B：板块扫描

> 用户语境："看一下半导体板块"、"AI 概念股有哪些龙头"、"今天哪个板块异动"

**核心脚本**：`scan_sector.py`

```bash
$VPY scripts/scan_sector.py --sector "半导体" --top 10
$VPY scripts/scan_sector.py --sector "新能源车" --include-risk
$VPY scripts/scan_sector.py --list  # 列出所有可用板块
```

**输出**：板块涨跌幅、龙头股 Top N（按市值+成交额）、热门股 Top N（按换手率+雪球关注度）、高风险股（ST/连续跌停/财务恶化）

### 场景 C：公司深度卡片

> 用户语境："分析一下宁德时代 300750"、"腾讯 0700 财务怎么样"、"阿里巴巴 BABA 商业模式"

**核心脚本**：`analyze_company.py`（速查模式）/ `analyze_company.py --deep`（含 PDF 年报 + 上下游）

```bash
# 速查卡片（基本信息 + 财报 + 高管 + 主要股东 + 概念归属 + 近 30 天公告）
$VPY scripts/analyze_company.py --symbol SZ300750
$VPY scripts/analyze_company.py --symbol HK00700
$VPY scripts/analyze_company.py --symbol BABA   # 中概股

# 深度模式（再加：年报 PDF 解析的业务概要 / 主要客户供应商 / 风险因素 / 上下游图谱 / 同业对比）
$VPY scripts/analyze_company.py --symbol SZ300750 --deep
```

**Agent 输出模板（中文 Markdown）**：

```
# {名称} ({代码}) — 公司深度卡片

## 一、公司基础信息
- 行业 / 概念归属
- 上市日期 / 总市值 / 流通市值
- 主营业务一句话

## 二、关键财务指标（最近报告期 + 同比 + 滚动）
| 指标 | 本期 | YoY | 行业中位 |
| 营业收入 | ... | ... | ... |
| 归母净利 | ... | ... | ... |
| 毛利率 | ... | ... | ... |
| ROE TTM | ... | ... | ... |
| 资产负债率 | ... | ... | ... |
| 经营性现金流 | ... | ... | ... |

## 三、核心高管
| 姓名 | 职位 | 任期 | 薪酬 | 持股 |

## 四、主要股东（前 10）
| 股东 | 性质 | 持股比例 | 变动 |

## 五、近 30 天关键公告
- YYYY-MM-DD **公告标题** [PDF](url)

## 六、商业模式 / 上下游 (--deep 才有)
{基于年报"业务概要"章节 + 主要客户/供应商列表 + LLM 整合}

## 七、风险提示 (--deep 才有)
{年报"风险因素"章节摘要 + 当前公告中的风险关键词命中}

## 八、同业对比 (--deep 才有)
{同板块 5 家公司的 PE/PB/营收增速/净利润增速对比}
```

## 3. Agent 行为约束（核心摘要）

> 📜 **完整行为约束、反例 / 正例、强制工作流见 [references/agent-constraints.md](references/agent-constraints.md)。**
> 任何涉及个股归因、技术位、主力资金、大盘 regime、财务数字、产品时间线、宏观数据
> 的具体分析**必须**先读那份文件。本节只列出最关键的索引，避免在主 SKILL.md 里
> 反复加载完整 prompt。

### 输出语言

- **默认中文输出**，公司名 / 人名 / 产品名保留英文（Tencent / NVIDIA / GPT-5 等）
- **每条数据都要给来源链接**（巨潮公告 PDF / 披露易公告 / 雪球行情 / 财联社电报）

### 六条核心硬约束（一句话索引）

1. **数据溯源** —— 每个事实都要说得清来源（哪条接口 / 哪个 PDF 第几页）；
   不得用训练集记忆代替真实抓取。详见 [constraints §0–§1](references/agent-constraints.md)。
2. **技术分析必须基于 K 线** —— `price_history.regime / thresholds / yearly`
   没看过不准写"支撑位 / 压力位 / 破位 / 历史区间"。详见 [constraints §2](references/agent-constraints.md)。
3. **主力资金必须基于 fflow** —— `fund_flow.regime + reversal + rolling + 当日机构档位`
   没列出就不能写"持续流入 / 持续流出 / 机构进出"。详见 [constraints §3](references/agent-constraints.md)。
4. **大盘判断必须分 A 股 / 港股** —— 用 `smh regime` 的 `market_regime`；
   禁止跨市场套用，禁止凭"看着挺红"直觉。详见 [constraints §4](references/agent-constraints.md)。
5. **格式硬约束** —— 中文 + 标准 Markdown + 末尾必带"数据覆盖说明"。详见 [constraints §5](references/agent-constraints.md)。
6. **不要做的事** —— 不用付费源假数据、不把雪球关注度当机构观点、不预测未来股价、
   不主动建议买卖、不凭印象写时间点 / 价格 / 宏观数据。详见 [constraints §6](references/agent-constraints.md)。

### 分析前的强制工作流

写任何归因 / 时间节点 / 产品线信息**之前**，按顺序执行：

```
1. fetch_announcements  → 公告时间线
2. pdf_extract          → 从原文抽数字
3. fetch_market_news    → 当日宏观新闻 + 因果链来源
4. analyze_company      → price_history + fund_flow 一站式
5. smh regime           → A 股 / 港股各自的大盘背景
```

跳过这些步骤直接开写就违规。

## 4. 限频与稳定性

| 源 | 推荐 QPS | 备注 |
|---|---|---|
| 雪球 screener | 2/s（已在客户端做了节流） | 单 IP 每天 ~10000 次额度 |
| 巨潮公告 | 0.5/s | 频率高会 429 |
| 新浪行情 | 5/s（批量给 50 个代码一次） | |
| 财联社电报 | 1/s | |
| 同花顺 HTML | 0.3/s | 反爬较严，加 sleep |

所有脚本都已在 `core/http.py` 实现指数退避重试，遇到瞬态错误自动恢复。
若用户报告"数据缺失"，先看 stderr 的 FAIL 行；若是同花顺的 503/429，可以加 `--retry 3` 或重跑一次。

## 5. 风险规则速查

| 规则 | 描述 | 严重度 |
|------|------|--------|
| R1 | ST / *ST 股票 | 🔴 极高 / 🟠 高 |
| R2 | 当日跌停 | 🟠 高 |
| R3 | 短期暴跌 | 🟡 中 |
| R4 | 财务恶化（ROE/净利润） | 🟡 中 |
| R5 | 主力净流出 > 5 亿 | 🟡 中 |
| R6 | 公告：立案调查 | 🔴 极高 |
| R7 | 公告：退市风险警示 | 🔴 极高 |
| R8 | 公告：风险提示 | 🟠 高 |
| R9 | 公告：重大诉讼 | 🟡 中 |

## 6. 已知限制（v1.7）

| 维度 | 状态 |
|---|---|
| K 线 / 历年高低 / 创新低创新高检测 | ✅ v1.5 已加 |
| ST 误报修复 | ✅ v1.6 已修 |
| A 股概念归属（用东财 emweb，35+ 板块） | ✅ v1.6 已修 |
| 港股公司基本信息（30+ 字段） | ✅ v1.6 已修 |
| 港股年报 PDF（CID 编码 OCR fallback） | ✅ v1.6 已加 |
| 同业横向财务对比（PE/PB/ROE） | ✅ v1.6 已加 |
| 事件时间轴（价格+公告+新闻） | ✅ v1.6 已加 |
| 中概股 SEC EDGAR 集成 | ✅ v1.6 已加 |
| 雪球登录 cookie 热门帖 | ✅ v1.6 已加（用户配 cookie 后启用） |
| 增量缓存层 | ✅ v1.6 已加（行情 4h / 公告 1h / 财报 24h） |
| CLI 统一入口 `smh` | ✅ v1.6 已加 |
| 卖方研报观点 | ⏳ v2.5 |
| 上下游图谱 LLM 抽取（替代纯正则） | ⏳ v2.5 |
| 不做 K 线图（专业图表请用雪球/同花顺 App） | 设计选择 |

## 7. 快速触发示例

```
用户："分析一下宁德时代 300750"
→ Agent 调用 analyze_company.py --symbol SZ300750，按 §2.C 模板输出

用户："今天 A 股有什么异动"
→ Agent 调用 xueqiu_market.py --board all --top 5 + fetch_market_news.py --limit 30，按 §2.A 模板输出

用户："看下半导体板块的龙头"
→ Agent 调用 scan_sector.py --sector "半导体" --top 10，按 §2.B 模板输出

用户："腾讯最近发了什么公告"
→ Agent 调用 fetch_announcements.py --symbol HK00700 --days 30，按时间倒序列出公告 + PDF 链接
```

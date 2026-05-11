# Self Skills

个人 AI Agent Skills 集合。每个 skill 是一个独立目录，内部包含一份 `SKILL.md`（供 Agent 识别和调用）以及对应的脚本、数据与配置。

目标：把重复性的"查资料 + 命令行调用"这类操作沉淀成可被 Cursor / Claude 等 AI Agent 自动匹配的技能，直接在对话里触发。

## Skills 一览

| Skill | 一句话说明 | 关键词触发 |
|-------|-----------|-----------|
| [divination](divination/) | 中国传统算卦命理 CLI：八字排盘、周易起卦（铜钱 / 时间 / 数字 / 文字）、推背图、五行、穷通宝鉴 · 滴天髓 · 子平真诠全文搜索 | 算卦、占卜、八字、周易、易经、推背图、五行、运势、用神 |
| [newsboat-news-hub](newsboat-news-hub/) | 终端新闻方案：Newsboat 配置 → RSS 源管理（中国直连版 / 代理版）→ 每日新闻汇总 → 卡片图 / Word 导出 | Newsboat、RSS、新闻汇总、daily briefing、global news |
| [stock-market-hub](stock-market-hub/) | A 股 / 港股 / 中概 市场分析中心：当日财经新闻、板块龙头与热门股、公司深度卡片（基本面 / 财报 / 公告 / 上下游 / 年报 PDF 解析） | A股、港股、中概、个股分析、板块扫描、龙头、涨幅榜、主力净流入、ST、财报、巨潮、披露易、HKEX、SEC EDGAR、财联社、雪球 |
| [stock-portfolio-copilot](stock-portfolio-copilot/) | A 股 / 港股 持仓与交易决策助手：初始持仓、成交流水、自选股、资金约束、盘中建议（buy / add / hold / trim / sell / avoid / watch） | 持仓、成本价、成交记录、自选股、仓位、加仓、减仓、清仓、交易日志 |

除了上述四个 skill，仓库内还有一份共享 Python 包 [shared/stock_core](shared/stock_core/)（`company_analysis / market_snapshot / stock_market_hub`），被两个股票 skill 共同复用——它本身不是 skill，不要把 `shared/` 链接到 `~/.cursor/skills/`。

## 目录结构

```text
self_skills/
├── README.md
├── requirements.txt              # 第三方依赖（多数脚本仅用标准库）
├── enable_proxy.sh               # 本地开发可选：临时给 shell 套代理
├── .gitignore
│
├── divination/                   # 算卦 CLI（独立 skill）
│   ├── SKILL.md
│   ├── scripts/
│   │   ├── divination            # 主 CLI（python3 脚本）
│   │   ├── base_data.py          # 天干地支 / 五行 / 纳音
│   │   ├── yijing_data.py        # 周易 64 卦
│   │   ├── tuibei_data.py        # 推背图 60 象
│   │   ├── qiongtong_data.py     # 穷通宝鉴 120 条
│   │   └── classics_data.py      # 滴天髓 + 子平真诠原文
│   ├── data/divination.db        # 首次运行自动构建（已 .gitignore）
│   └── references/commands.md    # 完整命令参数手册
│
├── newsboat-news-hub/            # 终端新闻 hub（独立 skill）
│   ├── SKILL.md
│   ├── config                    # Newsboat 主配置（vim 键位 + 4 个分类宏）
│   ├── urls-china                # 中国大陆直连源（28 条）
│   ├── urls-full                 # 代理 / 海外环境完整源（36 条）
│   └── scripts/
│       ├── setup.sh              # 一键部署配置到 ~/.newsboat/
│       ├── fetch_news.py         # 并行抓 RSS → 日期窗口过滤 → 分类 JSON
│       ├── generate_cards.py     # JSON → 小红书风格新闻卡片图（playwright）
│       └── md_to_docx.py         # 汇总 markdown → Word（python-docx）
│
├── stock-market-hub/             # 股票分析中心（独立 skill）
│   ├── SKILL.md
│   ├── bin/smh                   # 统一 CLI 入口（company / sector / news / market / risk / ann / timeline / pdf / supply / cache）
│   ├── scripts/
│   │   ├── analyze_company.py    # 公司深度卡片（A 股 / 港股 / 中概）
│   │   ├── scan_sector.py        # 板块扫描（龙头 + 热门 + 风险）
│   │   ├── xueqiu_market.py      # 雪球涨跌榜 / 主力 / 散户热度
│   │   ├── fetch_market_news.py  # 财联社 / 雪球当日新闻流
│   │   ├── fetch_announcements.py# 巨潮 / 披露易公告
│   │   ├── event_timeline.py     # 价格 + 公告 + 新闻 三流时间轴
│   │   ├── pdf_extract.py        # 年报 / 业绩公告 PDF 关键章节抽取
│   │   ├── supply_chain.py       # 上下游图谱
│   │   ├── risk_scan.py          # ST / 立案 / 连续跌停等风险规则扫描
│   │   ├── company_api.py        # 给外部 skill 用的薄封装
│   │   └── core/                 # HTTP（curl_cffi + Chrome 指纹）、缓存、限频
│   ├── agents/openai.yaml        # Agent 元信息
│   ├── data/xueqiu.cookie.example
│   └── references/
│       ├── data_sources.md       # 数据源全景与限频说明
│       └── v2_roadmap.md
│
├── stock-portfolio-copilot/      # 持仓与交易决策助手（独立 skill）
│   ├── SKILL.md
│   ├── bin/spc                   # CLI 入口
│   ├── scripts/
│   │   ├── main.py               # 子命令分发
│   │   └── spc_core/             # db / ledger / portfolio / decision / market_bridge / settings
│   ├── agents/openai.yaml
│   └── tests/test_spc.py
│
└── shared/                       # 跨 skill 共享代码（非 skill）
    └── stock_core/
        ├── company_analysis.py   # 公司分析共享内核
        ├── market_snapshot.py    # 市场快照
        └── stock_market_hub.py   # 桥接 stock-market-hub
```

## 快速开始

### 1. 克隆

```bash
git clone https://github.com/yanglei9211/self_skills.git
cd self_skills
```

### 2. 准备运行环境

`divination` 和 `newsboat-news-hub/fetch_news.py` 只用 Python 3.8+ 标准库，**clone 完即可使用**。

需要 venv 的场景（两个股票 skill + newsboat 的卡片图 / docx 导出）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 仅当要生成新闻卡片图
.venv/bin/playwright install chromium

# 仅当要做港股年报 OCR（pytesseract / pdf2image 走系统二进制）
brew install tesseract tesseract-lang poppler          # macOS
# sudo apt install tesseract-ocr poppler-utils         # Debian / Ubuntu
```

两个股票 skill 的 `bin/smh` 和 `bin/spc` 默认走 **同一个** `self_skills/.venv/bin/python3`，所以一份 venv 同时服务它们。

可选的外部凭证 / UA：

```bash
# 中概股查 SEC EDGAR 时建议带合规 UA
export SEC_USER_AGENT="your-name your-email@example.com"

# 雪球热门帖 / 个股新闻流（需要登录 cookie）
mkdir -p ~/.config/stock-market-hub
cp stock-market-hub/data/xueqiu.cookie.example ~/.config/stock-market-hub/xueqiu.cookie
# 然后把浏览器里登录后的 xueqiu cookie 字符串粘进去
```

### 3. 在 Cursor / Claude 中启用 skill

**规则：skill 的真身永远在本仓库里，`~/.cursor/skills/` 下只放软链接** —— 这样改代码立刻生效，不会出现"两份代码漂移"。

```bash
# Cursor 客户端
ln -s "$(pwd)/divination"               ~/.cursor/skills/divination
ln -s "$(pwd)/newsboat-news-hub"        ~/.cursor/skills/newsboat-news-hub
ln -s "$(pwd)/stock-market-hub"         ~/.cursor/skills/stock-market-hub
ln -s "$(pwd)/stock-portfolio-copilot"  ~/.cursor/skills/stock-portfolio-copilot

# Claude Code 把上面的 ~/.cursor/skills/ 换成 ~/.claude/skills/ 即可

# 注意：shared/ 是共享代码，不要软链
```

之后在对话里说"帮我算一下八字"、"生成今日全球新闻汇总"、"分析一下宁德时代 300750"、"同步我的持仓"，Agent 会自动匹配并调用对应 skill。

### 4. 直接命令行使用

```bash
# 算卦
python3 divination/scripts/divination cast --method coin --pretty
python3 divination/scripts/divination bazi --birth "1990-05-15 14:30" --pretty

# 新闻汇总（标准库即可）
python3 newsboat-news-hub/scripts/fetch_news.py --date 2026-05-10 --tz ET --retry-failed 3 > news.json

# 股票分析（需 .venv）
./stock-market-hub/bin/smh company SZ300750
./stock-market-hub/bin/smh sector "AI PC" --top 10
./stock-market-hub/bin/smh market --board gainers --top 10

# 持仓（需 .venv）
./stock-portfolio-copilot/bin/spc position init --market a --code 300750 --qty 1000 --cost 245.30
./stock-portfolio-copilot/bin/spc portfolio sync
./stock-portfolio-copilot/bin/spc analyze now --scope holdings
```

## 依赖一览

| 脚本 / 工具 | 第三方依赖 | 备注 |
|---|---|---|
| `divination/scripts/divination` | 无 | Python 3.8+ 标准库 |
| `newsboat-news-hub/scripts/fetch_news.py` | 无 | 标准库（`urllib` / `xml.etree` / `concurrent.futures`） |
| `newsboat-news-hub/scripts/generate_cards.py` | `playwright` | 还需 `playwright install chromium` |
| `newsboat-news-hub/scripts/md_to_docx.py` | `python-docx` | 仅在用户明确"导出 Word"时调用 |
| Newsboat 本身 | `brew install newsboat` / `apt install newsboat` | macOS / Linux |
| `stock-market-hub/**` | `akshare` `pdfplumber` `curl_cffi` `pandas` `lxml` `pytesseract` `pdf2image` | 系统层另需 `tesseract` + `poppler`（港股年报 OCR fallback） |
| `stock-portfolio-copilot/**` | 同上（通过 `shared/stock_core` 复用 stock-market-hub） | 持仓数据库存在 `~/.local/share/stock-portfolio-copilot/portfolio.db`，可用 `SPC_DATA_DIR` 覆盖 |

## 内部约定 / 开发规范

- **skill 布局**：每个 skill 目录根必须有一份 `SKILL.md`，第一段带 YAML frontmatter（`name` + `description`），description 里要写明触发关键词供 Agent 匹配。
- **真身 + 软链**：skill 的代码永远在本仓库里维护、提交，`~/.cursor/skills/<name>` / `~/.claude/skills/<name>` 必须是软链接，不允许是真实目录或复制。改造已有真实目录的步骤见 `.cursor/rules/skills-layout.mdc`。
- **依赖最小化**：能用标准库就不引第三方。必须引入时在 `requirements.txt` 里注明"服务于哪个脚本"。
- **共享代码**：跨 skill 的可复用 Python 模块放 `shared/stock_core/` 这样的目录，由各 skill 内的脚本 `import`，不要把同样的代码各拷一份。
- **产物不入库**：数据库（`*.db`）、抓取结果（`news_*.json`）、卡片图（`*.png`）等已在 `.gitignore` 中屏蔽。
- **凭证 / token 一律不进仓**：`.env` / `*.token` / `*.key` / `secrets/` 已被 `.gitignore` 兜底。雪球 cookie、SEC UA 这种走 `~/.config/<skill>/` 或环境变量，不要写进仓库。
- **数据溯源**：股票相关分析必须给来源链接（巨潮 / 披露易 / 雪球 / 财联社 …）；新闻汇总必须使用 RSS 原始 `link` 字段，不允许截断或省略号。具体反例见各自 `SKILL.md`。

## License

仅供个人学习使用。其中命理 / 新闻 / 公告 / 财报数据版权归原作者所有，本仓库只做格式整理与工具封装。

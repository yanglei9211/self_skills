# Self Skills

个人 AI Agent Skills 集合。每个 skill 是一个独立目录，内部包含一份 `SKILL.md`（供 Agent 识别和调用）以及对应的脚本、数据与配置。

目标：把重复性的"查资料 + 命令行调用"这类操作沉淀成可被 Cursor / Claude 等 AI Agent 自动匹配的技能，直接在对话里触发。

## Skills 一览

| Skill | 一句话说明 | 关键词触发 |
|-------|-----------|-----------|
| [divination](divination/) | 中国传统算卦命理 CLI：八字排盘、周易起卦（铜钱/时间/数字/文字）、推背图查询、穷通宝鉴·滴天髓·子平真诠全文搜索 | 算卦、占卜、八字、周易、易经、推背图、五行、运势 |
| [newsboat-news-hub](newsboat-news-hub/) | 终端新闻阅读方案：Newsboat 配置 → RSS 源管理（中国直连版 / 代理版）→ 每日新闻汇总 → 小红书风格卡片图生成 | Newsboat、RSS、新闻汇总、daily briefing |

## 目录结构

```text
self_skills/
├── README.md
├── requirements.txt          # 第三方依赖（大多数脚本仅用标准库）
├── .gitignore
│
├── divination/               # 算卦 CLI
│   ├── SKILL.md              # Agent 入口：命令说明、工作流、引用格式
│   ├── scripts/
│   │   ├── divination        # 主 CLI（python3 脚本）
│   │   ├── base_data.py      # 基础数据（天干地支/五行/纳音等）
│   │   ├── yijing_data.py    # 周易 64 卦数据
│   │   ├── tuibei_data.py    # 推背图 60 象
│   │   ├── qiongtong_data.py # 穷通宝鉴 120 条
│   │   └── classics_data.py  # 滴天髓、子平真诠原文
│   ├── data/
│   │   └── divination.db     # SQLite 数据库（首次运行自动构建，已在 .gitignore）
│   └── references/
│       └── commands.md       # 完整命令参数手册
│
└── newsboat-news-hub/
    ├── SKILL.md              # Agent 入口：安装、部署、抓取、汇总流程
    ├── config                # Newsboat 主配置（键位、宏、刷新策略等）
    ├── urls-china            # 中国大陆可直连的 RSS 源（28 条）
    ├── urls-full             # 海外 / 代理网络下的完整 RSS 源（33 条）
    └── scripts/
        ├── setup.sh          # 一键部署配置到 ~/.newsboat/
        ├── fetch_news.py     # 并行抓取 RSS → 自动分类 → 输出 JSON
        └── generate_cards.py # JSON → 小红书风格新闻卡片图（需 playwright）
```

## 快速开始

### 1. 克隆与安装

```bash
git clone https://github.com/yanglei9211/self_skills.git
cd self_skills

# 大部分脚本仅依赖 Python 3 标准库，无需安装即可使用
# 如需使用 newsboat-news-hub 的卡片生成功能：
pip install -r requirements.txt
playwright install chromium
```

### 2. 在 Cursor / Claude 中启用 skill

把对应 skill 目录链接到本地 skills 目录（路径以 Cursor 为例，Claude Code 请替换为 `~/.claude/skills/`）：

```bash
# 算卦
ln -s "$(pwd)/divination" ~/.cursor/skills/divination

# 新闻
ln -s "$(pwd)/newsboat-news-hub" ~/.cursor/skills/newsboat-news-hub
```

之后在对话里说"帮我算一下八字"或"生成今日新闻汇总"，Agent 会自动匹配并调用。

### 3. 直接命令行使用

```bash
# 起一个卦
python3 divination/scripts/divination cast --method coin --pretty

# 抓一批新闻
python3 newsboat-news-hub/scripts/fetch_news.py > news.json
```

## 依赖说明

| 脚本 | 第三方依赖 |
|------|-----------|
| `divination/scripts/divination` | 无，仅 Python 3.8+ 标准库（`sqlite3` / `argparse` / `hashlib` / `random` 等）|
| `newsboat-news-hub/scripts/fetch_news.py` | 无，仅标准库（`urllib` / `xml.etree` / `concurrent.futures`）|
| `newsboat-news-hub/scripts/generate_cards.py` | `playwright`（+ `playwright install chromium`）|
| Newsboat 本身 | `brew install newsboat` / `apt install newsboat` |

## 开发约定

- 每个 skill 目录根必须有一份 `SKILL.md`，第一段带 YAML frontmatter（`name` + `description`），description 里需列出触发关键词供 Agent 匹配。
- 脚本尽量只用标准库，避免给 Agent 环境增加安装成本。必须引入第三方依赖时，在 `requirements.txt` 中注明"服务于哪个脚本"。
- 产物（数据库、抓取结果、卡片图等）不提交到仓库，已在 `.gitignore` 中屏蔽。
- 涉及用户凭证（token / key / .env 等）一律不进仓库，`.gitignore` 已兜底。

## License

仅供个人学习使用。其中命理/新闻数据版权归原作者所有，本仓库仅做格式整理与工具封装。

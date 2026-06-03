# newsboat-news-hub

终端新闻阅读和每日新闻汇总方案。包含 Newsboat 配置、RSS 源列表、新闻抓取脚本、卡片图生成和 Markdown 转 Word 工具。

## 适用场景

- 安装和配置 Newsboat。
- 管理美国、国际、科技、财经等 RSS 源。
- 生成每日新闻 JSON 或 Markdown 汇总。
- 在有代理或中国大陆直连环境中选择不同 RSS 源列表。

## 不适用场景

- 不负责实时金融行情或股票公告分析。
- 不绕过付费墙；RSS 内容以源站公开 feed 为准。

## 快速开始

```bash
# macOS
brew install newsboat

# 部署配置
mkdir -p ~/.newsboat
cp newsboat-news-hub/config ~/.newsboat/config
cp newsboat-news-hub/urls-china ~/.newsboat/urls

# 启动
newsboat
```

有稳定代理时可改用完整源：

```bash
cp newsboat-news-hub/urls-full ~/.newsboat/urls
```

## 生成新闻汇总

```bash
# 最近一天，美国东部时间窗口
python3 newsboat-news-hub/scripts/fetch_news.py --tz ET > news.json

# 指定日期
python3 newsboat-news-hub/scripts/fetch_news.py --date 2026-05-10 --tz ET > news.json
```

## 主要文件

| 文件 | 说明 |
|---|---|
| `config` | Newsboat 主配置，包含 vim 风格键位和分类宏 |
| `urls-china` | 中国大陆直连 RSS 源 |
| `urls-full` | 有代理或海外网络下的完整 RSS 源 |
| `scripts/setup.sh` | 一键部署配置到 `~/.newsboat/` |
| `scripts/fetch_news.py` | 抓取 RSS 并输出 JSON |
| `scripts/generate_cards.py` | 将新闻 JSON 渲染成卡片图 |
| `scripts/md_to_docx.py` | 将 Markdown 汇总转为 Word |

## 依赖与配置

- `fetch_news.py` 仅使用 Python 标准库。
- 生成卡片图需要 `playwright` 和 Chromium：

```bash
pip install -r requirements.txt
playwright install chromium
```

- 导出 Word 需要 `python-docx`。
- Newsboat 本体需要系统安装：`brew install newsboat`、`apt install newsboat` 或 `pacman -S newsboat`。

## 数据与产物

- 抓取结果常见为 `news_cn.json`、`news_full.json`，已在 `.gitignore` 中排除。
- 卡片图输出到 `cards/`，默认不入库。

## 注意事项

- 源可达性会随网络环境变化；新增源前先用 `curl -L --max-time 8` 测试。
- 新闻汇总必须保留 RSS 原始 `link` 字段，不要输出无法追溯的摘要。

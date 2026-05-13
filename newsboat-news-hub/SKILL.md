---
name: newsboat-news-hub
description: >-
  安装配置 Newsboat 终端新闻阅读器，管理美国及国际主流媒体 RSS 源，生成每日新闻汇总。
  当用户提到 Newsboat、RSS、新闻订阅、新闻汇总、全球新闻、美国新闻、终端阅读新闻、
  news feeds、news summary、daily briefing 时使用。
---

# Newsboat 新闻中心

一套完整的终端新闻阅读方案：Newsboat 安装 → 配置 → RSS 源管理 → 每日新闻汇总生成。

## 1. 安装

```bash
# macOS
brew install newsboat

# Ubuntu / Debian
sudo apt install newsboat

# Arch
sudo pacman -S newsboat
```

## 2. 部署配置

将本 skill 自带的配置文件写入用户目录：

```bash
mkdir -p ~/.newsboat
```

将 [config](config) 写入 `~/.newsboat/config`，将 [urls-china](urls-china) 写入 `~/.newsboat/urls`。

如果用户有代理（VPN/Clash/V2Ray），改用 [urls-full](urls-full) 并在 config 中追加代理设置：

```
proxy http://127.0.0.1:7890
proxy-type http
```

端口号根据用户实际代理替换（Clash 默认 7890，V2Ray 常见 1080）。SOCKS5 用：

```
proxy socks5h://127.0.0.1:7890
proxy-type socks5
```

## 3. 源选择策略

本 skill 提供两套 RSS 源文件：

| 文件 | 适用场景 | 源数量 |
|------|---------|--------|
| [urls-china](urls-china) | **中国大陆直连**（无需代理） | 28 源 |
| [urls-full](urls-full) | **有代理** 或海外网络 | 36 源 |

### 中国大陆可直连的域名（2026.4 实测）

```
✓ apnews.com          ✓ feeds.npr.org        ✓ abcnews.go.com
✓ cbsnews.com         ✓ rss.politico.com     ✓ thehill.com
✓ feeds.foxnews.com   ✓ cnbc.com             ✓ feeds.content.dowjones.io (MarketWatch)
✓ feeds.arstechnica.com ✓ theverge.com        ✓ feeds.skynews.com
✓ france24.com
```

### 中国大陆不可达的域名（被墙）

```
✗ rss.nytimes.com      ✗ feeds.washingtonpost.com
✗ feeds.bbci.co.uk     ✗ wsj.com
✗ feeds.bloomberg.com  ✗ theguardian.com
✗ reutersagency.com (已下线 RSS)
✗ rsshub.app           ✗ feedx.net
✗ news.google.com（token URL 无法直连访问）
```

### 新增/排查源的流程

当用户想添加新源或已有源失效时：

```bash
# 测试单个源是否可达
curl -s -o /dev/null -w "HTTP %{http_code}" -L --max-time 8 "RSS_URL"

# 批量测试（并行）
test_url() {
  local url="$1" name="$2"
  code=$(curl -s -o /dev/null -w "%{http_code}" -L --max-time 5 --connect-timeout 3 "$url" 2>/dev/null)
  [ $? -ne 0 ] && echo "✗ $name" || echo "✓ $name → HTTP $code"
}
test_url "https://example.com/rss" "Example" &
wait
```

退出码含义：6=DNS 失败（域名错误），28=超时（被墙），35=SSL 错误，56=连接重置。

## 4. 使用 Newsboat

```bash
newsboat              # 启动
newsboat --tui        # 如果版本支持 TUI
```

### 键位速查（vim 风格，config 已配好）

| 操作 | 按键 |
|------|------|
| 上/下 | `k`/`j` |
| 打开/返回 | `l`/`h` |
| 浏览器打开 | `o` |
| 下一篇未读 | `n` |
| 刷新全部 | `R` |
| 刷新当前 | `r` |
| 标记全部已读 | `A` |
| 搜索 | `/` |
| 按标签过滤 | `t` |
| 退出 | `q` |

### 宏（config 已配好）

| 宏 | 功能 |
|----|------|
| `,p` | 只看政治新闻 |
| `,w` | 只看国际新闻 |
| `,t` | 只看科技新闻 |
| `,b` | 只看财经新闻 |

## 5. 生成每日新闻汇总

当用户要求生成新闻汇总时，执行 [scripts/fetch_news.py](scripts/fetch_news.py)。

### 命令选择（按用户意图）

```bash
# 用户说"今天的新闻"、"最近一天的新闻"
python3 SKILL_DIR/scripts/fetch_news.py --tz ET

# 用户说"4月20日的新闻" / "昨天的新闻" / 某个具体日期
# ★ 必须使用 --date 严格过滤，不要用默认 24h 窗口 "估计"
python3 SKILL_DIR/scripts/fetch_news.py --date 2026-04-20 --tz ET

# 多日区间
python3 SKILL_DIR/scripts/fetch_news.py --since 2026-04-18 --until 2026-04-21 --tz ET

# 时区按用户语境选：美东新闻 → ET（默认）；国内新闻 → CN（= Asia/Shanghai）
python3 SKILL_DIR/scripts/fetch_news.py --date 2026-04-20 --tz CN

# 遇到 AP / Fox 等源返回 429 / 403 / 5xx 时自动指数退避重试（推荐日常加上）
python3 SKILL_DIR/scripts/fetch_news.py --date 2026-04-20 --tz ET --retry-failed 3
```

### 失败重试（`--retry-failed`）

- `--retry-failed N`：对**可重试错误**（HTTP 429 / 403 / 408 / 425 / 5xx、timeout、URLError）最多重试 N 次。默认 0（不重试，向后兼容）。
- 退避策略：指数退避 `base * 2^attempt + jitter`，如果服务器在 429 响应中带 `Retry-After`，则尊重该头（上限 60s）。
- **不会重试的错误**：XML 解析失败（源本身返回坏 XML）、HTTP 401 / 404 —— 这类错误重试也没用，脚本会直接标 FAIL。
- 启用重试后，并发数自动从 16 降到 8，避免对同一个正在退避的域名继续施压。
- **推荐默认加 `--retry-failed 3`**，尤其是用户明确希望"尽量补齐来源"时。AP 美联社 429 是常态，Fox News 偶尔 403，一次重试通常能救回来。
- 代价：启用后单次运行可能从 ~5s 延长到 60s+（取决于服务器的 `Retry-After`），用户时间紧迫时可以不加。

### 日期过滤硬约束

- **只要用户说出具体日期**（含"昨天"/"今天 ET"/"周一"等可换算的说法），**必须加 `--date YYYY-MM-DD`**，不得省略。
- 过滤窗口：`[--date 00:00, 次日 00:00)`，时区由 `--tz` 决定（默认 `America/New_York`）。
- 用户没明示时区但在讨论美国新闻 → `--tz ET`；讨论国内新闻 → `--tz CN`；讨论欧洲新闻 → 按具体国家（`Europe/London` / `Europe/Paris` 等）。
- stderr 会打印 `in_window=N  before=X  after=Y` 统计，**必须在汇总末尾的"抓取说明"里把这几个数字告诉用户**，用于交代数据覆盖情况。
- **RSS 的天然限制**：绝大多数源只保留最近 24–48 小时内容，请求超过 2 天之前的日期会返回几乎空的结果。遇到这种情况要主动告诉用户原因，而不是硬凑。

脚本行为：
1. 并行抓取所有 urls 文件中的 RSS 源（每源最多 60 条，启用日期过滤时）
2. 解析标题、来源、链接、`pubDate`
3. **按 `--date` / `--since`/`--until` + `--tz` 严格过滤**（不在窗口的直接丢弃，除非显式 `--allow-no-date`）
4. 按主题自动分类（18 个类别）、去重
5. 输出 JSON 到 stdout，每条含 `title / source / link / pubDate`

### 输出格式硬约束（索引）

> 📜 **完整模板、语言要求、URL 反例、正例/反例对照见
> [references/output-format.md](references/output-format.md)。**
> 生成汇总前**必须**先读那份文件，否则容易踩 URL 截断 / 直接粘英文原标题 / 链接占位等坑。

最关键的 4 条索引（详细见上面 reference）：

1. **默认中文输出**，每条由你翻译改写，不要直接粘 JSON 里的英文原标题；
   人名 / 机构名 / 产品名保留英文。
2. **链接必须完整**，**禁止**在 URL 中使用 `...` / `[省略]` / `xxx`；
   `news.google.com/rss/articles/CBMi...` 这种写法点开必然 404。
3. **每个主题 5–8 条**按重要性排序；多家媒体同一事件合并为一条，多链接都列出。
4. **末尾追加"抓取说明"**：`--date / --tz` 实际参数、`in_window / before / after / no_date`
   统计、失败的源（如 AP 经常 429）。

## 6. 导出为 Word（.docx）—— 可选，仅在用户明确要求时使用

**默认流程就是把汇总直接在对话里输出**，**不要**主动生成 .docx 文件。只有当用户明确说"导出 Word / 导成 docx / 存成文档 / 下载下来"等意图时，才执行这一步。

### 用法

先把第 5 节生成的汇总（含完整链接、中文摘要、抓取说明）写入一份 markdown 文件，然后转换：

```bash
# 1. 把汇总 markdown 落盘（例：当天日期 + 时区）
cat > /tmp/news_2026-04-21_ET.md <<'MDEOF'
# 2026 年 4 月 21 日（周二，美东时间）全球新闻汇总

...（第 5 节生成的完整汇总正文）...
MDEOF

# 2. 转成 docx（默认存到工作区 exports/ 下）
mkdir -p exports
python3 SKILL_DIR/scripts/md_to_docx.py \
  /tmp/news_2026-04-21_ET.md \
  exports/news_2026-04-21_ET.docx
```

### 脚本行为

- 依赖：`python-docx`（`pip install python-docx`，大多数 Python 环境已预装）
- 输入：任何上述格式约束下生成的 markdown 文件
- 输出：.docx 文件，包含：
  - `# / ## / ###` 映射为 Word 的 Heading 1/2/3（可在导航面板里跳转）
  - `[text](url)` 保留为**真正的 Word 超链接**（蓝色下划线、Ctrl/Cmd+点击跳转），URL 原样粘贴不截断
  - `**bold**`、`` `code` ``（等宽字体）、`>` 引用块
  - 中文字体：macOS 上 PingFang SC；Windows Word 会自动回退到默认东亚字体
- 生成后**只向用户报告文件路径 + 大小**（如 `57 KB / 99 条链接`），**不要**把 docx 的内容再重复贴回聊天框

### Agent 行为约束

1. **不要预先生成 docx**：用户不提"导出"就别做。不要在对话里问"要不要导出 Word？"—— 除非聊天流程自然需要。
2. **不要在聊天框里重复内容**：导出成功后一句话回报路径即可，用户自己打开。
3. **如果 markdown 没落盘**：先让 md 内容落到 `/tmp/news_<日期>_<tz>.md`，再转 docx，不要试图直接把聊天框里的内容"截出来"。

## 7. 媒体立场参考

写汇总时注明来源的政治光谱位置，帮助读者交叉阅读：

```
← 左                                                    右 →
NPR   WaPo   NYT   CNN   ABC/CBS   Politico   WSJ   Fox News
```

| 定位 | 媒体 |
|------|------|
| 通讯社（最中立） | AP 美联社 |
| 偏左 | NPR |
| 中间 | ABC News, CBS News |
| 政治专业 | Politico, The Hill |
| 偏右 | Fox News |
| 财经 | CNBC, MarketWatch |
| 科技 | Ars Technica, The Verge |
| 国际第三方 | Sky News（英）, France 24（法） |
| 聚合 | Google News（收录 NYT/WaPo/CNN 等） |

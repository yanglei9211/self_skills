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
| [urls-full](urls-full) | **有代理** 或海外网络 | 33 源 |

### 中国大陆可直连的域名（2026.4 实测）

```
✓ apnews.com          ✓ news.google.com     ✓ feeds.npr.org
✓ abcnews.go.com      ✓ cbsnews.com         ✓ rss.politico.com
✓ thehill.com          ✓ feeds.foxnews.com   ✓ cnbc.com
✓ feeds.content.dowjones.io (MarketWatch)
✓ feeds.arstechnica.com ✓ theverge.com
✓ feeds.skynews.com    ✓ france24.com
```

### 中国大陆不可达的域名（被墙）

```
✗ rss.nytimes.com      ✗ feeds.washingtonpost.com
✗ feeds.bbci.co.uk     ✗ wsj.com
✗ feeds.bloomberg.com  ✗ theguardian.com
✗ reutersagency.com (已下线 RSS)
✗ rsshub.app           ✗ feedx.net
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

当用户要求生成新闻汇总时，执行 [scripts/fetch_news.py](scripts/fetch_news.py)：

```bash
python3 SKILL_DIR/scripts/fetch_news.py
```

脚本会：
1. 并行抓取所有 urls 文件中的 RSS 源
2. 解析标题、来源、链接
3. 按主题自动分类（伊朗/中东、美国政治、中国、科技/AI 等 18 个类别）
4. 去重后输出 JSON 到 stdout

拿到 JSON 后，按以下模板组织汇总：

```
# YYYY年M月D日（周X）全球新闻汇总

## 一、[最重要主题]
- **标题摘要**。 — [来源](链接)

## 二、[第二重要主题]
...
```

要求：
- 每个主题下最多 5-8 条，按重要性排序
- 每条必须带 `— [来源名](原文链接)` 引用
- Google News 的标题通常包含 ` - 来源名`，提取真实来源展示
- 多家媒体报道同一事件时合并为一条，列出多个来源
- 用中文撰写摘要，保留人名/机构名英文原文

## 6. 媒体立场参考

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

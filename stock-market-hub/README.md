# stock-market-hub

A 股、港股和中概股市场分析中心。提供当日市场新闻、板块扫描、风险扫描、公司深度卡片、公告、PDF 年报解析、上下游和主力资金流分析。

## 适用场景

- 查看 A 股、港股、中概股当日市场速览。
- 扫描板块龙头、热门股、涨跌榜、风险股。
- 分析单个公司基本面、财报、公告、上下游、近期事件。
- 获取财联社、雪球、巨潮、披露易、SEC EDGAR 等公开来源数据。

## 不适用场景

- 不提供投资建议或自动交易。
- 不保证所有数据源实时可用；公开接口可能限频、变更或被反爬。
- 东方财富部分接口已知反爬严重，当前实现不依赖它作为核心路径。

## 快速开始

```bash
cd /path/to/self_skills
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

SMH=./stock-market-hub/bin/smh
$SMH company SZ300750
$SMH sector "AI PC" --top 10
$SMH market --board gainers --top 10
```

## 主要命令

| 功能 | 示例 |
|---|---|
| 公司深度卡片 | `smh company SZ300750` |
| 板块扫描 | `smh sector "AI PC" --top 10` |
| 市场速览 | `smh market --board gainers --top 10` |
| 风险扫描 | `smh risk --rules R1,R2,R5` |
| 公告查询 | `smh ann SZ300750 --days 60` |
| 事件时间轴 | `smh timeline HK01810 --days 30` |
| PDF 解析 | `smh pdf URL --sections business,risks` |
| 上下游 | `smh supply SZ300750` |
| 主力资金流 | `smh flow SZ300750` |
| 大盘风险偏好 | `smh regime --market hk` |

## 依赖与配置

Python 依赖在根 `requirements.txt` 中维护：

- `akshare`
- `pdfplumber`
- `curl_cffi`
- `pandas`
- `lxml`
- `pytesseract`
- `pdf2image`

可选系统依赖：

```bash
brew install tesseract tesseract-lang poppler
```

可选环境变量和配置：

```bash
export SEC_USER_AGENT="your-name your-email@example.com"
mkdir -p ~/.config/stock-market-hub
# 可选：写入雪球登录 cookie
```

## 数据与产物

- 缓存和抓取结果为本地产物，不应提交。
- 雪球 cookie、SEC UA 等凭证类配置不入库。
- 数据源说明见 `references/data_sources.md`。

## 注意事项

- 输出必须保留数据来源链接，尤其是公告、新闻、财报和 SEC 文件。
- 公司分析不等于投资建议；需要明确区分事实、推断和风险。
- 如果某个数据源失败，应说明失败原因和替代来源，不要静默编造。

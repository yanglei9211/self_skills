# 数据源对照表（2026-04 实测）

本文件记录 `stock-market-hub` 用到的全部公开数据源、接口、限频、已知限制、备用方案。
新增/排查源时请同步更新本表。

## 测试基线

- 出口 IP：中国大陆（北京联通）
- 客户端：`curl_cffi` + `impersonate="chrome"` 模拟 Chrome TLS 指纹
- 普通 `requests` / `curl` 在大量源上会被反爬识别（HTTP 000 / 立刻断连），**必须**统一走 `curl_cffi`

## 一、可用源

### 1. 行情快照（实时报价、延迟 0–60s）


| 源        | 域名                    | 接口                                            | 备注                                                         |
| -------- | --------------------- | --------------------------------------------- | ---------------------------------------------------------- |
| **新浪财经** | `hq.sinajs.cn`        | `GET /list=sh600519,sz000001,hk00700,gb_baba` | A股+港股+美股一把抓，**首选**。返回 JS 字符串 `var hq_str_xxx="..."`，逗号分隔字段 |
| **腾讯财经** | `qt.gtimg.cn`         | `GET /q=sh600519,sz000001,hk00700,usBABA`     | 备份。返回 GBK 编码 `v_xxx="...";`                                |
| ❌ 东方财富   | `push2.eastmoney.com` | `/api/qt/clist/get`                           | **不可用**：服务端立即断开（IP 段被反爬封禁）。所有 akshare 中走 push2 的接口都受影响     |
| ✅ 东方财富资金流 | `push2his.eastmoney.com` | `/api/qt/stock/fflow/daykline/get` | **可用**（与上面被封的 push2 是不同 host）。`secid={1\|0\|116}.{code}`，返回逗号分隔的 ~120 个交易日逐日资金流。详见下方"主力资金流"小节 |


#### 新浪行情字段（A股，从 0 索引）

```
0=name 1=open 2=prev_close 3=last 4=high 5=low 6=bid 7=ask
8=volume(股) 9=amount(元) 10..18=买1-5量价 19..28=卖1-5量价 29=date 30=time
```

#### 新浪行情字段（港股 `hk*`）

```
0=name_en 1=name_cn 2=open 3=prev 4=high 5=low 6=last 7=change 8=pct
9=bid 10=ask 11=amount(港元) 12=volume(股) 13=pe 14=yield 15=high_52w 16=low_52w
17=date 18=time
```

#### 美股 `gb_<ticker>`（小写 ticker）

```
0=name 1=last 2=pct 3=time 4=change 5=open 6=high 7=low 8=prev 9=amount(美元) 10=volume
```

### 2. 板块数据（行业/概念）


| 源       | 接口                                                                                     | 备注                |
| ------- | -------------------------------------------------------------------------------------- | ----------------- |
| **同花顺** | `https://q.10jqka.com.cn/thshy/`（行业）`/gn/`（概念）                                         | HTML 表格，需 lxml 解析 |
| 同花顺板块成分 | `https://q.10jqka.com.cn/<板块代码>/`                                                      | 例 `881101`（半导体）   |
| 新浪板块    | `https://vip.stock.finance.sina.com.cn/q/go.php/vIndustryAnalysis/kind/sw/index.phtml` | 申万行业，HTML         |


### 3. 公告（权威披露源）


| 源            | 域名                  | 接口                                   | 备注                                                                                                                    |
| ------------ | ------------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| **巨潮资讯**（A股） | `www.cninfo.com.cn` | `POST /new/hisAnnouncement/query`    | 法定披露源。POST 表单参数：`stock`（代码,内部代码）`pageNum, pageSize, column=szse                                                       |
| **披露易**（港股）  | `www1.hkexnews.hk`  | `POST /search/titleSearchServlet.do` | 法定披露源。需要 `lang=ZH`、`category`、`subcategory` 参数；返回 JSON。或 RSS：`https://www.hkex.com.hk/eng/sitefiles/rss/all_news.xml` |
| 港交所新闻 RSS    | `www.hkex.com.hk`   | `/Services/RSS-Feeds`                | 官方 RSS 多个频道                                                                                                           |


#### 巨潮 column 映射

```
sse  = 上交所（6 开头股票）
szse = 深交所（0/3 开头股票）
hke  = 港股
neeq = 新三板
```

#### 巨潮内部代码

巨潮要求 `stock=000001,9900001611` 的格式，前面是股票代码，后面是 6 位交易所内部代码 + 4 位补 0。
查内部代码：`http://www.cninfo.com.cn/new/data/szse_stock?keyWord=000001`

### 4. 财经新闻


| 源                    | 接口                                                                                         | 备注                                                                                                                                                                       |
| -------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **财联社电报**            | `https://www.cls.cn/nodeapi/updateTelegraphList?app=CailianpressWeb&os=web&sv=7.7.5&rn=30` | JSON，分钟级更新，最权威的 A 股新闻流。`level=A` 是重要电报（红色）                                                                                                                               |
| 财联社头条                | `https://www.cls.cn/nodeapi/telegraphList?...&category=red`                                | 仅红色重磅                                                                                                                                                                    |
| **新浪财经滚动**           | `https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=1686&num=50&page=1`              | JSON，覆盖更广                                                                                                                                                                |
| 雪球热门帖 ⚠️             | `https://xueqiu.com/statuses/hot/listV2.json?since_id=-1&size=20`                          | 阿里云 WAF JS 挑战，匿名访问被拦。要稳定使用需用户提供已登录 `xq_a_token` Cookie，或自建 RSSHub Docker。**v1 暂不启用**。                                                                                    |
| **雪球 Screener** ✅    | `https://xueqiu.com/service/v5/stock/screener/quote/list`                                  | **公开 API，无需登录！** 仅需 `acw_tc`（首次访问 xueqiu.com 自动获取）。覆盖全 A / 港 / 美 / 创业板 / 科创板 / ST 等市场，字段超全（PE/PB/PS/ROE/换手率/北向资金/主力净流入/雪球关注者数等 30+），可按任意字段排序筛选。是 stock-market-hub 的核心数据源 |
| 雪球行情快照 ✅             | `https://stock.xueqiu.com/v5/stock/realtime/quotec.json?symbol=SH600519,HK00700`           | 公开，A+港+美统一接口                                                                                                                                                             |
| 新浪财经 RSS             | `https://rss.sina.com.cn/finance/stock/all.xml`                                            | **2026.4 实测路径变了**（404），改用上面的 JSON 滚动接口                                                                                                                                   |
| 复用 newsboat-news-hub | CNBC、MarketWatch、WSJ、Bloomberg                                                             | 海外视角，对港股+中概影响大                                                                                                                                                           |


### 5. 财报数据


| 源            | 接口                                                                                                              | 备注                                                                                       |
| ------------ | --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| **新浪财报**（首选） | `https://money.finance.sina.com.cn/corp/go.php/vFD_BalanceSheet/stockid/<code>/ctrl/<year>/displaytype/4.phtml` | HTML 表格，可解析为 DataFrame。三大表都有：`vFD_BalanceSheet` / `vFD_ProfitStatement` / `vFD_CashFlow` |
| akshare 包装   | `ak.stock_financial_report_sina(stock="sh600519", symbol="资产负债表")`                                              | 实测仍可用（走新浪后端）                                                                             |
| 港股财报         | `ak.stock_financial_hk_report_em`                                                                               | 走东财，可能受 push2 屏蔽影响。**备用**：直接拉年报 PDF 解析                                                   |


### 6. 公司基本面


| 数据       | 接口                                                                                   | 备注                     |
| -------- | ------------------------------------------------------------------------------------ | ---------------------- |
| 高管 / 管理层 | `https://money.finance.sina.com.cn/corp/go.php/vCI_CorpManager/stockid/<code>.phtml` | 新浪 HTML                |
| 主要股东     | `https://money.finance.sina.com.cn/corp/go.php/vCI_StockHolder/stockid/<code>.phtml` | 新浪 HTML                |
| 公司基本信息   | `https://money.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/<code>.phtml`    | 新浪 HTML                |
| 概念归属     | 同花顺 `https://basic.10jqka.com.cn/<code>/concept.html`                                | 新浪也有但不全                |
| 港股公司资料   | `https://stock.finance.sina.com.cn/hkstock/finance/<code>.html`                      | 港股需 5 位补零代码（如 `00700`） |


### 6.5 主力资金流（个股）✅

封装在 `shared/stock_core/fund_flow.py` 的 `fetch_daily_fund_flow(market, code)`。

**接口**：

```
GET https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get
    ?secid={market_id}.{code}
    &lmt=0
    &klt=101
    &fields1=f1,f2,f3,f7
    &fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63
    &ut=b2884a393a59ad64002292a3e90d46a5
```

| 项 | 说明 |
|---|---|
| 是否需登录 | 否，无 cookie 也能访问 |
| 反爬状态 | ✅ 稳定（与被封的 `push2.eastmoney.com` 是不同 host，2026-05 实测可用） |
| 返回 | 最近约 120 个交易日的逐日资金流（按日期升序） |
| 单位 | 金额：元；占比：% |

**`secid` 规则**：

| 市场 | 规则 | 例 |
|---|---|---|
| 上交所 A 股（6 开头）| `1.<6 位代码>` | `1.600519` |
| 深交所 A 股（0/3 开头）| `0.<6 位代码>` | `0.300750` |
| 港股 | `116.<5 位补零代码>` | `116.00700` |
| 北交所（4/8 开头）| **不支持**：fund_flow 调用方应自行跳过 | — |
| 美股 | **不适用**："主力资金"非美股标准市场指标 | — |

**返回字段**（`klines` 是逗号分隔字符串数组）：

| 索引 | 字段 | 含义 |
|---|---|---|
| 0 | f51 | 日期 (YYYY-MM-DD) |
| 1 | f52 | 主力净额（元）|
| 2 | f53 | 小单净额（元）|
| 3 | f54 | 中单净额（元）|
| 4 | f55 | 大单净额（元）|
| 5 | f56 | 超大单净额（元）|
| 6 | f57 | 主力净占比（%）|
| 7~10 | f58~f61 | 小/中/大/超大 净占比（%）|
| 11 | f62 | 收盘价 |
| 12 | f63 | 涨跌幅（%）|

**使用约定**：
- 缓存策略：盘中 60s / 盘后 4h（`is_market_open(market)` 判定，`A 股 09:30-11:30 + 13:00-15:00`，`港股 09:30-12:00 + 13:00-16:00`）
- 港股资金分级是东财根据成交单笔大小推算，不如 A 股可靠，对外渲染时必须标注「仅供参考」
- 与雪球 `screener.main_net_inflows` 互补：雪球给"全市场榜单 + 当日累计"，东财 fflow 给"单只 120 日逐日序列 + 五档细分"


### 7. 风险信号源


| 数据      | 接口                                               | 备注         |
| ------- | ------------------------------------------------ | ---------- |
| ST 股票名单 | 新浪行情 `name` 字段含 "ST"/"*ST"                       | 行情接口顺带就能看到 |
| 涨跌停     | 新浪行情字段 4(高)/5(低) 与昨收 2 对比，A 股 ±10%（创业板/科创板 ±20%） | 自己算        |
| 大股东质押   | `http://www.cninfo.com.cn/data20/...` 巨潮专项数据     | 公告里也有      |
| 立案调查    | 巨潮关键词搜索 `searchkey=立案`                           | 命中即高风险     |
| 退市预警    | 巨潮关键词搜索 `searchkey=终止上市`                         |            |


## 二、不可用 / 受限源


| 源                                              | 状态          | 替代                                             |
| ---------------------------------------------- | ----------- | ---------------------------------------------- |
| `push2.eastmoney.com`                          | ❌ 当前 IP 段被封 | 改用新浪/腾讯/同花顺                                    |
| 新浪 RSS `rss.sina.com.cn/finance/stock/all.xml` | ❌ 404，路径已变  | 改用 `feed.mix.sina.com.cn/api/roll/get` JSON 接口 |
| Wind / iFinD 卖方研报全文                            | 💰 付费       | 用东财研报频道摘要 + 公告替代                               |
| 路透 RSS                                         | ❌ 已下线       | AP 美联社 + Bloomberg via Google News             |


## 三、调用规范（所有脚本必须遵守）

### 1. 必须用 curl_cffi + Chrome 指纹

```python
from curl_cffi import requests as cffi
r = cffi.get(url, headers=headers, params=params, impersonate="chrome", timeout=8)
```

普通 `requests` 在新浪/腾讯/同花顺/巨潮上有概率失败。

### 2. 限频 / 退避


| 源                   | 单机推荐 QPS            | 备注                                  |
| ------------------- | ------------------- | ----------------------------------- |
| 新浪行情 `hq.sinajs.cn` | 5/s（批量请求一次给 50 个代码） | 单请求最多 80 个代码                        |
| 腾讯行情 `qt.gtimg.cn`  | 5/s                 | 同上                                  |
| 巨潮公告查询              | 0.5/s（每 2 秒一次）      | 频率太高会被限速                            |
| 财联社电报               | 1/s                 |                                     |
| 同花顺 HTML            | 0.5/s               | 反爬较严，建议加随机 sleep 1-2s               |
| 雪球                  | 1/s                 | 需要 cookie `xq_a_token`，匿名请求只能拿 20 条 |


### 3. 编码

- 腾讯股票：`GBK`（必须 `r.content.decode("gbk")`）
- 同花顺：`GBK`
- 其他：UTF-8

### 4. 多源回退原则

每个数据点如果单源失败，按"新浪 → 腾讯 → 同花顺 → akshare 兜底"顺序回退。
失败原因要在 stderr 打印，但不要让单个源的失败导致整个脚本退出。

### 5. 缓存

公告 PDF、财报 HTML 这种"基本不变"的数据，缓存到 `~/.cache/stock-market-hub/`，TTL 24h。
行情、新闻不缓存。

## 四、付费源升级路径（v2 才考虑）


| 付费源           | 解决什么                 | 大致成本        |
| ------------- | -------------------- | ----------- |
| Tushare Pro   | 接口稳定 + 历史分钟级 + 财报字段全 | 200-500 元/年 |
| 同花顺 iFinD 个人版 | 卖方研报全文 + 完整产业链       | 1-3 万/年     |
| 财新通会员         | 高质量深度财经报道            | 600 元/年     |
| Wind          | 机构标配                 | 不建议个人       |


不到瓶颈不上付费源。
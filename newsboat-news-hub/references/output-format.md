# 新闻汇总输出格式（完整版）

> 本文档是 `newsboat-news-hub` 主 SKILL.md 第 5 节"输出格式硬约束 + 正例/反例对照"
> 的完整版。主 SKILL.md 里只保留索引；生成汇总前**必须**先来读这份文件。

## 1. 汇总模板（默认中文）

拿到 `fetch_news.py` 输出的 JSON 后，按以下模板组织：

```
# YYYY年M月D日（周X，[时区]）全球新闻汇总

## 一、[最重要主题]
- `HH:MM` **中文摘要一句话**。 — [来源名](it["link"])
```

## 2. 语言要求（默认中文）

- **默认整份汇总用中文输出**，除非用户明确要求英文。
- **每条的中文摘要必须由你翻译/改写生成**，不要直接粘贴 JSON 里的英文原标题。
  JSON 里的 `title` 只是素材，不是最终呈现。
- 例外：**人名、机构名、产品名、地名**（Trump、NVIDIA、Anthropic、Gaza 等）保留英文原文，
  便于检索与交叉阅读。
- 如果需要让读者看到原标题（比如长标题信息量大），可以在中文摘要后用括号附带
  `（原文：...）`，不要替代中文摘要。

## 3. 其余格式要求

- 每个主题下最多 5–8 条，按重要性排序。
- **每条必须使用 JSON 里的 `it["link"]` 字段的完整原始字符串作为 URL**。
  严禁以下三种偷懒做法：
  1. 用媒体首页 URL（`https://www.nytimes.com/`）代替真实文章 URL
  2. 链接留空或写 `(链接)` 占位
  3. **在 URL 中使用 `...` / `[省略]` / `xxx` 等省略符号**。
     `news.google.com/rss/articles/CBMi...` 这样的写法是错的 —— Google News 的 token
     是 90+ 字符的 base64，**少一个字符都会 404/400**。要么完整粘贴，要么不要贴。
- 如果因为 URL 太长担心影响可读性，**不要**自作主张截断；可以把每条的链接放到行尾或者
  额外用脚注，但 token 必须完整。
- Google News 的标题通常包含 ` - 来源名`，脚本已自动拆分，直接用 `it["source"]` 展示即可。
- 多家媒体报道同一事件时合并为一条，把多个来源链接都列出来。
- 条目开头可以附带 `` `HH:MM` `` 时间戳（取自 `pubDate` 的小时分钟）便于定位，
  时区同 `--tz`。
- 汇总末尾追加"抓取说明"小节：列出 `--date`/`--tz` 实际参数、
  `in_window / before / after / no_date` 统计、失败的源（如 AP 美联社经常 429）。

## 4. URL 书写反例（务必不要这样做）

<bad>
[New York Post via GN](https://news.google.com/rss/articles/CBMi...)
</bad>

<reasoning>
省略号不是 URL 的一部分，点开必然 404/400。用户会直接看到问题。
</reasoning>

<good>
[New York Post via Google News](https://news.google.com/rss/articles/CBMitgFBVV95cUxPVXVWbDYxS1k0VFRxWjhQNDdNT0Flbzgxd0VERk1kWm9xS3ROTXVQQXFmUEVSMndoTklFbDkxTXQ4Zm9pUDZYdjJPUmFHNHljZXlybmZObUNETmFJVDFkSUZ1b3BxYkVlQUlHNEF6UFpNZEJ3c3BGdGpYT1VGaFRaZnpTWDZNMzN4ZjlMSzFHbGVsRG1Nc1JmSnlLcVl4cU5BVDlXOEZOUk9ocnV3eVJSUG00YzEtUQ?oc=5)
</good>

## 5. 正例 / 反例对照

<good>
- `17:42` **苹果 CEO Tim Cook 宣布卸任，硬件工程高级副总裁 John Ternus 接任**。
  执掌 15 年告一段落。 — [CBS News](https://www.cbsnews.com/news/tim-cook-apple-ceo-to-step-down-john-ternus/)
</good>

<bad>
- Tim Cook to step down as Apple CEO, with John Ternus tapped as successor — [CBS News](https://www.cbsnews.com/...)
</bad>

<reasoning>反例直接粘贴了英文原标题，没有中文摘要。默认应该输出中文。</reasoning>

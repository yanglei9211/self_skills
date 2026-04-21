---
name: divination
description: "中国传统算卦命理 CLI：八字排盘、周易起卦（铜钱/时间/数字/文字）、推背图查询、五行分析、全文搜索。当用户提到算卦、占卜、算命、八字、生辰、周易、易经、卦象、推背图、五行、运势、吉凶时使用。"
---

# 算卦 CLI

通过 `divination` CLI 查询中国传统命理数据并进行算卦。

## 基本用法

```bash
DIVINATION=$(python3 -c "import os; print(os.path.realpath(os.path.expanduser('~/.cursor/skills/divination/scripts/divination')))")
python3 $DIVINATION <command> [options] --pretty
```

始终加 `--pretty` 以获得格式化 JSON 输出。首次运行会自动初始化数据库，数据更新后运行 `init` 重建。

## 核心命令

### 1. 八字排盘

```bash
python3 $DIVINATION bazi --birth "1990-05-15 14:30" --pretty
```

返回四柱（年月日时）、天干地支、纳音、五行分布、日主。

### 2. 起卦

```bash
# 铜钱法（随机掷卦）
python3 $DIVINATION cast --method coin --pretty

# 时间起卦（用当前时间）
python3 $DIVINATION cast --method time --pretty

# 数字起卦（梅花易数）
python3 $DIVINATION cast --method number --num1 38 --num2 21 --pretty

# 文字起卦（用问题文字的哈希）
python3 $DIVINATION cast --method text --text "今年事业运势如何" --pretty
```

返回主卦、变卦、动爻，包含完整卦辞和爻辞。

### 3. 查询卦象

```bash
python3 $DIVINATION hexagram --number 1 --pretty    # 按序号
python3 $DIVINATION hexagram --name 乾 --pretty      # 按卦名
```

### 4. 推背图

```bash
python3 $DIVINATION tuibei --number 3 --pretty       # 按象序号
python3 $DIVINATION tuibei --keyword 日月 --pretty    # 按关键词
```

### 5. 五行

```bash
python3 $DIVINATION element --name 金 --pretty       # 查单个
python3 $DIVINATION element --pretty                  # 查全部
```

### 6. 穷通宝鉴（用神查询）

```bash
# 查某日主在某月的用神（如：乙木日主·九月戌月）
python3 $DIVINATION qiongtong --stem 乙 --month 戌 --pretty

# 查某日主全年12个月的用神
python3 $DIVINATION qiongtong --stem 乙 --pretty
```

返回《穷通宝鉴》原文、用神建议、关键词。120条（10日主×12月）全覆盖。

### 7. 命理经典（滴天髓·子平真诠）

```bash
# 按书名查
python3 $DIVINATION classic --book 滴天髓 --pretty

# 按关键词查
python3 $DIVINATION classic --keyword 通关 --pretty

# 书名+关键词
python3 $DIVINATION classic --book 滴天髓 --keyword 乙木 --pretty
```

### 8. 全文搜索

```bash
python3 $DIVINATION search "龙" --pretty
```

跨周易、推背图、穷通宝鉴、滴天髓、子平真诠全库搜索。

## Agent 工作流

用户提问算卦/命理需求时，按以下步骤操作：

1. **理解需求**：用户想算什么？八字？卦象？运势？
2. **收集信息**：如需八字则问出生日期时间；如需起卦则确定方法
3. **调用 CLI**：将需求翻译为命令
4. **解读结果**：基于返回的原始数据（卦辞、爻辞、五行等）给出解读
5. **标注引用**：引用原文时标明出处，如 `[周易·第1卦·乾·卦辞]` 或 `[推背图·第3象]`

### 常见场景映射

| 用户说 | 执行命令 |
|--------|---------|
| 帮我算八字 | `bazi --birth "..."` |
| 帮我占一卦 | `cast --method coin` |
| 用我的问题起卦 | `cast --method text --text "..."` |
| 今天运势 | `cast --method time` + `bazi`（如有生日） |
| 查一下乾卦 | `hexagram --name 乾` |
| 推背图第三象 | `tuibei --number 3` |
| 搜索"龙"相关 | `search "龙"` |
| 我五行缺什么 | `bazi --birth "..."` 后分析五行分布 |
| 我的用神是什么 | `bazi` 得日主和月支 → `qiongtong --stem X --month Y` |
| 什么是通关 | `classic --keyword 通关` |
| 乙木有什么特性 | `classic --book 滴天髓 --keyword 乙木` |

### 八字解读工作流

排完八字后，依次查询以下内容作为解读依据：

1. `qiongtong --stem <日主> --month <月支>` → 获取用神指导
2. `classic --book 滴天髓 --keyword <日主天干>` → 获取日主特性
3. `classic --keyword 调候` 或 `通关` 或 `病药` → 获取理论依据
4. `element --name <五行>` → 获取生克关系

解读时必须引用原文出处，不做无据推断。

### 解读规则

- **八字**：先查穷通宝鉴定用神，再结合滴天髓理论分析
- **卦象**：重点解读卦辞和动爻对应的爻辞；有变卦时结合变卦
- **推背图**：仅作历史文化参考，不做预测性断言
- **五行**：结合生克关系给出建议

### 引用格式

解读中引用原文时使用以下格式：

> 「飞龙在天，利见大人」—— [周易·第1卦·乾·九五]

> 「日月当空，照临下土」—— [推背图·第3象·谶]

> 「九月乙木，木气凋零，金旺乘权。宜丙火出干，暖局制金。」—— [穷通宝鉴·乙木·九月]

> 「乙木虽柔，刲羊解牛。怀丁抱丙，跨凤乘猴。」—— [滴天髓·通神论·乙木论]

## 详细参考

命令完整参数见 [references/commands.md](references/commands.md)

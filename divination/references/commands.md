# 命令参考

## 全局选项

| 选项 | 说明 |
|------|------|
| `--pretty` | 格式化 JSON 输出 |

## init — 初始化数据库

```bash
python3 $DIVINATION init
```

首次运行自动初始化。手动重建数据库时使用。

## bazi — 八字排盘

```bash
python3 $DIVINATION bazi --birth "YYYY-MM-DD HH:MM" --pretty
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--birth` | 是 | 出生时间，格式 `YYYY-MM-DD HH:MM` 或 `YYYY-MM-DD`（默认正午） |

**返回字段：**
- `bazi`: 八字字符串（如 `庚午 丁巳 甲子 壬申`）
- `pillars`: 四柱详情（天干、地支、五行、纳音、属相）
- `day_master`: 日主天干
- `day_master_element`: 日主五行
- `five_elements_count`: 八字中五行各出现次数

## cast — 起卦

```bash
python3 $DIVINATION cast --method <method> [options] --pretty
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--method` | 否 | 起卦方法：`coin`(默认) / `time` / `number` / `text` |
| `--num1` | number时 | 第一个数字 |
| `--num2` | 否 | 第二个数字（number 方法，不传则从 num1 派生） |
| `--text` | text时 | 起卦用的文字（通常是用户的问题） |
| `--seed` | 否 | 随机种子，用于复现结果 |

**起卦方法说明：**
- `coin` 铜钱法：模拟三枚铜钱掷六次，可能产生动爻和变卦
- `time` 时间起卦：用当前时间计算卦象
- `number` 梅花易数：用数字计算卦象
- `text` 文字起卦：用问题文字的哈希值起卦，相同文字产生相同卦

**返回字段：**
- `main_hexagram`: 主卦详情（名称、卦辞、爻辞、象传）
- `changing_lines`: 动爻位置列表（coin 方法）
- `changed_hexagram`: 变卦详情（coin 方法，有动爻时）
- `moving_line`: 动爻位置（time/number/text 方法）

## hexagram — 查询卦象

```bash
python3 $DIVINATION hexagram --number <1-64> --pretty
python3 $DIVINATION hexagram --name <卦名> --pretty
```

| 参数 | 说明 |
|------|------|
| `--number` | 卦序号 1-64（周文王序） |
| `--name` | 卦名（中文或拼音，如 `乾` 或 `qián`） |

## trigram — 查询八卦

```bash
python3 $DIVINATION trigram --pretty              # 列出全部八卦
python3 $DIVINATION trigram --name 乾 --pretty    # 查单个
```

## tuibei — 查询推背图

```bash
python3 $DIVINATION tuibei --number <1-60> --pretty
python3 $DIVINATION tuibei --keyword <关键词> --pretty
```

| 参数 | 说明 |
|------|------|
| `--number` | 象序号 1-60 |
| `--keyword` | 关键词搜索（搜谶、颂、解释） |

## element — 查询五行

```bash
python3 $DIVINATION element --pretty             # 全部五行及关系
python3 $DIVINATION element --name 金 --pretty   # 单个元素
```

返回生克关系（生谁、克谁、被谁生、被谁克）、对应季节、颜色、器官、情绪。

## search — 全文搜索

```bash
python3 $DIVINATION search "关键词" --pretty
```

跨周易（卦辞、爻辞、象传）和推背图（谶、颂、解释）全库 FTS5 全文搜索。返回匹配片段，高亮标记为 `【关键词】`。

**返回字段：**
- `keyword`: 搜索词
- `count`: 匹配数量
- `results[]`: 来源（source）、ID、标题、匹配片段（snippet）

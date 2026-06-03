# divination

中国传统算卦命理 CLI。支持八字排盘、周易起卦、卦象查询、推背图、五行、穷通宝鉴、滴天髓和子平真诠全文搜索。

## 适用场景

- 八字排盘、五行分布、日主分析。
- 周易起卦：铜钱、时间、数字、文字。
- 查询周易 64 卦、推背图 60 象、穷通宝鉴用神、命理经典原文。
- 面向 Agent 或人工命令行使用，输出结构化 JSON。

## 不适用场景

- 不提供确定性人生建议或投资、医疗、法律判断。
- 不替代专业命理师；输出应结合原文引用谨慎解读。

## 快速开始

```bash
DIVINATION=$(python3 -c "import os; print(os.path.realpath(os.path.expanduser('~/.cursor/skills/divination/scripts/divination')))")
python3 "$DIVINATION" cast --method coin --pretty
python3 "$DIVINATION" bazi --birth "1990-05-15 14:30" --pretty
```

首次运行会自动初始化 SQLite 数据库。数据更新后可运行：

```bash
python3 "$DIVINATION" init --pretty
```

## 主要命令

| 功能 | 示例 |
|---|---|
| 八字排盘 | `bazi --birth "1990-05-15 14:30" --pretty` |
| 铜钱起卦 | `cast --method coin --pretty` |
| 时间起卦 | `cast --method time --pretty` |
| 数字起卦 | `cast --method number --num1 38 --num2 21 --pretty` |
| 文字起卦 | `cast --method text --text "今年事业如何" --pretty` |
| 查询卦象 | `hexagram --name 乾 --pretty` |
| 推背图 | `tuibei --number 3 --pretty` |
| 五行 | `element --name 金 --pretty` |
| 穷通宝鉴 | `qiongtong --stem 乙 --month 戌 --pretty` |
| 经典搜索 | `classic --keyword 通关 --pretty` |
| 全文搜索 | `search "龙" --pretty` |

## 依赖与配置

- Python 3.8+ 标准库，无第三方依赖。
- 本地数据库：`divination/data/divination.db`，首次运行自动构建，已在 `.gitignore` 中排除。

## 数据与产物

- 静态命理数据在 `scripts/*_data.py` 中维护。
- 完整命令说明见 `references/commands.md`。
- CLI 输出建议统一加 `--pretty`，便于人工阅读和 Agent 引用。

## 注意事项

- 解读时应引用原文出处，例如 `[周易·第1卦·乾·卦辞]`。
- 不要无依据扩展原文含义；命理解释应标注不确定性。

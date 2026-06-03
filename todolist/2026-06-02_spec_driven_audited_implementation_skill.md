# Spec：实现 spec-driven-audited-implementation skill

## 1. 背景

当前使用 Claude Code / Superpower / Goal 实现中等复杂需求时，已经形成了一套初步流程：

1. 用户先编写 spec。
2. agent 审查 spec。
3. agent 创建 plan。
4. agent 按 plan 实现。
5. agent review。
6. agent 修复 review 问题。
7. agent 产出 final audit。

但实际执行中发现几个问题：

1. 仅通过一次性提示词约束，agent 不一定严格遵循流程。
2. agent 可能由 main agent 从头跑到尾，review 和测试缺乏独立性。
3. `Use superpowers` 不一定意味着真正调用 Superpower skill / subagent。
4. agent 可能没有充分利用 subagent / independent reviewer / tester。
5. review 可能变成 main agent 自我背书。
6. 测试可能围绕实现代码补测试，而不是围绕 spec 和验收标准设计测试。
7. 最终总结可能空泛，缺乏基于 git diff、测试命令、验收标准的证据。
8. 过程产物虽然可以落盘，但缺乏统一协议，后续人工检查成本仍然偏高。

因此，需要实现一个新的 skill，将这套流程固化为可复用、agent 可读、可审计、可降级声明的执行协议。

---

## 2. 目标

实现一个新的 skill：

    spec-driven-audited-implementation

该 skill 用于：

1. 已有 spec 的中等复杂或复杂代码任务。
2. 需要尽量少人工干预。
3. 需要 plan / review / test / fix / final audit 等过程产物落盘。
4. 需要尽可能使用 subagent / independent reviewer / independent tester。
5. 如果无法使用独立 subagent，也必须显式降级，不得伪装成独立审查。
6. 所有中间产物默认使用中文。
7. 所有执行产物默认写入：

    ~/Documents/local2/self_skills/.superpower/plan/

---

## 3. 非目标

本需求不要求实现完整 agent orchestrator。

明确非目标：

1. 不要求实现真正的多进程 / 多 agent 调度器。
2. 不要求强制启动多个 Claude Code 会话。
3. 不要求接入 Multica / OpenClaw / LangGraph 等外部系统。
4. 不要求实现复杂 UI。
5. 不要求自动 merge 代码。
6. 不要求自动提交 git commit。
7. 不要求替代 Superpower 原有 skill。
8. 不要求保证 100% 强制 agent 使用 subagent，因为 skill 本质仍是 agent 协议。
9. 第一版不要求实现复杂语义校验脚本。
10. 第一版不要求实现跨项目配置系统。

---

## 4. 核心原则

### 4.1 Spec 驱动

所有实现必须以用户提供的 spec 为最高优先级。

agent 不得根据自己的偏好擅自扩展需求。

### 4.2 过程可审计

执行过程必须生成落盘产物，方便用户事后检查。

默认产物包括：

    <task_name>_capability_check.md
    <task_name>_plan.md
    <task_name>_plan_review.md
    <task_name>_implementation_summary.md
    <task_name>_test_report.md
    <task_name>_review.md
    <task_name>_fix_report.md
    <task_name>_final_audit.md

### 4.3 独立性优先

如果环境支持 subagent / independent reviewer / independent tester，应优先使用。

如果不支持，必须显式声明降级。

### 4.4 不得把 main agent 自检伪装成独立审查

如果 review 不是由独立上下文完成，必须写明：

    非独立 review，仅为 main agent 自检，不能视为最终质量保证。

如果测试设计不是独立完成，必须写明：

    非独立测试设计，仅为 main agent 自检，存在测试偏向实现的风险。

如果 final audit 不是独立完成，必须写明：

    非独立 final audit，仅为 main agent 自检，建议使用外部强 agent 二审。

### 4.5 证据优先

报告不能写空泛结论。

所有重要结论必须尽量绑定到：

1. spec 验收标准
2. plan 任务
3. 实际 git diff
4. 测试文件
5. 测试命令
6. 测试结果
7. review finding
8. fix 记录

### 4.6 中文产物

默认所有中间产物使用中文。

例外：

1. 代码
2. 命令
3. 文件路径
4. 函数名
5. 类名
6. 变量名
7. 错误日志原文
8. git diff 原文

---

## 5. 目录与文件要求

### 5.1 skill 目录

在当前 self_skills 项目中新增 skill 目录。

推荐路径：

    skills/spec-driven-audited-implementation/

如果当前项目已有其他 skill 目录约定，应优先遵循项目现有结构。

### 5.2 必须创建的文件

至少创建：

    skills/spec-driven-audited-implementation/SKILL.md

建议创建：

    skills/spec-driven-audited-implementation/templates/capability_check.md
    skills/spec-driven-audited-implementation/templates/plan.md
    skills/spec-driven-audited-implementation/templates/plan_review.md
    skills/spec-driven-audited-implementation/templates/implementation_summary.md
    skills/spec-driven-audited-implementation/templates/test_report.md
    skills/spec-driven-audited-implementation/templates/review.md
    skills/spec-driven-audited-implementation/templates/fix_report.md
    skills/spec-driven-audited-implementation/templates/final_audit.md

可选创建：

    skills/spec-driven-audited-implementation/scripts/verify_artifacts.py

第一版可以不实现脚本，但如果实现，必须保持简单、可读、低侵入。

---

## 6. SKILL.md 内容要求

`SKILL.md` 必须是 agent 可读的流程协议，而不是面向人类的长篇解释。

必须包含以下章节。

### 6.1 Purpose

说明该 skill 的用途。

用于已有 spec 的中等复杂代码任务，实现目标是：

1. 最小人工干预。
2. spec 驱动实现。
3. 过程产物落盘。
4. 尽可能使用独立 reviewer / tester / auditor。
5. 无法独立时显式降级。
6. 所有中间产物中文输出。
7. 通过 final audit 给用户提供可检查证据。

### 6.2 When to use

必须说明适用场景：

1. 用户提供了完整或基本完整的 spec。
2. 任务涉及多文件修改。
3. 任务需要测试。
4. 任务需要 review。
5. 用户希望减少中途人工追踪。
6. 用户希望过程产物可回放。

### 6.3 When not to use

必须说明不适用场景：

1. 极小修改。
2. 简单问答。
3. 不涉及代码实现的解释类任务。
4. 用户明确要求快速直接修改。
5. 用户不希望产物落盘。
6. 没有 spec 且需求仍需大量澄清。

### 6.4 Default directories

必须写明默认目录。

原始 spec 默认目录：

    ~/Documents/local2/self_skills/todolist/

执行产物默认目录：

    ~/Documents/local2/self_skills/.superpower/plan/

### 6.5 Artifact naming

必须写明默认命名规则：

    <task_name>_capability_check.md
    <task_name>_plan.md
    <task_name>_plan_review.md
    <task_name>_implementation_summary.md
    <task_name>_test_report.md
    <task_name>_review.md
    <task_name>_fix_report.md
    <task_name>_final_audit.md

如果用户没有提供 `task_name`，agent 应从 spec 文件名推导。

例如：

    2026-06-01_point_in_time_backtesting_system.md

推导为：

    2026-06-01_point_in_time_backtesting_system

### 6.6 Language requirements

必须写明：

1. 所有中间报告使用中文。
2. 最终回复使用中文。
3. 代码、命令、文件路径、函数名、变量名、错误日志可以保留原文。
4. 不要额外生成英文报告。

### 6.7 Critical rule：independence disclosure

必须明确写入：

    Do not treat main-agent self-review as independent review.

并用中文解释：

不得把 main agent 自检伪装成独立 review。

如果无法使用真正独立 reviewer / tester / auditor，必须在以下文件中说明：

1. capability_check
2. review
3. test_report
4. final_audit

---

## 7. 执行流程要求

`SKILL.md` 必须定义以下阶段。

### Phase 0：Capability Check

在计划和编码前，必须创建：

    <task_name>_capability_check.md

内容必须包含：

1. Spec 路径
2. Artifact 目录
3. task_name
4. 是否可用 Superpower
5. 是否可用 subagent
6. 是否可用 requesting-code-review skill
7. 是否可用 independent reviewer context
8. 是否可用 independent tester context
9. 是否可用 parallel execution
10. 本次实际执行模式：
    - Planner
    - Implementer
    - Reviewer
    - Tester
    - Fixer
    - Auditor
11. 降级说明
12. 独立性风险

要求：

如果无法确认某项能力，必须写：

    无法确认

不得假装可用。

如果无法使用真正独立 review，必须写：

    本次 review 将降级为 main agent 自检，不能视为最终质量保证。

### Phase 1：Spec Review

必须先阅读 spec，不得直接编码。

需要识别：

1. 阻塞性歧义
2. 非阻塞性假设
3. 隐藏边界情况
4. 验收标准
5. 实现风险
6. 非目标
7. 不允许做的事
8. 可能的 scope creep 风险

阻塞性问题包括：

1. 数据模型存在不兼容选择
2. 公共 API 行为不明确
3. 数据迁移或不可逆操作存在风险
4. 涉及安全敏感行为
5. 业务规则不清晰，且随意猜测可能造成明显错误行为

只有遇到真正阻塞问题才停止询问用户。

普通歧义应选择最小、保守、不破坏现有行为的假设，并记录在 plan 和 final audit 中。

### Phase 2：Plan

必须创建：

    <task_name>_plan.md

Plan 必须包含：

1. Spec 审查结果
2. 非阻塞假设
3. 验收标准列表
4. 实现任务列表
5. 测试策略
6. review 策略
7. 风险与回滚考虑
8. 明确不做的事情

每个任务必须包含：

1. 任务名称
2. 预期改动
3. 可能修改文件
4. 需要新增或修改的测试
5. 验证命令
6. 是否需要独立 review
7. 风险等级
8. 回滚考虑

限制：

1. 严格限制在 spec 范围内。
2. 不做无关重构。
3. 不修改公共 API，除非 spec 明确要求。
4. 不引入新依赖，除非 spec 明确要求或有充分理由。
5. 不做格式化大面积无关改动。

### Phase 3：Plan Review

必须创建：

    <task_name>_plan_review.md

如果可以使用独立 subagent / reviewer，必须使用。

Plan Review 输入只能是：

1. Spec
2. Plan

不得依赖后续实现假设。

Plan Review 必须检查：

1. Plan 是否覆盖所有验收标准
2. 是否遗漏关键边界情况
3. 是否存在 scope creep
4. 是否测试不足
5. 是否任务拆分过粗
6. 是否风险识别不足
7. 是否存在不必要的公共 API / 数据模型变更

如果无法独立 review，必须写明：

    非独立 plan review，仅为 main agent 自检。

### Phase 4：Implementation

实现者必须按 plan 逐项实现。

要求：

1. 每个任务开始前说明当前任务。
2. 每个任务完成后更新 implementation summary。
3. 尽可能运行该任务对应验证命令。
4. 验证失败必须先定位并修复。
5. 如果失败与本任务无关，可以记录为遗留风险，但不得谎称通过。

必须创建或更新：

    <task_name>_implementation_summary.md

Implementation Summary 必须包含：

1. 已完成任务
2. 每个任务修改的文件
3. 每个任务运行的验证命令
4. 验证结果
5. 失败与修复记录
6. 与 plan 的偏差
7. 未完成项

禁止行为：

1. 扩大范围
2. 无关重构
3. 删除测试
4. 弱化测试断言
5. 跳过测试来规避失败
6. 吞掉异常
7. 屏蔽错误
8. 过度 mock 核心逻辑
9. 只运行很小范围测试却声称整体通过

### Phase 5：Test Design and Test Report

必须创建：

    <task_name>_test_report.md

测试设计必须从 spec 和验收标准出发，而不是只围绕实现代码。

如果可以使用 independent tester，必须使用。

Test Report 必须包含：

1. 测试设计模式：
   - independent tester
   - independent session
   - simulated tester role
   - main-agent self-check
2. 测试策略
3. 验收标准到测试的映射
4. 新增或修改的测试文件
5. 测试命令
6. 测试结果
7. 失败记录
8. 修复记录
9. 未覆盖风险
10. 是否存在测试降级

测试者必须检查：

1. 是否只测 happy path
2. 是否缺少边界情况
3. 是否缺少失败路径
4. 是否存在过度 mock
5. 是否删除测试
6. 是否弱化断言
7. 是否跳过测试
8. 是否测试实现细节而非业务行为

### Phase 6：Code Review

必须创建：

    <task_name>_review.md

如果可以使用 Superpower requesting-code-review skill 或等价独立 reviewer，必须使用。

Reviewer 输入必须是：

1. Spec
2. Plan
3. 实际 git diff
4. 测试文件
5. 测试命令与结果

Reviewer 不得依赖 Implementer 的主观解释。

Reviewer 必须主动寻找反例，而不是证明实现是对的。

Review Report 必须包含：

1. review 时间
2. review 模式：
   - independent subagent
   - independent session
   - simulated reviewer role
   - main-agent self-check
3. review 输入
4. findings，按等级分组：
   - Critical
   - Important
   - Minor
5. 每个 finding 必须包含：
   - 等级
   - 文件路径
   - 问题说明
   - 为什么是问题
   - 修复建议
   - 是否必须修复
6. 如果未发现 Critical / Important，必须说明检查过哪些高风险点，以及为什么认为没有问题。

如果不是独立 review，必须写：

    非独立 review，仅为 main agent 自检，不能视为最终质量保证。

### Phase 7：Fix Review Findings

必须创建：

    <task_name>_fix_report.md

必须修复所有 Critical 和 Important 问题。

Minor 问题如果低风险则修复；如果不修复，需要说明原因。

Fix Report 必须包含：

1. Review finding 列表
2. 已修复问题
3. 每个问题的修复方式
4. 涉及文件
5. 重新运行的验证命令
6. 验证结果
7. 未修复问题及原因

如果 Critical / Important 无法修复，必须停止并说明原因，不得继续声称完成。

### Phase 8：Final Audit

必须创建：

    <task_name>_final_audit.md

Final Audit 必须基于证据，而不是主观总结。

输入必须包括：

1. Spec
2. Plan
3. Plan Review
4. Implementation Summary
5. Test Report
6. Review Report
7. Fix Report
8. 实际 git diff
9. 验证结果

Final Audit 必须包含：

1. 总体结论
2. 是否建议接受实现
3. 修改文件列表
4. 所有产物路径
5. 验收标准逐条状态
6. 每条验收标准的证据
7. 已运行命令
8. 测试结果
9. Review findings 与修复状态
10. Scope creep 检查
11. 执行中采用的假设
12. 剩余风险
13. 明确没有做的事情
14. 是否真正使用了独立 reviewer / tester / auditor
15. 如果发生降级，对质量保证的影响

如果没有独立 review，不得写：

    已完成独立审查
    fully verified
    完全验证

只能写：

    功能实现已完成，但质量保证仅达到 main-agent 自检级别，建议外部强 agent 二审。

---

## 8. Point-in-time / Backtesting 专项要求

由于用户经常处理回测、股票、时间点数据、强势池扫描等任务，该 skill 需要包含一个专项规则。

当 spec 或 task_name 包含以下关键词时：

    point-in-time
    backtesting
    回测
    历史时间点
    快照
    snapshot
    未来数据
    future leakage
    lookahead

测试与 review 必须重点检查未来数据泄漏。

必须在 Test Report 和 Review Report 中检查：

1. 是否存在读取未来数据风险。
2. 是否存在 `latest` / `current` / `today` / `now` 等隐式当前时间数据源。
3. 是否存在指定历史日期时默认读取最新文件。
4. 是否存在全量数据预加载后再过滤，但特征已被未来数据污染。
5. 是否存在文件修改时间、当前系统日期影响回测结果。
6. 是否有测试覆盖“未来数据存在但不应被读取”的场景。
7. 是否有测试覆盖“目标日期快照缺失”的场景。
8. 是否有测试覆盖“同一标的不同历史日期可见数据不同”的场景。

如果无法实现这些测试，必须在 Final Audit 中明确写明原因和风险。

---

## 9. 可选脚本：verify_artifacts.py

第一版可选实现一个轻量脚本：

    skills/spec-driven-audited-implementation/scripts/verify_artifacts.py

功能：

输入：

    python verify_artifacts.py --task-name <task_name> --artifact-dir <artifact_dir>

检查：

1. 必要 artifact 文件是否存在。
2. 每个文件是否非空。
3. 是否包含关键章节关键词。
4. final_audit 是否包含验收标准、测试命令、剩余风险。
5. review 是否声明 review 模式。
6. capability_check 是否声明 subagent / reviewer / tester 可用性。
7. test_report 是否声明测试设计模式。

输出：

1. PASS / FAIL
2. 缺失文件列表
3. 缺失章节列表
4. 风险提示

限制：

1. 不要求理解代码语义。
2. 不要求判断实现是否正确。
3. 不要求检查 git diff。
4. 不要引入复杂依赖，优先使用 Python 标准库。

---

## 10. 示例调用方式

`SKILL.md` 中必须包含示例调用。

示例：

    Use superpowers.
    Use the spec-driven-audited-implementation skill.

    Spec:
    ~/Documents/local2/self_skills/todolist/2026-06-01_point_in_time_backtesting_system.md

    Task name:
    2026-06-01_point_in_time_backtesting_system

    Goal:
    按照 skill 协议审查 spec、创建中文执行产物、实现需求、测试、review、修复、final audit。

    Hard requirements:
    - 所有中间产物写入 ~/Documents/local2/self_skills/.superpower/plan/
    - 所有中间产物使用中文
    - 必须先创建 capability_check
    - 必须声明是否真的使用了 subagent / independent reviewer / independent tester
    - 如果没有使用真正独立 review，不得声称 fully verified
    - Review 必须基于 Spec、Plan、git diff、测试结果，不得只基于实现者总结
    - point-in-time 相关实现必须重点检查未来数据泄漏

---

## 11. 验收标准

实现完成后，必须满足以下验收标准。

### 11.1 文件结构验收

必须存在：

    skills/spec-driven-audited-implementation/SKILL.md

如果实现了 templates，则至少包含：

    templates/capability_check.md
    templates/plan.md
    templates/plan_review.md
    templates/implementation_summary.md
    templates/test_report.md
    templates/review.md
    templates/fix_report.md
    templates/final_audit.md

如果实现了脚本，则存在：

    scripts/verify_artifacts.py

### 11.2 SKILL.md 内容验收

`SKILL.md` 必须包含：

1. Purpose
2. When to use
3. When not to use
4. Default directories
5. Artifact naming
6. Language requirements
7. Critical rule：不得把 main-agent self-review 视为 independent review
8. Phase 0：Capability Check
9. Phase 1：Spec Review
10. Phase 2：Plan
11. Phase 3：Plan Review
12. Phase 4：Implementation
13. Phase 5：Test Design and Test Report
14. Phase 6：Code Review
15. Phase 7：Fix Review Findings
16. Phase 8：Final Audit
17. Point-in-time / Backtesting 专项要求
18. 示例调用方式
19. Acceptance policy

### 11.3 独立性声明验收

`SKILL.md` 必须明确规定：

1. main-agent self-review 不能算 independent review。
2. 如果无法独立 review，必须显式降级。
3. 如果无法独立 tester，必须显式降级。
4. 如果无法独立 final audit，必须显式降级。
5. 降级必须写入 capability_check、review、test_report、final_audit。
6. 没有独立 review 时，不得声称 fully verified。

### 11.4 中文产物验收

`SKILL.md` 必须规定：

1. 所有中间产物默认中文。
2. 最终回复默认中文。
3. 代码、命令、路径、日志原文可保留英文。
4. 不生成英文版报告。

### 11.5 Artifact 协议验收

`SKILL.md` 必须定义 8 个默认产物：

    _capability_check.md
    _plan.md
    _plan_review.md
    _implementation_summary.md
    _test_report.md
    _review.md
    _fix_report.md
    _final_audit.md

并说明每个产物必须包含哪些内容。

### 11.6 Review 质量验收

`SKILL.md` 必须规定：

1. Reviewer 输入必须包含 Spec、Plan、git diff、测试文件、测试结果。
2. Reviewer 不得依赖 Implementer 主观解释。
3. Reviewer 必须主动寻找反例。
4. Review findings 必须按 Critical / Important / Minor 分级。
5. 每个 finding 必须包含文件路径、问题说明、影响、修复建议。
6. 如果没有 Critical / Important，也必须说明检查过哪些高风险点。

### 11.7 Test 质量验收

`SKILL.md` 必须规定：

1. 测试设计从 spec 和验收标准出发。
2. 不得只围绕实现细节写测试。
3. 必须检查 happy path、边界情况、失败路径、防回归。
4. 必须检查测试是否被删除、跳过、弱化、过度 mock。
5. Test Report 必须包含验收标准到测试的映射。

### 11.8 Point-in-time 专项验收

`SKILL.md` 必须包含 point-in-time / backtesting 专项规则。

必须检查：

1. 未来数据泄漏。
2. `latest` / `current` / `today` / `now` 风险。
3. 指定历史日期时默认读最新数据风险。
4. 全量数据预加载后再过滤风险。
5. 未来数据存在但不应读取的测试场景。
6. 快照缺失测试场景。
7. 不同历史日期可见数据不同的测试场景。

### 11.9 可选脚本验收

如果实现 `verify_artifacts.py`，必须满足：

1. 使用 Python 标准库。
2. 支持 `--task-name`。
3. 支持 `--artifact-dir`。
4. 检查必要文件是否存在。
5. 检查关键章节是否存在。
6. 输出 PASS / FAIL。
7. 不修改任何文件。
8. 不依赖外部服务。

---

## 12. Acceptance Policy

该 skill 必须定义明确的接受策略。

只有满足以下条件时，agent 才能将任务标记为“完成”：

1. spec 验收标准已满足。
2. plan 中所有任务已完成，或者未完成项已明确记录并说明原因。
3. 必要测试已运行，并记录命令和结果。
4. Critical / Important review findings 已修复。
5. scope creep 检查通过。
6. 必要 artifact 文件已创建。
7. 所有降级情况已披露。
8. 如果没有独立 review，不得声称 fully verified。

如果无法使用独立 review，但功能实现已完成，必须使用类似表述：

    功能实现已完成，但质量保证仅达到 main-agent 自检级别，建议外部强 agent 二审。

不得使用：

    已完成独立审查
    fully verified
    完全验证

除非确实使用了独立 reviewer / independent context。

---

## 13. 建议实现步骤

建议按以下步骤实现：

1. 检查当前 self_skills 项目结构，确认 skill 放置目录。
2. 创建 `skills/spec-driven-audited-implementation/`。
3. 编写 `SKILL.md`。
4. 创建 templates 目录和 8 个模板文件。
5. 如果时间允许，创建轻量 `verify_artifacts.py`。
6. 自查 `SKILL.md` 是否覆盖所有验收标准。
7. 用一个小型 fake spec 做 dry run，检查 agent 是否能理解调用方式。
8. 输出实现报告。

---

## 14. 最终输出要求

实现完成后，最终回复必须使用中文，并包含：

1. 实现摘要
2. 新增文件列表
3. 修改文件列表
4. 是否创建 templates
5. 是否创建 verify_artifacts.py
6. 验收标准逐条完成情况
7. 已运行的检查命令
8. 剩余风险
9. 使用示例

---

## 15. 额外约束

1. 不要修改与该 skill 无关的现有文件，除非项目结构要求注册 skill。
2. 不要删除现有 skill。
3. 不要重命名现有目录。
4. 不要引入不必要依赖。
5. 不要把报告模板写成过度冗长的文章。
6. 模板应简洁、结构化、便于 agent 填写。
7. `SKILL.md` 应清晰、可执行、面向 agent，而不是长篇理念说明。
8. 如果发现项目已有类似 skill，应优先复用结构，但不得直接覆盖。

# spec-driven-audited-implementation

面向已有 spec 的可审计实现流程 skill。它把“按文档实现”拆成能力检查、计划、计划审查、实现、测试、代码审查、修复和最终审计，并要求留下中文过程产物。

## 适用场景

- 已有 spec、PRD、issue、设计文档或任务 Markdown。
- 任务有中等以上风险：多文件修改、行为变更、数据处理、外部集成、回测/时点数据等。
- 希望使用 Superpowers、subagent、独立测试/审查和 final audit。
- 希望少中途打断，但事后能审计整个执行过程。

## 不适用场景

- 需求仍在探索期，需要先写 spec。
- 很小的直接修改。
- 用户明确要求快速 hack 或不要过程产物。

## 产物目录

默认在当前工作根目录下创建任务目录：

```text
<work_root>/.superpower/<task_name>/
  meta/capability_check.md
  plan/spec_review.md
  plan/plan.md
  plan/plan_review.md
  implementation/implementation_summary.md
  test/test_report.md
  review/code_review.md
  fix/fix_report.md
  audit/final_audit.md
```

`work_root` 优先取当前 git 仓库根目录；如果不在 git 仓库中，则使用 agent 当前工作目录。

## 快速使用

```text
Use superpowers.
Use the spec-driven-audited-implementation skill.

Spec:
./todolist/2026-06-01_point_in_time_backtesting_system.md

Task name:
2026-06-01_point_in_time_backtesting_system
```

## 校验产物

```bash
python3 spec-driven-audited-implementation/scripts/verify_artifacts.py \
  --artifact-dir .superpower/2026-06-01_point_in_time_backtesting_system/
```

旧版扁平目录仍可校验：

```bash
python3 spec-driven-audited-implementation/scripts/verify_artifacts.py \
  --task-name 2026-06-01_point_in_time_backtesting_system \
  --artifact-dir .superpower/plan/
```

## 依赖与配置

- `verify_artifacts.py` 只使用 Python 标准库。
- 不需要额外服务；但执行流程要求 Superpowers 可用。
- 建议配合 Cursor subagent / code-reviewer 使用。

## 关键约束

- Superpowers 是硬门槛，不允许降级继续。
- Planner、Implementer、Tester、Reviewer、Auditor 应尽量分离。
- 每条验收标准必须标证据等级：`E2E`、`UNIT`、`MOCKED`、`STATIC`、`UNVERIFIED`、`ENV_MISMATCH`。
- Final Audit 必须主动寻找反证，不能只总结前序报告。
- point-in-time/backtesting 任务必须有对抗性未来数据测试，否则不能标 `ACCEPTED`。

## 数据与产物

- `.superpower/` 是本地执行产物目录，应由项目 `.gitignore` 排除。
- 产物可以用于复盘、二次审计和跨 agent handoff。

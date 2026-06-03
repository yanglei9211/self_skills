# 能力检查

## 基本信息

- Spec 路径：
- Work root：
- Task artifact 目录：`<work_root>/.superpower/<task_name>/`
- Task name：
- 检查时间：

## 能力可用性

### Superpowers 硬门槛

- Superpowers hard gate：通过 / STOP
- `using-superpowers` 已加载：是 / 否
- 如果 Superpowers 不可用或无法确认，必须停止并询问用户，不允许降级继续。

| 能力 | 状态 | 证据或说明 |
|---|---|---|
| Superpower skills | 可用（必需） |  |
| Goal / Todo tracking | 可用 / 不可用 / 无法确认 |  |
| Subagent | 可用 / 不可用 / 无法确认 |  |
| requesting-code-review skill | 可用 / 不可用 / 无法确认 |  |
| Independent planner context | 可用 / 不可用 / 无法确认 |  |
| Independent implementer context | 可用 / 不可用 / 无法确认 |  |
| Independent reviewer context | 可用 / 不可用 / 无法确认 |  |
| Independent tester context | 可用 / 不可用 / 无法确认 |  |
| Independent auditor context | 可用 / 不可用 / 无法确认 |  |
| Parallel execution | 可用 / 不可用 / 无法确认 |  |

## 编排模式

- Orchestration mode：subagent-orchestrated / main-agent driven execution
- 如果 subagent 可用但没有拆分独立 Planner / Implementer / Tester，必须写：`本次执行未拆分独立 Planner / Implementer / Tester，仅达到 main-agent driven execution，不能视为完整可审计执行。`

## 本次执行模式

| 角色 | 执行方式 / agent | 必用 Superpowers | 是否独立 | 不能复用的角色 | 降级说明 |
|---|---|---|---|---|---|
| Planner |  | using-superpowers, writing-plans | 是 / 否 / 无法确认 | Plan Reviewer |  |
| Plan Reviewer |  | using-superpowers | 是 / 否 / 无法确认 | Planner |  |
| Implementer |  | using-superpowers, test-driven-development, executing-plans/subagent-driven-development | 是 / 否 / 无法确认 | Tester, Code Reviewer, Auditor |  |
| Tester |  | using-superpowers, test-driven-development, verification-before-completion | 是 / 否 / 无法确认 | Implementer, Auditor |  |
| Code Reviewer |  | using-superpowers, requesting-code-review | 是 / 否 / 无法确认 | Implementer |  |
| Fixer |  | using-superpowers, test-driven-development, verification-before-completion | 是 / 否 / 无法确认 |  |  |
| Auditor |  | using-superpowers, verification-before-completion | 是 / 否 / 无法确认 | Planner, Implementer, Tester, Code Reviewer |  |

## 独立性风险

- Review：
- Test design：
- Final audit：
- Planner / Implementer / Tester separation：

## 必须披露的降级

- 如果无法使用真正独立 review，必须写：`本次 review 将降级为 main agent 自检，不能视为最终质量保证。`
- 如果无法使用真正独立测试设计，必须写：`本次测试设计将降级为 main agent 自检，存在测试偏向实现的风险。`
- 如果无法使用真正独立 final audit，必须写：`本次 final audit 将降级为 main agent 自检，建议使用外部强 agent 二审。`
- 如果某项能力无法确认，必须写：`无法确认`，不得假装可用。

## 完成状态规则

- 允许的最终状态：ACCEPTED / CONDITIONAL_ACCEPTED / INCOMPLETE / BLOCKED
- 每条 AC 必须使用证据等级：E2E / UNIT / MOCKED / STATIC / UNVERIFIED / ENV_MISMATCH
- 如果任何 AC 只有 MOCKED / STATIC / UNVERIFIED / ENV_MISMATCH 证据，不能写 fully verified。
- 如果 artifact 里的命令在当前环境不可复现，必须标 ENV_MISMATCH。

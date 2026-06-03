# 最终审计

> 简练约束：Final Audit 只做决策、AC 证据、阻塞项、矛盾、剩余风险。不要复述完整 plan/test/review；用 artifact 路径和 finding ID 引用。

## 总体结论

- Final status：ACCEPTED / CONDITIONAL_ACCEPTED / INCOMPLETE / BLOCKED
- 建议：接受 / 有条件接受 / 不接受
- 结论：

若非独立，写明：`非独立 final audit，仅为 main agent 自检，建议使用外部强 agent 二审。`

## 修改文件

- 

## Artifact 路径

- Capability Check：`meta/capability_check.md`
- Spec Review：`plan/spec_review.md`
- Plan：`plan/plan.md`
- Plan Review：`plan/plan_review.md`
- Implementation Summary：`implementation/implementation_summary.md`
- Test Report：`test/test_report.md`
- Review：`review/code_review.md`
- Fix Report：`fix/fix_report.md`
- Final Audit：`audit/final_audit.md`

## 验收标准状态

| 验收标准 | 状态 | 证据等级 | 证据 | 是否可复现 | 不足说明 |
|---|---|---|---|---|---|
|  | 满足 / 未满足 / 部分满足 / 未验证 | E2E / UNIT / MOCKED / STATIC / UNVERIFIED / ENV_MISMATCH |  | 是 / 否 |  |

红线：`MOCKED`、`STATIC`、`UNVERIFIED`、`ENV_MISMATCH` 不能被总结为 fully verified。

## 已运行命令

```text
<粘贴命令和关键输出>
```

## Fresh Verification Gate

- Auditor fresh run 命令：
- 输出是否与 Test Report 一致：是 / 否
- 如果 Test Report 命令失败但替代命令通过，记录为 `ENV_MISMATCH`：
- 是否有命令仅引用前序 artifact、未重新执行：是 / 否

## Review Findings 与修复状态

| 等级 | Finding | 影响 AC | 状态 | 是否允许延后 | 证据 |
|---|---|---|---|---|---|
|  |  |  | 已修复 / 延后 / 未修复 | 是 / 否 |  |

红线：Important finding 如果违反 spec AC，不能延后同时声称该 AC 已满足。

## Scope Creep 检查

- 是否超出 spec：否 / 是
- 证据：

## 执行假设

- 

## 剩余风险

| 风险 | 严重度 | 影响 AC | 接受影响 | 后续动作 |
|---|---|---|---|---|
| 无 /  | Critical / Important / Minor |  | 阻塞 / 条件接受 / 可接受 |  |

## Artifact 一致性检查

| 检查项 | 结论 | 证据 / 矛盾 |
|---|---|---|
| 测试数量是否一致（如 58/58 vs 57/57） | 一致 / 不一致 |  |
| Test Report 的缺口是否被 Final Audit 正确降级 | 是 / 否 |  |
| Fix Report 延后项是否影响 AC 状态 | 否 / 是 |  |
| 记录命令与实际通过命令是否一致 | 是 / 否 |  |
| Mock/Static/Unverified 是否被误写成 fully verified | 否 / 是 |  |

## 反证审查

Auditor 必须主动尝试推翻“已完成”结论：

| 反证问题 | 结论 | 证据 |
|---|---|---|
| 是否存在 mock 通过但真实 API 未验证？ | 否 / 是 |  |
| 是否存在命令不可复现？ | 否 / 是 |  |
| 是否存在 PIT 对抗性测试缺失？ | 否 / 是 |  |
| 是否存在 CLI wrapper / subprocess 未验证？ | 否 / 是 |  |
| 是否存在默认参数与 spec 示例冲突？ | 否 / 是 |  |
| 是否存在报告承认风险但结论过强？ | 否 / 是 |  |

## 明确没有做的事情

- 

## 独立性披露

| 环节 | 是否独立 | 降级影响 |
|---|---|---|
| Plan Review | 是 / 否 / 无法确认 |  |
| Planner / Implementer / Tester 是否分离 | 是 / 否 / 无法确认 |  |
| Test Design | 是 / 否 / 无法确认 |  |
| Code Review | 是 / 否 / 无法确认 |  |
| Final Audit | 是 / 否 / 无法确认 |  |

没有独立 review 时，只能写：`功能实现已完成，但质量保证仅达到 main-agent 自检级别，建议外部强 agent 二审。`

如果 subagent 可用但没有拆分独立 Planner / Implementer / Tester，只能写：`本次执行未拆分独立 Planner / Implementer / Tester，仅达到 main-agent driven execution，不能视为完整可审计执行。`

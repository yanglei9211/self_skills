---
name: spec-driven-audited-implementation
description: Use when the user already has a spec, PRD, design doc, task markdown, issue, or 需求文档 for a medium/high-risk implementation and wants low-interruption execution plus auditable plan, test, review, and final-audit artifacts.
---

# Spec-Driven Audited Implementation

## Purpose

Use this skill when implementation should follow an existing written spec and leave evidence that a later reviewer can audit. The default workflow is: review the spec, plan, review the plan, implement within scope, test from acceptance criteria, review against the diff, fix findings, and leave Chinese artifacts.

This skill starts after requirements are already written. It is not the right skill for open-ended discovery, brainstorming, or spec writing.

## Fast Trigger Boundary

Use this skill only when the first two signals are true and the third signal is usually true:

- A concrete written spec already exists, such as a spec, PRD, design doc, task markdown, issue, ticket, or `*.md` requirement file.
- The user expects real implementation work, not just explanation, brainstorming, or a code review-only pass.
- The user wants stronger evidence, fewer mid-task interruptions, role separation, or auditable Chinese artifacts.

If these signals are weak, prefer a direct implementation workflow or a spec-writing workflow instead.

Treat a linked markdown file, issue, or PRD as a valid spec if it contains enough detail to implement conservatively: scope, expected behavior or acceptance criteria, and major constraints or non-goals.

## Hard Requirement: Superpowers

Superpowers are mandatory for this skill. Do not downgrade when Superpowers are unavailable.

Before Phase 0 work proceeds, the main agent must load `using-superpowers` and every phase agent must load the relevant Superpowers skill for its role:

- Planner: `writing-plans` plus any required domain/debugging skills.
- Plan Reviewer: `receiving-code-review` or an equivalent review discipline when reviewing feedback.
- Implementer: `test-driven-development` for feature/bugfix work, plus `executing-plans` or `subagent-driven-development` when following a written plan.
- Tester: `test-driven-development` and `verification-before-completion` where applicable; tests must be designed from spec acceptance criteria.
- Code Reviewer: `requesting-code-review` or the code-reviewer subagent protocol.
- Auditor: `verification-before-completion`.

If Superpowers cannot be loaded or the environment cannot confirm Superpowers are available, STOP and ask the user how to proceed. Do not continue as a downgraded audited implementation.

In OpenCode, Superpowers skills are loaded via the `skill` tool (e.g., `skill: using-superpowers`, `skill: writing-plans`). The skills are installed in `~/.claude/skills/` and are auto-discovered. Subagent role dispatch uses OpenCode's `task` tool with `subagent_type: "general"` or `subagent_type: "explore"`. Task tracking uses OpenCode's `todowrite` tool.

## When to Use

- The user provides or links an existing spec, PRD, design doc, task markdown, issue, or `*.md` requirement file.
- The task is medium or high risk: multiple files, behavior changes, correctness-sensitive logic, migrations, data handling, or external integrations.
- The user says things like `按 spec 实现`, `按文档做`, `少打断我`, `给我测试/review/final audit 证据`, or `留执行产物`.
- Testing, review, traceability, or final-audit evidence matters.
- The user asks to use Superpowers, Goal/task tracking, subagents, independent planner/implementer/tester/reviewer/auditor, or auditable artifacts.

## When Not to Use

- Tiny edits, simple Q&A, explanation-only work, or throwaway prototypes.
- No spec exists and requirements still need broad discovery.
- The user explicitly asks for a fast direct change, no artifact files, or minimal process overhead.

## Default Directories

- Spec input may be anywhere; common local default for this workspace is `todolist/`.
- Artifact output root is always under the current work root: `<work_root>/.superpower/<task_name>/`.

Resolve `work_root` in this order:

1. Current git repository root.
2. If not inside a git repository, the agent's current working directory.

Do not write artifacts to a fixed absolute path such as `~/Documents/local2/self_skills/.superpower/plan/`.

If the user gives no `task_name`, derive it from the spec filename without `.md`. If the same task directory already exists and this is a new iteration, create a distinct task name such as `<task_name>_fix_YYYYMMDD` or ask before reusing the directory.

## Artifact Naming

Create this directory structure:

```text
<work_root>/.superpower/<task_name>/
  meta/
    capability_check.md
  plan/
    spec_review.md
    plan.md
    plan_review.md
  implementation/
    implementation_summary.md
  test/
    test_report.md
  review/
    code_review.md
  fix/
    fix_report.md
  audit/
    final_audit.md
```

Use fixed filenames inside the task directory. Do not prefix every file with `<task_name>` inside the task directory.

Templates live in `templates/`. Keep artifacts concise, structured, and Chinese by default.

## Language Requirements

All process artifacts and final user replies are Chinese by default. Keep code, commands, paths, symbols, and original logs in their source language. Do not generate parallel English reports unless the user asks.

## Critical Rule: Independence Disclosure

Do not treat main-agent self-review as independent review.

不得把 main agent 自检伪装成独立 planning、implementation、testing、review 或 final audit。If a truly separate context is unavailable, explicitly disclose the downgrade in `capability_check`, `plan_review`, `test_report`, `review`, and `final_audit`.

Required downgrade wording:

- Review: `非独立 review，仅为 main agent 自检，不能视为最终质量保证。`
- Test design: `非独立测试设计，仅为 main agent 自检，存在测试偏向实现的风险。`
- Final audit: `非独立 final audit，仅为 main agent 自检，建议使用外部强 agent 二审。`

## V2 Orchestration Rule

Default execution mode is subagent-orchestrated. The main agent is the orchestrator: it loads the skill, creates artifacts, dispatches roles, checks returned work, applies fixes only when assigned as Fixer, and reports evidence.

If subagents are available, these roles must be separate contexts by default:

- Planner: reads the spec and produces the plan.
- Plan Reviewer: reads only the spec and plan, then reviews the plan.
- Implementer: applies the reviewed plan.
- Tester: designs tests from the spec and acceptance criteria, not from Implementer notes.
- Code Reviewer: reviews spec, plan, diff, tests, and command output.
- Auditor: reads artifacts, git diff, and verification output to produce final audit.

The same subagent may continue related work only when that preserves independence. Do not use the Implementer as Tester, Code Reviewer, or Auditor. Do not use the Planner as Plan Reviewer.

If subagents are available but Planner, Implementer, and Tester are not separated, the run is downgraded to `main-agent driven execution`. In that case, do not call it full audited implementation. Write: `本次执行未拆分独立 Planner / Implementer / Tester，仅达到 main-agent driven execution，不能视为完整可审计执行。`

If Goal/TodoWrite or an equivalent task tracker is available, track Phase 0-8 explicitly. If unavailable, disclose the downgrade in `capability_check`.

## Execution Quality Control

Audited implementation is not a polished summary. It is an evidence challenge. Every phase must preserve enough evidence for a later agent to disprove inflated claims.

### Report Brevity

Artifacts must be concise. Evidence must be complete enough to audit, but do not duplicate the same narrative across plan, test report, review, fix report, and final audit.

Default size targets:

- Capability Check: <= 80 lines.
- Plan Review, Test Report, Code Review, Fix Report: <= 120 lines each unless there are many real findings.
- Final Audit: <= 150 lines.

Rules:

- Put raw command output in fenced blocks only when it is short and decisive; otherwise paste the command, exit code, and key lines.
- Prefer one acceptance-criteria table with evidence levels over repeated paragraphs.
- Do not repeat full file lists in every artifact; link to the artifact or summarize changed areas.
- Do not restate every non-goal in Final Audit unless it affects acceptance.
- For repeated findings, reference the original finding ID instead of rewriting the full explanation.
- Final Audit should focus on decision, blockers, AC evidence, contradictions, and residual risks.
- If an artifact exceeds the target, add a one-line reason such as `篇幅超限原因：findings 数量较多`.

### Completion Status State Machine

Use exactly one final status:

- `ACCEPTED`: every acceptance criterion is satisfied with fresh reproducible evidence; Critical/Important findings are fixed.
- `CONDITIONAL_ACCEPTED`: core behavior works, but some non-blocking verification or production-readiness items remain. Do not describe this as merge-ready or production-ready.
- `INCOMPLETE`: any acceptance criterion is unmet or unverified.
- `BLOCKED`: required dependency, permission, environment, user input, or external service is unavailable.

Do not mark an acceptance criterion as passed unless its evidence is fresh and reproducible. Unverified acceptance criteria mean `INCOMPLETE` or `BLOCKED`, not `ACCEPTED`.

### Evidence Levels

Every acceptance criterion in Test Report and Final Audit must include an evidence level:

- `E2E`: real CLI, subprocess, browser, API, or end-to-end command verifies the behavior.
- `UNIT`: unit test verifies the behavior.
- `MOCKED`: mock/fake/stub verifies local logic only.
- `STATIC`: code reading, grep, diff, or structural inspection.
- `UNVERIFIED`: not verified.
- `ENV_MISMATCH`: the recorded command does not run in the current environment.

Mocked tests cannot prove real external API behavior. Static review cannot prove runtime behavior. If the artifact command fails in the current environment, the prior evidence is invalid until corrected or marked `ENV_MISMATCH`.

### Role Skepticism

Separated roles must not trust each other blindly:

- Tester must derive tests from the spec and acceptance criteria, not from Implementer notes.
- Code Reviewer must inspect code, tests, and diff, not Implementation Summary conclusions.
- Auditor must challenge prior artifacts and look for contradictions, not summarize them.
- Main agent must verify key subagent claims before presenting them as fact.

Agent success reports are not evidence. Commands, code, diffs, and reproducible outputs are evidence.

### Red Lines

- Do not mark an AC as passed without fresh reproducible evidence.
- Do not use mocked tests as proof of real external API behavior.
- Do not accept point-in-time/backtesting work without adversarial future-data tests.
- Do not call a run complete if the artifact command fails in the current environment.
- Do not defer an Important finding that violates a spec acceptance criterion while claiming acceptance.
- Do not let Final Audit merely summarize prior reports; it must challenge them.
- Do not claim CLI support without subprocess or wrapper-level verification.
- Do not claim default command support without running the spec's example command or marking it unverified.

### Artifact Consistency Checks

Final Audit must check for contradictions across artifacts:

- Test counts disagree, such as `58/58` vs `57/57`.
- Test Report says a critical gap remains but Final Audit marks the related AC as passed.
- Fix Report defers a finding while Final Audit marks the related AC as satisfied.
- The recorded verification command differs from the command that actually passes.
- An item appears as `MOCKED`, `STATIC`, `UNVERIFIED`, or `ENV_MISMATCH` but is summarized as fully verified.

## Execution Protocol

### Phase 0: Capability Check

Before planning or coding, create `meta/capability_check.md` inside the task artifact directory.

Record the spec path, artifact directory, task name, Superpowers hard-gate status, Goal/Todo tracking availability, subagent availability, requesting-code-review availability, independent planner/implementer/tester/reviewer/auditor availability, parallel execution availability, orchestration mode, actual role mapping, downgrade notes, and independence risks.

If unsure about non-Superpowers capabilities, write `无法确认`. Never pretend a capability is available. If Superpowers are unavailable or cannot be confirmed, stop instead of downgrading.

### Phase 1: Spec Review

Read the spec before coding. Identify blocking ambiguities, non-blocking assumptions, hidden edge cases, acceptance criteria, implementation risks, non-goals, forbidden changes, and scope creep risks.

Ask the user only for truly blocking issues: incompatible data model choices, unclear public API behavior, irreversible migration risk, security-sensitive behavior, or business rules where guessing would likely create wrong behavior. For ordinary ambiguity, choose the smallest conservative assumption and record it.

Record the result in `plan/spec_review.md`.

### Phase 2: Plan

Create `plan/plan.md` before implementation. If subagents are available, dispatch an independent Planner subagent to draft it from the spec. The main agent may refine formatting, but must not silently replace the Planner's substance without recording the deviation.

Include spec review results, assumptions, acceptance criteria, implementation tasks, test strategy, review strategy, risks, rollback notes, and explicit non-goals. Each task should state expected changes, likely files, tests, validation command, review need, risk level, and rollback approach.

Do not expand scope, introduce dependencies, change public APIs, or do unrelated formatting unless the spec requires it.

### Phase 3: Plan Review

Create `plan/plan_review.md`. If subagents are available, dispatch an independent Plan Reviewer that is not the Planner.

Inputs are only the spec and plan. Check acceptance coverage, missing edges, scope creep, test gaps, coarse task splits, weak risk analysis, and unnecessary public API or data model changes.

If review is not independent, write: `非独立 plan review，仅为 main agent 自检。`

### Phase 4: Implementation

Implement task by task from the plan. If subagents are available, dispatch an Implementer subagent that did not author the plan review. Before each task, state the current task. After each task, update `implementation/implementation_summary.md` with completed work, modified files, validation commands, results, failures/fixes, plan deviations, and unfinished items.

Do not delete tests, weaken assertions, skip tests to hide failures, swallow errors, over-mock core logic, or claim broad verification from narrow commands.

### Phase 5: Test Design and Test Report

Create `test/test_report.md`. If subagents are available, dispatch a Tester subagent that is not the Implementer. Design tests from the spec and acceptance criteria, not from implementation details.

Record test mode (`independent tester`, `independent session`, `simulated tester role`, or `main-agent self-check`), acceptance-to-test mapping, evidence level, exact commands, environment/interpreter, test files, results, failures, fixes, uncovered risks, and downgrade notes.

Check happy paths, boundaries, failure paths, deleted/skipped/weakened tests, over-mocking, and implementation-detail testing.

### Phase 6: Code Review

Create `review/code_review.md`. Use `requesting-code-review` or an equivalent independent reviewer when available. The Code Reviewer must not be the Implementer or Tester.

Reviewer inputs must include the spec, plan, actual git diff, test files, and test command results. The reviewer must seek counterexamples, not confirmation.

Findings are grouped as Critical, Important, and Minor. Each finding includes file path, problem, impact, fix suggestion, and whether it must be fixed. If no Critical/Important findings exist, state which high-risk areas were checked.

### Phase 7: Fix Review Findings

Create `fix/fix_report.md`. Fix all Critical and Important findings. Fix low-risk Minor findings when reasonable; otherwise record why not.

Record findings, fixes, files changed, rerun commands, results, and unresolved items. If Critical or Important findings cannot be fixed, stop and report the blocker.

### Phase 8: Final Audit

Create `audit/final_audit.md` from evidence: spec, plan, plan review, implementation summary, test report, review report, fix report, git diff, and verification output. If subagents are available, dispatch an Auditor that is not the Planner, Implementer, Tester, or Code Reviewer.

Include final status (`ACCEPTED`, `CONDITIONAL_ACCEPTED`, `INCOMPLETE`, or `BLOCKED`), accept/reject recommendation, changed files, artifact paths, acceptance criteria status with evidence level, fresh commands run, test results, review/fix status, scope creep check, assumptions, residual risks, explicit non-goals, artifact consistency checks, contradiction review, and whether independent reviewer/tester/auditor were truly used.

Without independent review, do not write `已完成独立审查`, `fully verified`, or `完全验证`. Use: `功能实现已完成，但质量保证仅达到 main-agent 自检级别，建议外部强 agent 二审。`

## Point-in-Time / Backtesting Rule

If the spec or task name mentions `point-in-time`, `backtesting`, `回测`, `历史时间点`, `快照`, `snapshot`, `未来数据`, `future leakage`, `lookahead`, `latest`, `current`, `today`, or `now`, Test Report and Review Report must explicitly check future-data leakage.

Check whether historical runs accidentally read latest/current data, whether full datasets are preloaded before date filtering, whether system date or file mtime affects results, and whether tests cover future data present but unread, missing target snapshots, and different visible data across dates.

PIT/backtesting work is not `ACCEPTED` without adversarial future-data tests:

- Database/source contains T+1/T+N future rows that would change the signal if leaked.
- Snapshot(T) excludes those future rows.
- T-day signal values match a manual or independent calculation from data `<= T`.
- T1 < T2 snapshot visibility differs predictably, preferably with subset checks.
- Full datasets used for forward tracking cannot enter the signal path.

If these checks are impossible, explain why and record the risk in Final Audit.

## Acceptance Policy

Only mark the task complete when:

- Spec acceptance criteria are satisfied. Unresolved acceptance criteria mean the task is blocked or incomplete, not complete.
- Plan tasks are complete or exceptions are documented.
- Required tests were run and command output is recorded.
- Critical and Important review findings are fixed.
- Scope creep check passed.
- Required artifacts exist.
- Downgrades are disclosed.
- No non-independent work is described as independent.
- If subagents were available, Planner / Implementer / Tester were separate contexts, or the run is explicitly marked `main-agent driven execution` and not described as full audited implementation.
- Every AC has an evidence level, and no `MOCKED`, `STATIC`, `UNVERIFIED`, or `ENV_MISMATCH` item is described as fully verified.
- Final Audit challenged prior artifacts and recorded contradiction checks.

## Example Invocation

```text
Use superpowers.
Use the spec-driven-audited-implementation skill.

Spec:
./todolist/2026-06-01_point_in_time_backtesting_system.md

Task name:
2026-06-01_point_in_time_backtesting_system

Goal:
按照 skill 协议审查 spec、创建中文执行产物、实现需求、测试、review、修复、final audit。

Artifact dir:
./.superpower/2026-06-01_point_in_time_backtesting_system/
```

## Verification Helper

After producing artifacts, run:

```bash
python3 <path-to-self_skills>/spec-driven-audited-implementation/scripts/verify_artifacts.py \
  --artifact-dir <work_root>/.superpower/<task_name>/
```

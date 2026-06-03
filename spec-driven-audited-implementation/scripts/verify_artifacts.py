#!/usr/bin/env python3
"""Verify spec-driven-audited-implementation artifact structure."""

from __future__ import annotations

import argparse
from pathlib import Path


ARTIFACTS = {
    "capability_check": {
        "path": "meta/capability_check.md",
        "legacy_suffix": "capability_check",
        "required": ["Spec", "Task", "Superpowers hard gate", "using-superpowers", "必用 Superpowers", "能力", "编排模式", "Planner", "Implementer", "Tester", "独立性", "完成状态规则", "证据等级"],
    },
    "plan": {
        "path": "plan/plan.md",
        "legacy_suffix": "plan",
        "required": ["角色来源", "Planner", "Spec", "验收标准", "实现任务", "测试策略", "Review"],
    },
    "plan_review": {
        "path": "plan/plan_review.md",
        "legacy_suffix": "plan_review",
        "required": ["Review 模式", "Plan Reviewer", "与 Planner 是否分离", "检查结果", "Findings"],
    },
    "implementation_summary": {
        "path": "implementation/implementation_summary.md",
        "legacy_suffix": "implementation_summary",
        "required": ["Implementer", "与 Tester / Code Reviewer / Auditor 是否分离", "已完成任务", "验证命令", "与 plan 偏差"],
    },
    "test_report": {
        "path": "test/test_report.md",
        "legacy_suffix": "test_report",
        "required": ["测试设计模式", "Tester", "与 Implementer 是否分离", "验收标准到测试映射", "证据等级", "Fresh Verification", "测试命令", "未覆盖风险", "测试结论状态"],
    },
    "review": {
        "path": "review/code_review.md",
        "legacy_suffix": "review",
        "required": ["Review 模式", "Code Reviewer", "与 Implementer 是否分离", "Review 输入", "Findings", "高风险点", "默认值审查"],
    },
    "fix_report": {
        "path": "fix/fix_report.md",
        "legacy_suffix": "fix_report",
        "required": ["Review Finding", "已修复", "未修复", "验证"],
    },
    "final_audit": {
        "path": "audit/final_audit.md",
        "legacy_suffix": "final_audit",
        "required": ["总体结论", "Final status", "验收标准状态", "证据等级", "Fresh Verification Gate", "已运行命令", "剩余风险", "Artifact 一致性检查", "反证审查", "独立性披露", "Planner / Implementer / Tester 是否分离"],
    },
}

LINE_TARGETS = {
    "capability_check": 80,
    "plan_review": 120,
    "test_report": 120,
    "review": 120,
    "fix_report": 120,
    "final_audit": 150,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check required audit artifacts exist and contain key sections."
    )
    parser.add_argument(
        "--task-name",
        help="Legacy artifact filename prefix. Not needed for the new nested layout.",
    )
    parser.add_argument(
        "--artifact-dir",
        required=True,
        help="New layout: <work-root>/.superpower/<task-name>/. Legacy layout: directory containing <task-name>_<artifact>.md files.",
    )
    return parser.parse_args()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text()


def main() -> int:
    args = parse_args()
    artifact_dir = Path(args.artifact_dir).expanduser()
    failures: list[str] = []
    warnings: list[str] = []

    if not artifact_dir.exists():
        print("FAIL")
        print(f"Missing artifact directory: {artifact_dir}")
        return 1

    if args.task_name and (artifact_dir / args.task_name).is_dir():
        artifact_dir = artifact_dir / args.task_name

    new_layout = any((artifact_dir / spec["path"]).exists() for spec in ARTIFACTS.values())
    legacy_layout = bool(args.task_name) and any(
        (artifact_dir / f"{args.task_name}_{spec['legacy_suffix']}.md").exists()
        for spec in ARTIFACTS.values()
    )
    if not new_layout and not legacy_layout:
        warnings.append(
            "No nested artifacts found. Expected <artifact-dir>/meta, plan, implementation, test, review, fix, audit."
        )

    def artifact_path(name: str, spec: dict) -> Path:
        if new_layout:
            return artifact_dir / spec["path"]
        if not args.task_name:
            return artifact_dir / spec["path"]
        return artifact_dir / f"{args.task_name}_{spec['legacy_suffix']}.md"

    for suffix, spec in ARTIFACTS.items():
        path = artifact_path(suffix, spec)
        if not path.exists():
            failures.append(f"missing file: {path}")
            continue

        text = read_text(path).strip()
        if not text:
            failures.append(f"empty file: {path}")
            continue

        missing_terms = [term for term in spec["required"] if term not in text]
        if missing_terms:
            failures.append(
                f"missing sections in {path.name}: {', '.join(missing_terms)}"
            )
        line_target = LINE_TARGETS.get(suffix)
        if line_target is not None:
            line_count = len(text.splitlines())
            if line_count > line_target and "篇幅超限原因" not in text:
                warnings.append(
                    f"{path.name} has {line_count} lines; target is <= {line_target}. Add 篇幅超限原因 or make it more concise."
                )

    final_audit = artifact_path("final_audit", ARTIFACTS["final_audit"])
    if final_audit.exists() and final_audit.stat().st_size:
        final_text = read_text(final_audit)
        risky_phrases = ["fully verified", "完全验证", "已完成独立审查"]
        found_risky = [phrase for phrase in risky_phrases if phrase in final_text]
        if found_risky:
            warnings.append(
                "final_audit uses strong verification wording; verify independent reviewer/tester/auditor evidence before accepting: "
                + ", ".join(found_risky)
            )
        weak_evidence_terms = ["MOCKED", "STATIC", "UNVERIFIED", "ENV_MISMATCH", "未验证"]
        has_weak_evidence = any(term in final_text for term in weak_evidence_terms)
        has_strong_pass = any(
            term in final_text for term in ["ACCEPTED", "完全通过", "全部通过", "所有验收标准"]
        )
        if has_weak_evidence and has_strong_pass:
            warnings.append(
                "final_audit mixes weak/unverified evidence with strong acceptance wording"
            )

    all_text = ""
    for suffix, spec in ARTIFACTS.items():
        path = artifact_path(suffix, spec)
        if path.exists() and path.stat().st_size:
            all_text += "\n" + read_text(path)

    for count_pair in [("58/58", "57/57"), ("58 个", "57 个")]:
        if all(term in all_text for term in count_pair):
            warnings.append(
                f"artifact test counts may conflict: {count_pair[0]} and {count_pair[1]}"
            )

    contradiction_pairs = [
        ("严重缺口", "ACCEPTED"),
        ("未验证", "满足"),
        ("ENV_MISMATCH", "通过"),
        ("MOCKED", "真实"),
    ]
    for weak, strong in contradiction_pairs:
        if weak in all_text and strong in all_text:
            warnings.append(
                f"artifact may contain contradiction: '{weak}' appears with '{strong}'"
            )

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
    else:
        print("PASS")

    if warnings:
        print("WARNINGS")
        for warning in warnings:
            print(f"- {warning}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

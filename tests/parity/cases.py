"""Baseline parity cases for Phase 2 commit 13 (bootstrap).

Five deterministic cases — one per registered runner kind. They
exercise the framework end-to-end against modules whose v1 and
v2 entry points share an implementation (shim era). Subsequent
REWRITE commits 14–18 swap the underlying v2 callable; commit 19
expands this list to 30 to satisfy the G2 gate (≥95% parity).
"""

from __future__ import annotations

from .harness import ParityCase

BASELINE_CASES: list[ParityCase] = [
    ParityCase(
        id="permission-plan-mode-blocks-shell",
        kind="permission_mode",
        label="plan mode denies shell exec",
        inputs={
            "mode": "plan",
            "tool_name": "run_shell",
            "tool_input": {"command": "ls"},
        },
    ),
    ParityCase(
        id="permission-ask-mode-allows-read",
        kind="permission_mode",
        label="ask mode allows read_file",
        inputs={
            "mode": "ask",
            "tool_name": "read_file",
            "tool_input": {"file_path": "README.md"},
        },
    ),
    ParityCase(
        id="token-budget-half-and-exceed",
        kind="token_budget",
        label="token budget warns then exceeds",
        inputs={
            "total_limit": 1000,
            "deltas": [200, 600, 250],
        },
    ),
    ParityCase(
        id="working-facts-extract-and-merge",
        kind="working_facts",
        label="working facts extract + merge",
        inputs={
            "initial": {},
            "message": "我叫小红，我在上海。",
            "source_turn": 3,
        },
    ),
    ParityCase(
        id="loop-budget-tool-call-overflow",
        kind="loop_budget",
        label="loop budget terminates after tool-call overflow",
        inputs={
            "max_total_tool_calls": 3,
            "rounds": [
                ["read_file"],
                ["read_file"],
                ["read_file", "grep"],
            ],
        },
    ),
    ParityCase(
        id="trusted-paths-workspace-scratch",
        kind="trusted_paths",
        label="workspace scratch dir is trusted",
        inputs={
            "message": "请清理 workspace/playground/legacy 里的临时文件",
        },
    ),
    ParityCase(
        id="smart-truncate-large-content",
        kind="smart_truncate",
        label="smart_truncate compresses oversized text",
        inputs={
            "text": "abcd" * 5000,
            "limit": 1000,
            "label": "tool_output",
            "head_ratio": 0.6,
        },
    ),
    ParityCase(
        id="smart-truncate-under-limit",
        kind="smart_truncate",
        label="smart_truncate keeps short text intact",
        inputs={
            "text": "short result",
            "limit": 1000,
            "label": "tool_output",
        },
    ),
    ParityCase(
        id="context-estimate-tokens-mixed",
        kind="context_estimate_tokens",
        label="estimate_tokens on bilingual content",
        inputs={
            "text": "Hello world，这是一个用于上下文估算的混合中英文样本。" * 10,
        },
    ),
    ParityCase(
        id="context-estimate-tokens-empty",
        kind="context_estimate_tokens",
        label="estimate_tokens on empty string is zero",
        inputs={"text": ""},
    ),
]


__all__ = ["BASELINE_CASES"]

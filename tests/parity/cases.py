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
    ParityCase(
        id="brain-response-with-tool-call",
        kind="brain_response",
        label="Brain Response round-trips content + tool_calls",
        inputs={
            "content": "Plan: read README then summarise.",
            "tool_calls": [
                {"name": "read_file", "input": {"file_path": "README.md"}, "id": "t1"},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 120, "output_tokens": 80},
        },
    ),
    ParityCase(
        id="brain-response-empty",
        kind="brain_response",
        label="Brain Response default ctor parity",
        inputs={"content": ""},
    ),
    ParityCase(
        id="reasoning-decision-final-answer",
        kind="reasoning_decision",
        label="Decision FINAL_ANSWER parity",
        inputs={
            "decision_type": "FINAL_ANSWER",
            "text_content": "已完成",
            "stop_reason": "end_turn",
        },
    ),
    ParityCase(
        id="reasoning-decision-tool-calls",
        kind="reasoning_decision",
        label="Decision TOOL_CALLS parity",
        inputs={
            "decision_type": "TOOL_CALLS",
            "tool_calls": [
                {"name": "grep", "input": {"pattern": "Brain"}, "id": "t9"},
            ],
            "stop_reason": "tool_use",
        },
    ),
    ParityCase(
        id="primary-agent-set-get-roundtrip",
        kind="primary_agent",
        label="primary agent registry set/get round-trips",
        inputs={},
    ),
    # ----- expansion to 30 (commit 19, G2 sign-off) -----
    ParityCase(
        id="permission-agent-mode-allows-shell",
        kind="permission_mode",
        label="agent mode passes through shell",
        inputs={
            "mode": "agent",
            "tool_name": "run_shell",
            "tool_input": {"command": "echo hi"},
        },
    ),
    ParityCase(
        id="permission-coordinator-mode-blocks-write",
        kind="permission_mode",
        label="coordinator mode blocks write_file",
        inputs={
            "mode": "coordinator",
            "tool_name": "write_file",
            "tool_input": {"file_path": "x.txt", "content": "hi"},
        },
    ),
    ParityCase(
        id="token-budget-empty",
        kind="token_budget",
        label="token budget starts at zero",
        inputs={"total_limit": 5000, "deltas": []},
    ),
    ParityCase(
        id="loop-budget-no-overflow",
        kind="loop_budget",
        label="loop budget under cap continues",
        inputs={
            "max_total_tool_calls": 10,
            "rounds": [["read_file"], ["read_file"]],
        },
    ),
    ParityCase(
        id="trusted-paths-untrusted",
        kind="trusted_paths",
        label="non-workspace path is untrusted",
        inputs={"message": "请删除 /etc/passwd"},
    ),
    ParityCase(
        id="context-estimate-tokens-english",
        kind="context_estimate_tokens",
        label="estimate_tokens English-only ascii",
        inputs={"text": "Hello world. " * 50},
    ),
    ParityCase(
        id="confirm-normalize-yes",
        kind="confirm_normalize",
        label="confirmation 'yes' maps to CONFIRM",
        inputs={"answer": "yes"},
    ),
    ParityCase(
        id="confirm-normalize-chinese-no",
        kind="confirm_normalize",
        label="confirmation '不' maps to non-confirm",
        inputs={"answer": "不"},
    ),
    ParityCase(
        id="confirm-normalize-empty",
        kind="confirm_normalize",
        label="empty answer maps to non-confirm",
        inputs={"answer": ""},
    ),
    ParityCase(
        id="domain-allowlist-default-allow",
        kind="domain_allowlist",
        label="default domain decision is allow",
        inputs={
            "conversation_id": "conv-default",
            "host": "github.com",
            "blocked": [],
            "allowed": [],
        },
    ),
    ParityCase(
        id="domain-allowlist-blocked-host",
        kind="domain_allowlist",
        label="explicitly blocked host is denied",
        inputs={
            "conversation_id": "conv-blocked",
            "host": "evil.example.com",
            "blocked": ["evil.example.com"],
            "allowed": [],
        },
    ),
    ParityCase(
        id="capability-id-skill-system",
        kind="capability_id",
        label="capability id for system skill",
        inputs={
            "kind": "skill",
            "local_id": "Code Reviewer",
            "origin": "system",
        },
    ),
    ParityCase(
        id="capability-id-plugin-tool",
        kind="capability_id",
        label="capability id for plugin tool",
        inputs={
            "kind": "tool",
            "local_id": "fetch:web",
            "origin": "plugin",
            "plugin_id": "my-plugin",
        },
    ),
    ParityCase(
        id="user-profile-resolve-name-alias",
        kind="user_profile_resolve",
        label="resolve user profile name alias",
        inputs={"alias": "name"},
    ),
    ParityCase(
        id="user-profile-resolve-unknown-alias",
        kind="user_profile_resolve",
        label="unknown alias returns None",
        inputs={"alias": "definitely_not_a_profile_key"},
    ),
]


__all__ = ["BASELINE_CASES"]

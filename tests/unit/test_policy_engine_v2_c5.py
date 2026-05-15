"""C5 unit tests: PolicyConfigV2 ↔ PolicyEngineV2 wire-up.

覆盖：
- 配置驱动的 ``safety_immune.paths`` 合并（config + ctx）
- ``owner_only.tools`` 显式列表 vs CONTROL_PLANE 启发式
- ``approval_classes.overrides`` + ``most_strict`` 不可削弱
- ``shell_risk.custom_*`` / ``blocked_commands`` 透传到 classifier
- ``unattended.default_strategy`` 5 种语义 + ctx override
- ``ReplayAuthorization`` 30s TTL + 消息匹配
- ``TrustedPathOverride`` sticky + path_pattern 正则
- ``build_engine_from_config`` 工厂端到端
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openakita.core.policy_v2 import (
    ApprovalClass,
    ApprovalClassesConfig,
    ConfirmationMode,
    DecisionAction,
    OwnerOnlyConfig,
    PolicyConfigV2,
    PolicyContext,
    PolicyEngineV2,
    ReplayAuthorization,
    SafetyImmuneConfig,
    SessionRole,
    ShellRiskConfig,
    ToolCallEvent,
    TrustedPathOverride,
    UnattendedConfig,
    build_engine_from_config,
)


def _ctx(
    *,
    role: SessionRole = SessionRole.AGENT,
    mode: ConfirmationMode = ConfirmationMode.DEFAULT,
    workspace: str = "/ws",
    is_unattended: bool = False,
    unattended_strategy: str = "",
    user_message: str = "",
    immune: tuple[str, ...] = (),
    replays: list[ReplayAuthorization] | None = None,
    trusts: list[TrustedPathOverride] | None = None,
    is_owner: bool = True,
    channel: str = "desktop",
) -> PolicyContext:
    return PolicyContext(
        session_id="s1",
        workspace=Path(workspace),
        channel=channel,
        is_owner=is_owner,
        session_role=role,
        confirmation_mode=mode,
        is_unattended=is_unattended,
        unattended_strategy=unattended_strategy,
        user_message=user_message,
        safety_immune_paths=immune,
        replay_authorizations=replays or [],
        trusted_path_overrides=trusts or [],
    )


def _evt(tool: str, **params: object) -> ToolCallEvent:
    return ToolCallEvent(tool=tool, params=dict(params))


# ---------------------------------------------------------------------------
# Step 3: safety_immune
# ---------------------------------------------------------------------------


class TestSafetyImmuneFromConfig:
    def test_config_immune_path_triggers_confirm(self) -> None:
        config = PolicyConfigV2(safety_immune=SafetyImmuneConfig(paths=["/etc"]))
        engine = PolicyEngineV2(config=config)
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/etc/passwd"),
            _ctx(mode=ConfirmationMode.TRUST),  # trust 模式应该被 immune 推回
        )
        assert decision.action == DecisionAction.CONFIRM
        assert decision.safety_immune_match is not None
        assert "/etc/passwd" in decision.safety_immune_match

    def test_ctx_immune_unioned_with_config(self) -> None:
        config = PolicyConfigV2(safety_immune=SafetyImmuneConfig(paths=["/etc"]))
        engine = PolicyEngineV2(config=config)
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/dynamic/protected/x"),
            _ctx(mode=ConfirmationMode.TRUST, immune=("/dynamic/protected",)),
        )
        assert decision.action == DecisionAction.CONFIRM

    def test_no_immune_paths_no_block(self) -> None:
        engine = PolicyEngineV2(config=PolicyConfigV2())
        decision = engine.evaluate_tool_call(
            _evt("read_file", path="/anywhere"),
            _ctx(mode=ConfirmationMode.TRUST),
        )
        assert decision.action == DecisionAction.ALLOW

    def test_immune_path_dedupe_when_overlapping(self) -> None:
        """config + ctx 重叠路径不应造成多次匹配。"""
        config = PolicyConfigV2(safety_immune=SafetyImmuneConfig(paths=["/etc"]))
        engine = PolicyEngineV2(config=config)
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/etc/x"),
            _ctx(mode=ConfirmationMode.TRUST, immune=("/etc",)),
        )
        # 只应该有 1 个 safety_immune chain step
        immune_steps = [
            s
            for s in decision.chain
            if "safety_immune" in s.note.lower() or s.name == "safety_immune"
        ]
        assert len(immune_steps) == 1


# ---------------------------------------------------------------------------
# Step 4: owner_only
# ---------------------------------------------------------------------------


class TestOwnerOnlyFromConfig:
    def test_config_owner_only_blocks_non_owner(self) -> None:
        config = PolicyConfigV2(owner_only=OwnerOnlyConfig(tools=["run_shell"]))
        engine = PolicyEngineV2(config=config)
        decision = engine.evaluate_tool_call(
            _evt("run_shell", command="ls"),
            _ctx(is_owner=False, channel="im:telegram"),
        )
        assert decision.action == DecisionAction.DENY
        assert decision.is_owner_required is True

    def test_owner_can_use_owner_only_tool(self) -> None:
        config = PolicyConfigV2(owner_only=OwnerOnlyConfig(tools=["run_shell"]))
        engine = PolicyEngineV2(config=config)
        decision = engine.evaluate_tool_call(
            _evt("run_shell", command="ls"),
            _ctx(is_owner=True),
        )
        # Not blocked by owner_only; will pass to subsequent steps
        assert not decision.is_owner_required

    def test_control_plane_default_owner_only(self) -> None:
        """未在 config.owner_only 列出，但 CONTROL_PLANE 类启发式应触发 owner-only。"""
        engine = PolicyEngineV2(config=PolicyConfigV2())
        decision = engine.evaluate_tool_call(
            _evt("system_shutdown"),  # heuristic CONTROL_PLANE
            _ctx(is_owner=False, channel="im:telegram"),
        )
        assert decision.action == DecisionAction.DENY


# ---------------------------------------------------------------------------
# Step 2b: approval_classes.overrides
# ---------------------------------------------------------------------------


class TestApprovalOverrides:
    def test_override_stricter_than_classifier_applied(self) -> None:
        """heuristic 把 ``custom_tool`` 判 UNKNOWN；override 升 DESTRUCTIVE 应被采纳。"""
        config = PolicyConfigV2(
            approval_classes=ApprovalClassesConfig(
                overrides={"custom_tool": ApprovalClass.DESTRUCTIVE}
            )
        )
        engine = PolicyEngineV2(config=config)
        decision = engine.evaluate_tool_call(
            _evt("custom_tool"),
            _ctx(mode=ConfirmationMode.TRUST),
        )
        # DESTRUCTIVE in TRUST mode → matrix CONFIRM (per matrix.py)
        assert decision.approval_class == ApprovalClass.DESTRUCTIVE
        assert any("approval_override_applied" in s.name for s in decision.chain)

    def test_override_weaker_than_classifier_ignored(self) -> None:
        """heuristic 判 ``delete_file`` 为 DESTRUCTIVE；override 想降到 READONLY，应被忽略。"""
        config = PolicyConfigV2(
            approval_classes=ApprovalClassesConfig(
                overrides={"delete_file": ApprovalClass.READONLY_GLOBAL}
            )
        )
        engine = PolicyEngineV2(config=config)
        decision = engine.evaluate_tool_call(
            _evt("delete_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.TRUST),
        )
        # 仍按 DESTRUCTIVE 走（override 被忽略）
        assert decision.approval_class == ApprovalClass.DESTRUCTIVE
        assert any("approval_override_ignored" in s.name for s in decision.chain)

    def test_no_override_no_chain_step(self) -> None:
        engine = PolicyEngineV2(config=PolicyConfigV2())
        decision = engine.evaluate_tool_call(
            _evt("read_file", path="/ws/x"),
            _ctx(),
        )
        assert not any("approval_override" in s.name for s in decision.chain)

    def test_override_preserves_shell_risk_metadata(self) -> None:
        """override 升级 class 时不应丢失 classifier 算出的 shell_risk_level。"""
        config = PolicyConfigV2(
            shell_risk=ShellRiskConfig(custom_high=[r"my_risky_cmd"]),
            approval_classes=ApprovalClassesConfig(
                overrides={"run_shell": ApprovalClass.DESTRUCTIVE}
            ),
        )
        engine = build_engine_from_config(config)
        decision = engine.evaluate_tool_call(
            _evt("run_shell", command="my_risky_cmd --x"),
            _ctx(),
        )
        assert decision.shell_risk_level == "high"
        assert decision.needs_sandbox is True


# ---------------------------------------------------------------------------
# Shell risk customs (via classifier)
# ---------------------------------------------------------------------------


class TestShellRiskCustomsFlow:
    def test_user_custom_critical_pattern_recognized(self) -> None:
        config = PolicyConfigV2(shell_risk=ShellRiskConfig(custom_critical=[r"my_dangerous_cmd"]))
        engine = build_engine_from_config(config)
        decision = engine.evaluate_tool_call(
            _evt("run_shell", command="my_dangerous_cmd --force"),
            _ctx(mode=ConfirmationMode.TRUST),
        )
        # CRITICAL → DESTRUCTIVE → matrix CONFIRM in TRUST
        assert decision.approval_class == ApprovalClass.DESTRUCTIVE
        assert decision.shell_risk_level == "critical"

    def test_user_blocked_command_overrides_default_list(self) -> None:
        """显式 ``blocked_commands`` 覆盖 module 默认表（loader 是替换非 union）。"""
        config = PolicyConfigV2(shell_risk=ShellRiskConfig(blocked_commands=["only_my_blocker"]))
        engine = build_engine_from_config(config)
        decision = engine.evaluate_tool_call(
            _evt("run_shell", command="only_my_blocker --x"),
            _ctx(),
        )
        # Should hit BLOCKED → DESTRUCTIVE
        assert decision.shell_risk_level == "blocked"

    def test_disabled_shell_risk_skips_refine(self) -> None:
        config = PolicyConfigV2(shell_risk=ShellRiskConfig(enabled=False))
        engine = build_engine_from_config(config)
        decision = engine.evaluate_tool_call(
            _evt("run_shell", command="rm -rf /"),  # would normally be CRITICAL
            _ctx(mode=ConfirmationMode.TRUST),
        )
        # Without shell_risk: classifier returns base EXEC_CAPABLE; matrix in trust = CONFIRM
        assert decision.shell_risk_level is None


# ---------------------------------------------------------------------------
# Step 7: replay_authorization
# ---------------------------------------------------------------------------


class TestReplayAuthorization:
    def test_active_msg_match_relaxes_to_allow(self) -> None:
        engine = PolicyEngineV2(config=PolicyConfigV2())
        auth = ReplayAuthorization(
            expires_at=time.time() + 30,
            original_message="please overwrite /ws/x",
            confirmation_id="abc",
        )
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(
                mode=ConfirmationMode.DEFAULT,
                user_message="please overwrite /ws/x",
                replays=[auth],
            ),
        )
        # write_file in workspace + DEFAULT → matrix CONFIRM, then replay relaxes
        assert decision.action == DecisionAction.ALLOW
        assert any(s.name == "replay" for s in decision.chain)

    def test_expired_authorization_does_not_relax(self) -> None:
        engine = PolicyEngineV2(config=PolicyConfigV2())
        auth = ReplayAuthorization(
            expires_at=time.time() - 1,  # already expired
            original_message="overwrite",
        )
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.DEFAULT, user_message="overwrite", replays=[auth]),
        )
        assert decision.action == DecisionAction.CONFIRM

    def test_op_match_works_when_msg_empty(self) -> None:
        engine = PolicyEngineV2(config=PolicyConfigV2())
        auth = ReplayAuthorization(
            expires_at=time.time() + 30,
            operation="write",
        )
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.DEFAULT, user_message="", replays=[auth]),
        )
        assert decision.action == DecisionAction.ALLOW

    def test_no_match_falls_through_to_confirm(self) -> None:
        engine = PolicyEngineV2(config=PolicyConfigV2())
        auth = ReplayAuthorization(
            expires_at=time.time() + 30,
            original_message="DIFFERENT message",
        )
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.DEFAULT, user_message="please overwrite", replays=[auth]),
        )
        assert decision.action == DecisionAction.CONFIRM


# ---------------------------------------------------------------------------
# Step 8: trusted_path
# ---------------------------------------------------------------------------


class TestTrustedPath:
    def test_op_only_rule_relaxes_matching_op(self) -> None:
        engine = PolicyEngineV2(config=PolicyConfigV2())
        rule = TrustedPathOverride(operation="write")
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.DEFAULT, trusts=[rule]),
        )
        assert decision.action == DecisionAction.ALLOW
        assert any(s.name == "trusted_path" for s in decision.chain)

    def test_op_mismatch_no_relax(self) -> None:
        engine = PolicyEngineV2(config=PolicyConfigV2())
        rule = TrustedPathOverride(operation="delete")
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.DEFAULT, trusts=[rule]),
        )
        assert decision.action == DecisionAction.CONFIRM

    def test_path_pattern_match_relaxes(self) -> None:
        engine = PolicyEngineV2(config=PolicyConfigV2())
        rule = TrustedPathOverride(path_pattern=r"qa_test")
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/qa_test_2026/x"),
            _ctx(
                mode=ConfirmationMode.DEFAULT,
                user_message="write to qa_test_2026/x",
                trusts=[rule],
            ),
        )
        assert decision.action == DecisionAction.ALLOW

    def test_malformed_regex_does_not_crash(self) -> None:
        engine = PolicyEngineV2(config=PolicyConfigV2())
        rule = TrustedPathOverride(path_pattern=r"[unclosed")
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.DEFAULT, user_message="x", trusts=[rule]),
        )
        # malformed → treated as no-match, returns CONFIRM
        assert decision.action == DecisionAction.CONFIRM

    def test_expired_trust_no_relax(self) -> None:
        engine = PolicyEngineV2(config=PolicyConfigV2())
        rule = TrustedPathOverride(operation="write", expires_at=time.time() - 1)
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.DEFAULT, trusts=[rule]),
        )
        assert decision.action == DecisionAction.CONFIRM


# ---------------------------------------------------------------------------
# Step 11: unattended strategies
# ---------------------------------------------------------------------------


class TestUnattendedStrategies:
    @pytest.fixture
    def confirm_class_event(self) -> tuple[ToolCallEvent, PolicyContext]:
        """write_file in DEFAULT mode → matrix CONFIRM."""
        return (
            _evt("write_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.DEFAULT, is_unattended=True),
        )

    def test_strategy_deny(self, confirm_class_event: tuple) -> None:
        config = PolicyConfigV2(unattended=UnattendedConfig(default_strategy="deny"))
        engine = PolicyEngineV2(config=config)
        evt, ctx = confirm_class_event
        decision = engine.evaluate_tool_call(evt, ctx)
        assert decision.action == DecisionAction.DENY
        assert decision.is_unattended_path is True

    def test_strategy_auto_approve_readonly(self) -> None:
        config = PolicyConfigV2(unattended=UnattendedConfig(default_strategy="auto_approve"))
        engine = PolicyEngineV2(config=config)
        # Need a class that goes to CONFIRM → readonly classes go ALLOW directly,
        # so we need to force a path through unattended. Use TRUST + read tool.
        # Actually readonly in any mode = ALLOW immediately by matrix; doesn't reach unattended.
        # So we test: write op in unattended + auto_approve → DENY (refused write)
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.DEFAULT, is_unattended=True),
        )
        assert decision.action == DecisionAction.DENY
        assert "auto_approve refused write" in decision.reason

    def test_strategy_defer_to_owner(self) -> None:
        config = PolicyConfigV2(unattended=UnattendedConfig(default_strategy="defer_to_owner"))
        engine = PolicyEngineV2(config=config)
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.DEFAULT, is_unattended=True),
        )
        assert decision.action == DecisionAction.DEFER
        # 关键：is_unattended_path 必须 True，C12 调用方靠它路由到 pending_approvals
        assert decision.is_unattended_path is True

    def test_strategy_defer_to_inbox(self) -> None:
        config = PolicyConfigV2(unattended=UnattendedConfig(default_strategy="defer_to_inbox"))
        engine = PolicyEngineV2(config=config)
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.DEFAULT, is_unattended=True),
        )
        assert decision.action == DecisionAction.DEFER

    def test_strategy_ask_owner(self) -> None:
        config = PolicyConfigV2(unattended=UnattendedConfig(default_strategy="ask_owner"))
        engine = PolicyEngineV2(config=config)
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(mode=ConfirmationMode.DEFAULT, is_unattended=True),
        )
        assert decision.action == DecisionAction.CONFIRM

    def test_ctx_strategy_overrides_config(self) -> None:
        """ctx.unattended_strategy 非空时覆盖 config 默认。"""
        config = PolicyConfigV2(unattended=UnattendedConfig(default_strategy="ask_owner"))
        engine = PolicyEngineV2(config=config)
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(
                mode=ConfirmationMode.DEFAULT,
                is_unattended=True,
                unattended_strategy="deny",  # per-call override
            ),
        )
        assert decision.action == DecisionAction.DENY


# ---------------------------------------------------------------------------
# build_engine_from_config factory
# ---------------------------------------------------------------------------


class TestBuildEngineFactory:
    def test_factory_wires_classifier_with_shell_customs(self) -> None:
        config = PolicyConfigV2(
            shell_risk=ShellRiskConfig(custom_critical=[r"factory_dangerous"]),
        )
        engine = build_engine_from_config(config)
        decision = engine.evaluate_tool_call(
            _evt("run_shell", command="factory_dangerous --x"),
            _ctx(),
        )
        assert decision.shell_risk_level == "critical"

    def test_factory_wires_engine_overrides(self) -> None:
        config = PolicyConfigV2(
            approval_classes=ApprovalClassesConfig(
                overrides={"factory_tool": ApprovalClass.DESTRUCTIVE}
            )
        )
        engine = build_engine_from_config(config)
        decision = engine.evaluate_tool_call(
            _evt("factory_tool"),
            _ctx(),
        )
        assert decision.approval_class == ApprovalClass.DESTRUCTIVE

    def test_factory_default_config_no_crash(self) -> None:
        engine = build_engine_from_config(PolicyConfigV2())
        decision = engine.evaluate_tool_call(
            _evt("read_file", path="/ws/x"),
            _ctx(),
        )
        assert decision.action == DecisionAction.ALLOW


# ---------------------------------------------------------------------------
# ReplayAuthorization / TrustedPathOverride dataclass fundamentals
# ---------------------------------------------------------------------------


class TestDataclassesFundamentals:
    def test_replay_is_active_now(self) -> None:
        a = ReplayAuthorization(expires_at=time.time() + 30)
        assert a.is_active()
        b = ReplayAuthorization(expires_at=time.time() - 1)
        assert not b.is_active()

    def test_replay_frozen(self) -> None:
        a = ReplayAuthorization(expires_at=time.time() + 30)
        with pytest.raises((AttributeError, Exception)):
            a.expires_at = 0  # type: ignore[misc]

    def test_trust_no_expires_always_active(self) -> None:
        rule = TrustedPathOverride(operation="write")
        assert rule.is_active()

    def test_trust_with_expires_respects_now(self) -> None:
        rule = TrustedPathOverride(operation="write", expires_at=time.time() - 1)
        assert not rule.is_active()


# ---------------------------------------------------------------------------
# Coercion: from_session accepts v1 dict shape
# ---------------------------------------------------------------------------


class TestPolicyContextCoercion:
    """C5 boundary fix: PolicyContext 接受 string 形式的 role/mode 并归一为 enum。

    动机：``cfg.confirmation.mode`` 在 ``use_enum_values=True`` 下返回 str，
    早期版本下游 ``ctx.confirmation_mode.value`` 会 AttributeError。__post_init__
    在 boundary 单点修复，避免 30 处调用方各自 coerce。
    """

    def test_string_mode_coerced_to_enum(self) -> None:
        ctx = PolicyContext(
            session_id="s",
            workspace=Path("/ws"),
            confirmation_mode="trust",  # type: ignore[arg-type]
        )
        assert ctx.confirmation_mode == ConfirmationMode.TRUST
        assert isinstance(ctx.confirmation_mode, ConfirmationMode)

    def test_string_role_coerced_to_enum(self) -> None:
        ctx = PolicyContext(
            session_id="s",
            workspace=Path("/ws"),
            session_role="plan",  # type: ignore[arg-type]
        )
        assert ctx.session_role == SessionRole.PLAN

    def test_invalid_string_falls_back_to_default(self) -> None:
        ctx = PolicyContext(
            session_id="s",
            workspace=Path("/ws"),
            confirmation_mode="not_a_mode",  # type: ignore[arg-type]
            session_role="ghost_mode",  # type: ignore[arg-type]
        )
        assert ctx.confirmation_mode == ConfirmationMode.DEFAULT
        assert ctx.session_role == SessionRole.AGENT

    def test_engine_accepts_string_mode_no_crash(self) -> None:
        """End-to-end: engine 用 string-mode ctx 不再 crash。"""
        engine = PolicyEngineV2(config=PolicyConfigV2())
        decision = engine.evaluate_tool_call(
            _evt("read_file", path="/ws/x"),
            PolicyContext(
                session_id="s",
                workspace=Path("/ws"),
                confirmation_mode="trust",  # type: ignore[arg-type]
            ),
        )
        assert decision.action == DecisionAction.ALLOW


class TestSessionCoercion:
    def test_replay_dict_form_coerced(self) -> None:
        from openakita.core.policy_v2.context import _coerce_replay_auths

        v1_dict = {
            "expires_at": time.time() + 30,
            "original_message": "msg",
            "confirmation_id": "id1",
            "operation": "write",
        }
        auths = _coerce_replay_auths(v1_dict)
        assert len(auths) == 1
        assert auths[0].original_message == "msg"
        assert auths[0].confirmation_id == "id1"

    def test_trust_v1_overrides_dict_with_rules_coerced(self) -> None:
        from openakita.core.policy_v2.context import _coerce_trusted_paths

        v1_overrides = {
            "rules": [
                {"operation": "WRITE", "path_pattern": "qa_test", "expires_at": None},
                {"operation": "delete", "expires_at": time.time() + 60},
            ]
        }
        rules = _coerce_trusted_paths(v1_overrides)
        assert len(rules) == 2
        assert rules[0].operation == "write"  # lowercased
        assert rules[0].path_pattern == "qa_test"
        assert rules[0].expires_at is None
        assert rules[1].operation == "delete"

    def test_malformed_replay_skipped(self) -> None:
        from openakita.core.policy_v2.context import _coerce_replay_auths

        items = [
            {"expires_at": "not_a_number"},  # bad expiry
            {"expires_at": time.time() + 30, "original_message": "ok"},
        ]
        auths = _coerce_replay_auths(items)
        # Malformed skipped silently; valid one survives
        assert len(auths) == 1
        assert auths[0].original_message == "ok"


# ---------------------------------------------------------------------------
# C5 audit fixes (2nd pass review)
# ---------------------------------------------------------------------------


class TestC5AuditFixes:
    """4 个 audit-discovered issues 的回归测试。"""

    def test_audit_a_params_none_does_not_crash(self) -> None:
        """``ToolCallEvent.params=None`` 应被容错，不应进 engine_crash 兜底。

        params 为 None 多见于上游构造时遗漏；fail-safe 虽然兜得住但是会污染
        审计计数 + 日志。step 3 应 defensive ``params or {}``。
        """
        import dataclasses

        config = PolicyConfigV2(safety_immune=SafetyImmuneConfig(paths=["/etc"]))
        engine = PolicyEngineV2(config=config)
        evt = dataclasses.replace(ToolCallEvent(tool="read_file"), params=None)  # type: ignore[arg-type]
        decision = engine.evaluate_tool_call(evt, _ctx())
        assert "engine_crash" not in decision.reason
        assert engine.stats()["engine_crash"] == 0

    def test_audit_b_unattended_chain_note_shows_effective_strategy(self) -> None:
        """ctx.unattended_strategy='' + config default → chain note 应显示生效值。"""
        config = PolicyConfigV2(unattended=UnattendedConfig(default_strategy="defer_to_owner"))
        engine = PolicyEngineV2(config=config)
        ctx = _ctx(
            mode=ConfirmationMode.DEFAULT,
            is_unattended=True,
            unattended_strategy="",  # explicitly empty
        )
        decision = engine.evaluate_tool_call(_evt("write_file", path="/ws/x"), ctx)
        unattended_step = next(s for s in decision.chain if s.name == "unattended")
        assert "defer_to_owner" in unattended_step.note
        assert unattended_step.note != "strategy="

    def test_audit_b_unattended_ctx_override_in_chain_note(self) -> None:
        """ctx 显式 strategy 覆盖 config 时 chain note 反映 ctx 的值。"""
        config = PolicyConfigV2(unattended=UnattendedConfig(default_strategy="ask_owner"))
        engine = PolicyEngineV2(config=config)
        ctx = _ctx(
            mode=ConfirmationMode.DEFAULT,
            is_unattended=True,
            unattended_strategy="deny",
        )
        decision = engine.evaluate_tool_call(_evt("write_file", path="/ws/x"), ctx)
        unattended_step = next(s for s in decision.chain if s.name == "unattended")
        assert "deny" in unattended_step.note

    def test_audit_c_replay_strips_whitespace_for_match(self) -> None:
        """v1 ``risk_authorized_replay`` 比对前 strip 双方；C5 必须对齐，
        否则 chat 带尾换行的消息 replay 会 silently 失效（C7 wire-up 后破坏 v1 功能）。"""
        engine = PolicyEngineV2(config=PolicyConfigV2())
        auth = ReplayAuthorization(
            expires_at=time.time() + 30,
            original_message="overwrite x",  # no whitespace
        )
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(
                mode=ConfirmationMode.DEFAULT,
                user_message="  overwrite x\n",  # padded variant
                replays=[auth],
            ),
        )
        assert decision.action == DecisionAction.ALLOW
        assert any(s.name == "replay" for s in decision.chain)

    def test_audit_c_replay_strip_both_sides(self) -> None:
        """对称：auth.original_message 也带空白时也能正常比对。"""
        engine = PolicyEngineV2(config=PolicyConfigV2())
        auth = ReplayAuthorization(
            expires_at=time.time() + 30,
            original_message="  overwrite x  ",  # padded
        )
        decision = engine.evaluate_tool_call(
            _evt("write_file", path="/ws/x"),
            _ctx(
                mode=ConfirmationMode.DEFAULT,
                user_message="overwrite x",  # clean
                replays=[auth],
            ),
        )
        assert decision.action == DecisionAction.ALLOW

    def test_audit_d_classifier_config_split_brain_warning(self, caplog) -> None:
        """传 classifier + config 且 shell_risk 不一致 → 应 WARNING（避免静默 footgun）。"""
        import logging

        from openakita.core.policy_v2 import ApprovalClassifier, ShellRiskConfig

        cfg_a = PolicyConfigV2(shell_risk=ShellRiskConfig(custom_critical=[r"a_cmd"]))
        cfg_b = PolicyConfigV2(shell_risk=ShellRiskConfig(custom_critical=[r"b_cmd"]))
        clf = ApprovalClassifier(shell_risk_config=cfg_a.shell_risk)

        with caplog.at_level(logging.WARNING, logger="openakita.core.policy_v2.engine"):
            PolicyEngineV2(classifier=clf, config=cfg_b)

        assert any("split-brain" in rec.message for rec in caplog.records), (
            "Expected split-brain WARNING when classifier and engine see different "
            f"shell_risk configs; got: {[r.message for r in caplog.records]}"
        )

    def test_audit_d_no_warning_when_shell_risk_matches(self, caplog) -> None:
        """同一 cfg 共享 → 不应有 WARNING（false positive 避免）。"""
        import logging

        from openakita.core.policy_v2 import ApprovalClassifier, ShellRiskConfig

        cfg = PolicyConfigV2(shell_risk=ShellRiskConfig(custom_critical=[r"x_cmd"]))
        clf = ApprovalClassifier(shell_risk_config=cfg.shell_risk)

        with caplog.at_level(logging.WARNING, logger="openakita.core.policy_v2.engine"):
            PolicyEngineV2(classifier=clf, config=cfg)

        assert not any("split-brain" in rec.message for rec in caplog.records)

    def test_audit_d_no_warning_when_only_classifier_passed(self, caplog) -> None:
        """只传 classifier、不传 config → 不应 WARNING（config 是 None，没有冲突）。"""
        import logging

        from openakita.core.policy_v2 import ApprovalClassifier

        clf = ApprovalClassifier()

        with caplog.at_level(logging.WARNING, logger="openakita.core.policy_v2.engine"):
            PolicyEngineV2(classifier=clf)

        assert not any("split-brain" in rec.message for rec in caplog.records)

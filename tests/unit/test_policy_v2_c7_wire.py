"""C7：build_policy_context + evaluate_message_intent_via_v2 + handler.TOOL_CLASSES wire-up tests."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openakita.core.policy_v2 import (
    ApprovalClass,
    ConfirmationMode,
    DecisionAction,
    SessionRole,
    build_policy_context,
    evaluate_message_intent_via_v2,
    mode_to_session_role,
    reset_current_context,
    set_current_context,
)
from openakita.core.policy_v2.context import (
    PolicyContext,
    ToolPolicy,
    TrustedPathOverride,
)
from openakita.core.policy_v2.global_engine import (
    rebuild_engine_v2,
    reset_engine_v2,
)

_MEMORY_DELETE_POLICY = {
    "memory_delete_by_query": ToolPolicy(
        preview_param="dry_run",
        preview_default=True,
        preview_step_name="tool_preview",
        preview_reason="tool call only previews candidates",
        commit_requires_riskgate=True,
        riskgate_operation="memory_delete",
        riskgate_scope_params=("query", "source", "memory_type"),
        riskgate_scope_required_any=("query", "source", "memory_type"),
        riskgate_scope_exact_params=("source", "memory_type"),
        riskgate_scope_text_params=("query",),
        commit_step_name="tool_commit_requires_riskgate",
        commit_reason="tool commit requires confirmed RiskGate tool authorization",
    )
}

# ---------------------------------------------------------------------------
# mode_to_session_role
# ---------------------------------------------------------------------------


class TestModeToSessionRole:
    def test_known_modes(self):
        assert mode_to_session_role("agent") == SessionRole.AGENT
        assert mode_to_session_role("plan") == SessionRole.PLAN
        assert mode_to_session_role("ask") == SessionRole.ASK
        assert mode_to_session_role("coordinator") == SessionRole.COORDINATOR

    def test_case_insensitive(self):
        assert mode_to_session_role("AGENT") == SessionRole.AGENT
        assert mode_to_session_role("Plan") == SessionRole.PLAN

    def test_unknown_falls_back_to_agent(self):
        # 未知 mode 不抛异常，回退 AGENT（保守 = 行为像默认 mode）
        assert mode_to_session_role("xyzzy") == SessionRole.AGENT
        assert mode_to_session_role("") == SessionRole.AGENT
        assert mode_to_session_role(None) == SessionRole.AGENT


# ---------------------------------------------------------------------------
# build_policy_context
# ---------------------------------------------------------------------------


class TestBuildPolicyContext:
    def test_minimal_no_session(self):
        """无 session 输入时，仍能构出有效 ctx（CLI 单轮场景）。"""
        ctx = build_policy_context(session=None, mode="agent")
        assert isinstance(ctx, PolicyContext)
        assert ctx.session_role == SessionRole.AGENT
        assert ctx.replay_authorizations == []
        assert ctx.trusted_path_overrides == []
        # Either Path.cwd() is included directly, or config roots cover it.
        assert len(ctx.workspace_roots) >= 1

    def test_mode_translates_to_session_role(self):
        for mode, expected in [
            ("agent", SessionRole.AGENT),
            ("plan", SessionRole.PLAN),
            ("ask", SessionRole.ASK),
        ]:
            ctx = build_policy_context(session=None, mode=mode)
            assert ctx.session_role == expected

    def test_explicit_workspace_used(self):
        ctx = build_policy_context(session=None, workspace="/tmp/myws")
        # explicit workspace should be in the union of roots (config ∪ explicit)
        assert Path("/tmp/myws") in ctx.workspace_roots

    def test_explicit_workspace_unions_with_config_roots(self, monkeypatch):
        """显式 workspace 不应替换 config.workspace.paths，必须做并集。

        这是清理阶段的硬规则：任何入口都不能因为传入了一个单一 cwd 而
        缩小用户在安全页配置的工作区授权范围。
        """
        from openakita.core.policy_v2 import global_engine

        class _Workspace:
            paths = ("/configured/root", "/other/configured")

        class _Confirmation:
            mode = "default"

        class _FakeCfg:
            workspace = _Workspace()
            confirmation = _Confirmation()

        monkeypatch.setattr(global_engine, "get_config_v2", lambda: _FakeCfg())

        ctx = build_policy_context(session=None, workspace="/extra/explicit")
        roots = set(ctx.workspace_roots)
        assert Path("/configured/root") in roots
        assert Path("/other/configured") in roots
        assert Path("/extra/explicit") in roots

    def test_user_message_propagated(self):
        ctx = build_policy_context(session=None, user_message="please refactor")
        assert ctx.user_message == "please refactor"

    def test_session_metadata_replay_auths_are_not_a_policy_source(self):
        """RiskGate grants must be passed explicitly by the backend."""
        future = time.time() + 30.0
        session = MagicMock()
        session.get_metadata = MagicMock(
            side_effect=lambda key: {
                "untrusted_client_authorization": {
                    "expires_at": future,
                    "original_message": "delete logs",
                    "confirmation_id": "conf-1",
                    "operation": "delete",
                }
            }.get(key)
        )
        ctx = build_policy_context(session=session)
        assert ctx.replay_authorizations == []

    def test_explicit_replay_auths_are_the_policy_source(self):
        """Turn-scoped RiskGate grants are passed explicitly."""
        turn_future = time.time() + 3600.0
        session = MagicMock()
        session.get_metadata = MagicMock(return_value=None)

        ctx = build_policy_context(
            session=session,
            replay_authorizations=[
                {
                    "expires_at": turn_future,
                    "original_message": "delete turn marker",
                    "confirmation_id": "turn-conf",
                    "operation": "delete",
                }
            ],
        )

        assert [ra.confirmation_id for ra in ctx.replay_authorizations] == ["turn-conf"]
        assert ctx.replay_authorizations[0].expires_at == turn_future

    def test_explicit_turn_replay_does_not_relax_riskgate_commit_policy(self):
        """RiskGate-protected commits require scoped executor authorization."""
        from openakita.core.policy_v2 import evaluate_via_v2

        message = "请删除长期记忆中所有包含 OPENAKITA_RISKGATE_689_REPRO_TEST 的记忆。"
        ctx = build_policy_context(
            session=None,
            mode="agent",
            user_message=message,
            tool_policies=_MEMORY_DELETE_POLICY,
            replay_authorizations=[
                {
                    "turn_scoped": True,
                    "original_message": message,
                    "confirmation_id": "turn-conf",
                    "operation": "delete",
                }
            ],
        )
        token = set_current_context(ctx)
        try:
            decision = evaluate_via_v2(
                "memory_delete_by_query",
                {"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "dry_run": False},
            )
        finally:
            reset_current_context(token)

        assert decision.action == DecisionAction.CONFIRM
        assert any(step.name == "tool_commit_requires_riskgate" for step in decision.chain)
        assert not any(step.name == "replay" for step in decision.chain)
        assert decision.metadata["riskgate_required"] is True

    def test_memory_delete_dry_run_preview_does_not_need_tool_confirmation(self):
        """The non-mutating preview must not show a normal security confirm."""
        from openakita.core.policy_v2 import evaluate_via_v2

        ctx = build_policy_context(
            session=None,
            mode="agent",
            user_message="请删除长期记忆中所有包含 OPENAKITA_RISKGATE_689_REPRO_TEST 的记忆。",
            tool_policies=_MEMORY_DELETE_POLICY,
        )
        token = set_current_context(ctx)
        try:
            decision = evaluate_via_v2(
                "memory_delete_by_query",
                {"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "dry_run": True},
            )
        finally:
            reset_current_context(token)

        assert decision.action == DecisionAction.ALLOW
        assert any(step.name == "tool_preview" for step in decision.chain)

    def test_memory_delete_real_delete_without_riskgate_requests_tool_riskgate(self):
        """A real commit asks for backend RiskGate from the structured tool call."""
        from openakita.core.policy_v2 import evaluate_via_v2

        ctx = build_policy_context(
            session=None,
            mode="agent",
            user_message="retry",
            tool_policies=_MEMORY_DELETE_POLICY,
        )
        token = set_current_context(ctx)
        try:
            decision = evaluate_via_v2(
                "memory_delete_by_query",
                {
                    "query": "OPENAKITA_RISKGATE_689_REPRO_TEST",
                    "dry_run": False,
                    "confirm_token": "token",
                },
            )
        finally:
            reset_current_context(token)

        assert decision.action == DecisionAction.CONFIRM
        assert any(step.name == "tool_commit_requires_riskgate" for step in decision.chain)
        assert decision.metadata["riskgate_required"] is True
        assert decision.metadata["riskgate_operation"] == "memory_delete"
        assert decision.metadata["riskgate_scope"]["query"] == "OPENAKITA_RISKGATE_689_REPRO_TEST"

    def test_riskgate_metadata_without_replay_does_not_authorize_commit(self):
        """The trace marker alone must not bypass a tool's replay requirement."""
        from openakita.core.policy_v2 import evaluate_via_v2

        ctx = build_policy_context(
            session=None,
            mode="agent",
            user_message="请删除长期记忆中所有包含 OPENAKITA_RISKGATE_689_REPRO_TEST 的记忆。",
            tool_policies=_MEMORY_DELETE_POLICY,
            extra_metadata={"risk_gate_turn_authorized": True},
        )
        token = set_current_context(ctx)
        try:
            decision = evaluate_via_v2(
                "memory_delete_by_query",
                {
                    "query": "OPENAKITA_RISKGATE_689_REPRO_TEST",
                    "dry_run": False,
                    "confirm_token": "token",
                },
            )
        finally:
            reset_current_context(token)

        assert decision.action == DecisionAction.CONFIRM
        assert any(step.name == "tool_commit_requires_riskgate" for step in decision.chain)

    def test_explicit_turn_replay_does_not_relax_unlisted_tool(self):
        """Turn-scoped replay grants should not relax broader destructive tools."""
        from openakita.core.policy_v2 import evaluate_via_v2

        message = "请删除长期记忆中所有包含 OPENAKITA_RISKGATE_689_REPRO_TEST 的记忆。"
        ctx = build_policy_context(
            session=None,
            mode="agent",
            user_message=message,
            tool_policies=_MEMORY_DELETE_POLICY,
            replay_authorizations=[
                {
                    "turn_scoped": True,
                    "original_message": message,
                    "confirmation_id": "turn-conf",
                    "operation": "delete",
                    "tool_names": ["memory_delete_by_query"],
                }
            ],
        )
        token = set_current_context(ctx)
        try:
            decision = evaluate_via_v2(
                "delete_file",
                {"path": "memory.json"},
            )
        finally:
            reset_current_context(token)

        assert decision.action == DecisionAction.CONFIRM
        assert not any(step.name == "replay" for step in decision.chain)

    def test_session_metadata_trusted_paths_extracted(self):
        """v1 trusted_path_overrides {rules:[...]} → TrustedPathOverride list。"""
        session = MagicMock()
        session.get_metadata = MagicMock(
            side_effect=lambda key: {
                "trusted_path_overrides": {
                    "rules": [
                        {
                            "operation": "write",
                            "path_pattern": "/tmp/**",
                            "expires_at": time.time() + 600,
                            "granted_at": time.time(),
                        }
                    ]
                }
            }.get(key)
        )
        ctx = build_policy_context(session=session)
        assert len(ctx.trusted_path_overrides) == 1
        tp = ctx.trusted_path_overrides[0]
        assert isinstance(tp, TrustedPathOverride)
        assert tp.operation == "write"
        assert tp.path_pattern == "/tmp/**"

    def test_session_get_metadata_failure_falls_back_to_empty(self):
        """session.get_metadata 抛异常时，ctx 仍能构出（fail-soft）。"""
        session = MagicMock()
        session.get_metadata = MagicMock(side_effect=RuntimeError("boom"))
        ctx = build_policy_context(session=session)
        assert ctx.replay_authorizations == []
        assert ctx.trusted_path_overrides == []

    def test_malformed_explicit_replay_auth_skipped(self):
        """malformed explicit replay entries are skipped instead of raising."""
        session = MagicMock()
        ctx = build_policy_context(
            session=session,
            replay_authorizations=[
                {"expires_at": "not-a-float"},  # malformed
                {  # valid
                    "expires_at": time.time() + 30,
                    "original_message": "ok",
                    "confirmation_id": "x",
                    "operation": "write",
                },
            ],
        )
        # 第一条 malformed 跳过，第二条保留
        assert len(ctx.replay_authorizations) == 1
        assert ctx.replay_authorizations[0].original_message == "ok"

    def test_extra_metadata_merged(self):
        ctx = build_policy_context(
            session=None,
            extra_metadata={"trace_id": "abc-123", "request_id": "r-1"},
        )
        assert ctx.metadata["trace_id"] == "abc-123"
        assert ctx.metadata["request_id"] == "r-1"


# ---------------------------------------------------------------------------
# evaluate_message_intent_via_v2
# ---------------------------------------------------------------------------


class TestEvaluateMessageIntentViaV2:
    def test_no_signal_allows(self):
        """无 risk_intent 信号 → ALLOW（无 write 意图）。"""
        ctx = build_policy_context(session=None, mode="agent")
        decision = evaluate_message_intent_via_v2(
            "hello",
            risk_intent=None,
            extra_ctx=ctx,
        )
        assert decision.action == DecisionAction.ALLOW

    def test_trust_mode_bypass(self, monkeypatch):
        """trust 模式下 → ALLOW，即使有 write 信号。"""
        from openakita.core.policy_v2.global_engine import get_config_v2

        cfg = get_config_v2()
        cfg.confirmation.mode = ConfirmationMode.TRUST
        try:
            ctx = build_policy_context(session=None, mode="agent")
            assert ctx.confirmation_mode == ConfirmationMode.TRUST
            risk_intent = {"risk_level": "MEDIUM", "operation_kind": "write"}
            decision = evaluate_message_intent_via_v2(
                "delete file",
                risk_intent=risk_intent,
                extra_ctx=ctx,
            )
            assert decision.action == DecisionAction.ALLOW
            assert "trust" in decision.reason.lower()
        finally:
            cfg.confirmation.mode = ConfirmationMode.DEFAULT
            reset_engine_v2()

    def test_default_mode_with_write_signal_confirms(self, monkeypatch):
        """default 模式 + write 信号 → CONFIRM。"""
        from openakita.core.policy_v2.global_engine import get_config_v2

        cfg = get_config_v2()
        cfg.confirmation.mode = ConfirmationMode.DEFAULT
        try:
            ctx = build_policy_context(session=None, mode="agent")
            risk_intent = {"risk_level": "MEDIUM", "operation_kind": "write"}
            decision = evaluate_message_intent_via_v2(
                "remove all logs",
                risk_intent=risk_intent,
                extra_ctx=ctx,
            )
            assert decision.action == DecisionAction.CONFIRM
        finally:
            reset_engine_v2()

    def test_plan_mode_blocks_write(self):
        """plan 模式禁止 write 意图 → DENY。"""
        ctx = build_policy_context(session=None, mode="plan")
        risk_intent = {"risk_level": "MEDIUM", "operation_kind": "write"}
        decision = evaluate_message_intent_via_v2(
            "rewrite file",
            risk_intent=risk_intent,
            extra_ctx=ctx,
        )
        assert decision.action == DecisionAction.DENY

    def test_engine_failure_returns_confirm(self, monkeypatch):
        """engine 抛异常时 fail-soft → CONFIRM（不 DENY 阻断对话）。"""
        import openakita.core.policy_v2.adapter as adapter_mod

        def _broken_engine():
            raise RuntimeError("engine offline")

        monkeypatch.setattr(adapter_mod, "_get_engine", _broken_engine)
        ctx = build_policy_context(session=None, mode="agent")
        decision = evaluate_message_intent_via_v2(
            "anything",
            risk_intent=None,
            extra_ctx=ctx,
        )
        assert decision.action == DecisionAction.CONFIRM
        assert "暂时" in decision.reason or "请确认" in decision.reason


# ---------------------------------------------------------------------------
# handler.TOOL_CLASSES → SystemHandlerRegistry → PolicyEngineV2.classifier
# ---------------------------------------------------------------------------


class TestExplicitLookupWiring:
    """C7.5：rebuild_engine_v2(explicit_lookup=registry.get_tool_class)
    验证 handler 显式声明真正生效（决策来源 = EXPLICIT_HANDLER_ATTR）。
    """

    def test_filesystem_write_file_explicit(self, monkeypatch):
        """write_file 来自 FilesystemHandler.TOOL_CLASSES → MUTATING_SCOPED 显式。"""
        from openakita.tools.handlers import SystemHandlerRegistry
        from openakita.tools.handlers.filesystem import FilesystemHandler

        registry = SystemHandlerRegistry()
        handler_instance = FilesystemHandler.__new__(FilesystemHandler)
        registry.register(
            "filesystem",
            handler_instance.handle if hasattr(handler_instance, "handle") else (lambda *a: ""),
            tool_names=FilesystemHandler.TOOLS,
            tool_classes=FilesystemHandler.TOOL_CLASSES,
        )
        result = registry.get_tool_class("write_file")
        assert result is not None
        approval_class, source = result
        assert approval_class == ApprovalClass.MUTATING_SCOPED
        # source 是 EXPLICIT_REGISTER_PARAM（因为我们 explicit pass tool_classes=）
        from openakita.core.policy_v2 import DecisionSource

        assert source in (
            DecisionSource.EXPLICIT_REGISTER_PARAM,
            DecisionSource.EXPLICIT_HANDLER_ATTR,
        )

    def test_classifier_consults_explicit_lookup(self):
        """rebuild_engine_v2 注入 explicit_lookup 后，classifier 取显式 class 而非启发式。"""
        from openakita.tools.handlers import SystemHandlerRegistry
        from openakita.tools.handlers.filesystem import FilesystemHandler

        registry = SystemHandlerRegistry()
        handler_fn = lambda *args, **kw: ""  # noqa: E731 — minimal stub
        registry.register(
            "filesystem",
            handler_fn,
            tool_names=FilesystemHandler.TOOLS,
            tool_classes=FilesystemHandler.TOOL_CLASSES,
        )

        engine = rebuild_engine_v2(explicit_lookup=registry.get_tool_class)
        # classify_with_source 返回 tuple[ApprovalClass, DecisionSource]
        approval_class, source = engine._classifier.classify_with_source("delete_file")
        assert approval_class == ApprovalClass.DESTRUCTIVE

        from openakita.core.policy_v2 import DecisionSource

        # 显式来源（不应是 HEURISTIC_PREFIX 或 FALLBACK_UNKNOWN）
        assert source in (
            DecisionSource.EXPLICIT_REGISTER_PARAM,
            DecisionSource.EXPLICIT_HANDLER_ATTR,
        )

        # 清理
        reset_engine_v2(clear_explicit_lookup=True)

    def test_explicit_lookup_survives_reset(self):
        """C7 二轮 audit 修复：reset_engine_v2() 后懒加载仍能恢复 explicit_lookup。

        用户改设置 → ``api/routes/config.py`` 调 ``reset_policy_engine`` →
        ``reset_engine_v2`` → 下次工具调用 lazily 重建。如果 explicit_lookup
        没持久化，138 个 handler 显式声明的 ApprovalClass 会全部退化到启发式。
        """
        from openakita.core.policy_v2 import DecisionSource
        from openakita.core.policy_v2.global_engine import get_engine_v2

        marker_lookup = lambda name: (  # noqa: E731
            (ApprovalClass.MUTATING_SCOPED, DecisionSource.EXPLICIT_HANDLER_ATTR)
            if name == "regression_marker_tool"
            else None
        )

        reset_engine_v2(clear_explicit_lookup=True)
        rebuild_engine_v2(explicit_lookup=marker_lookup)
        engine_a = get_engine_v2()
        ac_a, src_a = engine_a._classifier.classify_with_source("regression_marker_tool")
        assert ac_a == ApprovalClass.MUTATING_SCOPED
        assert src_a == DecisionSource.EXPLICIT_HANDLER_ATTR

        reset_engine_v2()
        engine_b = get_engine_v2()
        ac_b, src_b = engine_b._classifier.classify_with_source("regression_marker_tool")
        assert ac_b == ApprovalClass.MUTATING_SCOPED, (
            "explicit_lookup lost after reset → tool fell back to heuristic"
        )
        assert src_b == DecisionSource.EXPLICIT_HANDLER_ATTR

        reset_engine_v2(clear_explicit_lookup=True)
        engine_c = get_engine_v2()
        ac_c, src_c = engine_c._classifier.classify_with_source("regression_marker_tool")
        assert src_c != DecisionSource.EXPLICIT_HANDLER_ATTR

        reset_engine_v2(clear_explicit_lookup=True)


# ---------------------------------------------------------------------------
# ContextVar 套件
# ---------------------------------------------------------------------------


class TestContextVarLifecycle:
    def test_set_and_reset_context(self):
        """set/reset 配对正确归位。"""
        from openakita.core.policy_v2 import get_current_context

        # 入站前 = None
        assert get_current_context() is None

        ctx = build_policy_context(session=None, session_id="t1")
        token = set_current_context(ctx)
        try:
            assert get_current_context() is ctx
        finally:
            reset_current_context(token)

        # 出站后 = None
        assert get_current_context() is None

    @pytest.mark.asyncio
    async def test_contextvar_propagates_to_child_task(self):
        """asyncio.create_task 应继承 ContextVar 快照。"""
        import asyncio

        from openakita.core.policy_v2 import get_current_context

        ctx = build_policy_context(session=None, session_id="parent")
        token = set_current_context(ctx)
        try:
            captured = []

            async def child():
                captured.append(get_current_context())

            task = asyncio.create_task(child())
            await task
            assert len(captured) == 1
            assert captured[0] is ctx
        finally:
            reset_current_context(token)

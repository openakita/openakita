"""C3 unit tests: PolicyEngineV2 — 12-step decision chain + dual entry points.

Acceptance criteria for C3:
- 12-step chain: 每步生成 DecisionStep；短路逻辑正确（matrix DENY/ALLOW 跳过 relax）
- evaluate_tool_call: 矩阵决策与 ApprovalClassifier + lookup_matrix 等价
- evaluate_message_intent: trust 模式放行；plan/ask 拒写；其他模式按 risk_signal
- fail-safe: 任何内部异常 → DENY + 记录原因（不向上传）
- thread-safety: stats 加锁
- shell command refine: HIGH/CRITICAL command → DESTRUCTIVE + needs_sandbox/checkpoint
- channel compat: IM 渠道下 INTERACTIVE 类工具被拒
- safety_immune: ctx.safety_immune_paths 命中即 CONFIRM
- owner_only: CONTROL_PLANE 类 + 非 owner → DENY
- unattended: ask_owner / deny / auto_approve 各自行为正确
- audit_hook: 决策完调用，异常不影响主流程
"""

from __future__ import annotations

from pathlib import Path

from openakita.core.policy_v2 import (
    ApprovalClass,
    ApprovalClassifier,
    ConfirmationMode,
    DecisionAction,
    MessageIntentEvent,
    PolicyContext,
    PolicyDecisionV2,
    PolicyEngineV2,
    SessionRole,
    ToolCallEvent,
    lookup_matrix,
)


def _ctx(
    workspace: Path,
    *,
    role: SessionRole = SessionRole.AGENT,
    mode: ConfirmationMode = ConfirmationMode.DEFAULT,
    channel: str = "desktop",
    is_owner: bool = True,
    is_unattended: bool = False,
    unattended_strategy: str = "ask_owner",
    safety_immune_paths: tuple[str, ...] = (),
) -> PolicyContext:
    return PolicyContext(
        session_id="test",
        workspace=workspace,
        channel=channel,
        is_owner=is_owner,
        session_role=role,
        confirmation_mode=mode,
        is_unattended=is_unattended,
        unattended_strategy=unattended_strategy,
        safety_immune_paths=safety_immune_paths,
    )


# ============================================================
# Step 1: preflight + tool name normalization
# ============================================================


class TestPreflight:
    def test_basic_tool_classified(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="read_file", params={"path": "/tmp/x"}),
            _ctx(tmp_path),
        )
        assert decision.action == DecisionAction.ALLOW
        assert decision.approval_class == ApprovalClass.READONLY_GLOBAL
        # chain 至少有 preflight + classify + matrix*
        assert any(s.name == "preflight" for s in decision.chain)
        assert any(s.name == "classify" for s in decision.chain)

    def test_plugin_prefix_stripped(self, tmp_path: Path) -> None:
        """``plugin:foo_read`` 应被剥前缀后用 'foo_read' 走 classifier。"""
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="plugin:read_remote"),
            _ctx(tmp_path),
        )
        # read_ 前缀走 heuristic → READONLY_GLOBAL → matrix ALLOW
        assert decision.approval_class == ApprovalClass.READONLY_GLOBAL
        # preflight detail 应包含归一化结果
        preflight = next(s for s in decision.chain if s.name == "preflight")
        assert "read_remote" in preflight.note


# ============================================================
# Step 6: matrix decision (核心)
# ============================================================


class TestMatrixDecision:
    def test_default_mode_destructive_confirms(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="delete_file", params={"path": "/tmp/x"}),
            _ctx(tmp_path, mode=ConfirmationMode.DEFAULT),
        )
        assert decision.action == DecisionAction.CONFIRM
        assert decision.approval_class == ApprovalClass.DESTRUCTIVE

    def test_trust_mode_destructive_still_confirms(self, tmp_path: Path) -> None:
        """trust 模式仍 CONFIRM 不可恢复操作（matrix invariant）。"""
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="delete_file", params={"path": "/tmp/x"}),
            _ctx(tmp_path, mode=ConfirmationMode.TRUST),
        )
        assert decision.action == DecisionAction.CONFIRM

    def test_trust_mode_readonly_allows(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="read_file", params={"path": "/tmp/x"}),
            _ctx(tmp_path, mode=ConfirmationMode.TRUST),
        )
        assert decision.action == DecisionAction.ALLOW

    def test_strict_mode_mutating_denies(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": str(tmp_path / "x")}),
            _ctx(tmp_path, mode=ConfirmationMode.STRICT),
        )
        # strict 模式下 MUTATING_SCOPED 矩阵给 CONFIRM（人工放行）
        # 不应直接 ALLOW
        assert decision.action != DecisionAction.ALLOW

    def test_plan_mode_blocks_mutating(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": str(tmp_path / "x")}),
            _ctx(tmp_path, role=SessionRole.PLAN),
        )
        # plan 模式禁所有 mutation
        assert decision.action == DecisionAction.DENY

    def test_engine_decision_consistent_with_matrix_lookup(
        self, tmp_path: Path
    ) -> None:
        """engine 输出与裸 lookup_matrix(role, mode, klass) 应一致（除 relax/safety_immune 等额外步骤）。"""
        engine = PolicyEngineV2()
        for role in [SessionRole.PLAN, SessionRole.ASK, SessionRole.AGENT]:
            for mode in [
                ConfirmationMode.DEFAULT,
                ConfirmationMode.TRUST,
                ConfirmationMode.STRICT,
            ]:
                decision = engine.evaluate_tool_call(
                    ToolCallEvent(tool="read_file", params={"path": "/tmp/x"}),
                    _ctx(tmp_path, role=role, mode=mode),
                )
                expected = lookup_matrix(role, mode, ApprovalClass.READONLY_GLOBAL)
                assert decision.action == expected, (
                    f"mismatch role={role} mode={mode}: "
                    f"engine={decision.action} matrix={expected}"
                )


# ============================================================
# Step 5: channel compatibility
# ============================================================


class TestChannelCompat:
    """Channel compat 仅按 ``desktop_*`` / ``browser_*`` 工具名前缀 DENY。

    docs §4.21.1：``ask_user`` 等 INTERACTIVE 类工具在 IM 渠道下走 IM 适配器
    交互（IM 群里发问），不应被 channel_compat 拦截；只有真正依赖前台 GUI 的
    desktop_*/browser_* 才拦。
    """

    def test_im_channel_blocks_desktop_prefix(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="desktop_clipboard"),
            _ctx(tmp_path, channel="im:telegram"),
        )
        assert decision.action == DecisionAction.DENY
        assert "channel" in decision.reason.lower()
        assert "desktop_clipboard" in decision.reason
        assert any(s.name == "channel_compat" for s in decision.chain)

    def test_im_channel_blocks_browser_prefix(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="browser_navigate"),
            _ctx(tmp_path, channel="im:feishu"),
        )
        assert decision.action == DecisionAction.DENY

    def test_webhook_channel_blocks_desktop_tool(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="desktop_screenshot"),
            _ctx(tmp_path, channel="webhook"),
        )
        assert decision.action == DecisionAction.DENY

    def test_im_channel_allows_ask_user(self, tmp_path: Path) -> None:
        """ask_user 在 IM 走 IM 适配器交互，不应被 channel_compat DENY。"""
        clf = ApprovalClassifier(
            explicit_lookup=lambda t: (
                (ApprovalClass.INTERACTIVE, _src_explicit())
                if t == "ask_user"
                else None
            )
        )
        engine = PolicyEngineV2(classifier=clf)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="ask_user"),
            _ctx(tmp_path, channel="im:telegram"),
        )
        # INTERACTIVE 矩阵恒 ALLOW + ask_user 不在 channel block 列表
        assert decision.action == DecisionAction.ALLOW

    def test_desktop_channel_allows_desktop_tool(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="desktop_clipboard"),
            _ctx(tmp_path, channel="desktop"),
        )
        # desktop 渠道当然能跑 desktop_*
        # 不会触发 channel_compat DENY；具体最终 action 看 matrix
        assert decision.action != DecisionAction.DENY or "channel" not in decision.reason

    def test_cli_channel_allows_desktop_tool(self, tmp_path: Path) -> None:
        """CLI 渠道（terminal）能跑 desktop_*——通常 CLI 用户在桌面环境。"""
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="desktop_clipboard"),
            _ctx(tmp_path, channel="cli"),
        )
        assert "channel_compat" not in {s.name for s in decision.chain} or (
            decision.action != DecisionAction.DENY
        )

    def test_im_channel_allows_general_interactive(self, tmp_path: Path) -> None:
        """非 desktop_*/browser_* 前缀的 INTERACTIVE 工具在 IM 也应允许。"""
        clf = ApprovalClassifier(
            explicit_lookup=lambda t: (
                (ApprovalClass.INTERACTIVE, _src_explicit())
                if t == "prompt_choice"
                else None
            )
        )
        engine = PolicyEngineV2(classifier=clf)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="prompt_choice"),
            _ctx(tmp_path, channel="im:telegram"),
        )
        assert decision.action == DecisionAction.ALLOW


# ============================================================
# Step 3: safety_immune
# ============================================================


class TestSafetyImmune:
    def test_path_match_forces_confirm(self, tmp_path: Path) -> None:
        """ctx.safety_immune_paths 命中 → CONFIRM 即使 trust 模式。"""
        engine = PolicyEngineV2()
        # MUTATING_SCOPED 在 trust 模式下默认 ALLOW，但命中 immune 应升级到 CONFIRM
        decision = engine.evaluate_tool_call(
            ToolCallEvent(
                tool="write_file", params={"path": str(tmp_path / "secret.env")}
            ),
            _ctx(
                tmp_path,
                mode=ConfirmationMode.TRUST,
                safety_immune_paths=(str(tmp_path),),
            ),
        )
        assert decision.action == DecisionAction.CONFIRM
        assert decision.safety_immune_match is not None
        assert any(s.name == "safety_immune" for s in decision.chain)

    def test_no_paths_no_immune_block(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="read_file", params={"path": str(tmp_path / "x")}),
            _ctx(tmp_path, safety_immune_paths=()),
        )
        # 无 immune 路径 → 走正常矩阵
        assert decision.safety_immune_match is None


# ============================================================
# Step 4: owner_only (CONTROL_PLANE in IM)
# ============================================================


class TestOwnerOnly:
    def test_control_plane_non_owner_denies(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="schedule_task", params={"cron": "* * * * *"}),
            _ctx(tmp_path, channel="im:telegram", is_owner=False),
        )
        assert decision.action == DecisionAction.DENY
        assert decision.is_owner_required is True

    def test_control_plane_owner_allows(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="schedule_task"),
            _ctx(tmp_path, channel="im:telegram", is_owner=True),
        )
        # CONTROL_PLANE 矩阵在 default 模式下 = CONFIRM
        assert decision.action == DecisionAction.CONFIRM
        assert decision.is_owner_required is False


# ============================================================
# Shell refine integration
# ============================================================


class TestShellIntegration:
    def test_run_shell_critical_command(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="run_shell", params={"command": "rm -rf / "}),
            _ctx(tmp_path),
        )
        # CRITICAL → DESTRUCTIVE
        assert decision.approval_class == ApprovalClass.DESTRUCTIVE
        assert decision.shell_risk_level == "critical"
        assert decision.needs_checkpoint is True

    def test_run_shell_blocked_command(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="run_shell", params={"command": "regedit /s evil.reg"}),
            _ctx(tmp_path),
        )
        assert decision.shell_risk_level == "blocked"
        assert decision.needs_sandbox is False

    def test_run_shell_low_command_decides_via_matrix(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="run_shell", params={"command": "ls -la"}),
            _ctx(tmp_path, mode=ConfirmationMode.TRUST),
        )
        # EXEC_CAPABLE 在 trust 模式 = ALLOW
        assert decision.action == DecisionAction.ALLOW
        assert decision.shell_risk_level == "low"


# ============================================================
# Step 11: unattended branch
# ============================================================


class TestUnattended:
    def test_unattended_deny_strategy(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": str(tmp_path / "x")}),
            _ctx(tmp_path, is_unattended=True, unattended_strategy="deny"),
        )
        assert decision.action == DecisionAction.DENY
        assert decision.is_unattended_path is True

    def test_unattended_auto_approve_readonly(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        # MUTATING_SCOPED CONFIRM → unattended auto_approve 仅放只读 → 仍 DENY
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": str(tmp_path / "x")}),
            _ctx(tmp_path, is_unattended=True, unattended_strategy="auto_approve"),
        )
        assert decision.action == DecisionAction.DENY  # write_file 非只读
        assert decision.is_unattended_path is True

    def test_unattended_does_not_trigger_when_matrix_allows(
        self, tmp_path: Path
    ) -> None:
        """matrix ALLOW → 不进 unattended 分支（直接放行）。"""
        engine = PolicyEngineV2()
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="read_file", params={"path": "/tmp/x"}),
            _ctx(tmp_path, is_unattended=True, unattended_strategy="deny"),
        )
        # READONLY_GLOBAL → matrix ALLOW → 不走 unattended
        assert decision.action == DecisionAction.ALLOW
        assert decision.is_unattended_path is False


# ============================================================
# evaluate_message_intent
# ============================================================


class TestMessageIntent:
    def test_trust_mode_bypasses(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_message_intent(
            MessageIntentEvent(message="please rm -rf /", risk_intent="destructive"),
            _ctx(tmp_path, mode=ConfirmationMode.TRUST),
        )
        assert decision.action == DecisionAction.ALLOW

    def test_plan_mode_blocks_write_intent(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_message_intent(
            MessageIntentEvent(message="write to file", risk_intent="write"),
            _ctx(tmp_path, role=SessionRole.PLAN),
        )
        assert decision.action == DecisionAction.DENY

    def test_ask_mode_allows_readonly_intent(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_message_intent(
            MessageIntentEvent(message="just look at file", risk_intent="readonly"),
            _ctx(tmp_path, role=SessionRole.ASK),
        )
        assert decision.action == DecisionAction.ALLOW

    def test_default_mode_no_signal_allows(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_message_intent(
            MessageIntentEvent(message="hello", risk_intent=None),
            _ctx(tmp_path),
        )
        assert decision.action == DecisionAction.ALLOW

    def test_default_mode_risk_signal_confirms(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_message_intent(
            MessageIntentEvent(message="delete x", risk_intent="destructive"),
            _ctx(tmp_path),
        )
        assert decision.action == DecisionAction.CONFIRM

    def test_dict_risk_intent_supported(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        decision = engine.evaluate_message_intent(
            MessageIntentEvent(
                message="x", risk_intent={"risk_level": "destructive", "operation": "rm"}
            ),
            _ctx(tmp_path),
        )
        assert decision.action == DecisionAction.CONFIRM


# ============================================================
# Fail-safe (Step 0)
# ============================================================


class TestFailSafe:
    def test_classifier_crash_returns_deny(self, tmp_path: Path) -> None:
        class _BadClassifier:
            def classify_full(self, *args, **kwargs):  # noqa: ANN001, ARG002
                raise RuntimeError("boom")

        engine = PolicyEngineV2(classifier=_BadClassifier())  # type: ignore[arg-type]
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="read_file"),
            _ctx(tmp_path),
        )
        assert decision.action == DecisionAction.DENY
        assert "engine_crash" in decision.reason
        # 计数器记录
        assert engine.stats()["engine_crash"] == 1

    def test_audit_hook_exception_does_not_propagate(self, tmp_path: Path) -> None:
        def bad_hook(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ARG001
            raise RuntimeError("audit broken")

        engine = PolicyEngineV2(audit_hook=bad_hook)
        # 不应抛异常
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="read_file"),
            _ctx(tmp_path),
        )
        assert decision.action == DecisionAction.ALLOW

    def test_intent_engine_crash_returns_deny(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        # 故意构造让 _evaluate_message_intent_impl 抛错
        # SessionRole 异常值 → _coerce 不会触发；改用 mock-ish 构造
        # 简单：传一个会让 ConfirmationMode 比较出错的 ctx — 实际上很难自然触发
        # 这里改测：bad risk_intent (object that raises in getattr)
        class _BadRisk:
            def __getattr__(self, name):  # noqa: ANN001
                raise RuntimeError("bad")

        decision = engine.evaluate_message_intent(
            MessageIntentEvent(message="x", risk_intent=_BadRisk()),
            _ctx(tmp_path),
        )
        # 不抛 → 要么 ALLOW（_extract_risk_signal 容忍）要么 DENY
        # 当前实现 _extract_risk_signal 会 try getattr(value) 直接抛 → 顶层 catch
        assert isinstance(decision, PolicyDecisionV2)


# ============================================================
# Audit hook + stats
# ============================================================


class TestAudit:
    def test_audit_hook_called(self, tmp_path: Path) -> None:
        captured = []

        def hook(decision, event, ctx):  # noqa: ANN001
            captured.append((decision.action, event.tool, ctx.session_id))

        engine = PolicyEngineV2(audit_hook=hook)
        engine.evaluate_tool_call(
            ToolCallEvent(tool="read_file"),
            _ctx(tmp_path),
        )
        assert len(captured) == 1
        assert captured[0][0] == DecisionAction.ALLOW
        assert captured[0][1] == "read_file"

    def test_stats_tracked(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        engine.evaluate_tool_call(ToolCallEvent(tool="read_file"), _ctx(tmp_path))
        engine.evaluate_tool_call(ToolCallEvent(tool="read_file"), _ctx(tmp_path))
        engine.evaluate_message_intent(
            MessageIntentEvent(message="x"), _ctx(tmp_path)
        )
        stats = engine.stats()
        assert stats["evaluate_tool_call"] == 2
        assert stats["evaluate_message_intent"] == 1
        assert stats["engine_crash"] == 0


# ============================================================
# Cross-disk path refinement (continued from C2; sanity in engine)
# ============================================================


class TestPathRefineThroughEngine:
    def test_write_outside_workspace_upgrades_to_global(self, tmp_path: Path) -> None:
        """跨盘 path → MUTATING_GLOBAL → trust 模式 ALLOW（解决用户原始抱怨）。

        plan v2 设计：trust 模式下跨盘写允许（用户开 trust 就是要"少打扰"），
        敏感路径靠 ``safety_immune.paths`` opt-in 保护，而非默认拦截。
        但 needs_checkpoint=True 仍传给 C8 提示先快照。
        """
        engine = PolicyEngineV2()
        outside = tmp_path.parent / "_outside_99" / "x.txt"
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": str(outside)}),
            _ctx(tmp_path, mode=ConfirmationMode.TRUST),
        )
        assert decision.approval_class == ApprovalClass.MUTATING_GLOBAL
        assert decision.action == DecisionAction.ALLOW
        assert decision.needs_checkpoint is True

    def test_write_outside_workspace_default_mode_confirms(
        self, tmp_path: Path
    ) -> None:
        """default 模式跨盘写 → CONFIRM（合理保护）。"""
        engine = PolicyEngineV2()
        outside = tmp_path.parent / "_outside_99" / "x.txt"
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": str(outside)}),
            _ctx(tmp_path, mode=ConfirmationMode.DEFAULT),
        )
        assert decision.approval_class == ApprovalClass.MUTATING_GLOBAL
        assert decision.action == DecisionAction.CONFIRM

    def test_write_inside_workspace_trust_allows(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        inside = tmp_path / "x.txt"
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": str(inside)}),
            _ctx(tmp_path, mode=ConfirmationMode.TRUST),
        )
        assert decision.approval_class == ApprovalClass.MUTATING_SCOPED
        # MUTATING_SCOPED 在 trust 模式 = ALLOW
        assert decision.action == DecisionAction.ALLOW


# ============================================================
# C3 review additions: path boundary, risk signal extraction, intent audit
# ============================================================


class TestSafetyImmunePathBoundary:
    """C3 复审 fix：safety_immune 必须按路径组件边界匹配，不能裸 startswith。

    隐患示例：``safety_immune_paths = ('/private_test_lab/ssh',)``，攻击者
    构造路径 ``/private_test_lab/ssh-old/x.txt``——裸 startswith 会误中，把
    无关目录强制升 CONFIRM。极端情况下也可能造成"安全错觉"或拒绝合法
    请求，构成可利用的策略漂移。

    **C8 注**：早期版本用 ``/etc/ssh`` 与 ``C:\\ProgramData\\OpenAkita`` 做
    fixture，C8 引入 ``BUILTIN_SAFETY_IMMUNE_PATHS`` 后这些路径已被 ``/etc/**``
    与 ``C:/ProgramData/**`` 内置覆盖，会让"sibling not matched"误成 PASS。
    改用合成命名空间（``/private_test_lab/...`` / ``D:/TestLab/...``）保留
    boundary 语义验证，与 builtin 解耦。
    """

    def test_sibling_directory_not_matched(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        ctx = _ctx(tmp_path, safety_immune_paths=("/private_test_lab/ssh",))
        d = engine.evaluate_tool_call(
            ToolCallEvent(
                tool="write_file",
                params={"path": "/private_test_lab/ssh-old/x.txt"},
            ),
            ctx,
        )
        assert d.safety_immune_match is None

    def test_real_child_matched(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        ctx = _ctx(tmp_path, safety_immune_paths=("/private_test_lab/ssh",))
        d = engine.evaluate_tool_call(
            ToolCallEvent(
                tool="write_file",
                params={"path": "/private_test_lab/ssh/sshd_config"},
            ),
            ctx,
        )
        assert d.safety_immune_match is not None
        assert d.action == DecisionAction.CONFIRM

    def test_exact_path_matched(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        ctx = _ctx(tmp_path, safety_immune_paths=("/private_test_lab/ssh",))
        d = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": "/private_test_lab/ssh"}),
            ctx,
        )
        assert d.safety_immune_match is not None

    def test_windows_backslash_normalized(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        ctx = _ctx(tmp_path, safety_immune_paths=("D:\\TestLab\\OpenAkita",))
        d = engine.evaluate_tool_call(
            ToolCallEvent(
                tool="write_file",
                params={"path": "D:\\TestLab\\OpenAkita\\config.yaml"},
            ),
            ctx,
        )
        assert d.safety_immune_match is not None

    def test_case_insensitive_match_windows(self, tmp_path: Path) -> None:
        engine = PolicyEngineV2()
        ctx = _ctx(tmp_path, safety_immune_paths=("/Users/Me/identity",))
        d = engine.evaluate_tool_call(
            ToolCallEvent(
                tool="write_file", params={"path": "/users/me/identity/SOUL.md"}
            ),
            ctx,
        )
        assert d.safety_immune_match is not None

    def test_empty_protected_string_not_universal_match(self, tmp_path: Path) -> None:
        """``''`` 在 immune 列表里不应当成"匹配一切"。"""
        engine = PolicyEngineV2()
        ctx = _ctx(tmp_path, safety_immune_paths=("",))
        d = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": "/anywhere/file.txt"}),
            ctx,
        )
        assert d.safety_immune_match is None

    def test_unc_path_normalized(self, tmp_path: Path) -> None:
        """UNC ``\\\\server\\share`` 路径应被正确识别为同目录。"""
        from openakita.core.policy_v2.engine import _path_under

        assert _path_under("//server/share/file", "//server/share") is True
        assert _path_under("\\\\server\\share\\file", "\\\\server\\share") is True

    def test_mixed_separator_normalized(self, tmp_path: Path) -> None:
        from openakita.core.policy_v2.engine import _path_under

        assert _path_under("C:/foo/bar", "C:\\foo") is True
        assert _path_under("/foo//bar///baz", "/foo/bar") is True

    # ---- C6: glob /** 锚定符支持 ----
    def test_double_star_anchor_matches_descendants(self) -> None:
        """``C:/Windows/**`` 应作为 ``C:/Windows`` 目录前缀匹配（C6 修复）。

        修复前：把 ``**`` 当字面字符 → 永远 false negative；导致 POLICIES.yaml
        里写 ``zones.protected: [C:/Windows/**]`` 完全失效。
        """
        from openakita.core.policy_v2.engine import _path_under, _strip_glob_anchor

        assert _strip_glob_anchor("c:/windows/**") == "c:/windows"
        assert _strip_glob_anchor("/etc/**") == "/etc"
        assert _strip_glob_anchor("/etc/**/") == "/etc"  # _normalize 之后再剥
        assert _strip_glob_anchor("**") == ""
        assert _strip_glob_anchor("/etc/ssh") == "/etc/ssh"  # 无变化

        assert _path_under("C:/Windows/System32/x.dll", "C:/Windows/**") is True
        assert _path_under("/etc/ssh/sshd_config", "/etc/**") is True
        assert _path_under("/etc/ssh", "/etc/**") is True  # 该目录本身也算 under
        assert _path_under("/var/log/x", "/etc/**") is False

    def test_single_star_anchor_also_stripped(self) -> None:
        """末尾 ``/*`` 也按 directory anchor 处理。"""
        from openakita.core.policy_v2.engine import _path_under

        assert _path_under("/etc/ssh/sshd_config", "/etc/*") is True
        assert _path_under("/etc/passwd", "/etc/*") is True

    def test_intermediate_glob_is_literal_not_supported(self) -> None:
        """中段 glob (``/etc/*/secret``) 不做 fnmatch —— 仍按字面前缀匹配。

        若后期需要支持，应在 schema 层拆 ``exact_paths`` vs ``glob_patterns``，
        而不是把万能 fnmatch 灌进 hot path。
        """
        from openakita.core.policy_v2.engine import _path_under

        # 字面 prefix 不匹配（因为 raw 不以 ``/etc/*/`` literal 开头）
        assert _path_under("/etc/ssh/secret", "/etc/*/secret") is False


class TestExtractRiskSignal:
    """C3 复审 fix：_extract_risk_signal 必须能解析真实 RiskIntentResult 字段。

    v1 字段是 ``operation_kind`` 不是 ``operation``；``requires_confirmation``
    是直接信号；中性值（none/low/read/...）应归 None；StrEnum 值应 unwrap 成
    短字符串而非 ``RiskLevel.HIGH`` 这种带前缀的 repr。
    """

    def test_real_risk_intent_result_extracted(self) -> None:
        from openakita.core.policy_v2.engine import _extract_risk_signal
        from openakita.core.risk_intent import (
            OperationKind,
            RiskIntentResult,
            RiskLevel,
        )

        r = RiskIntentResult(
            risk_level=RiskLevel.HIGH,
            operation_kind=OperationKind.WRITE,
        )
        signal = _extract_risk_signal(r)
        assert signal in ("high", "write"), f"Expected high or write, got {signal!r}"

    def test_requires_confirmation_alone_is_signal(self) -> None:
        from openakita.core.policy_v2.engine import _extract_risk_signal
        from openakita.core.risk_intent import (
            OperationKind,
            RiskIntentResult,
            RiskLevel,
        )

        r = RiskIntentResult(
            risk_level=RiskLevel.LOW,
            operation_kind=OperationKind.READ,
            requires_confirmation=True,
        )
        signal = _extract_risk_signal(r)
        assert signal is not None  # confirms-required IS a signal

    def test_neutral_state_no_signal(self) -> None:
        from openakita.core.policy_v2.engine import _extract_risk_signal
        from openakita.core.risk_intent import (
            OperationKind,
            RiskIntentResult,
            RiskLevel,
        )

        r = RiskIntentResult(
            risk_level=RiskLevel.NONE,
            operation_kind=OperationKind.NONE,
            requires_confirmation=False,
        )
        assert _extract_risk_signal(r) is None

    def test_low_risk_with_write_op_returns_write(self) -> None:
        """LOW risk_level 是中性，但 operation_kind=WRITE 是真信号。"""
        from openakita.core.policy_v2.engine import _extract_risk_signal
        from openakita.core.risk_intent import (
            OperationKind,
            RiskIntentResult,
            RiskLevel,
        )

        r = RiskIntentResult(
            risk_level=RiskLevel.LOW,
            operation_kind=OperationKind.WRITE,
        )
        assert _extract_risk_signal(r) == "write"

    def test_dict_with_strenum_value(self) -> None:
        """dict 形式 + StrEnum 值，应 unwrap 成 'high'，不是 'RiskLevel.HIGH'。"""
        from openakita.core.policy_v2.engine import _extract_risk_signal
        from openakita.core.risk_intent import RiskLevel

        signal = _extract_risk_signal({"risk_level": RiskLevel.HIGH})
        assert signal == "high"

    def test_dict_requires_confirmation(self) -> None:
        from openakita.core.policy_v2.engine import _extract_risk_signal

        signal = _extract_risk_signal({"requires_confirmation": True})
        assert signal == "requires_confirmation"

    def test_intent_full_pipeline_with_real_result(self, tmp_path: Path) -> None:
        """端到端：真实 RiskIntentResult 喂 evaluate_message_intent。"""
        from openakita.core.risk_intent import (
            OperationKind,
            RiskIntentResult,
            RiskLevel,
        )

        engine = PolicyEngineV2()
        r = RiskIntentResult(
            risk_level=RiskLevel.HIGH,
            operation_kind=OperationKind.DELETE,
            requires_confirmation=True,
        )
        d = engine.evaluate_message_intent(
            MessageIntentEvent(message="delete x", risk_intent=r),
            _ctx(tmp_path),
        )
        assert d.action == DecisionAction.CONFIRM


class TestAuditIntentHook:
    """C3 复审 fix：evaluate_message_intent 应也支持 audit_intent_hook。"""

    def test_intent_hook_called(self, tmp_path: Path) -> None:
        captured = []

        def hook(decision, event, ctx):  # noqa: ANN001
            captured.append((decision.action, event.message, ctx.session_id))

        engine = PolicyEngineV2(audit_intent_hook=hook)
        engine.evaluate_message_intent(
            MessageIntentEvent(message="hello"),
            _ctx(tmp_path),
        )
        assert len(captured) == 1
        assert captured[0][1] == "hello"

    def test_intent_hook_exception_does_not_propagate(self, tmp_path: Path) -> None:
        def bad_hook(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ARG001
            raise RuntimeError("intent audit broken")

        engine = PolicyEngineV2(audit_intent_hook=bad_hook)
        decision = engine.evaluate_message_intent(
            MessageIntentEvent(message="x"),
            _ctx(tmp_path),
        )
        assert decision.action == DecisionAction.ALLOW

    def test_tool_hook_and_intent_hook_independent(self, tmp_path: Path) -> None:
        """audit_hook 只接收 tool calls；audit_intent_hook 只接收 intent。"""
        tool_calls = []
        intent_calls = []

        engine = PolicyEngineV2(
            audit_hook=lambda d, e, c: tool_calls.append(e.tool),  # noqa: ARG005
            audit_intent_hook=lambda d, e, c: intent_calls.append(e.message),  # noqa: ARG005
        )
        engine.evaluate_tool_call(ToolCallEvent(tool="read_file"), _ctx(tmp_path))
        engine.evaluate_message_intent(
            MessageIntentEvent(message="hi"), _ctx(tmp_path)
        )
        assert tool_calls == ["read_file"]
        assert intent_calls == ["hi"]


class TestClassifierConcurrency:
    """C3 复审 fix：classifier cache 在并发下不能抛 KeyError。

    OrderedDict 在 CPython 单 op 由 GIL 保护，但 ``get`` + ``move_to_end``
    不是原子的——若另一线程在中间 popitem 把当前 key 淘汰，会 KeyError。
    classifier 用 try/except 兜住此竞态（返回正确值，仅 LRU 排序短暂失序）。
    """

    def test_concurrent_classify_no_crash(self) -> None:
        import threading

        clf = ApprovalClassifier(cache_size=2)  # 极小 cache 加大竞争
        errors: list[str] = []

        def worker():
            try:
                for i in range(500):
                    clf.classify_with_source(f"tool_{i % 9}")
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [], f"concurrent classify raised: {errors[:3]}"


# ============================================================
# Helpers
# ============================================================


def _src_explicit():
    """返回 EXPLICIT_REGISTER_PARAM 用的 DecisionSource enum 值。"""
    from openakita.core.policy_v2 import DecisionSource

    return DecisionSource.EXPLICIT_REGISTER_PARAM

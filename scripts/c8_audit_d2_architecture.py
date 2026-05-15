"""C8 audit D2 — 架构合理性：IM SSE 不再早退、PolicyContext 正确传播
session_role + is_owner、safety_immune builtin 与 ctx 正确联合."""

from __future__ import annotations

import asyncio
from pathlib import Path

from openakita.core.policy_v2 import (
    BUILTIN_SAFETY_IMMUNE_PATHS,
    ConfirmationMode,
    PolicyContext,
    SessionRole,
    build_policy_context,
)


def _check_session_role_propagation() -> None:
    """模拟 ``switch_mode`` 改 session.session_role 后下一轮 ctx 反映新 role."""

    class _Sess:
        def __init__(self) -> None:
            self.session_role = "agent"
            self.confirmation_mode_override = None
            self.channel = "telegram"
            self._meta: dict = {}

        def get_metadata(self, k, default=None):
            return self._meta.get(k, default)

        def set_metadata(self, k, v):
            self._meta[k] = v

    sess = _Sess()
    ctx = build_policy_context(session=sess, workspace=Path.cwd(), mode="agent")
    assert ctx.session_role == SessionRole.AGENT, f"expected AGENT, got {ctx.session_role}"

    # switch_mode 会修改这个字段
    sess.session_role = "plan"
    ctx2 = build_policy_context(session=sess, workspace=Path.cwd(), mode="agent")
    # session.session_role 优先级 > 入参 mode
    assert ctx2.session_role == SessionRole.PLAN, f"expected PLAN, got {ctx2.session_role}"
    print("D2 session_role propagation: OK (switch_mode → ctx.session_role)")


def _check_is_owner_propagation() -> None:
    class _Sess:
        def __init__(self, is_owner: bool | None) -> None:
            self.session_role = "agent"
            self.confirmation_mode_override = None
            self.channel = "telegram"
            self._meta: dict = {}
            if is_owner is not None:
                self._meta["is_owner"] = is_owner

        def get_metadata(self, k, default=None):
            return self._meta.get(k, default)

        def set_metadata(self, k, v):
            self._meta[k] = v

    # IM gateway sets is_owner=False → ctx reflects it
    ctx = build_policy_context(session=_Sess(is_owner=False), workspace=Path.cwd())
    assert ctx.is_owner is False, "is_owner=False from session metadata not honored"

    # IM gateway sets is_owner=True → ctx reflects it
    ctx = build_policy_context(session=_Sess(is_owner=True), workspace=Path.cwd())
    assert ctx.is_owner is True

    # No metadata → kwarg default applies (back-compat single-user)
    ctx = build_policy_context(
        session=_Sess(is_owner=None), workspace=Path.cwd(), is_owner=True
    )
    assert ctx.is_owner is True
    print("D2 is_owner propagation: OK (gateway metadata → ctx.is_owner)")


def _check_confirmation_mode_override() -> None:
    class _Sess:
        def __init__(self, override: str | None) -> None:
            self.session_role = "agent"
            self.confirmation_mode_override = override
            self.channel = "telegram"
            self._meta: dict = {}

        def get_metadata(self, k, default=None):
            return self._meta.get(k, default)

        def set_metadata(self, k, v):
            self._meta[k] = v

    ctx = build_policy_context(session=_Sess("strict"), workspace=Path.cwd())
    assert ctx.confirmation_mode == ConfirmationMode.STRICT
    ctx = build_policy_context(session=_Sess(None), workspace=Path.cwd())
    # falls back to global config; should be a valid ConfirmationMode
    assert isinstance(ctx.confirmation_mode, ConfirmationMode)
    print("D2 confirmation_mode_override: OK (per-session override honored)")


async def _check_im_sse_no_early_exit_smoke() -> None:
    """Light smoke: reasoning_engine source no longer contains the abort
    string for IM CONFIRM. Real flow test would need a full agent integration;
    we settle for a structural assert here."""
    src = Path("src/openakita/core/reasoning_engine.py").read_text(encoding="utf-8")
    # The two early-exit branches contained this Chinese string; if it still
    # appears we know we missed a hotspot.
    abort_phrase = "IM 通道，无法安全完成交互式确认"
    assert abort_phrase not in src, (
        f"reasoning_engine still contains the IM early-exit phrase {abort_phrase!r} — "
        "C8 §2.3 fix incomplete"
    )
    print("D2 reasoning_engine IM early-exit: REMOVED (no abort phrase found)")


def _check_safety_immune_user_addition() -> None:
    """Verify ctx.safety_immune_paths can ADD to but not remove from builtin."""
    from openakita.core.policy_v2 import PolicyEngineV2, ToolCallEvent

    e = PolicyEngineV2()
    # User adds a path via ctx
    ctx = PolicyContext(
        session_id="t",
        workspace=Path.cwd(),
        safety_immune_paths=("/my/special/dir",),
    )
    immune = e._collect_immune_paths(ctx)
    # both builtin and ctx-added are present
    assert "/my/special/dir" in immune
    assert any("/etc/**" in p or "/etc" in p for p in immune)
    # Hitting builtin path triggers the immune match
    decision = e.evaluate_tool_call(
        ToolCallEvent(tool="write_file", params={"path": "/etc/passwd"}),
        ctx,
    )
    assert decision.safety_immune_match is not None
    print("D2 safety_immune builtin + ctx union: OK (additive only)")


def main() -> None:
    _check_session_role_propagation()
    _check_is_owner_propagation()
    _check_confirmation_mode_override()
    asyncio.run(_check_im_sse_no_early_exit_smoke())
    _check_safety_immune_user_addition()
    print()
    print(f"D2 ALL PASS — {len(BUILTIN_SAFETY_IMMUNE_PATHS)} builtin immune paths")


if __name__ == "__main__":
    main()

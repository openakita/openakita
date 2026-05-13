"""C8 audit D1 — 完整性：5 子任务每个 wire-up 都到位."""

from __future__ import annotations

from openakita.channels.gateway import MessageGateway
from openakita.core.policy_v2 import (
    BUILTIN_SAFETY_IMMUNE_BY_CATEGORY,
    BUILTIN_SAFETY_IMMUNE_PATHS,
    PolicyEngineV2,
    expand_builtin_immune_paths,
)
from openakita.sessions.session import Session
from openakita.tools.handlers.mode import ModeHandler


def main() -> None:
    # ---- #1 safety_immune ----
    e = PolicyEngineV2()
    print(
        f"#1 builtin immune: {len(BUILTIN_SAFETY_IMMUNE_BY_CATEGORY)} categories, "
        f"{len(BUILTIN_SAFETY_IMMUNE_PATHS)} paths"
    )
    assert len(BUILTIN_SAFETY_IMMUNE_BY_CATEGORY) == 9, "must have exactly 9 categories"
    print(f"#1 engine wired: {len(e._immune_paths_from_config)} immune paths active")
    assert len(e._immune_paths_from_config) >= len(BUILTIN_SAFETY_IMMUNE_PATHS)
    print("#1 expand_builtin_immune_paths():", expand_builtin_immune_paths()[:3], "...")

    # ---- #2 OwnerOnly + IM owner ----
    print(
        f"#2 Gateway._get_owner_user_ids exists: "
        f"{hasattr(MessageGateway, '_get_owner_user_ids')}"
    )
    print(
        f"#2 Gateway._apply_persisted_owner_allowlist exists: "
        f"{hasattr(MessageGateway, '_apply_persisted_owner_allowlist')}"
    )
    assert hasattr(MessageGateway, "_get_owner_user_ids")
    assert hasattr(MessageGateway, "_apply_persisted_owner_allowlist")

    # ---- #3 switch_mode ----
    print(f"#3 ModeHandler.TOOL_CLASSES: {ModeHandler.TOOL_CLASSES}")
    s = Session.create(channel="cli", chat_id="c", user_id="u")
    print(f"#3 Session.session_role: {s.session_role!r}")
    print(f"#3 Session.confirmation_mode_override: {s.confirmation_mode_override!r}")
    assert s.session_role == "agent"
    assert s.confirmation_mode_override is None
    s.session_role = "plan"
    rebuilt = Session.from_dict(s.to_dict())
    assert rebuilt.session_role == "plan"
    print("#3 Session round-trip session_role='plan': OK")

    # ---- #4 consume_session_trust prune ----
    from openakita.core.trusted_paths import (
        SESSION_KEY,
        consume_session_trust,
        grant_session_trust,
    )

    class _Sess:
        def __init__(self) -> None:
            self._meta = {}

        def get_metadata(self, k, default=None):
            return self._meta.get(k, default)

        def set_metadata(self, k, v):
            self._meta[k] = v

    sess = _Sess()
    grant_session_trust(sess, operation="write")
    grant_session_trust(sess, operation="delete", expires_at=0.0)  # already expired
    assert len(sess.get_metadata(SESSION_KEY)["rules"]) == 2
    consume_session_trust(sess, message="x", operation="write")
    remaining = sess.get_metadata(SESSION_KEY)["rules"]
    assert len(remaining) == 1, f"expected 1 rule, got {len(remaining)}"
    print(f"#4 consume_session_trust prunes expired: OK ({remaining[0]['operation']!r} kept)")

    # ---- #5 IM prefix early-exit gone + idempotent prepare_ui_confirm ----
    from openakita.core.policy import get_policy_engine, reset_policy_engine
    from openakita.core.reasoning_engine import _is_im_conversation

    assert _is_im_conversation("telegram:1234") is True
    print("#5 _is_im_conversation('telegram:1234'): True")

    reset_policy_engine()
    pe = get_policy_engine()
    pe.prepare_ui_confirm("test-id-c8")
    ev1 = pe._ui_confirm_events["test-id-c8"]
    pe.prepare_ui_confirm("test-id-c8")  # second call must be idempotent
    ev2 = pe._ui_confirm_events["test-id-c8"]
    assert ev1 is ev2, "prepare_ui_confirm must be idempotent (same Event)"
    print("#5 prepare_ui_confirm idempotent: OK (same Event instance)")
    pe.cleanup_ui_confirm("test-id-c8")

    print()
    print("D1 ALL PASS")


if __name__ == "__main__":
    main()

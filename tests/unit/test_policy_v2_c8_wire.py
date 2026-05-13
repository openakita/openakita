"""C8 wire-up tests: 5 sub-fixes covering safety_immune builtin, OwnerOnly +
IM owner judgment, switch_mode → session_role, consume_session_trust pruning,
and the IM-prefix early-exit removal in reasoning_engine.

Each suite has a tight focus and a regression assertion that protects the
specific pitfall called out in ``docs/policy_v2_research.md`` §2.x.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from openakita.core.policy_v2 import (
    BUILTIN_SAFETY_IMMUNE_BY_CATEGORY,
    BUILTIN_SAFETY_IMMUNE_PATHS,
    ApprovalClass,
    ConfirmationMode,
    DecisionAction,
    DecisionSource,
    PolicyConfigV2,
    PolicyContext,
    PolicyEngineV2,
    SafetyImmuneConfig,
    SessionRole,
    ToolCallEvent,
    build_policy_context,
    expand_builtin_immune_paths,
)
from openakita.core.trusted_paths import (
    SESSION_KEY,
    consume_session_trust,
    grant_session_trust,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(workspace: Path, **overrides: Any) -> PolicyContext:
    base = {
        "session_id": "c8-test",
        "workspace": workspace,
        "channel": "desktop",
    }
    base.update(overrides)
    return PolicyContext(**base)


class _FakeSession:
    """Minimal duck-typed session for adapter.build_policy_context tests."""

    def __init__(
        self,
        *,
        session_role: str = "agent",
        confirmation_mode_override: str | None = None,
        is_owner: bool | None = None,
        channel: str = "desktop",
    ) -> None:
        self.session_role = session_role
        self.confirmation_mode_override = confirmation_mode_override
        self.channel = channel
        self._meta: dict[str, Any] = {}
        if is_owner is not None:
            self._meta["is_owner"] = is_owner

    def get_metadata(self, key: str, default: Any = None) -> Any:
        return self._meta.get(key, default)

    def set_metadata(self, key: str, value: Any) -> None:
        self._meta[key] = value


# ===========================================================================
# #1 safety_immune builtin 9 categories
# ===========================================================================


class TestSafetyImmuneBuiltinCategories:
    def test_nine_categories_are_present(self) -> None:
        """`BUILTIN_SAFETY_IMMUNE_BY_CATEGORY` must enumerate exactly 9 keys."""
        assert len(BUILTIN_SAFETY_IMMUNE_BY_CATEGORY) == 9
        expected = {
            "identity",
            "audit",
            "checkpoints",
            "sessions",
            "scheduler",
            "credentials",
            "os_system",
            "kernel_fs",
            "package_dirs",
        }
        assert set(BUILTIN_SAFETY_IMMUNE_BY_CATEGORY) == expected

    def test_flat_list_matches_category_union(self) -> None:
        """`BUILTIN_SAFETY_IMMUNE_PATHS` must equal the flattened categories."""
        union: list[str] = []
        for paths in BUILTIN_SAFETY_IMMUNE_BY_CATEGORY.values():
            union.extend(paths)
        assert list(BUILTIN_SAFETY_IMMUNE_PATHS) == union

    def test_expansion_resolves_cwd_and_home(self, tmp_path: Path) -> None:
        expanded = expand_builtin_immune_paths(cwd=tmp_path)
        cwd_str = str(tmp_path).replace("\\", "/")
        # ${CWD} expanded
        assert any(p.startswith(cwd_str) for p in expanded)
        # ~ expanded (cross-platform Path.home())
        home_prefix = str(Path("~").expanduser()).replace("\\", "/")
        assert any(p.startswith(home_prefix) for p in expanded)
        # No raw placeholders remain
        assert all("${CWD}" not in p for p in expanded)
        assert all(not p.startswith("~") for p in expanded)

    def test_engine_protects_identity_soul_md(self, tmp_path: Path) -> None:
        """write_file on $CWD/identity/SOUL.md must trigger CONFIRM via builtin."""
        cwd = Path.cwd()  # engine uses Path.cwd() at __init__ for builtin expansion
        engine = PolicyEngineV2()
        ctx = _ctx(cwd)
        target = str(cwd / "identity" / "SOUL.md")
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": target}),
            ctx,
        )
        assert decision.safety_immune_match is not None
        assert "SOUL.md" in decision.safety_immune_match

    def test_engine_protects_etc_glob(self, tmp_path: Path) -> None:
        """write_file under /etc/anything triggers CONFIRM via builtin /etc/**."""
        engine = PolicyEngineV2()
        ctx = _ctx(tmp_path)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": "/etc/passwd"}),
            ctx,
        )
        assert decision.safety_immune_match is not None
        assert "/etc" in decision.safety_immune_match

    def test_user_paths_can_add_to_builtin_not_replace(self) -> None:
        """User-provided safety_immune.paths is unioned with builtin (additive)."""
        # User adds a custom immune path that's NOT in builtin
        config = PolicyConfigV2(
            safety_immune=SafetyImmuneConfig(paths=["/my/custom/dir"])
        )
        engine = PolicyEngineV2(config=config)
        # Both builtin /etc/** and custom /my/custom/dir present
        immune = engine._immune_paths_from_config
        assert "/my/custom/dir" in immune
        assert "/etc/**" in immune

    def test_user_cannot_remove_builtin_via_config(self) -> None:
        """Even if user lists a builtin path explicitly, no duplication."""
        config = PolicyConfigV2(
            safety_immune=SafetyImmuneConfig(paths=["/etc/**"])
        )
        engine = PolicyEngineV2(config=config)
        # /etc/** appears exactly once (no duplicate)
        assert engine._immune_paths_from_config.count("/etc/**") == 1


# ===========================================================================
# #2 OwnerOnly: PolicyContext.is_owner from session.metadata + engine gate
# ===========================================================================


class TestOwnerOnlyAndIsOwnerWiring:
    def test_build_policy_context_reads_is_owner_metadata(
        self, tmp_path: Path
    ) -> None:
        """If session has metadata['is_owner']=False, ctx must reflect that."""
        session = _FakeSession(is_owner=False)
        ctx = build_policy_context(
            session=session, workspace=tmp_path, mode="agent", is_owner=True
        )
        assert ctx.is_owner is False

    def test_build_policy_context_defaults_is_owner_true_when_metadata_missing(
        self, tmp_path: Path
    ) -> None:
        """No metadata['is_owner'] → kwarg default applies (back-compat)."""
        session = _FakeSession()  # no is_owner set
        ctx = build_policy_context(
            session=session, workspace=tmp_path, mode="agent", is_owner=True
        )
        assert ctx.is_owner is True

    def test_engine_blocks_control_plane_when_not_owner(self, tmp_path: Path) -> None:
        """CONTROL_PLANE class with is_owner=False → DENY (engine step 4).

        Inject an ``explicit_lookup`` into ApprovalClassifier so the engine
        sees ``switch_mode`` as CONTROL_PLANE without depending on the global
        handler registry (production wires this via ``agent.py:_init_handlers``
        → ``rebuild_engine_v2(explicit_lookup=registry.get_tool_class)``).
        """
        from openakita.core.policy_v2 import ApprovalClassifier

        def _lookup(name: str) -> tuple[ApprovalClass, DecisionSource] | None:
            if name == "switch_mode":
                return ApprovalClass.CONTROL_PLANE, DecisionSource.EXPLICIT_HANDLER_ATTR
            return None

        classifier = ApprovalClassifier(explicit_lookup=_lookup)
        engine = PolicyEngineV2(classifier=classifier)
        ctx = _ctx(tmp_path, is_owner=False)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(
                tool="switch_mode",
                params={"target_mode": "agent"},
            ),
            ctx,
        )
        assert decision.action == DecisionAction.DENY
        assert decision.is_owner_required is True

    def test_engine_allows_control_plane_when_owner(self, tmp_path: Path) -> None:
        from openakita.core.policy_v2 import ApprovalClassifier

        def _lookup(name: str) -> tuple[ApprovalClass, DecisionSource] | None:
            if name == "switch_mode":
                return ApprovalClass.CONTROL_PLANE, DecisionSource.EXPLICIT_HANDLER_ATTR
            return None

        classifier = ApprovalClassifier(explicit_lookup=_lookup)
        engine = PolicyEngineV2(classifier=classifier)
        ctx = _ctx(tmp_path, is_owner=True)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(
                tool="switch_mode",
                params={"target_mode": "agent"},
            ),
            ctx,
        )
        # owner=True → step 4 passes; final action depends on matrix
        # but is_owner_required must NOT be set
        assert decision.is_owner_required is False

    def test_owner_only_explicit_tool_list_blocks_non_owner(
        self, tmp_path: Path
    ) -> None:
        """`config.owner_only.tools` list is the second path: explicit per-tool
        owner_only opt-in (not necessarily CONTROL_PLANE class)."""
        from openakita.core.policy_v2 import OwnerOnlyConfig

        config = PolicyConfigV2(
            owner_only=OwnerOnlyConfig(tools=["read_file"])
        )
        engine = PolicyEngineV2(config=config)
        ctx = _ctx(tmp_path, is_owner=False)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="read_file", params={"path": str(tmp_path / "x")}),
            ctx,
        )
        assert decision.action == DecisionAction.DENY
        assert decision.is_owner_required is True


# ===========================================================================
# #3 switch_mode → session_role; build_policy_context honors it
# ===========================================================================


class TestSwitchModeWriteThrough:
    def test_session_dataclass_has_session_role_field(self) -> None:
        """Regression: docs §2.2 — Session must have session_role attribute."""
        from openakita.sessions.session import Session

        s = Session.create(channel="cli", chat_id="c", user_id="u")
        assert hasattr(s, "session_role")
        assert s.session_role == "agent"
        assert hasattr(s, "confirmation_mode_override")
        assert s.confirmation_mode_override is None

    def test_session_round_trips_session_role_in_to_dict(self) -> None:
        from openakita.sessions.session import Session

        s = Session.create(channel="cli", chat_id="c", user_id="u")
        s.session_role = "plan"
        s.confirmation_mode_override = "strict"
        d = s.to_dict()
        assert d["session_role"] == "plan"
        assert d["confirmation_mode_override"] == "strict"
        round_tripped = Session.from_dict(d)
        assert round_tripped.session_role == "plan"
        assert round_tripped.confirmation_mode_override == "strict"

    def test_session_from_dict_back_compat_old_payload(self) -> None:
        """Old sessions.json without these fields must default to ('agent', None)."""
        from openakita.sessions.session import Session

        # Simulate an old serialized session
        s = Session.create(channel="cli", chat_id="c", user_id="u")
        old_dict = s.to_dict()
        old_dict.pop("session_role", None)
        old_dict.pop("confirmation_mode_override", None)

        rebuilt = Session.from_dict(old_dict)
        assert rebuilt.session_role == "agent"
        assert rebuilt.confirmation_mode_override is None

    @pytest.mark.asyncio
    async def test_switch_mode_handler_writes_session_role(self) -> None:
        """ModeHandler._switch_mode should set session.session_role (not .mode)."""
        from openakita.sessions.session import Session
        from openakita.tools.handlers.mode import ModeHandler

        agent = MagicMock()
        agent.session = Session.create(channel="cli", chat_id="c", user_id="u")
        handler = ModeHandler(agent)

        result = await handler._switch_mode({"target_mode": "plan", "reason": "test"})

        assert agent.session.session_role == "plan"
        assert "Plan" in result

    def test_build_policy_context_honors_session_session_role(
        self, tmp_path: Path
    ) -> None:
        """If session.session_role='plan', PolicyContext.session_role=PLAN even when
        the kwarg ``mode`` says 'agent'. Demonstrates switch_mode actually takes
        effect on the next decision."""
        session = _FakeSession(session_role="plan")
        ctx = build_policy_context(
            session=session, workspace=tmp_path, mode="agent"
        )
        assert ctx.session_role == SessionRole.PLAN

    def test_build_policy_context_honors_confirmation_mode_override(
        self, tmp_path: Path
    ) -> None:
        session = _FakeSession(confirmation_mode_override="strict")
        ctx = build_policy_context(session=session, workspace=tmp_path)
        assert ctx.confirmation_mode == ConfirmationMode.STRICT


# ===========================================================================
# #4 consume_session_trust prunes expired rules
# ===========================================================================


class TestConsumeSessionTrustPrunesExpired:
    def test_expired_rules_are_removed_after_consume(self) -> None:
        session = _FakeSession()
        # Active rule (no expiry)
        grant_session_trust(session, operation="write")
        # Expired rule
        grant_session_trust(
            session, operation="delete", expires_at=time.time() - 100
        )
        # Another expired rule
        grant_session_trust(
            session, operation="execute", expires_at=time.time() - 50
        )

        rules_before = session.get_metadata(SESSION_KEY)["rules"]
        assert len(rules_before) == 3

        # Consume — should match the active rule and prune the 2 expired ones
        matched = consume_session_trust(session, message="anything", operation="write")
        assert matched is True

        rules_after = session.get_metadata(SESSION_KEY)["rules"]
        assert len(rules_after) == 1
        assert rules_after[0]["operation"] == "write"

    def test_malformed_expires_at_also_pruned(self) -> None:
        session = _FakeSession()
        grant_session_trust(session, operation="write")
        # Manually inject a bad rule (e.g. non-numeric expires_at)
        overrides = session.get_metadata(SESSION_KEY)
        overrides["rules"].append(
            {"operation": "delete", "expires_at": "not-a-number"}
        )

        matched = consume_session_trust(session, message="anything", operation="write")
        assert matched is True

        rules = session.get_metadata(SESSION_KEY)["rules"]
        assert len(rules) == 1
        assert rules[0]["operation"] == "write"

    def test_consume_without_any_match_still_prunes(self) -> None:
        """Even if no rule matches the request, expired rules should be GC'd."""
        session = _FakeSession()
        grant_session_trust(
            session, operation="write", expires_at=time.time() - 1
        )
        # Request for a different op — no match, but expiry should still be pruned
        matched = consume_session_trust(session, message="x", operation="read")
        assert matched is False
        assert session.get_metadata(SESSION_KEY)["rules"] == []

    def test_no_rules_returns_false_without_metadata_write(self) -> None:
        """No rules at all → False, no metadata mutation (no spurious writes)."""
        session = _FakeSession()
        matched = consume_session_trust(session, message="x", operation="write")
        assert matched is False
        # session.metadata never had SESSION_KEY set
        assert SESSION_KEY not in session._meta


# ===========================================================================
# #5 IM-prefix early-exit removal: reasoning_engine no longer aborts
# ===========================================================================


class TestImPrefixSseFlow:
    def test_prepare_ui_confirm_is_idempotent_when_no_decision(self) -> None:
        """Two prepare calls on the same id must keep the first Event instance
        so that both reasoning_engine and gateway see the same wakeable."""
        from openakita.core.policy import get_policy_engine, reset_policy_engine

        reset_policy_engine()
        pe = get_policy_engine()
        pe.prepare_ui_confirm("c8-test-id")
        ev1 = pe._ui_confirm_events.get("c8-test-id")
        pe.prepare_ui_confirm("c8-test-id")
        ev2 = pe._ui_confirm_events.get("c8-test-id")
        assert ev1 is ev2  # same Event instance — no replacement
        pe.cleanup_ui_confirm("c8-test-id")

    def test_prepare_ui_confirm_reissues_after_resolution(self) -> None:
        """If id was already resolved, prepare should issue a fresh Event."""
        from openakita.core.policy import get_policy_engine, reset_policy_engine

        reset_policy_engine()
        pe = get_policy_engine()
        pe.prepare_ui_confirm("c8-second-round")
        ev1 = pe._ui_confirm_events.get("c8-second-round")
        # Force a resolution to land in decisions
        pe._ui_confirm_decisions["c8-second-round"] = "allow_once"
        # prepare again — must reissue because decision exists from a prior round
        pe.prepare_ui_confirm("c8-second-round")
        ev2 = pe._ui_confirm_events.get("c8-second-round")
        assert ev2 is not ev1
        assert "c8-second-round" not in pe._ui_confirm_decisions
        pe.cleanup_ui_confirm("c8-second-round")

    def test_is_im_conversation_helper_still_recognizes_prefixes(self) -> None:
        """Helper is now used for timeout heuristic, not for early-exit."""
        from openakita.core.reasoning_engine import _is_im_conversation

        assert _is_im_conversation("telegram:1234") is True
        assert _is_im_conversation("feishu:abc") is True
        assert _is_im_conversation("desktop_session_xx") is False
        assert _is_im_conversation(None) is False

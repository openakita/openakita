"""Stage 2 — End-to-end invariants: ``profile.current`` → engine decision.

Locks the contract that switching the product-level security profile (via
``_apply_security_profile_defaults``) yields the **correct runtime
behaviour** at the engine level.

Why this exists
---------------

Product layer (``profile.current``) and runtime layer
(``confirmation.mode`` + ``enabled``) are **deliberately separate**
(see :class:`SecurityProfileConfig` docstring): a profile is a UI macro
that *writes* a bundle of defaults across multiple independent fields,
and the engine only reads those fields, not ``profile.current``.

Stage 1 introduced one production-code consumer of ``profile.current``
itself — ``FilesystemHandler._allowed_roots()`` — which short-circuits
on ``("off", "trust")``. If a future change drifts the
``profile → fields`` mapping or the engine ↔ profile contract, those two
layers can disagree (e.g. filesystem allows but engine confirms).

These tests pin the whole chain so any such drift fails loudly:

    SecurityProfileUpdate body
            ↓
    _apply_security_profile_defaults(sec, profile)
            ↓
    PolicyConfigV2.model_validate(sec)   ← schema validates field types
            ↓
    PolicyEngineV2(config=cfg)
            ↓
    PolicyContext(confirmation_mode=cfg.confirmation.mode, ...)
            ↓
    engine.evaluate_tool_call(...).action == EXPECTED
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.api.routes.config import _apply_security_profile_defaults
from openakita.core.policy_v2 import (
    ConfirmationMode,
    DecisionAction,
    PolicyConfigV2,
    PolicyContext,
    PolicyEngineV2,
    SessionRole,
    ToolCallEvent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_cfg_from_profile(profile: str) -> PolicyConfigV2:
    """Mimic the API path: dict → ``_apply_security_profile_defaults`` → cfg."""
    sec: dict = {}
    _apply_security_profile_defaults(sec, profile)
    return PolicyConfigV2.model_validate(sec)


def _ctx_from_cfg(cfg: PolicyConfigV2, workspace: Path) -> PolicyContext:
    """Mimic the adapter: cfg.confirmation.mode → ctx.confirmation_mode."""
    mode_value = cfg.confirmation.mode
    if not isinstance(mode_value, ConfirmationMode):
        mode_value = ConfirmationMode(mode_value)
    return PolicyContext(
        session_id="invariant-test",
        workspace=workspace,
        session_role=SessionRole.AGENT,
        confirmation_mode=mode_value,
        is_owner=True,
    )


# ---------------------------------------------------------------------------
# Profile → field mapping (the bundle written by ``_apply_security_profile_defaults``)
# ---------------------------------------------------------------------------


class TestProfileFieldMapping:
    """Field-level assertions: profile preset writes the documented bundle."""

    def test_trust_profile_writes_trust_mode_and_enabled(self) -> None:
        cfg = _build_cfg_from_profile("trust")
        assert cfg.profile.current == "trust"
        assert cfg.enabled is True
        assert cfg.confirmation.mode == ConfirmationMode.TRUST

    def test_protect_profile_writes_default_mode_and_enabled(self) -> None:
        cfg = _build_cfg_from_profile("protect")
        assert cfg.profile.current == "protect"
        assert cfg.enabled is True
        assert cfg.confirmation.mode == ConfirmationMode.DEFAULT

    def test_strict_profile_writes_strict_mode_and_enabled(self) -> None:
        cfg = _build_cfg_from_profile("strict")
        assert cfg.profile.current == "strict"
        assert cfg.enabled is True
        assert cfg.confirmation.mode == ConfirmationMode.STRICT

    def test_off_profile_disables_globally(self) -> None:
        cfg = _build_cfg_from_profile("off")
        assert cfg.profile.current == "off"
        assert cfg.enabled is False, (
            "off profile must disable security globally (enabled=False); "
            "this is what makes engine.preflight short-circuit to ALLOW "
            "regardless of confirmation.mode."
        )


# ---------------------------------------------------------------------------
# E2E: profile → engine.evaluate_tool_call
# ---------------------------------------------------------------------------


class TestTrustProfileE2E:
    """Trust profile ⇒ engine decides ALLOW for everything except safety net."""

    def test_trust_write_inside_workspace_allows(self, tmp_path: Path) -> None:
        cfg = _build_cfg_from_profile("trust")
        engine = PolicyEngineV2(config=cfg)
        ctx = _ctx_from_cfg(cfg, tmp_path)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": str(tmp_path / "x.txt")}),
            ctx,
        )
        assert decision.action == DecisionAction.ALLOW

    def test_trust_write_outside_workspace_still_allows(self, tmp_path: Path) -> None:
        """**Stage 1/2 invariant**: trust profile must not block out-of-workspace
        writes — the whole point of trust is "let the LLM pick paths freely".
        """
        cfg = _build_cfg_from_profile("trust")
        engine = PolicyEngineV2(config=cfg)
        ctx = _ctx_from_cfg(cfg, tmp_path)
        far_away = tmp_path.parent / "_completely_outside" / "x.txt"
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": str(far_away)}),
            ctx,
        )
        assert decision.action == DecisionAction.ALLOW, (
            f"trust profile must allow out-of-workspace writes, got {decision.action.value}; "
            f"reason={decision.reason!r}"
        )

    def test_trust_read_any_allows(self, tmp_path: Path) -> None:
        cfg = _build_cfg_from_profile("trust")
        engine = PolicyEngineV2(config=cfg)
        ctx = _ctx_from_cfg(cfg, tmp_path)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="read_file", params={"path": "/anywhere/x"}),
            ctx,
        )
        assert decision.action == DecisionAction.ALLOW

    def test_trust_delete_still_confirms_safety_net(self, tmp_path: Path) -> None:
        """DESTRUCTIVE never silently allowed — matrix safety net (existing C2 invariant)."""
        cfg = _build_cfg_from_profile("trust")
        engine = PolicyEngineV2(config=cfg)
        ctx = _ctx_from_cfg(cfg, tmp_path)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="delete_file", params={"path": str(tmp_path / "x")}),
            ctx,
        )
        assert decision.action == DecisionAction.CONFIRM, (
            "trust profile must still confirm DESTRUCTIVE operations — "
            "this is the matrix safety net that survives even maximum trust."
        )


class TestOffProfileE2E:
    """Off profile ⇒ security disabled globally, all evaluations short-circuit."""

    def test_off_write_anywhere_allows(self, tmp_path: Path) -> None:
        cfg = _build_cfg_from_profile("off")
        engine = PolicyEngineV2(config=cfg)
        ctx = _ctx_from_cfg(cfg, tmp_path)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": "/anywhere/x"}),
            ctx,
        )
        assert decision.action == DecisionAction.ALLOW

    def test_off_delete_allows_no_safety_net(self, tmp_path: Path) -> None:
        """off profile is the operator's explicit 'I know what I'm doing' switch
        and bypasses even the destructive safety net — that's its purpose."""
        cfg = _build_cfg_from_profile("off")
        engine = PolicyEngineV2(config=cfg)
        ctx = _ctx_from_cfg(cfg, tmp_path)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="delete_file", params={"path": "/etc/passwd"}),
            ctx,
        )
        assert decision.action == DecisionAction.ALLOW, (
            "off profile must short-circuit to ALLOW even for DESTRUCTIVE; "
            "it is the global 'security disabled' switch."
        )


class TestProtectProfileE2E:
    """Protect profile ⇒ default-mode behaviour (the recommended default)."""

    def test_protect_read_inside_allows(self, tmp_path: Path) -> None:
        cfg = _build_cfg_from_profile("protect")
        engine = PolicyEngineV2(config=cfg)
        ctx = _ctx_from_cfg(cfg, tmp_path)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="read_file", params={"path": str(tmp_path / "x")}),
            ctx,
        )
        assert decision.action == DecisionAction.ALLOW

    def test_protect_write_inside_workspace_confirms(self, tmp_path: Path) -> None:
        """**Protect profile design intent**: ``MUTATING_SCOPED × DEFAULT (AGENT) = CONFIRM``.

        Even inside the workspace, write_file requires user confirmation —
        that is precisely what makes "protect" the recommended default:
        the AI is kept in the box AND must ask before mutating anything.
        """
        cfg = _build_cfg_from_profile("protect")
        engine = PolicyEngineV2(config=cfg)
        ctx = _ctx_from_cfg(cfg, tmp_path)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": str(tmp_path / "x.txt")}),
            ctx,
        )
        assert decision.action == DecisionAction.CONFIRM

    def test_protect_write_outside_workspace_confirms(self, tmp_path: Path) -> None:
        """**Stage 1 contract**: protect profile must still gate out-of-workspace
        writes (the whole point of "protect" is to keep the AI inside the box).

        write_file outside → classifier upgrades to MUTATING_GLOBAL
        → MUTATING_GLOBAL × DEFAULT (AGENT matrix) = CONFIRM.
        """
        cfg = _build_cfg_from_profile("protect")
        engine = PolicyEngineV2(config=cfg)
        ctx = _ctx_from_cfg(cfg, tmp_path)
        outside = tmp_path.parent / "_completely_outside" / "x.txt"
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": str(outside)}),
            ctx,
        )
        assert decision.action == DecisionAction.CONFIRM, (
            "protect profile must gate out-of-workspace writes; "
            f"got {decision.action.value} (reason={decision.reason!r})."
        )


class TestStrictProfileE2E:
    """Strict profile ⇒ stricter matrix behaviour."""

    def test_strict_write_inside_workspace_confirms(self, tmp_path: Path) -> None:
        """MUTATING_SCOPED × STRICT (AGENT matrix) = CONFIRM."""
        cfg = _build_cfg_from_profile("strict")
        engine = PolicyEngineV2(config=cfg)
        ctx = _ctx_from_cfg(cfg, tmp_path)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="write_file", params={"path": str(tmp_path / "x.txt")}),
            ctx,
        )
        assert decision.action == DecisionAction.CONFIRM

    def test_strict_delete_denies(self, tmp_path: Path) -> None:
        """DESTRUCTIVE × STRICT = DENY (hard cliff)."""
        cfg = _build_cfg_from_profile("strict")
        engine = PolicyEngineV2(config=cfg)
        ctx = _ctx_from_cfg(cfg, tmp_path)
        decision = engine.evaluate_tool_call(
            ToolCallEvent(tool="delete_file", params={"path": str(tmp_path / "x")}),
            ctx,
        )
        assert decision.action == DecisionAction.DENY


# ---------------------------------------------------------------------------
# Parametric round-trip: every preset must produce a valid cfg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["trust", "protect", "strict", "off"])
def test_every_preset_produces_valid_cfg(profile: str) -> None:
    """Smoke: every preset must yield a pydantic-valid PolicyConfigV2.

    Guards against future ``_apply_security_profile_defaults`` edits that
    set a field to an invalid value.
    """
    cfg = _build_cfg_from_profile(profile)
    assert isinstance(cfg, PolicyConfigV2)
    assert cfg.profile.current == profile

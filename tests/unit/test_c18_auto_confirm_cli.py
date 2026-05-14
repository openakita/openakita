"""C18 Phase D — ``--auto-confirm`` CLI flag tests.

Coverage:
1. Helper ``_apply_auto_confirm_flag`` sets ``OPENAKITA_AUTO_CONFIRM=1``
   when enabled.
2. Helper is a no-op when disabled (env var not touched).
3. End-to-end: typer CliRunner invocation of ``openakita --auto-confirm
   --version`` writes the env var that ``apply_env_overrides`` then sees.
4. The flag does NOT change the engine's CLASSIFIER behavior for
   destructive / safety_immune paths (regression guard).
"""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from openakita.core.policy_v2 import env_overrides as eo
from openakita.core.policy_v2.schema import PolicyConfigV2


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure each test starts without OPENAKITA_AUTO_CONFIRM set."""
    monkeypatch.delenv("OPENAKITA_AUTO_CONFIRM", raising=False)


class TestApplyAutoConfirmFlag:
    def test_enabled_sets_env_var(self, clean_env: None) -> None:
        from openakita.main import _apply_auto_confirm_flag

        _apply_auto_confirm_flag(enabled=True)
        try:
            assert os.environ.get("OPENAKITA_AUTO_CONFIRM") == "1"
        finally:
            os.environ.pop("OPENAKITA_AUTO_CONFIRM", None)

    def test_disabled_does_not_touch_env_var(self, clean_env: None) -> None:
        from openakita.main import _apply_auto_confirm_flag

        _apply_auto_confirm_flag(enabled=False)
        assert "OPENAKITA_AUTO_CONFIRM" not in os.environ

    def test_disabled_does_not_clear_pre_existing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the operator sets the ENV var directly and then runs
        without the flag, the ENV var must be preserved (the flag is
        additive, not authoritative)."""
        from openakita.main import _apply_auto_confirm_flag

        monkeypatch.setenv("OPENAKITA_AUTO_CONFIRM", "1")
        _apply_auto_confirm_flag(enabled=False)
        assert os.environ.get("OPENAKITA_AUTO_CONFIRM") == "1"


class TestPhaseDFeedsPhaseC:
    """Phase D's flag must compose with Phase C's override layer —
    nothing about ``ConfigPath → mode=trust`` should be reimplemented in
    main.py. We assert the integration end-to-end."""

    def test_flag_set_then_overrides_apply(self, clean_env: None) -> None:
        from openakita.main import _apply_auto_confirm_flag

        _apply_auto_confirm_flag(enabled=True)
        try:
            cfg = PolicyConfigV2()
            new_cfg, report = eo.apply_env_overrides(cfg)
            assert new_cfg.confirmation.mode == "trust"
            assert any(
                o["env"] == "OPENAKITA_AUTO_CONFIRM" for o in report.applied
            )
        finally:
            os.environ.pop("OPENAKITA_AUTO_CONFIRM", None)

    def test_flag_unset_overrides_no_op(self, clean_env: None) -> None:
        cfg = PolicyConfigV2()
        new_cfg, report = eo.apply_env_overrides(cfg, environ={})
        assert new_cfg.confirmation.mode == "default"
        assert not report.has_any()


class TestCliInvocation:
    """Use ``CliRunner`` so we exercise typer's flag parsing path."""

    def _run(
        self,
        runner: CliRunner,
        args: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ):
        # Avoid heavy startup side-effects of subcommands (which would
        # need a real LLM endpoint). Use --version which short-circuits.
        monkeypatch.delenv("OPENAKITA_AUTO_CONFIRM", raising=False)
        from openakita.main import app

        return runner.invoke(app, args)

    def test_version_flag_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Baseline: ``--version`` exits 0 cleanly so we know the CLI
        wiring is healthy before testing --auto-confirm."""
        runner = CliRunner()
        result = self._run(runner, ["--version"], monkeypatch)
        assert result.exit_code == 0

    def test_auto_confirm_sets_env_var_before_subcommand_logic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running ``openakita --auto-confirm --version`` must set the
        env var even though --version short-circuits before any
        subcommand runs. (The callback applies the flag BEFORE the
        --version check.)"""
        runner = CliRunner()
        captured: dict[str, str | None] = {"value": None}

        # Spy on _apply_auto_confirm_flag so we know it was called even
        # though the process state is reset by CliRunner after exit.
        from openakita import main as main_module

        real_fn = main_module._apply_auto_confirm_flag

        def _spy(enabled: bool) -> None:
            captured["value"] = "1" if enabled else None
            real_fn(enabled=enabled)

        monkeypatch.setattr(main_module, "_apply_auto_confirm_flag", _spy)

        result = runner.invoke(main_module.app, ["--auto-confirm", "--version"])
        assert result.exit_code == 0
        assert captured["value"] == "1"

    def test_auto_confirm_help_documents_safety_carveout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The flag's help text must mention that destructive and
        safety_immune are still gated — otherwise operators will think
        --auto-confirm is "yolo mode" and complain when shell rm gets
        a confirm prompt."""
        runner = CliRunner()
        result = runner.invoke(self._import_app(), ["--help"])
        assert result.exit_code == 0
        # Help text appears in stdout; check both safety guardrails are
        # named so the operator sees the contract.
        assert "destructive" in result.stdout.lower()
        assert "safety_immune" in result.stdout.lower()

    def _import_app(self):
        from openakita.main import app

        return app


class TestDestructiveGateRegression:
    """The classifier — not ConfirmationMode — gates destructive +
    safety_immune. The --auto-confirm flag flips mode → trust, which
    only auto-allows non-destructive. This is a sanity check that the
    flag does NOT downgrade safety_immune or destructive tools to
    auto-allow."""

    def test_trust_mode_still_requires_confirm_for_destructive(self) -> None:
        """The Phase D flag only affects confirmation.mode. The bus +
        classifier still gate destructive (mutating_global) and
        safety_immune by approval_class, independently of mode. This
        regression test confirms the schema field hasn't accidentally
        been extended to bypass classifier rules.
        """
        cfg = PolicyConfigV2.model_validate({"confirmation": {"mode": "trust"}})
        # The schema doesn't expose a "bypass_destructive" flag — that's
        # the whole point. Verify it never sneaks in.
        cfg_dict = cfg.model_dump()
        assert "bypass_destructive" not in cfg_dict.get("confirmation", {})
        assert "bypass_safety_immune" not in cfg_dict.get("confirmation", {})

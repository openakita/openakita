"""C18 Phase C — POLICIES.yaml ENV variable override tests.

Coverage:
1. Coerce helpers reject bad input loudly.
2. ``apply_env_overrides`` returns same identity when no ENV is set.
3. Each registered ENV var actually patches the right cfg field.
4. Invalid value → ``skipped_errors`` entry, no patch.
5. Post-override validation failure → original cfg + error in report.
6. ``OPENAKITA_POLICY_FILE`` precedes ``settings.identity_path`` in
   ``_resolve_yaml_path``.
7. End-to-end ``load_policies_yaml`` plumbs override report onto
   ``MigrationReport.env_overrides``.
8. ``_audit_env_overrides`` writes audit rows on apply / on errors.
9. ENV overrides re-applied on hot-reload (each ``load_policies_yaml``
   re-reads environ, not cached at process start).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openakita.core.policy_v2 import env_overrides as eo
from openakita.core.policy_v2 import global_engine
from openakita.core.policy_v2.loader import load_policies_yaml
from openakita.core.policy_v2.schema import PolicyConfigV2

# ---------------------------------------------------------------------------
# Coerce helpers
# ---------------------------------------------------------------------------


class TestCoerceHelpers:
    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "Y", "ENABLE"])
    def test_coerce_bool_true(self, raw: str) -> None:
        assert eo._coerce_bool(raw) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "DISABLED"])
    def test_coerce_bool_false(self, raw: str) -> None:
        assert eo._coerce_bool(raw) is False

    @pytest.mark.parametrize("raw", ["", "maybe", "yes-please", "junk"])
    def test_coerce_bool_rejects_ambiguous(self, raw: str) -> None:
        with pytest.raises(ValueError):
            eo._coerce_bool(raw)

    def test_coerce_unattended_accepts_valid(self) -> None:
        assert eo._coerce_unattended_strategy("ask_owner") == "ask_owner"
        assert eo._coerce_unattended_strategy("  deny  ") == "deny"

    def test_coerce_unattended_rejects_unknown(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            eo._coerce_unattended_strategy("yolo_mode")
        msg = str(excinfo.value)
        # Error message must enumerate valid choices for the operator.
        assert "ask_owner" in msg
        assert "deny" in msg

    def test_coerce_str_path_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            eo._coerce_str_path("   ")


# ---------------------------------------------------------------------------
# apply_env_overrides
# ---------------------------------------------------------------------------


class TestApplyOverrides:
    def test_no_env_returns_same_identity(self) -> None:
        cfg = PolicyConfigV2()
        new_cfg, report = eo.apply_env_overrides(cfg, environ={})
        assert new_cfg is cfg, "no-op invocation must preserve identity"
        assert not report.has_any()

    def test_hot_reload_enable(self) -> None:
        cfg = PolicyConfigV2()
        assert cfg.hot_reload.enabled is False
        new_cfg, report = eo.apply_env_overrides(
            cfg, environ={"OPENAKITA_POLICY_HOT_RELOAD": "true"}
        )
        assert new_cfg.hot_reload.enabled is True
        assert len(report.applied) == 1
        applied = report.applied[0]
        assert applied["env"] == "OPENAKITA_POLICY_HOT_RELOAD"
        assert applied["path"] == "hot_reload.enabled"
        assert applied["value"] is True
        assert applied["redacted"] is False

    def test_auto_confirm_flips_mode_to_trust(self) -> None:
        cfg = PolicyConfigV2()
        new_cfg, report = eo.apply_env_overrides(
            cfg, environ={"OPENAKITA_AUTO_CONFIRM": "1"}
        )
        # "trust" is the v2 enum value for auto-allow (v1: "yolo").
        assert new_cfg.confirmation.mode == "trust"
        assert len(report.applied) == 1
        assert report.applied[0]["path"] == "confirmation.mode"

    def test_auto_confirm_false_resets_to_default(self) -> None:
        cfg = PolicyConfigV2.model_validate({"confirmation": {"mode": "trust"}})
        new_cfg, report = eo.apply_env_overrides(
            cfg, environ={"OPENAKITA_AUTO_CONFIRM": "false"}
        )
        assert new_cfg.confirmation.mode == "default"

    def test_unattended_strategy(self) -> None:
        cfg = PolicyConfigV2()
        new_cfg, report = eo.apply_env_overrides(
            cfg, environ={"OPENAKITA_UNATTENDED_STRATEGY": "deny"}
        )
        assert new_cfg.unattended.default_strategy == "deny"

    def test_audit_log_path(self, tmp_path: Path) -> None:
        cfg = PolicyConfigV2()
        custom = str(tmp_path / "audit.jsonl")
        new_cfg, report = eo.apply_env_overrides(
            cfg, environ={"OPENAKITA_AUDIT_LOG_PATH": custom}
        )
        # Note schema._validate_safe_path normalizes; only assert it isn't
        # the default.
        assert new_cfg.audit.log_path != cfg.audit.log_path

    def test_multiple_overrides_compose(self) -> None:
        cfg = PolicyConfigV2()
        new_cfg, report = eo.apply_env_overrides(
            cfg,
            environ={
                "OPENAKITA_POLICY_HOT_RELOAD": "1",
                "OPENAKITA_AUTO_CONFIRM": "1",
            },
        )
        assert new_cfg.hot_reload.enabled is True
        assert new_cfg.confirmation.mode == "trust"
        applied_envs = {e["env"] for e in report.applied}
        assert "OPENAKITA_POLICY_HOT_RELOAD" in applied_envs
        assert "OPENAKITA_AUTO_CONFIRM" in applied_envs


class TestInvalidValues:
    def test_invalid_bool_logged_not_applied(self) -> None:
        cfg = PolicyConfigV2()
        new_cfg, report = eo.apply_env_overrides(
            cfg, environ={"OPENAKITA_POLICY_HOT_RELOAD": "yes-please"}
        )
        # YAML default preserved.
        assert new_cfg.hot_reload.enabled is False
        assert len(report.applied) == 0
        assert len(report.skipped_errors) == 1
        err = report.skipped_errors[0]
        assert err["env"] == "OPENAKITA_POLICY_HOT_RELOAD"
        assert "boolean" in err["error"].lower()

    def test_invalid_unattended_logged_not_applied(self) -> None:
        cfg = PolicyConfigV2()
        new_cfg, report = eo.apply_env_overrides(
            cfg, environ={"OPENAKITA_UNATTENDED_STRATEGY": "yolo_mode"}
        )
        assert new_cfg.unattended.default_strategy == "ask_owner"
        assert len(report.skipped_errors) == 1

    def test_post_validation_failure_falls_back(self, tmp_path: Path) -> None:
        """When the patched dict somehow fails Pydantic validation,
        return the ORIGINAL cfg + log an aggregate error."""
        # Use the schema's `_validate_safe_path` to force a failure:
        # an empty string passes _coerce_str_path's non-empty check but
        # gets caught by the safe-path validator on the audit field.
        # Inject by mocking the coerce to bypass our own check.
        cfg = PolicyConfigV2()

        # Patch the registry temporarily with a coerce that returns
        # something the schema will reject.
        bad_spec = eo.OverrideSpec(
            env_name="OPENAKITA_AUDIT_LOG_PATH",
            cfg_path="audit.log_path",
            coerce=lambda raw: "../../etc/passwd",
        )
        original = eo._REGISTRY
        try:
            eo._REGISTRY = (bad_spec,)
            new_cfg, report = eo.apply_env_overrides(
                cfg, environ={"OPENAKITA_AUDIT_LOG_PATH": "anything"}
            )
        finally:
            eo._REGISTRY = original

        # Validation failure path: cfg unchanged + skipped_errors logs.
        assert new_cfg is cfg or new_cfg.audit.log_path == cfg.audit.log_path
        assert any(e["env"] == "<validation>" for e in report.skipped_errors)


# ---------------------------------------------------------------------------
# Integration: load_policies_yaml + _resolve_yaml_path + audit
# ---------------------------------------------------------------------------


class TestResolveYamlPath:
    def test_env_var_takes_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom = tmp_path / "custom.yaml"
        custom.write_text("security: {}\n", encoding="utf-8")
        monkeypatch.setenv("OPENAKITA_POLICY_FILE", str(custom))
        assert global_engine._resolve_yaml_path() == custom

    def test_env_var_empty_string_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAKITA_POLICY_FILE", "   ")
        # Falls through to settings/fallback; just verify it didn't return
        # the empty path.
        got = global_engine._resolve_yaml_path()
        assert got is None or str(got) != "" and str(got).strip()


class TestLoadIntegration:
    def test_load_attaches_env_override_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml = tmp_path / "POLICIES.yaml"
        yaml.write_text("security: {}\n", encoding="utf-8")
        monkeypatch.setenv("OPENAKITA_POLICY_HOT_RELOAD", "1")

        cfg, report = load_policies_yaml(yaml)

        assert cfg.hot_reload.enabled is True
        assert report.env_overrides is not None
        assert len(report.env_overrides.applied) == 1
        assert report.env_overrides.applied[0]["env"] == "OPENAKITA_POLICY_HOT_RELOAD"

    def test_load_with_apply_env_false_skips_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml = tmp_path / "POLICIES.yaml"
        yaml.write_text("security: {}\n", encoding="utf-8")
        monkeypatch.setenv("OPENAKITA_POLICY_HOT_RELOAD", "1")

        cfg, report = load_policies_yaml(yaml, apply_env=False)

        assert cfg.hot_reload.enabled is False  # YAML default kept
        assert report.env_overrides is None  # Layer didn't run

    def test_load_no_envs_attaches_empty_report(self, tmp_path: Path) -> None:
        yaml = tmp_path / "POLICIES.yaml"
        yaml.write_text("security: {}\n", encoding="utf-8")
        # Avoid host pollution: pass an empty environ.
        cfg, report = load_policies_yaml(yaml, environ={})
        assert report.env_overrides is not None
        assert not report.env_overrides.has_any()


# ---------------------------------------------------------------------------
# Audit row emission
# ---------------------------------------------------------------------------


class TestAuditEmission:
    def test_apply_writes_audit_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # C18 二轮 audit (BUG-C2) 修复后，``_audit_env_overrides`` 用
        # cfg.audit.log_path 直接构造 ephemeral logger（绕开单例避免
        # 死锁）。所以我们通过 YAML 指定 audit 路径来捕获 audit 行，
        # 比之前 monkey-patch get_audit_logger 更真实地反映生产路径。
        audit_path = tmp_path / "audit.jsonl"
        yaml = tmp_path / "POLICIES.yaml"
        yaml.write_text(
            "security:\n"
            "  audit:\n"
            f"    log_path: '{audit_path.as_posix()}'\n"
            "    include_chain: false\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("OPENAKITA_POLICY_HOT_RELOAD", "1")

        global_engine.reset_engine_v2(clear_explicit_lookup=True)
        global_engine._clear_last_known_good()
        global_engine.rebuild_engine_v2(yaml_path=yaml)

        assert audit_path.exists()
        rows = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").strip().splitlines()
            if line.strip()
        ]
        applied_rows = [
            r for r in rows if r.get("decision") == "env_override_applied"
        ]
        assert applied_rows, "audit chain must contain env_override_applied row"
        assert applied_rows[-1]["policy"] == "policy_env_override"

    def test_skipped_errors_write_separate_audit_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audit_path = tmp_path / "audit.jsonl"
        yaml = tmp_path / "POLICIES.yaml"
        yaml.write_text(
            "security:\n"
            "  audit:\n"
            f"    log_path: '{audit_path.as_posix()}'\n"
            "    include_chain: false\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("OPENAKITA_POLICY_HOT_RELOAD", "totally-broken-value")

        global_engine.reset_engine_v2(clear_explicit_lookup=True)
        global_engine._clear_last_known_good()
        global_engine.rebuild_engine_v2(yaml_path=yaml)

        rows = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").strip().splitlines()
            if line.strip()
        ]
        invalid_rows = [
            r for r in rows if r.get("decision") == "env_override_invalid"
        ]
        assert invalid_rows, (
            "audit chain must contain env_override_invalid row when ENV value "
            "fails coerce"
        )


# ---------------------------------------------------------------------------
# Hot-reload re-reads ENV
# ---------------------------------------------------------------------------


class TestHotReloadReadsEnv:
    def test_each_load_picks_up_current_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml = tmp_path / "POLICIES.yaml"
        yaml.write_text("security: {}\n", encoding="utf-8")

        # First load with ENV off.
        monkeypatch.delenv("OPENAKITA_POLICY_HOT_RELOAD", raising=False)
        cfg1, _ = load_policies_yaml(yaml)
        assert cfg1.hot_reload.enabled is False

        # Operator flips the var; subsequent load applies it.
        monkeypatch.setenv("OPENAKITA_POLICY_HOT_RELOAD", "1")
        cfg2, _ = load_policies_yaml(yaml)
        assert cfg2.hot_reload.enabled is True

        # Flips back.
        monkeypatch.delenv("OPENAKITA_POLICY_HOT_RELOAD")
        cfg3, _ = load_policies_yaml(yaml)
        assert cfg3.hot_reload.enabled is False


# ---------------------------------------------------------------------------
# Documentation: list_override_envs stable + reachable
# ---------------------------------------------------------------------------


def test_list_override_envs_returns_all_5() -> None:
    """If you add a new ENV var, also extend the audit script
    + docs/configuration.md (Phase F). This test fails loudly when the
    registry size drifts so the maintainer doesn't forget."""
    envs = eo.list_override_envs()
    # The OPENAKITA_POLICY_FILE one is handled in _resolve_yaml_path,
    # not the override layer — so the registry has 4 entries currently.
    # Bumping this assert IS the prompt to update docs.
    assert len(envs) == 4
    assert "OPENAKITA_POLICY_HOT_RELOAD" in envs
    assert "OPENAKITA_AUTO_CONFIRM" in envs
    assert "OPENAKITA_UNATTENDED_STRATEGY" in envs
    assert "OPENAKITA_AUDIT_LOG_PATH" in envs

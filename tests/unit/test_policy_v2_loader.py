"""C4 unit tests: PolicyConfigV2 schema + loader (yaml + Pydantic + deep-merge).

Covered:
- Pydantic v2 strict validation (extra='forbid', enum coercion, range checks)
- deep-merge: user partial config preserved, missing fields filled with defaults
- expand_placeholders: ${CWD} / ~ expansion
- load_policies_yaml: file not found / parse error / strict mode error
- load_policies_from_dict: in-memory pipeline (migration + merge + validate)
- end-to-end: load real-shaped v1 yaml → fully populated v2 PolicyConfigV2
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.core.policy_v2 import (
    ApprovalClass,
    ConfirmationMode,
    PolicyConfigError,
    PolicyConfigV2,
    SessionRole,
    load_policies_from_dict,
    load_policies_yaml,
)

# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestSchemaDefaults:
    def test_defaults_construct_clean(self) -> None:
        cfg = PolicyConfigV2()
        assert cfg.enabled is True
        assert cfg.workspace.paths == ["${CWD}"]
        assert cfg.confirmation.mode == ConfirmationMode.DEFAULT.value
        assert cfg.session_role.default == SessionRole.AGENT.value
        assert cfg.safety_immune.paths == []
        assert cfg.shell_risk.enabled is True
        assert "regedit" in cfg.shell_risk.blocked_commands

    def test_extra_fields_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            PolicyConfigV2.model_validate({"enabled": True, "typo_field": 1})

    def test_extra_field_in_subblock_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PolicyConfigV2.model_validate({"confirmation": {"mode": "trust", "typo": 1}})

    def test_invalid_mode_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PolicyConfigV2.model_validate({"confirmation": {"mode": "bogus"}})

    def test_invalid_session_role_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PolicyConfigV2.model_validate({"session_role": {"default": "bogus"}})

    def test_negative_timeout_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PolicyConfigV2.model_validate({"confirmation": {"timeout_seconds": -1}})

    def test_too_large_timeout_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PolicyConfigV2.model_validate({"confirmation": {"timeout_seconds": 999999}})

    def test_approval_class_override_validates(self) -> None:
        cfg = PolicyConfigV2.model_validate(
            {
                "approval_classes": {
                    "overrides": {
                        "my_tool": ApprovalClass.READONLY_GLOBAL,
                        "danger_tool": "destructive",
                    }
                }
            }
        )
        assert cfg.approval_classes.overrides["my_tool"] == "readonly_global"
        assert cfg.approval_classes.overrides["danger_tool"] == "destructive"

    def test_invalid_approval_class_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PolicyConfigV2.model_validate({"approval_classes": {"overrides": {"x": "bogus_class"}}})

    def test_invalid_unattended_strategy_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PolicyConfigV2.model_validate({"unattended": {"default_strategy": "bogus"}})

    def test_workspace_string_coerced(self) -> None:
        cfg = PolicyConfigV2.model_validate({"workspace": {"paths": "/single"}})
        assert cfg.workspace.paths == ["/single"]


class TestExpandPlaceholders:
    def test_cwd_expanded(self, tmp_path: Path) -> None:
        cfg = PolicyConfigV2(
            workspace=PolicyConfigV2().workspace.model_copy(
                update={"paths": ["${CWD}", "/literal"]}
            )
        )
        expanded = cfg.expand_placeholders(cwd=tmp_path)
        # ${CWD} → tmp_path
        assert str(tmp_path).replace("\\", "/") in expanded.workspace.paths
        assert "/literal" in expanded.workspace.paths

    def test_tilde_expanded_in_safety_immune(self, tmp_path: Path) -> None:
        from openakita.core.policy_v2 import SafetyImmuneConfig

        cfg = PolicyConfigV2(safety_immune=SafetyImmuneConfig(paths=["~/.ssh/**", "/etc/shadow"]))
        expanded = cfg.expand_placeholders(cwd=tmp_path)
        assert any(".ssh" in p and not p.startswith("~") for p in expanded.safety_immune.paths)
        assert "/etc/shadow" in expanded.safety_immune.paths


# ---------------------------------------------------------------------------
# Loader: deep-merge + migration + validation
# ---------------------------------------------------------------------------


class TestLoaderDeepMerge:
    def test_user_partial_merges_with_defaults(self, tmp_path: Path) -> None:
        cfg, _ = load_policies_from_dict({"security": {"confirmation": {"mode": "trust"}}})
        # User-set
        assert cfg.confirmation.mode == "trust"
        # Default-fill
        assert cfg.workspace.paths == [str(Path.cwd()).replace("\\", "/")]
        assert cfg.shell_risk.enabled is True
        assert "regedit" in cfg.shell_risk.blocked_commands

    def test_list_fields_replace_not_union(self) -> None:
        """list 字段是整体替换（用户配 blocked_commands 是想精准覆盖）。"""
        cfg, _ = load_policies_from_dict(
            {"security": {"shell_risk": {"blocked_commands": ["only_one"]}}}
        )
        assert cfg.shell_risk.blocked_commands == ["only_one"]
        # 但 ``enabled`` 等其他字段仍走默认
        assert cfg.shell_risk.enabled is True


class TestLoaderFile:
    def test_nonexistent_file_returns_defaults(self, tmp_path: Path) -> None:
        cfg, report = load_policies_yaml(tmp_path / "nope.yaml")
        assert isinstance(cfg, PolicyConfigV2)
        assert cfg.confirmation.mode == "default"
        assert report.schema_detected == "empty"

    def test_none_path_returns_defaults(self) -> None:
        cfg, _ = load_policies_yaml(None)
        assert isinstance(cfg, PolicyConfigV2)

    def test_v1_file_loads_as_v2(self, tmp_path: Path) -> None:
        p = tmp_path / "v1.yaml"
        p.write_text(
            """
security:
  enabled: true
  zones:
    workspace: ["${CWD}"]
    protected: ["/etc/**"]
    forbidden: ["~/.ssh/**"]
  confirmation:
    mode: yolo
            """,
            encoding="utf-8",
        )
        cfg, report = load_policies_yaml(p)
        assert report.schema_detected == "v1"
        assert cfg.confirmation.mode == "trust"
        assert "/etc/**" in cfg.safety_immune.paths
        assert any(".ssh" in p for p in cfg.safety_immune.paths)

    def test_real_existing_policies_yaml(self, tmp_path: Path) -> None:
        """Smoke test against the actual ``identity/POLICIES.yaml`` if present."""
        real = Path("identity/POLICIES.yaml")
        if not real.exists():
            pytest.skip("identity/POLICIES.yaml not present in this checkout")

        cfg, report = load_policies_yaml(real)
        assert isinstance(cfg, PolicyConfigV2)
        # Real file is v1 schema (auto_confirm/yolo mode + zones.* etc.)
        assert report.schema_detected == "v1"
        assert report.has_changes()

    def test_invalid_yaml_returns_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("not: valid: yaml: : :", encoding="utf-8")
        cfg, _ = load_policies_yaml(p)
        # Falls back to defaults, doesn't crash
        assert isinstance(cfg, PolicyConfigV2)

    def test_non_dict_top_level_returns_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n", encoding="utf-8")
        cfg, _ = load_policies_yaml(p)
        assert isinstance(cfg, PolicyConfigV2)

    def test_strict_mode_raises_on_typo(self, tmp_path: Path) -> None:
        p = tmp_path / "typo.yaml"
        p.write_text(
            "security:\n  confirmation:\n    typo_field: 1\n",
            encoding="utf-8",
        )
        with pytest.raises(PolicyConfigError):
            load_policies_yaml(p, strict=True)

    def test_lenient_mode_falls_back_on_typo(self, tmp_path: Path) -> None:
        p = tmp_path / "typo.yaml"
        p.write_text(
            "security:\n  confirmation:\n    typo_field: 1\n",
            encoding="utf-8",
        )
        cfg, _ = load_policies_yaml(p, strict=False)
        # falls back to defaults
        assert cfg.confirmation.mode == "default"

    def test_strict_mode_raises_on_invalid_mode(self, tmp_path: Path) -> None:
        p = tmp_path / "bad_mode.yaml"
        p.write_text(
            "security:\n  confirmation:\n    mode: bogus_mode\n",
            encoding="utf-8",
        )
        with pytest.raises(PolicyConfigError) as exc_info:
            load_policies_yaml(p, strict=True)
        assert "validation failed" in str(exc_info.value)


class TestLoaderEndToEnd:
    def test_v1_yolo_with_paths_full_pipeline(self, tmp_path: Path) -> None:
        p = tmp_path / "complete_v1.yaml"
        p.write_text(
            """
security:
  enabled: true
  zones:
    enabled: true
    workspace: ["${CWD}"]
    controlled: []
    protected:
      - C:/Windows/**
      - /etc/**
    forbidden:
      - ~/.ssh/**
    default_zone: workspace
  confirmation:
    enabled: true
    mode: yolo
    timeout_seconds: 60
    confirm_ttl: 120.0
  command_patterns:
    enabled: true
    blocked_commands: ["custom_blocked"]
  self_protection:
    enabled: true
    protected_dirs: ["data/", "identity/"]
    audit_to_file: true
    audit_path: data/audit/log.jsonl
    death_switch_threshold: 5
  sandbox:
    enabled: false
    backend: auto
    network:
      allow_in_sandbox: false
      allowed_domains: []
            """,
            encoding="utf-8",
        )
        cfg, report = load_policies_yaml(p, cwd=tmp_path)
        # Confirmation
        assert cfg.confirmation.mode == "trust"
        assert cfg.confirmation.timeout_seconds == 60
        # Workspace ${CWD} expanded
        assert str(tmp_path).replace("\\", "/") in cfg.workspace.paths
        # safety_immune union
        immune = cfg.safety_immune.paths
        assert "C:/Windows/**" in immune
        assert "/etc/**" in immune
        assert "data/" in immune
        assert "identity/" in immune
        assert any(".ssh" in p for p in immune)  # ~/.ssh expanded
        # shell_risk migrated
        assert cfg.shell_risk.blocked_commands == ["custom_blocked"]
        # audit migrated
        assert cfg.audit.enabled is True
        assert cfg.audit.log_path == "data/audit/log.jsonl"
        # death_switch migrated
        assert cfg.death_switch.threshold == 5
        # sandbox.network flattened
        assert cfg.sandbox.network_allow_in_sandbox is False
        assert cfg.sandbox.network_allowed_domains == []
        # Migration report
        assert report.schema_detected == "v1"
        assert len(report.fields_migrated) >= 7
        assert any("controlled" in d or "default_zone" in d for d in report.fields_dropped)


class TestRoundTrip:
    """从 dict → v2 → dict → 再 load，结果应等价。"""

    def test_pure_v2_roundtrip_idempotent(self) -> None:
        original = {
            "security": {
                "enabled": True,
                "confirmation": {"mode": "default"},
                "safety_immune": {"paths": ["/etc/shadow"]},
            }
        }
        cfg1, report1 = load_policies_from_dict(original)
        # Re-export to dict, re-load
        roundtrip = {"security": cfg1.model_dump()}
        cfg2, report2 = load_policies_from_dict(roundtrip)
        assert report2.schema_detected == "v2"
        assert report2.fields_migrated == []
        assert cfg2.confirmation.mode == cfg1.confirmation.mode
        assert cfg2.safety_immune.paths == cfg1.safety_immune.paths

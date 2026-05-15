"""C4 unit tests: v1 → v2 POLICIES.yaml migration (pure functions).

Covered:
- detect_schema_version: empty/v1/v2/mixed
- migrate_v1_to_v2: 10 mapping rules + dedupe + conflict reporting
- mode aliases (yolo/smart/cautious + auto_confirm)
- safety_immune union (zones.protected ∪ zones.forbidden ∪ self_protection.protected_dirs)
- v2 fields preserved through migration
- mixed schema: v2 wins, v1 reported as conflict
"""

from __future__ import annotations

import pytest

from openakita.core.policy_v2 import (
    detect_schema_version,
    migrate_v1_to_v2,
)

# ---------------------------------------------------------------------------
# detect_schema_version
# ---------------------------------------------------------------------------


class TestDetect:
    def test_none_is_empty(self) -> None:
        assert detect_schema_version(None) == "empty"

    def test_empty_dict_is_empty(self) -> None:
        assert detect_schema_version({}) == "empty"

    def test_no_security_key_is_empty(self) -> None:
        assert detect_schema_version({"other": {}}) == "empty"

    def test_security_with_zones_is_v1(self) -> None:
        assert detect_schema_version({"security": {"zones": {}}}) == "v1"

    def test_security_with_command_patterns_is_v1(self) -> None:
        assert detect_schema_version({"security": {"command_patterns": {"enabled": False}}}) == "v1"

    def test_security_with_self_protection_is_v1(self) -> None:
        assert detect_schema_version({"security": {"self_protection": {}}}) == "v1"

    def test_security_with_safety_immune_is_v2(self) -> None:
        assert detect_schema_version({"security": {"safety_immune": {}}}) == "v2"

    def test_security_with_shell_risk_is_v2(self) -> None:
        assert detect_schema_version({"security": {"shell_risk": {}}}) == "v2"

    def test_mixed_schema_detected(self) -> None:
        assert detect_schema_version({"security": {"zones": {}, "safety_immune": {}}}) == "mixed"

    def test_only_confirmation_v1_mode_detected(self) -> None:
        assert detect_schema_version({"security": {"confirmation": {"mode": "yolo"}}}) == "v1"

    def test_only_confirmation_v2_mode_detected(self) -> None:
        assert detect_schema_version({"security": {"confirmation": {"mode": "trust"}}}) == "v2"

    def test_only_enabled_field_defaults_to_v2(self) -> None:
        """极简配置（仅 enabled）当 v2 处理（无任何废弃字段）。"""
        assert detect_schema_version({"security": {"enabled": True}}) == "v2"


# ---------------------------------------------------------------------------
# migrate_v1_to_v2: zones → workspace + safety_immune
# ---------------------------------------------------------------------------


class TestZonesMigration:
    def test_workspace_migrated(self) -> None:
        v1 = {"security": {"zones": {"workspace": ["${CWD}", "/extra"]}}}
        v2, report = migrate_v1_to_v2(v1)
        assert v2["security"]["workspace"]["paths"] == ["${CWD}", "/extra"]
        assert any("workspace" in m for m in report.fields_migrated)

    def test_workspace_string_coerced_to_list(self) -> None:
        v1 = {"security": {"zones": {"workspace": "/single/path"}}}
        v2, _ = migrate_v1_to_v2(v1)
        assert v2["security"]["workspace"]["paths"] == ["/single/path"]

    def test_workspace_empty_falls_to_cwd_default(self) -> None:
        v1 = {"security": {"zones": {"workspace": []}}}
        v2, _ = migrate_v1_to_v2(v1)
        assert v2["security"]["workspace"]["paths"] == ["${CWD}"]

    def test_protected_and_forbidden_unioned(self) -> None:
        v1 = {
            "security": {
                "zones": {
                    "protected": ["/etc/**", "C:/Windows/**"],
                    "forbidden": ["~/.ssh/**", "/etc/shadow"],
                }
            }
        }
        v2, report = migrate_v1_to_v2(v1)
        immune = v2["security"]["safety_immune"]["paths"]
        assert immune == [
            "/etc/**",
            "C:/Windows/**",
            "~/.ssh/**",
            "/etc/shadow",
        ]
        assert any("protected" in m for m in report.fields_migrated)

    def test_dedupe_in_union(self) -> None:
        v1 = {
            "security": {
                "zones": {
                    "protected": ["/etc/**"],
                    "forbidden": ["/etc/**", "~/.ssh"],
                }
            }
        }
        v2, _ = migrate_v1_to_v2(v1)
        immune = v2["security"]["safety_immune"]["paths"]
        assert immune == ["/etc/**", "~/.ssh"]  # dedup preserves order

    def test_controlled_dropped_with_warn(self) -> None:
        v1 = {"security": {"zones": {"controlled": ["/foo/**"]}}}
        v2, report = migrate_v1_to_v2(v1)
        assert "controlled" not in v2["security"].get("safety_immune", {}).get("paths", [])
        assert any("controlled" in d for d in report.fields_dropped)

    def test_default_zone_dropped(self) -> None:
        v1 = {"security": {"zones": {"default_zone": "workspace"}}}
        _, report = migrate_v1_to_v2(v1)
        assert any("default_zone" in d for d in report.fields_dropped)


class TestSelfProtectionMigration:
    def test_protected_dirs_merge_into_safety_immune(self) -> None:
        v1 = {
            "security": {
                "zones": {"protected": ["/etc/**"]},
                "self_protection": {"protected_dirs": ["data/", "identity/"]},
            }
        }
        v2, _ = migrate_v1_to_v2(v1)
        immune = v2["security"]["safety_immune"]["paths"]
        assert "/etc/**" in immune
        assert "data/" in immune
        assert "identity/" in immune

    def test_audit_split_to_audit_block(self) -> None:
        v1 = {
            "security": {
                "self_protection": {
                    "audit_to_file": True,
                    "audit_path": "/var/log/policy.jsonl",
                }
            }
        }
        v2, _ = migrate_v1_to_v2(v1)
        assert v2["security"]["audit"]["enabled"] is True
        assert v2["security"]["audit"]["log_path"] == "/var/log/policy.jsonl"

    def test_death_switch_split_to_block(self) -> None:
        v1 = {
            "security": {
                "self_protection": {
                    "death_switch_threshold": 5,
                    "death_switch_total_multiplier": 4,
                }
            }
        }
        v2, _ = migrate_v1_to_v2(v1)
        assert v2["security"]["death_switch"]["threshold"] == 5
        assert v2["security"]["death_switch"]["total_multiplier"] == 4


# ---------------------------------------------------------------------------
# command_patterns → shell_risk
# ---------------------------------------------------------------------------


class TestCommandPatternsMigration:
    def test_renamed_block(self) -> None:
        v1 = {
            "security": {
                "command_patterns": {
                    "enabled": True,
                    "custom_critical": [r"format\s+[a-z]:"],
                    "custom_high": [r"rm\s+-rf"],
                    "excluded_patterns": [r"safe_cmd"],
                    "blocked_commands": ["evil_cmd"],
                }
            }
        }
        v2, _ = migrate_v1_to_v2(v1)
        sr = v2["security"]["shell_risk"]
        assert sr["enabled"] is True
        assert sr["custom_critical"] == [r"format\s+[a-z]:"]
        assert sr["custom_high"] == [r"rm\s+-rf"]
        assert sr["blocked_commands"] == ["evil_cmd"]


class TestSandboxNetworkFlatten:
    def test_network_block_flattened(self) -> None:
        v1 = {
            "security": {
                "sandbox": {
                    "enabled": True,
                    "network": {
                        "allow_in_sandbox": True,
                        "allowed_domains": ["api.example.com"],
                    },
                }
            }
        }
        v2, _ = migrate_v1_to_v2(v1)
        sb = v2["security"]["sandbox"]
        assert "network" not in sb
        assert sb["network_allow_in_sandbox"] is True
        assert sb["network_allowed_domains"] == ["api.example.com"]


# ---------------------------------------------------------------------------
# confirmation mode aliases
# ---------------------------------------------------------------------------


class TestConfirmationMigration:
    @pytest.mark.parametrize(
        "old, new",
        [
            ("yolo", "trust"),
            ("smart", "default"),
            ("cautious", "strict"),
        ],
    )
    def test_mode_aliases(self, old: str, new: str) -> None:
        v1 = {"security": {"confirmation": {"mode": old}}}
        v2, report = migrate_v1_to_v2(v1)
        assert v2["security"]["confirmation"]["mode"] == new
        assert any(old in m and new in m for m in report.fields_migrated)

    def test_auto_confirm_overrides_mode_to_trust(self) -> None:
        v1 = {"security": {"confirmation": {"mode": "smart", "auto_confirm": True}}}
        v2, report = migrate_v1_to_v2(v1)
        assert v2["security"]["confirmation"]["mode"] == "trust"
        assert any("auto_confirm" in m for m in report.fields_migrated)
        assert any("auto_confirm" in d for d in report.fields_dropped)

    def test_v2_mode_preserved(self) -> None:
        v1 = {"security": {"confirmation": {"mode": "default"}}}
        v2, _ = migrate_v1_to_v2(v1)
        assert v2["security"]["confirmation"]["mode"] == "default"

    def test_other_confirmation_fields_preserved(self) -> None:
        v1 = {
            "security": {
                "confirmation": {
                    "mode": "yolo",
                    "timeout_seconds": 30,
                    "default_on_timeout": "deny",
                    "confirm_ttl": 60.0,
                }
            }
        }
        v2, _ = migrate_v1_to_v2(v1)
        c = v2["security"]["confirmation"]
        assert c["mode"] == "trust"
        assert c["timeout_seconds"] == 30
        assert c["default_on_timeout"] == "deny"
        assert c["confirm_ttl"] == 60.0


# ---------------------------------------------------------------------------
# Mixed schema (v2 wins, v1 reported as conflict)
# ---------------------------------------------------------------------------


class TestMixedSchema:
    def test_workspace_v2_wins_over_zones_workspace(self) -> None:
        mixed = {
            "security": {
                "zones": {"workspace": ["/v1_path"]},
                "workspace": {"paths": ["/v2_path"]},
            }
        }
        v2, report = migrate_v1_to_v2(mixed)
        assert v2["security"]["workspace"]["paths"] == ["/v2_path"]
        assert any("workspace" in c for c in report.conflicts)

    def test_v2_safety_immune_unioned_with_v1_zones(self) -> None:
        """v2 safety_immune.paths 已存在时，v1 zones.protected 仍合入。

        语义：用户既写了 v2 又留了 v1 残留——保守 union 而非简单丢 v1。
        """
        mixed = {
            "security": {
                "zones": {"protected": ["/v1_protected"]},
                "safety_immune": {"paths": ["/v2_immune"]},
            }
        }
        v2, _ = migrate_v1_to_v2(mixed)
        immune = v2["security"]["safety_immune"]["paths"]
        assert "/v2_immune" in immune
        assert "/v1_protected" in immune


# ---------------------------------------------------------------------------
# Pure v2 (passthrough)
# ---------------------------------------------------------------------------


class TestV2Passthrough:
    def test_pure_v2_unchanged(self) -> None:
        v2_input = {
            "security": {
                "enabled": True,
                "workspace": {"paths": ["${CWD}"]},
                "confirmation": {"mode": "trust"},
                "safety_immune": {"paths": ["~/.ssh/**"]},
            }
        }
        v2, report = migrate_v1_to_v2(v2_input)
        assert report.schema_detected == "v2"
        assert report.fields_migrated == []
        assert report.fields_dropped == []
        # 字段值原样保留
        assert v2["security"]["workspace"]["paths"] == ["${CWD}"]
        assert v2["security"]["confirmation"]["mode"] == "trust"
        assert v2["security"]["safety_immune"]["paths"] == ["~/.ssh/**"]


class TestEmptyInput:
    def test_none_returns_minimal(self) -> None:
        v2, report = migrate_v1_to_v2(None)
        assert v2 == {"security": {}}
        assert report.schema_detected == "empty"

    def test_empty_dict_returns_minimal(self) -> None:
        v2, report = migrate_v1_to_v2({})
        assert v2 == {"security": {}}


class TestHardening:
    """C4 review fixes: regression guards for edge cases."""

    def test_safety_immune_paths_null_does_not_crash(self) -> None:
        """``safety_immune: {paths: null}`` + v1 zones.protected → 不崩溃。

        regression: 修复前 ``list(None) → TypeError``。
        """
        v1 = {
            "security": {
                "safety_immune": {"paths": None},
                "zones": {"protected": ["/etc/**"]},
            }
        }
        v2, _ = migrate_v1_to_v2(v1)
        assert "/etc/**" in v2["security"]["safety_immune"]["paths"]

    def test_safety_immune_block_null_does_not_crash(self) -> None:
        v1 = {
            "security": {
                "safety_immune": None,
                "zones": {"protected": ["/etc/**"]},
            }
        }
        v2, _ = migrate_v1_to_v2(v1)
        assert "/etc/**" in v2["security"]["safety_immune"]["paths"]

    def test_command_patterns_and_shell_risk_mixed_v2_wins(self) -> None:
        """同时有 v1 ``command_patterns`` 和 v2 ``shell_risk`` —— v2 胜出，v1 报冲突。"""
        mixed = {
            "security": {
                "command_patterns": {"blocked_commands": ["v1_only"]},
                "shell_risk": {"blocked_commands": ["v2_only"]},
            }
        }
        v2, report = migrate_v1_to_v2(mixed)
        assert v2["security"]["shell_risk"]["blocked_commands"] == ["v2_only"]
        assert any("command_patterns" in c for c in report.conflicts)

    def test_v2_blocks_derived_from_schema_fields(self) -> None:
        """守门测试：v2_blocks 应等于 PolicyConfigV2.model_fields - {'enabled'}。

        防止未来在 schema 新增字段后忘了更新 migration 拷贝列表。
        """
        from openakita.core.policy_v2 import PolicyConfigV2
        from openakita.core.policy_v2.migration import _V2_BLOCKS

        expected = set(PolicyConfigV2.model_fields) - {"enabled"}
        assert expected == _V2_BLOCKS

    def test_input_dict_not_mutated(self) -> None:
        """v1 dict 不应被 migrate_v1_to_v2 修改（纯函数契约）。"""
        import copy

        original = {
            "security": {
                "zones": {"protected": ["/etc/**"], "forbidden": ["~/.ssh/**"]},
                "self_protection": {"enabled": True, "protected_dirs": ["data/"]},
                "confirmation": {"mode": "yolo", "auto_confirm": True},
            }
        }
        snapshot = copy.deepcopy(original)
        migrate_v1_to_v2(original)
        assert original == snapshot, "input dict was mutated"

    def test_legacy_mode_aliases_single_source_of_truth(self) -> None:
        """``LEGACY_MODE_ALIASES`` 应统一在 enums.py 中定义。"""
        from openakita.core.policy_v2 import context as ctx
        from openakita.core.policy_v2.enums import LEGACY_MODE_ALIASES
        from openakita.core.policy_v2.migration import LEGACY_MODE_ALIASES as M_ALIAS

        assert ctx.LEGACY_MODE_ALIASES is LEGACY_MODE_ALIASES
        assert M_ALIAS is LEGACY_MODE_ALIASES


class TestSelfProtectionDisabledSemantics:
    """v1 ``self_protection.enabled = false`` 必须在 v2 保留停用语义。

    - 不把 ``protected_dirs`` 升级为 ``safety_immune`` 路径
    - 把 ``death_switch.enabled`` 显式置 false
    - 文件已在生产 ``identity/POLICIES.yaml`` 中使用 enabled=false，覆盖必要
    """

    def test_disabled_skips_protected_dirs_migration(self) -> None:
        v1 = {
            "security": {
                "self_protection": {
                    "enabled": False,
                    "protected_dirs": ["data/", "identity/"],
                }
            }
        }
        v2, report = migrate_v1_to_v2(v1)
        # protected_dirs 不应该出现在 safety_immune
        immune = v2["security"].get("safety_immune", {}).get("paths", [])
        assert "data/" not in immune
        assert "identity/" not in immune
        # 应该报到 fields_dropped 让用户知情
        assert any(
            "protected_dirs" in d and ("跳过" in d or "skip" in d.lower())
            for d in report.fields_dropped
        )

    def test_disabled_propagates_to_death_switch(self) -> None:
        v1 = {"security": {"self_protection": {"enabled": False}}}
        v2, report = migrate_v1_to_v2(v1)
        # death_switch.enabled 必须显式置 false
        assert v2["security"]["death_switch"]["enabled"] is False
        # 报到 fields_migrated（不是 dropped——是有效的语义传播）
        assert any(
            "self_protection.enabled=false" in m and "death_switch.enabled=false" in m
            for m in report.fields_migrated
        )

    def test_enabled_true_does_not_force_death_switch(self) -> None:
        """enabled=true（v1 默认）不应该强行覆盖 death_switch.enabled。"""
        v1 = {
            "security": {
                "self_protection": {
                    "enabled": True,
                    "death_switch_threshold": 5,
                }
            }
        }
        v2, _ = migrate_v1_to_v2(v1)
        # death_switch 块存在（因为 threshold 迁移），但 enabled 不被设置
        ds = v2["security"]["death_switch"]
        assert ds["threshold"] == 5
        assert "enabled" not in ds  # 留给 schema 默认 True

    def test_disabled_still_migrates_audit(self) -> None:
        """``self_protection.enabled = false`` 不应影响 audit 迁移
        （v1 中 audit 独立于 sp.enabled，由 ``audit_to_file`` 控制）。
        """
        v1 = {
            "security": {
                "self_protection": {
                    "enabled": False,
                    "audit_to_file": True,
                    "audit_path": "/var/log/x.jsonl",
                }
            }
        }
        v2, _ = migrate_v1_to_v2(v1)
        assert v2["security"]["audit"]["enabled"] is True
        assert v2["security"]["audit"]["log_path"] == "/var/log/x.jsonl"

    def test_real_production_yaml_no_silent_re_enable(self) -> None:
        """复现 ``identity/POLICIES.yaml`` 真实场景：
        enabled=false + protected_dirs + audit_to_file=true + death_switch_threshold=3
        必须：safety_immune 不含 protected_dirs；death_switch.enabled=false；audit ok。
        """
        v1 = {
            "security": {
                "zones": {"protected": ["/etc/**"]},  # 这些走正常路径
                "self_protection": {
                    "enabled": False,
                    "protected_dirs": ["data/", "identity/", "logs/", "src/"],
                    "audit_to_file": True,
                    "audit_path": "data/audit/policy_decisions.jsonl",
                    "death_switch_threshold": 3,
                    "death_switch_total_multiplier": 3,
                },
            }
        }
        v2, _ = migrate_v1_to_v2(v1)
        immune = v2["security"]["safety_immune"]["paths"]
        # zones.protected 仍然合入（不受 sp_disabled 影响）
        assert "/etc/**" in immune
        # 但 protected_dirs 不应该被合入
        for d in ("data/", "identity/", "logs/", "src/"):
            assert d not in immune, f"{d} should not be in safety_immune (sp.enabled=false)"
        # death_switch 显式停用
        assert v2["security"]["death_switch"]["enabled"] is False
        # 但阈值仍然迁移（schema 允许 enabled=false 同时保留 threshold 配置）
        assert v2["security"]["death_switch"]["threshold"] == 3
        # audit 正常迁移
        assert v2["security"]["audit"]["enabled"] is True


class TestEnabledProfileCanonicalization:
    """security.enabled ↔ profile.current=off 单一总开关归一化（cleanup phase）。"""

    def test_enabled_false_promotes_to_profile_off(self) -> None:
        v1 = {"security": {"enabled": False}}
        v2, report = migrate_v1_to_v2(v1)
        assert v2["security"]["enabled"] is False
        assert v2["security"]["profile"]["current"] == "off"
        assert any("profile.current=off" in f for f in report.fields_migrated)

    def test_profile_off_forces_enabled_false(self) -> None:
        """profile.current=off but no explicit enabled → enabled coerced to false."""
        v2_in = {"security": {"profile": {"current": "off", "base": "protect"}}}
        v2, _ = migrate_v1_to_v2(v2_in)
        assert v2["security"]["enabled"] is False
        assert v2["security"]["profile"]["current"] == "off"

    def test_enabled_true_with_protect_unchanged(self) -> None:
        v2_in = {
            "security": {
                "enabled": True,
                "profile": {"current": "protect", "base": None},
            }
        }
        v2, _ = migrate_v1_to_v2(v2_in)
        assert v2["security"]["enabled"] is True
        assert v2["security"]["profile"]["current"] == "protect"

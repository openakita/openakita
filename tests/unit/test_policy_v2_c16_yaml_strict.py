"""C16 Phase B — POLICIES.yaml strict validation + last-known-good cache.

Covers:

- ``MigrationReport.unknown_security_keys`` is populated for typos /
  attacker-injected fields under ``security.*``.
- Strict[bool] on ``security.enabled`` rejects stringy ``"yes"`` / ``"no"`` /
  ``"true"`` and integer ``1`` / ``0`` (no silent coercion).
- ``shell_risk.custom_*`` rejects malformed regex at load.
- Pattern length / list-length caps.
- ``audit.log_path`` rejects ``..`` traversal; ``workspace.paths`` allows
  it (operators may reference sibling projects).
- ``_LAST_KNOWN_GOOD`` cache: validation error after a good load keeps
  the previous config; first-load failure falls back to defaults.
- ``reset_policy_v2_layer`` clears LKG (test isolation invariant).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from openakita.core.policy_v2 import global_engine as ge
from openakita.core.policy_v2.loader import load_policies_from_dict
from openakita.core.policy_v2.migration import migrate_v1_to_v2
from openakita.core.policy_v2.schema import (
    AuditConfig,
    PolicyConfigV2,
    ShellRiskConfig,
    WorkspaceConfig,
)

# ---------------------------------------------------------------------------
# Unknown security keys
# ---------------------------------------------------------------------------


def test_unknown_security_key_recorded_in_migration_report():
    raw = {
        "security": {
            "safety_immune": {"paths": ["/etc"]},
            "totally_made_up_key": {"do_evil": True},
            "another_attacker_field": 42,
        }
    }
    _, report = migrate_v1_to_v2(raw)
    assert "security.totally_made_up_key" in report.unknown_security_keys
    assert "security.another_attacker_field" in report.unknown_security_keys


def test_known_v1_and_v2_keys_not_flagged_as_unknown():
    raw = {
        "security": {
            "enabled": True,
            "zones": {"workspace": "/x"},
            "command_patterns": {"enabled": True},
            "self_protection": {"audit_to_file": True},
            "safety_immune": {"paths": []},
            "audit": {"enabled": True},
        }
    }
    _, report = migrate_v1_to_v2(raw)
    assert report.unknown_security_keys == []


# ---------------------------------------------------------------------------
# Strict[bool] — no silent coercion
# ---------------------------------------------------------------------------


def test_enabled_string_yes_rejected():
    with pytest.raises(ValidationError):
        PolicyConfigV2.model_validate({"enabled": "yes"})


def test_enabled_string_no_rejected():
    """The original bug: bool('no') is True in Python — silently turning
    a *disable* intent into *enable*. Strict[bool] must catch this."""
    with pytest.raises(ValidationError):
        PolicyConfigV2.model_validate({"enabled": "no"})


def test_enabled_integer_rejected():
    with pytest.raises(ValidationError):
        PolicyConfigV2.model_validate({"enabled": 1})


def test_enabled_proper_bool_accepted():
    cfg = PolicyConfigV2.model_validate({"enabled": False})
    assert cfg.enabled is False


def test_audit_include_chain_string_rejected():
    with pytest.raises(ValidationError):
        AuditConfig.model_validate({"include_chain": "true"})


def test_migration_does_not_bool_cast_enabled():
    """Regression for the silent ``bool(src_sec['enabled'])`` cast that
    used to turn ``"no"`` into ``True``. After C16 we want the raw value
    to flow through so Strict[bool] sees and rejects it.
    """
    out, _ = migrate_v1_to_v2({"security": {"enabled": "no"}})
    assert out["security"]["enabled"] == "no"  # raw passthrough


# ---------------------------------------------------------------------------
# shell_risk regex validation
# ---------------------------------------------------------------------------


def test_malformed_regex_rejected():
    with pytest.raises(ValidationError) as exc:
        ShellRiskConfig.model_validate({"custom_critical": ["[unclosed"]})
    assert "valid regex" in str(exc.value)


def test_regex_length_cap_enforced():
    long_pat = "a" * 250
    with pytest.raises(ValidationError):
        ShellRiskConfig.model_validate({"custom_high": [long_pat]})


def test_regex_list_count_cap_enforced():
    big_list = [f"pat{i}" for i in range(65)]
    with pytest.raises(ValidationError):
        ShellRiskConfig.model_validate({"custom_high": big_list})


def test_valid_regex_accepted():
    cfg = ShellRiskConfig.model_validate(
        {"custom_critical": [r"rm\s+-rf", r"dd\s+if="]}
    )
    assert len(cfg.custom_critical) == 2


# ---------------------------------------------------------------------------
# Path validators — scoped
# ---------------------------------------------------------------------------


def test_audit_log_path_rejects_traversal():
    with pytest.raises(ValidationError):
        AuditConfig.model_validate({"log_path": "../../etc/passwd"})


def test_audit_log_path_rejects_empty():
    with pytest.raises(ValidationError):
        AuditConfig.model_validate({"log_path": ""})


def test_workspace_paths_allow_parent_dir():
    """Operators may legitimately reference parent / sibling directories
    in workspace.paths — do not reject those.
    """
    cfg = WorkspaceConfig.model_validate({"paths": ["../shared", "/abs/path"]})
    assert "../shared" in cfg.paths


def test_workspace_path_length_capped():
    with pytest.raises(ValidationError):
        WorkspaceConfig.model_validate({"paths": ["a" * 5000]})


# ---------------------------------------------------------------------------
# Last-known-good cache
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_global_engine():
    ge.reset_engine_v2(clear_explicit_lookup=False)
    ge._clear_last_known_good()
    yield
    ge.reset_engine_v2(clear_explicit_lookup=False)
    ge._clear_last_known_good()


def test_last_known_good_set_on_successful_load():
    """A successful load should populate the LKG cache."""
    cfg, _ = load_policies_from_dict({"security": {"enabled": True}})
    ge._set_last_known_good(cfg)
    assert ge._get_last_known_good() is cfg


def test_recover_from_load_failure_uses_lkg_when_present():
    good = PolicyConfigV2.model_validate({"safety_immune": {"paths": ["/critical"]}})
    ge._set_last_known_good(good)

    recovered = ge._recover_from_load_failure(
        ValueError("simulated validation error"),
        source="POLICIES.yaml",
    )
    # LKG returned verbatim — user's safety_immune path is preserved.
    assert recovered is good
    assert "/critical" in recovered.safety_immune.paths


def test_recover_from_load_failure_falls_back_to_defaults_on_first_load():
    """No LKG → fall back to PolicyConfigV2() defaults + WARN.

    This preserves the pre-C16 behaviour for fresh installs with a typo
    in POLICIES.yaml — operators are not locked out at first run.
    """
    assert ge._get_last_known_good() is None
    recovered = ge._recover_from_load_failure(
        ValueError("simulated"), source="POLICIES.yaml"
    )
    # Default safety_immune paths is the schema default (empty list).
    assert recovered.safety_immune.paths == []


def test_lkg_concurrent_set_and_get_safe():
    import threading

    cfg_a = PolicyConfigV2.model_validate({"enabled": True})
    cfg_b = PolicyConfigV2.model_validate({"enabled": False})

    iterations = 200

    def _writer(cfg):
        for _ in range(iterations):
            ge._set_last_known_good(cfg)

    def _reader():
        for _ in range(iterations):
            v = ge._get_last_known_good()
            assert v in (cfg_a, cfg_b)

    threads = [
        threading.Thread(target=_writer, args=(cfg_a,)),
        threading.Thread(target=_writer, args=(cfg_b,)),
        threading.Thread(target=_reader),
        threading.Thread(target=_reader),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_reset_policy_v2_layer_clears_lkg():
    cfg = PolicyConfigV2.model_validate({"enabled": True})
    ge._set_last_known_good(cfg)
    assert ge._get_last_known_good() is cfg
    ge.reset_policy_v2_layer(scope="test")
    assert ge._get_last_known_good() is None


# ---------------------------------------------------------------------------
# End-to-end: rebuild_engine_v2 with bad YAML keeps LKG config
# ---------------------------------------------------------------------------


def test_rebuild_with_bad_yaml_keeps_lkg(tmp_path: Path):
    """Write a good YAML, load it, then corrupt it and rebuild — engine
    should keep using the good config, not silently degrade to defaults.
    """
    yaml_path = tmp_path / "POLICIES.yaml"
    yaml_path.write_text(
        "security:\n"
        "  enabled: true\n"
        "  safety_immune:\n"
        "    paths:\n"
        "      - /important/dir\n",
        encoding="utf-8",
    )
    ge.rebuild_engine_v2(yaml_path=yaml_path)
    cfg1 = ge.get_config_v2()
    assert "/important/dir" in cfg1.safety_immune.paths

    # Corrupt: introduce a Strict[bool] violation
    yaml_path.write_text(
        "security:\n  enabled: maybe\n",
        encoding="utf-8",
    )
    ge.rebuild_engine_v2(yaml_path=yaml_path)
    cfg2 = ge.get_config_v2()
    # LKG kicked in — safety_immune still has the path.
    assert "/important/dir" in cfg2.safety_immune.paths

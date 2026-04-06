"""Unit tests for the six-layer security system.

Tests cover:
- L1: Zone resolution + operation type matrix
- L3: Shell command risk classification + pattern matching
- L4: Checkpoint creation and rollback
- L5: Self-protection + death switch + audit logger
- Policy Engine YAML loading (new + legacy format)
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from openakita.core.policy import (
    OpType,
    PolicyDecision,
    PolicyEngine,
    RiskLevel,
    SecurityConfig,
    Zone,
    ZonePolicyConfig,
    CommandPatternConfig,
    SelfProtectionConfig,
    SandboxConfig,
    ConfirmationConfig,
    CheckpointConfig,
)
from openakita.core.checkpoint import CheckpointManager
from openakita.core.audit_logger import AuditLogger


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "controlled").mkdir()
    (tmp_path / "protected").mkdir()
    (tmp_path / "forbidden").mkdir()
    return tmp_path


@pytest.fixture
def engine(tmp_workspace):
    config = SecurityConfig(
        enabled=True,
        zones=ZonePolicyConfig(
            enabled=True,
            workspace=[str(tmp_workspace / "workspace")],
            controlled=[str(tmp_workspace / "controlled")],
            protected=[str(tmp_workspace / "protected")],
            forbidden=[str(tmp_workspace / "forbidden")],
        ),
        confirmation=ConfirmationConfig(auto_confirm=False),
        command_patterns=CommandPatternConfig(
            blocked_commands=["regedit", "bcdedit"],
        ),
        self_protection=SelfProtectionConfig(
            enabled=True,
            protected_dirs=["data/", "src/"],
            death_switch_threshold=3,
        ),
        sandbox=SandboxConfig(enabled=True),
    )
    return PolicyEngine(config)


# ---------------------------------------------------------------------------
# L1: Zone Resolution
# ---------------------------------------------------------------------------

class TestZoneResolution:
    def test_workspace_zone(self, engine, tmp_workspace):
        path = str(tmp_workspace / "workspace" / "file.txt")
        assert engine.resolve_zone(path) == Zone.WORKSPACE

    def test_controlled_zone(self, engine, tmp_workspace):
        path = str(tmp_workspace / "controlled" / "doc.txt")
        assert engine.resolve_zone(path) == Zone.CONTROLLED

    def test_protected_zone(self, engine, tmp_workspace):
        path = str(tmp_workspace / "protected" / "sys.dll")
        assert engine.resolve_zone(path) == Zone.PROTECTED

    def test_forbidden_zone(self, engine, tmp_workspace):
        path = str(tmp_workspace / "forbidden" / "key.pem")
        assert engine.resolve_zone(path) == Zone.FORBIDDEN

    def test_unmatched_defaults_to_protected(self, engine):
        assert engine.resolve_zone("/some/random/path") == Zone.PROTECTED


# ---------------------------------------------------------------------------
# L1: Zone × OpType Matrix
# ---------------------------------------------------------------------------

class TestZoneOpMatrix:
    def test_workspace_read_allowed(self, engine, tmp_workspace):
        path = str(tmp_workspace / "workspace" / "readme.md")
        result = engine.assert_tool_allowed("read_file", {"path": path})
        assert result.decision == PolicyDecision.ALLOW

    def test_workspace_delete_allowed(self, engine, tmp_workspace):
        path = str(tmp_workspace / "workspace" / "old.txt")
        result = engine.assert_tool_allowed("delete_file", {"path": path})
        assert result.decision == PolicyDecision.ALLOW

    def test_controlled_read_allowed(self, engine, tmp_workspace):
        path = str(tmp_workspace / "controlled" / "data.csv")
        result = engine.assert_tool_allowed("read_file", {"path": path})
        assert result.decision == PolicyDecision.ALLOW

    def test_controlled_create_allowed(self, engine, tmp_workspace):
        path = str(tmp_workspace / "controlled" / "new_file.txt")
        result = engine.assert_tool_allowed("write_file", {"path": path})
        assert result.decision == PolicyDecision.ALLOW

    def test_controlled_delete_needs_confirm(self, engine, tmp_workspace):
        path = str(tmp_workspace / "controlled" / "existing.txt")
        result = engine.assert_tool_allowed("delete_file", {"path": path})
        assert result.decision == PolicyDecision.CONFIRM

    def test_protected_read_allowed(self, engine, tmp_workspace):
        path = str(tmp_workspace / "protected" / "config.ini")
        result = engine.assert_tool_allowed("read_file", {"path": path})
        assert result.decision == PolicyDecision.ALLOW

    def test_protected_write_denied(self, engine, tmp_workspace):
        path = str(tmp_workspace / "protected" / "new.txt")
        result = engine.assert_tool_allowed("write_file", {"path": path})
        assert result.decision == PolicyDecision.DENY

    def test_protected_delete_denied(self, engine, tmp_workspace):
        path = str(tmp_workspace / "protected" / "file.sys")
        result = engine.assert_tool_allowed("delete_file", {"path": path})
        assert result.decision == PolicyDecision.DENY

    def test_forbidden_read_denied(self, engine, tmp_workspace):
        path = str(tmp_workspace / "forbidden" / "id_rsa")
        result = engine.assert_tool_allowed("read_file", {"path": path})
        assert result.decision == PolicyDecision.DENY

    def test_forbidden_grep_denied(self, engine, tmp_workspace):
        path = str(tmp_workspace / "forbidden")
        result = engine.assert_tool_allowed("grep", {"path": path})
        assert result.decision == PolicyDecision.DENY

    def test_forbidden_list_denied(self, engine, tmp_workspace):
        path = str(tmp_workspace / "forbidden")
        result = engine.assert_tool_allowed("list_directory", {"path": path})
        assert result.decision == PolicyDecision.DENY


# ---------------------------------------------------------------------------
# L3: Risk Classification
# ---------------------------------------------------------------------------

class TestRiskClassification:
    def test_critical_dd(self, engine):
        assert engine.classify_shell_risk("dd if=/dev/zero of=/dev/sda") == RiskLevel.CRITICAL

    def test_critical_format(self, engine):
        assert engine.classify_shell_risk("format C:") == RiskLevel.CRITICAL

    def test_critical_mkfs(self, engine):
        assert engine.classify_shell_risk("mkfs.ext4 /dev/sda1") == RiskLevel.CRITICAL

    def test_critical_rm_rf_root(self, engine):
        assert engine.classify_shell_risk("rm -rf / ") == RiskLevel.CRITICAL

    def test_high_rm_rf(self, engine):
        assert engine.classify_shell_risk("rm -rf /home/user/dir") == RiskLevel.HIGH

    def test_high_remove_item_recurse(self, engine):
        assert engine.classify_shell_risk("Remove-Item C:\\Temp -Recurse") == RiskLevel.HIGH

    def test_high_del_s(self, engine):
        assert engine.classify_shell_risk("del /S C:\\temp") == RiskLevel.HIGH

    def test_high_pip_uninstall(self, engine):
        assert engine.classify_shell_risk("pip uninstall requests") == RiskLevel.HIGH

    def test_low_ls(self, engine):
        assert engine.classify_shell_risk("ls -la") == RiskLevel.LOW

    def test_low_echo(self, engine):
        assert engine.classify_shell_risk("echo hello") == RiskLevel.LOW

    def test_low_python(self, engine):
        assert engine.classify_shell_risk("python script.py") == RiskLevel.LOW


# ---------------------------------------------------------------------------
# L3: Shell command blocking
# ---------------------------------------------------------------------------

class TestShellCommandBlocking:
    def test_blocked_command_denied(self, engine):
        result = engine.assert_tool_allowed("run_shell", {"command": "regedit"})
        assert result.decision == PolicyDecision.DENY

    def test_blocked_command_with_exe(self, engine):
        result = engine.assert_tool_allowed("run_shell", {"command": "bcdedit.exe /set"})
        assert result.decision == PolicyDecision.DENY

    def test_critical_command_denied(self, engine):
        result = engine.assert_tool_allowed("run_shell", {"command": "diskpart"})
        assert result.decision == PolicyDecision.DENY

    def test_high_command_needs_confirm(self, engine):
        result = engine.assert_tool_allowed("run_shell", {"command": "rm -rf /tmp/test"})
        assert result.decision == PolicyDecision.CONFIRM

    def test_normal_command_allowed(self, engine):
        result = engine.assert_tool_allowed("run_shell", {"command": "ls -la"})
        assert result.decision == PolicyDecision.ALLOW

    def test_excluded_pattern(self, engine):
        engine.config.command_patterns.excluded_patterns = [r"rm\s+-rf\s+"]
        result = engine.assert_tool_allowed("run_shell", {"command": "rm -rf /tmp/test"})
        assert result.decision == PolicyDecision.ALLOW


# ---------------------------------------------------------------------------
# L5: Death Switch
# ---------------------------------------------------------------------------

class TestDeathSwitch:
    def test_consecutive_denials_trigger_readonly(self, engine, tmp_workspace):
        assert not engine.readonly_mode

        for i in range(3):
            path = str(tmp_workspace / "protected" / f"file{i}.txt")
            engine.assert_tool_allowed("write_file", {"path": path})

        assert engine.readonly_mode

    def test_readonly_mode_blocks_writes(self, engine, tmp_workspace):
        engine._readonly_mode = True
        path = str(tmp_workspace / "workspace" / "ok.txt")
        result = engine.assert_tool_allowed("write_file", {"path": path})
        assert result.decision == PolicyDecision.DENY
        assert "只读模式" in result.reason

    def test_readonly_mode_allows_reads(self, engine, tmp_workspace):
        engine._readonly_mode = True
        path = str(tmp_workspace / "workspace" / "ok.txt")
        result = engine.assert_tool_allowed("read_file", {"path": path})
        assert result.decision == PolicyDecision.ALLOW

    def test_reset_readonly(self, engine, tmp_workspace):
        engine._readonly_mode = True
        engine.reset_readonly_mode()
        assert not engine.readonly_mode


# ---------------------------------------------------------------------------
# L5: Self-protection
# ---------------------------------------------------------------------------

class TestSelfProtection:
    def test_cannot_delete_self_protected_dir(self, engine):
        result = engine.assert_tool_allowed("delete_file", {"path": "data/agent.db"})
        assert result.decision == PolicyDecision.DENY
        assert "自保护" in result.reason

    def test_cannot_delete_src(self, engine):
        result = engine.assert_tool_allowed("delete_file", {"path": "src/openakita/core/policy.py"})
        assert result.decision == PolicyDecision.DENY

    def test_read_self_protected_allowed(self, engine):
        result = engine.assert_tool_allowed("read_file", {"path": "src/openakita/core/policy.py"})
        assert result.decision != PolicyDecision.DENY or "自保护" not in result.reason


# ---------------------------------------------------------------------------
# L4: Checkpoint
# ---------------------------------------------------------------------------

class TestCheckpoint:
    def test_create_and_rewind(self, tmp_path):
        snapshot_dir = str(tmp_path / "snapshots")
        mgr = CheckpointManager(snapshot_dir=snapshot_dir, max_snapshots=10)

        test_file = tmp_path / "target.txt"
        test_file.write_text("original content", encoding="utf-8")

        cp_id = mgr.create_checkpoint(
            file_paths=[str(test_file)],
            tool_name="edit_file",
            description="before edit",
        )
        assert cp_id is not None

        test_file.write_text("modified content", encoding="utf-8")
        assert test_file.read_text(encoding="utf-8") == "modified content"

        success = mgr.rewind_to_checkpoint(cp_id)
        assert success
        assert test_file.read_text(encoding="utf-8") == "original content"

    def test_list_checkpoints(self, tmp_path):
        snapshot_dir = str(tmp_path / "snapshots")
        mgr = CheckpointManager(snapshot_dir=snapshot_dir, max_snapshots=10)

        f = tmp_path / "file.txt"
        f.write_text("data", encoding="utf-8")

        mgr.create_checkpoint([str(f)], tool_name="t1")
        mgr.create_checkpoint([str(f)], tool_name="t2")

        items = mgr.list_checkpoints()
        assert len(items) == 2
        assert items[0]["tool_name"] == "t2"

    def test_max_snapshots_enforced(self, tmp_path):
        snapshot_dir = str(tmp_path / "snapshots")
        mgr = CheckpointManager(snapshot_dir=snapshot_dir, max_snapshots=3)

        f = tmp_path / "file.txt"
        f.write_text("data", encoding="utf-8")

        ids = []
        for i in range(5):
            cid = mgr.create_checkpoint([str(f)], tool_name=f"t{i}")
            ids.append(cid)

        items = mgr.list_checkpoints(limit=10)
        assert len(items) == 3

    def test_rewind_nonexistent_fails(self, tmp_path):
        mgr = CheckpointManager(snapshot_dir=str(tmp_path / "snap"))
        assert not mgr.rewind_to_checkpoint("nonexistent")

    def test_snapshot_for_new_file(self, tmp_path):
        mgr = CheckpointManager(snapshot_dir=str(tmp_path / "snap"))
        new_file = tmp_path / "new.txt"
        cp_id = mgr.create_checkpoint([str(new_file)], tool_name="write")
        assert cp_id is not None

        new_file.write_text("content", encoding="utf-8")
        success = mgr.rewind_to_checkpoint(cp_id)
        assert success
        assert not new_file.exists()


# ---------------------------------------------------------------------------
# L5: Audit Logger
# ---------------------------------------------------------------------------

class TestAuditLogger:
    def test_log_and_tail(self, tmp_path):
        log_path = str(tmp_path / "audit.jsonl")
        logger = AuditLogger(path=log_path)

        logger.log("write_file", "deny", "blocked path", policy="ZonePolicy")
        logger.log("run_shell", "confirm", "high risk", policy="RiskClassification")

        entries = logger.tail(10)
        assert len(entries) == 2
        assert entries[0]["tool"] == "write_file"
        assert entries[1]["decision"] == "confirm"

    def test_empty_log(self, tmp_path):
        log_path = str(tmp_path / "empty.jsonl")
        logger = AuditLogger(path=log_path)
        assert logger.tail() == []


# ---------------------------------------------------------------------------
# YAML Loading
# ---------------------------------------------------------------------------

class TestYAMLLoading:
    def test_load_new_format(self, tmp_path):
        yaml_path = tmp_path / "POLICIES.yaml"
        yaml_path.write_text("""
security:
  enabled: true
  zones:
    enabled: true
    workspace:
      - "${CWD}"
    controlled:
      - "D:/docs"
    forbidden:
      - "~/.ssh/**"
    default_zone: protected
  command_patterns:
    blocked_commands:
      - shutdown
  sandbox:
    enabled: false
    backend: docker
""", encoding="utf-8")

        engine = PolicyEngine()
        engine.load_from_yaml(yaml_path)

        assert engine.config.enabled is True
        assert "D:/docs" in engine.config.zones.controlled
        assert engine.config.sandbox.enabled is False
        assert engine.config.sandbox.backend == "docker"
        assert "shutdown" in engine.config.command_patterns.blocked_commands

    def test_load_legacy_format(self, tmp_path):
        yaml_path = tmp_path / "POLICIES.yaml"
        yaml_path.write_text("""
tool_policies:
  - tool_name: run_shell
    require_confirmation: false
scope_policy:
  blocked_paths:
    - "/etc/shadow"
  blocked_commands:
    - regedit
auto_confirm: false
""", encoding="utf-8")

        engine = PolicyEngine()
        engine.load_from_yaml(yaml_path)

        assert "regedit" in engine.config.command_patterns.blocked_commands

    def test_nonexistent_file(self, tmp_path):
        engine = PolicyEngine()
        engine.load_from_yaml(tmp_path / "missing.yaml")
        assert engine.config.enabled is True


# ---------------------------------------------------------------------------
# Security disabled
# ---------------------------------------------------------------------------

class TestSecurityDisabled:
    def test_all_allowed_when_disabled(self, tmp_workspace):
        config = SecurityConfig(enabled=False)
        engine = PolicyEngine(config)
        result = engine.assert_tool_allowed("delete_file", {"path": "C:/Windows/System32"})
        assert result.decision == PolicyDecision.ALLOW


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_confirm_result_has_zone_info(self, engine, tmp_workspace):
        path = str(tmp_workspace / "controlled" / "file.txt")
        result = engine.assert_tool_allowed("delete_file", {"path": path})
        assert result.decision == PolicyDecision.CONFIRM
        assert result.metadata.get("zone") == "controlled"
        assert result.metadata.get("op_type") == "delete"

    def test_high_risk_shell_has_risk_level(self, engine):
        result = engine.assert_tool_allowed("run_shell", {"command": "rm -rf /tmp/x"})
        assert result.decision == PolicyDecision.CONFIRM
        assert result.metadata.get("risk_level") == "high"
        assert result.metadata.get("needs_sandbox") is True

    def test_needs_checkpoint_for_controlled_edit(self, engine, tmp_workspace):
        test_file = tmp_workspace / "controlled" / "doc.txt"
        test_file.write_text("hello", encoding="utf-8")
        result = engine.assert_tool_allowed("edit_file", {"path": str(test_file)})
        assert result.decision == PolicyDecision.ALLOW
        assert result.metadata.get("needs_checkpoint") is True

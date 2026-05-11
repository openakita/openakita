"""Unit tests for the six-layer security system.

Tests cover:
- L1: Zone resolution + operation type matrix
- L3: Shell command risk classification + pattern matching
- L4: Checkpoint creation and rollback
- L5: Self-protection + death switch + audit logger
- Policy Engine YAML loading (new + legacy format)
"""

import pytest

from openakita.core.audit_logger import AuditLogger
from openakita.core.checkpoint import CheckpointManager
from openakita.core.policy import (
    CommandPatternConfig,
    ConfirmationConfig,
    PolicyDecision,
    PolicyEngine,
    RiskLevel,
    SandboxConfig,
    SecurityConfig,
    SelfProtectionConfig,
    UserAllowlistConfig,
    Zone,
    ZonePolicyConfig,
)

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

    def test_workspace_delete_requires_confirm(self, engine, tmp_workspace):
        path = str(tmp_workspace / "workspace" / "old.txt")
        result = engine.assert_tool_allowed("delete_file", {"path": path})
        assert result.decision == PolicyDecision.CONFIRM

    def test_controlled_read_allowed(self, engine, tmp_workspace):
        path = str(tmp_workspace / "controlled" / "data.csv")
        result = engine.assert_tool_allowed("read_file", {"path": path})
        assert result.decision == PolicyDecision.ALLOW

    def test_controlled_create_requires_confirm(self, engine, tmp_workspace):
        # P0-1：CONTROLLED 区域所有写操作（含 CREATE）必须 CONFIRM。
        # 旧行为是 ALLOW —— 已被复盘判定为放行越界写入，回归即重大隐患。
        path = str(tmp_workspace / "controlled" / "new_file.txt")
        result = engine.assert_tool_allowed("write_file", {"path": path})
        assert result.decision == PolicyDecision.CONFIRM

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

    def test_yolo_still_confirms_high_risk_shell(self, tmp_workspace):
        config = SecurityConfig(
            zones=ZonePolicyConfig(workspace=[str(tmp_workspace / "workspace")]),
            confirmation=ConfirmationConfig(mode="yolo", auto_confirm=True),
            command_patterns=CommandPatternConfig(enabled=True),
            self_protection=SelfProtectionConfig(enabled=True),
            sandbox=SandboxConfig(enabled=False),
        )
        trust_engine = PolicyEngine(config)
        result = trust_engine.assert_tool_allowed(
            "run_powershell",
            {"command": "Remove-Item .\\data\\skills.json -Force"},
        )
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
        # P0-1：CONTROLLED 区 EDIT 由 ALLOW 升级到 CONFIRM；
        # smart/cautious 模式下用户能看到拦截弹窗。
        # needs_checkpoint 仍由 metadata 报告，无论决策是 ALLOW/CONFIRM。
        test_file = tmp_workspace / "controlled" / "doc.txt"
        test_file.write_text("hello", encoding="utf-8")
        result = engine.assert_tool_allowed("edit_file", {"path": str(test_file)})
        assert result.decision == PolicyDecision.CONFIRM
        assert result.metadata.get("needs_checkpoint") is True


# ---------------------------------------------------------------------------
# Three-tier allowlists (TTL / session / persistent)
# ---------------------------------------------------------------------------

class TestAllowlists:
    def test_ttl_cache_allow_once(self, engine):
        """mark_confirmed with scope='once' creates a TTL entry that bypasses."""
        params = {"command": "npm install lodash"}
        engine.mark_confirmed("run_shell", params, scope="once")
        result = engine.assert_tool_allowed("run_shell", params)
        assert result.decision == PolicyDecision.ALLOW
        assert result.metadata.get("confirmed_bypass") is True

    def test_session_allowlist(self, engine):
        """mark_confirmed with scope='session' creates session-lived entry."""
        params = {"command": "npm install express"}
        engine.mark_confirmed("run_shell", params, scope="session")
        result = engine.assert_tool_allowed("run_shell", params)
        assert result.decision == PolicyDecision.ALLOW
        assert result.metadata.get("confirmed_bypass") is True

    def test_session_allowlist_cleared_on_cleanup(self, engine):
        """cleanup_session clears session allowlist."""
        params = {"command": "npm install express"}
        engine.mark_confirmed("run_shell", params, scope="session")
        engine.cleanup_session("sess-1")
        # Session allowlist is cleared, but TTL cache still has entry
        # so it still passes (both session and TTL written in mark_confirmed)
        result = engine.assert_tool_allowed("run_shell", params)
        assert result.decision == PolicyDecision.ALLOW  # TTL still alive

    def test_persistent_allowlist_command_pattern(self, engine):
        """Persistent command patterns should match via glob."""
        engine._config.user_allowlist = UserAllowlistConfig(
            commands=[{"pattern": "npm install*", "added_at": "", "needs_sandbox": False}],
            tools=[],
        )
        result = engine.assert_tool_allowed("run_shell", {"command": "npm install react"})
        assert result.decision == PolicyDecision.ALLOW
        assert result.metadata.get("confirmed_bypass") is True

    def test_persistent_allowlist_tool(self, engine):
        """Persistent tool allowlist should match by tool name."""
        engine._config.user_allowlist = UserAllowlistConfig(
            commands=[],
            tools=[{"name": "write_file", "zone": "workspace", "needs_sandbox": False}],
        )
        result = engine.assert_tool_allowed("write_file", {"path": "/some/path"})
        assert result.decision == PolicyDecision.ALLOW
        assert result.metadata.get("confirmed_bypass") is True

    def test_needs_sandbox_preserved_in_ttl(self, engine):
        """needs_sandbox flag should survive the confirmation cache."""
        params = {"command": "rm -rf /tmp/test"}
        engine.mark_confirmed("run_shell", params, scope="once", needs_sandbox=True)
        result = engine.assert_tool_allowed("run_shell", params)
        assert result.decision == PolicyDecision.ALLOW
        assert result.metadata.get("needs_sandbox") is True

    def test_needs_sandbox_preserved_in_session(self, engine):
        """needs_sandbox flag should survive the session allowlist."""
        params = {"command": "rm -rf /tmp/test"}
        engine.mark_confirmed("run_shell", params, scope="session", needs_sandbox=True)
        result = engine.assert_tool_allowed("run_shell", params)
        assert result.decision == PolicyDecision.ALLOW
        assert result.metadata.get("needs_sandbox") is True


# ---------------------------------------------------------------------------
# Confirmation mode switching
# ---------------------------------------------------------------------------

class TestConfirmationMode:
    def test_yolo_mode_still_confirms_high(self):
        config = SecurityConfig(
            enabled=True,
            confirmation=ConfirmationConfig(mode="yolo"),
            command_patterns=CommandPatternConfig(blocked_commands=[]),
        )
        engine = PolicyEngine(config)
        result = engine.assert_tool_allowed("run_shell", {"command": "rm -rf /tmp/x"})
        assert result.decision == PolicyDecision.CONFIRM

    def test_yolo_mode_allows_unknown_path_write(self, tmp_path):
        config = SecurityConfig(
            enabled=True,
            zones=ZonePolicyConfig(
                workspace=[str(tmp_path / "workspace")],
                protected=[str(tmp_path / "protected")],
                forbidden=[str(tmp_path / "forbidden")],
                default_zone=Zone.WORKSPACE,
            ),
            confirmation=ConfirmationConfig(mode="yolo"),
        )
        engine = PolicyEngine(config)
        result = engine.assert_tool_allowed("write_file", {"path": str(tmp_path / "other" / "a.txt")})
        assert result.decision == PolicyDecision.ALLOW
        assert result.metadata.get("trust_mode") is True

    def test_yolo_mode_denies_protected_write(self, tmp_path):
        protected = tmp_path / "protected"
        config = SecurityConfig(
            enabled=True,
            zones=ZonePolicyConfig(
                protected=[str(protected)],
                forbidden=[],
                default_zone=Zone.WORKSPACE,
            ),
            confirmation=ConfirmationConfig(mode="yolo"),
        )
        engine = PolicyEngine(config)
        result = engine.assert_tool_allowed("write_file", {"path": str(protected / "sys.txt")})
        assert result.decision == PolicyDecision.DENY
        assert result.policy_name == "BaselineProtection"

    def test_yolo_mode_denies_forbidden_read(self, tmp_path):
        forbidden = tmp_path / "secrets"
        config = SecurityConfig(
            enabled=True,
            zones=ZonePolicyConfig(
                protected=[],
                forbidden=[str(forbidden)],
                default_zone=Zone.WORKSPACE,
            ),
            confirmation=ConfirmationConfig(mode="yolo"),
        )
        engine = PolicyEngine(config)
        result = engine.assert_tool_allowed("read_file", {"path": str(forbidden / "id_rsa")})
        assert result.decision == PolicyDecision.DENY
        assert result.policy_name == "BaselineProtection"

    def test_yolo_mode_allows_ordinary_shell_execution(self):
        config = SecurityConfig(
            enabled=True,
            confirmation=ConfirmationConfig(mode="yolo"),
            command_patterns=CommandPatternConfig(blocked_commands=[]),
        )
        engine = PolicyEngine(config)
        result = engine.assert_tool_allowed("run_shell", {"command": "pip install requests"})
        assert result.decision == PolicyDecision.ALLOW
        assert result.metadata.get("trust_mode") is True
        assert result.metadata.get("baseline_checked") is True

    def test_cautious_mode_confirms_medium(self):
        config = SecurityConfig(
            enabled=True,
            confirmation=ConfirmationConfig(mode="cautious"),
            command_patterns=CommandPatternConfig(blocked_commands=[]),
        )
        engine = PolicyEngine(config)
        result = engine.assert_tool_allowed("run_shell", {"command": "pip install requests"})
        assert result.decision == PolicyDecision.CONFIRM

    def test_smart_mode_confirms_medium(self):
        config = SecurityConfig(
            enabled=True,
            confirmation=ConfirmationConfig(mode="smart"),
            command_patterns=CommandPatternConfig(blocked_commands=[]),
        )
        engine = PolicyEngine(config)
        result = engine.assert_tool_allowed("run_shell", {"command": "pip install requests"})
        assert result.decision == PolicyDecision.CONFIRM

    def test_critical_always_denied(self):
        """CRITICAL risk is denied regardless of mode."""
        config = SecurityConfig(
            enabled=True,
            confirmation=ConfirmationConfig(mode="yolo"),
            command_patterns=CommandPatternConfig(blocked_commands=[]),
        )
        engine = PolicyEngine(config)
        result = engine.assert_tool_allowed("run_shell", {"command": "dd if=/dev/zero of=/dev/sda"})
        assert result.decision == PolicyDecision.DENY


# ---------------------------------------------------------------------------
# resolve_ui_confirm with extended decisions
# ---------------------------------------------------------------------------

class TestResolveUIConfirm:
    def test_allow_once_scope(self, engine):
        engine.store_ui_pending("t1", "run_shell", {"command": "npm test"})
        found = engine.resolve_ui_confirm("t1", "allow_once")
        assert found is True
        # TTL cache should have entry
        result = engine.assert_tool_allowed("run_shell", {"command": "npm test"})
        assert result.decision == PolicyDecision.ALLOW

    def test_allow_session_scope(self, engine):
        engine.store_ui_pending("t2", "run_shell", {"command": "npm run build"})
        found = engine.resolve_ui_confirm("t2", "allow_session")
        assert found is True
        assert len(engine._session_allowlist) > 0

    def test_deny_does_not_cache(self, engine):
        engine.store_ui_pending("t3", "run_shell", {"command": "rm -rf /tmp"})
        found = engine.resolve_ui_confirm("t3", "deny")
        assert found is True
        assert len(engine._confirmed_cache) == 0

    def test_sandbox_preserves_needs_sandbox(self, engine):
        engine.store_ui_pending("t4", "run_shell", {"command": "wget evil"}, needs_sandbox=True)
        found = engine.resolve_ui_confirm("t4", "sandbox")
        assert found is True
        result = engine.assert_tool_allowed("run_shell", {"command": "wget evil"})
        assert result.decision == PolicyDecision.ALLOW
        assert result.metadata.get("needs_sandbox") is True

    def test_legacy_allow_maps_to_allow_once(self, engine):
        engine.store_ui_pending("t5", "run_shell", {"command": "echo hi"})
        found = engine.resolve_ui_confirm("t5", "allow")
        assert found is True

    def test_nonexistent_returns_false(self, engine):
        assert engine.resolve_ui_confirm("nonexistent", "allow_once") is False

    def test_resolve_ui_confirm_not_shadowed_by_duplicate_def(self, engine):
        """Regression guard: policy.py 曾因 backport 合并引入同名 ``resolve_ui_confirm``
        的简化版本，覆盖掉真正做 ``pop`` + ``mark_confirmed`` 的老实现，
        导致 IM 卡片确认后 allow_session / allow_always / sandbox 全部
        失效（只 set event 不写 allowlist）。这里从静态和运行时双向守卫。

        - 静态：PolicyEngine 类字典里只能有一个 ``resolve_ui_confirm``；
          Python 类里后定义的同名方法会覆盖前面的，一旦再次有人复制
          粘贴同名 def，这里不会命中——但 test_allow_once_scope 等
          会因返回 None 而 FAIL，作为第二道护栏。
        - 运行时：调用必须返回 ``bool``（True/False），而不是 None。
        """
        # 单一定义守卫：类字典里 resolve_ui_confirm 只能来自 PolicyEngine
        # 自身（不是被子类或其他地方重写）；且必须是 callable 且返回 bool。
        method = type(engine).__dict__.get("resolve_ui_confirm")
        assert method is not None, "resolve_ui_confirm 必须在 PolicyEngine 上定义"
        assert callable(method)

        # 运行时守卫：返回值必须是 bool（老版本契约），不是 None。
        engine.store_ui_pending("t_guard", "run_shell", {"command": "echo guard"})
        result = engine.resolve_ui_confirm("t_guard", "allow_session")
        assert result is True, (
            "resolve_ui_confirm 返回 None 说明老版本 def 已被覆盖；"
            "检查 core/policy.py 是否有第二个同名 def"
        )
        # 并且副作用要生效：session allowlist 里应该有东西
        assert len(engine._session_allowlist) > 0, (
            "resolve_ui_confirm 副作用失效：mark_confirmed 没被调用；"
            "意味着老版本定义被新版本覆盖了"
        )


# ---------------------------------------------------------------------------
# Death switch with configurable multiplier
# ---------------------------------------------------------------------------

class TestDeathSwitchMultiplier:
    def test_total_multiplier(self, tmp_workspace):
        config = SecurityConfig(
            enabled=True,
            zones=ZonePolicyConfig(
                enabled=True,
                workspace=[str(tmp_workspace / "workspace")],
                protected=[str(tmp_workspace / "protected")],
            ),
            self_protection=SelfProtectionConfig(
                enabled=True,
                death_switch_threshold=5,
                death_switch_total_multiplier=2,
            ),
        )
        engine = PolicyEngine(config)
        # Allow some in between to reset consecutive counter
        for _ in range(4):
            engine.assert_tool_allowed("write_file", {"path": str(tmp_workspace / "protected" / "x")})
        assert not engine.readonly_mode
        engine._on_allow("read_file")  # reset consecutive counter
        # Still accumulating total
        for _ in range(6):
            engine.assert_tool_allowed("write_file", {"path": str(tmp_workspace / "protected" / "y")})
        assert engine.readonly_mode


# ---------------------------------------------------------------------------
# YAML loading with new fields
# ---------------------------------------------------------------------------

class TestYAMLNewFields:
    def test_default_frontend_mode_matches_trust_mode(self):
        engine = PolicyEngine()

        assert engine.config.confirmation.mode == "yolo"
        assert engine._frontend_mode == "yolo"

    def test_load_confirmation_mode(self, tmp_path):
        yaml_path = tmp_path / "POLICIES.yaml"
        yaml_path.write_text("""
security:
  confirmation:
    mode: yolo
    confirm_ttl: 300
""", encoding="utf-8")
        engine = PolicyEngine()
        engine.load_from_yaml(yaml_path)
        assert engine.config.confirmation.mode == "yolo"
        assert engine.config.confirmation.confirm_ttl == 300.0
        assert engine._frontend_mode == "yolo"

    def test_auto_confirm_backward_compat(self, tmp_path):
        yaml_path = tmp_path / "POLICIES.yaml"
        yaml_path.write_text("""
security:
  confirmation:
    auto_confirm: true
""", encoding="utf-8")
        engine = PolicyEngine()
        engine.load_from_yaml(yaml_path)
        assert engine.config.confirmation.mode == "yolo"

    def test_user_allowlist_loaded(self, tmp_path):
        yaml_path = tmp_path / "POLICIES.yaml"
        yaml_path.write_text("""
security:
  user_allowlist:
    commands:
      - pattern: "npm install*"
        added_at: "2026-04-07T00:00:00Z"
    tools:
      - name: write_file
        zone: workspace
""", encoding="utf-8")
        engine = PolicyEngine()
        engine.load_from_yaml(yaml_path)
        assert len(engine.config.user_allowlist.commands) == 1
        assert len(engine.config.user_allowlist.tools) == 1

    def test_death_switch_total_multiplier_loaded(self, tmp_path):
        yaml_path = tmp_path / "POLICIES.yaml"
        yaml_path.write_text("""
security:
  self_protection:
    death_switch_threshold: 5
    death_switch_total_multiplier: 4
""", encoding="utf-8")
        engine = PolicyEngine()
        engine.load_from_yaml(yaml_path)
        assert engine.config.self_protection.death_switch_total_multiplier == 4

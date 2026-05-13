from pathlib import Path

from openakita.core.policy import PermissionMode, PolicyDecision, PolicyEngine


def test_plan_permission_mode_denies_write(tmp_path):
    engine = PolicyEngine()
    engine.config.zones.workspace = [str(tmp_path)]
    engine.set_permission_mode("plan")

    result = engine.assert_tool_allowed("write_file", {"path": str(tmp_path / "a.txt")})

    assert result.decision == PolicyDecision.DENY
    assert result.metadata["permission_mode"] == PermissionMode.PLAN.value


def test_accept_edits_still_blocks_critical_shell():
    engine = PolicyEngine()
    engine.set_permission_mode("accept_edits")

    result = engine.assert_tool_allowed("run_shell", {"command": "rm -rf /"})

    assert result.decision in (PolicyDecision.DENY, PolicyDecision.CONFIRM)


def test_accept_edits_allows_workspace_edit_but_not_controlled_write(tmp_path):
    workspace = tmp_path / "workspace"
    controlled = tmp_path / "controlled"
    workspace.mkdir()
    controlled.mkdir()
    engine = PolicyEngine()
    engine.config.zones.workspace = [str(workspace)]
    engine.config.zones.controlled = [str(controlled)]
    engine.set_permission_mode("accept_edits")

    workspace_result = engine.assert_tool_allowed("write_file", {"path": str(workspace / "a.txt")})
    controlled_result = engine.assert_tool_allowed("write_file", {"path": str(controlled / "a.txt")})

    assert workspace_result.decision == PolicyDecision.ALLOW
    assert controlled_result.decision == PolicyDecision.CONFIRM


def test_bypass_permissions_still_respects_forbidden_paths(tmp_path):
    secret = Path(tmp_path) / ".ssh" / "id_rsa"
    engine = PolicyEngine()
    engine.config.zones.forbidden = [str(secret.parent)]
    engine.set_permission_mode("bypass_permissions")

    result = engine.assert_tool_allowed("read_file", {"path": str(secret)})

    assert result.decision == PolicyDecision.DENY

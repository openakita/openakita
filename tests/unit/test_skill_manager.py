import json

import pytest

from openakita.core.skill_manager import (
    SKILL_GIT_CLONE_TIMEOUT_SECONDS,
    SkillManager,
)


class _DummyCatalog:
    def generate_catalog(self) -> str:
        return ""


class _DummyLoader:
    def load_skill(self, target_dir):
        raise AssertionError("load_skill should not be called for timeout failures")


class _ShellResult:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def success(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return self.stdout + (f"\n{self.stderr}" if self.stderr else "")


class _TimeoutShellTool:
    def __init__(self):
        self.calls = []

    async def run(self, command: str, timeout: int | None = None):
        self.calls.append({"command": command, "timeout": timeout})
        return _ShellResult(
            returncode=-1,
            stderr=f"Command timed out after {SKILL_GIT_CLONE_TIMEOUT_SECONDS} seconds",
        )


@pytest.mark.asyncio
async def test_install_from_git_uses_dedicated_timeout_and_returns_structured_timeout_error(tmp_path):
    shell_tool = _TimeoutShellTool()
    manager = SkillManager(
        skill_registry=object(),
        skill_loader=_DummyLoader(),
        skill_catalog=_DummyCatalog(),
        shell_tool=shell_tool,
    )

    result = await manager._install_from_git(
        "https://github.com/openclaw/multi-search-engine.git",
        name=None,
        subdir=None,
        skills_dir=tmp_path,
    )

    assert shell_tool.calls
    assert shell_tool.calls[0]["timeout"] == SKILL_GIT_CLONE_TIMEOUT_SECONDS

    payload = json.loads(result)
    assert payload["error"] is True
    assert payload["tool_name"] == "install_skill"
    assert payload["error_type"] == "timeout"
    assert payload["details"]["failure_class"] == "skill_install_network_timeout"
    assert payload["details"]["timeout_seconds"] == SKILL_GIT_CLONE_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_install_from_git_opens_circuit_after_repeated_network_failures(tmp_path):
    shell_tool = _TimeoutShellTool()
    manager = SkillManager(
        skill_registry=object(),
        skill_loader=_DummyLoader(),
        skill_catalog=_DummyCatalog(),
        shell_tool=shell_tool,
    )
    git_url = "https://github.com/openclaw/multi-search-engine.git"

    for _ in range(2):
        payload = json.loads(
            await manager._install_from_git(git_url, name=None, subdir=None, skills_dir=tmp_path)
        )
        assert payload["details"]["failure_class"] == "skill_install_network_timeout"

    blocked_payload = json.loads(
        await manager._install_from_git(git_url, name=None, subdir=None, skills_dir=tmp_path)
    )
    assert blocked_payload["error"] is True
    assert blocked_payload["details"]["failure_class"] == "skill_install_circuit_open"
    assert blocked_payload["details"]["blocked_by"] == "skill_install_network_timeout"
    assert shell_tool.calls and len(shell_tool.calls) == 2

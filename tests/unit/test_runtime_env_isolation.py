import json

from openakita.agents.profile import AgentProfile
from openakita.api.routes.agents import ProfileCreateRequest, create_agent_profile
from openakita.experience import ToolExperienceTracker
from openakita.runtime_envs import (
    apply_execution_environment,
    resolve_agent_env,
    resolve_scratch_env,
    resolve_skill_env,
)
from openakita.skills.parser import parse_skill
from openakita.skills.runtime_registry import (
    mark_skill_loaded,
    mark_skill_pending_update,
    read_skill_runtime_registry,
)


def test_agent_profile_runtime_policy_roundtrip():
    profile = AgentProfile(
        id="seo-writer",
        name="SEO Writer",
        runtime_env_mode="agent",
        runtime_env_dependencies=["pandas==2.2.0"],
    )

    restored = AgentProfile.from_dict(profile.to_dict())

    assert restored.runtime_env_mode == "agent"
    assert restored.runtime_env_dependencies == ["pandas==2.2.0"]


def test_agent_profile_create_api_persists_runtime_policy(monkeypatch, tmp_path):
    from openakita.agents import profile as profile_module

    store = profile_module.ProfileStore(tmp_path / "agents")
    monkeypatch.setattr(profile_module, "get_profile_store", lambda: store)

    body = ProfileCreateRequest(
        id="runtime-writer",
        name="Runtime Writer",
        runtime_env_mode="agent",
        runtime_env_dependencies=["packaging==24.2"],
    )

    result = create_agent_profile(body)
    try:
        result = result.send(None)
    except StopIteration as exc:
        result = exc.value

    profile = result["profile"]
    assert profile["runtime_env_mode"] == "agent"
    assert profile["runtime_env_dependencies"] == ["packaging==24.2"]


def test_execution_env_resolvers_are_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr("openakita.runtime_envs.get_runtime_root", lambda: tmp_path / "runtime")

    agent = resolve_agent_env("writer", deps=["requests==2.31.0"])
    skill = resolve_skill_env("xlsx", deps=["openpyxl==3.1.2"])
    scratch = resolve_scratch_env(session_id="session-1")

    assert agent.scope == "agent"
    assert skill.scope == "skill"
    assert scratch.scope == "scratch"
    assert "agents" in agent.venv_path.parts
    assert "skills" in skill.venv_path.parts
    assert "scratch" in scratch.venv_path.parts
    assert agent.deps_hash


def test_apply_execution_environment_prefers_managed_python(monkeypatch, tmp_path):
    monkeypatch.setattr("openakita.runtime_envs.get_runtime_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(
        "openakita.runtime_envs.resolve_pip_index",
        lambda: {"url": "https://example.invalid/simple", "trusted_host": "example.invalid"},
    )
    spec = resolve_agent_env("writer")

    env = apply_execution_environment({"PATH": "base"}, spec)

    assert env["OPENAKITA_EXECUTION_ENV_SCOPE"] == "agent"
    assert env["OPENAKITA_AGENT_PYTHON"] == str(spec.python_path)
    assert env["OPENAKITA_EXECUTION_DEPS_HASH"] == spec.deps_hash
    assert env["PATH"].startswith(str(spec.bin_path))
    assert env["PYTHONNOUSERSITE"] == "1"


def test_skill_python_metadata_is_parsed(tmp_path):
    skill_dir = tmp_path / "skills" / "data-skill"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
name: data-skill
description: Data skill
metadata:
  openakita:
    python:
      env: skill
      dependencies:
        - pandas==2.2.0
        - requests
---
# Data Skill
""",
        encoding="utf-8",
    )

    parsed = parse_skill(skill_file)

    assert parsed.metadata.python_env == "skill"
    assert parsed.metadata.python_dependencies == ["pandas==2.2.0", "requests"]


def test_skill_runtime_registry_tracks_loaded_and_pending_update(monkeypatch, tmp_path):
    monkeypatch.setattr("openakita.skills.runtime_registry._get_openakita_root", lambda: tmp_path)

    mark_skill_loaded(
        "data-skill",
        source_path=str(tmp_path / "skills" / "data-skill"),
        dependencies=["requests", "pandas==2.2.0"],
    )
    mark_skill_pending_update("data-skill", revision="rev-2")

    state = read_skill_runtime_registry()["skills"]["data-skill"]
    assert state["installed"] is True
    assert state["loaded"] is True
    assert state["pending_update_revision"] == "rev-2"
    assert state["reload_required"] is True
    assert state["update_policy"] == "disk-only"
    assert state["deps_hash"]


def test_tool_experience_redacts_secrets(tmp_path):
    tracker = ToolExperienceTracker(tmp_path / "tool_experience.jsonl")

    tracker.record(
        tool_name="run_shell",
        agent_profile_id="writer",
        env_scope="agent",
        success=False,
        input_summary={"command": "echo token=abc123"},
        output="Authorization: Bearer secret-token",
        error_type="runtime",
    )

    line = (tmp_path / "tool_experience.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    dumped = json.dumps(payload)
    assert "abc123" not in dumped
    assert "secret-token" not in dumped
    assert "[REDACTED]" in dumped

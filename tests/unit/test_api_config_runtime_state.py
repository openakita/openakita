import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class _DummyPersonaManager:
    def __init__(self):
        self.switched_to: str | None = None

    def switch_preset(self, preset_name: str) -> bool:
        self.switched_to = preset_name
        return True


class _DummyAgent:
    def __init__(self):
        self.persona_manager = _DummyPersonaManager()
        self.cache_invalidated = False
        self._context = SimpleNamespace(system="old")

    def _invalidate_system_prompt_cache(self, reason: str = "") -> None:
        self.cache_invalidated = True

    def _build_system_prompt(self) -> str:
        return f"persona={self.persona_manager.switched_to}"


class _DummyPool:
    def __init__(self):
        self.reasons: list[str] = []

    def notify_runtime_config_changed(self, reason: str) -> None:
        self.reasons.append(reason)


@pytest.fixture
def isolated_runtime_state(tmp_path, monkeypatch):
    from openakita.config import runtime_state, settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "project_root", tmp_path)
    monkeypatch.setattr(settings, "persona_name", "default")
    monkeypatch.setattr(runtime_state, "_state_file", data_dir / "runtime_state.json")
    return tmp_path


@pytest.mark.asyncio
async def test_read_env_overlays_runtime_state_persona(isolated_runtime_state):
    from openakita.api.routes.config import read_env
    from openakita.config import settings

    env_path = isolated_runtime_state / ".env"
    env_path.write_text("PERSONA_NAME=default\nOPENAI_API_KEY=sk-1234567890\n", encoding="utf-8")
    settings.persona_name = "jarvis"

    data = await read_env()

    assert data["env"]["PERSONA_NAME"] == "jarvis"
    assert data["has_value"]["PERSONA_NAME"] is True
    assert "PERSONA_NAME=default" in data["raw"]
    assert data["env"]["OPENAI_API_KEY"].startswith("sk-1")


@pytest.mark.asyncio
async def test_write_env_persists_persona_to_runtime_state_and_refreshes_agents(
    isolated_runtime_state,
):
    from openakita.api.routes.config import EnvUpdateRequest, write_env
    from openakita.config import runtime_state, settings

    env_path = isolated_runtime_state / ".env"
    env_path.write_text("PERSONA_NAME=default\nOPENAI_API_KEY=old\n", encoding="utf-8")
    agent = _DummyAgent()
    pool = _DummyPool()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent=agent, agent_pool=pool)))

    response = await write_env(
        EnvUpdateRequest(entries={"PERSONA_NAME": "jarvis", "OPENAI_API_KEY": "new"}, delete_keys=[]),
        request,
    )

    assert response["status"] == "ok"
    assert "PERSONA_NAME" in response["updated_keys"]
    assert settings.persona_name == "jarvis"
    assert json.loads(runtime_state.state_file.read_text(encoding="utf-8"))["persona_name"] == "jarvis"

    env_text = env_path.read_text(encoding="utf-8")
    assert "PERSONA_NAME=" not in env_text
    assert "OPENAI_API_KEY=new" in env_text

    assert agent.persona_manager.switched_to == "jarvis"
    assert agent.cache_invalidated is True
    assert agent._context.system == "persona=jarvis"
    assert pool.reasons == ["runtime_config:persona_name"]


@pytest.mark.asyncio
async def test_write_env_invalid_runtime_value_does_not_partially_update(
    isolated_runtime_state,
):
    from openakita.api.routes.config import EnvUpdateRequest, write_env
    from openakita.config import settings

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent=None, agent_pool=None)))

    with pytest.raises(HTTPException):
        await write_env(
            EnvUpdateRequest(
                entries={
                    "PERSONA_NAME": "jarvis",
                    "ALWAYS_LOAD_TOOLS": "not-json",
                },
                delete_keys=[],
            ),
            request,
        )

    assert settings.persona_name == "default"

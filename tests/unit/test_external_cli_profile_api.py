from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.agents.cli_detector import CliProviderId
from openakita.agents.profile import AgentType, ProfileStore
from openakita.api.routes import agents as agents_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "agents")
    monkeypatch.setattr(
        "openakita.agents.profile.get_profile_store",
        lambda: store,
    )
    app = FastAPI()
    app.include_router(agents_routes.router)
    return TestClient(app), store


def test_create_external_cli_profile_persists_cli_fields(client):
    c, store = client
    response = c.post(
        "/api/agents/profiles",
        json={
            "id": "claude-custom",
            "name": "Claude Custom",
            "type": "external_cli",
            "cli_provider_id": "claude_code",
            "cli_permission_mode": "write",
            "cli_env": {"CLAUDE_CONFIG_DIR": "${HOME}/.claude"},
        },
    )

    assert response.status_code == 200
    profile = store.get("claude-custom")
    assert profile is not None
    assert profile.type == AgentType.EXTERNAL_CLI
    assert profile.cli_provider_id == CliProviderId.CLAUDE_CODE
    assert profile.cli_permission_mode.value == "write"
    assert profile.cli_env == {"CLAUDE_CONFIG_DIR": "${HOME}/.claude"}
    assert response.json()["profile"]["type"] == "external_cli"


def test_create_external_cli_profile_requires_provider_id(client):
    c, _store = client
    response = c.post(
        "/api/agents/profiles",
        json={
            "id": "broken-cli",
            "name": "Broken CLI",
            "type": "external_cli",
        },
    )

    assert response.status_code == 400
    assert "cli_provider_id" in response.json()["detail"]


def test_create_native_profile_rejects_cli_provider_id(client):
    c, _store = client
    response = c.post(
        "/api/agents/profiles",
        json={
            "id": "native-with-cli",
            "name": "Native With CLI",
            "type": "custom",
            "cli_provider_id": "claude_code",
        },
    )

    assert response.status_code == 400
    assert "cli_provider_id" in response.json()["detail"]


def test_create_native_profile_rejects_cli_only_fields(client):
    c, _store = client
    response = c.post(
        "/api/agents/profiles",
        json={
            "id": "native-with-cli-fields",
            "name": "Native With CLI Fields",
            "type": "custom",
            "cli_permission_mode": "plan",
            "cli_env": {"CODEX_HOME": "/tmp/codex"},
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "cli_permission_mode" in detail
    assert "cli_env" in detail


def test_update_external_cli_profile_can_change_permission_and_env(client):
    c, store = client
    c.post(
        "/api/agents/profiles",
        json={
            "id": "codex-custom",
            "name": "Codex Custom",
            "type": "external_cli",
            "cli_provider_id": "codex",
            "cli_permission_mode": "plan",
        },
    )

    response = c.put(
        "/api/agents/profiles/codex-custom",
        json={
            "cli_permission_mode": "write",
            "cli_env": {"CODEX_HOME": "/tmp/codex"},
        },
    )

    assert response.status_code == 200
    profile = store.get("codex-custom")
    assert profile is not None
    assert profile.cli_permission_mode.value == "write"
    assert profile.cli_env == {"CODEX_HOME": "/tmp/codex"}


def test_update_external_cli_profile_can_change_provider_id(client):
    c, store = client
    c.post(
        "/api/agents/profiles",
        json={
            "id": "provider-change",
            "name": "Provider Change",
            "type": "external_cli",
            "cli_provider_id": "claude_code",
        },
    )

    response = c.put(
        "/api/agents/profiles/provider-change",
        json={"cli_provider_id": "codex"},
    )

    assert response.status_code == 200
    profile = store.get("provider-change")
    assert profile is not None
    assert profile.cli_provider_id == CliProviderId.CODEX


def test_update_external_cli_profile_rejects_invalid_provider_id(client):
    c, _store = client
    c.post(
        "/api/agents/profiles",
        json={
            "id": "invalid-provider",
            "name": "Invalid Provider",
            "type": "external_cli",
            "cli_provider_id": "claude_code",
        },
    )

    response = c.put(
        "/api/agents/profiles/invalid-provider",
        json={"cli_provider_id": "not-real"},
    )

    assert response.status_code == 400
    assert "cli_provider_id" in response.json()["detail"]


def test_update_native_profile_rejects_provider_id(client):
    c, _store = client
    c.post(
        "/api/agents/profiles",
        json={
            "id": "native-profile",
            "name": "Native Profile",
            "type": "custom",
        },
    )

    response = c.put(
        "/api/agents/profiles/native-profile",
        json={"cli_provider_id": "codex"},
    )

    assert response.status_code == 400
    assert "cli_provider_id" in response.json()["detail"]


def test_update_native_profile_rejects_cli_only_fields(client):
    c, _store = client
    c.post(
        "/api/agents/profiles",
        json={
            "id": "native-update-cli-fields",
            "name": "Native Update CLI Fields",
            "type": "custom",
        },
    )

    response = c.put(
        "/api/agents/profiles/native-update-cli-fields",
        json={
            "cli_permission_mode": "plan",
            "cli_env": {"CODEX_HOME": "/tmp/codex"},
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "cli_permission_mode" in detail
    assert "cli_env" in detail


def test_update_external_cli_profile_rejects_clearing_provider_id(client):
    c, store = client
    c.post(
        "/api/agents/profiles",
        json={
            "id": "clear-provider",
            "name": "Clear Provider",
            "type": "external_cli",
            "cli_provider_id": "claude_code",
        },
    )

    response = c.put(
        "/api/agents/profiles/clear-provider",
        json={"cli_provider_id": None},
    )

    assert response.status_code == 400
    assert "cli_provider_id" in response.json()["detail"]
    profile = store.get("clear-provider")
    assert profile is not None
    assert profile.cli_provider_id == CliProviderId.CLAUDE_CODE

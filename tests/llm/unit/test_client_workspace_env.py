import json

from openakita.llm.client import LLMClient


def _write_workspace(workspace, api_key: str) -> None:
    data_dir = workspace / "data"
    data_dir.mkdir(parents=True)
    (workspace / ".env").write_text(f"CUSTOM_API_KEY={api_key}\n", encoding="utf-8")
    (data_dir / "llm_endpoints.json").write_text(
        json.dumps({
            "endpoints": [{
                "name": "custom-test",
                "provider": "custom",
                "api_type": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key_env": "CUSTOM_API_KEY",
                "model": "demo",
                "priority": 1,
                "capabilities": ["text"],
            }],
            "compiler_endpoints": [],
            "stt_endpoints": [],
            "settings": {},
        }),
        encoding="utf-8",
    )


def test_llm_client_reads_workspace_env_on_first_load(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    _write_workspace(workspace, "alpha")
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)

    client = LLMClient(config_path=workspace / "data" / "llm_endpoints.json")

    assert client.providers["custom-test"].api_key == "alpha"


def test_llm_client_isolates_same_env_name_across_workspaces(tmp_path, monkeypatch):
    workspace_one = tmp_path / "workspace-one"
    workspace_two = tmp_path / "workspace-two"
    _write_workspace(workspace_one, "alpha")
    _write_workspace(workspace_two, "beta")
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)

    client_one = LLMClient(config_path=workspace_one / "data" / "llm_endpoints.json")
    client_two = LLMClient(config_path=workspace_two / "data" / "llm_endpoints.json")

    assert client_one.providers["custom-test"].api_key == "alpha"
    assert client_two.providers["custom-test"].api_key == "beta"

    (workspace_one / ".env").write_text("CUSTOM_API_KEY=alpha-2\n", encoding="utf-8")
    assert client_one.reload() is True

    assert client_one.providers["custom-test"].api_key == "alpha-2"
    assert client_two.providers["custom-test"].api_key == "beta"

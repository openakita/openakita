from openakita.llm.endpoint_manager import EndpointManager


def test_save_endpoint_auto_generates_unique_env_var_names(tmp_path):
    manager = EndpointManager(tmp_path)

    first = manager.save_endpoint(
        endpoint={
            "name": "custom-one",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "https://api.example.com/v1",
            "model": "demo-1",
        },
        api_key="secret-1",
    )
    second = manager.save_endpoint(
        endpoint={
            "name": "custom-two",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "https://api.example.com/v1",
            "model": "demo-2",
        },
        api_key="secret-2",
    )

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")

    assert first["api_key_env"] == "CUSTOM_API_KEY"
    assert second["api_key_env"] == "CUSTOM_API_KEY_2"
    assert "CUSTOM_API_KEY=secret-1" in env_text
    assert "CUSTOM_API_KEY_2=secret-2" in env_text


def test_save_endpoint_keeps_existing_env_var_when_updating_same_endpoint(tmp_path):
    manager = EndpointManager(tmp_path)

    first = manager.save_endpoint(
        endpoint={
            "name": "custom-one",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "https://api.example.com/v1",
            "model": "demo-1",
        },
        api_key="secret-1",
    )
    updated = manager.save_endpoint(
        endpoint={
            "name": "custom-one",
            "provider": "custom",
            "api_type": "openai",
            "base_url": "https://api.example.com/v2",
            "model": "demo-1b",
        },
        api_key="secret-1b",
    )

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")

    assert updated["api_key_env"] == first["api_key_env"]
    assert "CUSTOM_API_KEY=secret-1b" in env_text

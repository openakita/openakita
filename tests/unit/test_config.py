"""L1 Unit Tests: LLM config loading, validation, and endpoint parsing."""

import json
import os

from openakita.llm.config import (
    create_default_config,
    load_endpoints_config,
    save_endpoints_config,
    validate_config,
)
from openakita.llm.types import EndpointConfig


class TestLoadEndpointsConfig:
    def test_missing_file_returns_empty(self, tmp_path):
        result = load_endpoints_config(tmp_path / "nonexistent.json")
        endpoints, compiler_eps, stt_eps, settings = result
        assert endpoints == []
        assert compiler_eps == []
        assert settings == {}

    def test_invalid_json_raises_error(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json}", encoding="utf-8")
        endpoints, compiler_eps, stt_eps, settings = load_endpoints_config(bad_file)
        assert endpoints == []
        assert compiler_eps == []
        assert stt_eps == []
        assert settings == {}

    def test_valid_config_loads_endpoints(self, tmp_path):
        config_file = tmp_path / "endpoints.json"
        config_file.write_text(json.dumps({
            "endpoints": [{
                "name": "test-ep",
                "provider": "openai",
                "api_type": "openai",
                "base_url": "https://api.test.com/v1",
                "api_key": "sk-test",
                "model": "gpt-4",
                "priority": 1,
            }],
            "settings": {"retry_count": 3},
        }), encoding="utf-8")
        endpoints, _, _, settings = load_endpoints_config(config_file)
        assert len(endpoints) == 1
        assert endpoints[0].name == "test-ep"
        assert endpoints[0].model == "gpt-4"
        assert settings["retry_count"] == 3

    def test_endpoints_sorted_by_priority(self, tmp_path):
        config_file = tmp_path / "endpoints.json"
        config_file.write_text(json.dumps({
            "endpoints": [
                {"name": "low", "provider": "a", "api_type": "openai", "base_url": "https://a.com", "api_key": "k", "model": "m", "priority": 3},
                {"name": "high", "provider": "b", "api_type": "openai", "base_url": "https://b.com", "api_key": "k", "model": "m", "priority": 1},
            ],
        }), encoding="utf-8")
        endpoints, _, _, _ = load_endpoints_config(config_file)
        assert endpoints[0].name == "high"
        assert endpoints[1].name == "low"

    def test_compiler_endpoints_loaded(self, tmp_path):
        config_file = tmp_path / "endpoints.json"
        config_file.write_text(json.dumps({
            "endpoints": [],
            "compiler_endpoints": [{
                "name": "compiler",
                "provider": "openai",
                "api_type": "openai",
                "base_url": "https://api.test.com/v1",
                "api_key": "sk-test",
                "model": "gpt-4o-mini",
                "priority": 1,
            }],
        }), encoding="utf-8")
        _, compiler_eps, _, _ = load_endpoints_config(config_file)
        assert len(compiler_eps) == 1
        assert compiler_eps[0].name == "compiler"

    def test_empty_endpoints_list(self, tmp_path):
        config_file = tmp_path / "endpoints.json"
        config_file.write_text(json.dumps({"endpoints": []}), encoding="utf-8")
        endpoints, _, _, _ = load_endpoints_config(config_file)
        assert endpoints == []


class TestSaveEndpointsConfig:
    def test_save_creates_file(self, tmp_path):
        config_file = tmp_path / "data" / "endpoints.json"
        ep = EndpointConfig(
            name="test",
            provider="openai",
            api_type="openai",
            base_url="https://api.test.com/v1",
            model="gpt-4",
        )
        save_endpoints_config([ep], config_path=config_file)
        assert config_file.exists()

        data = json.loads(config_file.read_text(encoding="utf-8"))
        assert len(data["endpoints"]) == 1
        assert data["endpoints"][0]["name"] == "test"

    def test_save_creates_parent_dirs(self, tmp_path):
        config_file = tmp_path / "deep" / "nested" / "endpoints.json"
        save_endpoints_config([], config_path=config_file)
        assert config_file.exists()

    def test_save_includes_default_settings(self, tmp_path):
        config_file = tmp_path / "endpoints.json"
        save_endpoints_config([], config_path=config_file)
        data = json.loads(config_file.read_text(encoding="utf-8"))
        assert "settings" in data
        assert data["settings"]["fallback_on_error"] is True


class TestCreateDefaultConfig:
    def test_creates_default_endpoints(self, tmp_path):
        config_file = tmp_path / "data" / "endpoints.json"
        create_default_config(config_path=config_file)
        assert config_file.exists()
        data = json.loads(config_file.read_text(encoding="utf-8"))
        assert len(data["endpoints"]) == 2
        assert data["endpoints"][0]["name"] == "claude-primary"
        assert data["endpoints"][1]["name"] == "qwen-backup"


class TestValidateConfig:
    def test_validate_nonexistent_file(self, tmp_path):
        errors = validate_config(tmp_path / "missing.json")
        assert any("No endpoints" in e for e in errors)

    def test_validate_valid_config(self, tmp_path):
        config_file = tmp_path / "endpoints.json"
        config_file.write_text(json.dumps({
            "endpoints": [{
                "name": "test",
                "provider": "openai",
                "api_type": "openai",
                "base_url": "https://api.test.com/v1",
                "api_key": "sk-direct-key",
                "model": "gpt-4",
                "priority": 1,
            }],
        }), encoding="utf-8")
        errors = validate_config(config_file)
        # May have warnings about env var, but base_url and api_type are valid
        api_type_errors = [e for e in errors if "api_type" in e]
        assert len(api_type_errors) == 0

    def test_validate_invalid_api_type(self, tmp_path):
        config_file = tmp_path / "endpoints.json"
        config_file.write_text(json.dumps({
            "endpoints": [{
                "name": "bad",
                "provider": "custom",
                "api_type": "grpc",
                "base_url": "https://api.test.com/v1",
                "api_key": "sk-test",
                "model": "m",
                "priority": 1,
            }],
        }), encoding="utf-8")
        errors = validate_config(config_file)
        assert any("api_type" in e for e in errors)

    def test_validate_invalid_base_url(self, tmp_path):
        config_file = tmp_path / "endpoints.json"
        config_file.write_text(json.dumps({
            "endpoints": [{
                "name": "bad",
                "provider": "openai",
                "api_type": "openai",
                "base_url": "not-a-url",
                "api_key": "sk-test",
                "model": "m",
                "priority": 1,
            }],
        }), encoding="utf-8")
        errors = validate_config(config_file)
        assert any("base_url" in e for e in errors)

    def test_validate_missing_api_key_env(self, tmp_path):
        config_file = tmp_path / "endpoints.json"
        config_file.write_text(json.dumps({
            "endpoints": [{
                "name": "test",
                "provider": "openai",
                "api_type": "openai",
                "base_url": "https://api.test.com/v1",
                "api_key_env": "NONEXISTENT_KEY_VAR",
                "model": "m",
                "priority": 1,
            }],
        }), encoding="utf-8")
        os.environ.pop("NONEXISTENT_KEY_VAR", None)
        errors = validate_config(config_file)
        assert any("NONEXISTENT_KEY_VAR" in e for e in errors)

    def test_validate_invalid_json(self, tmp_path):
        config_file = tmp_path / "endpoints.json"
        config_file.write_text("not json", encoding="utf-8")
        errors = validate_config(config_file)
        assert any("Invalid JSON" in e for e in errors)

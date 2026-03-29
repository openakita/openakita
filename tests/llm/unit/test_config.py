"""
单元测试 - 配置加载 (6 个)

UT-G01 ~ UT-G06
"""

import json

from openakita.llm.config import (
    load_endpoints_config,
    save_endpoints_config,
    validate_config,
)
from openakita.llm.types import EndpointConfig


class TestConfigLoading:
    """配置加载测试"""

    def test_ut_g01_load_valid_config(self, tmp_path, test_config_content):
        """UT-G01: 加载有效配置"""
        config_path = tmp_path / "llm_endpoints.json"
        with open(config_path, "w") as f:
            json.dump(test_config_content, f)

        endpoints, _compiler, _stt, settings = load_endpoints_config(config_path)

        assert len(endpoints) == 2
        assert isinstance(endpoints[0], EndpointConfig)
        assert endpoints[0].name == "test-claude"
        assert endpoints[1].name == "test-qwen"
        assert settings["retry_count"] == 2

    def test_ut_g02_file_not_found(self, tmp_path):
        """UT-G02: 配置文件不存在"""
        config_path = tmp_path / "nonexistent.json"

        # 应该返回空配置而不是抛出异常
        endpoints, _compiler, _stt, settings = load_endpoints_config(config_path)

        assert endpoints == []
        assert settings == {}

    def test_ut_g03_invalid_json(self, tmp_path):
        """UT-G03: 配置格式错误"""
        config_path = tmp_path / "invalid.json"
        with open(config_path, "w") as f:
            f.write("{ invalid json }")

        endpoints, _compiler, _stt, settings = load_endpoints_config(config_path)

        assert endpoints == []
        assert settings == {}

    def test_ut_g04_missing_fields(self, tmp_path):
        """UT-G04: 字段缺失"""
        config_path = tmp_path / "incomplete.json"
        incomplete_config = {
            "endpoints": [
                {
                    "name": "test",
                    # 缺少必填字段
                }
            ]
        }
        with open(config_path, "w") as f:
            json.dump(incomplete_config, f)

        # 应该跳过无效端点并记录警告
        endpoints, _compiler, _stt, _settings = load_endpoints_config(config_path)
        assert len(endpoints) == 0


class TestEnvironmentVariables:
    """环境变量测试"""

    def test_ut_g05_env_var_replacement(self, tmp_path, test_config_content, monkeypatch):
        """UT-G05: 环境变量替换"""
        config_path = tmp_path / "config.json"
        with open(config_path, "w") as f:
            json.dump(test_config_content, f)

        # 设置环境变量
        monkeypatch.setenv("TEST_ANTHROPIC_KEY", "sk-test-123")

        endpoints, _compiler, _stt, _settings = load_endpoints_config(config_path)

        # 端点应该正确加载（环境变量存在）
        assert endpoints[0].api_key_env == "TEST_ANTHROPIC_KEY"

    def test_loads_workspace_env_for_explicit_config_path(self, tmp_path, monkeypatch):
        """显式 config_path 应从同工作区 .env 解析 API Key。"""
        workspace = tmp_path / "workspace"
        data_dir = workspace / "data"
        data_dir.mkdir(parents=True)

        config_path = data_dir / "llm_endpoints.json"
        config_path.write_text(json.dumps({
            "endpoints": [{
                "name": "custom-test",
                "provider": "custom",
                "api_type": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key_env": "CUSTOM_API_KEY",
                "model": "demo",
                "priority": 1,
            }],
        }), encoding="utf-8")
        (workspace / ".env").write_text("CUSTOM_API_KEY=secret-123\n", encoding="utf-8")

        monkeypatch.delenv("CUSTOM_API_KEY", raising=False)

        endpoints, _compiler, _stt, _settings = load_endpoints_config(config_path)

        assert len(endpoints) == 1
        assert endpoints[0].api_key_env == "CUSTOM_API_KEY"
        assert endpoints[0].api_key == "secret-123"
        assert endpoints[0].get_api_key() == "secret-123"

    def test_validate_config_reads_workspace_env_for_explicit_config_path(self, tmp_path, monkeypatch):
        """校验显式 config_path 时不应误报工作区 .env 中已有的 key 缺失。"""
        workspace = tmp_path / "workspace"
        data_dir = workspace / "data"
        data_dir.mkdir(parents=True)

        config_path = data_dir / "llm_endpoints.json"
        config_path.write_text(json.dumps({
            "endpoints": [{
                "name": "custom-test",
                "provider": "custom",
                "api_type": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key_env": "CUSTOM_API_KEY",
                "model": "demo",
                "priority": 1,
            }],
        }), encoding="utf-8")
        (workspace / ".env").write_text("CUSTOM_API_KEY=secret-123\n", encoding="utf-8")

        monkeypatch.delenv("CUSTOM_API_KEY", raising=False)

        errors = validate_config(config_path)

        assert not any("CUSTOM_API_KEY" in e and "not set" in e for e in errors)


class TestEmptyConfig:
    """空配置测试"""

    def test_ut_g06_empty_endpoints(self, tmp_path):
        """UT-G06: 空端点列表"""
        config_path = tmp_path / "empty.json"
        empty_config = {"endpoints": [], "settings": {}}
        with open(config_path, "w") as f:
            json.dump(empty_config, f)

        endpoints, _compiler, _stt, settings = load_endpoints_config(config_path)

        # 空列表是有效的，应该返回警告但不抛异常
        assert endpoints == []


class TestConfigSaving:
    """配置保存测试"""

    def test_save_and_reload(self, tmp_path):
        """测试保存和重新加载"""
        config_path = tmp_path / "saved.json"

        endpoints = [
            EndpointConfig(
                name="test-save",
                provider="anthropic",
                api_type="anthropic",
                base_url="https://api.anthropic.com",
                api_key_env="TEST_KEY",
                model="claude-3-sonnet",
                capabilities=["text", "vision"],
            )
        ]

        save_endpoints_config(endpoints, config_path=config_path)

        # 重新加载验证
        loaded, _compiler, _stt, _settings = load_endpoints_config(config_path)

        assert len(loaded) == 1
        assert loaded[0].name == "test-save"
        assert loaded[0].capabilities == ["text", "vision"]


class TestValidation:
    """配置验证测试"""

    def test_validate_good_config(self, tmp_path, test_config_content, monkeypatch):
        """测试有效配置验证"""
        config_path = tmp_path / "good.json"
        with open(config_path, "w") as f:
            json.dump(test_config_content, f)

        # 设置环境变量
        monkeypatch.setenv("TEST_ANTHROPIC_KEY", "sk-test")
        monkeypatch.setenv("TEST_DASHSCOPE_KEY", "sk-test")

        errors = validate_config(config_path)

        # 应该没有严重错误
        assert len([e for e in errors if "not set" not in e]) == 0

    def test_validate_missing_api_key(self, tmp_path, test_config_content):
        """测试缺少 API Key"""
        config_path = tmp_path / "missing_key.json"
        with open(config_path, "w") as f:
            json.dump(test_config_content, f)

        errors = validate_config(config_path)

        # 应该报告缺少的环境变量
        assert any("not set" in e for e in errors)

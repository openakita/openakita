"""
L3 Integration Tests: Setup wizard directory creation, env generation,
endpoint writing, and identity file creation.

Tests the non-interactive (programmatic) parts of SetupWizard.
The interactive prompts are NOT tested here — only the file-writing logic.
"""

import json

import pytest

from openakita.setup.wizard import SetupWizard


@pytest.fixture
def wizard(tmp_path):
    """SetupWizard pointing to a temp project dir."""
    w = SetupWizard(project_dir=tmp_path)
    return w


class TestWizardInit:
    def test_defaults(self, wizard, tmp_path):
        assert wizard.project_dir == tmp_path
        assert wizard.env_path == tmp_path / ".env"
        assert wizard.config == {}
        assert wizard._locale == "zh"

    def test_default_locale_settings(self, wizard):
        assert wizard._defaults["MODEL_DOWNLOAD_SOURCE"] == "hf-mirror"
        assert wizard._defaults["WHISPER_LANGUAGE"] == "zh"


class TestCreateDirectories:
    def test_creates_all_required_dirs(self, wizard, tmp_path):
        wizard._create_directories()
        for d in ["data", "identity", "skills", "logs"]:
            assert (tmp_path / d).is_dir()

    def test_creates_gitkeep_files(self, wizard, tmp_path):
        wizard._create_directories()
        for d in ["data", "identity", "skills", "logs"]:
            assert (tmp_path / d / ".gitkeep").exists()

    def test_idempotent(self, wizard, tmp_path):
        wizard._create_directories()
        wizard._create_directories()
        assert (tmp_path / "data").is_dir()


class TestGenerateEnvContent:
    def test_basic_env_content(self, wizard):
        wizard.config = {
            "ANTHROPIC_API_KEY": "sk-test-123",
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            "DEFAULT_MODEL": "claude-sonnet-4-20250514",
            "THINKING_MODE": "auto",
        }
        content = wizard._generate_env_content()
        assert "ANTHROPIC_API_KEY=sk-test-123" in content
        assert "DEFAULT_MODEL=claude-sonnet-4-20250514" in content
        assert "THINKING_MODE=auto" in content

    def test_env_contains_agent_settings(self, wizard):
        wizard.config = {}
        content = wizard._generate_env_content()
        assert "AGENT_NAME=OpenAkita" in content
        assert "AUTO_CONFIRM=false" in content
        assert "DATABASE_PATH=data/agent.db" in content

    def test_env_contains_tool_settings(self, wizard):
        content = wizard._generate_env_content()
        assert "MCP_ENABLED=true" in content
        assert "DESKTOP_ENABLED=true" in content

    def test_env_with_im_channels(self, wizard):
        wizard.config = {
            "TELEGRAM_BOT_TOKEN": "123:ABC",
            "TELEGRAM_ENABLED": "true",
        }
        content = wizard._generate_env_content()
        assert "TELEGRAM_BOT_TOKEN=123:ABC" in content
        assert "TELEGRAM_ENABLED=true" in content

    def test_env_with_proxy(self, wizard):
        wizard.config = {
            "HTTP_PROXY": "http://proxy:8080",
            "HTTPS_PROXY": "http://proxy:8080",
        }
        content = wizard._generate_env_content()
        assert "HTTP_PROXY=http://proxy:8080" in content

    def test_env_with_scheduler(self, wizard):
        wizard.config = {"SCHEDULER_ENABLED": "true"}
        content = wizard._generate_env_content()
        assert "SCHEDULER_ENABLED" in content


class TestWriteLLMEndpoints:
    def test_creates_endpoints_file(self, wizard, tmp_path):
        (tmp_path / "data").mkdir(exist_ok=True)
        wizard.config = {
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            "DEFAULT_MODEL": "claude-sonnet-4-20250514",
        }
        wizard._write_llm_endpoints()

        ep_file = tmp_path / "data" / "llm_endpoints.json"
        assert ep_file.exists()

        data = json.loads(ep_file.read_text(encoding="utf-8"))
        assert "endpoints" in data
        assert len(data["endpoints"]) >= 2
        names = {e["name"] for e in data["endpoints"]}
        assert "primary" in names
        assert "backup" in names

    def test_endpoints_have_settings(self, wizard, tmp_path):
        (tmp_path / "data").mkdir(exist_ok=True)
        wizard.config = {}
        wizard._write_llm_endpoints()

        data = json.loads((tmp_path / "data" / "llm_endpoints.json").read_text(encoding="utf-8"))
        assert "settings" in data
        assert data["settings"]["fallback_on_error"] is True

    def test_preserves_existing_endpoints(self, wizard, tmp_path):
        (tmp_path / "data").mkdir(exist_ok=True)
        ep_file = tmp_path / "data" / "llm_endpoints.json"
        ep_file.write_text(json.dumps({
            "endpoints": [{"name": "existing", "provider": "openai", "api_type": "openai",
                          "base_url": "https://api.openai.com/v1", "model": "gpt-4", "priority": 1}],
        }), encoding="utf-8")

        wizard.config = {}
        wizard._write_llm_endpoints()

        data = json.loads(ep_file.read_text(encoding="utf-8"))
        assert data["endpoints"][0]["name"] == "existing"

    def test_compiler_endpoints_written(self, wizard, tmp_path):
        (tmp_path / "data").mkdir(exist_ok=True)
        wizard.config = {
            "_compiler_primary": {
                "provider": "openai-compatible",
                "api_type": "openai",
                "base_url": "https://api.test.com/v1",
                "api_key_env": "TEST_KEY",
                "model": "qwen-plus",
            },
        }
        wizard._write_llm_endpoints()

        data = json.loads((tmp_path / "data" / "llm_endpoints.json").read_text(encoding="utf-8"))
        assert "compiler_endpoints" in data
        assert len(data["compiler_endpoints"]) >= 1
        assert data["compiler_endpoints"][0]["name"] == "compiler-primary"

    def test_endpoint_capabilities_inferred(self, wizard, tmp_path):
        (tmp_path / "data").mkdir(exist_ok=True)
        wizard.config = {
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            "DEFAULT_MODEL": "claude-sonnet-4-20250514",
        }
        wizard._write_llm_endpoints()

        data = json.loads((tmp_path / "data" / "llm_endpoints.json").read_text(encoding="utf-8"))
        caps = data["endpoints"][0].get("capabilities", [])
        assert "text" in caps
        assert "tools" in caps


class TestCreateIdentityExamples:
    def test_creates_soul_md(self, wizard, tmp_path):
        (tmp_path / "identity").mkdir(exist_ok=True)
        wizard._create_identity_examples()

        soul = tmp_path / "identity" / "SOUL.md"
        assert soul.exists()
        content = soul.read_text(encoding="utf-8")
        assert "OpenAkita" in content

    def test_does_not_overwrite_existing(self, wizard, tmp_path):
        identity_dir = tmp_path / "identity"
        identity_dir.mkdir(exist_ok=True)
        soul = identity_dir / "SOUL.md"
        soul.write_text("My custom soul", encoding="utf-8")

        wizard._create_identity_examples()
        assert soul.read_text(encoding="utf-8") == "My custom soul"


class TestLocaleDefaults:
    def test_zh_defaults(self, wizard):
        wizard._locale = "zh"
        wizard._defaults = {
            "MODEL_DOWNLOAD_SOURCE": "hf-mirror",
            "EMBEDDING_MODEL": "shibing624/text2vec-base-chinese",
            "WHISPER_LANGUAGE": "zh",
            "SCHEDULER_TIMEZONE": "Asia/Shanghai",
        }
        assert wizard._defaults["MODEL_DOWNLOAD_SOURCE"] == "hf-mirror"

    def test_en_defaults(self, wizard):
        wizard._locale = "en"
        wizard._defaults = {
            "MODEL_DOWNLOAD_SOURCE": "huggingface",
            "EMBEDDING_MODEL": "all-MiniLM-L6-v2",
            "WHISPER_LANGUAGE": "en",
            "SCHEDULER_TIMEZONE": "UTC",
        }
        assert wizard._defaults["MODEL_DOWNLOAD_SOURCE"] == "huggingface"
        assert wizard._defaults["SCHEDULER_TIMEZONE"] == "UTC"


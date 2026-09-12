"""OrcaRouter 服务商注册表单测。

镜像 OpenRouter 注册表的接入方式：providers.json 声明 + _CLASS_MODULE_MAP 注册。
"""

import json
from pathlib import Path

from openakita.llm import registries
from openakita.llm.registries import ALL_REGISTRIES, get_registry
from openakita.llm.registries.orcarouter import OrcaRouterRegistry

_REGISTRIES_JSON = Path(registries.__file__).parent / "providers.json"


def _entry() -> dict:
    entries = json.loads(_REGISTRIES_JSON.read_text(encoding="utf-8"))
    return next(e for e in entries if e["slug"] == "orcarouter")


def test_orcarouter_registered():
    """OrcaRouter 应出现在内置服务商列表里。"""
    slugs = {r.info.slug for r in ALL_REGISTRIES}
    assert "orcarouter" in slugs


def test_get_registry_returns_orcarouter():
    reg = get_registry("orcarouter")
    assert isinstance(reg, OrcaRouterRegistry)
    assert reg.info.slug == "orcarouter"
    assert reg.info.default_base_url == "https://api.orcarouter.ai/v1"
    assert reg.info.api_key_env_suggestion == "ORCAROUTER_API_KEY"
    assert reg.info.api_type == "openai"
    assert reg.info.supports_capability_api is True


def test_providers_json_entry():
    entry = _entry()
    assert entry["registry_class"] == "OrcaRouterRegistry"
    assert entry["api_type"] == "openai"
    assert entry["default_base_url"] == "https://api.orcarouter.ai/v1"
    assert entry["api_key_env_suggestion"] == "ORCAROUTER_API_KEY"


def test_router_models_in_fallback():
    """内置的路由模型应该包含 orcarouter/auto 与 orcarouter/free。"""
    # list_models 是 async 且需要网络，这里直接检查模块级 fallback
    from openakita.llm.registries.orcarouter import _ROUTER_MODELS

    router_ids = {m.id for m in _ROUTER_MODELS}
    assert "orcarouter/auto" in router_ids
    assert "orcarouter/free" in router_ids


class TestParseCapabilities:
    def test_vision_and_pdf_from_input_modalities(self):
        caps = OrcaRouterRegistry()._parse_capabilities(
            {"input_modalities": ["text", "image", "file"]}, "openai/gpt-4o"
        )
        assert caps["text"] is True
        assert caps["vision"] is True
        assert caps["video"] is False
        assert caps["pdf"] is True

    def test_audio_and_video(self):
        caps = OrcaRouterRegistry()._parse_capabilities(
            {"input_modalities": ["text", "image", "video", "audio", "file"]},
            "google/gemini-2.5-flash",
        )
        assert caps["video"] is True
        assert caps["audio"] is True

    def test_tools_from_model_name(self):
        caps = OrcaRouterRegistry()._parse_capabilities({}, "deepseek/deepseek-v4-pro")
        assert caps["tools"] is True

    def test_thinking_from_model_name(self):
        caps = OrcaRouterRegistry()._parse_capabilities({}, "openai/o3")
        assert caps["thinking"] is True
        assert caps["tools"] is True  # 推理模型同样支持工具

    def test_empty_architecture_defaults(self):
        caps = OrcaRouterRegistry()._parse_capabilities({}, "orcarouter/auto")
        assert caps == {
            "text": True,
            "vision": False,
            "video": False,
            "tools": False,
            "thinking": False,
            "audio": False,
            "pdf": False,
        }

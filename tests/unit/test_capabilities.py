"""L1 Unit Tests: LLM model capability inference and matching."""

import pytest

from openakita.llm.capabilities import (
    get_all_providers,
    get_models_by_provider,
    get_provider_slug_from_base_url,
    infer_capabilities,
    is_thinking_only,
    supports_capability,
)


class TestInferCapabilities:
    def test_known_model_exact_match(self):
        caps = infer_capabilities("gpt-4o", provider_slug="openai")
        assert caps["vision"] is True
        assert caps["tools"] is True
        assert caps["text"] is True

    def test_anthropic_claude_has_pdf(self):
        caps = infer_capabilities("claude-opus-4.5", provider_slug="anthropic")
        assert caps["pdf"] is True
        assert caps["vision"] is True
        assert caps["tools"] is True

    def test_deepseek_r1_is_thinking(self):
        caps = infer_capabilities("deepseek-r1", provider_slug="deepseek")
        assert caps["thinking"] is True

    def test_prefix_matching(self):
        caps = infer_capabilities("gpt-4o-2024-01-01", provider_slug="openai")
        assert caps["vision"] is True

    def test_cross_provider_fuzzy_match(self):
        """Model name matches across providers when provider_slug is unknown."""
        caps = infer_capabilities("claude-sonnet-4.5-latest")
        assert caps["tools"] is True

    def test_keyword_vision_inference(self):
        caps = infer_capabilities("some-unknown-vl-model")
        assert caps["vision"] is True

    def test_keyword_thinking_inference(self):
        caps = infer_capabilities("some-model-thinking")
        assert caps["thinking"] is True

    def test_keyword_tools_inference(self):
        caps = infer_capabilities("qwen-something-new")
        assert caps["tools"] is True

    def test_user_config_overrides(self):
        user = {"vision": True, "tools": False}
        caps = infer_capabilities("gpt-4o", provider_slug="openai", user_config=user)
        assert caps["vision"] is True
        assert caps["tools"] is False

    def test_all_capability_fields_present(self):
        caps = infer_capabilities("unknown-model-xyz")
        for key in ["text", "vision", "video", "tools", "thinking", "audio", "pdf"]:
            assert key in caps

    def test_unknown_model_defaults(self):
        caps = infer_capabilities("totally-unknown-model-12345")
        assert caps["text"] is True
        assert caps["vision"] is False

    def test_kimi_supports_video(self):
        caps = infer_capabilities("kimi-k2.5", provider_slug="moonshot")
        assert caps["video"] is True

    def test_gemini_supports_audio(self):
        caps = infer_capabilities("gemini-2.5-pro", provider_slug="google")
        assert caps["audio"] is True


class TestProviderSlugFromURL:
    def test_openai_url(self):
        assert get_provider_slug_from_base_url("https://api.openai.com/v1") == "openai"

    def test_anthropic_url(self):
        assert get_provider_slug_from_base_url("https://api.anthropic.com") == "anthropic"

    def test_dashscope_url(self):
        assert get_provider_slug_from_base_url("https://dashscope.aliyuncs.com/compatible-mode/v1") == "dashscope"

    def test_localhost_ollama(self):
        assert get_provider_slug_from_base_url("http://localhost:11434/v1") == "ollama"

    def test_localhost_lmstudio(self):
        assert get_provider_slug_from_base_url("http://127.0.0.1:1234/v1") == "lmstudio"

    def test_localhost_generic(self):
        assert get_provider_slug_from_base_url("http://localhost:8080/v1") == "local"

    def test_unknown_url(self):
        assert get_provider_slug_from_base_url("https://my-custom-llm.com/v1") is None


class TestSupportsCapability:
    def test_gpt4o_supports_vision(self):
        assert supports_capability("gpt-4o", "vision", "openai") is True

    def test_gpt4_no_vision(self):
        assert supports_capability("gpt-4", "vision", "openai") is False

    def test_deepseek_r1_supports_thinking(self):
        assert supports_capability("deepseek-r1", "thinking", "deepseek") is True


class TestIsThinkingOnly:
    def test_deepseek_r1_is_thinking_only(self):
        assert is_thinking_only("deepseek-r1", "deepseek") is True

    def test_gpt4o_is_not_thinking_only(self):
        assert is_thinking_only("gpt-4o", "openai") is False

    def test_qwq_is_thinking_only(self):
        assert is_thinking_only("qwq-plus", "dashscope") is True


class TestProviderLists:
    def test_get_all_providers(self):
        providers = get_all_providers()
        assert "openai" in providers
        assert "anthropic" in providers
        assert "deepseek" in providers
        assert len(providers) >= 5

    def test_get_models_by_provider(self):
        models = get_models_by_provider("openai")
        assert "gpt-4o" in models
        assert len(models) >= 3

    def test_unknown_provider_returns_empty(self):
        models = get_models_by_provider("nonexistent-provider")
        assert models == []


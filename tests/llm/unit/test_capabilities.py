"""
单元测试 - 能力推断 (12 个)

UT-C01 ~ UT-C12
"""

import pytest
from openakita.llm.capabilities import (
    infer_capabilities,
    get_provider_slug_from_base_url,
    MODEL_CAPABILITIES,
    supports_capability,
    is_thinking_only,
)


class TestUserConfig:
    """用户配置优先级测试"""
    
    def test_ut_c01_user_config_priority(self):
        """UT-C01: 用户配置优先"""
        user_config = {
            "text": True,
            "vision": True,
            "video": True,
            "tools": False,
            "thinking": True,
        }
        
        caps = infer_capabilities(
            "some-model",
            provider_slug="dashscope",
            user_config=user_config
        )
        
        # 应该包含用户配置的所有字段
        for key, val in user_config.items():
            assert caps[key] == val


class TestProviderMatching:
    """服务商匹配测试"""
    
    def test_ut_c02_exact_match(self):
        """UT-C02: 服务商精确匹配"""
        caps = infer_capabilities("qwen-plus", provider_slug="dashscope")
        
        assert caps["text"] == True
        assert caps["tools"] == True
        assert caps["thinking"] == True
    
    def test_ut_c03_prefix_match(self):
        """UT-C03: 服务商前缀匹配"""
        # qwen-plus-latest 应该匹配 qwen-plus
        caps = infer_capabilities("qwen-plus-latest", provider_slug="dashscope")
        
        assert caps["thinking"] == True
    
    def test_ut_c04_cross_provider_match(self):
        """UT-C04: 跨服务商匹配"""
        # 使用 openrouter 但模型是 gpt-4o，应该匹配 openai 的能力
        caps = infer_capabilities("gpt-4o", provider_slug="openrouter")
        
        assert caps["text"] == True
        assert caps["vision"] == True
        assert caps["tools"] == True


class TestKeywordInference:
    """关键词推断测试"""
    
    def test_ut_c05_vision_keyword(self):
        """UT-C05: Vision 关键词推断"""
        caps = infer_capabilities("unknown-model-vl-latest")
        
        assert caps["vision"] == True
    
    def test_ut_c06_thinking_keyword(self):
        """UT-C06: Thinking 关键词推断"""
        caps = infer_capabilities("unknown-thinking-model")
        
        assert caps["thinking"] == True
        
        # r1 关键词
        caps = infer_capabilities("some-r1-model")
        assert caps["thinking"] == True
    
    def test_ut_c07_video_keyword(self):
        """UT-C07: Video 关键词推断"""
        # kimi 关键词
        caps = infer_capabilities("kimi-custom-model")
        assert caps["video"] == True
        
        # gemini 关键词
        caps = infer_capabilities("gemini-custom")
        assert caps["video"] == True
    
    def test_ut_c08_tools_keyword(self):
        """UT-C08: Tools 关键词推断"""
        caps = infer_capabilities("qwen-custom-model")
        assert caps["tools"] == True
        
        caps = infer_capabilities("claude-custom")
        assert caps["tools"] == True


class TestDefaultCapabilities:
    """默认能力测试"""
    
    def test_ut_c09_default_capabilities(self):
        """UT-C09: 默认能力"""
        caps = infer_capabilities("completely-unknown-model-xyz")
        
        assert caps["text"] == True
        assert caps["vision"] == False
        assert caps["video"] == False
        assert caps["tools"] == False
        assert caps["thinking"] == False
        assert caps["audio"] == False
        assert caps["pdf"] == False


class TestSpecialFlags:
    """特殊标记测试"""
    
    def test_ut_c10_thinking_only_flag(self):
        """UT-C10: Thinking-only 标记"""
        # deepseek-r1 是 thinking-only
        assert is_thinking_only("deepseek-r1", provider_slug="deepseek")
        
        # qwq-plus 是 thinking-only
        assert is_thinking_only("qwq-plus", provider_slug="dashscope")
        
        # qwen-plus 不是 thinking-only
        assert not is_thinking_only("qwen-plus", provider_slug="dashscope")


class TestURLDetection:
    """URL 检测测试"""
    
    def test_ut_c11_base_url_detection(self):
        """UT-C11: base_url 推断服务商"""
        assert get_provider_slug_from_base_url("https://api.moonshot.cn/v1") == "moonshot"
        assert get_provider_slug_from_base_url("https://dashscope.aliyuncs.com/v1") == "dashscope"
        assert get_provider_slug_from_base_url("https://api.anthropic.com/v1") == "anthropic"
        assert get_provider_slug_from_base_url("https://openrouter.ai/api/v1") == "openrouter"
    
    def test_ut_c12_unknown_url(self):
        """UT-C12: 未知 base_url"""
        result = get_provider_slug_from_base_url("https://custom-llm.example.com/api")
        
        assert result is None


class TestHelperFunctions:
    """辅助函数测试"""
    
    def test_supports_capability(self):
        """测试 supports_capability"""
        assert supports_capability("gpt-4o", "vision", provider_slug="openai")
        assert supports_capability("gpt-4o", "tools", provider_slug="openai")
        assert not supports_capability("gpt-4", "vision", provider_slug="openai")
    
    def test_model_capabilities_structure(self):
        """测试 MODEL_CAPABILITIES 结构"""
        # 检查所有服务商都有条目
        assert "openai" in MODEL_CAPABILITIES
        assert "anthropic" in MODEL_CAPABILITIES
        assert "dashscope" in MODEL_CAPABILITIES
        assert "moonshot" in MODEL_CAPABILITIES
        
        # 检查模型有正确的基础能力字段
        for provider, models in MODEL_CAPABILITIES.items():
            for model_id, caps in models.items():
                assert "text" in caps
                assert "vision" in caps
                assert "tools" in caps
                assert "thinking" in caps
                # audio/pdf 是可选字段（通过 _normalize 在 infer 时补全）
                if "audio" in caps:
                    assert isinstance(caps["audio"], bool)
                if "pdf" in caps:
                    assert isinstance(caps["pdf"], bool)
class TestAudioPdfInference:
    """audio/pdf 能力推断测试"""

    def test_ut_c13_audio_keyword_inference(self):
        """UT-C13: audio 关键词推断"""
        # "audio" 关键词
        caps = infer_capabilities("some-audio-model")
        assert caps["audio"] == True

        # "gemini" 关键词也推断 audio
        caps = infer_capabilities("gemini-custom-model")
        assert caps["audio"] == True

    def test_ut_c14_pdf_keyword_inference(self):
        """UT-C14: pdf 关键词推断"""
        # "claude" 关键词
        caps = infer_capabilities("claude-custom-model")
        assert caps["pdf"] == True

        # "gemini" 关键词也推断 pdf
        caps = infer_capabilities("gemini-custom-model")
        assert caps["pdf"] == True

    def test_ut_c15_gpt4o_audio_exact_match(self):
        """UT-C15: gpt-4o-audio 精确匹配"""
        caps = infer_capabilities("gpt-4o-audio", provider_slug="openai")
        assert caps["audio"] == True

    def test_ut_c16_gemini_full_capabilities(self):
        """UT-C16: Gemini 模型有 audio + pdf"""
        for model_name in ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-2.5-pro"]:
            caps = infer_capabilities(model_name, provider_slug="google")
            assert caps["audio"] == True, f"{model_name} should have audio"
            assert caps["pdf"] == True, f"{model_name} should have pdf"

    def test_ut_c17_claude_pdf_capability(self):
        """UT-C17: Claude 3+ 模型有 pdf"""
        for model_name in ["claude-3-sonnet", "claude-3-opus", "claude-3-haiku", "claude-3-5-sonnet"]:
            caps = infer_capabilities(model_name, provider_slug="anthropic")
            assert caps["pdf"] == True, f"{model_name} should have pdf"

    def test_ut_c18_dashscope_audio_models(self):
        """UT-C18: DashScope 音频模型"""
        caps = infer_capabilities("qwen-audio-turbo", provider_slug="dashscope")
        assert caps["audio"] == True

        caps = infer_capabilities("qwen2-audio", provider_slug="dashscope")
        assert caps["audio"] == True

    def test_ut_c19_normalize_always_includes_audio_pdf(self):
        """UT-C19: _normalize 保底 — 返回值始终包含 audio 和 pdf"""
        # 完全未知的模型也应该有 audio 和 pdf 字段
        caps = infer_capabilities("totally-unknown-xyz-model-2025")

        assert "audio" in caps
        assert "pdf" in caps
        assert isinstance(caps["audio"], bool)
        assert isinstance(caps["pdf"], bool)
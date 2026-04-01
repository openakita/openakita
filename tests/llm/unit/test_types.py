"""
单元测试 - 类型定义 (8 个)

UT-T01 ~ UT-T08
"""

import pytest

from openakita.llm.types import (
    EndpointConfig,
    LLMRequest,
    LLMResponse,
    StopReason,
    TextBlock,
    Usage,
    VideoContent,
)


class TestLLMRequest:
    """LLMRequest 测试"""

    def test_ut_t01_basic_creation(self, sample_text_message):
        """UT-T01: LLMRequest 基本创建"""
        request = LLMRequest(messages=[sample_text_message])

        assert len(request.messages) == 1
        assert request.system == ""
        assert request.tools is None
        assert request.max_tokens == 0
        assert request.temperature == 1.0
        assert not request.enable_thinking

    def test_ut_t02_full_params(self, sample_messages, sample_tool):
        """UT-T02: LLMRequest 完整参数"""
        request = LLMRequest(
            messages=sample_messages,
            system="You are helpful.",
            tools=[sample_tool],
            max_tokens=2000,
            temperature=0.5,
            enable_thinking=True,
            stop_sequences=["END"],
            extra_params={"top_p": 0.9},
        )

        assert len(request.messages) == 3
        assert request.system == "You are helpful."
        assert len(request.tools) == 1
        assert request.max_tokens == 2000
        assert request.temperature == 0.5
        assert request.enable_thinking
        assert request.stop_sequences == ["END"]
        assert request.extra_params == {"top_p": 0.9}


class TestLLMResponse:
    """LLMResponse 测试"""

    def test_ut_t03_parse_response(self):
        """UT-T03: LLMResponse 解析"""
        response = LLMResponse(
            id="msg_123",
            content=[TextBlock(text="Hello world")],
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=10, output_tokens=5),
            model="test-model",
        )

        assert response.id == "msg_123"
        assert response.text == "Hello world"
        assert response.stop_reason == StopReason.END_TURN
        assert response.usage.total_tokens == 15
        assert response.model == "test-model"
        assert not response.has_tool_calls


class TestContentBlock:
    """ContentBlock 测试"""

    def test_ut_t04_polymorphism(self, sample_tool_use_block):
        """UT-T04: ContentBlock 多态"""
        text_block = TextBlock(text="Hello")
        tool_block = sample_tool_use_block

        assert text_block.type == "text"
        assert tool_block.type == "tool_use"

        # 测试 to_dict
        text_dict = text_block.to_dict()
        assert text_dict["type"] == "text"
        assert text_dict["text"] == "Hello"

        tool_dict = tool_block.to_dict()
        assert tool_dict["type"] == "tool_use"
        assert tool_dict["name"] == "get_weather"
        assert tool_dict["input"]["location"] == "Beijing, China"


class TestMediaContent:
    """媒体内容测试"""

    def test_ut_t05_image_content(self, sample_image_content):
        """UT-T05: ImageContent 创建"""
        assert sample_image_content.media_type == "image/png"
        assert len(sample_image_content.data) > 0

        data_url = sample_image_content.to_data_url()
        assert data_url.startswith("data:image/png;base64,")

    def test_ut_t06_video_content(self):
        """UT-T06: VideoContent 创建"""
        video = VideoContent(media_type="video/mp4", data="base64_video_data")

        assert video.media_type == "video/mp4"
        assert video.data == "base64_video_data"

        data_url = video.to_data_url()
        assert data_url.startswith("data:video/mp4;base64,")


class TestEndpointConfig:
    """EndpointConfig 测试"""

    def test_ut_t07_validation(self):
        """UT-T07: EndpointConfig 验证"""
        # 缺少必填字段会抛出 TypeError
        with pytest.raises(TypeError):
            EndpointConfig(name="test")  # 缺少其他必填字段

    def test_ut_t08_defaults(self):
        """UT-T08: EndpointConfig 默认值"""
        config = EndpointConfig(
            name="test",
            provider="anthropic",
            api_type="anthropic",
            base_url="https://api.example.com",
            api_key_env="API_KEY",
            model="test-model",
        )

        assert config.priority == 1
        assert config.max_tokens == 0
        assert config.timeout == 180
        assert config.capabilities == ["text"]
        assert config.extra_params is None
        assert config.note is None

        # 测试 has_capability
        assert config.has_capability("text")
        assert not config.has_capability("vision")

        # 测试 from_dict
        config_dict = config.to_dict()
        restored = EndpointConfig.from_dict(config_dict)
        assert restored.name == config.name
        assert restored.model == config.model

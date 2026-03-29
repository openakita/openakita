"""
单元测试 - 工具转换器 (10 个)

UT-F01 ~ UT-F10
"""

from openakita.llm.converters.tools import (
    convert_tool_calls_from_openai,
    convert_tool_calls_to_openai,
    convert_tool_result_to_openai,
    convert_tools_to_openai,
)
from openakita.llm.types import ToolResultBlock, ToolUseBlock


class TestToolDefinitionConversion:
    """工具定义转换测试"""

    def test_ut_f01_anthropic_to_internal(self, sample_tool):
        """UT-F01: Anthropic→内部: 工具定义"""
        # Tool 类就是内部格式，验证属性
        assert sample_tool.name == "get_weather"
        assert sample_tool.description == "Get the current weather for a location"
        assert "properties" in sample_tool.input_schema
        assert "location" in sample_tool.input_schema["properties"]

    def test_ut_f04_internal_to_openai(self, sample_tool):
        """UT-F04: 内部→OpenAI: 工具定义"""
        openai_tools = convert_tools_to_openai([sample_tool])

        assert len(openai_tools) == 1
        tool = openai_tools[0]

        assert tool["type"] == "function"
        assert tool["function"]["name"] == "get_weather"
        assert tool["function"]["description"] == sample_tool.description
        assert tool["function"]["parameters"] == sample_tool.input_schema


class TestToolCallConversion:
    """工具调用转换测试"""

    def test_ut_f02_anthropic_tool_use(self, sample_tool_use_block):
        """UT-F02: Anthropic→内部: 工具调用"""
        # ToolUseBlock 就是内部格式
        assert sample_tool_use_block.id == "call_123"
        assert sample_tool_use_block.name == "get_weather"
        assert sample_tool_use_block.input == {"location": "Beijing, China"}

    def test_ut_f05_internal_to_openai_calls(self, sample_tool_use_block):
        """UT-F05: 内部→OpenAI: 工具调用"""
        openai_calls = convert_tool_calls_to_openai([sample_tool_use_block])

        assert len(openai_calls) == 1
        call = openai_calls[0]

        assert call["id"] == "call_123"
        assert call["type"] == "function"
        assert call["function"]["name"] == "get_weather"
        # OpenAI 格式的 arguments 是 JSON 字符串
        assert '"location"' in call["function"]["arguments"]

    def test_ut_f07_openai_to_internal_calls(self):
        """UT-F07: OpenAI→内部: 工具调用"""
        openai_calls = [
            {
                "id": "call_456",
                "type": "function",
                "function": {
                    "name": "search",
                    "arguments": '{"query": "weather"}'
                }
            }
        ]

        tool_blocks = convert_tool_calls_from_openai(openai_calls)

        assert len(tool_blocks) == 1
        block = tool_blocks[0]

        assert isinstance(block, ToolUseBlock)
        assert block.id == "call_456"
        assert block.name == "search"
        assert block.input == {"query": "weather"}

    def test_ut_f08_string_arguments(self):
        """UT-F08: OpenAI→内部: 参数字符串解析"""
        # 测试 JSON 字符串正确解析为 dict
        openai_calls = [
            {
                "id": "call_789",
                "type": "function",
                "function": {
                    "name": "complex_tool",
                    "arguments": '{"nested": {"key": "value"}, "array": [1, 2, 3]}'
                }
            }
        ]

        tool_blocks = convert_tool_calls_from_openai(openai_calls)

        assert tool_blocks[0].input["nested"]["key"] == "value"
        assert tool_blocks[0].input["array"] == [1, 2, 3]

    def test_ut_f08b_normalizes_stringified_nested_array_fields(self):
        """UT-F08b: 按 schema 还原被字符串化的数组字段。"""
        openai_calls = [
            {
                "id": "call_plan",
                "type": "function",
                "function": {
                    "name": "create_plan",
                    "arguments": (
                        '{"task_summary":"demo",'
                        '"steps":"[{\\\"id\\\":\\\"step_1\\\",\\\"description\\\":\\\"first\\\"},'
                        '{\\\"id\\\":\\\"step_2\\\",\\\"description\\\":\\\"second\\\"}]"}'
                    ),
                },
            }
        ]

        tool_blocks = convert_tool_calls_from_openai(openai_calls)

        assert tool_blocks[0].input["task_summary"] == "demo"
        assert isinstance(tool_blocks[0].input["steps"], list)
        assert tool_blocks[0].input["steps"][0]["description"] == "first"
        assert tool_blocks[0].input["steps"][1]["description"] == "second"


class TestToolResultConversion:
    """工具结果转换测试"""

    def test_ut_f03_anthropic_tool_result(self):
        """UT-F03: Anthropic→内部: 工具结果"""
        result = ToolResultBlock(
            tool_use_id="call_123",
            content="The weather is sunny.",
            is_error=False,
        )

        assert result.tool_use_id == "call_123"
        assert result.content == "The weather is sunny."
        assert not result.is_error

    def test_ut_f06_internal_to_openai_result(self):
        """UT-F06: 内部→OpenAI: 工具结果"""
        openai_msg = convert_tool_result_to_openai(
            tool_use_id="call_123",
            content="Result data",
        )

        assert openai_msg["role"] == "tool"
        assert openai_msg["tool_call_id"] == "call_123"
        assert openai_msg["content"] == "Result data"


class TestEdgeCases:
    """边缘情况测试"""

    def test_ut_f09_no_tool_calls(self):
        """UT-F09: 无工具调用"""
        tool_blocks = convert_tool_calls_from_openai([])

        assert tool_blocks == []

    def test_ut_f10_multiple_tool_calls(self):
        """UT-F10: 多工具调用"""
        openai_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "tool1", "arguments": "{}"}
            },
            {
                "id": "call_2",
                "type": "function",
                "function": {"name": "tool2", "arguments": '{"x": 1}'}
            },
            {
                "id": "call_3",
                "type": "function",
                "function": {"name": "tool3", "arguments": '{"y": 2}'}
            },
        ]

        tool_blocks = convert_tool_calls_from_openai(openai_calls)

        assert len(tool_blocks) == 3
        assert tool_blocks[0].name == "tool1"
        assert tool_blocks[1].name == "tool2"
        assert tool_blocks[2].name == "tool3"

"""
OpenAI 和 Anthropic API 格式转换示例

本文件演示如何使用 APIConverter 进行格式转换
"""

import json
from typing import Any, Dict, List

# 假设 APIConverter 类已经在项目中实现
# 这里提供一个简化版本用于演示


class APIConverter:
    """OpenAI 和 Anthropic API 格式转换工具"""
    
    OPENAI_TO_ANTHROPIC_FINISH_REASON = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
        "function_call": "tool_use"
    }
    
    ANTHROPIC_TO_OPENAI_STOP_REASON = {
        "end_turn": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "stop_sequence": "stop"
    }
    
    @staticmethod
    def anthropic_to_openai_request(anthropic_request: Dict[str, Any]) -> Dict[str, Any]:
        """将 Anthropic 请求转换为 OpenAI 格式"""
        openai_messages = []
        
        # 转换 system
        if "system" in anthropic_request:
            openai_messages.append({
                "role": "system",
                "content": anthropic_request["system"]
            })
        
        # 转换 messages
        for msg in anthropic_request.get("messages", []):
            role = msg["role"]
            content = msg["content"]
            
            # 处理 content 数组
            if isinstance(content, list):
                converted_content = []
                for part in content:
                    if part.get("type") == "text":
                        converted_content.append({
                            "type": "text",
                            "text": part.get("text", "")
                        })
                    elif part.get("type") == "image":
                        source = part.get("source", {})
                        if source.get("type") == "base64":
                            converted_content.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{source.get('media_type')};base64,{source.get('data')}"
                                }
                            })
                content = converted_content if converted_content else content[0].get("text", "") if content else ""
            
            openai_messages.append({
                "role": role,
                "content": content
            })
        
        result = {
            "messages": openai_messages,
            "max_tokens": anthropic_request.get("max_tokens", 1024)
        }
        
        # 转换 tools
        if "tools" in anthropic_request:
            result["tools"] = APIConverter.anthropic_tools_to_openai(anthropic_request["tools"])
        
        return result
    
    @staticmethod
    def openai_to_anthropic_request(openai_request: Dict[str, Any]) -> Dict[str, Any]:
        """将 OpenAI 请求转换为 Anthropic 格式"""
        anthropic_request = {
            "messages": [],
            "max_tokens": openai_request.get("max_tokens", 1024)
        }
        
        # 提取 system 消息
        messages = openai_request.get("messages", [])
        system_messages = [msg for msg in messages if msg.get("role") == "system"]
        if system_messages:
            anthropic_request["system"] = system_messages[0].get("content", "")
            messages = [msg for msg in messages if msg.get("role") != "system"]
        
        # 转换其他消息
        for msg in messages:
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            
            content = msg.get("content")
            if isinstance(content, list):
                converted_content = []
                for part in content:
                    if part.get("type") == "text":
                        converted_content.append({
                            "type": "text",
                            "text": part.get("text", "")
                        })
                    elif part.get("type") == "image_url":
                        image_url = part.get("image_url", {}).get("url", "")
                        if image_url.startswith("data:"):
                            parts = image_url.split(",", 1)
                            if len(parts) == 2:
                                header = parts[0]
                                data = parts[1]
                                media_type = header.split(";")[0].split(":")[1]
                                converted_content.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media_type,
                                        "data": data
                                    }
                                })
                content = converted_content if converted_content else content
            else:
                content = [{"type": "text", "text": str(content)}]
            
            anthropic_request["messages"].append({
                "role": role,
                "content": content
            })
        
        # 转换 tools
        if "tools" in openai_request:
            anthropic_request["tools"] = APIConverter.openai_tools_to_anthropic(openai_request["tools"])
        
        return anthropic_request
    
    @staticmethod
    def anthropic_tools_to_openai(anthropic_tools: List[Dict]) -> List[Dict]:
        """将 Anthropic tools 转换为 OpenAI 格式"""
        openai_tools = []
        for tool in anthropic_tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {})
                }
            })
        return openai_tools
    
    @staticmethod
    def openai_tools_to_anthropic(openai_tools: List[Dict]) -> List[Dict]:
        """将 OpenAI tools 转换为 Anthropic 格式"""
        anthropic_tools = []
        for tool in openai_tools:
            if tool.get("type") == "function":
                function = tool.get("function", {})
                anthropic_tools.append({
                    "name": function.get("name", ""),
                    "description": function.get("description", ""),
                    "input_schema": function.get("parameters", {})
                })
        return anthropic_tools
    
    @staticmethod
    def convert_finish_reason(openai_reason: str) -> str:
        """转换 finish_reason"""
        return APIConverter.OPENAI_TO_ANTHROPIC_FINISH_REASON.get(openai_reason, "end_turn")
    
    @staticmethod
    def convert_stop_reason(anthropic_reason: str) -> str:
        """转换 stop_reason"""
        return APIConverter.ANTHROPIC_TO_OPENAI_STOP_REASON.get(anthropic_reason, "stop")


# ========== 示例 1: 基本消息格式转换 ==========

def example_1_basic_message_conversion():
    """示例 1: 基本消息格式转换"""
    print("=" * 60)
    print("示例 1: 基本消息格式转换")
    print("=" * 60)
    
    # Anthropic 格式请求
    anthropic_request = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "system": "You are a helpful assistant.",
        "messages": [
            {
                "role": "user",
                "content": "Hello!"
            }
        ]
    }
    
    print("\n原始 Anthropic 请求:")
    print(json.dumps(anthropic_request, indent=2, ensure_ascii=False))
    
    # 转换为 OpenAI 格式
    openai_request = APIConverter.anthropic_to_openai_request(anthropic_request)
    
    print("\n转换后的 OpenAI 请求:")
    print(json.dumps(openai_request, indent=2, ensure_ascii=False))
    
    # 再转换回 Anthropic 格式
    back_to_anthropic = APIConverter.openai_to_anthropic_request(openai_request)
    
    print("\n转换回 Anthropic 格式:")
    print(json.dumps(back_to_anthropic, indent=2, ensure_ascii=False))


# ========== 示例 2: 工具定义转换 ==========

def example_2_tools_conversion():
    """示例 2: 工具定义转换"""
    print("\n" + "=" * 60)
    print("示例 2: 工具定义转换")
    print("=" * 60)
    
    # Anthropic 工具定义
    anthropic_tools = [
        {
            "name": "get_weather",
            "description": "Get the current weather in a given location",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and state, e.g. San Francisco, CA"
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "The unit of temperature"
                    }
                },
                "required": ["location"]
            }
        }
    ]
    
    print("\n原始 Anthropic 工具定义:")
    print(json.dumps(anthropic_tools, indent=2, ensure_ascii=False))
    
    # 转换为 OpenAI 格式
    openai_tools = APIConverter.anthropic_tools_to_openai(anthropic_tools)
    
    print("\n转换后的 OpenAI 工具定义:")
    print(json.dumps(openai_tools, indent=2, ensure_ascii=False))
    
    # 再转换回 Anthropic 格式
    back_to_anthropic = APIConverter.openai_tools_to_anthropic(openai_tools)
    
    print("\n转换回 Anthropic 格式:")
    print(json.dumps(back_to_anthropic, indent=2, ensure_ascii=False))


# ========== 示例 3: 多模态消息转换 ==========

def example_3_multimodal_conversion():
    """示例 3: 多模态消息转换"""
    print("\n" + "=" * 60)
    print("示例 3: 多模态消息转换")
    print("=" * 60)
    
    # Anthropic 多模态消息
    anthropic_message = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "What's in this image?"
            },
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": "/9j/4AAQSkZJRg..."  # 简化的 base64 数据
                }
            }
        ]
    }
    
    print("\n原始 Anthropic 多模态消息:")
    print(json.dumps(anthropic_message, indent=2, ensure_ascii=False))
    
    # 转换为 OpenAI 格式
    openai_message = {
        "role": "user",
        "content": []
    }
    
    for part in anthropic_message["content"]:
        if part.get("type") == "text":
            openai_message["content"].append({
                "type": "text",
                "text": part.get("text", "")
            })
        elif part.get("type") == "image":
            source = part.get("source", {})
            if source.get("type") == "base64":
                openai_message["content"].append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{source.get('media_type')};base64,{source.get('data')}"
                    }
                })
    
    print("\n转换后的 OpenAI 多模态消息:")
    print(json.dumps(openai_message, indent=2, ensure_ascii=False))


# ========== 示例 4: 工具调用响应转换 ==========

def example_4_tool_call_response_conversion():
    """示例 4: 工具调用响应转换"""
    print("\n" + "=" * 60)
    print("示例 4: 工具调用响应转换")
    print("=" * 60)
    
    # OpenAI 工具调用响应
    openai_response = {
        "id": "chatcmpl-123",
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"location": "San Francisco, CA", "unit": "fahrenheit"}'
                        }
                    }
                ]
            },
            "finish_reason": "tool_calls"
        }]
    }
    
    print("\n原始 OpenAI 工具调用响应:")
    print(json.dumps(openai_response, indent=2, ensure_ascii=False))
    
    # 转换为 Anthropic 格式
    choice = openai_response["choices"][0]
    message = choice["message"]
    
    content = []
    if message.get("tool_calls"):
        for tool_call in message["tool_calls"]:
            arguments = json.loads(tool_call["function"]["arguments"])
            content.append({
                "type": "tool_use",
                "id": tool_call["id"],
                "name": tool_call["function"]["name"],
                "input": arguments
            })
    
    anthropic_response = {
        "id": openai_response["id"],
        "type": "message",
        "role": "assistant",
        "content": content,
        "stop_reason": APIConverter.convert_finish_reason(choice["finish_reason"])
    }
    
    print("\n转换后的 Anthropic 工具调用响应:")
    print(json.dumps(anthropic_response, indent=2, ensure_ascii=False))


# ========== 主函数 ==========

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("OpenAI 和 Anthropic API 格式转换示例")
    print("=" * 60)
    
    # 运行所有示例
    example_1_basic_message_conversion()
    example_2_tools_conversion()
    example_3_multimodal_conversion()
    example_4_tool_call_response_conversion()
    
    print("\n" + "=" * 60)
    print("所有示例运行完成！")
    print("=" * 60)

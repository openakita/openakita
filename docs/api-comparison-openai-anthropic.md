# OpenAI 与 Anthropic API 差异对比调研

本文档详细对比 OpenAI 和 Anthropic (Claude) API 在消息格式、工具调用、多模态、响应格式和流式输出等方面的差异。

## 1. 消息格式 (Messages Format)

### 1.1 OpenAI Messages 结构

OpenAI 使用 `messages` 数组，每个消息包含 `role` 和 `content` 字段。

**基本结构：**
```json
{
  "model": "gpt-4",
  "messages": [
    {
      "role": "system",
      "content": "You are a helpful assistant."
    },
    {
      "role": "user",
      "content": "Hello!"
    },
    {
      "role": "assistant",
      "content": "Hi there! How can I help you?"
    }
  ],
  "max_tokens": 1000
}
```

**支持的角色：**
- `system`: 系统提示词（可选，但建议放在第一条）
- `user`: 用户消息
- `assistant`: 助手回复
- `tool`: 工具调用结果（用于 function calling）

**多模态内容格式：**
```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "What's in this image?"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,/9j/4AAQSkZJRg..."
      }
    }
  ]
}
```

### 1.2 Anthropic Messages 结构

Anthropic 使用 `messages` 数组，但结构略有不同。

**基本结构：**
```json
{
  "model": "claude-3-5-sonnet-20241022",
  "max_tokens": 1024,
  "system": "You are a helpful assistant.",
  "messages": [
    {
      "role": "user",
      "content": "Hello!"
    },
    {
      "role": "assistant",
      "content": "Hi there! How can I help you?"
    }
  ]
}
```

**关键差异：**
- `system` 是**独立参数**，不在 `messages` 数组中
- `messages` 中只包含 `user` 和 `assistant` 角色
- 消息必须交替出现（user → assistant → user → assistant）

**多模态内容格式：**
```json
{
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
        "data": "/9j/4AAQSkZJRg..."
      }
    }
  ]
}
```

### 1.3 System Prompt 处理差异

| 特性 | OpenAI | Anthropic |
|------|--------|-----------|
| System 位置 | `messages` 数组中的第一条消息 | 独立的 `system` 参数 |
| 是否必需 | 可选 | 可选 |
| 是否可多次出现 | 可以（但通常只放一条） | 只能有一个 `system` 参数 |
| 长度限制 | 无明确限制 | 建议不超过 200,000 tokens |

**OpenAI 示例：**
```json
{
  "messages": [
    {
      "role": "system",
      "content": "You are a helpful assistant."
    },
    {
      "role": "user",
      "content": "Hello"
    }
  ]
}
```

**Anthropic 示例：**
```json
{
  "system": "You are a helpful assistant.",
  "messages": [
    {
      "role": "user",
      "content": "Hello"
    }
  ]
}
```

### 1.4 格式转换示例

**Anthropic → OpenAI：**
```python
def anthropic_to_openai(anthropic_request):
    """将 Anthropic 格式转换为 OpenAI 格式"""
    openai_messages = []
    
    # 将 system 参数转换为第一条 system 消息
    if "system" in anthropic_request:
        openai_messages.append({
            "role": "system",
            "content": anthropic_request["system"]
        })
    
    # 转换 messages（需要处理 content 数组）
    for msg in anthropic_request.get("messages", []):
        role = msg["role"]
        content = msg["content"]
        
        # 如果 content 是数组，需要转换格式
        if isinstance(content, list):
            converted_content = []
            for part in content:
                if part.get("type") == "text":
                    converted_content.append({
                        "type": "text",
                        "text": part.get("text", "")
                    })
                elif part.get("type") == "image":
                    # 转换图片格式
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
    
    return {
        "messages": openai_messages,
        "max_tokens": anthropic_request.get("max_tokens", 1024)
    }
```

**OpenAI → Anthropic：**
```python
def openai_to_anthropic(openai_request):
    """将 OpenAI 格式转换为 Anthropic 格式"""
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
            continue  # Anthropic 只支持 user 和 assistant
        
        content = msg.get("content")
        
        # 如果 content 是数组，需要转换格式
        if isinstance(content, list):
            converted_content = []
            for part in content:
                if part.get("type") == "text":
                    converted_content.append({
                        "type": "text",
                        "text": part.get("text", "")
                    })
                elif part.get("type") == "image_url":
                    # 转换图片格式
                    image_url = part.get("image_url", {}).get("url", "")
                    if image_url.startswith("data:"):
                        # 解析 data URL: data:image/jpeg;base64,xxx
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
            # 简单文本内容
            content = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
        
        anthropic_request["messages"].append({
            "role": role,
            "content": content
        })
    
    return anthropic_request
```

## 2. 工具调用 (Function Calling / Tool Use)

### 2.1 OpenAI Tools 定义格式

**工具定义：**
```json
{
  "model": "gpt-4",
  "messages": [
    {
      "role": "user",
      "content": "What's the weather in San Francisco?"
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get the current weather in a given location",
        "parameters": {
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
    }
  ],
  "tool_choice": "auto"
}
```

**工具调用响应格式：**
```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "created": 1677652288,
  "model": "gpt-4",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_abc123",
            "type": "function",
            "function": {
              "name": "get_weather",
              "arguments": "{\"location\": \"San Francisco, CA\", \"unit\": \"fahrenheit\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {
    "prompt_tokens": 82,
    "completion_tokens": 18,
    "total_tokens": 100
  }
}
```

**工具结果返回格式：**
```json
{
  "role": "tool",
  "tool_call_id": "call_abc123",
  "content": "72 degrees and sunny"
}
```

### 2.2 Anthropic Tools 定义格式

**工具定义：**
```json
{
  "model": "claude-3-5-sonnet-20241022",
  "max_tokens": 1024,
  "messages": [
    {
      "role": "user",
      "content": "What's the weather in San Francisco?"
    }
  ],
  "tools": [
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
}
```

**工具调用响应格式（在 content blocks 中）：**
```json
{
  "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "tool_use",
      "id": "toolu_01A09q90qw90lq917835lq9",
      "name": "get_weather",
      "input": {
        "location": "San Francisco, CA",
        "unit": "fahrenheit"
      }
    }
  ],
  "model": "claude-3-5-sonnet-20241022",
  "stop_reason": "tool_use",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 25,
    "output_tokens": 4
  }
}
```

**工具结果返回格式：**
```json
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "toolu_01A09q90qw90lq917835lq9",
      "content": "72 degrees and sunny"
    }
  ]
}
```

### 2.3 关键差异对比

| 特性 | OpenAI | Anthropic |
|------|--------|-----------|
| 工具定义位置 | `tools` 数组 | `tools` 数组 |
| 工具定义结构 | `type: "function"` + `function` 对象 | 直接在 `tools` 数组中 |
| 参数定义 | `parameters` (JSON Schema) | `input_schema` (JSON Schema) |
| 工具调用位置 | `message.tool_calls` 数组 | `content` 数组中的 `tool_use` block |
| 工具调用 ID | `tool_call_id` | `id` (在 tool_use block 中) |
| 参数格式 | JSON 字符串 (`arguments`) | JSON 对象 (`input`) |
| 工具结果角色 | `role: "tool"` | `role: "user"` + `tool_result` block |
| 工具结果关联 | `tool_call_id` | `tool_use_id` |
| 停止原因 | `finish_reason: "tool_calls"` | `stop_reason: "tool_use"` |

### 2.4 格式转换示例

**OpenAI Tools → Anthropic Tools：**
```python
def openai_tools_to_anthropic(openai_tools):
    """将 OpenAI tools 格式转换为 Anthropic 格式"""
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
```

**Anthropic Tools → OpenAI Tools：**
```python
def anthropic_tools_to_openai(anthropic_tools):
    """将 Anthropic tools 格式转换为 OpenAI 格式"""
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
```

**工具调用响应转换：**

**OpenAI → Anthropic：**
```python
def openai_tool_calls_to_anthropic(openai_response):
    """将 OpenAI tool_calls 转换为 Anthropic tool_use blocks"""
    tool_use_blocks = []
    
    message = openai_response.choices[0].message
    if message.tool_calls:
        for tool_call in message.tool_calls:
            import json
            arguments = json.loads(tool_call.function.arguments) if isinstance(tool_call.function.arguments, str) else tool_call.function.arguments
            
            tool_use_blocks.append({
                "type": "tool_use",
                "id": tool_call.id,
                "name": tool_call.function.name,
                "input": arguments
            })
    
    return tool_use_blocks
```

**Anthropic → OpenAI：**
```python
def anthropic_tool_use_to_openai(anthropic_response):
    """将 Anthropic tool_use blocks 转换为 OpenAI tool_calls"""
    tool_calls = []
    
    for block in anthropic_response.content:
        if block.type == "tool_use":
            import json
            tool_calls.append({
                "id": block.id,
                "type": "function",
                "function": {
                    "name": block.name,
                    "arguments": json.dumps(block.input)
                }
            })
    
    return tool_calls
```

**工具结果转换：**

**OpenAI Tool Result → Anthropic Tool Result：**
```python
def openai_tool_result_to_anthropic(tool_message):
    """将 OpenAI tool 消息转换为 Anthropic tool_result"""
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_message.get("tool_call_id"),
                "content": tool_message.get("content", "")
            }
        ]
    }
```

**Anthropic Tool Result → OpenAI Tool Result：**
```python
def anthropic_tool_result_to_openai(anthropic_message):
    """将 Anthropic tool_result 转换为 OpenAI tool 消息"""
    tool_results = []
    
    for block in anthropic_message.get("content", []):
        if block.get("type") == "tool_result":
            tool_results.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id"),
                "content": block.get("content", "")
            })
    
    return tool_results
```

## 3. 多模态/图片 (Vision)

### 3.1 OpenAI 图片输入格式

**Base64 编码：**
```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "What's in this image?"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,/9j/4AAQSkZJRg..."
      }
    }
  ]
}
```

**URL 方式：**
```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "What's in this image?"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "https://example.com/image.jpg"
      }
    }
  ]
}
```

**支持的格式：**
- JPEG
- PNG
- GIF
- WebP

### 3.2 Anthropic 图片输入格式

**Base64 编码：**
```json
{
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
        "data": "/9j/4AAQSkZJRg..."
      }
    }
  ]
}
```

**支持的格式：**
- JPEG
- PNG
- GIF
- WebP
- PDF（Anthropic 特有）

**PDF 支持示例：**
```json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "Summarize this PDF"
    },
    {
      "type": "image",
      "source": {
        "type": "base64",
        "media_type": "application/pdf",
        "data": "JVBERi0xLjQKJeLjz9MK..."
      }
    }
  ]
}
```

### 3.3 关键差异

| 特性 | OpenAI | Anthropic |
|------|--------|-----------|
| 图片类型字段 | `image_url` | `image` |
| Base64 格式 | `data:image/jpeg;base64,xxx` | `source.type: "base64"` + `data: "xxx"` |
| URL 支持 | ✅ 支持 | ❌ 不支持（仅 base64） |
| PDF 支持 | ❌ 不支持 | ✅ 支持 |
| Media Type | 在 data URL 中 | 独立的 `media_type` 字段 |

### 3.4 格式转换示例

**OpenAI Image → Anthropic Image：**
```python
def openai_image_to_anthropic(image_block):
    """将 OpenAI image_url 转换为 Anthropic image"""
    image_url = image_block.get("image_url", {}).get("url", "")
    
    if image_url.startswith("data:"):
        # 解析 data URL
        parts = image_url.split(",", 1)
        if len(parts) == 2:
            header = parts[0]  # data:image/jpeg;base64
            data = parts[1]
            
            # 提取 media_type
            media_type = header.split(";")[0].split(":")[1]  # image/jpeg
            
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data
                }
            }
    else:
        # URL 方式 - Anthropic 不支持，需要先下载并转换为 base64
        raise ValueError("Anthropic API does not support image URLs. Please convert to base64 first.")
    
    return None
```

**Anthropic Image → OpenAI Image：**
```python
def anthropic_image_to_openai(image_block):
    """将 Anthropic image 转换为 OpenAI image_url"""
    source = image_block.get("source", {})
    
    if source.get("type") == "base64":
        media_type = source.get("media_type", "image/jpeg")
        data = source.get("data", "")
        
        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:{media_type};base64,{data}"
            }
        }
    
    return None
```

## 4. 响应格式 (Response Format)

### 4.1 OpenAI 响应格式

**基本响应：**
```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "created": 1677652288,
  "model": "gpt-4",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 8,
    "total_tokens": 18
  }
}
```

**带工具调用的响应：**
```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "created": 1677652288,
  "model": "gpt-4",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_abc123",
            "type": "function",
            "function": {
              "name": "get_weather",
              "arguments": "{\"location\": \"San Francisco\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {
    "prompt_tokens": 82,
    "completion_tokens": 18,
    "total_tokens": 100
  }
}
```

**Finish Reasons：**
- `stop`: 正常完成
- `length`: 达到 max_tokens 限制
- `tool_calls`: 需要调用工具
- `content_filter`: 内容被过滤
- `function_call`: 旧版 function calling（已废弃）

### 4.2 Anthropic 响应格式

**基本响应：**
```json
{
  "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "Hello! How can I help you today?"
    }
  ],
  "model": "claude-3-5-sonnet-20241022",
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 10,
    "output_tokens": 8
  }
}
```

**带工具调用的响应：**
```json
{
  "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "I'll check the weather for you."
    },
    {
      "type": "tool_use",
      "id": "toolu_01A09q90qw90lq917835lq9",
      "name": "get_weather",
      "input": {
        "location": "San Francisco"
      }
    }
  ],
  "model": "claude-3-5-sonnet-20241022",
  "stop_reason": "tool_use",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 25,
    "output_tokens": 4
  }
}
```

**Stop Reasons：**
- `end_turn`: 正常完成一轮对话
- `max_tokens`: 达到 max_tokens 限制
- `tool_use`: 需要调用工具
- `stop_sequence`: 遇到停止序列

### 4.3 关键差异对比

| 特性 | OpenAI | Anthropic |
|------|--------|-----------|
| 响应结构 | `choices[0].message` | `content` 数组 |
| 文本内容位置 | `message.content` (字符串) | `content[].text` (在 text block 中) |
| 工具调用位置 | `message.tool_calls` (数组) | `content[]` 中的 `tool_use` blocks |
| 停止原因字段 | `finish_reason` | `stop_reason` |
| 停止原因值 | `stop`, `length`, `tool_calls` | `end_turn`, `max_tokens`, `tool_use` |
| Token 使用字段 | `usage.prompt_tokens`, `usage.completion_tokens` | `usage.input_tokens`, `usage.output_tokens` |
| 响应 ID | `id` (字符串) | `id` (字符串) |

### 4.4 Stop Reason / Finish Reason 映射

```python
OPENAI_TO_ANTHROPIC_FINISH_REASON = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",  # 近似映射
    "function_call": "tool_use"  # 旧版 function calling
}

ANTHROPIC_TO_OPENAI_STOP_REASON = {
    "end_turn": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "stop_sequence": "stop"  # 近似映射
}

def convert_finish_reason(openai_reason):
    """将 OpenAI finish_reason 转换为 Anthropic stop_reason"""
    return OPENAI_TO_ANTHROPIC_FINISH_REASON.get(openai_reason, "end_turn")

def convert_stop_reason(anthropic_reason):
    """将 Anthropic stop_reason 转换为 OpenAI finish_reason"""
    return ANTHROPIC_TO_OPENAI_STOP_REASON.get(anthropic_reason, "stop")
```

### 4.5 响应格式转换示例

**OpenAI Response → Anthropic Response：**
```python
def openai_response_to_anthropic(openai_response):
    """将 OpenAI 响应转换为 Anthropic 格式"""
    choice = openai_response.choices[0]
    message = choice.message
    
    # 构建 content blocks
    content = []
    
    # 添加文本内容
    if message.content:
        content.append({
            "type": "text",
            "text": message.content
        })
    
    # 添加工具调用
    if message.tool_calls:
        import json
        for tool_call in message.tool_calls:
            arguments = json.loads(tool_call.function.arguments) if isinstance(tool_call.function.arguments, str) else tool_call.function.arguments
            content.append({
                "type": "tool_use",
                "id": tool_call.id,
                "name": tool_call.function.name,
                "input": arguments
            })
    
    # 转换 finish_reason
    stop_reason = convert_finish_reason(choice.finish_reason)
    
    return {
        "id": openai_response.id,
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": openai_response.model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": openai_response.usage.prompt_tokens,
            "output_tokens": openai_response.usage.completion_tokens
        }
    }
```

**Anthropic Response → OpenAI Response：**
```python
def anthropic_response_to_openai(anthropic_response):
    """将 Anthropic 响应转换为 OpenAI 格式"""
    # 提取文本内容
    text_content = ""
    tool_calls = []
    
    for block in anthropic_response.content:
        if block.type == "text":
            text_content += block.text
        elif block.type == "tool_use":
            import json
            tool_calls.append({
                "id": block.id,
                "type": "function",
                "function": {
                    "name": block.name,
                    "arguments": json.dumps(block.input)
                }
            })
    
    # 构建 message
    message = {
        "role": "assistant",
        "content": text_content if text_content else None
    }
    
    if tool_calls:
        message["tool_calls"] = tool_calls
    
    # 转换 stop_reason
    finish_reason = convert_stop_reason(anthropic_response.stop_reason)
    
    return {
        "id": anthropic_response.id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": anthropic_response.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason
            }
        ],
        "usage": {
            "prompt_tokens": anthropic_response.usage.input_tokens,
            "completion_tokens": anthropic_response.usage.output_tokens,
            "total_tokens": anthropic_response.usage.input_tokens + anthropic_response.usage.output_tokens
        }
    }
```

## 5. 流式输出 (Streaming)

### 5.1 OpenAI 流式格式

**请求参数：**
```python
response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True  # 启用流式输出
)
```

**流式响应格式（SSE）：**
```
data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1694268190,"model":"gpt-4","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1694268190,"model":"gpt-4","choices":[{"index":0,"delta":{"content":" there"},"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1694268190,"model":"gpt-4","choices":[{"index":0,"delta":{"content":"!"},"finish_reason":"stop"}]}

data: [DONE]
```

**Python SDK 使用：**
```python
stream = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content is not None:
        print(chunk.choices[0].delta.content, end="")
```

**工具调用的流式响应：**
```python
# 流式响应中，工具调用会分多个 chunk 发送
for chunk in stream:
    if chunk.choices[0].delta.tool_calls:
        for tool_call_delta in chunk.choices[0].delta.tool_calls:
            # tool_call_delta 包含部分工具调用信息
            # 需要累积多个 chunk 才能组成完整的 tool_call
            pass
```

### 5.2 Anthropic 流式格式

**请求参数：**
```python
with client.messages.stream(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}]
) as stream:
    for text in stream.text_stream:
        print(text, end="")
```

**流式事件类型：**
- `message_start`: 消息开始
- `content_block_start`: 内容块开始（text 或 tool_use）
- `content_block_delta`: 内容增量（文本片段）
- `content_block_stop`: 内容块结束
- `message_delta`: 消息增量（usage 信息）
- `message_stop`: 消息结束

**流式响应示例（SSE）：**
```
event: message_start
data: {"type":"message_start","message":{"id":"msg_01XFDUDYJgAACzvnptvVoYEL","type":"message","role":"assistant","content":[],"model":"claude-3-5-sonnet-20241022","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":10,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" there"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"!"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":3}}

event: message_stop
data: {"type":"message_stop"}
```

**Python SDK 使用：**
```python
# 方式 1: 使用 text_stream（最简单）
with client.messages.stream(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}]
) as stream:
    for text in stream.text_stream:
        print(text, end="")

# 方式 2: 使用事件流（更细粒度控制）
with client.messages.stream(...) as stream:
    for event in stream:
        if event.type == "content_block_delta":
            if event.delta.type == "text_delta":
                print(event.delta.text, end="")
```

**工具调用的流式响应：**
```python
with client.messages.stream(...) as stream:
    for event in stream:
        if event.type == "content_block_start":
            if event.content_block.type == "tool_use":
                # 工具调用开始
                tool_id = event.content_block.id
                tool_name = event.content_block.name
        elif event.type == "content_block_delta":
            if event.delta.type == "input_json_delta":
                # 工具参数增量
                partial_input = event.delta.partial_json
```

### 5.3 关键差异对比

| 特性 | OpenAI | Anthropic |
|------|--------|-----------|
| 流式参数 | `stream=True` | `stream()` 方法 |
| 事件类型 | 单一 chunk 格式 | 多种事件类型（message_start, content_block_delta 等） |
| 文本增量 | `chunk.choices[0].delta.content` | `event.delta.text` (在 content_block_delta 中) |
| 工具调用流式 | `delta.tool_calls` (需要累积) | `content_block_start` + `input_json_delta` |
| 完成标记 | `[DONE]` | `message_stop` 事件 |
| SDK 抽象 | 直接迭代 chunks | 提供 `text_stream` 和事件流两种方式 |

### 5.4 流式格式转换示例

**OpenAI Stream → Anthropic Stream（模拟）：**
```python
def openai_stream_to_anthropic_events(openai_stream):
    """将 OpenAI 流式响应转换为 Anthropic 事件格式（模拟）"""
    message_id = None
    content_blocks = []
    current_block_index = 0
    
    for chunk in openai_stream:
        if not message_id:
            message_id = chunk.id
            # 发送 message_start
            yield {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": chunk.model,
                    "stop_reason": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0}
                }
            }
        
        choice = chunk.choices[0]
        delta = choice.delta
        
        # 处理文本内容
        if delta.content:
            # 如果是新的内容块，发送 content_block_start
            if current_block_index >= len(content_blocks):
                content_blocks.append({"type": "text", "text": ""})
                yield {
                    "type": "content_block_start",
                    "index": current_block_index,
                    "content_block": {"type": "text", "text": ""}
                }
            
            # 发送文本增量
            yield {
                "type": "content_block_delta",
                "index": current_block_index,
                "delta": {
                    "type": "text_delta",
                    "text": delta.content
                }
            }
            content_blocks[current_block_index]["text"] += delta.content
        
        # 处理工具调用
        if delta.tool_calls:
            # OpenAI 的工具调用需要累积多个 chunk
            # 这里简化处理
            pass
        
        # 处理完成
        if choice.finish_reason:
            # 结束当前内容块
            if current_block_index < len(content_blocks):
                yield {
                    "type": "content_block_stop",
                    "index": current_block_index
                }
            
            # 发送 message_delta 和 message_stop
            yield {
                "type": "message_delta",
                "delta": {
                    "stop_reason": convert_finish_reason(choice.finish_reason),
                    "stop_sequence": None
                },
                "usage": {
                    "output_tokens": chunk.usage.completion_tokens if hasattr(chunk, 'usage') else 0
                }
            }
            
            yield {"type": "message_stop"}
```

**Anthropic Stream → OpenAI Stream（模拟）：**
```python
def anthropic_stream_to_openai_chunks(anthropic_stream):
    """将 Anthropic 流式响应转换为 OpenAI chunk 格式（模拟）"""
    message_id = None
    model = None
    accumulated_content = ""
    tool_calls_accumulator = {}
    
    for event in anthropic_stream:
        if event.type == "message_start":
            message_id = event.message.id
            model = event.message.model
        
        elif event.type == "content_block_start":
            if event.content_block.type == "tool_use":
                # 开始新的工具调用
                tool_id = event.content_block.id
                tool_calls_accumulator[tool_id] = {
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": event.content_block.name,
                        "arguments": ""
                    }
                }
        
        elif event.type == "content_block_delta":
            if event.delta.type == "text_delta":
                # 文本增量
                accumulated_content += event.delta.text
                yield {
                    "id": message_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": event.delta.text},
                        "finish_reason": None
                    }]
                }
            elif event.delta.type == "input_json_delta":
                # 工具参数增量
                # OpenAI 格式需要累积完整的 arguments
                pass
        
        elif event.type == "content_block_stop":
            # 内容块结束
            pass
        
        elif event.type == "message_delta":
            # 发送最终 chunk（如果有剩余内容）
            if accumulated_content:
                yield {
                    "id": message_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": convert_stop_reason(event.delta.stop_reason)
                    }]
                }
        
        elif event.type == "message_stop":
            # 发送 [DONE]
            yield "[DONE]"
```

## 6. 完整转换工具示例

以下是一个完整的转换工具类，包含所有格式转换功能：

```python
import json
import time
from typing import Any, Dict, List, Optional

class APIConverter:
    """OpenAI 和 Anthropic API 格式转换工具"""
    
    # Finish reason 映射
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
```

## 7. 总结

### 主要差异总结

1. **消息格式**
   - OpenAI: system 在 messages 数组中
   - Anthropic: system 是独立参数

2. **工具调用**
   - OpenAI: `tool_calls` 数组，参数是 JSON 字符串
   - Anthropic: `tool_use` blocks，参数是 JSON 对象

3. **多模态**
   - OpenAI: 支持 URL 和 base64
   - Anthropic: 仅支持 base64，但支持 PDF

4. **响应格式**
   - OpenAI: `choices[0].message`
   - Anthropic: `content` 数组（blocks）

5. **流式输出**
   - OpenAI: 简单的 chunk 格式
   - Anthropic: 事件驱动的流式格式

### 使用建议

1. **统一内部格式**：建议在应用内部使用统一的格式（如 Anthropic 格式），然后转换为目标 API 格式
2. **工具调用处理**：注意参数格式差异（JSON 字符串 vs JSON 对象）
3. **图片处理**：如果使用 Anthropic，需要先将 URL 图片转换为 base64
4. **流式处理**：Anthropic 的事件流提供更细粒度的控制，但 OpenAI 的格式更简单

### 参考资源

- [OpenAI API 文档](https://platform.openai.com/docs/api-reference)
- [Anthropic API 文档](https://docs.anthropic.com/en/api/messages)
- [OpenAI Function Calling](https://platform.openai.com/docs/guides/function-calling)
- [Anthropic Tool Use](https://docs.anthropic.com/en/docs/tool-use)

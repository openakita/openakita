# LLM 模型 API 接口特性调研报告

> 调研日期: 2026-02-02  
> 调研目的: 了解各主流 LLM 模型在工具调用、Thinking 模式、多模态传输等方面的接口差异，为 OpenAkita 多模型适配提供参考

## 一、调研范围

### 1.1 当前系统使用的模型

| 模型 | Provider | API 类型 | 配置位置 |
|------|----------|---------|---------|
| MiniMax M2.1 | minimax | Anthropic 兼容 | `data/llm_endpoints.json` |
| Kimi K2.5 | moonshot | OpenAI 兼容 | `data/llm_endpoints.json` |
| DashScope Qwen3 | dashscope | OpenAI 兼容 | `data/llm_endpoints.json` |
| Claude Opus | yunwu (中转) | Anthropic 原生 | `data/llm_endpoints.json` |

### 1.2 其他主流模型

- **OpenAI**: GPT-4o, GPT-4.1, o1, o3, o4-mini
- **Google Gemini**: Gemini 2.5 Flash/Pro, Gemini 3 Flash/Pro
- **DeepSeek**: DeepSeek-V3, DeepSeek-Reasoner
- **智谱**: GLM-4, GLM-4V
- **豆包**: Doubao-7b, Doubao-35b, Doubao-Pro
- **百川**: Baichuan2-turbo, Baichuan2-turbo-192k

---

## 二、工具调用 (Tool Calling / Function Calling)

### 2.1 格式对比总览

| 模型 | API 标准 | 返回格式 | 特殊情况 |
|------|---------|---------|---------|
| **OpenAI** | OpenAI | `tool_calls` 数组 | 支持 `strict` 模式 |
| **Claude** | Anthropic | `tool_use` block | 需保留完整 block 回传 |
| **Gemini** | Google | `function_call` 对象 | 需处理 `thought_signature` |
| **MiniMax** | Anthropic 兼容 | `tool_use` block | **可能降级为文本格式** |
| **Kimi K2** | OpenAI 兼容 | `tool_calls` 数组 | **原生模型有特殊格式** |
| **DeepSeek** | OpenAI 兼容 | `tool_calls` 数组 | 支持 `strict` 模式 |
| **Qwen** | OpenAI 兼容 | `tool_calls` 数组 | 标准格式 |
| **GLM-4** | OpenAI 兼容 | `tool_calls` 数组 | 标准格式 |
| **豆包** | OpenAI 兼容 | `tool_calls` 数组 | 标准格式 |

### 2.2 OpenAI 工具调用

**基础格式**:
```json
{
  "tools": [{
    "type": "function",
    "function": {
      "name": "get_weather",
      "description": "获取天气信息",
      "parameters": {
        "type": "object",
        "properties": {
          "location": {"type": "string", "description": "城市名称"}
        },
        "required": ["location"]
      }
    }
  }]
}
```

**返回格式**:
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "tool_calls": [{
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"location\": \"北京\"}"
        }
      }]
    },
    "finish_reason": "tool_calls"
  }]
}
```

**Strict 模式** (Structured Outputs):
```json
{
  "tools": [{
    "type": "function",
    "function": {
      "name": "get_weather",
      "strict": true,
      "parameters": {
        "type": "object",
        "properties": {...},
        "required": ["location"],
        "additionalProperties": false
      }
    }
  }]
}
```

**注意事项**:
- `strict: true` 时，所有属性必须在 `required` 中
- 必须设置 `additionalProperties: false`
- 保证返回的 JSON 严格符合 Schema

### 2.3 Anthropic (Claude) 工具调用

**请求格式**:
```json
{
  "tools": [{
    "name": "get_weather",
    "description": "获取天气信息",
    "input_schema": {
      "type": "object",
      "properties": {
        "location": {"type": "string", "description": "城市名称"}
      },
      "required": ["location"]
    }
  }]
}
```

**返回格式**:
```json
{
  "content": [
    {"type": "text", "text": "让我查询一下天气"},
    {
      "type": "tool_use",
      "id": "toolu_01A09q90qw90lq917835lgs",
      "name": "get_weather",
      "input": {"location": "北京"}
    }
  ],
  "stop_reason": "tool_use"
}
```

**工具结果回传**:
```json
{
  "role": "user",
  "content": [{
    "type": "tool_result",
    "tool_use_id": "toolu_01A09q90qw90lq917835lgs",
    "content": "北京今天晴，气温 24°C"
  }]
}
```

**注意事项**:
- 使用 `input_schema` 而非 `parameters`
- 必须完整保留 `tool_use` block 回传
- Extended Thinking 时需同时保留 `thinking` block

### 2.4 MiniMax M2.1 工具调用

**API 选项**:

| API | Base URL | 特点 |
|-----|----------|------|
| Anthropic 兼容 | `https://api.minimaxi.com/anthropic` | 返回标准 tool_use block |
| OpenAI 兼容 | `https://api.minimaxi.com/v1` | 返回标准 tool_calls |

**⚠️ 关键问题: 文本格式降级**

当 Anthropic 兼容 API 在某些情况下可能降级，返回文本格式的工具调用：

```xml
<minimax:tool_call>
<invoke name="get_weather">
<parameter name="location">北京</parameter>
</invoke>
</minimax:tool_call>
```

**系统处理方案** (已实现):
- 文件: `src/openakita/llm/converters/tools.py`
- 函数: `parse_text_tool_calls()`, `has_text_tool_calls()`
- 在 `src/openakita/llm/providers/anthropic.py` 的 `_parse_response()` 中自动检测和解析

**Thinking 内容处理**:
- Anthropic API: thinking 作为独立 block
- OpenAI API (`reasoning_split=True`): thinking 在 `reasoning_details` 字段
- OpenAI API (`reasoning_split=False`): thinking 以 `<think>...</think>` 标签在 content 中

**重要**: 必须完整回传 thinking 内容以保持思维链连续性

### 2.5 Kimi K2.5 工具调用

**API 格式**: OpenAI 兼容

**标准返回**:
```json
{
  "choices": [{
    "message": {
      "tool_calls": [{
        "id": "call_xxx",
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"location\": \"北京\"}"
        }
      }],
      "finish_reason": "tool_calls"
    }
  }]
}
```

**⚠️ 特殊格式 (自托管/vLLM 场景)**

当使用 Kimi K2 原生模型 (非 API) 时，可能返回特殊文本格式：

```
<<|tool_calls_section_begin|>>
<<|tool_call_begin|>>functions.get_weather:0<<|tool_call_argument_begin|>>{"location": "北京"}<<|tool_call_end|>>
<<|tool_calls_section_end|>>
```

**格式解析规则**:
- Tool ID 格式: `functions.{func_name}:{idx}` (idx 从 0 递增)
- 需要从 `functions.get_weather:0` 中提取函数名 `get_weather`

**系统处理方案** (已实现):
- 文件: `src/openakita/llm/converters/tools.py`
- 函数: `_parse_kimi_tool_calls()`

**Tool ID 一致性问题**:
- Kimi 期望 ID 格式为 `functions.func_name:idx`
- 如果 ID 格式不一致，多轮工具调用可能崩溃
- 解决方案: 确保回传时保持原始 ID 格式

### 2.6 DeepSeek 工具调用

**API 格式**: OpenAI 兼容

**基础用法**:
```python
from openai import OpenAI

client = OpenAI(
    api_key="<your api key>",
    base_url="https://api.deepseek.com",
)

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages,
    tools=tools
)
```

**Strict 模式** (Beta):
```python
client = OpenAI(
    api_key="<your api key>",
    base_url="https://api.deepseek.com/beta",  # 注意使用 beta 端点
)

tools = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "strict": True,  # 启用 strict 模式
        "parameters": {
            "type": "object",
            "properties": {...},
            "required": ["location"],
            "additionalProperties": False  # 必须设置
        }
    }
}]
```

**Thinking 模式下的工具调用**:
- 需要 DeepSeek-V3.2 或更高版本
- 设置 `model: "deepseek-reasoner"` 或 `thinking: {"type": "enabled"}`

### 2.7 Gemini 工具调用

**请求格式**:
```python
from google import genai
from google.genai import types

tools = types.Tool(function_declarations=[{
    "name": "get_weather",
    "description": "获取天气信息",
    "parameters": {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "城市名称"}
        },
        "required": ["location"]
    }
}])

config = types.GenerateContentConfig(tools=[tools])
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="北京天气怎么样？",
    config=config
)
```

**返回格式**:
- 返回 `function_call` 对象
- 包含函数名和参数

**Thinking 模式下的工具调用**:
- 需要处理 `thought_signature` (思考签名)
- Gemini 3 模型要求必须回传签名
- SDK 会自动处理签名回传

---

## 三、Thinking / Reasoning 模式

### 3.1 功能对比总览

| 模型 | 实现方式 | 配置参数 | 输出格式 | 可关闭 |
|------|---------|---------|---------|-------|
| **Claude** | `thinking` 参数 | `budget_tokens` | `thinking` block | 是 |
| **OpenAI o1/o3** | Responses API | 自动 | `reasoning_items` | 否 |
| **Gemini** | `thinkingConfig` | `thinkingLevel`/`thinkingBudget` | `thought: true` part | 部分 |
| **MiniMax** | 内置 | `reasoning_split` | `<think>` 或独立字段 | 否 |
| **Kimi** | `thinking` 参数 | `{"type": "enabled"}` | 待确认 | 是 |
| **DeepSeek** | 模型/参数 | `thinking` 参数 | 链式推理 | 是 |
| **Qwen** | `enable_thinking` | `true/false` | `<think>` 标签 | 是 |

### 3.2 Claude Extended Thinking

**启用方式**:
```json
{
  "model": "claude-sonnet-4-5",
  "max_tokens": 16000,
  "thinking": {
    "type": "enabled",
    "budget_tokens": 10000
  },
  "messages": [...]
}
```

**返回格式**:
```json
{
  "content": [
    {
      "type": "thinking",
      "thinking": "Let me analyze this step by step...",
      "signature": "WaUjzkypQ2mUEVM36O2TxuC06KN8xyfbJwyem2dw3URve/op..."
    },
    {
      "type": "text",
      "text": "Based on my analysis..."
    }
  ]
}
```

**重要注意事项**:

1. **Summarized Thinking** (Claude 4 系列):
   - 返回的是思考摘要，非完整内容
   - 计费按完整 thinking tokens，非摘要 tokens
   - 摘要 tokens 与实际计费不匹配

2. **Signature 字段**:
   - 用于验证 thinking block 来源
   - 必须原样回传
   - 跨平台兼容 (API, Bedrock, Vertex AI)

3. **工具调用时**:
   - 必须保留 `thinking` block 回传
   - 不能修改或重排 thinking blocks 顺序

4. **Interleaved Thinking** (Claude 4):
   - 需要 Beta header: `interleaved-thinking-2025-05-14`
   - 允许在工具调用之间进行推理
   - `budget_tokens` 可超过 `max_tokens`

### 3.3 OpenAI Reasoning (o1/o3/o4)

**使用 Responses API**:
```python
from openai import OpenAI

client = OpenAI()
response = client.responses.create(
    model="o3",
    messages=[{"role": "user", "content": "..."}]
)
```

**返回结构**:
- `reasoning_item`: 包含内部推理链 (摘要)
- `message_item`: 最终输出
- 分开统计 reasoning tokens

**注意事项**:
- 多步对话中 reasoning tokens 被丢弃
- 通过 Responses API 传递之前的 reasoning items 可保持最佳性能
- 不支持 `temperature`, `top_p` 等参数修改

### 3.4 Gemini Thinking

**配置方式**:
```python
from google.genai import types

config = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(
        include_thoughts=True,  # 获取思考摘要
        thinking_level="high",  # Gemini 3: minimal/low/medium/high
        # thinking_budget=1024  # Gemini 2.5: 具体 token 数
    )
)
```

**Thinking Levels (Gemini 3)**:
- `minimal`: 最小延迟，复杂编码任务可能仍会思考
- `low`: 简单指令、聊天、高吞吐应用
- `medium`: 平衡的思考 (仅 Flash)
- `high`: 最大推理深度 (默认)

**Thinking Budget (Gemini 2.5)**:
- `-1`: 动态思考 (默认)
- `0`: 关闭思考 (仅 Flash)
- `128-32768`: 具体 token 预算

**Thought Signatures**:
- Gemini 3 可能对所有类型的 parts 返回签名
- 函数调用时必须回传签名
- SDK 自动处理签名回传

### 3.5 MiniMax Interleaved Thinking

**Anthropic API**:
- Thinking 作为独立 block 返回
- 格式与 Claude 类似

**OpenAI API**:
```python
response = client.chat.completions.create(
    model="MiniMax-M2.1",
    messages=messages,
    tools=tools,
    extra_body={"reasoning_split": True},  # 分离思考内容
)

# 思考内容在 reasoning_details 字段
print(response.choices[0].message.reasoning_details)
```

**原生格式** (`reasoning_split=False`):
- 思考以 `<think>...</think>` 标签包裹在 content 中
- 需要手动解析

**关键要求**: 必须完整回传 thinking 内容，保持思维链连续性

### 3.6 DeepSeek Thinking Mode

**启用方式**:
```python
# 方式 1: 使用 reasoner 模型
response = client.chat.completions.create(
    model="deepseek-reasoner",
    messages=messages
)

# 方式 2: thinking 参数
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages,
    extra_body={"thinking": {"type": "enabled"}}
)
```

**支持的功能**:
- JSON output
- Tool calls (需 V3.2+)
- Chat completion

**不支持的参数**:
- `temperature`
- `top_p`
- `presence_penalty`
- `frequency_penalty`
- `logprobs`
- `top_logprobs`

---

## 四、多模态传输 (图片/视频)

### 4.1 图片传输对比

| 模型 | 支持格式 | 传输方式 | 单次限制 | 大小限制 |
|------|---------|---------|---------|---------|
| **OpenAI** | JPEG, PNG, GIF, WebP | base64 / URL | - | 20MB |
| **Claude** | JPEG, PNG, GIF, WebP | base64 / URL | 100张 (API) | - |
| **Gemini** | 多种格式 | base64 inline / Files API | - | 20MB inline |
| **Kimi K2.5** | 多种格式 | base64 / URL | - | - |
| **Qwen-VL** | 多种格式 | base64 / URL | - | - |

### 4.2 OpenAI 图片传输

**Base64 方式**:
```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "这张图片是什么？"},
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,{base64_data}"
      }
    }
  ]
}
```

**URL 方式**:
```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "这张图片是什么？"},
    {
      "type": "image_url",
      "image_url": {
        "url": "https://example.com/image.jpg"
      }
    }
  ]
}
```

### 4.3 Anthropic 图片传输

**Base64 方式**:
```json
{
  "role": "user",
  "content": [
    {
      "type": "image",
      "source": {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": "{base64_data}"
      }
    },
    {"type": "text", "text": "这张图片是什么？"}
  ]
}
```

**URL 方式**:
```json
{
  "role": "user",
  "content": [
    {
      "type": "image",
      "source": {
        "type": "url",
        "url": "https://example.com/image.jpg"
      }
    },
    {"type": "text", "text": "这张图片是什么？"}
  ]
}
```

### 4.4 Gemini 图片/视频传输

**Inline Data (小于 20MB)**:
```python
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=[
        types.Part.from_bytes(
            data=image_bytes,
            mime_type="image/jpeg"
        ),
        "这张图片是什么？"
    ]
)
```

**Files API (大于 20MB 或视频)**:
```python
# 上传文件
file = client.files.upload(path="video.mp4")

# 使用文件
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=[
        types.Part.from_uri(
            file_uri=file.uri,
            mime_type="video/mp4"
        ),
        "描述这个视频的内容"
    ]
)
```

**支持 YouTube URL**:
```python
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=[
        types.Part.from_uri(
            file_uri="https://www.youtube.com/watch?v=xxxxx",
            mime_type="video/youtube"
        ),
        "总结这个视频"
    ]
)
```

### 4.5 视频处理策略

| 模型 | 原生视频支持 | 替代方案 |
|------|-------------|---------|
| **Gemini** | ✅ Files API | - |
| **Kimi K2.5** | ✅ 原生多模态 | - |
| **OpenAI** | ❌ | 抽帧为图片序列 |
| **Claude** | ❌ | 抽帧为图片序列 |
| **Qwen** | 向量化支持 | 抽帧 |

**抽帧策略** (不支持视频的模型):
```python
import cv2
import base64

def extract_frames(video_path, sample_rate=1):
    """每秒抽取 sample_rate 帧"""
    frames = []
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    frame_interval = int(fps / sample_rate)
    frame_count = 0
    
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        
        if frame_count % frame_interval == 0:
            _, buffer = cv2.imencode('.jpg', frame)
            base64_frame = base64.b64encode(buffer).decode()
            frames.append(base64_frame)
        
        frame_count += 1
    
    cap.release()
    return frames
```

---

## 五、系统实现现状

### 5.1 已实现的兼容处理

| 功能 | 文件位置 | 说明 |
|------|---------|------|
| MiniMax 文本格式解析 | `llm/converters/tools.py` | `parse_text_tool_calls()` |
| Kimi K2 格式解析 | `llm/converters/tools.py` | `_parse_kimi_tool_calls()` |
| Thinking 标签清理 | `core/agent.py` | `strip_thinking_tags()` |
| 工具调用格式检测 | `llm/converters/tools.py` | `has_text_tool_calls()` |
| Anthropic 文本解析集成 | `llm/providers/anthropic.py` | `_parse_response()` |

### 5.2 支持的 Thinking 标签清理

当前 `strip_thinking_tags()` 函数清理以下格式:
- `<thinking>...</thinking>` - Claude extended thinking
- `<think>...</think>` - MiniMax/Qwen thinking
- `<minimax:tool_call>...</minimax:tool_call>` - MiniMax 工具调用
- `<<|tool_calls_section_begin|>>...<<|tool_calls_section_end|>>` - Kimi K2 工具调用

---

## 六、改进建议

### 6.1 短期改进

1. **OpenAI Provider 增加文本格式解析**
   - 类似 Anthropic provider，检测并解析文本格式工具调用
   - 处理 Kimi 可能的特殊格式

2. **Thinking 内容标准化处理**
   - 统一各模型 thinking 输出的提取和存储
   - 正确回传 thinking blocks 以保持思维链

3. **模型能力检测**
   - 在 `llm_endpoints.json` 中记录各模型的能力边界
   - 避免调用不支持的功能

### 6.2 中期改进

1. **多模态适配器**
   - 根据目标模型自动转换图片/视频格式
   - 不支持视频的模型自动抽帧

2. **Thinking Signature 处理**
   - 正确保存和回传 Claude/Gemini 的 signature
   - 支持 interleaved thinking

3. **Strict 模式支持**
   - 为支持的模型 (OpenAI, DeepSeek) 启用 strict 工具调用
   - 自动补全 `required` 和 `additionalProperties`

### 6.3 长期改进

1. **模型能力注册表**
   - 维护各模型的能力矩阵
   - 自动路由到最适合的模型

2. **统一 Thinking 接口**
   - 抽象各模型的 thinking 配置
   - 提供统一的 thinking 开关和预算设置

---

## 七、参考资料

### 官方文档

- [OpenAI Function Calling](https://platform.openai.com/docs/guides/function-calling)
- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
- [Anthropic Tool Use](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview)
- [Anthropic Extended Thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
- [Gemini Function Calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [Gemini Thinking](https://ai.google.dev/gemini-api/docs/thinking)
- [Gemini Files API](https://ai.google.dev/gemini-api/docs/files)
- [Gemini Video Understanding](https://ai.google.dev/gemini-api/docs/video-understanding)
- [MiniMax Tool Use](https://platform.minimaxi.com/docs/guides/text-m2-function-call)
- [Kimi K2 Tool Call Guidance](https://huggingface.co/moonshotai/Kimi-K2-Thinking/blob/main/docs/tool_call_guidance.md)
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls)
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)
- [DashScope API Reference](https://help.aliyun.com/zh/model-studio/dashscope-api-reference/)
- [智谱 AI 开放平台](https://open.bigmodel.cn/)

### 相关项目文档

- [OpenAkita LLM Tool Call Formats](./llm-tool-call-formats.md) - 工具调用格式快速参考

"""
IM Channel 工具定义

包含 IM 通道相关的工具：
- deliver_artifacts: 通过网关交付附件并返回回执
- get_voice_file: 获取语音文件
- get_image_file: 获取图片文件
- get_chat_history: 获取聊天历史
"""

IM_CHANNEL_TOOLS = [
    {
        "name": "deliver_artifacts",
        "category": "IM Channel",
        "description": "Deliver artifacts (files/images/voice) to current IM chat via gateway, returning a receipt. Use this as the only delivery proof for attachments.",
        "detail": """通过网关向当前 IM 聊天交付附件（文件/图片/语音），并返回结构化回执（receipt）。

⚠️ **重要**：
- 文本回复会由网关直接转发（不需要用工具发送）。
- 附件交付必须使用本工具，并以回执作为“已交付”的唯一证据。

输入说明：
- artifacts: 要交付的附件清单（显式 manifest）
  - type: file | image | voice
  - path: 本地文件路径
  - caption: 说明文字（可选）
  - mime/name/dedupe_key: 预留字段（可选）

输出说明：
- 返回 JSON 字符串，包含每个 artifact 的回执（receipt）：
  - status: delivered | skipped | failed
  - message_id: 底层通道消息 ID（若适用）
  - size/sha256: 本地文件信息（若可读取）
  - dedupe_key: 会话内去重键（相同附件可被标记为 skipped）
  - error_code: 失败码/跳过原因（如 missing_type_or_path / deduped / unsupported_type / send_failed / adapter_not_found / missing_context）

示例：
- 发送截图：deliver_artifacts(artifacts=[{"type":"image","path":"data/temp/s.png","caption":"这是截图"}])
- 发送文件：deliver_artifacts(artifacts=[{"type":"file","path":"data/out/report.md"}])""",
        "input_schema": {
            "type": "object",
            "properties": {
                "artifacts": {
                    "type": "array",
                    "description": "要交付的附件清单（manifest）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "description": "file|image|voice"},
                            "path": {"type": "string", "description": "本地文件路径"},
                            "caption": {"type": "string", "description": "说明文字（可选）"},
                            "mime": {"type": "string", "description": "MIME 类型（可选）"},
                            "name": {"type": "string", "description": "展示文件名（可选）"},
                            "dedupe_key": {"type": "string", "description": "去重键（可选）"},
                        },
                        "required": ["type", "path"],
                    },
                    "minItems": 1,
                },
                "mode": {
                    "type": "string",
                    "description": "send|preview（预留）",
                    "default": "send",
                },
            },
            "required": ["artifacts"],
        },
    },
    {
        "name": "get_voice_file",
        "category": "IM Channel",
        "description": "Get local file path of voice message sent by user. When user sends voice message, system auto-downloads it. When you need to: (1) Process user's voice message, (2) Transcribe voice to text.",
        "detail": """获取用户发送的语音消息的本地文件路径。

**工作流程**：
1. 用户发送语音消息
2. 系统自动下载到本地
3. 使用此工具获取文件路径
4. 用语音识别脚本处理

**适用场景**：
- 处理用户的语音消息
- 语音转文字""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_image_file",
        "category": "IM Channel",
        "description": "Get local file path of image sent by user. ONLY use when you need the file path for programmatic operations (forward, save, crop, convert format). Do NOT use this to view or analyze image content — images are already included in your message as multimodal content and you can see them directly.",
        "detail": """获取用户发送的图片的本地文件路径。

⚠️ **重要**：用户发送的图片已作为多模态内容包含在你的消息中，你可以直接看到并理解图片。
**不要**为了查看或分析图片内容而调用此工具。

**仅在以下场景使用**：
- 需要将图片文件转发、保存到其他位置
- 需要用外部工具对图片文件进行格式转换、裁剪、压缩等操作
- 需要将图片路径传给其他工具或脚本""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_chat_history",
        "category": "IM Channel",
        "description": "Get current chat history including user messages, your replies, and system task notifications. When user says 'check previous messages' or 'what did I just send', use this tool.",
        "detail": """获取当前聊天的历史消息记录。

**返回内容**：
- 用户发送的消息
- 你之前的回复
- 系统任务发送的通知

**适用场景**：
- 用户说"看看之前的消息"
- 用户说"刚才发的什么"
- 需要回顾对话上下文""",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "获取最近多少条消息", "default": 20},
                "include_system": {
                    "type": "boolean",
                    "description": "是否包含系统消息（如任务通知）",
                    "default": True,
                },
            },
        },
    },
]

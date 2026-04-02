"""
IM Channel 工具定义

包含 IM 通道相关的工具：
- deliver_artifacts: 通过网关交付附件并返回回执（支持跨通道发送）
- get_voice_file: 获取语音文件
- get_image_file: 获取图片文件
- get_chat_history: 获取聊天历史
"""

IM_CHANNEL_TOOLS = [
    {
        "name": "deliver_artifacts",
        "category": "IM Channel",
        "description": "Deliver artifacts (files/images/voice) to an IM chat via gateway, returning a receipt. Supports cross-channel delivery via target_channel (e.g. send files from Desktop to Telegram). Use this as the only delivery proof for attachments.",
        "detail": """通过网关交付附件（文件/图片/语音），并返回结构化回执（receipt）。

⚠️ **重要**：
- 文本回复会由网关直接转发（不需要用工具发送）。
- 附件交付必须使用本工具，并以回执作为"已交付"的唯一证据。

输入说明：
- artifacts: 要交付的附件清单（显式 manifest）
  - type: file | image | voice
  - path: 本地文件路径
  - caption: 说明文字（可选）
  - mime/name/dedupe_key: 预留字段（可选）
- target_channel（可选）: 目标 IM 通道名。指定后会将附件发送到该通道（如从桌面端发送文件到 telegram）。
  不填则默认发送到当前通道（IM 模式）或返回文件 URL（桌面模式）。
- prefer_chat_type（可选，默认 "private"）: 跨通道发送时偏好的聊天类型。
  - "private": 优先发送到私聊窗口（默认，适合截图、文件等个人交付）
  - "group": 优先发送到群聊窗口（适合用户明确要求发到群里的场景）
  仅在指定 target_channel 时生效。

输出说明：
- 返回 JSON 字符串，包含每个 artifact 的回执（receipt）：
  - status: delivered | skipped | failed
  - message_id: 底层通道消息 ID（若适用）
  - size/sha256: 本地文件信息（若可读取）
  - dedupe_key: 会话内去重键（相同附件可被标记为 skipped）
  - error_code: 失败码/跳过原因（如 missing_type_or_path / deduped / unsupported_type / send_failed / adapter_not_found / missing_context）

示例：
- 发送截图：deliver_artifacts(artifacts=[{"type":"image","path":"data/temp/s.png","caption":"这是截图"}])
- 发送文件：deliver_artifacts(artifacts=[{"type":"file","path":"data/out/report.md"}])
- 跨通道发送：deliver_artifacts(artifacts=[{"type":"file","path":"data/out/report.docx"}], target_channel="telegram")
- 从桌面发图到飞书：deliver_artifacts(artifacts=[{"type":"image","path":"data/temp/chart.png","caption":"图表"}], target_channel="feishu")
- 发到飞书群聊：deliver_artifacts(artifacts=[{"type":"file","path":"data/out/report.md"}], target_channel="feishu", prefer_chat_type="group")""",
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
                "target_channel": {
                    "type": "string",
                    "description": "目标 IM 通道名（如 telegram/wework/feishu/lark/dingtalk）。留空或不填则发送到当前通道（IM 模式）或桌面端（Desktop 模式）。",
                },
                "prefer_chat_type": {
                    "type": "string",
                    "description": "跨通道发送时偏好的聊天类型: private(私聊,默认) / group(群聊)。优先选择匹配类型的会话，不匹配时回退到其他类型。仅在指定 target_channel 时生效。",
                    "default": "private",
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
    {
        "name": "get_chat_info",
        "category": "IM Channel",
        "description": "Get current chat/group information (name, member count, description, owner). Use when you need to understand the current chat context.",
        "detail": """获取当前聊天/群组的信息。

**返回内容**：
- 群聊名称、描述、群主、成员数等
- 私聊时返回对方用户信息

**适用场景**：
- 需要了解当前群聊环境
- 用户询问"这个群有多少人"等""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_user_info",
        "category": "IM Channel",
        "description": "Get user info by user_id (name, avatar). Use when you need to look up a specific user's details.",
        "detail": """获取指定用户的信息。

**返回内容**：
- 用户名称、头像等基本信息

**适用场景**：
- 需要查询某个用户的名称
- 需要获取用户头像""",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "用户 ID（open_id 格式）"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "get_chat_members",
        "category": "IM Channel",
        "description": "Get member list of the current group chat. Use when user asks about group members or you need to know who is in the chat.",
        "detail": """获取当前群聊的成员列表。

**返回内容**：
- 成员 ID 和名称列表

**适用场景**：
- 用户询问"群里都有谁"
- 需要查找特定群成员""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recent_messages",
        "category": "IM Channel",
        "description": "Get recent messages from the chat (platform API, not session history). Use when in a topic/thread and need to see messages outside the thread, or when user asks about recent group activity.",
        "detail": """获取群聊最近的消息列表（通过平台 API 获取，非会话历史）。

**与 get_chat_history 的区别**：
- get_chat_history: 获取当前会话上下文中的消息（session 内的对话历史）
- get_recent_messages: 调用平台 API 获取群聊中的实际消息（包括话题外的消息）

**适用场景**：
- 在话题中需要查看话题外的群消息
- 用户说"看看群里刚才的通知"、"群里最近说了什么"
- 需要获取群聊中其他人的消息

**注意**：需要平台的消息读取权限（如飞书的 im:message:readonly）""",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "获取最近多少条消息", "default": 20},
            },
        },
    },
]

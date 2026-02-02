"""
IM Channel 工具定义

包含 IM 通道相关的工具：
- send_to_chat: 发送消息/文件
- get_voice_file: 获取语音文件
- get_image_file: 获取图片文件
- get_chat_history: 获取聊天历史
"""

IM_CHANNEL_TOOLS = [
    {
        "name": "send_to_chat",
        "description": "Send messages/files to current IM chat (only available in IM session). When you need to: (1) Send text responses, (2) Send screenshots/images after desktop_screenshot, (3) Send voice/documents to user. Use file_path for files.",
        "detail": """发送消息到当前 IM 聊天（仅在 IM 会话中可用）。

**支持发送**：
- 文本消息
- 图片文件
- 语音文件
- 其他文件

**使用场景**：
当你完成了生成文件（如截图、文档、语音）的任务时，使用此工具将文件发送给用户。

**示例**：
- 截图后: send_to_chat(file_path="C:/Users/.../screenshot.png")
- 发消息: send_to_chat(text="任务完成！")
- 带说明: send_to_chat(file_path="...", caption="这是截图")""",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要发送的文本消息（可选）"
                },
                "file_path": {
                    "type": "string",
                    "description": "要发送的文件路径（图片、文档等）"
                },
                "voice_path": {
                    "type": "string",
                    "description": "要发送的语音文件路径"
                },
                "caption": {
                    "type": "string",
                    "description": "文件的说明文字（可选）"
                }
            }
        }
    },
    {
        "name": "get_voice_file",
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
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "get_image_file",
        "description": "Get local file path of image sent by user. When user sends image, system auto-downloads it. When you need to: (1) Process user's image, (2) Analyze image content.",
        "detail": """获取用户发送的图片的本地文件路径。

**工作流程**：
1. 用户发送图片
2. 系统自动下载到本地
3. 使用此工具获取文件路径

**适用场景**：
- 处理用户的图片
- 分析图片内容""",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "get_chat_history",
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
                "limit": {
                    "type": "integer",
                    "description": "获取最近多少条消息",
                    "default": 20
                },
                "include_system": {
                    "type": "boolean",
                    "description": "是否包含系统消息（如任务通知）",
                    "default": True
                }
            }
        }
    },
]

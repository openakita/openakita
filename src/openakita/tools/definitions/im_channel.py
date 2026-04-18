"""
IM Channel tool definitions

Includes tools related to IM channels:
- deliver_artifacts: deliver attachments via the gateway and return a receipt (supports cross-channel delivery)
- get_voice_file: fetch a voice file
- get_image_file: fetch an image file
- get_chat_history: fetch chat history
"""

IM_CHANNEL_TOOLS = [
    {
        "name": "deliver_artifacts",
        "category": "IM Channel",
        "description": "Deliver artifacts (files/images/voice) to an IM chat via gateway, returning a receipt. Supports cross-channel delivery via target_channel (e.g. send files from Desktop to Telegram). Use this as the only delivery proof for attachments.",
        "detail": """Deliver attachments (files/images/voice) via the gateway and return a structured receipt.

**Important**:
- Text replies are forwarded directly by the gateway (no tool call needed).
- Attachment delivery must use this tool, and the receipt is the only proof of "delivered".

Input:
- artifacts: list of attachments to deliver (explicit manifest)
  - type: file | image | voice
  - path: local file path
  - caption: caption text (optional)
  - mime/name/dedupe_key: reserved fields (optional)
- target_channel (optional): target IM channel name. When specified, the attachment is sent to that channel (e.g., send a file from Desktop to telegram).
  If omitted, defaults to the current channel (IM mode) or returns a file URL (Desktop mode).
- prefer_chat_type (optional, default "private"): preferred chat type for cross-channel delivery.
  - "private": prefer a private chat window (default, suitable for screenshots, files, and other personal delivery)
  - "group": prefer a group chat window (suitable when the user explicitly wants it sent to a group)
  Only takes effect when target_channel is specified.

Output:
- Returns a JSON string containing a receipt for each artifact:
  - status: delivered | skipped | failed
  - message_id: underlying channel message ID (if applicable)
  - size/sha256: local file info (if readable)
  - dedupe_key: per-session dedupe key (identical attachments may be marked as skipped)
  - error_code: failure/skip reason (e.g., missing_type_or_path / deduped / unsupported_type / send_failed / adapter_not_found / missing_context)

Examples:
- Send a screenshot: deliver_artifacts(artifacts=[{"type":"image","path":"data/temp/s.png","caption":"screenshot"}])
- Send a file: deliver_artifacts(artifacts=[{"type":"file","path":"data/out/report.md"}])
- Cross-channel delivery: deliver_artifacts(artifacts=[{"type":"file","path":"data/out/report.docx"}], target_channel="telegram")
- Send an image from Desktop to Feishu: deliver_artifacts(artifacts=[{"type":"image","path":"data/temp/chart.png","caption":"chart"}], target_channel="feishu")
- Send to a Feishu group chat: deliver_artifacts(artifacts=[{"type":"file","path":"data/out/report.md"}], target_channel="feishu", prefer_chat_type="group")""",
        "input_schema": {
            "type": "object",
            "properties": {
                "artifacts": {
                    "type": "array",
                    "description": "List of attachments to deliver (manifest)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "description": "file|image|voice"},
                            "path": {"type": "string", "description": "Local file path"},
                            "caption": {"type": "string", "description": "Caption text (optional)"},
                            "mime": {"type": "string", "description": "MIME type (optional)"},
                            "name": {"type": "string", "description": "Display file name (optional)"},
                            "dedupe_key": {"type": "string", "description": "Dedupe key (optional)"},
                        },
                        "required": ["type", "path"],
                    },
                    "minItems": 1,
                },
                "target_channel": {
                    "type": "string",
                    "description": "Target IM channel name (e.g., telegram/wework/feishu/dingtalk). If empty or omitted, sends to the current channel (IM mode) or Desktop client (Desktop mode).",
                },
                "prefer_chat_type": {
                    "type": "string",
                    "description": "Preferred chat type for cross-channel delivery: private (private chat, default) / group (group chat). Prefers conversations matching the type and falls back to the other type if none match. Only takes effect when target_channel is specified.",
                    "default": "private",
                },
                "mode": {
                    "type": "string",
                    "description": "send|preview (reserved)",
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
        "detail": """Get the local file path of a voice message sent by the user.

**Workflow**:
1. The user sends a voice message
2. The system automatically downloads it locally
3. Use this tool to get the file path
4. Process it with a speech-recognition script

**Use cases**:
- Processing a user's voice message
- Voice-to-text transcription""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_image_file",
        "category": "IM Channel",
        "description": "Get local file path of image sent by user. ONLY use when you need the file path for programmatic operations (forward, save, crop, convert format). Do NOT use this to view or analyze image content — images are already included in your message as multimodal content and you can see them directly.",
        "detail": """Get the local file path of an image sent by the user.

**Important**: Images sent by the user are already included in your message as multimodal content, so you can see and understand them directly.
**Do not** call this tool just to view or analyze image content.

**Use only in the following cases**:
- You need to forward or save the image file elsewhere
- You need to use external tools to convert format, crop, or compress the image file
- You need to pass the image path to another tool or script""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_chat_history",
        "category": "IM Channel",
        "description": "Get current chat history including user messages, your replies, and system task notifications. When user says 'check previous messages' or 'what did I just send', use this tool.",
        "detail": """Get the message history of the current chat.

**Returns**:
- Messages sent by the user
- Your previous replies
- Notifications sent by system tasks

**Use cases**:
- The user says "check previous messages"
- The user says "what did I just send"
- You need to review the conversation context""",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of recent messages to fetch", "default": 20},
                "include_system": {
                    "type": "boolean",
                    "description": "Whether to include system messages (e.g., task notifications)",
                    "default": True,
                },
            },
        },
    },
    {
        "name": "get_chat_info",
        "category": "IM Channel",
        "description": "Get current chat/group information (name, member count, description, owner). Use when you need to understand the current chat context.",
        "detail": """Get info about the current chat/group.

**Returns**:
- Group name, description, owner, member count, etc.
- For private chats, the other user's info

**Use cases**:
- Need to understand the current group chat context
- User asks things like "how many people are in this group\"""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_user_info",
        "category": "IM Channel",
        "description": "Get user info by user_id (name, avatar). Use when you need to look up a specific user's details.",
        "detail": """Get info about a specific user.

**Returns**:
- Basic info such as user name and avatar

**Use cases**:
- Need to look up a user's name
- Need to retrieve a user's avatar""",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "User ID (open_id format)"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "get_chat_members",
        "category": "IM Channel",
        "description": "Get member list of the current group chat. Use when user asks about group members or you need to know who is in the chat.",
        "detail": """Get the member list of the current group chat.

**Returns**:
- List of member IDs and names

**Use cases**:
- User asks "who's in the group"
- Need to find a specific group member""",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recent_messages",
        "category": "IM Channel",
        "description": "Get recent messages from the chat (platform API, not session history). Use when in a topic/thread and need to see messages outside the thread, or when user asks about recent group activity.",
        "detail": """Get the most recent messages of the group chat (via the platform API, not session history).

**Difference from get_chat_history**:
- get_chat_history: fetches messages in the current session context (conversation history within the session)
- get_recent_messages: calls the platform API to fetch actual group-chat messages (including messages outside the topic)

**Use cases**:
- In a topic/thread, need to see group messages outside the thread
- User says "check the notification the group just sent" or "what was said in the group recently"
- Need to retrieve messages from other people in the group

**Note**: Requires the platform's message-read permission (e.g., Feishu's im:message:readonly)""",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of recent messages to fetch", "default": 20},
            },
        },
    },
]

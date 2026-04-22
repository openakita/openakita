"""
User Profile 工具定义

包含用户档案管理相关的工具：
- update_user_profile: 更新用户档案
- skip_profile_question: 跳过档案问题
- get_user_profile: 获取用户档案
"""

PROFILE_TOOLS = [
    {
        "name": "update_user_profile",
        "category": "Profile",
        "description": "Update structured user profile fields (name, work_field, os, ide, timezone, etc.) when user shares personal info. When you need to: (1) Save user preferences to a structured field, (2) Remember user's work domain, (3) Provide personalized service. NOTE: For persona/communication-style preferences (sticker_preference, emoji_usage, humor, formality, etc.), use update_persona_trait instead. For free-form observations, lessons, or patterns that don't map to a profile field, use add_memory instead.",
        "detail": """更新用户档案信息。

**适用场景**：
当用户告诉你关于他们的偏好、习惯、工作领域等信息时，使用此工具保存。这样你就能更好地了解用户，提供个性化服务。

**支持的档案项**：
- name: 称呼
- agent_role: **Agent 扮演的角色**（如"工作助手"、"技术顾问"），**不是用户职业**。用户说"我是后端工程师/产品经理/老师"应改用 `key="profession"`，handler 会自动落入长期记忆，**不要**塞到 agent_role
- work_field: **工作领域行业**（如 互联网/金融/教育），**不是地理位置**。用户说"我住上海/广州"应改用 `key="city"` 或 `key="location"`，**不要**塞到 work_field
- preferred_language: 编程语言偏好
- os: 操作系统
- ide: 开发工具
- detail_level: 详细程度偏好
- code_comment_lang: 代码注释语言
- indent_style: 缩进风格（2空格/4空格/tab）
- code_style: 代码风格规范（PEP8/Google Style/Prettier 等）
- work_hours: 工作时间
- timezone: 时区
- confirm_preference: 确认偏好
- hobbies: 兴趣爱好
- health_habits: 健康习惯
- communication_style: 沟通风格偏好
- humor_preference: 幽默偏好
- proactive_preference: 主动消息偏好
- emoji_preference: 表情偏好
- care_topics: 关心话题

**注意**：表情包偏好(sticker_preference)、表情使用(emoji_usage)、幽默感(humor)、正式程度(formality)等沟通风格相关偏好属于人格系统，应使用 `update_persona_trait` 工具更新，而非此工具。

**重要禁忌（避免字段错配）**：
- 用户的"职业/职位/工种"（后端工程师、产品经理、设计师、老师等）→ 用 `key="profession"`，**不要**填进 agent_role
- 用户的"地理位置/居住地"（上海、北京、广州、香港等）→ 用 `key="city"` 或 `key="location"`，**不要**填进 work_field
- 不在白名单的 key 不会丢失：handler 已实现 fallback，会自动保存到长期记忆。请按语义选最贴近的字段名（哪怕不在白名单），由 handler 决定落点。""",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "档案项键名"},
                "value": {"type": "string", "description": "用户提供的信息值"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "skip_profile_question",
        "category": "Profile",
        "description": "Skip profile question when user explicitly refuses to answer. When user says 'I don't want to answer' or 'skip this question', use this tool to stop asking about that item.",
        "detail": """当用户明确表示不想回答某个问题时，跳过该问题（以后不再询问）。

**适用场景**：
- 用户说"不想回答"
- 用户说"跳过这个问题"
- 用户表示不愿透露某信息""",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "要跳过的档案项键名"}},
            "required": ["key"],
        },
    },
    {
        "name": "get_user_profile",
        "category": "Profile",
        "description": "Get current user profile summary to understand user's preferences and context. When you need to: (1) Check known user info, (2) Personalize responses.",
        "detail": """获取当前用户档案信息摘要。

**返回信息**：
- 已填写的档案项
- 用户偏好设置
- 工作相关信息

**适用场景**：
- 检查已知的用户信息
- 个性化响应""",
        "input_schema": {"type": "object", "properties": {}},
    },
]

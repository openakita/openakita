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
        "description": "Update structured user profile fields (name, work_field, os, ide, timezone, etc.) when user shares personal info. When you need to: (1) Save user preferences to a structured field, (2) Remember user's work domain, (3) Provide personalized service. NOTE: For free-form observations, lessons, or patterns that don't map to a profile field, use add_memory instead.",
        "detail": """更新用户档案信息。

**适用场景**：
当用户告诉你关于他们的偏好、习惯、工作领域等信息时，使用此工具保存。这样你就能更好地了解用户，提供个性化服务。

**支持的档案项**：
- name: 称呼
- agent_role: Agent 角色
- work_field: 工作领域
- preferred_language: 编程语言偏好
- os: 操作系统
- ide: 开发工具
- detail_level: 详细程度偏好
- code_comment_lang: 代码注释语言
- work_hours: 工作时间
- timezone: 时区
- confirm_preference: 确认偏好
- hobbies: 兴趣爱好
- health_habits: 健康习惯
- communication_style: 沟通风格偏好
- humor_preference: 幽默偏好
- proactive_preference: 主动消息偏好
- emoji_preference: 表情偏好
- care_topics: 关心话题""",
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

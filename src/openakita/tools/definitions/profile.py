"""
User Profile tool definitions

Contains tools for user profile management:
- update_user_profile: update user profile
- skip_profile_question: skip a profile question
- get_user_profile: get user profile
"""

PROFILE_TOOLS = [
    {
        "name": "update_user_profile",
        "category": "Profile",
        "description": "Update structured user profile fields (name, work_field, os, ide, timezone, etc.) when user shares personal info. When you need to: (1) Save user preferences to a structured field, (2) Remember user's work domain, (3) Provide personalized service. NOTE: For persona/communication-style preferences (sticker_preference, emoji_usage, humor, formality, etc.), use update_persona_trait instead. For free-form observations, lessons, or patterns that don't map to a profile field, use add_memory instead.",
        "detail": """Update user profile information.

**When to use**:
When the user shares information about their preferences, habits, work domain, etc., use this tool to save it. This allows you to better understand the user and provide personalized service.

**Supported profile fields**:
- name: display name
- agent_role: Agent role
- work_field: work domain
- preferred_language: programming language preference
- os: operating system
- ide: development tool
- detail_level: level of detail preference
- code_comment_lang: code comment language
- indent_style: indent style (2 spaces / 4 spaces / tab)
- code_style: code style guide (PEP8 / Google Style / Prettier, etc.)
- work_hours: work schedule
- timezone: time zone
- confirm_preference: confirmation preference
- hobbies: hobbies and interests
- health_habits: health habits
- communication_style: communication style preference
- humor_preference: humor preference
- proactive_preference: proactive messaging preference
- emoji_preference: emoji preference
- care_topics: topics of interest

**Note**: Preferences related to communication style — such as sticker usage (sticker_preference), emoji usage (emoji_usage), humor, and formality — belong to the persona system and should be updated using the `update_persona_trait` tool, not this one.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Profile field key"},
                "value": {"type": "string", "description": "Value provided by the user"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "skip_profile_question",
        "category": "Profile",
        "description": "Skip profile question when user explicitly refuses to answer. When user says 'I don't want to answer' or 'skip this question', use this tool to stop asking about that item.",
        "detail": """Skip a question when the user explicitly declines to answer it (will not be asked again).

**When to use**:
- The user says they don't want to answer
- The user says to skip the question
- The user indicates they don't want to share certain information""",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Profile field key to skip"}},
            "required": ["key"],
        },
    },
    {
        "name": "get_user_profile",
        "category": "Profile",
        "description": "Get current user profile summary to understand user's preferences and context. When you need to: (1) Check known user info, (2) Personalize responses.",
        "detail": """Get a summary of the current user profile.

**Returned information**:
- Completed profile fields
- User preferences
- Work-related information

**When to use**:
- Review known user information
- Personalize responses""",
        "input_schema": {"type": "object", "properties": {}},
    },
]

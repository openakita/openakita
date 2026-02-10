"""
人格系统 + 活人感工具定义

包含人格管理和活人感模式相关的工具:
- switch_persona: 切换人格预设
- update_persona_trait: 更新人格偏好特质
- toggle_proactive: 开关活人感模式
- get_persona_profile: 获取当前人格配置
"""

PERSONA_TOOLS = [
    {
        "name": "switch_persona",
        "category": "Persona",
        "description": "Switch the agent's persona preset (e.g., business, girlfriend, tech_expert). When user asks to change the agent's personality or communication style.",
        "detail": """切换 Agent 的人格预设角色。

**可用预设**：
- default: 默认助手（专业友好）
- business: 商务助理（正式高效）
- tech_expert: 技术专家（严谨深度）
- butler: 私人管家（周到体贴）
- girlfriend: 女友感（温柔关心）
- boyfriend: 男友感（阳光鼓励）
- family: 家人感（亲切唠叨）
- jarvis: 贾维斯（英式幽默、小叛逆、话唠、任务时严谨）

**适用场景**：
- 用户要求切换角色/性格
- 用户说"正式一点"/"随意一点"等""",
        "input_schema": {
            "type": "object",
            "properties": {
                "preset_name": {
                    "type": "string",
                    "description": "预设名称 (default/business/tech_expert/butler/girlfriend/boyfriend/family/jarvis)",
                    "enum": [
                        "default",
                        "business",
                        "tech_expert",
                        "butler",
                        "girlfriend",
                        "boyfriend",
                        "family",
                        "jarvis",
                    ],
                }
            },
            "required": ["preset_name"],
        },
    },
    {
        "name": "update_persona_trait",
        "category": "Persona",
        "description": "Update a specific persona preference dimension (formality, humor, emoji, etc.) based on user feedback or explicit request.",
        "detail": """更新用户的人格偏好维度。

**支持的维度**：
- formality: 正式程度 (very_formal/formal/neutral/casual/very_casual)
- humor: 幽默感 (none/occasional/frequent)
- emoji_usage: 表情使用 (never/rare/moderate/frequent)
- reply_length: 回复长度 (very_short/short/moderate/detailed/very_detailed)
- proactiveness: 主动程度 (silent/low/moderate/high)
- emotional_distance: 情感距离 (professional/friendly/close/intimate)
- address_style: 称呼方式 (自由文本)
- encouragement: 鼓励程度 (none/occasional/frequent)
- care_topics: 关心话题 (自由文本)
- sticker_preference: 表情包偏好 (never/rare/moderate/frequent)

**适用场景**：
- 用户明确表达偏好（"随意一点"/"别发表情"等）
- 从对话中推断出偏好变化""",
        "input_schema": {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "description": "偏好维度名",
                },
                "preference": {
                    "type": "string",
                    "description": "偏好值",
                },
                "source": {
                    "type": "string",
                    "description": "来源 (explicit=用户明确说/mined=从对话推断/correction=用户修正)",
                    "enum": ["explicit", "mined", "correction"],
                },
                "evidence": {
                    "type": "string",
                    "description": "证据描述（用户说了什么）",
                },
            },
            "required": ["dimension", "preference"],
        },
    },
    {
        "name": "toggle_proactive",
        "category": "Persona",
        "description": "Toggle the proactive/living-presence mode on or off. Controls whether the agent sends proactive messages (greetings, reminders, follow-ups).",
        "detail": """开关活人感模式。

开启后 Agent 会主动发送消息：
- 早安/晚安问候
- 任务跟进提醒
- 关键记忆回顾
- 闲聊问候（长时间未互动时）

频率由用户反馈自适应调整，安静时段(23:00-07:00)不发送。

**适用场景**：
- 用户要求开启/关闭主动消息
- 用户说"别主动给我发消息了"
- 用户说"开启活人感"/"主动一点"等""",
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "是否启用活人感模式",
                }
            },
            "required": ["enabled"],
        },
    },
    {
        "name": "get_persona_profile",
        "category": "Persona",
        "description": "Get the current merged persona profile including preset, user customizations, and context adaptations.",
        "detail": """获取当前合并后的人格配置信息。

**返回信息**：
- 当前预设角色名称
- 沟通风格配置
- 用户偏好叠加
- 上下文适配
- 表情包配置
- 活人感模式状态

**适用场景**：
- 用户询问当前角色配置
- 需要确认人格设置""",
        "input_schema": {"type": "object", "properties": {}},
    },
]

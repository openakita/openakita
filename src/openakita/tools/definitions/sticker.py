"""
表情包工具定义

包含表情包发送相关的工具:
- send_sticker: 搜索并发送表情包图片
"""

STICKER_TOOLS = [
    {
        "name": "send_sticker",
        "category": "IM Channel",
        "description": "Search and send a sticker/meme image to express emotions. Use during casual chat, greetings, encouragement, etc. to make conversation more vivid.",
        "detail": """搜索并发送表情包图片来表达情绪。闲聊时增加趣味性，让对话更生动。

**搜索方式**（二选一或组合）：
- query: 关键词搜索（如：鼓掌/开心/加油/摸鱼/害怕/比心）
- mood: 情绪类型搜索（happy/sad/angry/greeting/encourage/love/tired/surprise）

**可选过滤**：
- category: 限定分类（如：猫/企鹅/程序员）

**使用时机**：
- 闲聊问候时
- 鼓励用户时
- 表达情绪时
- 庆祝任务完成时
- 注意：需要遵循当前角色的表情包使用频率设定""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（如：鼓掌/开心/加油/摸鱼/害怕/比心）",
                },
                "mood": {
                    "type": "string",
                    "enum": [
                        "happy",
                        "sad",
                        "angry",
                        "greeting",
                        "encourage",
                        "love",
                        "tired",
                        "surprise",
                    ],
                    "description": "情绪类型，与 query 二选一",
                },
                "category": {
                    "type": "string",
                    "description": "可选，限定分类（如：猫/企鹅/程序员）",
                },
            },
        },
    },
]

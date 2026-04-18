"""
Sticker tool definitions

Contains tools related to sending stickers:
- send_sticker: Search and send sticker/meme images
"""

STICKER_TOOLS = [
    {
        "name": "send_sticker",
        "category": "IM Channel",
        "description": "Search and send a sticker/meme image to express emotions. Use during casual chat, greetings, encouragement, etc. to make conversation more vivid.",
        "detail": """Search and send sticker/meme images to express emotions. Adds fun during casual chat and makes conversations more vivid.

**Search methods** (use one or both):
- query: Keyword search (e.g., applause/happy/cheer/slacking off/scared/heart)
- mood: Mood type search (happy/sad/angry/greeting/encourage/love/tired/surprise)

**Optional filters**:
- category: Restrict to a category (e.g., cats/penguins/programmers)

**When to use**:
- Casual chat and greetings
- Encouraging the user
- Expressing emotions
- Celebrating task completion
- Note: Follow the current character's sticker usage frequency settings

**Important**: Stickers can only be sent via this tool. Do not describe stickers in text replies as a substitute for actually sending them.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keywords (e.g., applause/happy/cheer/slacking off/scared/heart)",
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
                    "description": "Mood type, use as an alternative to query",
                },
                "category": {
                    "type": "string",
                    "description": "Optional, restrict to a category (e.g., cats/penguins/programmers)",
                },
            },
        },
    },
]

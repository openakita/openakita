"""
Persona system + living-presence tool definitions

Contains tools related to persona management and living-presence mode:
- switch_persona: Switch persona preset
- update_persona_trait: Update persona preference trait
- toggle_proactive: Toggle living-presence mode on/off
- get_persona_profile: Get current persona configuration
"""

PERSONA_TOOLS = [
    {
        "name": "switch_persona",
        "category": "Persona",
        "description": "Switch to a built-in persona preset or a user-created Agent role. Built-in presets: default/business/tech_expert/butler/girlfriend/boyfriend/family/jarvis. Also supports user-created role names (e.g., 'Zhuge Liang', 'Translator', etc.). Use when the user requests a role or communication style change.",
        "detail": """Switch the Agent's persona role.

**Built-in presets**:
- default: Default assistant (professional and friendly)
- business: Business assistant (formal and efficient)
- tech_expert: Tech expert (rigorous and in-depth)
- butler: Personal butler (thoughtful and attentive)
- girlfriend: Girlfriend vibe (warm and caring)
- boyfriend: Boyfriend vibe (sunny and encouraging)
- family: Family vibe (warm and chatty)
- jarvis: Jarvis (British humor, slightly rebellious, talkative, rigorous during tasks)

Also accepts user-created Agent role names; the system will automatically look up the matching Agent Profile.

**When to use**:
- User requests a role/personality switch
- User says things like "be more formal" / "be more casual"
- User says "switch to Zhuge Liang" / "use the XX role" etc.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "preset_name": {
                    "type": "string",
                    "description": "Preset name or user-created role name",
                }
            },
            "required": ["preset_name"],
        },
    },
    {
        "name": "update_persona_trait",
        "category": "Persona",
        "description": "Update a specific persona preference dimension (formality, humor, emoji_usage, sticker_preference, etc.) based on user feedback or explicit request. Use this for ALL communication-style preferences including sticker/emoji/humor settings.",
        "detail": """Update a persona preference dimension for the user.

**Supported dimensions**:
- formality: Formality level (very_formal/formal/neutral/casual/very_casual)
- humor: Humor level (none/occasional/frequent)
- emoji_usage: Emoji usage (never/rare/moderate/frequent)
- reply_length: Reply length (very_short/short/moderate/detailed/very_detailed)
- proactiveness: Proactiveness level (silent/low/moderate/high)
- emotional_distance: Emotional distance (professional/friendly/close/intimate)
- address_style: Address style (free text)
- encouragement: Encouragement level (none/occasional/frequent)
- care_topics: Care topics (free text)
- sticker_preference: Sticker preference (never/rare/moderate/frequent)

**When to use**:
- User explicitly states a preference ("be more casual" / "don't use emojis" etc.)
- A preference change is inferred from the conversation""",
        "input_schema": {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "description": "Preference dimension name",
                },
                "preference": {
                    "type": "string",
                    "description": "Preference value",
                },
                "source": {
                    "type": "string",
                    "description": "Source (explicit=user stated directly/mined=inferred from conversation/correction=user correction)",
                    "enum": ["explicit", "mined", "correction"],
                },
                "evidence": {
                    "type": "string",
                    "description": "Evidence description (what the user said)",
                },
            },
            "required": ["dimension", "preference"],
        },
    },
    {
        "name": "toggle_proactive",
        "category": "Persona",
        "description": "Toggle the proactive/living-presence mode on or off. Controls whether the agent sends proactive messages (greetings, reminders, follow-ups).",
        "detail": """Toggle the living-presence mode on or off.

When enabled, the Agent proactively sends messages:
- Good morning / good night greetings
- Task follow-up reminders
- Key memory reviews
- Casual check-ins (when no interaction for a long time)

Frequency adapts based on user feedback; no messages during quiet hours (23:00-07:00).

**When to use**:
- User requests enabling/disabling proactive messages
- User says "stop sending me messages on your own"
- User says "enable living presence" / "be more proactive" etc.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "Whether to enable living-presence mode",
                }
            },
            "required": ["enabled"],
        },
    },
    {
        "name": "get_persona_profile",
        "category": "Persona",
        "description": "Get the current merged persona profile including preset, user customizations, and context adaptations.",
        "detail": """Get the current merged persona configuration.

**Returns**:
- Current preset role name
- Communication style settings
- User preference overrides
- Context adaptations
- Sticker configuration
- Living-presence mode status

**When to use**:
- User asks about current role configuration
- Need to confirm persona settings""",
        "input_schema": {"type": "object", "properties": {}},
    },
]

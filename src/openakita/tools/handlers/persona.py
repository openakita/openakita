"""
Persona system + proactive engagement handler

Handles tool calls related to persona and proactive engagement:
- switch_persona: Switch to a preset or user-created Agent role
- update_persona_trait: Update preference traits
- toggle_proactive: Toggle proactive engagement on/off
- get_persona_profile: Get persona configuration
"""

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


def _find_agent_profile_by_name(name: str):
    """Fuzzily find a user-created AgentProfile by name.

    Returns (profile, store) or (None, None).
    """
    try:
        from ...agents.profile import ProfileStore
        from ...config import settings

        store_dir = settings.data_dir / "agents"
        if not store_dir.exists():
            return None, None
        store = ProfileStore(store_dir)
        name_lower = name.strip().lower()
        for p in store.list_all(include_ephemeral=False):
            if p.name.strip().lower() == name_lower:
                return p, store
        for p in store.list_all(include_ephemeral=False):
            if name_lower in p.name.strip().lower():
                return p, store
    except Exception as e:
        logger.debug(f"Agent profile lookup failed: {e}")
    return None, None


class PersonaHandler:
    """Persona system handler"""

    TOOLS = [
        "switch_persona",
        "update_persona_trait",
        "toggle_proactive",
        "get_persona_profile",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """Handle tool calls"""
        if tool_name == "switch_persona":
            return self._switch_persona(params)
        elif tool_name == "update_persona_trait":
            return self._update_persona_trait(params)
        elif tool_name == "toggle_proactive":
            return self._toggle_proactive(params)
        elif tool_name == "get_persona_profile":
            return self._get_persona_profile(params)
        else:
            return f"❌ Unknown persona tool: {tool_name}"

    def _switch_persona(self, params: dict) -> str:
        """Switch persona preset or user-created Agent role"""
        preset_name = params.get("preset_name", "default")

        if not hasattr(self.agent, "persona_manager") or not self.agent.persona_manager:
            return "❌ Persona system not initialized"

        # 1) Try built-in presets first
        success = self.agent.persona_manager.switch_preset(preset_name)
        if success:
            from ...config import runtime_state, settings

            settings.persona_name = preset_name
            runtime_state.save()
            return (
                f"✅ Persona switched to: {preset_name}\n\n"
                f"Available presets: {', '.join(self.agent.persona_manager.available_presets)}"
            )

        # 2) Built-in preset not matched, try user-created Agent Profile
        profile, _ = _find_agent_profile_by_name(preset_name)
        if profile:
            switched = self._switch_to_agent_profile(profile)
            if switched:
                return (
                    f"✅ Switched to custom role '{profile.name}' ({profile.description or 'no description'})\n"
                    f"The role will take effect starting from the next message."
                )

        # 3) Nothing matched
        available_presets = self.agent.persona_manager.available_presets
        lines = [f"❌ No preset or custom role found with the name '{preset_name}'\n"]
        lines.append(f"**Built-in presets**: {', '.join(available_presets)}")
        try:
            from ...agents.profile import ProfileStore
            from ...config import settings

            store_dir = settings.data_dir / "agents"
            if store_dir.exists():
                store = ProfileStore(store_dir)
                custom_profiles = [
                    p for p in store.list_all(include_ephemeral=False) if p.type.value == "custom"
                ]
                if custom_profiles:
                    names = [f"{p.icon} {p.name}" for p in custom_profiles]
                    lines.append(f"**Custom roles**: {', '.join(names)}")
        except Exception:
            pass
        return "\n".join(lines)

    def _switch_to_agent_profile(self, profile) -> bool:
        """Switch the current session's agent_profile_id to the target AgentProfile."""
        try:
            session = getattr(self.agent, "_current_session", None)
            if session is None:
                logger.warning("[switch_persona] No active session, cannot switch profile")
                return False
            ctx = getattr(session, "context", None)
            if ctx is None:
                logger.warning("[switch_persona] Session has no context")
                return False
            old_id = getattr(ctx, "agent_profile_id", "default")
            ctx.agent_profile_id = profile.id
            logger.info(
                f"[switch_persona] Switched agent_profile_id: {old_id} -> {profile.id} "
                f"({profile.name})"
            )
            return True
        except Exception as e:
            logger.error(f"[switch_persona] Profile switch failed: {e}")
            return False

    def _update_persona_trait(self, params: dict) -> str:
        """Update persona preference traits"""
        from ...core.persona import PersonaTrait

        if not hasattr(self.agent, "persona_manager") or not self.agent.persona_manager:
            return "❌ Persona system not initialized"

        dimension = params.get("dimension", "")
        preference = params.get("preference", "")
        source = params.get("source", "explicit")
        evidence = params.get("evidence", "")

        if not dimension or not preference:
            return "❌ Must provide dimension and preference"

        trait = PersonaTrait(
            id=str(uuid.uuid4())[:8],
            dimension=dimension,
            preference=preference,
            confidence=0.9 if source == "explicit" else 0.6,
            source=source,
            evidence=evidence,
        )

        self.agent.persona_manager.add_trait(trait)

        # Also write to memory system (deduplicate by dimension: keep only the latest value for each dimension)
        if hasattr(self.agent, "memory_manager") and self.agent.memory_manager:
            from ...memory.types import Memory, MemoryPriority, MemoryType

            mm = self.agent.memory_manager
            store = getattr(mm, "store", None)

            # Find existing memory for the same dimension, update instead of creating new
            if store:
                existing = store.query_semantic(memory_type="persona_trait", limit=50)
                for old in existing:
                    if old.content.startswith(f"{dimension}="):
                        store.update_semantic(
                            old.id,
                            {
                                "content": f"{dimension}={preference}",
                                "importance_score": max(old.importance_score, trait.confidence),
                            },
                        )
                        return f"✅ Updated persona preference: {dimension} = {preference} (source: {source})"

            memory = Memory(
                type=MemoryType.PERSONA_TRAIT,
                priority=MemoryPriority.LONG_TERM,
                content=f"{dimension}={preference}",
                source=source,
                tags=[f"dimension:{dimension}", f"preference:{preference}"],
                importance_score=trait.confidence,
            )
            mm.add_memory(memory)

        return f"✅ Updated persona preference: {dimension} = {preference} (source: {source})"

    def _toggle_proactive(self, params: dict) -> str:
        """Toggle proactive engagement mode"""
        enabled = params.get("enabled", False)

        if not hasattr(self.agent, "proactive_engine") or not self.agent.proactive_engine:
            return "❌ Proactive engagement engine not initialized"

        self.agent.proactive_engine.toggle(enabled)
        # Update config and persist
        from ...config import runtime_state, settings

        settings.proactive_enabled = enabled
        runtime_state.save()

        if enabled:
            return "✅ Proactive engagement mode enabled! I will send you greetings and reminders from time to time.\n\nYou can say 'disable proactive engagement' at any time to turn it off."
        else:
            return "✅ Proactive engagement mode disabled. I will no longer send messages proactively."

    def _get_persona_profile(self, params: dict) -> str:
        """Get current persona configuration"""
        if not hasattr(self.agent, "persona_manager") or not self.agent.persona_manager:
            return "❌ Persona system not initialized"

        merged = self.agent.persona_manager.get_merged_persona()

        lines = [
            "## Current Persona Configuration",
            "",
            f"**Preset role**: {merged.preset_name}",
            f"**Formality**: {merged.formality}",
            f"**Humor**: {merged.humor}",
            f"**Emoji usage**: {merged.emoji_usage}",
            f"**Reply length**: {merged.reply_length}",
            f"**Proactiveness**: {merged.proactiveness}",
            f"**Emotional distance**: {merged.emotional_distance}",
            f"**Encouragement**: {merged.encouragement}",
            f"**Sticker preference**: {merged.sticker_preference}",
        ]

        if merged.address_style:
            lines.append(f"**Address style**: {merged.address_style}")

        if merged.care_topics:
            lines.append(f"**Care topics**: {', '.join(merged.care_topics)}")

        # Proactive engagement status
        proactive_status = (
            "Enabled"
            if (
                hasattr(self.agent, "proactive_engine")
                and self.agent.proactive_engine
                and self.agent.proactive_engine.config.enabled
            )
            else "Disabled"
        )
        lines.append(f"\n**Proactive engagement mode**: {proactive_status}")

        if merged.user_customizations:
            lines.append(f"\n### User Preference Overrides\n{merged.user_customizations}")

        if merged.context_adaptations:
            lines.append(f"\n### Context Adaptations\n{merged.context_adaptations}")

        return "\n".join(lines)


def create_handler(agent: "Agent"):
    """Create persona handler"""
    handler = PersonaHandler(agent)
    return handler.handle

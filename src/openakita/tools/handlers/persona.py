"""
人格系统 + 活人感处理器

处理人格和活人感相关的工具调用:
- switch_persona: 切换预设
- update_persona_trait: 更新偏好特质
- toggle_proactive: 开关活人感
- get_persona_profile: 获取人格配置
"""

import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class PersonaHandler:
    """人格系统处理器"""

    TOOLS = [
        "switch_persona",
        "update_persona_trait",
        "toggle_proactive",
        "get_persona_profile",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
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
        """切换人格预设"""
        preset_name = params.get("preset_name", "default")

        if not hasattr(self.agent, "persona_manager") or not self.agent.persona_manager:
            return "❌ 人格系统未初始化"

        success = self.agent.persona_manager.switch_preset(preset_name)
        if success:
            # 更新配置并持久化
            from ...config import runtime_state, settings
            settings.persona_name = preset_name
            runtime_state.save()
            return f"✅ 已切换人格为: {preset_name}\n\n当前可用预设: {', '.join(self.agent.persona_manager.available_presets)}"
        else:
            available = self.agent.persona_manager.available_presets
            return f"❌ 预设 '{preset_name}' 不存在\n可用预设: {', '.join(available)}"

    def _update_persona_trait(self, params: dict) -> str:
        """更新人格偏好特质"""
        from ...core.persona import PersonaTrait

        if not hasattr(self.agent, "persona_manager") or not self.agent.persona_manager:
            return "❌ 人格系统未初始化"

        dimension = params.get("dimension", "")
        preference = params.get("preference", "")
        source = params.get("source", "explicit")
        evidence = params.get("evidence", "")

        if not dimension or not preference:
            return "❌ 需要提供 dimension 和 preference"

        trait = PersonaTrait(
            id=str(uuid.uuid4())[:8],
            dimension=dimension,
            preference=preference,
            confidence=0.9 if source == "explicit" else 0.6,
            source=source,
            evidence=evidence,
        )

        self.agent.persona_manager.add_trait(trait)

        # 同时写入记忆系统
        if hasattr(self.agent, "memory_manager") and self.agent.memory_manager:
            from ...memory.types import Memory, MemoryPriority, MemoryType

            memory = Memory(
                type=MemoryType.PERSONA_TRAIT,
                priority=MemoryPriority.LONG_TERM,
                content=f"{dimension}={preference}",
                source=source,
                tags=[f"dimension:{dimension}", f"preference:{preference}"],
                importance_score=trait.confidence,
            )
            self.agent.memory_manager.add_memory(memory)

        return f"✅ 已更新人格偏好: {dimension} = {preference} (来源: {source})"

    def _toggle_proactive(self, params: dict) -> str:
        """开关活人感模式"""
        enabled = params.get("enabled", False)

        if not hasattr(self.agent, "proactive_engine") or not self.agent.proactive_engine:
            return "❌ 活人感引擎未初始化"

        self.agent.proactive_engine.toggle(enabled)
        # 更新配置并持久化
        from ...config import runtime_state, settings
        settings.proactive_enabled = enabled
        runtime_state.save()

        if enabled:
            return "✅ 已开启活人感模式！我会不定期给你发问候和提醒~\n\n你可以随时说「关闭活人感」来关闭。"
        else:
            return "✅ 已关闭活人感模式，我不会再主动发消息了。"

    def _get_persona_profile(self, params: dict) -> str:
        """获取当前人格配置"""
        if not hasattr(self.agent, "persona_manager") or not self.agent.persona_manager:
            return "❌ 人格系统未初始化"

        merged = self.agent.persona_manager.get_merged_persona()

        lines = [
            f"## 当前人格配置",
            f"",
            f"**预设角色**: {merged.preset_name}",
            f"**正式程度**: {merged.formality}",
            f"**幽默感**: {merged.humor}",
            f"**表情使用**: {merged.emoji_usage}",
            f"**回复长度**: {merged.reply_length}",
            f"**主动程度**: {merged.proactiveness}",
            f"**情感距离**: {merged.emotional_distance}",
            f"**鼓励程度**: {merged.encouragement}",
            f"**表情包偏好**: {merged.sticker_preference}",
        ]

        if merged.address_style:
            lines.append(f"**称呼方式**: {merged.address_style}")

        if merged.care_topics:
            lines.append(f"**关心话题**: {', '.join(merged.care_topics)}")

        # 活人感状态
        proactive_status = "已开启" if (
            hasattr(self.agent, "proactive_engine")
            and self.agent.proactive_engine
            and self.agent.proactive_engine.config.enabled
        ) else "已关闭"
        lines.append(f"\n**活人感模式**: {proactive_status}")

        if merged.user_customizations:
            lines.append(f"\n### 用户偏好叠加\n{merged.user_customizations}")

        if merged.context_adaptations:
            lines.append(f"\n### 上下文适配\n{merged.context_adaptations}")

        return "\n".join(lines)


def create_handler(agent: "Agent"):
    """创建人格处理器"""
    handler = PersonaHandler(agent)
    return handler.handle

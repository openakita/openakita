"""
Three-layer hierarchical persona management module

Layer 1: Base preset layer (identity/personas/*.md)
Layer 2: User customization overlay (identity/personas/user_custom.md + PERSONA_TRAIT memories)
Layer 3: Context adaptation layer (time/task/mood)

Merge algorithm: preset -> user customization override -> context-adaptive adjustment
"""

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openakita.memory.types import normalize_tags

logger = logging.getLogger(__name__)


# ── Preference dimension definitions ──────────────────────────────

PERSONA_DIMENSIONS = {
    "formality": {
        "range": ["very_formal", "formal", "neutral", "casual", "very_casual"],
        "question": "Do you prefer me to speak more formally or casually?",
        "priority": 1,
    },
    "humor": {
        "range": ["none", "occasional", "frequent"],
        "question": "Would you like me to crack a joke now and then?",
        "priority": 2,
    },
    "emoji_usage": {
        "range": ["never", "rare", "moderate", "frequent"],
        "question": "Do you like me using emoji in replies?",
        "priority": 3,
    },
    "reply_length": {
        "range": ["very_short", "short", "moderate", "detailed", "very_detailed"],
        "question": "Do you prefer concise replies or detailed ones?",
        "priority": 4,
    },
    "proactiveness": {
        "range": ["silent", "low", "moderate", "high"],
        "question": "Would you like me to message you proactively, e.g., greetings or reminders?",
        "priority": 2,
    },
    "emotional_distance": {
        "range": ["professional", "friendly", "close", "intimate"],
        "question": "What kind of relationship would you like us to have? Professional or closer?",
        "priority": 3,
    },
    "address_style": {
        "range": "free_text",
        "question": "How would you like me to address you?",
        "priority": 1,
    },
    "encouragement": {
        "range": ["none", "occasional", "frequent"],
        "question": "Do you like me to encourage you when you complete tasks?",
        "priority": 4,
    },
    "care_topics": {
        "range": "free_text_list",
        "question": "Are there any topics you'd like me to pay special attention to or remind you about?",
        "priority": 3,
    },
    "sticker_preference": {
        "range": ["never", "rare", "moderate", "frequent"],
        "question": "Do you like me to send stickers?",
        "priority": 4,
    },
}


# ── Data structures ───────────────────────────────────────────────


@dataclass
class PersonaTrait:
    """User persona preference trait"""

    id: str
    dimension: str  # Dimension name (formality/humor/...)
    preference: str  # Preference value
    confidence: float  # Confidence 0-1
    source: str  # Source (explicit/mined/feedback/correction)
    evidence: str  # Evidence description
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    reinforcement_count: int = 0  # Reinforcement count

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "dimension": self.dimension,
            "preference": self.preference,
            "confidence": self.confidence,
            "source": self.source,
            "evidence": self.evidence,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "reinforcement_count": self.reinforcement_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PersonaTrait":
        return cls(
            id=data.get("id", f"trait_{data.get('dimension', 'unknown')}_{id(data)}"),
            dimension=data["dimension"],
            preference=data["preference"],
            confidence=data.get("confidence", 0.5),
            source=data.get("source", "mined"),
            evidence=data.get("evidence", ""),
            created_at=datetime.fromisoformat(data["created_at"])
            if "created_at" in data
            else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"])
            if "updated_at" in data
            else datetime.now(),
            reinforcement_count=data.get("reinforcement_count", 0),
        )


@dataclass
class MergedPersona:
    """Final merged persona description"""

    preset_name: str = "default"
    personality: str = ""
    communication_style: str = ""
    prompt_snippet: str = ""
    user_customizations: str = ""
    context_adaptations: str = ""
    sticker_config: str = ""

    # Merged dimension values
    formality: str = "neutral"
    humor: str = "occasional"
    emoji_usage: str = "rare"
    reply_length: str = "moderate"
    proactiveness: str = "low"
    emotional_distance: str = "friendly"
    address_style: str = ""
    encouragement: str = "occasional"
    care_topics: list[str] = field(default_factory=list)
    sticker_preference: str = "rare"


# ── Preset parsing ────────────────────────────────────────────────


def _parse_preset_field(content: str, section_name: str) -> str:
    """Extract the content of a specified section from a Markdown preset file"""
    pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
    match = re.search(pattern, content, re.DOTALL)
    return match.group(1).strip() if match else ""


def _parse_dimension_from_style(style_text: str, dimension: str) -> str | None:
    """Extract dimension value from communication style text"""
    # Matches "- 正式程度: formal" or "- 幽默感: occasional" etc.
    # NOTE: Chinese labels kept — these match Chinese section headings in preset .md files.
    dim_map = {
        "formality": "正式程度",
        "humor": "幽默感",
        "reply_length": "回复长度",
        "emotional_distance": "情感距离",
        "emoji_usage": "表情使用",
    }
    label = dim_map.get(dimension, "")
    if not label:
        return None
    pattern = rf"-\s*{re.escape(label)}:\s*(\w+)"
    match = re.search(pattern, style_text)
    if match:
        # Extract the English value before parentheses
        val = match.group(1).strip()
        return val
    return None


# ── PersonaManager ────────────────────────────────────────────────


class PersonaManager:
    """Three-layer persona manager"""

    def __init__(self, personas_dir: Path | str, active_preset: str = "default"):
        self.personas_dir = (
            Path(personas_dir) if not isinstance(personas_dir, Path) else personas_dir
        )
        self.active_preset_name = active_preset
        self.user_traits: list[PersonaTrait] = []
        self._preset_cache: dict[str, str] = {}
        self._traits_lock = threading.Lock()  # Protects concurrent access to user_traits

    # ── Preset management ──

    @property
    def available_presets(self) -> list[str]:
        """List all available preset names"""
        presets = []
        if self.personas_dir.exists():
            for f in self.personas_dir.glob("*.md"):
                name = f.stem
                if name != "user_custom":
                    presets.append(name)
        return sorted(presets)

    def switch_preset(self, preset_name: str) -> bool:
        """Switch preset role"""
        if preset_name not in self.available_presets:
            logger.warning(f"Preset '{preset_name}' not found, available: {self.available_presets}")
            return False
        self.active_preset_name = preset_name
        logger.info(f"Persona switched to: {preset_name}")
        return True

    def load_preset(self, preset_name: str) -> MergedPersona:
        """Load and parse a preset file"""
        preset_file = self.personas_dir / f"{preset_name}.md"
        if not preset_file.exists():
            logger.warning(f"Preset file not found: {preset_file}, falling back to default")
            preset_file = self.personas_dir / "default.md"
            if not preset_file.exists():
                return MergedPersona(preset_name=preset_name)

        content = preset_file.read_text(encoding="utf-8")
        self._preset_cache[preset_name] = content

        persona = MergedPersona(preset_name=preset_name)
        # NOTE: Chinese section names kept — they match section headings in preset .md files.
        persona.personality = _parse_preset_field(content, "性格特征")
        persona.communication_style = _parse_preset_field(content, "沟通风格")
        persona.prompt_snippet = _parse_preset_field(content, "提示词片段")
        persona.sticker_config = _parse_preset_field(content, "表情包配置")

        # Parse specific dimension values
        style_text = persona.communication_style
        for dim_key in ["formality", "humor", "reply_length", "emotional_distance", "emoji_usage"]:
            val = _parse_dimension_from_style(style_text, dim_key)
            if val:
                setattr(persona, dim_key, val)

        # Parse sticker frequency
        # NOTE: Chinese pattern kept — matches field label in preset .md files.
        sticker_text = persona.sticker_config
        freq_match = re.search(r"使用频率:\s*(\w+)", sticker_text)
        if freq_match:
            persona.sticker_preference = freq_match.group(1).strip()

        return persona

    # ── User trait management ──

    def add_trait(self, trait: PersonaTrait) -> None:
        """Add or update a user preference trait (thread-safe)"""
        with self._traits_lock:
            # Check whether a trait for the same dimension already exists
            for i, existing in enumerate(self.user_traits):
                if existing.dimension == trait.dimension:
                    # If the new value matches, bump the reinforcement count
                    if existing.preference == trait.preference:
                        existing.reinforcement_count += 1
                        existing.confidence = min(1.0, existing.confidence + 0.1)
                        existing.updated_at = datetime.now()
                        logger.info(
                            f"Trait reinforced: {trait.dimension}={trait.preference} "
                            f"(count={existing.reinforcement_count}, conf={existing.confidence:.2f})"
                        )
                        return
                    # If the new value differs and has higher confidence, replace
                    elif trait.confidence > existing.confidence:
                        self.user_traits[i] = trait
                        logger.info(
                            f"Trait updated: {trait.dimension} "
                            f"{existing.preference}->{trait.preference}"
                        )
                        return
                    else:
                        logger.debug(
                            f"Trait ignored (lower confidence): {trait.dimension}="
                            f"{trait.preference} ({trait.confidence:.2f} < {existing.confidence:.2f})"
                        )
                        return
            # Append new
            self.user_traits.append(trait)
            logger.info(
                f"Trait added: {trait.dimension}={trait.preference} (conf={trait.confidence:.2f})"
            )

    def load_traits_from_memories(self, memories: list[dict]) -> None:
        """Load PERSONA_TRAIT-type memories from the memory system"""
        for mem in memories:
            if mem.get("type") != "persona_trait":
                continue
            # Parse content format: "dimension:value (confidence:X, source:Y, evidence:Z)"
            try:
                trait = self._parse_trait_from_memory(mem)
                if trait:
                    self.add_trait(trait)
            except Exception as e:
                logger.warning(f"Failed to parse persona trait from memory: {e}")

    def _parse_trait_from_memory(self, mem: dict) -> PersonaTrait | None:
        """Parse a PersonaTrait from a memory dict"""
        content = mem.get("content", "")
        tags = normalize_tags(mem.get("tags"))

        # Try to extract dimension info from tags
        dimension = None
        preference = None
        for tag in tags:
            if tag.startswith("dimension:"):
                dimension = tag.split(":", 1)[1]
            elif tag.startswith("preference:"):
                preference = tag.split(":", 1)[1]

        if not dimension or not preference:
            # Try to parse "dimension=value" format from content
            match = re.match(r"(\w+)\s*[=:]\s*(.+?)(?:\s*\(|$)", content)
            if match:
                dimension = match.group(1)
                preference = match.group(2).strip()
            else:
                return None

        return PersonaTrait(
            id=mem.get("id", ""),
            dimension=dimension,
            preference=preference,
            confidence=mem.get("importance_score", 0.5),
            source=mem.get("source", "mined"),
            evidence=content,
            created_at=datetime.fromisoformat(mem["created_at"])
            if "created_at" in mem
            else datetime.now(),
            updated_at=datetime.fromisoformat(mem["updated_at"])
            if "updated_at" in mem
            else datetime.now(),
        )

    # ── Context adaptation ──

    def get_current_context(self) -> dict[str, Any]:
        """Get current context info"""
        try:
            from zoneinfo import ZoneInfo

            from ..config import settings

            tz = ZoneInfo(settings.scheduler_timezone)
            now = datetime.now(tz)
        except Exception:
            now = datetime.now()
        hour = now.hour

        # Time-of-day classification
        if 5 <= hour < 9:
            time_period = "morning"
        elif 9 <= hour < 12:
            time_period = "forenoon"
        elif 12 <= hour < 14:
            time_period = "noon"
        elif 14 <= hour < 18:
            time_period = "afternoon"
        elif 18 <= hour < 22:
            time_period = "evening"
        else:
            time_period = "night"

        return {
            "time_period": time_period,
            "hour": hour,
            "weekday": now.weekday(),  # 0=Monday
            "is_weekend": now.weekday() >= 5,
        }

    def _apply_context_adaptations(self, persona: MergedPersona) -> str:
        """Generate adaptive notes based on context"""
        ctx = self.get_current_context()
        adaptations = []

        if ctx["time_period"] == "night":
            adaptations.append("It is late at night; tone should be gentler and quieter, replies concise")
        elif ctx["time_period"] == "morning":
            adaptations.append("It is morning; tone can be a bit more lively")

        if ctx["is_weekend"]:
            adaptations.append("Today is the weekend; tone can be more relaxed and casual")

        return "\n".join(f"- {a}" for a in adaptations) if adaptations else ""

    # ── Core merge algorithm ──

    def get_merged_persona(self) -> MergedPersona:
        """Merge the three persona layers into a final description"""
        # 1. Load base preset
        base = self.load_preset(self.active_preset_name)

        # 2. Apply user customization layer (overrides base values for the same dimension)
        customizations = []
        with self._traits_lock:
            traits_snapshot = list(self.user_traits)  # Snapshot to minimize lock hold time
        for trait in traits_snapshot:
            if trait.confidence >= 0.5:
                if hasattr(base, trait.dimension):
                    old_val = getattr(base, trait.dimension)
                    # Special handling for list-type fields (e.g., care_topics)
                    if isinstance(old_val, list):
                        # Append to list rather than overwrite
                        if trait.preference not in old_val:
                            old_val.append(trait.preference)
                        customizations.append(
                            f"- {trait.dimension}: +{trait.preference}"
                            f" (source: {trait.source}, confidence: {trait.confidence:.2f})"
                        )
                    else:
                        setattr(base, trait.dimension, trait.preference)
                        customizations.append(
                            f"- {trait.dimension}: {old_val} → {trait.preference}"
                            f" (source: {trait.source}, confidence: {trait.confidence:.2f})"
                        )
        base.user_customizations = "\n".join(customizations) if customizations else ""

        # 3. Load contents of user_custom.md
        user_custom_file = self.personas_dir / "user_custom.md"
        if user_custom_file.exists():
            custom_content = user_custom_file.read_text(encoding="utf-8")
            # Skip empty/placeholder content
            # NOTE: Chinese token kept — matches placeholder text in user_custom.md template.
            if "尚未收集" not in custom_content and len(custom_content.strip()) > 100:
                if base.user_customizations:
                    base.user_customizations += "\n\n--- user_custom.md ---\n" + custom_content
                else:
                    base.user_customizations = custom_content

        # 4. Apply context adaptation
        base.context_adaptations = self._apply_context_adaptations(base)

        return base

    # ── For prompt injection ──

    def get_persona_prompt_section(self) -> str:
        """Generate the persona description section for injection into the system prompt"""
        merged = self.get_merged_persona()

        parts = []
        parts.append(f"## Current persona: {merged.preset_name}")

        if merged.prompt_snippet:
            parts.append(f"\n### Role setting\n{merged.prompt_snippet}")

        if merged.communication_style:
            parts.append(f"\n### Communication style\n{merged.communication_style}")

        if merged.user_customizations:
            parts.append(f"\n### User preference overlay\n{merged.user_customizations}")

        if merged.context_adaptations:
            parts.append(f"\n### Current context adaptation\n{merged.context_adaptations}")

        if merged.sticker_config:
            parts.append(f"\n### Sticker configuration\n{merged.sticker_config}")

        return "\n".join(parts)

    def is_persona_active(self) -> bool:
        """Whether a non-default persona is active"""
        return self.active_preset_name != "default" or len(self.user_traits) > 0

    def get_next_question_dimension(self, asked_dimensions: set[str]) -> str | None:
        """Get the next preference dimension to ask about"""
        # Sort by priority
        sorted_dims = sorted(
            PERSONA_DIMENSIONS.items(),
            key=lambda x: x[1]["priority"],
        )
        for dim_key, _dim_info in sorted_dims:
            if dim_key in asked_dimensions:
                continue
            # Check whether we already have high-confidence data
            has_high_conf = any(
                t.dimension == dim_key and t.confidence >= 0.7 for t in self.user_traits
            )
            if not has_high_conf:
                return dim_key
        return None

    def get_question_for_dimension(self, dimension: str) -> str | None:
        """Get the question for the specified dimension"""
        dim_info = PERSONA_DIMENSIONS.get(dimension)
        return dim_info["question"] if dim_info else None

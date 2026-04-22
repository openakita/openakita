"""
Persona preference mining engine (Trait Miner)

Responsible for discovering and extracting user persona preferences from
multiple sources:
1. Explicit/implicit preference signals in conversation content (analyzed by LLM)
2. Signal analysis from user feedback
3. Proactive-question trigger management

Core principle: all preference analysis is delegated to the LLM (compiler model);
no keyword matching is used.
"""

import json
import logging
import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .persona import PersonaManager, PersonaTrait

logger = logging.getLogger(__name__)


# ── LLM analysis prompts ──────────────────────────────────────────────

TRAIT_MINING_SYSTEM = """You are a user-preference analysis expert. Your task is to identify signals about **communication style and interaction preferences** from user messages.

## Recognizable dimensions

| Dimension | Description | Allowed values |
|------|------|--------|
| formality | How formal the user's speech is | very_formal, formal, neutral, casual, very_casual |
| humor | Humor preference | none, occasional, frequent |
| emoji_usage | Emoji usage | never, rare, moderate, frequent |
| reply_length | Reply-length preference | very_short, short, moderate, detailed, very_detailed |
| proactiveness | Proactive-message preference | silent, low, moderate, high |
| emotional_distance | Emotional distance | professional, friendly, close, intimate |
| encouragement | Encouragement level | none, occasional, frequent |
| sticker_preference | Sticker preference | never, rare, moderate, frequent |
| address_style | Form of address | (free text) |
| care_topics | Topics the user cares about | (free text) |

## Signal types

1. **Direct correction** (confidence: 0.85-0.95): user explicitly asks for a style change
   - Example: "You sound too formal" → formality=casual
   - Example: "Stop sending stickers" → sticker_preference=never
   - Example: "Be more humorous" → humor=frequent

2. **Implicit signals** (confidence: 0.4-0.6): preferences hinted at by user behavior
   - User uses many emojis themselves → emoji_usage=moderate
   - User's tone is very casual / uses internet slang → formality=casual
   - User is active late at night → care_topics=health reminder:user often stays up late

3. **No signal**: plain task instructions, simple confirmations, and small talk carry no preference signals

## Important rules

- **Quality over quantity**: if there is no clear signal, return an empty array; do not over-interpret
- **Task instructions are not preference signals**: e.g., "check the weather", "open a file" contain no preferences
- **Short != prefers brevity**: "ok" or "mm" is just acknowledgment, not a short-reply preference
- **Only take the most explicit value per dimension**
- **Focus on the user's wording and tone itself**, not the topic of the message"""

TRAIT_MINING_PROMPT = """Analyze the following user message and extract persona preference signals.

User message:
```
{message}
```

If preference signals are found, return a JSON array:
```json
[{{"dimension": "dimension name", "preference": "preference value", "confidence": 0.5, "source": "correction or mined", "evidence": "reasoning"}}]
```

If there are no preference signals, return:
```json
[]
```

Output only JSON, nothing else."""


ANSWER_ANALYSIS_SYSTEM = """You are a user-preference analysis expert. The user has answered a question about a personal preference; analyze the answer and map it to the corresponding dimension value."""

ANSWER_ANALYSIS_PROMPT = """The user was asked the following question (regarding the {dimension} dimension):
"{question}"

User's answer:
"{answer}"

Dimension description: {dim_description}
Allowed values: {value_range}

Analyze the user's answer and return JSON:
```json
{{"preference": "best-matching value", "confidence": 0.9, "evidence": "reasoning"}}
```

Rules:
- If the user explicitly declines to answer (e.g., "skip", "never mind", "don't say"), return {{"skip": true}}
- For free-text dimensions (address_style, care_topics), extract the user's original meaning directly
- Output only JSON, nothing else."""


class TraitMiner:
    """
    Persona preference mining engine.

    All preference analysis is delegated to the LLM (compiler model
    compiler_think); no keyword matching, to avoid incomplete-rule coverage
    and false positives.
    """

    def __init__(self, persona_manager: "PersonaManager", brain: Any = None):
        """
        Args:
            persona_manager: PersonaManager instance
            brain: Brain instance (used for LLM calls). If not provided,
                   mine_from_message degrades into a no-op.
        """
        self.persona_manager = persona_manager
        self.brain = brain
        self._asked_dimensions: set[str] = set()
        self._last_question_date: datetime | None = None
        self._questions_today: int = 0

    async def mine_from_message(self, message: str, role: str = "user") -> list["PersonaTrait"]:
        """
        Mine preference signals from a single message (LLM-driven).

        Args:
            message: message content
            role: message role (user/assistant)

        Returns:
            List of extracted PersonaTrait objects
        """
        if role != "user":
            return []

        if not self.brain:
            logger.debug("[TraitMiner] No brain available, skipping LLM analysis")
            return []

        # Skip LLM calls for very short messages (<=3 chars) to save cost
        if len(message.strip()) <= 3:
            return []

        # 系统级"自言自语"（idle/heartbeat/agent 间转发的 system prompt）不
        # 反映真实用户偏好，跳过 LLM 调用，避免 idle_probe 风暴时把 compiler_think
        # 也卷进去烧 token。匹配开头的 [空闲检查] / [空闲心跳] / [系统] 等标签前缀。
        _stripped = message.lstrip()
        for _prefix in ("[空闲检查]", "[空闲心跳]", "[系统]", "[system]", "[idle]"):
            if _stripped.startswith(_prefix):
                return []

        try:
            from .tool_executor import smart_truncate as _st

            msg_trunc, _ = _st(message, 800, save_full=False, label="trait_msg")
            prompt = TRAIT_MINING_PROMPT.format(message=msg_trunc)
            response = await self.brain.compiler_think(
                prompt=prompt,
                system=TRAIT_MINING_SYSTEM,
            )

            if not response or not getattr(response, "content", None):
                return []

            traits = self._parse_trait_response(response.content)

            # Apply to persona_manager
            for trait in traits:
                self.persona_manager.add_trait(trait)

            if traits:
                logger.info(
                    f"[TraitMiner] LLM mined {len(traits)} trait(s): "
                    + ", ".join(f"{t.dimension}={t.preference}" for t in traits)
                )

            return traits

        except Exception as e:
            logger.debug(f"[TraitMiner] LLM analysis failed (non-critical): {e}")
            return []

    def _parse_trait_response(self, content: str) -> list["PersonaTrait"]:
        """Parse the JSON returned by the LLM into a list of PersonaTrait objects."""
        from .persona import PERSONA_DIMENSIONS, PersonaTrait

        if not content:
            return []

        # Extract JSON array
        json_match = re.search(r"\[[\s\S]*?\]", content)
        if not json_match:
            return []

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            logger.debug("[TraitMiner] Failed to parse JSON from LLM response")
            return []

        if not isinstance(data, list):
            return []

        traits: list[PersonaTrait] = []
        seen_dimensions: set[str] = set()

        for item in data:
            if not isinstance(item, dict):
                continue

            dimension = str(item.get("dimension", "")).strip()
            preference = str(item.get("preference", "")).strip()
            raw_confidence = item.get("confidence", 0.5)
            source = str(item.get("source", "mined"))
            evidence = str(item.get("evidence", ""))

            if not dimension or not preference:
                continue

            # Deduplicate on dimension
            if dimension in seen_dimensions:
                continue
            seen_dimensions.add(dimension)

            # Validate dimension is recognized
            if dimension not in PERSONA_DIMENSIONS:
                logger.debug(f"[TraitMiner] Unknown dimension '{dimension}', skipping")
                continue

            # Validate value range (for non-free-text dimensions)
            dim_info = PERSONA_DIMENSIONS[dimension]
            value_range = dim_info.get("range", [])
            if isinstance(value_range, list) and preference not in value_range:
                logger.debug(
                    f"[TraitMiner] Invalid preference '{preference}' "
                    f"for dimension '{dimension}', expected one of {value_range}"
                )
                continue

            # Filter out invalid values for free-text dimensions
            _INVALID_FREETEXT = {"任意文本", "unknown", "无", "null", "none", "n/a", "未知", ""}
            if preference.lower().strip() in _INVALID_FREETEXT:
                logger.debug(f"[TraitMiner] Rejected invalid freetext: {dimension}={preference}")
                continue

            # Clamp confidence range (guard against non-numeric LLM output)
            try:
                confidence = max(0.1, min(0.95, float(raw_confidence)))
            except (ValueError, TypeError):
                confidence = 0.5

            trait = PersonaTrait(
                id=str(uuid.uuid4())[:8],
                dimension=dimension,
                preference=preference,
                confidence=confidence,
                source=source if source in ("correction", "mined") else "mined",
                evidence=evidence[:100] if evidence else "LLM analysis of message content",
            )
            traits.append(trait)

        return traits

    # ── Proactive-question management ─────────────────────────────────

    def should_ask_question(self) -> bool:
        """Whether a persona-related question should be asked."""
        now = datetime.now()

        # At most 1 persona question per day
        if self._last_question_date and self._last_question_date.date() == now.date():
            if self._questions_today >= 1:
                return False

        # Check if there are still un-asked dimensions
        next_dim = self.persona_manager.get_next_question_dimension(self._asked_dimensions)
        return next_dim is not None

    def get_next_question(self) -> tuple[str, str] | None:
        """
        Get the next persona question to ask.

        Returns:
            (dimension, question) or None
        """
        dim = self.persona_manager.get_next_question_dimension(self._asked_dimensions)
        if not dim:
            return None

        question = self.persona_manager.get_question_for_dimension(dim)
        if not question:
            return None

        return (dim, question)

    def mark_question_asked(self, dimension: str) -> None:
        """Mark a dimension as already asked."""
        self._asked_dimensions.add(dimension)
        self._last_question_date = datetime.now()
        self._questions_today += 1

    async def process_answer(self, dimension: str, answer: str) -> Optional["PersonaTrait"]:
        """
        Process the user's answer to a persona question (LLM-driven).

        Args:
            dimension: dimension name
            answer: user's answer

        Returns:
            Extracted PersonaTrait, or None if the user skipped
        """
        from .persona import PERSONA_DIMENSIONS, PersonaTrait

        dim_info = PERSONA_DIMENSIONS.get(dimension)
        if not dim_info:
            return None

        # If brain is available, use the LLM to analyze the answer
        if self.brain:
            try:
                preference = await self._analyze_answer_with_llm(dimension, answer, dim_info)
                if preference is None:
                    # User skipped
                    self._asked_dimensions.add(dimension)
                    logger.info(f"User skipped question for dimension: {dimension}")
                    return None
            except Exception as e:
                logger.debug(f"[TraitMiner] LLM answer analysis failed: {e}")
                # Fallback: use the raw answer directly
                preference = answer.strip()
        else:
            preference = answer.strip()

        trait = PersonaTrait(
            id=str(uuid.uuid4())[:8],
            dimension=dimension,
            preference=preference,
            confidence=0.9,  # Explicit answers get high confidence
            source="explicit",
            evidence=f"User explicitly answered: '{answer[:50]}'",
        )

        self.persona_manager.add_trait(trait)
        self.mark_question_asked(dimension)
        return trait

    async def _analyze_answer_with_llm(
        self, dimension: str, answer: str, dim_info: dict
    ) -> str | None:
        """
        Use the LLM to analyze the user's answer to a preference question.

        Returns:
            The preference-value string, or None if the user skipped.
        """
        value_range = dim_info.get("range", [])
        if isinstance(value_range, list):
            range_desc = ", ".join(value_range)
        else:
            range_desc = f"free text ({value_range})"

        prompt = ANSWER_ANALYSIS_PROMPT.format(
            dimension=dimension,
            question=dim_info.get("question", ""),
            answer=answer[:200],
            dim_description=dim_info.get("question", dimension),
            value_range=range_desc,
        )

        response = await self.brain.compiler_think(
            prompt=prompt,
            system=ANSWER_ANALYSIS_SYSTEM,
        )

        if not response or not getattr(response, "content", None):
            return answer.strip()

        # Parse JSON
        json_match = re.search(r"\{[\s\S]*?\}", response.content)
        if not json_match:
            return answer.strip()

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            return answer.strip()

        if data.get("skip"):
            return None

        return data.get("preference", answer.strip())

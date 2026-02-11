"""
人格偏好挖掘引擎 (Trait Miner)

负责从多种来源发现和提取用户的人格偏好:
1. 对话内容中的显式/隐式偏好信号（由 LLM 分析）
2. 用户反馈的信号分析
3. 主动提问的触发管理

核心原则：所有偏好分析交由 LLM（编译模型）完成，不做关键词匹配。
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


# ── LLM 分析 Prompt ──────────────────────────────────────────────────

TRAIT_MINING_SYSTEM = """你是一个用户偏好分析专家。你的任务是从用户消息中识别关于**沟通风格和互动偏好**的信号。

## 可识别的维度

| 维度 | 说明 | 可选值 |
|------|------|--------|
| formality | 说话的正式程度 | very_formal, formal, neutral, casual, very_casual |
| humor | 幽默感偏好 | none, occasional, frequent |
| emoji_usage | 表情符号使用 | never, rare, moderate, frequent |
| reply_length | 回复长度偏好 | very_short, short, moderate, detailed, very_detailed |
| proactiveness | 主动消息偏好 | silent, low, moderate, high |
| emotional_distance | 情感距离 | professional, friendly, close, intimate |
| encouragement | 鼓励程度 | none, occasional, frequent |
| sticker_preference | 表情包偏好 | never, rare, moderate, frequent |
| address_style | 称呼方式 | (任意文本) |
| care_topics | 关心的话题 | (任意文本) |

## 信号类型

1. **直接修正** (confidence: 0.85-0.95): 用户明确要求改变风格
   - 例: "你说话太正式了" → formality=casual
   - 例: "别发表情包了" → sticker_preference=never
   - 例: "多幽默一点" → humor=frequent

2. **隐式信号** (confidence: 0.4-0.6): 用户行为暗示的偏好
   - 用户自己用了很多 emoji → emoji_usage=moderate
   - 用户语气很随意/用网络用语 → formality=casual
   - 用户深夜活跃 → care_topics=健康提醒:用户经常熬夜

3. **无信号**: 纯粹的任务指令、简单确认、闲聊内容不包含偏好信号

## 重要规则

- **宁缺毋滥**：没有明确信号就返回空数组，不要强行解读
- **任务指令不是偏好信号**：如 "帮我查天气" "打开文件" 等不包含任何偏好
- **简短≠偏好简洁**：用户说 "好的" "嗯" 只是确认，不代表偏好简短回复
- **同一维度只取最明确的一个**
- **关注用户的措辞和语气本身**，而不是消息的内容话题"""

TRAIT_MINING_PROMPT = """分析以下用户消息，提取人格偏好信号。

用户消息：
```
{message}
```

如果发现偏好信号，返回 JSON 数组：
```json
[{{"dimension": "维度名", "preference": "偏好值", "confidence": 0.5, "source": "correction或mined", "evidence": "识别依据"}}]
```

如果没有偏好信号，返回：
```json
[]
```

只输出 JSON，不要其他内容。"""


ANSWER_ANALYSIS_SYSTEM = """你是一个用户偏好分析专家。用户回答了一个关于个人偏好的问题，请分析回答内容并映射到对应的维度值。"""

ANSWER_ANALYSIS_PROMPT = """用户被问到以下问题（关于 {dimension} 维度）：
"{question}"

用户回答：
"{answer}"

维度说明：{dim_description}
可选值：{value_range}

请分析用户的回答，返回 JSON：
```json
{{"preference": "最匹配的值", "confidence": 0.9, "evidence": "判断依据"}}
```

规则：
- 如果用户明确拒绝回答（如"跳过""算了""不说"），返回 {{"skip": true}}
- 对于自由文本维度（address_style, care_topics），直接提取用户的原意
- 只输出 JSON，不要其他内容。"""


class TraitMiner:
    """
    人格偏好挖掘引擎

    所有偏好分析交由 LLM（编译模型 compiler_think）完成，
    不做关键词匹配，避免规则覆盖不全和误判。
    """

    def __init__(self, persona_manager: "PersonaManager", brain: Any = None):
        """
        Args:
            persona_manager: PersonaManager 实例
            brain: Brain 实例（用于 LLM 调用）。如果不提供，
                   mine_from_message 会退化为空操作。
        """
        self.persona_manager = persona_manager
        self.brain = brain
        self._asked_dimensions: set[str] = set()
        self._last_question_date: Optional[datetime] = None
        self._questions_today: int = 0

    async def mine_from_message(self, message: str, role: str = "user") -> list["PersonaTrait"]:
        """
        从单条消息中挖掘偏好信号（LLM 驱动）

        Args:
            message: 消息内容
            role: 消息角色 (user/assistant)

        Returns:
            提取到的 PersonaTrait 列表
        """
        if role != "user":
            return []

        if not self.brain:
            logger.debug("[TraitMiner] No brain available, skipping LLM analysis")
            return []

        # 过短的消息（≤3字）跳过 LLM 调用，节省开销
        if len(message.strip()) <= 3:
            return []

        try:
            prompt = TRAIT_MINING_PROMPT.format(message=message[:500])
            response = await self.brain.compiler_think(
                prompt=prompt,
                system=TRAIT_MINING_SYSTEM,
            )

            if not response or not getattr(response, "content", None):
                return []

            traits = self._parse_trait_response(response.content)

            # 应用到 persona_manager
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
        """解析 LLM 返回的 JSON 为 PersonaTrait 列表"""
        from .persona import PERSONA_DIMENSIONS, PersonaTrait

        if not content:
            return []

        # 提取 JSON 数组
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

            # 同一维度去重
            if dimension in seen_dimensions:
                continue
            seen_dimensions.add(dimension)

            # 校验维度是否合法
            if dimension not in PERSONA_DIMENSIONS:
                logger.debug(f"[TraitMiner] Unknown dimension '{dimension}', skipping")
                continue

            # 校验取值范围（非自由文本维度）
            dim_info = PERSONA_DIMENSIONS[dimension]
            value_range = dim_info.get("range", [])
            if isinstance(value_range, list) and preference not in value_range:
                logger.debug(
                    f"[TraitMiner] Invalid preference '{preference}' "
                    f"for dimension '{dimension}', expected one of {value_range}"
                )
                continue

            # 限制置信度范围（防御 LLM 返回非数字值）
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
                evidence=evidence[:100] if evidence else "LLM 分析消息内容",
            )
            traits.append(trait)

        return traits

    # ── 主动提问管理 ──────────────────────────────────────────────────

    def should_ask_question(self) -> bool:
        """是否应该提出人格相关问题"""
        now = datetime.now()

        # 每天最多 1 个人格问题
        if self._last_question_date and self._last_question_date.date() == now.date():
            if self._questions_today >= 1:
                return False

        # 检查是否还有未询问的维度
        next_dim = self.persona_manager.get_next_question_dimension(self._asked_dimensions)
        return next_dim is not None

    def get_next_question(self) -> Optional[tuple[str, str]]:
        """
        获取下一个要问的人格问题

        Returns:
            (dimension, question) 或 None
        """
        dim = self.persona_manager.get_next_question_dimension(self._asked_dimensions)
        if not dim:
            return None

        question = self.persona_manager.get_question_for_dimension(dim)
        if not question:
            return None

        return (dim, question)

    def mark_question_asked(self, dimension: str) -> None:
        """标记已经问过的维度"""
        self._asked_dimensions.add(dimension)
        self._last_question_date = datetime.now()
        self._questions_today += 1

    async def process_answer(self, dimension: str, answer: str) -> Optional["PersonaTrait"]:
        """
        处理用户对人格问题的回答（LLM 驱动）

        Args:
            dimension: 维度名
            answer: 用户回答

        Returns:
            提取的 PersonaTrait 或 None（如果用户跳过）
        """
        from .persona import PERSONA_DIMENSIONS, PersonaTrait

        dim_info = PERSONA_DIMENSIONS.get(dimension)
        if not dim_info:
            return None

        # 如果有 brain，用 LLM 分析回答
        if self.brain:
            try:
                preference = await self._analyze_answer_with_llm(
                    dimension, answer, dim_info
                )
                if preference is None:
                    # 用户跳过
                    self._asked_dimensions.add(dimension)
                    logger.info(f"User skipped question for dimension: {dimension}")
                    return None
            except Exception as e:
                logger.debug(f"[TraitMiner] LLM answer analysis failed: {e}")
                # 回退：直接用原文
                preference = answer.strip()
        else:
            preference = answer.strip()

        trait = PersonaTrait(
            id=str(uuid.uuid4())[:8],
            dimension=dimension,
            preference=preference,
            confidence=0.9,  # 显式回答置信度高
            source="explicit",
            evidence=f"用户明确回答: '{answer[:50]}'",
        )

        self.persona_manager.add_trait(trait)
        self.mark_question_asked(dimension)
        return trait

    async def _analyze_answer_with_llm(
        self, dimension: str, answer: str, dim_info: dict
    ) -> Optional[str]:
        """
        用 LLM 分析用户对偏好问题的回答

        Returns:
            偏好值字符串，或 None 表示用户跳过
        """
        value_range = dim_info.get("range", [])
        if isinstance(value_range, list):
            range_desc = ", ".join(value_range)
        else:
            range_desc = f"自由文本 ({value_range})"

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

        # 解析 JSON
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

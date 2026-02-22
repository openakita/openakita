"""
三层分层人格管理模块

Layer 1: 基础预设层 (identity/personas/*.md)
Layer 2: 用户自定义叠加层 (identity/personas/user_custom.md + PERSONA_TRAIT 记忆)
Layer 3: 上下文自适应层 (时间/任务/情绪)

合并算法: 预设 -> 用户自定义覆盖 -> 上下文自适应调整
"""

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── 偏好维度定义 ──────────────────────────────────────────────────

PERSONA_DIMENSIONS = {
    "formality": {
        "range": ["very_formal", "formal", "neutral", "casual", "very_casual"],
        "question": "你喜欢我说话正式一点还是随意一点？",
        "priority": 1,
    },
    "humor": {
        "range": ["none", "occasional", "frequent"],
        "question": "你希望我偶尔开个玩笑吗？",
        "priority": 2,
    },
    "emoji_usage": {
        "range": ["never", "rare", "moderate", "frequent"],
        "question": "你喜欢我在回复中使用 emoji 吗？",
        "priority": 3,
    },
    "reply_length": {
        "range": ["very_short", "short", "moderate", "detailed", "very_detailed"],
        "question": "你喜欢简洁的回复还是详细的回复？",
        "priority": 4,
    },
    "proactiveness": {
        "range": ["silent", "low", "moderate", "high"],
        "question": "你希望我主动给你发消息吗？比如问候、提醒之类的？",
        "priority": 2,
    },
    "emotional_distance": {
        "range": ["professional", "friendly", "close", "intimate"],
        "question": "你希望我们之间保持什么样的关系？专业的还是更亲近的？",
        "priority": 3,
    },
    "address_style": {
        "range": "free_text",
        "question": "你希望我怎么称呼你？",
        "priority": 1,
    },
    "encouragement": {
        "range": ["none", "occasional", "frequent"],
        "question": "你喜欢我在你完成任务时给你鼓励吗？",
        "priority": 4,
    },
    "care_topics": {
        "range": "free_text_list",
        "question": "有什么话题你希望我特别关注或提醒你的吗？",
        "priority": 3,
    },
    "sticker_preference": {
        "range": ["never", "rare", "moderate", "frequent"],
        "question": "你喜欢我发表情包吗？",
        "priority": 4,
    },
}


# ── 数据结构 ──────────────────────────────────────────────────────


@dataclass
class PersonaTrait:
    """用户人格偏好特质"""

    id: str
    dimension: str  # 维度名（formality/humor/...）
    preference: str  # 偏好值
    confidence: float  # 置信度 0-1
    source: str  # 来源（explicit/mined/feedback/correction）
    evidence: str  # 证据描述
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    reinforcement_count: int = 0  # 被强化次数

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
    """合并后的最终人格描述"""

    preset_name: str = "default"
    personality: str = ""
    communication_style: str = ""
    prompt_snippet: str = ""
    user_customizations: str = ""
    context_adaptations: str = ""
    sticker_config: str = ""

    # 合并后的具体维度值
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


# ── 预设解析 ──────────────────────────────────────────────────────


def _parse_preset_field(content: str, section_name: str) -> str:
    """从 Markdown 预设文件中提取指定 section 的内容"""
    pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
    match = re.search(pattern, content, re.DOTALL)
    return match.group(1).strip() if match else ""


def _parse_dimension_from_style(style_text: str, dimension: str) -> str | None:
    """从沟通风格文本中提取维度值"""
    # 匹配 "- 正式程度: formal" 或 "- 幽默感: occasional" 等
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
        # 提取括号前的英文值
        val = match.group(1).strip()
        return val
    return None


# ── PersonaManager ────────────────────────────────────────────────


class PersonaManager:
    """三层人格管理器"""

    def __init__(self, personas_dir: Path | str, active_preset: str = "default"):
        self.personas_dir = Path(personas_dir) if not isinstance(personas_dir, Path) else personas_dir
        self.active_preset_name = active_preset
        self.user_traits: list[PersonaTrait] = []
        self._preset_cache: dict[str, str] = {}
        self._traits_lock = threading.Lock()  # 保护 user_traits 的并发访问

    # ── 预设管理 ──

    @property
    def available_presets(self) -> list[str]:
        """列出所有可用的预设名"""
        presets = []
        if self.personas_dir.exists():
            for f in self.personas_dir.glob("*.md"):
                name = f.stem
                if name != "user_custom":
                    presets.append(name)
        return sorted(presets)

    def switch_preset(self, preset_name: str) -> bool:
        """切换预设角色"""
        if preset_name not in self.available_presets:
            logger.warning(f"Preset '{preset_name}' not found, available: {self.available_presets}")
            return False
        self.active_preset_name = preset_name
        logger.info(f"Persona switched to: {preset_name}")
        return True

    def load_preset(self, preset_name: str) -> MergedPersona:
        """加载并解析预设文件"""
        preset_file = self.personas_dir / f"{preset_name}.md"
        if not preset_file.exists():
            logger.warning(f"Preset file not found: {preset_file}, falling back to default")
            preset_file = self.personas_dir / "default.md"
            if not preset_file.exists():
                return MergedPersona(preset_name=preset_name)

        content = preset_file.read_text(encoding="utf-8")
        self._preset_cache[preset_name] = content

        persona = MergedPersona(preset_name=preset_name)
        persona.personality = _parse_preset_field(content, "性格特征")
        persona.communication_style = _parse_preset_field(content, "沟通风格")
        persona.prompt_snippet = _parse_preset_field(content, "提示词片段")
        persona.sticker_config = _parse_preset_field(content, "表情包配置")

        # 解析具体维度值
        style_text = persona.communication_style
        for dim_key in ["formality", "humor", "reply_length", "emotional_distance", "emoji_usage"]:
            val = _parse_dimension_from_style(style_text, dim_key)
            if val:
                setattr(persona, dim_key, val)

        # 解析表情包频率
        sticker_text = persona.sticker_config
        freq_match = re.search(r"使用频率:\s*(\w+)", sticker_text)
        if freq_match:
            persona.sticker_preference = freq_match.group(1).strip()

        return persona

    # ── 用户特质管理 ──

    def add_trait(self, trait: PersonaTrait) -> None:
        """添加或更新用户偏好特质（线程安全）"""
        with self._traits_lock:
            # 检查是否已存在同维度的 trait
            for i, existing in enumerate(self.user_traits):
                if existing.dimension == trait.dimension:
                    # 如果新值相同，增加强化计数
                    if existing.preference == trait.preference:
                        existing.reinforcement_count += 1
                        existing.confidence = min(1.0, existing.confidence + 0.1)
                        existing.updated_at = datetime.now()
                        logger.info(
                            f"Trait reinforced: {trait.dimension}={trait.preference} "
                            f"(count={existing.reinforcement_count}, conf={existing.confidence:.2f})"
                        )
                        return
                    # 如果新值不同且置信度更高，替换
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
            # 新增
            self.user_traits.append(trait)
            logger.info(f"Trait added: {trait.dimension}={trait.preference} (conf={trait.confidence:.2f})")

    def load_traits_from_memories(self, memories: list[dict]) -> None:
        """从记忆系统加载 PERSONA_TRAIT 类型的记忆"""
        for mem in memories:
            if mem.get("type") != "persona_trait":
                continue
            # 解析 content 格式: "dimension:value (confidence:X, source:Y, evidence:Z)"
            try:
                trait = self._parse_trait_from_memory(mem)
                if trait:
                    self.add_trait(trait)
            except Exception as e:
                logger.warning(f"Failed to parse persona trait from memory: {e}")

    def _parse_trait_from_memory(self, mem: dict) -> PersonaTrait | None:
        """从记忆字典中解析 PersonaTrait"""
        content = mem.get("content", "")
        tags = mem.get("tags", [])

        # 尝试从 tags 中获取维度信息
        dimension = None
        preference = None
        for tag in tags:
            if tag.startswith("dimension:"):
                dimension = tag.split(":", 1)[1]
            elif tag.startswith("preference:"):
                preference = tag.split(":", 1)[1]

        if not dimension or not preference:
            # 尝试从 content 解析 "dimension=value" 格式
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

    # ── 上下文自适应 ──

    def get_current_context(self) -> dict[str, Any]:
        """获取当前上下文信息"""
        try:
            from zoneinfo import ZoneInfo
            from ..config import settings
            tz = ZoneInfo(settings.scheduler_timezone)
            now = datetime.now(tz)
        except Exception:
            now = datetime.now()
        hour = now.hour

        # 时间段判断
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
            "weekday": now.weekday(),  # 0=周一
            "is_weekend": now.weekday() >= 5,
        }

    def _apply_context_adaptations(self, persona: MergedPersona) -> str:
        """根据上下文生成自适应说明"""
        ctx = self.get_current_context()
        adaptations = []

        if ctx["time_period"] == "night":
            adaptations.append("当前是深夜时段，语气应更温柔安静，回复简洁")
        elif ctx["time_period"] == "morning":
            adaptations.append("当前是早晨，语气可以活泼一些")

        if ctx["is_weekend"]:
            adaptations.append("今天是周末，可以更轻松随意")

        return "\n".join(f"- {a}" for a in adaptations) if adaptations else ""

    # ── 核心合并算法 ──

    def get_merged_persona(self) -> MergedPersona:
        """合并三层人格，输出最终人格描述"""
        # 1. 加载基础预设
        base = self.load_preset(self.active_preset_name)

        # 2. 叠加用户自定义层（覆盖同维度的基础值）
        customizations = []
        with self._traits_lock:
            traits_snapshot = list(self.user_traits)  # 快照，避免持锁时间过长
        for trait in traits_snapshot:
            if trait.confidence >= 0.5:
                if hasattr(base, trait.dimension):
                    old_val = getattr(base, trait.dimension)
                    # 特殊处理列表类型字段（如 care_topics）
                    if isinstance(old_val, list):
                        # 追加到列表而非覆盖
                        if trait.preference not in old_val:
                            old_val.append(trait.preference)
                        customizations.append(
                            f"- {trait.dimension}: +{trait.preference}"
                            f"（来源: {trait.source}，置信度: {trait.confidence:.2f}）"
                        )
                    else:
                        setattr(base, trait.dimension, trait.preference)
                        customizations.append(
                            f"- {trait.dimension}: {old_val} → {trait.preference}"
                            f"（来源: {trait.source}，置信度: {trait.confidence:.2f}）"
                        )
        base.user_customizations = "\n".join(customizations) if customizations else ""

        # 3. 加载 user_custom.md 的内容
        user_custom_file = self.personas_dir / "user_custom.md"
        if user_custom_file.exists():
            custom_content = user_custom_file.read_text(encoding="utf-8")
            # 跳过空白/占位内容
            if "尚未收集" not in custom_content and len(custom_content.strip()) > 100:
                if base.user_customizations:
                    base.user_customizations += "\n\n--- user_custom.md ---\n" + custom_content
                else:
                    base.user_customizations = custom_content

        # 4. 应用上下文自适应
        base.context_adaptations = self._apply_context_adaptations(base)

        return base

    # ── 用于 Prompt 注入 ──

    def get_persona_prompt_section(self) -> str:
        """生成用于注入 system prompt 的人格描述段"""
        merged = self.get_merged_persona()

        parts = []
        parts.append(f"## 当前人格: {merged.preset_name}")

        if merged.prompt_snippet:
            parts.append(f"\n### 角色设定\n{merged.prompt_snippet}")

        if merged.communication_style:
            parts.append(f"\n### 沟通风格\n{merged.communication_style}")

        if merged.user_customizations:
            parts.append(f"\n### 用户偏好叠加\n{merged.user_customizations}")

        if merged.context_adaptations:
            parts.append(f"\n### 当前上下文适配\n{merged.context_adaptations}")

        if merged.sticker_config:
            parts.append(f"\n### 表情包配置\n{merged.sticker_config}")

        return "\n".join(parts)

    def is_persona_active(self) -> bool:
        """是否激活了非默认人格"""
        return self.active_preset_name != "default" or len(self.user_traits) > 0

    def get_next_question_dimension(self, asked_dimensions: set[str]) -> str | None:
        """获取下一个待询问的偏好维度"""
        # 按优先级排序
        sorted_dims = sorted(
            PERSONA_DIMENSIONS.items(),
            key=lambda x: x[1]["priority"],
        )
        for dim_key, _dim_info in sorted_dims:
            if dim_key in asked_dimensions:
                continue
            # 检查是否已有高置信度的数据
            has_high_conf = any(
                t.dimension == dim_key and t.confidence >= 0.7
                for t in self.user_traits
            )
            if not has_high_conf:
                return dim_key
        return None

    def get_question_for_dimension(self, dimension: str) -> str | None:
        """获取指定维度的询问问题"""
        dim_info = PERSONA_DIMENSIONS.get(dimension)
        return dim_info["question"] if dim_info else None

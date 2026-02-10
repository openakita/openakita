"""
记忆类型定义

参考:
- Mem0: https://docs.mem0.ai/v0x/core-concepts/memory-types
- LangMem: https://langchain-ai.github.io/langmem/
- Memori: https://memorilabs.ai/docs/core-concepts/agents/
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MemoryType(Enum):
    """记忆类型 (参考 Memori)"""

    FACT = "fact"  # 事实信息 (用户偏好、技术栈)
    PREFERENCE = "preference"  # 用户偏好 (交互风格、代码风格)
    SKILL = "skill"  # 学到的技能 (成功模式、解决方案)
    CONTEXT = "context"  # 上下文信息 (项目背景、当前任务)
    RULE = "rule"  # 规则约束 (禁止行为、安全边界)
    ERROR = "error"  # 错误教训 (失败原因、避免重复)
    PERSONA_TRAIT = "persona_trait"  # 人格偏好特质 (沟通风格、情感距离等)


class MemoryPriority(Enum):
    """记忆优先级 (决定保留时长)"""

    TRANSIENT = "transient"  # 临时 (会话结束后丢弃)
    SHORT_TERM = "short_term"  # 短期 (保留几天)
    LONG_TERM = "long_term"  # 长期 (保留几周)
    PERMANENT = "permanent"  # 永久 (永不删除)


@dataclass
class Memory:
    """单条记忆"""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: MemoryType = MemoryType.FACT
    priority: MemoryPriority = MemoryPriority.SHORT_TERM
    content: str = ""
    source: str = ""  # 来源 (conversation/task/manual)
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    access_count: int = 0  # 访问次数 (用于相关性)
    importance_score: float = 0.5  # 重要性评分 (0-1)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "priority": self.priority.value,
            "content": self.content,
            "source": self.source,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "access_count": self.access_count,
            "importance_score": self.importance_score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Memory":
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            type=MemoryType(data.get("type", "fact")),
            priority=MemoryPriority(data.get("priority", "short_term")),
            content=data.get("content", ""),
            source=data.get("source", ""),
            tags=data.get("tags", []),
            created_at=datetime.fromisoformat(data["created_at"])
            if "created_at" in data
            else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"])
            if "updated_at" in data
            else datetime.now(),
            access_count=data.get("access_count", 0),
            importance_score=data.get("importance_score", 0.5),
        )

    def to_markdown(self) -> str:
        """转为 Markdown 格式"""
        tags_str = ", ".join(self.tags) if self.tags else ""
        return f"- [{self.type.value}] {self.content}" + (
            f" (tags: {tags_str})" if tags_str else ""
        )


@dataclass
class ConversationTurn:
    """对话轮次"""

    role: str  # user/assistant
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
        }


@dataclass
class SessionSummary:
    """会话摘要"""

    session_id: str
    start_time: datetime
    end_time: datetime
    task_description: str = ""
    outcome: str = ""  # success/partial/failed
    key_actions: list[str] = field(default_factory=list)
    learnings: list[str] = field(default_factory=list)
    errors_encountered: list[str] = field(default_factory=list)
    memories_created: list[str] = field(default_factory=list)  # Memory IDs

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "task_description": self.task_description,
            "outcome": self.outcome,
            "key_actions": self.key_actions,
            "learnings": self.learnings,
            "errors_encountered": self.errors_encountered,
            "memories_created": self.memories_created,
        }

    def to_markdown(self) -> str:
        """转为 Markdown 格式"""
        lines = [
            f"### Session: {self.session_id}",
            f"- 时间: {self.start_time.strftime('%Y-%m-%d %H:%M')} - {self.end_time.strftime('%H:%M')}",
            f"- 任务: {self.task_description}",
            f"- 结果: {self.outcome}",
        ]
        if self.key_actions:
            lines.append("- 关键操作:")
            for action in self.key_actions[:5]:
                lines.append(f"  - {action}")
        if self.learnings:
            lines.append("- 学习:")
            for learning in self.learnings[:3]:
                lines.append(f"  - {learning}")
        return "\n".join(lines)

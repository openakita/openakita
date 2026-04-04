"""
记忆类型定义

参考:
- Mem0: https://docs.mem0.ai/v0x/core-concepts/memory-types
- LangMem: https://langchain-ai.github.io/langmem/
- Memori: https://memorilabs.ai/docs/core-concepts/agents/

v2 新增:
- SemanticMemory: 实体-属性结构, 支持更新链
- Episode: 情节记忆, 保留完整交互故事
- ActionNode: 工具调用链节点
- Scratchpad: 跨 session 工作记忆草稿本
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


def normalize_tags(val: object) -> list[str]:
    """Ensure *val* is always ``list[str]``.

    LLMs sometimes return tags as a comma-separated string instead of an
    array.  This helper gracefully coerces any input into a safe list so
    that downstream ``.map()`` / iteration never crashes.
    """
    if isinstance(val, list):
        return [str(t) for t in val if t]
    if isinstance(val, str) and val:
        return [t.strip() for t in val.replace("\u3001", ",").split(",") if t.strip()]
    return []


_normalize_tags = normalize_tags


class MemoryType(Enum):
    """记忆类型"""

    FACT = "fact"
    PREFERENCE = "preference"
    SKILL = "skill"
    CONTEXT = "context"  # 保留向后兼容, 新系统中转为情节记忆
    RULE = "rule"
    ERROR = "error"
    PERSONA_TRAIT = "persona_trait"
    EXPERIENCE = "experience"  # 任务经验教训（可复用的流程/方法/踩坑总结）


class MemoryPriority(Enum):
    """记忆优先级 (决定保留时长)"""

    TRANSIENT = "transient"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    PERMANENT = "permanent"


class MemoryScope(str, Enum):
    """记忆作用域"""
    GLOBAL = "global"      # 全局共享（当前行为）
    AGENT = "agent"        # Agent 私有
    SESSION = "session"    # 会话私有


def _short_uuid() -> str:
    return str(uuid.uuid4())[:8]


# ---------------------------------------------------------------------------
# MEMORY.md 大小管理
# ---------------------------------------------------------------------------

MEMORY_MD_MAX_CHARS = 1500
"""MEMORY.md 统一大小上限（字符），写入端和读取端共用。"""

_RULE_SECTION_KEYWORDS = frozenset({"重要规则", "规则", "rules", "行为规则", "用户规则"})


def truncate_memory_md(content: str, max_chars: int = MEMORY_MD_MAX_CHARS) -> str:
    """按段落优先级截断 MEMORY.md 内容。

    策略：
    1. 按 ``## `` 拆分段落
    2. 将段落分为高优先级（规则类）和普通优先级
    3. 先填充高优先级段落（规则），再填充普通段落
    4. 超出预算时截断普通段落，规则段落尽量保留
    """
    content = content.strip()
    if not content or len(content) <= max_chars:
        return content

    sections = re.split(r"(?=^## )", content, flags=re.MULTILINE)

    high_priority: list[str] = []
    normal_priority: list[str] = []
    header = ""

    for section in sections:
        stripped = section.strip()
        if not stripped:
            continue
        if stripped.startswith("# ") and not stripped.startswith("## "):
            header = stripped
            continue
        title_match = re.match(r"^## (.+)", stripped)
        if title_match:
            title = title_match.group(1).strip().lower()
            if any(kw in title for kw in _RULE_SECTION_KEYWORDS):
                high_priority.append(stripped)
                continue
        normal_priority.append(stripped)

    result_parts: list[str] = []
    current_len = len(header) + 2 if header else 0
    if header:
        result_parts.append(header)

    for section in high_priority:
        if current_len + len(section) + 2 <= max_chars:
            result_parts.append(section)
            current_len += len(section) + 2
        else:
            remaining = max_chars - current_len - 20
            if remaining > 50:
                result_parts.append(section[:remaining] + "\n...(规则被截断)")
            break

    for section in normal_priority:
        if current_len + len(section) + 2 <= max_chars:
            result_parts.append(section)
            current_len += len(section) + 2

    return "\n\n".join(result_parts)


# ---------------------------------------------------------------------------
# SemanticMemory (v2, 取代旧 Memory)
# ---------------------------------------------------------------------------


@dataclass
class SemanticMemory:
    """语义记忆 — 实体-属性结构, 支持更新链"""

    id: str = field(default_factory=_short_uuid)
    type: MemoryType = MemoryType.FACT
    priority: MemoryPriority = MemoryPriority.SHORT_TERM
    content: str = ""
    source: str = ""

    # v2: 实体-属性结构
    subject: str = ""
    predicate: str = ""

    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    access_count: int = 0
    importance_score: float = 0.5

    # v2: 置信度与衰减
    confidence: float = 0.5
    decay_rate: float = 0.1
    last_accessed_at: datetime | None = None

    # v2: 更新链与溯源
    superseded_by: str | None = None
    source_episode_id: str | None = None

    # v3: 记忆分层
    scope: str = "global"        # MemoryScope value
    scope_owner: str = ""        # agent_profile_id or session_id

    # v4: 多 Agent 记忆隔离
    agent_id: str = ""

    # v2: retention / TTL
    expires_at: datetime | None = None

    def __post_init__(self):
        self.tags = normalize_tags(self.tags)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "type": self.type.value,
            "priority": self.priority.value,
            "content": self.content,
            "source": self.source,
            "subject": self.subject,
            "predicate": self.predicate,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "access_count": self.access_count,
            "importance_score": self.importance_score,
            "confidence": self.confidence,
            "decay_rate": self.decay_rate,
            "last_accessed_at": self.last_accessed_at.isoformat()
            if self.last_accessed_at
            else None,
            "superseded_by": self.superseded_by,
            "source_episode_id": self.source_episode_id,
            "scope": self.scope,
            "scope_owner": self.scope_owner,
            "agent_id": self.agent_id,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
        return d

    @classmethod
    def from_dict(cls, data: dict) -> SemanticMemory:
        last_accessed = data.get("last_accessed_at")
        return cls(
            id=data.get("id", _short_uuid()),
            type=MemoryType(data.get("type", "fact")),
            priority=MemoryPriority(data.get("priority", "short_term")),
            content=data.get("content", ""),
            source=data.get("source", ""),
            subject=data.get("subject", ""),
            predicate=data.get("predicate", ""),
            tags=data.get("tags", []),
            created_at=datetime.fromisoformat(data["created_at"])
            if "created_at" in data
            else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"])
            if "updated_at" in data
            else datetime.now(),
            access_count=data.get("access_count", 0),
            importance_score=data.get("importance_score", 0.5),
            confidence=data.get("confidence", 0.5),
            decay_rate=data.get("decay_rate", 0.1),
            last_accessed_at=datetime.fromisoformat(last_accessed)
            if last_accessed
            else None,
            superseded_by=data.get("superseded_by"),
            source_episode_id=data.get("source_episode_id"),
            scope=data.get("scope", "global"),
            scope_owner=data.get("scope_owner", ""),
            agent_id=data.get("agent_id", ""),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
        )

    def to_markdown(self) -> str:
        tags_str = ", ".join(self.tags) if self.tags else ""
        prefix = f"[{self.type.value}]"
        subj = f" {self.subject}:" if self.subject else ""
        return f"- {prefix}{subj} {self.content}" + (
            f" (tags: {tags_str})" if tags_str else ""
        )


# 向后兼容别名
Memory = SemanticMemory


# ---------------------------------------------------------------------------
# Episode (v2, 情节记忆)
# ---------------------------------------------------------------------------


@dataclass
class ActionNode:
    """情节中的单个动作节点 — 一次工具调用"""

    tool_name: str = ""
    key_params: dict = field(default_factory=dict)
    result_summary: str = ""
    success: bool = True
    error_message: str | None = None
    decision: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "key_params": self.key_params,
            "result_summary": self.result_summary,
            "success": self.success,
            "error_message": self.error_message,
            "decision": self.decision,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> ActionNode:
        return cls(
            tool_name=data.get("tool_name", ""),
            key_params=data.get("key_params", {}),
            result_summary=data.get("result_summary", ""),
            success=data.get("success", True),
            error_message=data.get("error_message"),
            decision=data.get("decision"),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if "timestamp" in data
            else datetime.now(),
        )


@dataclass
class Episode:
    """情节记忆 — 完整的交互故事"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""

    summary: str = ""
    goal: str = ""
    outcome: str = "completed"  # success / partial / failed / ongoing

    started_at: datetime = field(default_factory=datetime.now)
    ended_at: datetime = field(default_factory=datetime.now)

    action_nodes: list[ActionNode] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    linked_memory_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    importance_score: float = 0.5
    access_count: int = 0
    source: str = "session_end"  # session_end / context_compress / daily_consolidation

    def __post_init__(self):
        self.tags = normalize_tags(self.tags)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "summary": self.summary,
            "goal": self.goal,
            "outcome": self.outcome,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "action_nodes": [n.to_dict() for n in self.action_nodes],
            "entities": self.entities,
            "tools_used": self.tools_used,
            "linked_memory_ids": self.linked_memory_ids,
            "tags": self.tags,
            "importance_score": self.importance_score,
            "access_count": self.access_count,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Episode:
        nodes_raw = data.get("action_nodes", [])
        nodes = [ActionNode.from_dict(n) if isinstance(n, dict) else n for n in nodes_raw]
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            session_id=data.get("session_id", ""),
            summary=data.get("summary", ""),
            goal=data.get("goal", ""),
            outcome=data.get("outcome", "completed"),
            started_at=datetime.fromisoformat(data["started_at"])
            if "started_at" in data
            else datetime.now(),
            ended_at=datetime.fromisoformat(data["ended_at"])
            if "ended_at" in data
            else datetime.now(),
            action_nodes=nodes,
            entities=data.get("entities", []),
            tools_used=data.get("tools_used", []),
            linked_memory_ids=data.get("linked_memory_ids", []),
            tags=data.get("tags", []),
            importance_score=data.get("importance_score", 0.5),
            access_count=data.get("access_count", 0),
            source=data.get("source", "session_end"),
        )

    def to_markdown(self) -> str:
        lines = [
            f"### 历史操作记录: {self.goal or self.summary[:50]}",
            f"- 结果: {self.outcome}",
            f"- 时间: {self.started_at.strftime('%Y-%m-%d %H:%M')} - {self.ended_at.strftime('%H:%M')}",
        ]
        if self.summary:
            lines.append(f"- 摘要: {self.summary}")
        if self.tools_used:
            lines.append(f"- 使用工具: {', '.join(self.tools_used)}")
        if self.entities:
            lines.append(f"- 相关实体: {', '.join(self.entities[:5])}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scratchpad (v2, 工作记忆草稿本)
# ---------------------------------------------------------------------------


@dataclass
class Scratchpad:
    """跨 session 持久化的工作记忆草稿本"""

    user_id: str = "default"
    content: str = ""
    active_projects: list[str] = field(default_factory=list)
    current_focus: str = ""
    open_questions: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "content": self.content,
            "active_projects": self.active_projects,
            "current_focus": self.current_focus,
            "open_questions": self.open_questions,
            "next_steps": self.next_steps,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Scratchpad:
        return cls(
            user_id=data.get("user_id", "default"),
            content=data.get("content", ""),
            active_projects=data.get("active_projects", []),
            current_focus=data.get("current_focus", ""),
            open_questions=data.get("open_questions", []),
            next_steps=data.get("next_steps", []),
            updated_at=datetime.fromisoformat(data["updated_at"])
            if "updated_at" in data
            else datetime.now(),
        )

    def to_markdown(self) -> str:
        """Render scratchpad as markdown for system prompt injection."""
        lines: list[str] = []
        if self.current_focus:
            lines.append(f"## 当前任务\n{self.current_focus}")
        if self.active_projects:
            lines.append("## 近期完成\n" + "\n".join(f"- {p}" for p in self.active_projects[:5]))
        return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Attachment (v2, 文件/媒体记忆)
# ---------------------------------------------------------------------------


class AttachmentDirection(Enum):
    """附件方向"""
    INBOUND = "inbound"    # 用户发送给 agent
    OUTBOUND = "outbound"  # agent 生成/发送给用户


@dataclass
class Attachment:
    """文件/媒体附件记忆 — 追踪用户发送和 agent 生成的文件

    场景:
    - 用户发了一张猫的图片 → direction=inbound, description="一只橘猫..."
    - agent 生成了一份报告 → direction=outbound, description="用户要求的销售报告"
    - 用户发了一段语音 → direction=inbound, transcription="明天帮我..."
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    session_id: str = ""
    episode_id: str = ""

    filename: str = ""
    original_filename: str = ""
    mime_type: str = ""
    file_size: int = 0

    # 存储位置 (至少一个非空)
    local_path: str = ""       # 本地磁盘路径
    url: str = ""              # 远程 URL (IM 平台等)

    direction: AttachmentDirection = AttachmentDirection.INBOUND

    # 内容理解 — 由 LLM / OCR / STT 生成
    description: str = ""      # 图片/视频/文件的自然语言描述
    transcription: str = ""    # 语音/视频转写文本
    extracted_text: str = ""   # 从文档提取的文本摘要
    tags: list[str] = field(default_factory=list)

    # 关联
    linked_memory_ids: list[str] = field(default_factory=list)

    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        self.tags = normalize_tags(self.tags)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "episode_id": self.episode_id,
            "filename": self.filename,
            "original_filename": self.original_filename,
            "mime_type": self.mime_type,
            "file_size": self.file_size,
            "local_path": self.local_path,
            "url": self.url,
            "direction": self.direction.value,
            "description": self.description,
            "transcription": self.transcription,
            "extracted_text": self.extracted_text,
            "tags": self.tags,
            "linked_memory_ids": self.linked_memory_ids,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Attachment:
        direction_val = data.get("direction", "inbound")
        try:
            direction = AttachmentDirection(direction_val)
        except ValueError:
            direction = AttachmentDirection.INBOUND
        return cls(
            id=data.get("id", str(uuid.uuid4())[:12]),
            session_id=data.get("session_id", ""),
            episode_id=data.get("episode_id", ""),
            filename=data.get("filename", ""),
            original_filename=data.get("original_filename", ""),
            mime_type=data.get("mime_type", ""),
            file_size=data.get("file_size", 0),
            local_path=data.get("local_path", ""),
            url=data.get("url", ""),
            direction=direction,
            description=data.get("description", ""),
            transcription=data.get("transcription", ""),
            extracted_text=data.get("extracted_text", ""),
            tags=data.get("tags", []),
            linked_memory_ids=data.get("linked_memory_ids", []),
            created_at=datetime.fromisoformat(data["created_at"])
            if "created_at" in data
            else datetime.now(),
        )

    @property
    def searchable_text(self) -> str:
        """合并所有可搜索文本字段"""
        parts = [self.description, self.transcription, self.extracted_text,
                 self.filename, self.original_filename]
        parts.extend(self.tags)
        return " ".join(p for p in parts if p)

    @property
    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")

    @property
    def is_video(self) -> bool:
        return self.mime_type.startswith("video/")

    @property
    def is_audio(self) -> bool:
        return self.mime_type.startswith("audio/")

    @property
    def is_document(self) -> bool:
        return self.mime_type in (
            "application/pdf", "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain", "text/markdown",
        ) or self.mime_type.startswith("text/")


# ---------------------------------------------------------------------------
# 保留旧类型 (向后兼容)
# ---------------------------------------------------------------------------


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
    outcome: str = ""
    key_actions: list[str] = field(default_factory=list)
    learnings: list[str] = field(default_factory=list)
    errors_encountered: list[str] = field(default_factory=list)
    memories_created: list[str] = field(default_factory=list)

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

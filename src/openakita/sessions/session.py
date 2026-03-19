"""
会话对象定义

Session 代表一个独立的对话上下文，包含:
- 来源通道信息
- 对话历史
- 会话变量
- 配置覆盖
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class SessionState(Enum):
    """会话状态"""

    ACTIVE = "active"  # 活跃中
    IDLE = "idle"  # 空闲（无活动但未过期）
    EXPIRED = "expired"  # 已过期
    CLOSED = "closed"  # 已关闭


@dataclass
class SessionConfig:
    """
    会话配置

    可覆盖全局配置，实现会话级别的定制
    """

    max_history: int = 100  # 最大历史消息数
    timeout_minutes: int = 30  # 超时时间（分钟）
    language: str = "zh"  # 语言
    model: str | None = None  # 覆盖默认模型
    custom_prompt: str | None = None  # 自定义系统提示
    auto_summarize: bool = True  # 是否自动摘要长对话

    def merge_with_defaults(self, defaults: "SessionConfig") -> "SessionConfig":
        """合并配置，self 优先"""
        return SessionConfig(
            max_history=self.max_history or defaults.max_history,
            timeout_minutes=self.timeout_minutes or defaults.timeout_minutes,
            language=self.language or defaults.language,
            model=self.model or defaults.model,
            custom_prompt=self.custom_prompt or defaults.custom_prompt,
            auto_summarize=self.auto_summarize
            if self.auto_summarize is not None
            else defaults.auto_summarize,
        )


@dataclass
class SessionContext:
    """
    会话上下文

    存储会话级别的状态和数据
    """

    messages: list[dict] = field(default_factory=list)  # 对话历史
    variables: dict[str, Any] = field(default_factory=dict)  # 会话变量
    current_task: str | None = None  # 当前任务 ID
    memory_scope: str | None = None  # 记忆范围 ID
    summary: str | None = None  # 对话摘要（用于长对话压缩）
    topic_boundaries: list[int] = field(default_factory=list)  # 话题边界的消息索引
    current_topic_start: int = 0  # 当前话题起始消息索引
    agent_profile_id: str = "default"
    agent_switch_history: list[dict] = field(default_factory=list)
    handoff_events: list[dict] = field(default_factory=list)  # agent_handoff events for SSE
    # Active agents in this session (multi-agent collaboration)
    active_agents: list[str] = field(default_factory=list)
    # Delegation chain for the current request
    delegation_chain: list[dict] = field(default_factory=list)
    # Sub-agent work records — persisted traces of delegated tasks
    sub_agent_records: list[dict] = field(default_factory=list)
    _msg_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def add_message(self, role: str, content: str, **metadata) -> None:
        """添加消息"""
        with self._msg_lock:
            self.messages.append(
                {"role": role, "content": content, "timestamp": datetime.now().isoformat(), **metadata}
            )

    def mark_topic_boundary(self) -> None:
        """在当前消息位置标记话题边界。

        后续可用 get_current_topic_messages() 只获取当前话题的消息。
        """
        boundary_idx = len(self.messages)
        self.topic_boundaries.append(boundary_idx)
        self.current_topic_start = boundary_idx

    def get_current_topic_messages(self) -> list[dict]:
        """获取当前话题的消息（从最后一个边界开始）。"""
        if self.current_topic_start >= len(self.messages):
            return []
        return self.messages[self.current_topic_start:]

    def get_pre_topic_messages(self) -> list[dict]:
        """获取当前话题边界之前的消息。"""
        return self.messages[:self.current_topic_start]

    def get_messages(self, limit: int | None = None) -> list[dict]:
        """获取消息历史"""
        if limit:
            return self.messages[-limit:]
        return self.messages

    def set_variable(self, key: str, value: Any) -> None:
        """设置会话变量"""
        self.variables[key] = value

    def get_variable(self, key: str, default: Any = None) -> Any:
        """获取会话变量"""
        return self.variables.get(key, default)

    def clear_messages(self) -> None:
        """清空消息历史"""
        with self._msg_lock:
            self.messages = []
            self.topic_boundaries = []
            self.current_topic_start = 0
            self.variables["_context_reset_at"] = datetime.now().isoformat()

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "messages": self.messages,
            "variables": self.variables,
            "current_task": self.current_task,
            "memory_scope": self.memory_scope,
            "summary": self.summary,
            "topic_boundaries": self.topic_boundaries,
            "current_topic_start": self.current_topic_start,
            "agent_profile_id": self.agent_profile_id,
            "agent_switch_history": self.agent_switch_history,
            "handoff_events": self.handoff_events,
            "active_agents": self.active_agents,
            "delegation_chain": self.delegation_chain,
            "sub_agent_records": self.sub_agent_records,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionContext":
        """反序列化"""
        return cls(
            messages=data.get("messages", []),
            variables=data.get("variables", {}),
            current_task=data.get("current_task"),
            memory_scope=data.get("memory_scope"),
            summary=data.get("summary"),
            topic_boundaries=data.get("topic_boundaries", []),
            current_topic_start=data.get("current_topic_start", 0),
            agent_profile_id=data.get("agent_profile_id", "default"),
            agent_switch_history=data.get("agent_switch_history", []),
            handoff_events=data.get("handoff_events", []),
            active_agents=data.get("active_agents", []),
            delegation_chain=data.get("delegation_chain", []),
            sub_agent_records=data.get("sub_agent_records", []),
        )


@dataclass
class Session:
    """
    会话对象

    代表一个独立的对话上下文，关联:
    - 来源通道（telegram/feishu/...）
    - 聊天 ID（私聊/群聊/话题）
    - 用户 ID
    """

    id: str
    channel: str  # 来源通道
    chat_id: str  # 聊天 ID（群/私聊）
    user_id: str  # 用户 ID
    thread_id: str | None = None  # 话题/线程 ID（飞书话题等）
    chat_type: str = "private"  # "group" | "private"
    display_name: str = ""  # 用户昵称（用于 UI 展示）
    chat_name: str = ""  # 聊天/群组名称（群名、频道名等）

    # 状态
    state: SessionState = SessionState.ACTIVE
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)

    # 上下文
    context: SessionContext = field(default_factory=SessionContext)

    # 配置（可覆盖全局）
    config: SessionConfig = field(default_factory=SessionConfig)

    # 元数据
    metadata: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        channel: str,
        chat_id: str,
        user_id: str,
        thread_id: str | None = None,
        config: SessionConfig | None = None,
        chat_type: str = "private",
        display_name: str = "",
        chat_name: str = "",
    ) -> "Session":
        """创建新会话"""
        session_id = (
            f"{channel}_{chat_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
        )
        return cls(
            id=session_id,
            channel=channel,
            chat_id=chat_id,
            user_id=user_id,
            thread_id=thread_id,
            chat_type=chat_type,
            display_name=display_name,
            chat_name=chat_name,
            config=config or SessionConfig(),
        )

    def touch(self) -> None:
        """更新活跃时间"""
        self.last_active = datetime.now()
        if self.state == SessionState.IDLE:
            self.state = SessionState.ACTIVE

    def is_expired(self, timeout_minutes: int | None = None) -> bool:
        """仅在超长不活跃时标记过期（30 天冷归档）"""
        timeout = timeout_minutes or (60 * 24 * 30)  # 30 天
        elapsed = (datetime.now() - self.last_active).total_seconds() / 60
        return elapsed > timeout

    def mark_expired(self) -> None:
        """标记为过期"""
        self.state = SessionState.EXPIRED

    def mark_idle(self) -> None:
        """标记为空闲"""
        self.state = SessionState.IDLE

    def close(self) -> None:
        """关闭会话"""
        self.state = SessionState.CLOSED

    # ==================== 元数据管理 ====================

    def set_metadata(self, key: str, value: Any) -> None:
        """设置元数据"""
        self.metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """获取元数据"""
        return self.metadata.get(key, default)

    # ==================== 任务管理 ====================

    def set_task(self, task_id: str, description: str) -> None:
        """
        设置当前任务

        Args:
            task_id: 任务 ID
            description: 任务描述
        """
        self.context.current_task = task_id
        self.context.set_variable("task_description", description)
        self.context.set_variable("task_status", "in_progress")
        self.context.set_variable("task_started_at", datetime.now().isoformat())
        self.touch()
        logger.debug(f"Session {self.id}: set task {task_id}")

    def complete_task(self, success: bool = True, result: str = "") -> None:
        """
        完成当前任务

        Args:
            success: 是否成功
            result: 结果描述
        """
        self.context.set_variable("task_status", "completed" if success else "failed")
        self.context.set_variable("task_result", result)
        self.context.set_variable("task_completed_at", datetime.now().isoformat())

        task_id = self.context.current_task
        self.context.current_task = None

        self.touch()
        logger.debug(
            f"Session {self.id}: completed task {task_id} ({'success' if success else 'failed'})"
        )

    def get_task_status(self) -> dict:
        """
        获取当前任务状态

        Returns:
            任务状态字典
        """
        return {
            "task_id": self.context.current_task,
            "description": self.context.get_variable("task_description"),
            "status": self.context.get_variable("task_status"),
            "started_at": self.context.get_variable("task_started_at"),
            "completed_at": self.context.get_variable("task_completed_at"),
            "result": self.context.get_variable("task_result"),
        }

    def has_active_task(self) -> bool:
        """是否有正在进行的任务"""
        return self.context.current_task is not None

    @property
    def session_key(self) -> str:
        """会话唯一标识"""
        key = f"{self.channel}:{self.chat_id}:{self.user_id}"
        if self.thread_id:
            key += f":{self.thread_id}"
        return key

    def add_message(self, role: str, content: str, **metadata) -> None:
        """添加消息并更新活跃时间"""
        self.context.add_message(role, content, **metadata)
        self.touch()

        # 检查是否需要截断历史
        if len(self.context.messages) > self.config.max_history:
            self._truncate_history()

    _RULE_SIGNAL_WORDS = (
        "不要", "必须", "禁止", "每次", "规则", "永远不要", "务必",
        "永远", "always", "never", "must", "rule",
    )

    def _truncate_history(self) -> None:
        """截断历史消息，保留 75%，对丢弃部分生成简要摘要插入头部。

        优先保留用户设定的行为规则类消息。
        """
        with self.context._msg_lock:
            keep_count = int(self.config.max_history * 3 / 4)
            messages = self.context.messages
            dropped = messages[:-keep_count]
            kept = messages[-keep_count:]

            self._mark_dropped_for_extraction(dropped)

            max_summary_len = 300
            max_rules_len = 500
            keywords: list[str] = []
            rule_snippets: list[str] = []
            rules_len = 0

            for msg in dropped:
                if msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                if not isinstance(content, str) or not content:
                    continue

                from openakita.core.tool_executor import smart_truncate

                is_rule = any(w in content for w in self._RULE_SIGNAL_WORDS)
                if is_rule and rules_len < max_rules_len:
                    snippet, _ = smart_truncate(
                        content.replace("\n", " ").strip(), 300,
                        save_full=False, label="rule_hist",
                    )
                    rule_snippets.append(snippet)
                    rules_len += len(snippet)
                else:
                    preview, _ = smart_truncate(
                        content.replace("\n", " ").strip(), 150,
                        save_full=False, label="msg_hist",
                    )
                    keywords.append(preview)

            header_parts: list[str] = []
            if rule_snippets:
                header_parts.append("[用户规则（必须遵守）]\n" + "\n".join(rule_snippets))
            if keywords:
                header = "[历史背景，非当前任务]\n"
                body = ""
                for kw in keywords:
                    candidate = (body + "\n" + kw).strip() if body else kw
                    if len(header) + len(candidate) > max_summary_len:
                        break
                    body = candidate
                if body:
                    header_parts.append(header + body)

            if header_parts:
                kept.insert(0, {"role": "system", "content": "\n\n".join(header_parts)})

            self.context.messages = kept
            logger.debug(
                f"Session {self.id}: truncated history — "
                f"dropped {len(dropped)}, kept {len(kept)} messages, "
                f"preserved {len(rule_snippets)} rule snippets"
            )

    def _mark_dropped_for_extraction(self, dropped: list[dict]) -> None:
        """v2: 将被截断的消息标记为需要提取。

        通过 metadata["_memory_manager"] 或回调机制通知记忆系统。
        如果记忆系统不可用, 静默跳过 (不影响截断流程)。
        """
        memory_manager = self.metadata.get("_memory_manager")
        if memory_manager is None:
            return
        store = getattr(memory_manager, "store", None)
        if store is None:
            return
        try:
            for i, msg in enumerate(dropped):
                content = msg.get("content", "")
                if not content or not isinstance(content, str) or len(content) < 10:
                    continue
                store.enqueue_extraction(
                    session_id=self.id,
                    turn_index=i,
                    content=content,
                    tool_calls=msg.get("tool_calls"),
                    tool_results=msg.get("tool_results"),
                )
        except Exception as e:
            logger.warning(f"Failed to enqueue dropped messages for extraction: {e}")

    def to_dict(self) -> dict:
        """序列化"""
        # 过滤掉以 _ 开头的私有 metadata（如 _gateway, _session_key 等运行时数据）
        serializable_metadata = {
            k: v
            for k, v in self.metadata.items()
            if not k.startswith("_") and self._is_json_serializable(v)
        }

        return {
            "id": self.id,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "chat_type": self.chat_type,
            "display_name": self.display_name,
            "chat_name": self.chat_name,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
            "context": self.context.to_dict(),
            "config": {
                "max_history": self.config.max_history,
                "timeout_minutes": self.config.timeout_minutes,
                "language": self.config.language,
                "model": self.config.model,
                "custom_prompt": self.config.custom_prompt,
                "auto_summarize": self.config.auto_summarize,
            },
            "metadata": serializable_metadata,
        }

    def _is_json_serializable(self, value: Any) -> bool:
        """检查值是否可以 JSON 序列化"""
        import json

        try:
            json.dumps(value)
            return True
        except (TypeError, ValueError):
            return False

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        """反序列化"""
        config_data = data.get("config", {})
        return cls(
            id=data["id"],
            channel=data["channel"],
            chat_id=data["chat_id"],
            user_id=data["user_id"],
            thread_id=data.get("thread_id"),
            chat_type=data.get("chat_type", "private"),
            display_name=data.get("display_name", ""),
            chat_name=data.get("chat_name", ""),
            state=SessionState(data.get("state", "active")),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_active=datetime.fromisoformat(data["last_active"]),
            context=SessionContext.from_dict(data.get("context") or {}),
            config=SessionConfig(
                max_history=config_data.get("max_history", 100),
                timeout_minutes=config_data.get("timeout_minutes", 30),
                language=config_data.get("language", "zh"),
                model=config_data.get("model"),
                custom_prompt=config_data.get("custom_prompt"),
                auto_summarize=config_data.get("auto_summarize", True),
            ),
            metadata=data.get("metadata", {}),
        )

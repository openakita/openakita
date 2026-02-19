"""
会话对象定义

Session 代表一个独立的对话上下文，包含:
- 来源通道信息
- 对话历史
- 会话变量
- 配置覆盖
"""

import logging
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

    def add_message(self, role: str, content: str, **metadata) -> None:
        """添加消息"""
        self.messages.append(
            {"role": role, "content": content, "timestamp": datetime.now().isoformat(), **metadata}
        )

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
        self.messages = []

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "messages": self.messages,
            "variables": self.variables,
            "current_task": self.current_task,
            "memory_scope": self.memory_scope,
            "summary": self.summary,
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
        config: SessionConfig | None = None,
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
            config=config or SessionConfig(),
        )

    def touch(self) -> None:
        """更新活跃时间"""
        self.last_active = datetime.now()
        if self.state == SessionState.IDLE:
            self.state = SessionState.ACTIVE

    def is_expired(self, timeout_minutes: int | None = None) -> bool:
        """检查是否过期"""
        timeout = timeout_minutes or self.config.timeout_minutes
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
        return f"{self.channel}:{self.chat_id}:{self.user_id}"

    def add_message(self, role: str, content: str, **metadata) -> None:
        """添加消息并更新活跃时间"""
        self.context.add_message(role, content, **metadata)
        self.touch()

        # 检查是否需要截断历史
        if len(self.context.messages) > self.config.max_history:
            self._truncate_history()

    def _truncate_history(self) -> None:
        """截断历史消息，保留 75%，对丢弃部分生成简要摘要插入头部"""
        keep_count = int(self.config.max_history * 3 / 4)
        messages = self.context.messages
        dropped = messages[:-keep_count]
        kept = messages[-keep_count:]

        # v2: 标记被截断的消息需要记忆提取
        self._mark_dropped_for_extraction(dropped)

        summary_parts: list[str] = []
        for msg in dropped:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                preview = content[:80].replace("\n", " ")
                if len(content) > 80:
                    preview += "..."
                summary_parts.append(f"{role}: {preview}")
        if summary_parts:
            summary_text = (
                "[早期对话摘要（已截断的消息概要）]\n"
                + "\n".join(summary_parts[-20:])
            )
            if kept and kept[0].get("role") == "user":
                kept.insert(0, {"role": "assistant", "content": "好的，我已了解之前的对话概要。"})
            kept.insert(0, {"role": "user", "content": summary_text})

        self.context.messages = kept
        logger.debug(
            f"Session {self.id}: truncated history — "
            f"dropped {len(dropped)}, kept {len(kept)} messages"
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
            state=SessionState(data.get("state", "active")),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_active=datetime.fromisoformat(data["last_active"]),
            context=SessionContext.from_dict(data.get("context", {})),
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

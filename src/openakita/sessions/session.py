"""
会话对象定义

Session 代表一个独立的对话上下文，包含:
- 来源通道信息
- 对话历史
- 会话变量
- 配置覆盖
"""

import logging
import re
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

    max_history: int = 2000  # 硬安全上限（日常由 _trim_old_metadata 控制体积，此值仅为极端兜底）
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
class TaskCheckpoint:
    """任务检查点 — 借鉴 claude-code 的"任务连续性"思路。

    每条代表推理流式循环里的一次节点状态，用于：
    1) 前端"任务时间线"展示（已完成什么、下一步打算）；
    2) 流式中断 / 预算暂停后从特定 messages_offset 续跑。

    实际持久化形式为 dict（嵌入 SessionContext.task_checkpoints），
    本 dataclass 只是构造与字段校验的帮手，与 handoff_events 等同风格。
    """

    checkpoint_id: str
    task_id: str
    conversation_id: str
    iteration: int
    created_at: float
    summary: str = ""
    next_step_hint: str = ""
    exit_reason: str = "running"
    artifacts: list[str] = field(default_factory=list)
    messages_offset: int = 0

    # 合法 exit_reason 取值（仅供调用方对照，未做严格枚举校验以保持向前兼容）
    EXIT_REASONS = (
        "running",
        "iteration_complete",
        "budget_paused",
        "user_cancelled",
        "network_error",
        "completed",
        "failed",
    )

    def to_dict(self) -> dict:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "conversation_id": self.conversation_id,
            "iteration": self.iteration,
            "created_at": self.created_at,
            "summary": self.summary,
            "next_step_hint": self.next_step_hint,
            "exit_reason": self.exit_reason,
            "artifacts": list(self.artifacts),
            "messages_offset": self.messages_offset,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskCheckpoint":
        return cls(
            checkpoint_id=str(data.get("checkpoint_id", "")),
            task_id=str(data.get("task_id", "")),
            conversation_id=str(data.get("conversation_id", "")),
            iteration=int(data.get("iteration", 0) or 0),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            summary=str(data.get("summary", "")),
            next_step_hint=str(data.get("next_step_hint", "")),
            exit_reason=str(data.get("exit_reason", "running")),
            artifacts=list(data.get("artifacts") or []),
            messages_offset=int(data.get("messages_offset", 0) or 0),
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
    working_facts: dict[str, Any] = field(default_factory=dict)
    handoff_events: list[dict] = field(default_factory=list)  # agent_handoff events for SSE
    # Active agents in this session (multi-agent collaboration)
    active_agents: list[str] = field(default_factory=list)
    # Delegation chain for the current request
    delegation_chain: list[dict] = field(default_factory=list)
    # Sub-agent work records — persisted traces of delegated tasks
    sub_agent_records: list[dict] = field(default_factory=list)
    # Task checkpoints — emitted by reasoning_engine.reason_stream for resume / timeline
    # 上限由 append_task_checkpoint 控制，避免长会话无限增长。
    task_checkpoints: list[dict] = field(default_factory=list)
    focus_terms: list[str] = field(default_factory=list)
    focus_updated_at: str | None = None
    precompact_snapshot: dict[str, Any] = field(default_factory=dict)
    _msg_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    _DEDUP_TIME_WINDOW_SECONDS = 30

    def add_message(self, role: str, content: str, **metadata) -> bool:
        """添加消息（含去重：连续相同 + 时间窗口内相同）。

        Returns:
            True if the message was actually added, False if deduped.
        """
        with self._msg_lock:
            if self.messages:
                last = self.messages[-1]
                if last.get("role") == role and last.get("content") == content:
                    return False

            now = datetime.now()
            for msg in reversed(self.messages[-8:]):
                if msg.get("role") != role:
                    continue
                msg_content = msg.get("content", "") or ""
                if msg_content != content:
                    continue
                ts_str = msg.get("timestamp", "")
                if ts_str:
                    try:
                        msg_time = datetime.fromisoformat(ts_str)
                        if (now - msg_time).total_seconds() < self._DEDUP_TIME_WINDOW_SECONDS:
                            return False
                    except (ValueError, TypeError):
                        pass

            self.messages.append(
                {
                    "role": role,
                    "content": content,
                    "timestamp": now.isoformat(),
                    **metadata,
                }
            )
            if role == "user" and content:
                self.update_focus_terms(content)
            return True

    _FOCUS_FILE_RE = re.compile(
        r"(?:[A-Za-z]:[\\/][^\s\"'<>|]+|[\w./\\-]+\.(?:py|ts|tsx|js|jsx|md|json|yaml|yml|toml|rs|go))"
    )
    _FOCUS_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,12}")
    _FOCUS_STOP_WORDS = {
        "帮我",
        "这个",
        "那个",
        "一下",
        "继续",
        "看看",
        "请问",
        "如何",
        "怎么",
        "需要",
        "实现",
        "修改",
        "the",
        "and",
        "for",
        "with",
    }

    def update_focus_terms(self, content: str, *, max_terms: int = 12) -> None:
        """Update lightweight session focus terms; never writes long-term memory."""
        text = content.strip()
        if not text:
            return
        if len(self.messages) - self.current_topic_start <= 1:
            self.focus_terms = []

        candidates: list[str] = []
        candidates.extend(m.group(0).strip(".,，。;；") for m in self._FOCUS_FILE_RE.finditer(text))
        for match in self._FOCUS_WORD_RE.finditer(text):
            term = match.group(0).strip()
            if len(term) < 2 or term.lower() in self._FOCUS_STOP_WORDS:
                continue
            if (
                any(ch.isupper() for ch in term)
                or "_" in term
                or "-" in term
                or "/" in term
                or any(
                    keyword in term
                    for keyword in ("任务", "记忆", "权限", "审计", "会话", "路径", "压缩", "队列")
                )
            ):
                candidates.append(term)

        merged: list[str] = []
        for term in [*candidates, *self.focus_terms]:
            if term and term not in merged:
                merged.append(term)
        self.focus_terms = merged[:max_terms]
        if candidates:
            self.focus_updated_at = datetime.now().isoformat()

    def mark_topic_boundary(self) -> None:
        """在当前消息位置标记话题边界。

        后续可用 get_current_topic_messages() 只获取当前话题的消息。
        """
        boundary_idx = len(self.messages)
        self.topic_boundaries.append(boundary_idx)
        self.current_topic_start = boundary_idx
        self.focus_terms = []
        self.focus_updated_at = datetime.now().isoformat()

    def get_current_topic_messages(self) -> list[dict]:
        """获取当前话题的消息（从最后一个边界开始）。"""
        if self.current_topic_start >= len(self.messages):
            return []
        return self.messages[self.current_topic_start :]

    def get_pre_topic_messages(self) -> list[dict]:
        """获取当前话题边界之前的消息。"""
        return self.messages[: self.current_topic_start]

    def get_messages(self, limit: int | None = None) -> list[dict]:
        """获取消息历史"""
        if limit is not None:
            try:
                return self.messages[-int(limit) :]
            except (ValueError, TypeError):
                pass
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

    def append_task_checkpoint(
        self,
        checkpoint: "TaskCheckpoint | dict",
        *,
        max_keep: int = 50,
    ) -> dict:
        """追加任务检查点，超出 max_keep 时仅保留最近若干条。

        Args:
            checkpoint: TaskCheckpoint 实例或等价 dict。
            max_keep: 单会话内保留的检查点上限，默认 50。

        Returns:
            落到 task_checkpoints 中的 dict（也用于 SSE emit）。
        """
        if isinstance(checkpoint, TaskCheckpoint):
            data = checkpoint.to_dict()
        elif isinstance(checkpoint, dict):
            data = TaskCheckpoint.from_dict(checkpoint).to_dict()
        else:
            raise TypeError(
                f"checkpoint must be TaskCheckpoint or dict, got {type(checkpoint).__name__}"
            )

        with self._msg_lock:
            self.task_checkpoints.append(data)
            if len(self.task_checkpoints) > max_keep:
                self.task_checkpoints = self.task_checkpoints[-max_keep:]
        return data

    def latest_task_checkpoint(self, task_id: str | None = None) -> dict | None:
        """返回最近一条检查点；若给出 task_id，则限定该任务。"""
        with self._msg_lock:
            for ckpt in reversed(self.task_checkpoints):
                if task_id is None or ckpt.get("task_id") == task_id:
                    return ckpt
        return None

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
            "working_facts": self.working_facts,
            "handoff_events": self.handoff_events,
            "active_agents": self.active_agents,
            "delegation_chain": self.delegation_chain,
            "sub_agent_records": self.sub_agent_records,
            "task_checkpoints": self.task_checkpoints,
            "focus_terms": self.focus_terms,
            "focus_updated_at": self.focus_updated_at,
            "precompact_snapshot": self.precompact_snapshot,
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
            working_facts=data.get("working_facts", {}),
            handoff_events=data.get("handoff_events", []),
            active_agents=data.get("active_agents", []),
            delegation_chain=data.get("delegation_chain", []),
            sub_agent_records=data.get("sub_agent_records", []),
            task_checkpoints=data.get("task_checkpoints", []),
            focus_terms=data.get("focus_terms", []),
            focus_updated_at=data.get("focus_updated_at"),
            precompact_snapshot=data.get("precompact_snapshot", {}),
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
    bot_instance_id: str = ""  # 机器人实例 ID（为空时兼容旧数据，回退 channel）
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

    # PolicyV2 正交两层 mode（C8 §2.2 新增）
    # ``session_role``: SessionRole 枚举字符串 ("agent" / "plan" / "ask" / "coordinator")
    # ``confirmation_mode_override``: 若非 None，覆盖全局 ConfirmationMode（"default"
    # / "trust" / "strict" / "accept_edits" / "dont_ask"）。switch_mode 工具写入
    # ``session_role``；UI/handler 后续可对个别 session 单独覆盖 confirmation_mode。
    # 用 ``str`` 而非 ``Enum`` 是为了让旧 sessions.json（无字段）反序列化时可
    # ``getattr(session, "session_role", "agent")`` 兼容；adapter.build_policy_context
    # 把字符串 coerce 回 SessionRole 枚举。
    session_role: str = "agent"
    confirmation_mode_override: str | None = None

    # C12 §14.2: PolicyV2 unattended fields. Promoted from `metadata` to
    # first-class fields so scheduler / spawn_agent / webhook callers can set
    # them at session creation without metadata fishing. PolicyContext.from_session
    # reads first-class fields when present (with metadata fallback for
    # back-compat with sessions persisted before C12).
    #
    # ``is_unattended``: True for sessions where no human is interactively
    # responding (cron task / webhook / autonomous spawn). PolicyEngineV2
    # step 11 routes through ``_handle_unattended`` only when this is True.
    #
    # ``unattended_strategy``: empty → engine uses
    # ``config.unattended.default_strategy`` ("ask_owner" by default).
    # Explicit values: "deny" / "auto_approve" / "defer_to_owner" /
    # "defer_to_inbox" / "ask_owner".  Per-session override of config default.
    is_unattended: bool = False
    unattended_strategy: str = ""

    @classmethod
    def create(
        cls,
        channel: str,
        chat_id: str,
        user_id: str,
        bot_instance_id: str = "",
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
            bot_instance_id=bot_instance_id or channel,
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
        namespace = self.bot_instance_id or self.channel
        key = f"{namespace}:{self.chat_id}:{self.user_id}"
        if self.thread_id:
            key += f":{self.thread_id}"
        return key

    # 重型元数据键（思考链、工具摘要、代码产物），对旧消息裁剪以控制体积
    _HEAVY_METADATA_KEYS = ("chain_summary", "tool_summary", "artifacts")
    # 保留最近 N 条消息的完整元数据（前端展示思考链等），更早的仅保留 base content
    _METADATA_PRESERVE_WINDOW = 50

    def add_message(self, role: str, content: str, **metadata) -> bool:
        """添加消息并更新活跃时间。返回 True 表示消息被添加，False 表示被去重跳过。"""
        added = self.context.add_message(role, content, **metadata)
        self.touch()

        if added:
            self._trim_old_metadata()
            if len(self.context.messages) > self.config.max_history:
                self._truncate_history()
            # PR-D3: best-effort 同步写 SqliteTurnStore，避免崩溃丢历史。
            # 仅持久化主对话角色（user/assistant），且 transient_for_llm 的
            # 临时消息（如 RiskGate 确认应答）不写盘。
            try:
                if (
                    role in ("user", "assistant", "tool")
                    and not metadata.get("transient_for_llm")
                ):
                    self._write_turn_to_store(role, content, metadata)
            except Exception as exc:
                logger.debug(f"[Session] write_turn_to_store skipped: {exc}")
        return added

    def _write_turn_to_store(self, role: str, content: str, metadata: dict) -> None:
        """Persist a single turn to SQLite via session_manager._turn_writer."""
        try:
            from ..core.feature_flags import is_enabled as _ff_enabled

            if not _ff_enabled("history_db_merge_v1"):
                return
        except Exception:
            return

        manager = getattr(self, "_manager", None)
        writer = getattr(manager, "_turn_writer", None) if manager else None
        if writer is None:
            return

        try:
            import re as _re

            safe_id = (self.session_key or "").replace(":", "__")
            safe_id = _re.sub(r'[/\\+=%?*<>|"\x00-\x1f]', "_", safe_id)
            turn_index = max(0, len(self.context.messages) - 1)
            writer(
                safe_id,
                turn_index,
                role,
                content,
                metadata,
            )
        except Exception as exc:
            logger.debug(f"[Session] turn writer failed: {exc}")

    def _trim_old_metadata(self) -> None:
        """裁剪旧消息的重型元数据以控制内存与序列化体积。

        保留所有消息的 base content（role, content, timestamp），仅移除
        chain_summary / tool_summary / artifacts 等重型字段。
        这是日常的体积控制机制，不会删除任何消息——用户永远不会丢失聊天记录。
        """
        with self.context._msg_lock:
            messages = self.context.messages
            trim_end = len(messages) - self._METADATA_PRESERVE_WINDOW
            if trim_end <= 0:
                return
            for msg in messages[:trim_end]:
                for key in self._HEAVY_METADATA_KEYS:
                    msg.pop(key, None)

    _RULE_SIGNAL_WORDS = (
        "不要",
        "必须",
        "禁止",
        "每次",
        "规则",
        "永远不要",
        "务必",
        "永远",
        "always",
        "never",
        "must",
        "rule",
    )

    def _truncate_history(self) -> None:
        """硬安全网：仅当消息数超过 max_history（默认 2000）时触发。

        日常体积控制由 _trim_old_metadata 负责（不删消息），本方法仅在极端情况
        下删除最老 5% 的消息。正常使用几乎不会触发。
        """
        with self.context._msg_lock:
            keep_count = int(self.config.max_history * 95 / 100)
            messages = self.context.messages
            dropped = messages[:-keep_count]
            kept = messages[-keep_count:]

            logger.warning(
                f"Session {self.id}: HARD CAP truncation — "
                f"total {len(messages)}, dropping {len(dropped)}, "
                f"keeping {len(kept)} (max_history={self.config.max_history})"
            )

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
                        content.replace("\n", " ").strip(),
                        300,
                        save_full=False,
                        label="rule_hist",
                    )
                    rule_snippets.append(snippet)
                    rules_len += len(snippet)
                else:
                    preview, _ = smart_truncate(
                        content.replace("\n", " ").strip(),
                        150,
                        save_full=False,
                        label="msg_hist",
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
            logger.info(
                f"Session {self.id}: truncated — "
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
            "bot_instance_id": self.bot_instance_id or self.channel,
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
            "session_role": self.session_role,
            "confirmation_mode_override": self.confirmation_mode_override,
            "is_unattended": self.is_unattended,
            "unattended_strategy": self.unattended_strategy,
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
        # C8: session_role / confirmation_mode_override 旧 sessions.json 没有，
        # 走默认 "agent" / None。读取时容错任意非法值（None/类型错） → 默认。
        sr_raw = data.get("session_role", "agent")
        session_role = sr_raw if isinstance(sr_raw, str) and sr_raw else "agent"
        cm_raw = data.get("confirmation_mode_override")
        confirmation_mode_override = cm_raw if isinstance(cm_raw, str) and cm_raw else None
        # C12 §14.2: is_unattended / unattended_strategy. 旧 sessions.json 没有
        # → 默认 False / "" (= use config.unattended.default_strategy)
        is_unattended_raw = data.get("is_unattended", False)
        is_unattended = bool(is_unattended_raw) if is_unattended_raw is not None else False
        us_raw = data.get("unattended_strategy", "")
        unattended_strategy = us_raw if isinstance(us_raw, str) else ""
        return cls(
            id=data["id"],
            channel=data["channel"],
            bot_instance_id=data.get("bot_instance_id") or data.get("channel", ""),
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
                max_history=max(config_data.get("max_history", 2000), 500),
                timeout_minutes=config_data.get("timeout_minutes", 30),
                language=config_data.get("language", "zh"),
                model=config_data.get("model"),
                custom_prompt=config_data.get("custom_prompt"),
                auto_summarize=config_data.get("auto_summarize", True),
            ),
            metadata=data.get("metadata", {}),
            session_role=session_role,
            confirmation_mode_override=confirmation_mode_override,
            is_unattended=is_unattended,
            unattended_strategy=unattended_strategy,
        )

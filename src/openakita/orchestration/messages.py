"""
多 Agent 通信消息协议

定义 Agent 之间通信的消息格式和数据结构。
所有消息通过 ZMQ 传输，使用 JSON 序列化。
"""

import uuid
import json
import logging
from enum import Enum
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Any, Dict, List

logger = logging.getLogger(__name__)


class AgentStatus(Enum):
    """Agent 状态"""
    STARTING = "starting"      # 启动中
    IDLE = "idle"              # 空闲，等待任务
    BUSY = "busy"              # 正在执行任务
    STOPPING = "stopping"      # 正在停止
    DEAD = "dead"              # 已死亡（心跳超时）
    ERROR = "error"            # 错误状态


class AgentType(Enum):
    """Agent 类型"""
    MASTER = "master"          # 主协调器
    WORKER = "worker"          # 通用工作 Agent
    SPECIALIZED = "specialized"  # 专门化 Agent


class MessageType(Enum):
    """消息类型"""
    COMMAND = "command"        # 命令消息（需要响应）
    RESPONSE = "response"      # 响应消息
    EVENT = "event"            # 事件广播
    HEARTBEAT = "heartbeat"    # 心跳消息


class CommandType(Enum):
    """命令类型"""
    # Agent 生命周期
    REGISTER = "register"           # 注册 Agent
    UNREGISTER = "unregister"       # 注销 Agent
    SHUTDOWN = "shutdown"           # 请求关闭
    
    # 任务相关
    ASSIGN_TASK = "assign_task"     # 分配任务
    CANCEL_TASK = "cancel_task"     # 取消任务
    TASK_RESULT = "task_result"     # 任务结果
    
    # 状态查询
    GET_STATUS = "get_status"       # 获取状态
    LIST_AGENTS = "list_agents"     # 列出所有 Agent
    
    # 通信
    CHAT_REQUEST = "chat_request"   # 对话请求
    CHAT_RESPONSE = "chat_response" # 对话响应


class EventType(Enum):
    """事件类型"""
    AGENT_REGISTERED = "agent_registered"
    AGENT_UNREGISTERED = "agent_unregistered"
    AGENT_STATUS_CHANGED = "agent_status_changed"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    SYSTEM_ALERT = "system_alert"


@dataclass
class AgentInfo:
    """
    Agent 信息
    
    用于注册和状态查询
    """
    agent_id: str                           # 唯一标识
    agent_type: str                         # AgentType 值
    process_id: int                         # 进程 PID
    status: str = AgentStatus.STARTING.value  # AgentStatus 值
    capabilities: List[str] = field(default_factory=list)  # 能力列表
    current_task: Optional[str] = None      # 当前任务 ID
    current_task_desc: Optional[str] = None # 当前任务描述
    session_id: Optional[str] = None        # 关联的会话 ID
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_heartbeat: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 统计信息
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_runtime_seconds: float = 0.0
    
    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "AgentInfo":
        """从字典反序列化"""
        return cls(**data)
    
    def update_heartbeat(self) -> None:
        """更新心跳时间"""
        self.last_heartbeat = datetime.now().isoformat()
    
    def set_status(self, status: AgentStatus) -> None:
        """设置状态"""
        self.status = status.value
    
    def set_task(self, task_id: str, task_desc: str = "") -> None:
        """设置当前任务"""
        self.current_task = task_id
        self.current_task_desc = task_desc
        self.status = AgentStatus.BUSY.value
    
    def clear_task(self, success: bool = True) -> None:
        """清除当前任务"""
        self.current_task = None
        self.current_task_desc = None
        self.status = AgentStatus.IDLE.value
        if success:
            self.tasks_completed += 1
        else:
            self.tasks_failed += 1


@dataclass
class AgentMessage:
    """
    Agent 间通信的消息格式
    
    所有 Agent 之间的通信都使用此格式
    """
    msg_id: str                             # 消息唯一 ID
    msg_type: str                           # MessageType 值
    sender_id: str                          # 发送者 Agent ID
    target_id: str                          # 目标 Agent ID（"*" 表示广播）
    payload: Dict[str, Any]                 # 消息负载
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 可选字段
    command_type: Optional[str] = None      # CommandType 值（当 msg_type 是 command 时）
    event_type: Optional[str] = None        # EventType 值（当 msg_type 是 event 时）
    correlation_id: Optional[str] = None    # 关联 ID（用于请求-响应配对）
    ttl: int = 60                           # 消息有效期（秒）
    
    @classmethod
    def create(
        cls,
        msg_type: MessageType,
        sender_id: str,
        target_id: str,
        payload: Dict[str, Any],
        command_type: Optional[CommandType] = None,
        event_type: Optional[EventType] = None,
        correlation_id: Optional[str] = None,
    ) -> "AgentMessage":
        """创建消息"""
        return cls(
            msg_id=str(uuid.uuid4()),
            msg_type=msg_type.value,
            sender_id=sender_id,
            target_id=target_id,
            payload=payload,
            command_type=command_type.value if command_type else None,
            event_type=event_type.value if event_type else None,
            correlation_id=correlation_id,
        )
    
    @classmethod
    def command(
        cls,
        sender_id: str,
        target_id: str,
        command_type: CommandType,
        payload: Dict[str, Any],
    ) -> "AgentMessage":
        """创建命令消息"""
        return cls.create(
            msg_type=MessageType.COMMAND,
            sender_id=sender_id,
            target_id=target_id,
            payload=payload,
            command_type=command_type,
        )
    
    @classmethod
    def response(
        cls,
        sender_id: str,
        target_id: str,
        correlation_id: str,
        payload: Dict[str, Any],
    ) -> "AgentMessage":
        """创建响应消息"""
        return cls.create(
            msg_type=MessageType.RESPONSE,
            sender_id=sender_id,
            target_id=target_id,
            payload=payload,
            correlation_id=correlation_id,
        )
    
    @classmethod
    def event(
        cls,
        sender_id: str,
        event_type: EventType,
        payload: Dict[str, Any],
    ) -> "AgentMessage":
        """创建事件广播消息"""
        return cls.create(
            msg_type=MessageType.EVENT,
            sender_id=sender_id,
            target_id="*",  # 广播
            payload=payload,
            event_type=event_type,
        )
    
    @classmethod
    def heartbeat(cls, sender_id: str, agent_info: AgentInfo) -> "AgentMessage":
        """创建心跳消息"""
        return cls.create(
            msg_type=MessageType.HEARTBEAT,
            sender_id=sender_id,
            target_id="master",
            payload=agent_info.to_dict(),
        )
    
    def to_json(self) -> str:
        """序列化为 JSON"""
        return json.dumps(asdict(self), ensure_ascii=False)
    
    def to_bytes(self) -> bytes:
        """序列化为字节（用于 ZMQ 传输）"""
        return self.to_json().encode('utf-8')
    
    @classmethod
    def from_json(cls, json_str: str) -> "AgentMessage":
        """从 JSON 反序列化"""
        data = json.loads(json_str)
        return cls(**data)
    
    @classmethod
    def from_bytes(cls, data: bytes) -> "AgentMessage":
        """从字节反序列化"""
        return cls.from_json(data.decode('utf-8'))
    
    def is_expired(self) -> bool:
        """检查消息是否过期"""
        msg_time = datetime.fromisoformat(self.timestamp)
        elapsed = (datetime.now() - msg_time).total_seconds()
        return elapsed > self.ttl


@dataclass
class TaskPayload:
    """
    任务负载
    
    用于 ASSIGN_TASK 命令
    """
    task_id: str
    task_type: str                          # "chat" | "execute" | "scheduled"
    description: str
    content: str                            # 任务内容（如用户消息）
    session_id: Optional[str] = None        # 会话 ID（用于 IM 通道）
    context: Dict[str, Any] = field(default_factory=dict)  # 上下文
    priority: int = 0                       # 优先级（数字越小越高）
    timeout_seconds: int = 300              # 超时时间
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "TaskPayload":
        return cls(**data)


@dataclass
class TaskResult:
    """
    任务结果
    
    用于 TASK_RESULT 命令
    """
    task_id: str
    success: bool
    result: Optional[str] = None            # 成功时的结果
    error: Optional[str] = None             # 失败时的错误信息
    duration_seconds: float = 0.0
    iterations: int = 0                     # 执行迭代次数
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "TaskResult":
        return cls(**data)


# ==================== 便捷函数 ====================

def create_register_command(agent_info: AgentInfo) -> AgentMessage:
    """创建注册命令"""
    return AgentMessage.command(
        sender_id=agent_info.agent_id,
        target_id="master",
        command_type=CommandType.REGISTER,
        payload=agent_info.to_dict(),
    )


def create_unregister_command(agent_id: str) -> AgentMessage:
    """创建注销命令"""
    return AgentMessage.command(
        sender_id=agent_id,
        target_id="master",
        command_type=CommandType.UNREGISTER,
        payload={"agent_id": agent_id},
    )


def create_chat_request(
    sender_id: str,
    target_id: str,
    session_id: str,
    message: str,
    context: Dict[str, Any] = None,
) -> AgentMessage:
    """创建对话请求"""
    return AgentMessage.command(
        sender_id=sender_id,
        target_id=target_id,
        command_type=CommandType.CHAT_REQUEST,
        payload={
            "session_id": session_id,
            "message": message,
            "context": context or {},
        },
    )


def create_chat_response(
    sender_id: str,
    target_id: str,
    correlation_id: str,
    response: str,
    success: bool = True,
    error: Optional[str] = None,
) -> AgentMessage:
    """创建对话响应"""
    return AgentMessage.response(
        sender_id=sender_id,
        target_id=target_id,
        correlation_id=correlation_id,
        payload={
            "response": response,
            "success": success,
            "error": error,
        },
    )

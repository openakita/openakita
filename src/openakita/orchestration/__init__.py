"""
多 Agent 协同工作框架

本模块实现基于 ZeroMQ 的多进程 Agent 协同机制:
- AgentRegistry: Agent 注册中心，管理所有活跃 Agent
- AgentBus: ZMQ 通信总线，处理进程间通信
- MasterAgent: 主协调器，任务分发和监督
- WorkerAgent: 工作进程，执行具体任务

架构:
    ┌─────────────────────────────────────────┐
    │              主进程                       │
    │  ┌─────────┐  ┌─────────┐  ┌─────────┐  │
    │  │   CLI   │  │ Gateway │  │Scheduler│  │
    │  └────┬────┘  └────┬────┘  └────┬────┘  │
    │       │            │            │        │
    │       └────────────┼────────────┘        │
    │                    ▼                     │
    │            ┌──────────────┐              │
    │            │ MasterAgent  │              │
    │            │  (协调器)    │              │
    │            └──────┬───────┘              │
    │                   │                      │
    │            ┌──────┴───────┐              │
    │            │  AgentBus    │              │
    │            │   (ZMQ)      │              │
    │            └──────┬───────┘              │
    │                   │                      │
    │            ┌──────┴───────┐              │
    │            │AgentRegistry │              │
    │            └──────────────┘              │
    └─────────────────────────────────────────┘
                        │
           ┌────────────┼────────────┐
           ▼            ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ Worker 1 │ │ Worker 2 │ │ Worker N │
    │  (进程)  │ │  (进程)  │ │  (进程)  │
    └──────────┘ └──────────┘ └──────────┘
"""

from .messages import (
    AgentMessage,
    MessageType,
    CommandType,
    AgentStatus,
    AgentInfo,
)
from .registry import AgentRegistry
from .bus import AgentBus, BusConfig
from .master import MasterAgent
from .worker import WorkerAgent
from .monitor import AgentMonitor

__all__ = [
    # 消息协议
    "AgentMessage",
    "MessageType",
    "CommandType",
    "AgentStatus",
    "AgentInfo",
    # 核心组件
    "AgentRegistry",
    "AgentBus",
    "BusConfig",
    "MasterAgent",
    "WorkerAgent",
    "AgentMonitor",
]

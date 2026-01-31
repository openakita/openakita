"""
Agent 注册中心

管理所有活跃 Agent 的注册信息，提供:
- Agent 注册/注销
- 状态查询和监控
- 空闲 Agent 查找
- 健康检查（心跳超时检测）
"""

import json
import asyncio
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Callable, Any

from .messages import AgentInfo, AgentStatus, AgentType

logger = logging.getLogger(__name__)


class AgentRegistry:
    """
    Agent 注册中心
    
    线程安全的 Agent 注册表，支持:
    - Agent 注册和注销
    - 按状态/能力查询
    - 心跳超时检测
    - 状态变更回调
    
    设计说明:
    - 运行在主进程中
    - 使用线程锁保证并发安全
    - 支持持久化到文件（可选）
    """
    
    # 默认心跳超时（秒）
    DEFAULT_HEARTBEAT_TIMEOUT = 15
    
    def __init__(
        self,
        heartbeat_timeout: int = DEFAULT_HEARTBEAT_TIMEOUT,
        storage_path: Optional[Path] = None,
        on_status_change: Optional[Callable[[str, AgentStatus, AgentStatus], None]] = None,
    ):
        """
        Args:
            heartbeat_timeout: 心跳超时时间（秒）
            storage_path: 持久化存储路径（可选）
            on_status_change: 状态变更回调 (agent_id, old_status, new_status)
        """
        self.heartbeat_timeout = heartbeat_timeout
        self.storage_path = storage_path
        self.on_status_change = on_status_change
        
        # Agent 注册表 {agent_id: AgentInfo}
        self._agents: Dict[str, AgentInfo] = {}
        
        # 线程锁
        self._lock = threading.RLock()
        
        # 事件记录（最近 100 条）
        self._events: List[Dict[str, Any]] = []
        self._max_events = 100
        
        # 加载持久化数据
        if self.storage_path:
            self._load()
    
    # ==================== 注册/注销 ====================
    
    def register(self, agent_info: AgentInfo) -> bool:
        """
        注册 Agent
        
        Args:
            agent_info: Agent 信息
        
        Returns:
            是否注册成功
        """
        with self._lock:
            agent_id = agent_info.agent_id
            
            # 检查是否已存在
            if agent_id in self._agents:
                existing = self._agents[agent_id]
                # 如果是同一进程重新注册（可能是重启），更新信息
                if existing.process_id != agent_info.process_id:
                    logger.warning(
                        f"Agent {agent_id} already registered with different PID "
                        f"({existing.process_id} vs {agent_info.process_id})"
                    )
                    # 标记旧的为 DEAD
                    self._set_status(agent_id, AgentStatus.DEAD)
            
            # 注册
            agent_info.status = AgentStatus.IDLE.value
            agent_info.update_heartbeat()
            self._agents[agent_id] = agent_info
            
            self._record_event("register", {
                "agent_id": agent_id,
                "type": agent_info.agent_type,
                "capabilities": agent_info.capabilities,
            })
            
            logger.info(f"Agent registered: {agent_id} (type={agent_info.agent_type})")
            
            # 持久化
            self._save()
            
            return True
    
    def unregister(self, agent_id: str) -> bool:
        """
        注销 Agent
        
        Args:
            agent_id: Agent ID
        
        Returns:
            是否注销成功
        """
        with self._lock:
            if agent_id not in self._agents:
                logger.warning(f"Agent not found: {agent_id}")
                return False
            
            agent_info = self._agents[agent_id]
            del self._agents[agent_id]
            
            self._record_event("unregister", {
                "agent_id": agent_id,
                "type": agent_info.agent_type,
            })
            
            logger.info(f"Agent unregistered: {agent_id}")
            
            # 持久化
            self._save()
            
            return True
    
    # ==================== 心跳 ====================
    
    def heartbeat(self, agent_id: str, agent_info: Optional[AgentInfo] = None) -> bool:
        """
        更新心跳
        
        Args:
            agent_id: Agent ID
            agent_info: 可选的更新信息
        
        Returns:
            是否更新成功
        """
        with self._lock:
            if agent_id not in self._agents:
                logger.warning(f"Heartbeat from unknown agent: {agent_id}")
                return False
            
            existing = self._agents[agent_id]
            existing.update_heartbeat()
            
            # 如果提供了新信息，更新部分字段
            if agent_info:
                existing.status = agent_info.status
                existing.current_task = agent_info.current_task
                existing.current_task_desc = agent_info.current_task_desc
                existing.tasks_completed = agent_info.tasks_completed
                existing.tasks_failed = agent_info.tasks_failed
            
            # 如果之前是 DEAD 状态，恢复为 IDLE
            if existing.status == AgentStatus.DEAD.value:
                self._set_status(agent_id, AgentStatus.IDLE)
            
            return True
    
    def check_heartbeats(self) -> List[str]:
        """
        检查所有 Agent 的心跳，标记超时的为 DEAD
        
        Returns:
            标记为 DEAD 的 Agent ID 列表
        """
        dead_agents = []
        now = datetime.now()
        
        with self._lock:
            for agent_id, agent_info in self._agents.items():
                # 跳过已经是 DEAD 或 STOPPING 状态的
                if agent_info.status in (AgentStatus.DEAD.value, AgentStatus.STOPPING.value):
                    continue
                
                # 检查心跳超时
                last_hb = datetime.fromisoformat(agent_info.last_heartbeat)
                elapsed = (now - last_hb).total_seconds()
                
                if elapsed > self.heartbeat_timeout:
                    logger.warning(
                        f"Agent {agent_id} heartbeat timeout "
                        f"({elapsed:.1f}s > {self.heartbeat_timeout}s)"
                    )
                    self._set_status(agent_id, AgentStatus.DEAD)
                    dead_agents.append(agent_id)
        
        return dead_agents
    
    # ==================== 查询 ====================
    
    def get(self, agent_id: str) -> Optional[AgentInfo]:
        """获取 Agent 信息"""
        with self._lock:
            return self._agents.get(agent_id)
    
    def list_all(self) -> List[AgentInfo]:
        """列出所有 Agent"""
        with self._lock:
            return list(self._agents.values())
    
    def list_by_status(self, status: AgentStatus) -> List[AgentInfo]:
        """按状态列出 Agent"""
        with self._lock:
            return [
                a for a in self._agents.values()
                if a.status == status.value
            ]
    
    def list_by_type(self, agent_type: AgentType) -> List[AgentInfo]:
        """按类型列出 Agent"""
        with self._lock:
            return [
                a for a in self._agents.values()
                if a.agent_type == agent_type.value
            ]
    
    def find_idle_agent(
        self,
        capabilities: Optional[List[str]] = None,
        exclude_ids: Optional[List[str]] = None,
    ) -> Optional[AgentInfo]:
        """
        查找空闲的 Agent
        
        Args:
            capabilities: 需要的能力列表（可选）
            exclude_ids: 排除的 Agent ID 列表
        
        Returns:
            找到的 Agent 或 None
        """
        exclude_ids = exclude_ids or []
        
        with self._lock:
            candidates = []
            
            for agent_id, agent_info in self._agents.items():
                # 跳过排除的
                if agent_id in exclude_ids:
                    continue
                
                # 必须是 IDLE 状态
                if agent_info.status != AgentStatus.IDLE.value:
                    continue
                
                # 必须是 Worker 类型
                if agent_info.agent_type not in (AgentType.WORKER.value, AgentType.SPECIALIZED.value):
                    continue
                
                # 检查能力匹配
                if capabilities:
                    if not all(cap in agent_info.capabilities for cap in capabilities):
                        continue
                
                candidates.append(agent_info)
            
            if not candidates:
                return None
            
            # 返回任务完成数最少的（负载均衡）
            return min(candidates, key=lambda a: a.tasks_completed)
    
    def count(self) -> int:
        """返回 Agent 数量"""
        with self._lock:
            return len(self._agents)
    
    def count_by_status(self) -> Dict[str, int]:
        """按状态统计数量"""
        with self._lock:
            counts = {}
            for agent_info in self._agents.values():
                status = agent_info.status
                counts[status] = counts.get(status, 0) + 1
            return counts
    
    # ==================== 状态管理 ====================
    
    def set_agent_status(self, agent_id: str, status: AgentStatus) -> bool:
        """设置 Agent 状态"""
        with self._lock:
            return self._set_status(agent_id, status)
    
    def set_agent_task(
        self,
        agent_id: str,
        task_id: str,
        task_desc: str = "",
    ) -> bool:
        """设置 Agent 当前任务"""
        with self._lock:
            if agent_id not in self._agents:
                return False
            
            agent_info = self._agents[agent_id]
            old_status = agent_info.status
            agent_info.set_task(task_id, task_desc)
            
            if old_status != agent_info.status:
                self._trigger_status_change(agent_id, old_status, agent_info.status)
            
            self._record_event("task_assigned", {
                "agent_id": agent_id,
                "task_id": task_id,
                "task_desc": task_desc[:100],
            })
            
            return True
    
    def clear_agent_task(self, agent_id: str, success: bool = True) -> bool:
        """清除 Agent 当前任务"""
        with self._lock:
            if agent_id not in self._agents:
                return False
            
            agent_info = self._agents[agent_id]
            task_id = agent_info.current_task
            old_status = agent_info.status
            agent_info.clear_task(success)
            
            if old_status != agent_info.status:
                self._trigger_status_change(agent_id, old_status, agent_info.status)
            
            self._record_event("task_completed" if success else "task_failed", {
                "agent_id": agent_id,
                "task_id": task_id,
                "success": success,
            })
            
            return True
    
    def _set_status(self, agent_id: str, status: AgentStatus) -> bool:
        """内部方法：设置状态"""
        if agent_id not in self._agents:
            return False
        
        agent_info = self._agents[agent_id]
        old_status = agent_info.status
        agent_info.set_status(status)
        
        if old_status != status.value:
            self._trigger_status_change(agent_id, old_status, status.value)
            self._record_event("status_changed", {
                "agent_id": agent_id,
                "old_status": old_status,
                "new_status": status.value,
            })
        
        return True
    
    def _trigger_status_change(
        self,
        agent_id: str,
        old_status: str,
        new_status: str,
    ) -> None:
        """触发状态变更回调"""
        if self.on_status_change:
            try:
                self.on_status_change(
                    agent_id,
                    AgentStatus(old_status),
                    AgentStatus(new_status),
                )
            except Exception as e:
                logger.error(f"Status change callback error: {e}")
    
    # ==================== Dashboard 数据 ====================
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """
        获取仪表盘数据
        
        返回适合前端展示的数据结构
        """
        with self._lock:
            status_counts = self.count_by_status()
            
            agents_data = []
            for agent_info in self._agents.values():
                # 计算运行时间
                created = datetime.fromisoformat(agent_info.created_at)
                uptime = datetime.now() - created
                uptime_str = self._format_duration(uptime.total_seconds())
                
                # 计算心跳延迟
                last_hb = datetime.fromisoformat(agent_info.last_heartbeat)
                hb_ago = datetime.now() - last_hb
                hb_ago_str = f"{hb_ago.total_seconds():.0f}s ago"
                
                agents_data.append({
                    "agent_id": agent_info.agent_id,
                    "type": agent_info.agent_type,
                    "status": agent_info.status,
                    "capabilities": agent_info.capabilities,
                    "current_task": agent_info.current_task_desc or agent_info.current_task,
                    "uptime": uptime_str,
                    "tasks_completed": agent_info.tasks_completed,
                    "tasks_failed": agent_info.tasks_failed,
                    "last_heartbeat": hb_ago_str,
                    "process_id": agent_info.process_id,
                })
            
            return {
                "summary": {
                    "total_agents": len(self._agents),
                    "idle": status_counts.get(AgentStatus.IDLE.value, 0),
                    "busy": status_counts.get(AgentStatus.BUSY.value, 0),
                    "dead": status_counts.get(AgentStatus.DEAD.value, 0),
                    "stopping": status_counts.get(AgentStatus.STOPPING.value, 0),
                },
                "agents": agents_data,
                "recent_events": self._events[-20:],  # 最近 20 条事件
            }
    
    def _format_duration(self, seconds: float) -> str:
        """格式化时长"""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            return f"{seconds / 60:.0f}m"
        elif seconds < 86400:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h {mins}m"
        else:
            days = int(seconds // 86400)
            hours = int((seconds % 86400) // 3600)
            return f"{days}d {hours}h"
    
    # ==================== 事件记录 ====================
    
    def _record_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """记录事件"""
        event = {
            "time": datetime.now().isoformat(),
            "event": event_type,
            **data,
        }
        self._events.append(event)
        
        # 保持最大数量
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
    
    def get_recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取最近的事件"""
        with self._lock:
            return self._events[-limit:]
    
    # ==================== 持久化 ====================
    
    def _load(self) -> None:
        """从文件加载"""
        if not self.storage_path or not self.storage_path.exists():
            return
        
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            for item in data.get("agents", []):
                try:
                    agent_info = AgentInfo.from_dict(item)
                    # 加载时标记为 DEAD（需要重新注册）
                    agent_info.status = AgentStatus.DEAD.value
                    self._agents[agent_info.agent_id] = agent_info
                except Exception as e:
                    logger.warning(f"Failed to load agent: {e}")
            
            logger.info(f"Loaded {len(self._agents)} agents from storage (marked as DEAD)")
            
        except Exception as e:
            logger.error(f"Failed to load registry: {e}")
    
    def _save(self) -> None:
        """保存到文件"""
        if not self.storage_path:
            return
        
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            
            data = {
                "agents": [a.to_dict() for a in self._agents.values()],
                "updated_at": datetime.now().isoformat(),
            }
            
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
        except Exception as e:
            logger.error(f"Failed to save registry: {e}")
    
    # ==================== 清理 ====================
    
    def cleanup_dead_agents(self, max_age_hours: int = 24) -> int:
        """
        清理长时间处于 DEAD 状态的 Agent
        
        Args:
            max_age_hours: 最大保留时间（小时）
        
        Returns:
            清理的数量
        """
        cleaned = 0
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        
        with self._lock:
            to_remove = []
            
            for agent_id, agent_info in self._agents.items():
                if agent_info.status != AgentStatus.DEAD.value:
                    continue
                
                last_hb = datetime.fromisoformat(agent_info.last_heartbeat)
                if last_hb < cutoff:
                    to_remove.append(agent_id)
            
            for agent_id in to_remove:
                del self._agents[agent_id]
                cleaned += 1
                logger.info(f"Cleaned up dead agent: {agent_id}")
            
            if cleaned > 0:
                self._save()
        
        return cleaned

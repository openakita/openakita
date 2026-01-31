"""
Agent 协同监控模块

提供:
- 实时状态监控
- 性能指标收集
- 告警机制
- Dashboard 数据接口
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Callable
from dataclasses import dataclass, field

from .registry import AgentRegistry
from .messages import AgentStatus, AgentType

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """性能指标"""
    timestamp: datetime = field(default_factory=datetime.now)
    
    # 任务指标
    tasks_per_minute: float = 0.0
    avg_task_duration_seconds: float = 0.0
    task_success_rate: float = 0.0
    
    # Agent 指标
    total_agents: int = 0
    idle_agents: int = 0
    busy_agents: int = 0
    dead_agents: int = 0
    
    # 系统指标
    pending_tasks: int = 0
    queue_depth: int = 0


@dataclass
class Alert:
    """告警"""
    id: str
    level: str  # "info" | "warning" | "error" | "critical"
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    acknowledged: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class AgentMonitor:
    """
    Agent 监控器
    
    收集和分析 Agent 系统的运行状态
    """
    
    # 告警阈值
    ALERT_DEAD_AGENT_THRESHOLD = 1        # 死亡 Agent 数量
    ALERT_BUSY_RATIO_THRESHOLD = 0.9      # 繁忙比例
    ALERT_TASK_FAILURE_RATE = 0.3         # 任务失败率
    ALERT_AVG_DURATION_THRESHOLD = 60     # 平均任务时长（秒）
    
    def __init__(
        self,
        registry: AgentRegistry,
        on_alert: Optional[Callable[[Alert], None]] = None,
        metrics_history_size: int = 100,
    ):
        """
        Args:
            registry: Agent 注册中心
            on_alert: 告警回调
            metrics_history_size: 指标历史保留数量
        """
        self.registry = registry
        self.on_alert = on_alert
        self.metrics_history_size = metrics_history_size
        
        # 指标历史
        self._metrics_history: List[PerformanceMetrics] = []
        
        # 告警列表
        self._alerts: List[Alert] = []
        self._alert_counter = 0
        
        # 任务统计（用于计算速率）
        self._task_completions: List[datetime] = []
        self._task_durations: List[float] = []
        self._task_results: List[bool] = []
        
        # 运行状态
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
    
    async def start(self, interval_seconds: int = 30) -> None:
        """启动监控"""
        self._running = True
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(interval_seconds)
        )
        logger.info("Agent monitor started")
    
    async def stop(self) -> None:
        """停止监控"""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Agent monitor stopped")
    
    async def _monitor_loop(self, interval: int) -> None:
        """监控循环"""
        while self._running:
            try:
                await asyncio.sleep(interval)
                
                # 收集指标
                metrics = self.collect_metrics()
                self._metrics_history.append(metrics)
                
                # 保持历史大小
                if len(self._metrics_history) > self.metrics_history_size:
                    self._metrics_history = self._metrics_history[-self.metrics_history_size:]
                
                # 检查告警
                self._check_alerts(metrics)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
    
    def collect_metrics(self) -> PerformanceMetrics:
        """收集当前指标"""
        agents = self.registry.list_all()
        status_counts = self.registry.count_by_status()
        
        # 计算任务速率（每分钟）
        now = datetime.now()
        recent_completions = [
            t for t in self._task_completions
            if (now - t).total_seconds() < 60
        ]
        tasks_per_minute = len(recent_completions)
        
        # 计算平均任务时长
        recent_durations = self._task_durations[-50:]  # 最近 50 个任务
        avg_duration = (
            sum(recent_durations) / len(recent_durations)
            if recent_durations else 0.0
        )
        
        # 计算成功率
        recent_results = self._task_results[-50:]
        success_rate = (
            sum(1 for r in recent_results if r) / len(recent_results)
            if recent_results else 1.0
        )
        
        return PerformanceMetrics(
            tasks_per_minute=tasks_per_minute,
            avg_task_duration_seconds=avg_duration,
            task_success_rate=success_rate,
            total_agents=len(agents),
            idle_agents=status_counts.get(AgentStatus.IDLE.value, 0),
            busy_agents=status_counts.get(AgentStatus.BUSY.value, 0),
            dead_agents=status_counts.get(AgentStatus.DEAD.value, 0),
        )
    
    def record_task_completion(
        self,
        duration_seconds: float,
        success: bool,
    ) -> None:
        """记录任务完成"""
        self._task_completions.append(datetime.now())
        self._task_durations.append(duration_seconds)
        self._task_results.append(success)
        
        # 保持列表大小
        max_records = 1000
        if len(self._task_completions) > max_records:
            self._task_completions = self._task_completions[-max_records:]
        if len(self._task_durations) > max_records:
            self._task_durations = self._task_durations[-max_records:]
        if len(self._task_results) > max_records:
            self._task_results = self._task_results[-max_records:]
    
    def _check_alerts(self, metrics: PerformanceMetrics) -> None:
        """检查并生成告警"""
        # 检查死亡 Agent
        if metrics.dead_agents >= self.ALERT_DEAD_AGENT_THRESHOLD:
            self._create_alert(
                level="warning",
                message=f"检测到 {metrics.dead_agents} 个 Agent 故障",
                metadata={"dead_count": metrics.dead_agents},
            )
        
        # 检查繁忙比例
        if metrics.total_agents > 0:
            busy_ratio = metrics.busy_agents / metrics.total_agents
            if busy_ratio >= self.ALERT_BUSY_RATIO_THRESHOLD:
                self._create_alert(
                    level="warning",
                    message=f"Agent 繁忙比例过高: {busy_ratio:.1%}",
                    metadata={"busy_ratio": busy_ratio},
                )
        
        # 检查任务失败率
        if metrics.task_success_rate < (1 - self.ALERT_TASK_FAILURE_RATE):
            self._create_alert(
                level="error",
                message=f"任务失败率过高: {1 - metrics.task_success_rate:.1%}",
                metadata={"failure_rate": 1 - metrics.task_success_rate},
            )
        
        # 检查平均任务时长
        if metrics.avg_task_duration_seconds > self.ALERT_AVG_DURATION_THRESHOLD:
            self._create_alert(
                level="info",
                message=f"平均任务时长较长: {metrics.avg_task_duration_seconds:.1f}s",
                metadata={"avg_duration": metrics.avg_task_duration_seconds},
            )
    
    def _create_alert(
        self,
        level: str,
        message: str,
        metadata: Dict[str, Any] = None,
    ) -> None:
        """创建告警"""
        # 检查是否有相同的未确认告警（5 分钟内）
        cutoff = datetime.now() - timedelta(minutes=5)
        for alert in self._alerts:
            if (
                alert.message == message
                and not alert.acknowledged
                and alert.timestamp > cutoff
            ):
                return  # 跳过重复告警
        
        self._alert_counter += 1
        alert = Alert(
            id=f"alert-{self._alert_counter}",
            level=level,
            message=message,
            metadata=metadata or {},
        )
        
        self._alerts.append(alert)
        logger.warning(f"Alert [{level}]: {message}")
        
        # 触发回调
        if self.on_alert:
            try:
                self.on_alert(alert)
            except Exception as e:
                logger.error(f"Alert callback error: {e}")
        
        # 保持告警列表大小
        if len(self._alerts) > 100:
            self._alerts = self._alerts[-100:]
    
    def acknowledge_alert(self, alert_id: str) -> bool:
        """确认告警"""
        for alert in self._alerts:
            if alert.id == alert_id:
                alert.acknowledged = True
                return True
        return False
    
    def get_alerts(
        self,
        level: Optional[str] = None,
        unacknowledged_only: bool = False,
    ) -> List[Alert]:
        """获取告警列表"""
        alerts = self._alerts
        
        if level:
            alerts = [a for a in alerts if a.level == level]
        
        if unacknowledged_only:
            alerts = [a for a in alerts if not a.acknowledged]
        
        return alerts
    
    def get_metrics_history(self, limit: int = 50) -> List[PerformanceMetrics]:
        """获取指标历史"""
        return self._metrics_history[-limit:]
    
    def get_current_metrics(self) -> PerformanceMetrics:
        """获取当前指标"""
        return self.collect_metrics()
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """获取仪表盘数据"""
        metrics = self.collect_metrics()
        
        return {
            "metrics": {
                "tasks_per_minute": metrics.tasks_per_minute,
                "avg_task_duration": f"{metrics.avg_task_duration_seconds:.1f}s",
                "success_rate": f"{metrics.task_success_rate:.1%}",
            },
            "agents": {
                "total": metrics.total_agents,
                "idle": metrics.idle_agents,
                "busy": metrics.busy_agents,
                "dead": metrics.dead_agents,
            },
            "alerts": {
                "total": len(self._alerts),
                "unacknowledged": len([a for a in self._alerts if not a.acknowledged]),
                "recent": [
                    {
                        "id": a.id,
                        "level": a.level,
                        "message": a.message,
                        "time": a.timestamp.isoformat(),
                    }
                    for a in self._alerts[-5:]
                ],
            },
            "timestamp": datetime.now().isoformat(),
        }

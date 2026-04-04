"""
定时任务处理器

处理定时任务相关的系统技能：
- schedule_task: 创建定时任务
- list_scheduled_tasks: 列出任务
- cancel_scheduled_task: 取消任务
- update_scheduled_task: 更新任务
- trigger_scheduled_task: 立即触发
- query_task_executions: 查询执行历史
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class ScheduledHandler:
    """定时任务处理器"""

    TOOLS = [
        "schedule_task",
        "list_scheduled_tasks",
        "cancel_scheduled_task",
        "update_scheduled_task",
        "trigger_scheduled_task",
        "query_task_executions",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    def _get_scheduler(self):
        """获取调度器：优先用 agent 自身的，fallback 到全局单例（多 Agent 模式）"""
        scheduler = getattr(self.agent, "task_scheduler", None)
        if scheduler:
            return scheduler
        from ...scheduler import get_active_scheduler
        return get_active_scheduler()

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        scheduler = self._get_scheduler()
        if not scheduler:
            return "❌ 定时任务调度器未启动"
        self.agent.task_scheduler = scheduler

        if tool_name == "schedule_task":
            return await self._schedule_task(params)
        elif tool_name == "list_scheduled_tasks":
            return self._list_tasks(params)
        elif tool_name == "cancel_scheduled_task":
            return await self._cancel_task(params)
        elif tool_name == "update_scheduled_task":
            return await self._update_task(params)
        elif tool_name == "trigger_scheduled_task":
            return await self._trigger_task(params)
        elif tool_name == "query_task_executions":
            return self._query_executions(params)
        else:
            return f"❌ Unknown scheduled tool: {tool_name}"

    async def _schedule_task(self, params: dict) -> str:
        """创建定时任务"""
        from ...core.im_context import get_im_session
        from ...scheduler import ScheduledTask, TriggerType
        from ...scheduler.task import TaskSource, TaskType

        # 必填字段校验
        for field in ("name", "description", "trigger_type", "trigger_config"):
            if field not in params or not params[field]:
                return f"❌ 缺少必填参数: {field}"

        try:
            trigger_type = TriggerType(params["trigger_type"])
        except ValueError:
            return f"❌ 不支持的触发类型: {params['trigger_type']}（支持: once, interval, cron）"

        try:
            task_type = TaskType(params.get("task_type", "reminder"))
        except ValueError:
            return f"❌ 不支持的任务类型: {params.get('task_type')}（支持: reminder, task）"

        trigger_config = params.get("trigger_config", {})
        if not isinstance(trigger_config, dict):
            return "❌ trigger_config 必须是一个对象"

        # ==================== run_at 合理性校验 ====================
        if trigger_type == TriggerType.ONCE:
            try:
                now = datetime.now()
                run_at_raw = (params.get("trigger_config") or {}).get("run_at")
                if isinstance(run_at_raw, str):
                    parsed = datetime.fromisoformat(run_at_raw.strip())
                    delta = parsed - now

                    if delta.total_seconds() < -300:
                        return (
                            f"❌ run_at 时间 {parsed.strftime('%Y-%m-%d %H:%M')} 已经过去了。"
                            f"当前时间是 {now.strftime('%Y-%m-%d %H:%M')}。\n"
                            "请根据当前时间重新计算正确的日期和时间。"
                        )

                    if delta.days > 365:
                        return (
                            f"⚠️ run_at 时间 {parsed.strftime('%Y-%m-%d %H:%M')} 距现在超过 1 年，"
                            "可能是日期计算有误。请向用户确认具体日期后重试。"
                        )
            except ValueError:
                pass

        # 获取当前 IM 会话信息
        channel_id = chat_id = user_id = None
        session = get_im_session()
        if session:
            channel_id = session.channel
            chat_id = session.chat_id
            user_id = session.user_id

        # 如果用户指定了 target_channel，尝试解析到已配置的通道
        target_channel = params.get("target_channel")
        if target_channel:
            resolved = self._resolve_target_channel(target_channel)
            if resolved:
                channel_id, chat_id = resolved
                logger.info(f"Using target_channel={target_channel}: {channel_id}/{chat_id}")
            else:
                # 通道未配置或无可用 session，给出明确提示
                return (
                    f"❌ 指定的通道 '{target_channel}' 未配置或暂无可用会话。\n"
                    f"已配置的通道: {self._list_available_channels()}\n"
                    f"请确认通道名称正确，且该通道至少有过一次聊天记录。"
                )

        task = ScheduledTask.create(
            name=params["name"],
            description=params["description"],
            trigger_type=trigger_type,
            trigger_config=params["trigger_config"],
            task_type=task_type,
            reminder_message=params.get("reminder_message"),
            prompt=params.get("prompt", ""),
            user_id=user_id,
            channel_id=channel_id,
            chat_id=chat_id,
            task_source=TaskSource.CHAT,
        )
        task.metadata["notify_on_start"] = params.get("notify_on_start", True)
        task.metadata["notify_on_complete"] = params.get("notify_on_complete", True)

        try:
            task_id = await self.agent.task_scheduler.add_task(task)
        except ValueError as e:
            return f"❌ {e}"

        next_run = task.next_run.strftime("%Y-%m-%d %H:%M:%S") if task.next_run else "待计算"

        type_display = "📝 简单提醒" if task_type == TaskType.REMINDER else "🔧 复杂任务"

        logger.info(
            "定时任务已创建: ID=%s, 名称=%s, 类型=%s, 触发=%s, 下次执行=%s%s",
            task_id, task.name, type_display, task.trigger_type.value, next_run,
            f", 通知渠道={channel_id}/{chat_id}" if channel_id and chat_id else "",
        )

        logger.info(
            f"Created scheduled task: {task_id} ({task.name}), type={task_type.value}, next run: {next_run}"
        )

        return (
            f"✅ 已创建{type_display}\n- ID: {task_id}\n- 名称: {task.name}\n- 下次执行: {next_run}"
        )

    def _list_tasks(self, params: dict) -> str:
        """列出任务"""
        enabled_only = params.get("enabled_only", False)
        tasks = self.agent.task_scheduler.list_tasks(enabled_only=enabled_only)

        if not tasks:
            return "当前没有定时任务"

        output = f"共 {len(tasks)} 个定时任务:\n\n"
        for t in tasks:
            status = "✓" if t.enabled else "✗"
            next_run = t.next_run.strftime("%m-%d %H:%M") if t.next_run else "N/A"
            channel_info = f"{t.channel_id}/{t.chat_id}" if t.channel_id else "无通道"
            output += f"[{status}] {t.name} ({t.id})\n"
            output += f"    类型: {t.trigger_type.value}, 下次: {next_run}, 推送: {channel_info}\n"

        return output

    async def _cancel_task(self, params: dict) -> str:
        """取消任务"""
        task_id = params.get("task_id")
        if not task_id:
            return "❌ 缺少必填参数: task_id"

        result = await self.agent.task_scheduler.remove_task(task_id)

        if result == "ok":
            return f"✅ 任务 {task_id} 已取消"
        elif result == "system_task":
            return f"⚠️ 「{task_id}」是系统内置任务，不能删除。如需暂停，可以用 update_scheduled_task 设置 enabled=false"
        else:
            return f"❌ 任务 {task_id} 不存在"

    async def _update_task(self, params: dict) -> str:
        """更新任务（通过 scheduler 公共 API）"""
        task_id = params.get("task_id")
        if not task_id:
            return "❌ 缺少必填参数: task_id"
        task = self.agent.task_scheduler.get_task(task_id)
        if not task:
            return f"❌ 任务 {task_id} 不存在"

        changes = []
        updates: dict = {}

        if "notify_on_start" in params:
            metadata = dict(task.metadata)
            metadata["notify_on_start"] = params["notify_on_start"]
            updates["metadata"] = metadata
            changes.append("开始通知: " + ("开" if params["notify_on_start"] else "关"))
        if "notify_on_complete" in params:
            metadata = updates.get("metadata", dict(task.metadata))
            metadata["notify_on_complete"] = params["notify_on_complete"]
            updates["metadata"] = metadata
            changes.append("完成通知: " + ("开" if params["notify_on_complete"] else "关"))

        if "target_channel" in params:
            target_channel = params["target_channel"]
            resolved = self._resolve_target_channel(target_channel)
            if resolved:
                updates["channel_id"] = resolved[0]
                updates["chat_id"] = resolved[1]
                changes.append(f"推送通道: {target_channel}")
            else:
                return (
                    f"❌ 指定的通道 '{target_channel}' 未配置或暂无可用会话。\n"
                    f"已配置的通道: {self._list_available_channels()}"
                )

        if updates:
            await self.agent.task_scheduler.update_task(task_id, updates)

        if "enabled" in params:
            if params["enabled"]:
                await self.agent.task_scheduler.enable_task(task_id)
                changes.append("已启用")
            else:
                await self.agent.task_scheduler.disable_task(task_id)
                changes.append("已暂停")

        if changes:
            return f"✅ 任务 {task.name} 已更新: " + ", ".join(changes)
        return "⚠️ 没有指定要修改的设置"

    async def _trigger_task(self, params: dict) -> str:
        """立即触发任务"""
        task_id = params.get("task_id")
        if not task_id:
            return "❌ 缺少必填参数: task_id"

        task = self.agent.task_scheduler.get_task(task_id)
        if not task:
            return f"❌ 任务 {task_id} 不存在"
        if not task.enabled:
            return f"⚠️ 任务「{task.name}」已被暂停，请先恢复后再触发"

        execution = await self.agent.task_scheduler.trigger_now(task_id)

        if execution:
            status = "成功" if execution.status == "success" else "失败"
            return f"✅ 任务已触发执行，状态: {status}\n结果: {execution.result or execution.error or 'N/A'}"
        else:
            return f"❌ 任务 {task_id} 正在执行中或暂时无法触发"

    def _get_gateway(self):
        """获取消息网关实例"""
        # 优先从 executor 获取（executor 持有运行时的 gateway 引用）
        executor = getattr(self.agent, "_task_executor", None)
        if executor and getattr(executor, "gateway", None):
            return executor.gateway

        # fallback: 从全局 executor 获取（多 Agent 模式）
        from ...scheduler import get_active_executor
        global_executor = get_active_executor()
        if global_executor and getattr(global_executor, "gateway", None):
            return global_executor.gateway

        # fallback: 从 IM 上下文获取
        from ...core.im_context import get_im_gateway

        return get_im_gateway()

    def _resolve_target_channel(self, target_channel: str) -> tuple[str, str] | None:
        """
        将用户指定的通道名解析为 (channel_id, chat_id)

        策略（逐级回退）:
        1. 检查 gateway 中是否有该通道的适配器（即通道已配置并启动）
        2. 从 session_manager 中找到该通道最近活跃的 session
        3. 如果没有活跃 session，尝试从持久化文件 sessions.json 中查找
        4. 从通道注册表 channel_registry.json 查找历史记录（不受 session 过期影响）

        Args:
            target_channel: 通道名（如 wework、telegram、dingtalk 等）

        Returns:
            (channel_id, chat_id) 或 None
        """
        gateway = self._get_gateway()
        if not gateway:
            logger.warning("No gateway available to resolve target_channel")
            return None

        # 1. 检查适配器是否存在
        adapters = getattr(gateway, "_adapters", {})
        if target_channel not in adapters:
            logger.warning(f"Channel '{target_channel}' not found in gateway adapters")
            return None

        adapter = adapters[target_channel]
        if not getattr(adapter, "is_running", False):
            logger.warning(f"Channel '{target_channel}' adapter is not running")
            return None

        # 2. 从 session_manager 查找该通道的最近活跃 session
        session_manager = getattr(gateway, "session_manager", None)
        if session_manager:
            sessions = session_manager.list_sessions(channel=target_channel)
            if sessions:
                # 按最近活跃排序
                sessions.sort(
                    key=lambda s: getattr(s, "last_active", datetime.min),
                    reverse=True,
                )
                best = sessions[0]
                return (best.channel, best.chat_id)

        # 3. 从持久化文件中查找
        if session_manager:
            import json

            sessions_file = getattr(session_manager, "storage_path", None)
            if sessions_file:
                sessions_file = sessions_file / "sessions.json"
                if sessions_file.exists():
                    try:
                        with open(sessions_file, encoding="utf-8") as f:
                            raw_sessions = json.load(f)
                        # 过滤该通道的 session
                        channel_sessions = [
                            s for s in raw_sessions
                            if s.get("channel") == target_channel and s.get("chat_id")
                        ]
                        if channel_sessions:
                            channel_sessions.sort(
                                key=lambda s: s.get("last_active", ""),
                                reverse=True,
                            )
                            best = channel_sessions[0]
                            return (best["channel"], best["chat_id"])
                    except Exception as e:
                        logger.error(f"Failed to read sessions file: {e}")

        # 4. 从通道注册表查找历史记录（不受 session 过期影响）
        if session_manager and hasattr(session_manager, "get_known_channel_target"):
            known = session_manager.get_known_channel_target(target_channel)
            if known:
                logger.info(
                    f"Resolved target_channel='{target_channel}' from channel registry: "
                    f"chat_id={known[1]}"
                )
                return known

        logger.warning(
            f"Channel '{target_channel}' is configured but no session found "
            f"(neither active session nor channel registry). "
            f"Please send at least one message through this channel first."
        )
        return None

    def _list_available_channels(self) -> str:
        """列出所有已配置且在运行的 IM 通道名"""
        gateway = self._get_gateway()
        if not gateway:
            return "（无法获取通道信息）"

        adapters = getattr(gateway, "_adapters", {})
        if not adapters:
            return "（无已配置的通道）"

        running = []
        for name, adapter in adapters.items():
            status = "✓" if getattr(adapter, "is_running", False) else "✗"
            running.append(f"{name}({status})")

        return ", ".join(running) if running else "（无已配置的通道）"

    def _query_executions(self, params: dict) -> str:
        """查询执行历史"""
        task_id = params.get("task_id")
        limit = min(params.get("limit", 10), 50)

        execs = self.agent.task_scheduler.get_executions(
            task_id=task_id, limit=limit
        )

        if not execs:
            if task_id:
                return f"任务 {task_id} 暂无执行记录"
            return "暂无任何任务执行记录"

        lines = []
        for e in reversed(execs):
            time_str = e.started_at.strftime("%m-%d %H:%M") if e.started_at else "?"
            status_icon = "✅" if e.status == "success" else "❌"
            duration = f"{e.duration_seconds:.1f}s" if e.duration_seconds else "-"
            line = f"  {status_icon} {time_str} | 耗时 {duration}"
            if e.error:
                line += f" | 错误: {e.error[:100]}"
            lines.append(line)

        header = f"任务 {task_id} 的" if task_id else ""
        return f"📋 {header}最近 {len(execs)} 条执行记录：\n" + "\n".join(lines)


def create_handler(agent: "Agent"):
    """创建定时任务处理器"""
    handler = ScheduledHandler(agent)
    return handler.handle

"""
定时任务处理器

处理定时任务相关的系统技能：
- schedule_task: 创建定时任务
- list_scheduled_tasks: 列出任务
- cancel_scheduled_task: 取消任务
- update_scheduled_task: 更新任务
- trigger_scheduled_task: 立即触发
"""

import logging
from datetime import datetime, timedelta
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
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        if not hasattr(self.agent, "task_scheduler") or not self.agent.task_scheduler:
            return "❌ 定时任务调度器未启动"

        if tool_name == "schedule_task":
            return await self._schedule_task(params)
        elif tool_name == "list_scheduled_tasks":
            return self._list_tasks(params)
        elif tool_name == "cancel_scheduled_task":
            return await self._cancel_task(params)
        elif tool_name == "update_scheduled_task":
            return self._update_task(params)
        elif tool_name == "trigger_scheduled_task":
            return await self._trigger_task(params)
        else:
            return f"❌ Unknown scheduled tool: {tool_name}"

    async def _schedule_task(self, params: dict) -> str:
        """创建定时任务"""
        from ...core.im_context import get_im_session
        from ...scheduler import ScheduledTask, TriggerType
        from ...scheduler.task import TaskType

        trigger_type = TriggerType(params["trigger_type"])
        task_type = TaskType(params.get("task_type", "task"))

        # ==================== 凌晨“明天”语义歧义处理 ====================
        # 用户在凌晨（例如 00:00-04:00）设置“明天 xx 点”的提醒时，
        # 很常见的真实意图是“今天白天 xx 点”（即同一自然日内的下一次发生）。
        #
        # 由于 schedule_task 的 trigger_config 是由模型填充的“绝对时间”，
        # 这里用启发式做一次兜底：当描述/名称包含“明天/后天”等相对词且时间处于凌晨窗口时，
        # 在创建任务前要求用户确认具体日期，避免默默创建到错误的那一天。
        if trigger_type == TriggerType.ONCE:
            try:
                now = datetime.now()
                run_at_raw = (params.get("trigger_config") or {}).get("run_at")
                # 只处理字符串时间（例如 "2026-02-07 10:00" 或 ISO 格式）
                if isinstance(run_at_raw, str):
                    # fromisoformat 支持 "YYYY-MM-DD HH:MM[:SS]" / "YYYY-MM-DDTHH:MM:SS"
                    parsed = datetime.fromisoformat(run_at_raw.strip())
                    text_hint = " ".join(
                        str(x)
                        for x in (
                            params.get("name", ""),
                            params.get("description", ""),
                            params.get("reminder_message", ""),
                            params.get("prompt", ""),
                        )
                        if x
                    )
                    # 凌晨窗口：默认 00:00-04:00（可后续做成配置）
                    in_midnight_window = 0 <= now.hour < 4
                    has_relative_tomorrow = ("明天" in text_hint) or ("后天" in text_hint)
                    # 若包含“明天/后天”且解析出来的日期正好是“明天/后天”，则触发确认
                    if in_midnight_window and has_relative_tomorrow:
                        delta_days = (parsed.date() - now.date()).days
                        if delta_days in (1, 2):
                            # 给出两个候选日期：今天/明天（或明天/后天）
                            option1 = parsed - timedelta(days=delta_days)  # 回退到“今天/明天”
                            option2 = parsed
                            return (
                                "⚠️ 检测到**凌晨设置提醒**且文本包含“明天/后天”，可能存在日期歧义。\n\n"
                                f"你希望提醒发生在哪一天？\n"
                                f"1) {option1.strftime('%Y-%m-%d %H:%M')}（按“今天/明天”理解）\n"
                                f"2) {option2.strftime('%Y-%m-%d %H:%M')}（按字面“明天/后天”理解）\n\n"
                                "请直接回复 **1** 或 **2**，或回复一个明确时间（例如 `2026-02-06 10:00`）。\n"
                                "我收到你的确认后，会再帮你创建提醒。"
                            )
            except Exception:
                # 任何解析失败都不阻断创建流程
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
        )
        task.metadata["notify_on_start"] = params.get("notify_on_start", True)
        task.metadata["notify_on_complete"] = params.get("notify_on_complete", True)

        task_id = await self.agent.task_scheduler.add_task(task)
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
        task_id = params["task_id"]
        success = await self.agent.task_scheduler.remove_task(task_id)

        if success:
            return f"✅ 任务 {task_id} 已取消"
        else:
            return f"❌ 任务 {task_id} 不存在"

    def _update_task(self, params: dict) -> str:
        """更新任务"""
        task_id = params["task_id"]
        task = self.agent.task_scheduler.get_task(task_id)
        if not task:
            return f"❌ 任务 {task_id} 不存在"

        changes = []
        if "notify_on_start" in params:
            task.metadata["notify_on_start"] = params["notify_on_start"]
            changes.append("开始通知: " + ("开" if params["notify_on_start"] else "关"))
        if "notify_on_complete" in params:
            task.metadata["notify_on_complete"] = params["notify_on_complete"]
            changes.append("完成通知: " + ("开" if params["notify_on_complete"] else "关"))
        if "enabled" in params:
            if params["enabled"]:
                task.enable()
                changes.append("已启用")
            else:
                task.disable()
                changes.append("已暂停")

        # 修改推送通道
        if "target_channel" in params:
            target_channel = params["target_channel"]
            resolved = self._resolve_target_channel(target_channel)
            if resolved:
                task.channel_id, task.chat_id = resolved
                changes.append(f"推送通道: {target_channel}")
            else:
                return (
                    f"❌ 指定的通道 '{target_channel}' 未配置或暂无可用会话。\n"
                    f"已配置的通道: {self._list_available_channels()}"
                )

        self.agent.task_scheduler._save_tasks()

        if changes:
            return f"✅ 任务 {task.name} 已更新: " + ", ".join(changes)
        return "⚠️ 没有指定要修改的设置"

    async def _trigger_task(self, params: dict) -> str:
        """立即触发任务"""
        task_id = params["task_id"]
        execution = await self.agent.task_scheduler.trigger_now(task_id)

        if execution:
            status = "成功" if execution.status == "success" else "失败"
            return f"✅ 任务已触发执行，状态: {status}\n结果: {execution.result or execution.error or 'N/A'}"
        else:
            return f"❌ 任务 {task_id} 不存在"

    def _get_gateway(self):
        """获取消息网关实例"""
        # 优先从 executor 获取（executor 持有运行时的 gateway 引用）
        executor = getattr(self.agent, "_task_executor", None)
        if executor and getattr(executor, "gateway", None):
            return executor.gateway

        # fallback: 从 IM 上下文获取
        from ...core.im_context import get_im_gateway

        return get_im_gateway()

    def _resolve_target_channel(self, target_channel: str) -> tuple[str, str] | None:
        """
        将用户指定的通道名解析为 (channel_id, chat_id)

        策略:
        1. 检查 gateway 中是否有该通道的适配器（即通道已配置并启动）
        2. 从 session_manager 中找到该通道最近活跃的 session
        3. 如果没有 session，尝试从持久化文件中查找

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

        logger.warning(
            f"Channel '{target_channel}' is configured but no session found. "
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


def create_handler(agent: "Agent"):
    """创建定时任务处理器"""
    handler = ScheduledHandler(agent)
    return handler.handle

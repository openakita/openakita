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

        print("\n📅 定时任务已创建:")
        print(f"   ID: {task_id}")
        print(f"   名称: {task.name}")
        print(f"   类型: {type_display}")
        print(f"   触发: {task.trigger_type.value}")
        print(f"   下次执行: {next_run}")
        if channel_id and chat_id:
            print(f"   通知渠道: {channel_id}/{chat_id}")
        print()

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
            output += f"[{status}] {t.name} ({t.id})\n"
            output += f"    类型: {t.trigger_type.value}, 下次: {next_run}\n"

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


def create_handler(agent: "Agent"):
    """创建定时任务处理器"""
    handler = ScheduledHandler(agent)
    return handler.handle

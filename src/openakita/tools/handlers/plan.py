"""
Todo & Plan 工具处理器

Todo 工具（Agent 模式下的任务执行跟踪）：
- create_todo: 创建任务执行计划
- update_todo_step: 更新步骤状态
- get_todo_status: 获取计划执行状态
- complete_todo: 完成计划

Plan 模式工具（Plan 模式下的规划）：
- create_plan_file: 创建 .plan.md 计划文件
- exit_plan_mode: 退出 Plan 模式
"""

import json
import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

# ============================================
# Session Todo 状态管理（模块级别）
# ============================================

# 记录哪些 session 被标记为需要 Todo（compound 任务）
_session_todo_required: dict[str, bool] = {}

# 记录 session 的活跃 Todo（session_id -> plan_id）
_session_active_todos: dict[str, str] = {}


def require_todo_for_session(session_id: str, required: bool) -> None:
    """标记 session 是否需要 Todo（由 Prompt Compiler 调用）"""
    _session_todo_required[session_id] = required
    logger.info(f"[Plan] Session {session_id} todo_required={required}")


def is_todo_required(session_id: str) -> bool:
    """检查 session 是否被标记为需要 Todo"""
    return _session_todo_required.get(session_id, False)


def has_active_todo(session_id: str) -> bool:
    """检查 session 是否有活跃的 Todo"""
    return session_id in _session_active_todos


def get_active_plan_id(session_id: str) -> str | None:
    """获取 session 当前活跃 Todo 的 plan_id（供 SSE 事件同步用）"""
    return _session_active_todos.get(session_id)


def register_active_todo(session_id: str, plan_id: str) -> None:
    """注册活跃的 Todo"""
    _session_active_todos[session_id] = plan_id
    logger.info(f"[Plan] Registered active todo {plan_id} for session {session_id}")


def unregister_active_todo(session_id: str) -> None:
    """注销活跃的 Todo（保留 handler 以支持后续新建 todo）"""
    if session_id in _session_active_todos:
        todo_id = _session_active_todos.pop(session_id)
        logger.info(f"[Todo] Unregistered todo {todo_id} for session {session_id}")
    if session_id in _session_todo_required:
        del _session_todo_required[session_id]


def clear_session_todo_state(session_id: str) -> None:
    """清除 session 的所有 Todo 状态（会话结束时调用）"""
    _session_todo_required.pop(session_id, None)
    _session_active_todos.pop(session_id, None)
    _session_handlers.pop(session_id, None)


# 存储 session -> PlanHandler 实例的映射（用于任务完成判断时查询 Plan 状态）
_session_handlers: dict[str, "PlanHandler"] = {}


def _emit_todo_lifecycle_event(session_id: str, event_type: str, plan: dict | None = None) -> None:
    """通过 WebSocket 广播 todo 生命周期事件（供非流式路径使用）"""
    try:
        import asyncio
        from ...api.routes.websocket import broadcast_event
        data: dict = {"sessionId": session_id, "type": event_type}
        if plan:
            data["planId"] = plan.get("id", "")
            data["status"] = plan.get("status", "")
        asyncio.ensure_future(broadcast_event(f"todo:{event_type}", data))
    except Exception as e:
        logger.debug(f"[Todo] Failed to emit lifecycle event {event_type}: {e}")


def auto_close_todo(session_id: str) -> bool:
    """
    自动关闭指定 session 的活跃 Todo（任务结束时调用）。

    当一轮 ReAct 循环结束但 LLM 未显式调用 complete_todo 时，
    此函数确保 Todo 被正确收尾：
    - in_progress 步骤 -> completed（已开始执行，视为完成）
    - pending 步骤 -> skipped（未执行到）
    - Todo 状态设为 completed，保存并注销

    Returns:
        True 如果有 Todo 被关闭，False 如果没有活跃 Todo
    """
    if not has_active_todo(session_id):
        return False

    handler = get_todo_handler_for_session(session_id)
    plan = handler.get_plan_for(session_id) if handler else None
    if not handler or not plan:
        unregister_active_todo(session_id)
        return True

    steps = plan.get("steps", [])
    auto_closed_count = 0

    for step in steps:
        status = step.get("status", "pending")
        if status == "in_progress":
            step["status"] = "completed"
            step["result"] = step.get("result") or "(自动标记完成)"
            step["completed_at"] = datetime.now().isoformat()
            auto_closed_count += 1
        elif status == "pending":
            step["status"] = "skipped"
            step["result"] = "(任务结束时未执行到)"
            auto_closed_count += 1

    plan["status"] = "completed"
    plan["completed_at"] = datetime.now().isoformat()
    if not plan.get("summary"):
        plan["summary"] = "任务结束，计划自动关闭"

    handler._add_log("计划自动关闭（任务结束时未显式 complete_todo）", plan=plan)
    handler._save_plan_markdown(plan=plan)
    handler._todos_by_session.pop(session_id, None)
    if handler.current_todo is plan:
        handler.current_todo = None

    logger.info(
        f"[Todo] Auto-closed todo for session {session_id}, "
        f"auto_updated {auto_closed_count} steps"
    )

    unregister_active_todo(session_id)
    _emit_todo_lifecycle_event(session_id, "todo_completed", plan)
    return True


def cancel_todo(session_id: str) -> bool:
    """
    用户主动取消时关闭活跃 Todo。

    与 auto_close_todo 不同，此函数将计划和未完成步骤标记为 cancelled。

    Returns:
        True 如果有 Todo 被取消，False 如果没有活跃 Todo
    """
    if not has_active_todo(session_id):
        return False

    handler = get_todo_handler_for_session(session_id)
    plan = handler.get_plan_for(session_id) if handler else None
    if not handler or not plan:
        unregister_active_todo(session_id)
        return True

    steps = plan.get("steps", [])

    for step in steps:
        status = step.get("status", "pending")
        if status in ("in_progress", "pending"):
            step["status"] = "cancelled"
            step["result"] = step.get("result") or "(用户取消)"
            step["completed_at"] = datetime.now().isoformat()

    plan["status"] = "cancelled"
    plan["completed_at"] = datetime.now().isoformat()
    if not plan.get("summary"):
        plan["summary"] = "用户主动取消"

    handler._add_log("计划被用户取消", plan=plan)
    handler._save_plan_markdown(plan=plan)
    handler._todos_by_session.pop(session_id, None)
    if handler.current_todo is plan:
        handler.current_todo = None

    logger.info(f"[Todo] Cancelled todo for session {session_id}")
    unregister_active_todo(session_id)
    _emit_todo_lifecycle_event(session_id, "todo_completed", plan)
    return True


def force_close_plan(session_id: str) -> bool:
    """
    强制关闭指定 session 的 Plan 状态（死锁恢复用）。

    无条件清除所有与该 session 关联的 Plan 模块级状态，
    无论 handler 实例或 plan 数据是否可达。
    用于打破 todo_required=True + has_active_todo=False 的死锁。

    Returns:
        True 如果清理了任何状态
    """
    had_state = False
    if session_id in _session_active_todos:
        plan_id = _session_active_todos.pop(session_id)
        logger.warning(f"[Plan] Force-closed active todo {plan_id} for {session_id}")
        had_state = True
    if session_id in _session_todo_required:
        del _session_todo_required[session_id]
        had_state = True
    handler = _session_handlers.get(session_id)
    if handler:
        handler._todos_by_session.pop(session_id, None)
        if handler.current_todo and handler._get_conversation_id() == session_id:
            handler.current_todo = None
        had_state = True
    if had_state:
        logger.warning(f"[Plan] Force-closed all plan state for session {session_id}")
    return had_state


def register_plan_handler(session_id: str, handler: "PlanHandler") -> None:
    """注册 PlanHandler 实例"""
    _session_handlers[session_id] = handler
    logger.debug(f"[Plan] Registered handler for session {session_id}")


def get_todo_handler_for_session(session_id: str) -> Optional["PlanHandler"]:
    """获取 session 对应的 PlanHandler 实例"""
    return _session_handlers.get(session_id)


def get_active_todo_prompt(session_id: str) -> str:
    """
    获取 session 对应的活跃 Todo 提示词段落（注入 system_prompt 用）。

    返回紧凑格式的计划摘要，包含所有步骤及其当前状态。
    如果没有活跃 Todo 或 Todo 已完成，返回空字符串。
    """
    handler = get_todo_handler_for_session(session_id)
    if handler:
        return handler.get_plan_prompt_section(conversation_id=session_id)
    return ""


# Backward-compatible aliases (deprecated — use the *_todo variants)
unregister_active_plan = unregister_active_todo
clear_session_plan_state = clear_session_todo_state
auto_close_plan = auto_close_todo
cancel_plan = cancel_todo
get_plan_handler_for_session = get_todo_handler_for_session
get_active_plan_prompt = get_active_todo_prompt
has_active_plan = has_active_todo
register_active_plan = register_active_todo


def should_require_todo(user_message: str) -> bool:
    """
    检测用户请求是否需要 Todo 模式（多步骤任务检测）

    建议 18：提高阈值，只在"多工具协作或明显多步"时启用
    简单任务直接执行，不要过度计划

    触发条件：
    1. 包含 5+ 个动作词（明显的复杂任务）
    2. 包含 3+ 个动作词 + 连接词（明确的多步骤）
    3. 包含 3+ 个动作词 + 逗号分隔（明确的多步骤）
    """
    if not user_message:
        return False

    msg = user_message.lower()

    # 中文动作词
    zh_action_words = [
        "打开", "搜索", "截图", "发给", "发送", "写", "创建",
        "执行", "运行", "读取", "查看", "保存", "下载", "上传",
        "复制", "粘贴", "删除", "编辑", "修改", "更新", "安装",
        "配置", "设置", "启动", "关闭",
    ]
    # 英文动作词（匹配词根，兼容 -ing / -ed / -s 等形态）
    en_action_words = [
        "open", "search", "screenshot", "send", "write", "create",
        "execute", "run", "read", "view", "save", "download", "upload",
        "copy", "paste", "delete", "edit", "modify", "update", "install",
        "configure", "setup", "start", "stop", "close", "deploy", "build",
        "test", "refactor", "migrate", "fix", "implement", "add", "remove",
    ]

    # 中文连接词
    zh_connectors = ["然后", "接着", "之后", "并且", "再", "最后"]
    # 英文连接词
    en_connectors = ["then", "after that", "next", "finally", "and then", "followed by", "also"]

    # 统计动作词数量
    action_count = sum(1 for w in zh_action_words if w in msg)
    import re as _re
    for w in en_action_words:
        if _re.search(r'\b' + _re.escape(w), msg):
            action_count += 1

    # 检查连接词
    has_connector = (
        any(w in msg for w in zh_connectors)
        or any(_re.search(r'\b' + _re.escape(w) + r'\b', msg) for w in en_connectors)
    )

    # 检查逗号分隔的多个动作
    comma_separated = "，" in msg or "," in msg

    # 判断条件（建议 18：提高阈值）：
    # 1. 有 5 个以上动作词（明显复杂任务）
    # 2. 有 3 个以上动作词 + 连接词（明确多步骤）
    # 3. 有 3 个以上动作词 + 逗号分隔（明确多步骤）
    if action_count >= 5:
        return True
    if action_count >= 3 and has_connector:
        return True
    return bool(action_count >= 3 and comma_separated)


class PlanHandler:
    """Plan 模式处理器"""

    TOOLS = [
        "create_todo",
        "update_todo_step",
        "get_todo_status",
        "complete_todo",
        "create_plan_file",
        "exit_plan_mode",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self.current_todo: dict | None = None
        self._todos_by_session: dict[str, dict] = {}
        self.plan_dir = Path("data/plans")
        self.plan_dir.mkdir(parents=True, exist_ok=True)

    def _get_conversation_id(self) -> str:
        return (
            getattr(self.agent, "_current_conversation_id", None)
            or getattr(self.agent, "_current_session_id", None)
            or ""
        )

    def _get_current_todo(self) -> dict | None:
        """获取当前会话的 Todo（会话隔离）。

        如果本实例没有数据但模块级 _session_handlers 中有旧 handler
        持有该 todo（工具系统热重载后的典型场景），自动恢复到本实例。
        """
        cid = self._get_conversation_id()
        if cid:
            todo = self._todos_by_session.get(cid)
            if todo is not None:
                return todo
            # 尝试从旧 handler 恢复（热重载后 self 是新实例）
            old_handler = _session_handlers.get(cid)
            if old_handler is not None and old_handler is not self:
                old_todo = old_handler._todos_by_session.get(cid)
                if old_todo is not None:
                    self._todos_by_session[cid] = old_todo
                    logger.info(f"[Todo] Recovered todo {old_todo.get('id')} from previous handler for {cid}")
                    return old_todo
            return None
        return self.current_todo

    def _set_current_todo(self, plan: dict | None) -> None:
        """设置当前会话的 Todo（会话隔离）"""
        cid = self._get_conversation_id()
        if cid:
            if plan is not None:
                self._todos_by_session[cid] = plan
            else:
                self._todos_by_session.pop(cid, None)
        else:
            self.current_todo = plan

    def get_plan_for(self, conversation_id: str) -> dict | None:
        """按 conversation_id 获取 Todo（不依赖 agent state，供外部调用）"""
        if conversation_id:
            return self._todos_by_session.get(conversation_id)
        return self.current_todo

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        if tool_name == "create_todo":
            return await self._create_todo(params)
        elif tool_name == "update_todo_step":
            return await self._update_step(params)
        elif tool_name == "get_todo_status":
            return self._get_status()
        elif tool_name == "complete_todo":
            return await self._complete_todo(params)
        elif tool_name == "create_plan_file":
            return await self._create_plan_file(params)
        elif tool_name == "exit_plan_mode":
            return await self._exit_plan_mode(params)
        else:
            return f"❌ Unknown plan tool: {tool_name}"

    async def _create_todo(self, params: dict) -> str:
        """创建任务计划"""
        _plan = self._get_current_todo()
        if _plan and _plan.get("status") == "in_progress":
            plan_id = _plan["id"]
            status = self._get_status()
            return (
                f"⚠️ 已有活跃计划（{plan_id}），请先完成当前计划再创建新的。\n"
                f"如需继续，请逐步更新步骤状态。\n\n{status}"
            )

        # 状态不一致兜底：_session_active_todos 有记录但本实例无 plan 数据
        cid = self._get_conversation_id()
        if cid and has_active_todo(cid) and _plan is None:
            logger.warning(f"[Plan] Inconsistent state: active_todo registered but no plan data for {cid}, force-closing")
            force_close_plan(cid)

        plan_id = f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"

        steps = params.get("steps", [])
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except (json.JSONDecodeError, TypeError):
                return "❌ steps 参数格式错误，需要 JSON 数组"
        if not isinstance(steps, list):
            return "❌ steps 参数格式错误，需要 JSON 数组"
        if len(steps) == 0:
            return "❌ 至少需要一个步骤才能创建计划"

        normalized_steps: list[dict] = []
        for index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                return f"❌ steps[{index}] 格式错误，需要对象"

            step = dict(raw_step)

            # 必填字段校验：id / description 缺失时自动补全，防止下游 KeyError
            if "id" not in step or not str(step["id"]).strip():
                step["id"] = f"step_{index + 1}"
            else:
                step["id"] = str(step["id"]).strip()[:64]
            if "description" not in step or not str(step["description"]).strip():
                step["description"] = step["id"]
            else:
                step["description"] = str(step["description"]).strip()[:512]

            # 兼容模型偶发输出：把字符串化数组字段还原为 list
            for field_name in ("skills", "depends_on"):
                field_value = step.get(field_name)
                if isinstance(field_value, str):
                    try:
                        field_value = json.loads(field_value)
                    except (json.JSONDecodeError, TypeError):
                        return f"❌ steps[{index}].{field_name} 参数格式错误，需要 JSON 数组"
                    if not isinstance(field_value, list):
                        return f"❌ steps[{index}].{field_name} 参数格式错误，需要 JSON 数组"
                    step[field_name] = field_value
                elif field_value is not None and not isinstance(field_value, list):
                    return f"❌ steps[{index}].{field_name} 参数格式错误，需要 JSON 数组"

            step["status"] = "pending"
            step["result"] = ""
            step["started_at"] = None
            step["completed_at"] = None
            # skills: 每步必须可追溯到对应 skill（系统工具也有 system skill）
            step.setdefault("skills", [])
            step["skills"] = self._ensure_step_skills(step)
            normalized_steps.append(step)

        steps = normalized_steps

        _new_plan = {
            "id": plan_id,
            "task_summary": params.get("task_summary", ""),
            "steps": steps,
            "status": "in_progress",
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "logs": [],
        }
        self._set_current_todo(_new_plan)

        # 先落盘再注册，确保注册时文件已可查
        self._save_plan_markdown()

        conversation_id = self._get_conversation_id()
        if conversation_id:
            register_active_todo(conversation_id, plan_id)
            register_plan_handler(conversation_id, self)

        # 记录日志
        self._add_log(f"计划创建：{params.get('task_summary', '')}")
        for step in steps:
            logger.info(
                f"[Plan] Step {step.get('id')} tool={step.get('tool','-')} skills={step.get('skills', [])}"
            )

        # 生成计划展示消息
        plan_message = self._format_plan_message()

        # 进度事件由网关统一发送（节流/合并）
        try:
            session = getattr(self.agent, "_current_session", None)
            gateway = (
                session.get_metadata("_gateway")
                if session and hasattr(session, "get_metadata")
                else None
            )
            if gateway and hasattr(gateway, "emit_progress_event"):
                await gateway.emit_progress_event(
                    session, f"📋 已创建计划：{params.get('task_summary', '')}\n{plan_message}"
                )
        except Exception as e:
            logger.warning(f"Failed to emit plan progress: {e}")

        return f"✅ Created todo：{plan_id}\n\n{plan_message}"

    async def _update_step(self, params: dict) -> str:
        """更新步骤状态"""
        _plan = self._get_current_todo()
        if not _plan:
            cid = self._get_conversation_id()
            if cid and has_active_todo(cid):
                logger.warning(f"[Todo] update_step: todo data lost for {cid}, force-closing stale registration")
                force_close_plan(cid)
            return "❌ 当前没有活动的计划，请先创建一个任务计划"

        step_id = str(params.get("step_id", "")).strip()
        status = str(params.get("status", "")).strip()
        result = params.get("result", "")

        if not step_id:
            return "❌ 请指定要更新的步骤"
        if not status:
            return "❌ 请指定步骤的目标状态（如 in_progress、completed、failed、skipped）"

        _VALID_TRANSITIONS: dict[str, set[str]] = {
            "pending": {"in_progress", "skipped", "cancelled"},
            "in_progress": {"completed", "failed", "skipped", "cancelled"},
            "completed": set(),
            "failed": {"in_progress"},
            "skipped": {"in_progress"},
            "cancelled": set(),
        }

        # 查找并更新步骤
        step_found = False
        for step in _plan["steps"]:
            if step["id"] == step_id:
                old_status = step.get("status", "pending")
                allowed = _VALID_TRANSITIONS.get(old_status, set())
                if status != old_status and status not in allowed:
                    return (
                        f"⚠️ 步骤 {step_id} 当前状态为 {old_status}，"
                        f"不允许直接变更为 {status}。"
                        f"允许的目标状态：{', '.join(sorted(allowed)) or '无（已终态）'}"
                    )
                # depends_on 依赖检查：步骤开始前确认前置已完成
                if status == "in_progress":
                    deps = step.get("depends_on", [])
                    if deps:
                        _DONE = {"completed", "skipped"}
                        steps_map = {s["id"]: s for s in _plan["steps"]}
                        blocked = [d for d in deps if steps_map.get(d, {}).get("status") not in _DONE]
                        if blocked:
                            return (
                                f"⚠️ 步骤 {step_id} 依赖于 {', '.join(blocked)}，"
                                f"这些步骤尚未完成，请先完成它们。"
                            )
                step["status"] = status
                step["result"] = result
                # 保底：确保 skills 存在（兼容旧 plan 文件/旧模型输出）
                step.setdefault("skills", [])
                step["skills"] = self._ensure_step_skills(step)

                if status == "in_progress" and not step.get("started_at"):
                    step["started_at"] = datetime.now().isoformat()
                elif status in ["completed", "failed", "skipped"]:
                    step["completed_at"] = datetime.now().isoformat()

                step_found = True
                logger.info(
                    f"[Plan] Step update {step_id} status={status} tool={step.get('tool','-')} skills={step.get('skills', [])}"
                )
                break

        if not step_found:
            return f"❌ 未找到步骤：{step_id}"

        # 保存更新
        self._save_plan_markdown()

        # 记录日志
        status_emoji = {"in_progress": "🔄", "completed": "✅", "failed": "❌", "skipped": "⏭️"}.get(
            status, "📌"
        )

        self._add_log(f"{status_emoji} {step_id}: {result or status}")

        # 通知用户（每个状态变化都通知）
        # 计算进度：使用步骤的位置序号（而非已完成数量）
        steps = _plan["steps"]
        total_count = len(steps)

        # 使用步骤在列表中的位置序号（1-indexed）
        step_number = next(
            (i + 1 for i, s in enumerate(steps) if s["id"] == step_id),
            0,
        )

        # 查找步骤描述
        step_desc = ""
        for s in steps:
            if s["id"] == step_id:
                step_desc = s.get("description", "")
                break

        message = f"{status_emoji} **[{step_number}/{total_count}]** {step_desc or step_id}"
        if status == "completed" and result:
            message += f"\n   结果：{result}"
        elif status == "failed":
            message += f"\n   ❌ 错误：{result or '未知错误'}"

        try:
            session = getattr(self.agent, "_current_session", None)
            gateway = (
                session.get_metadata("_gateway")
                if session and hasattr(session, "get_metadata")
                else None
            )
            if gateway and hasattr(gateway, "emit_progress_event"):
                await gateway.emit_progress_event(session, message)
        except Exception as e:
            logger.warning(f"Failed to emit step progress: {e}")

        response = f"步骤 {step_id} 状态已更新为 {status}"

        # 验证提醒：步骤完成后建议验证结果
        if status == "completed":
            pending_steps = [s for s in steps if s.get("status") in ("pending", "in_progress")]
            if pending_steps:
                next_step = pending_steps[0]
                response += f"\n\n💡 下一步：{next_step.get('description', next_step['id'])}"
            else:
                response += "\n\n✅ 所有步骤已完成，请结束此计划。"
        elif status == "failed":
            response += "\n\n⚠️ 该步骤失败，请检查原因后决定是否重试或跳过。"

        return response

    def _get_status(self) -> str:
        """获取计划状态"""
        plan = self._get_current_todo()
        if not plan:
            return "当前没有活动的计划"
        steps = plan["steps"]

        completed = sum(1 for s in steps if s["status"] == "completed")
        failed = sum(1 for s in steps if s["status"] == "failed")
        pending = sum(1 for s in steps if s["status"] == "pending")
        in_progress = sum(1 for s in steps if s["status"] == "in_progress")

        status_text = f"""## 计划状态：{plan["task_summary"]}

**计划ID**: {plan["id"]}
**状态**: {plan["status"]}
**进度**: {completed}/{len(steps)} 完成

### 步骤列表

| 步骤 | 描述 | Skills | 状态 | 结果 |
|------|------|--------|------|------|
"""

        for step in steps:
            status_emoji = {
                "pending": "⬜",
                "in_progress": "🔄",
                "completed": "✅",
                "failed": "❌",
                "skipped": "⏭️",
            }.get(step["status"], "❓")

            skills = ", ".join(step.get("skills", []) or [])
            status_text += f"| {step['id']} | {step['description']} | {skills or '-'} | {status_emoji} | {step.get('result', '-')} |\n"

        status_text += f"\n**统计**: ✅ {completed} 完成, ❌ {failed} 失败, ⬜ {pending} 待执行, 🔄 {in_progress} 执行中"

        return status_text

    async def _complete_todo(self, params: dict) -> str:
        """完成计划"""
        _plan = self._get_current_todo()
        if not _plan:
            cid = self._get_conversation_id()
            if cid and has_active_todo(cid):
                logger.warning(f"[Plan] complete_todo: plan data lost for {cid}, force-closing stale registration")
                force_close_plan(cid)
                return "⚠️ 旧计划数据已丢失，已强制清除死锁状态。可以开始新任务。"
            return "❌ 当前没有活动的计划"

        summary = params.get("summary", "")

        steps = _plan["steps"]
        still_active = [s for s in steps if s.get("status") in ("pending", "in_progress")]
        if still_active:
            active_ids = [s.get("id", "?") for s in still_active[:5]]
            return (
                f"⚠️ 还有 {len(still_active)} 个步骤未完成：{', '.join(active_ids)}。\n"
                "请先完成或跳过这些步骤，再标记计划完成。"
            )

        _plan["status"] = "completed"
        _plan["completed_at"] = datetime.now().isoformat()
        _plan["summary"] = summary

        completed = sum(1 for s in steps if s["status"] == "completed")
        failed = sum(1 for s in steps if s["status"] == "failed")

        # 保存最终状态
        self._save_plan_markdown()
        self._add_log(f"计划完成：{summary}")

        # 生成完成消息
        complete_message = f"""🎉 **任务完成！**

{summary}

**执行统计**：
- 总步骤：{len(steps)}
- 成功：{completed}
- 失败：{failed}
"""

        # 完成事件由网关统一发送（节流/合并）
        try:
            session = getattr(self.agent, "_current_session", None)
            gateway = (
                session.get_metadata("_gateway")
                if session and hasattr(session, "get_metadata")
                else None
            )
            if gateway and hasattr(gateway, "emit_progress_event"):
                await gateway.emit_progress_event(session, complete_message)
        except Exception as e:
            logger.warning(f"Failed to emit complete progress: {e}")

        plan_id = _plan["id"]
        self._set_current_todo(None)

        conversation_id = self._get_conversation_id()
        if conversation_id:
            unregister_active_todo(conversation_id)

        return f"✅ 计划 {plan_id} 已完成\n\n{complete_message}"

    async def _create_plan_file(self, params: dict) -> str:
        """创建 Cursor 风格的 .plan.md 文件（YAML frontmatter + Markdown body）。

        用于 Plan 模式下生成结构化的计划文件。
        """
        name = params.get("name", "Untitled Plan")
        overview = params.get("overview", "")
        todos = params.get("todos", [])
        body = params.get("body", "")

        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except (json.JSONDecodeError, TypeError):
                return "❌ todos 参数格式错误，需要 JSON 数组"

        # Generate a plan file ID
        import hashlib as _hashlib
        _slug = name[:30].replace(" ", "_").replace("/", "_")
        _hash = _hashlib.md5(name.encode()).hexdigest()[:8]
        filename = f"{_slug}_{_hash}.plan.md"

        plan_file = self.plan_dir / filename
        if plan_file.exists():
            for _seq in range(2, 100):
                _candidate = self.plan_dir / f"{_slug}_{_hash}_{_seq}.plan.md"
                if not _candidate.exists():
                    plan_file = _candidate
                    break

        def _yaml_escape(val: str) -> str:
            """YAML 安全转义：含特殊字符时加引号并转义内部双引号"""
            if not val:
                return '""'
            needs_quote = any(c in val for c in (':', '#', '"', "'", '\n', '{', '}', '[', ']', ',', '&', '*', '?', '|', '-', '<', '>', '=', '!', '%', '@', '`'))
            if needs_quote:
                return '"' + val.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'
            return val

        # Build YAML frontmatter
        yaml_lines = ["---"]
        yaml_lines.append(f"name: {_yaml_escape(name)}")
        if overview:
            yaml_lines.append(f"overview: {_yaml_escape(overview)}")
        if todos:
            yaml_lines.append("todos:")
            for todo in todos:
                todo_id = todo.get("id", f"step_{secrets.token_hex(3)}")
                content = todo.get("content", "")
                status = todo.get("status", "pending")
                yaml_lines.append(f"  - id: {_yaml_escape(todo_id)}")
                yaml_lines.append(f"    content: {_yaml_escape(content)}")
                yaml_lines.append(f"    status: {status}")
        yaml_lines.append("isProject: true")
        yaml_lines.append("---")

        # Combine frontmatter + body
        content = "\n".join(yaml_lines) + "\n\n" + body

        plan_file.write_text(content, encoding="utf-8")
        logger.info(f"[Plan] Created plan file: {plan_file}")

        # Also register as active plan internally
        plan_id = f"planfile_{_hash}"
        steps = []
        for todo in todos:
            steps.append({
                "id": todo.get("id", f"step_{secrets.token_hex(3)}"),
                "description": todo.get("content", ""),
                "status": todo.get("status", "pending"),
                "result": "",
                "started_at": None,
                "completed_at": None,
                "skills": [],
            })

        _new_plan = {
            "id": plan_id,
            "task_summary": name,
            "steps": steps,
            "status": "in_progress",
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "logs": [],
            "plan_file": str(plan_file),
        }
        self._set_current_todo(_new_plan)

        conversation_id = self._get_conversation_id()
        if conversation_id:
            register_active_todo(conversation_id, plan_id)
            register_plan_handler(conversation_id, self)

        self._add_log(f"Plan 文件创建：{name}")

        return (
            f"✅ Plan 文件已创建: {plan_file}\n\n"
            f"包含 {len(todos)} 个步骤。\n\n"
            f"⚠️ 下一步：请调用 exit_plan_mode 通知用户规划完成。\n"
            f"不要尝试执行计划中的任何步骤 — 用户需要先审批计划。"
        )

    async def _exit_plan_mode(self, params: dict) -> str:
        """Exit Plan mode — OpenCode-style mode switch.

        1. Emit SSE events to notify the frontend
        2. Set a flag on the agent to signal mode switch to "agent"
        3. Return a message asking the user to approve the plan
        """
        summary = params.get("summary", "规划完成")

        try:
            session = getattr(self.agent, "_current_session", None)
            gateway = (
                session.get_metadata("_gateway")
                if session and hasattr(session, "get_metadata")
                else None
            )
            if gateway and hasattr(gateway, "emit_progress_event"):
                await gateway.emit_progress_event(
                    session,
                    f"📋 **Plan 模式完成**\n{summary}\n\n等待用户审批后执行...",
                )
        except Exception as e:
            logger.warning(f"Failed to emit exit_plan_mode event: {e}")

        # Emit SSE event for frontend (plan approval UI)
        conversation_id = self._get_conversation_id()
        plan_id = ""
        plan_file_path = ""
        current = self._get_current_todo()
        if current:
            plan_id = current.get("id", "")
            plan_file_path = current.get("plan_file", "")

        try:
            from ...api.routes.websocket import broadcast_event
            await broadcast_event("plan:ready_for_approval", {
                "conversation_id": conversation_id,
                "summary": summary,
                "plan_id": plan_id,
                "plan_file": plan_file_path,
            })
        except Exception:
            pass

        # Signal the agent that Plan mode is done — next user message
        # should switch to agent mode (OpenCode synthetic message pattern)
        try:
            pending_dict = getattr(self.agent, "_plan_exit_pending", None)
            if isinstance(pending_dict, dict):
                pending_dict[conversation_id] = {
                    "summary": summary,
                    "plan_id": plan_id,
                    "plan_file": plan_file_path,
                    "conversation_id": conversation_id,
                }
            else:
                self.agent._plan_exit_pending = {}
                self.agent._plan_exit_pending[conversation_id] = {
                    "summary": summary,
                    "plan_id": plan_id,
                    "plan_file": plan_file_path,
                    "conversation_id": conversation_id,
                }
            logger.info(
                f"[Plan] exit_plan_mode: flagged for mode switch "
                f"(conv={conversation_id}, plan_file={plan_file_path})"
            )
        except Exception as e:
            logger.warning(f"[Plan] Failed to set _plan_exit_pending: {e}")

        return (
            f"✅ Plan completed.\n\n"
            f"{summary}\n\n"
            f"The plan is ready for user review. "
            f"STOP HERE — do NOT attempt to execute the plan. "
            f"Wait for user to approve or request changes."
        )

    def _format_plan_message(self) -> str:
        """格式化计划展示消息"""
        plan = self._get_current_todo()
        if not plan:
            return ""
        steps = plan["steps"]

        message = f"""📋 **任务计划**：{plan["task_summary"]}

"""
        for i, step in enumerate(steps):
            prefix = "├─" if i < len(steps) - 1 else "└─"
            skills = ", ".join(step.get("skills", []) or [])
            if skills:
                message += f"{prefix} {i + 1}. {step['description']}  (skills: {skills})\n"
            else:
                message += f"{prefix} {i + 1}. {step['description']}\n"

        message += "\n开始执行..."

        return message

    def get_plan_prompt_section(self, conversation_id: str = "") -> str:
        """
        生成注入 system_prompt 的计划摘要段落。

        该段落放在 system_prompt 中，不随 working_messages 压缩而丢失，
        确保 LLM 在任何时候都能看到完整的计划结构和最新进度。

        Args:
            conversation_id: 指定会话 ID 以精确查找 Plan（避免依赖 agent state）

        Returns:
            紧凑格式的计划段落字符串；无活跃 Plan 或 Plan 已完成时返回空字符串。
        """
        plan = self.get_plan_for(conversation_id) if conversation_id else self._get_current_todo()
        if not plan or plan.get("status") == "completed":
            return ""
        steps = plan["steps"]
        total = len(steps)
        completed = sum(1 for s in steps if s["status"] in ("completed", "failed", "skipped"))

        lines = [
            f"## Active Plan: {plan['task_summary']}  (id: {plan['id']})",
            f"Progress: {completed}/{total} done",
            "",
        ]

        _max_result_len = 200 if total > 20 else 300
        _char_budget = 4000
        for i, step in enumerate(steps):
            num = i + 1
            icon = {
                "pending": "  ",
                "in_progress": ">>",
                "completed": "OK",
                "failed": "XX",
                "skipped": "--",
                "cancelled": "~~",
            }.get(step["status"], "??")
            desc = step.get("description", step["id"])
            result_hint = ""
            if step["status"] == "completed" and step.get("result"):
                result_hint = f" => {step['result'][:_max_result_len]}"
            elif step["status"] == "failed" and step.get("result"):
                result_hint = f" => FAIL: {step['result'][:_max_result_len]}"
            line = f"  [{icon}] {num}. {desc}{result_hint}"
            _char_budget -= len(line)
            if _char_budget < 0:
                lines.append(f"  ... ({total - i} more steps omitted)")
                break
            lines.append(line)

        plan_file = plan.get("plan_file", "")
        if plan_file:
            lines.append(f"Plan file: {plan_file}")

        lines.append("")
        if plan_file:
            lines.append(
                "IMPORTANT: This plan already exists as a plan file. "
                "In Plan mode, you can modify the plan file using write_file. "
                "In Agent mode, use update_todo_step to track execution progress. "
                "Do NOT call create_todo or create_plan_file again."
            )
        else:
            lines.append(
                "IMPORTANT: This plan already exists. Do NOT call create_todo again. "
                "Continue from the current step using update_todo_step."
            )

        # Stale step 提醒：长时间未更新的 in_progress 步骤
        for step in steps:
            if step["status"] == "in_progress" and step.get("started_at"):
                try:
                    started = datetime.fromisoformat(step["started_at"])
                    elapsed = (datetime.now() - started).total_seconds()
                    if elapsed > 300:
                        mins = int(elapsed / 60)
                        lines.append(
                            f"\n⚠️ STALE: Step '{step['id']}' has been in_progress for {mins} min. "
                            "Consider completing, failing, or skipping it."
                        )
                except (ValueError, TypeError):
                    pass

        return "\n".join(lines)

    def _save_plan_markdown(self, plan: dict | None = None) -> None:
        """保存计划到 Markdown 文件（可传入显式 plan 引用避免依赖 agent state）"""
        if plan is None:
            plan = self._get_current_todo()
        if not plan:
            return
        plan_file = self.plan_dir / f"{plan['id']}.md"

        content = f"""# 任务计划：{plan["task_summary"]}

**计划ID**: {plan["id"]}
**创建时间**: {plan["created_at"]}
**状态**: {plan["status"]}
**完成时间**: {plan.get("completed_at", "-")}

## 步骤列表

| ID | 描述 | Skills | 工具 | 状态 | 结果 |
|----|------|--------|------|------|------|
"""

        def _md_escape_cell(val: str) -> str:
            return val.replace("|", "\\|").replace("\n", " ")

        for step in plan["steps"]:
            status_emoji = {
                "pending": "⬜",
                "in_progress": "🔄",
                "completed": "✅",
                "failed": "❌",
                "skipped": "⏭️",
                "cancelled": "🚫",
            }.get(step["status"], "❓")

            tool = _md_escape_cell(step.get("tool", "-"))
            skills = _md_escape_cell(", ".join(step.get("skills", []) or []) or "-")
            result = _md_escape_cell(step.get("result", "-") or "-")
            sid = _md_escape_cell(step["id"])
            desc = _md_escape_cell(step["description"])

            content += (
                f"| {sid} | {desc} | {skills} | {tool} | {status_emoji} | {result} |\n"
            )

        content += "\n## 执行日志\n\n"
        for log in plan.get("logs", []):
            content += f"- {log}\n"

        if plan.get("summary"):
            content += f"\n## 完成总结\n\n{plan['summary']}\n"

        plan_file.write_text(content, encoding="utf-8")
        logger.info(f"[Plan] Saved to: {plan_file}")

    def _add_log(self, message: str, plan: dict | None = None) -> None:
        """添加日志（可传入显式 plan 引用避免依赖 agent state）"""
        if plan is None:
            plan = self._get_current_todo()
        if plan:
            timestamp = datetime.now().strftime("%H:%M:%S")
            plan.setdefault("logs", []).append(f"[{timestamp}] {message}")

    def _ensure_step_skills(self, step: dict) -> list[str]:
        """
        确保步骤的 skills 字段存在且可追溯。

        规则：
        - 如果 step 已给出 skills，保留并去重。
        - 如果没给出 skills 但给了 tool：尝试用 tool_name 匹配 system skill（skills/system/* 的 tool-name）。
        """
        skills = step.get("skills") or []
        if not isinstance(skills, list):
            skills = []

        # 若没提供 skills，则尝试从 tool 推断 system skill
        if not skills:
            tool = step.get("tool")
            if tool:
                try:
                    for s in self.agent.skill_registry.list_all():
                        if getattr(s, "system", False) and getattr(s, "tool_name", None) == tool:
                            skills = [s.name]
                            break
                except Exception:
                    pass

        # 去重并保持稳定顺序
        seen = set()
        normalized: list[str] = []
        for name in skills:
            if not name or not isinstance(name, str):
                continue
            if name in seen:
                continue
            seen.add(name)
            normalized.append(name)
        return normalized


def create_todo_handler(agent: "Agent"):
    """创建 Plan Handler 处理函数"""
    handler = PlanHandler(agent)
    return handler.handle

"""
Plan 模式处理器

处理任务计划相关的工具：
- create_plan: 创建任务执行计划
- update_plan_step: 更新步骤状态
- get_plan_status: 获取计划执行状态
- complete_plan: 完成计划
"""

import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

# ============================================
# Session Plan 状态管理（模块级别）
# ============================================

# 记录哪些 session 被标记为需要 Plan（compound 任务）
_session_plan_required: dict[str, bool] = {}

# 记录 session 的活跃 Plan（session_id -> plan_id）
_session_active_plans: dict[str, str] = {}


def require_plan_for_session(session_id: str, required: bool) -> None:
    """标记 session 是否需要 Plan（由 Prompt Compiler 调用）"""
    _session_plan_required[session_id] = required
    logger.info(f"[Plan] Session {session_id} plan_required={required}")


def is_plan_required(session_id: str) -> bool:
    """检查 session 是否被标记为需要 Plan"""
    return _session_plan_required.get(session_id, False)


def has_active_plan(session_id: str) -> bool:
    """检查 session 是否有活跃的 Plan"""
    return session_id in _session_active_plans


def register_active_plan(session_id: str, plan_id: str) -> None:
    """注册活跃的 Plan"""
    _session_active_plans[session_id] = plan_id
    logger.info(f"[Plan] Registered active plan {plan_id} for session {session_id}")


def unregister_active_plan(session_id: str) -> None:
    """注销活跃的 Plan"""
    if session_id in _session_active_plans:
        plan_id = _session_active_plans.pop(session_id)
        logger.info(f"[Plan] Unregistered plan {plan_id} for session {session_id}")
    # 同时清除 plan_required 标记和 handler
    if session_id in _session_plan_required:
        del _session_plan_required[session_id]
    if session_id in _session_handlers:
        del _session_handlers[session_id]


def clear_session_plan_state(session_id: str) -> None:
    """清除 session 的所有 Plan 状态（会话结束时调用）"""
    _session_plan_required.pop(session_id, None)
    _session_active_plans.pop(session_id, None)
    _session_handlers.pop(session_id, None)


# 存储 session -> PlanHandler 实例的映射（用于任务完成判断时查询 Plan 状态）
_session_handlers: dict[str, "PlanHandler"] = {}


def auto_close_plan(session_id: str) -> bool:
    """
    自动关闭指定 session 的活跃 Plan（任务结束时调用）。

    当一轮 ReAct 循环结束但 LLM 未显式调用 complete_plan 时，
    此函数确保 Plan 被正确收尾：
    - in_progress 步骤 → completed（已开始执行，视为完成）
    - pending 步骤 → skipped（未执行到）
    - Plan 状态设为 completed，保存并注销

    Returns:
        True 如果有 Plan 被关闭，False 如果没有活跃 Plan
    """
    if not has_active_plan(session_id):
        return False

    handler = get_plan_handler_for_session(session_id)
    plan = handler.get_plan_for(session_id) if handler else None
    if not handler or not plan:
        unregister_active_plan(session_id)
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

    handler._add_log("计划自动关闭（任务结束时未显式 complete_plan）", plan=plan)
    handler._save_plan_markdown(plan=plan)
    handler._plans_by_session.pop(session_id, None)
    if handler.current_plan is plan:
        handler.current_plan = None

    logger.info(
        f"[Plan] Auto-closed plan for session {session_id}, "
        f"auto_updated {auto_closed_count} steps"
    )

    unregister_active_plan(session_id)
    return True


def cancel_plan(session_id: str) -> bool:
    """
    用户主动取消时关闭活跃 Plan。

    与 auto_close_plan 不同，此函数将计划和未完成步骤标记为 cancelled。

    Returns:
        True 如果有 Plan 被取消，False 如果没有活跃 Plan
    """
    if not has_active_plan(session_id):
        return False

    handler = get_plan_handler_for_session(session_id)
    plan = handler.get_plan_for(session_id) if handler else None
    if not handler or not plan:
        unregister_active_plan(session_id)
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
    handler._plans_by_session.pop(session_id, None)
    if handler.current_plan is plan:
        handler.current_plan = None

    logger.info(f"[Plan] Cancelled plan for session {session_id}")
    unregister_active_plan(session_id)
    return True


def register_plan_handler(session_id: str, handler: "PlanHandler") -> None:
    """注册 PlanHandler 实例"""
    _session_handlers[session_id] = handler
    logger.debug(f"[Plan] Registered handler for session {session_id}")


def get_plan_handler_for_session(session_id: str) -> Optional["PlanHandler"]:
    """获取 session 对应的 PlanHandler 实例"""
    return _session_handlers.get(session_id)


def get_active_plan_prompt(session_id: str) -> str:
    """
    获取 session 对应的活跃 Plan 提示词段落（注入 system_prompt 用）。

    返回紧凑格式的计划摘要，包含所有步骤及其当前状态。
    如果没有活跃 Plan 或 Plan 已完成，返回空字符串。
    """
    handler = get_plan_handler_for_session(session_id)
    if handler:
        return handler.get_plan_prompt_section(conversation_id=session_id)
    return ""


def should_require_plan(user_message: str) -> bool:
    """
    检测用户请求是否需要 Plan 模式（多步骤任务检测）

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

    # 动作词列表
    action_words = [
        "打开",
        "搜索",
        "截图",
        "发给",
        "发送",
        "写",
        "创建",
        "执行",
        "运行",
        "读取",
        "查看",
        "保存",
        "下载",
        "上传",
        "复制",
        "粘贴",
        "删除",
        "编辑",
        "修改",
        "更新",
        "安装",
        "配置",
        "设置",
        "启动",
        "关闭",
    ]

    # 连接词（表示多步骤）
    connector_words = ["然后", "接着", "之后", "并且", "再", "最后"]

    # 统计动作词数量
    action_count = sum(1 for word in action_words if word in msg)

    # 检查连接词
    has_connector = any(word in msg for word in connector_words)

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
        "create_plan",
        "update_plan_step",
        "get_plan_status",
        "complete_plan",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self.current_plan: dict | None = None
        self._plans_by_session: dict[str, dict] = {}
        self.plan_dir = Path("data/plans")
        self.plan_dir.mkdir(parents=True, exist_ok=True)

    def _get_conversation_id(self) -> str:
        return (
            getattr(self.agent, "_current_conversation_id", None)
            or getattr(self.agent, "_current_session_id", None)
            or ""
        )

    def _get_current_plan(self) -> dict | None:
        """获取当前会话的 Plan（会话隔离）"""
        cid = self._get_conversation_id()
        if cid:
            return self._plans_by_session.get(cid)
        return self.current_plan

    def _set_current_plan(self, plan: dict | None) -> None:
        """设置当前会话的 Plan（会话隔离）"""
        cid = self._get_conversation_id()
        if cid:
            if plan is not None:
                self._plans_by_session[cid] = plan
            else:
                self._plans_by_session.pop(cid, None)
        else:
            self.current_plan = plan

    def get_plan_for(self, conversation_id: str) -> dict | None:
        """按 conversation_id 获取 Plan（不依赖 agent state，供外部调用）"""
        if conversation_id:
            return self._plans_by_session.get(conversation_id)
        return self.current_plan

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        if tool_name == "create_plan":
            return await self._create_plan(params)
        elif tool_name == "update_plan_step":
            return await self._update_step(params)
        elif tool_name == "get_plan_status":
            return self._get_status()
        elif tool_name == "complete_plan":
            return await self._complete_plan(params)
        else:
            return f"❌ Unknown plan tool: {tool_name}"

    async def _create_plan(self, params: dict) -> str:
        """创建任务计划"""
        _plan = self._get_current_plan()
        if _plan and _plan.get("status") == "in_progress":
            plan_id = _plan["id"]
            status = self._get_status()
            return (
                f"⚠️ 已有活跃计划 {plan_id}，不允许重复创建。\n"
                f"请使用 update_plan_step 继续执行当前计划。\n\n{status}"
            )

        plan_id = f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"

        # 创建 Plan 后：确保工具护栏至少追问 1 次，避免“无确认文本”直接结束
        # 注意：chat 循环里也会基于 active plan 动态提升 effective retries，这里是额外的全局兜底。
        try:
            from ...config import settings as _settings

            if int(getattr(_settings, "force_tool_call_max_retries", 1)) < 1:
                _settings.force_tool_call_max_retries = 1
                logger.info("[Plan] force_tool_call_max_retries bumped to 1 after create_plan")
        except Exception:
            pass

        steps = params.get("steps", [])
        for step in steps:
            step["status"] = "pending"
            step["result"] = ""
            step["started_at"] = None
            step["completed_at"] = None
            # skills: 每步必须可追溯到对应 skill（系统工具也有 system skill）
            step.setdefault("skills", [])
            step["skills"] = self._ensure_step_skills(step)

        _new_plan = {
            "id": plan_id,
            "task_summary": params.get("task_summary", ""),
            "steps": steps,
            "status": "in_progress",
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "logs": [],
        }
        self._set_current_plan(_new_plan)

        conversation_id = self._get_conversation_id()
        if conversation_id:
            register_active_plan(conversation_id, plan_id)
            register_plan_handler(conversation_id, self)  # 注册 handler 以便查询 Plan 状态

        # 保存到文件
        self._save_plan_markdown()

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

        return f"✅ 计划已创建：{plan_id}\n\n{plan_message}"

    async def _update_step(self, params: dict) -> str:
        """更新步骤状态"""
        _plan = self._get_current_plan()
        if not _plan:
            return "❌ 当前没有活动的计划，请先调用 create_plan"

        step_id = params.get("step_id", "")
        status = params.get("status", "")
        result = params.get("result", "")

        # 查找并更新步骤
        step_found = False
        for step in _plan["steps"]:
            if step["id"] == step_id:
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

        return f"步骤 {step_id} 状态已更新为 {status}"

    def _get_status(self) -> str:
        """获取计划状态"""
        plan = self._get_current_plan()
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

    async def _complete_plan(self, params: dict) -> str:
        """完成计划"""
        _plan = self._get_current_plan()
        if not _plan:
            return "❌ 当前没有活动的计划"

        summary = params.get("summary", "")

        _plan["status"] = "completed"
        _plan["completed_at"] = datetime.now().isoformat()
        _plan["summary"] = summary

        steps = _plan["steps"]
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
        self._set_current_plan(None)

        conversation_id = self._get_conversation_id()
        if conversation_id:
            unregister_active_plan(conversation_id)

        return f"✅ 计划 {plan_id} 已完成\n\n{complete_message}"

    def _format_plan_message(self) -> str:
        """格式化计划展示消息"""
        plan = self._get_current_plan()
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
        plan = self.get_plan_for(conversation_id) if conversation_id else self._get_current_plan()
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

        for i, step in enumerate(steps):
            num = i + 1
            icon = {
                "pending": "  ",
                "in_progress": ">>",
                "completed": "OK",
                "failed": "XX",
                "skipped": "--",
            }.get(step["status"], "??")
            desc = step.get("description", step["id"])
            result_hint = ""
            if step["status"] == "completed" and step.get("result"):
                result_hint = f" => {step['result'][:300]}"
            elif step["status"] == "failed" and step.get("result"):
                result_hint = f" => FAIL: {step['result'][:300]}"
            lines.append(f"  [{icon}] {num}. {desc}{result_hint}")

        lines.append("")
        lines.append(
            "IMPORTANT: This plan already exists. Do NOT call create_plan again. "
            "Continue from the current step using update_plan_step."
        )

        return "\n".join(lines)

    def _save_plan_markdown(self, plan: dict | None = None) -> None:
        """保存计划到 Markdown 文件（可传入显式 plan 引用避免依赖 agent state）"""
        if plan is None:
            plan = self._get_current_plan()
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

        for step in plan["steps"]:
            status_emoji = {
                "pending": "⬜",
                "in_progress": "🔄",
                "completed": "✅",
                "failed": "❌",
                "skipped": "⏭️",
            }.get(step["status"], "❓")

            tool = step.get("tool", "-")
            skills = ", ".join(step.get("skills", []) or [])
            result = step.get("result", "-")

            content += (
                f"| {step['id']} | {step['description']} | {skills or '-'} | {tool} | {status_emoji} | {result} |\n"
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
            plan = self._get_current_plan()
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


def create_plan_handler(agent: "Agent"):
    """创建 Plan Handler 处理函数"""
    handler = PlanHandler(agent)
    return handler.handle

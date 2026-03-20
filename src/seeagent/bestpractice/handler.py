"""
BPToolHandler — BP 工具路由。

7 个工具:
- bp_start: 启动 BP
- bp_continue: 继续下一个子任务
- bp_edit_output: 修改子任务输出 (Chat-to-Edit)
- bp_switch_task: 切换活跃 BP 实例
- bp_get_output: 获取子任务完整输出
- bp_cancel: 取消 BP 实例
- bp_supplement_input: 补充缺失的输入数据
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from .models import PendingContextSwitch, RunMode

if TYPE_CHECKING:
    from .context_bridge import ContextBridge
    from .engine import BPEngine
    from .models import BestPracticeConfig
    from .state_manager import BPStateManager

logger = logging.getLogger(__name__)

BP_TOOLS = [
    "bp_start", "bp_continue", "bp_edit_output", "bp_switch_task",
    "bp_get_output", "bp_cancel", "bp_supplement_input",
]


class BPToolHandler:
    """Routes bp_* tool calls to BPEngine/BPStateManager."""

    TOOLS = BP_TOOLS

    def __init__(
        self,
        engine: BPEngine,
        state_manager: BPStateManager,
        context_bridge: ContextBridge,
        config_registry: dict[str, BestPracticeConfig],
    ) -> None:
        self.engine = engine
        self.state_manager = state_manager
        self.context_bridge = context_bridge
        self.config_registry = config_registry

    async def handle(self, tool_name: str, params: dict[str, Any], agent: Any) -> str:
        session = getattr(agent, "_current_session", None)
        if not session:
            return "❌ 无活跃会话"

        dispatch = {
            "bp_start": self._handle_start,
            "bp_continue": self._handle_continue,
            "bp_edit_output": self._handle_edit_output,
            "bp_switch_task": self._handle_switch_task,
            "bp_get_output": self._handle_get_output,
            "bp_cancel": self._handle_cancel,
            "bp_supplement_input": self._handle_supplement_input,
        }

        handler = dispatch.get(tool_name)
        if not handler:
            return f"❌ Unknown BP tool: {tool_name}"

        return await handler(params, agent, session)

    # ── bp_start ───────────────────────────────────────────────

    async def _handle_start(self, params: dict, agent: Any, session: Any) -> str:
        bp_id = (params.get("bp_id") or "").strip()
        if not bp_id:
            return "❌ bp_id is required"

        bp_config = self.config_registry.get(bp_id)
        if not bp_config:
            available = ", ".join(self.config_registry.keys())
            return f"❌ Best Practice '{bp_id}' 不存在。可用: {available}"

        # Prevent duplicate: check for existing active instance
        existing = self.state_manager.get_active(session.id)
        if existing:
            if existing.bp_id == bp_id:
                # Same BP already active — guide LLM to continue
                return (
                    f"✅ 「{bp_config.name}」已在运行中 (instance={existing.instance_id})。"
                    f"请直接使用 bp_continue(instance_id=\"{existing.instance_id}\") 继续执行，"
                    f"无需重复启动。"
                )
            else:
                # Different BP — suspend old, proceed to create new
                old_name = existing.bp_config.name if existing.bp_config else existing.bp_id
                self.state_manager.suspend(existing.instance_id)
                logger.info(f"[BP] Suspended existing instance {existing.instance_id} "
                            f"({old_name}) to start {bp_id}")

        input_data = params.get("input_data", {})
        run_mode_str = params.get("run_mode", bp_config.default_run_mode.value)
        run_mode = RunMode(run_mode_str) if run_mode_str in ("manual", "auto") else RunMode.MANUAL

        logger.info(f"[BP-DEBUG] bp_start: bp_id={bp_id}, session_id={session.id}, "
                     f"run_mode={run_mode.value}")
        inst_id = self.state_manager.create_instance(
            bp_config, session.id, initial_input=input_data, run_mode=run_mode,
        )
        logger.info(f"[BP-DEBUG] bp_start: created instance {inst_id}")

        orchestrator = self._get_orchestrator(agent)
        if not orchestrator:
            return "❌ Orchestrator not available"

        result = await self.engine.execute_subtask(inst_id, bp_config, orchestrator, session)
        # 验证 advance 后的状态
        snap_after = self.state_manager.get(inst_id)
        if snap_after:
            logger.info(f"[BP-DEBUG] bp_start DONE: instance={inst_id}, "
                         f"idx_after={snap_after.current_subtask_index}, "
                         f"status={snap_after.status.value}")
        return result

    # ── bp_continue ────────────────────────────────────────────

    async def _handle_continue(self, params: dict, agent: Any, session: Any) -> str:
        instance_id = self._resolve_instance_id(params, session)
        logger.info(f"[BP-DEBUG] bp_continue called, instance_id={instance_id}, params={params}, "
                     f"session_id={getattr(session, 'id', '?')}")
        if not instance_id:
            # 诊断: 列出所有实例
            all_instances = list(self.state_manager._instances.keys())
            logger.warning(f"[BP-DEBUG] bp_continue: NO active instance found! "
                           f"All instances in memory: {all_instances}")
            for iid, snap in self.state_manager._instances.items():
                logger.warning(f"[BP-DEBUG]   {iid}: session_id={snap.session_id}, "
                               f"status={snap.status.value}, idx={snap.current_subtask_index}")
            return "❌ 没有活跃的 BP 实例，请指定 instance_id"

        snap = self.state_manager.get(instance_id)
        if not snap:
            logger.warning(f"[BP-DEBUG] bp_continue: instance {instance_id} not found")
            return f"❌ BP instance {instance_id} 不存在"

        bp_config = self._get_config_for_instance(snap)
        if not bp_config:
            logger.warning(f"[BP-DEBUG] bp_continue: config {snap.bp_id} not found")
            return f"❌ BP config {snap.bp_id} 不存在"

        logger.info(f"[BP-DEBUG] bp_continue: executing subtask idx={snap.current_subtask_index}, "
                     f"total={len(bp_config.subtasks)}, status={snap.status.value}, "
                     f"subtask_statuses={snap.subtask_statuses}")

        # 重置 stale 子任务
        self.engine.reset_stale_if_needed(instance_id, bp_config)

        orchestrator = self._get_orchestrator(agent)
        if not orchestrator:
            logger.warning("[BP-DEBUG] bp_continue: orchestrator not available")
            return "❌ Orchestrator not available"

        result = await self.engine.execute_subtask(instance_id, bp_config, orchestrator, session)
        logger.info(f"[BP-DEBUG] bp_continue: execute_subtask returned, result length={len(result)}")
        return result

    # ── bp_edit_output ─────────────────────────────────────────

    async def _handle_edit_output(self, params: dict, agent: Any, session: Any) -> str:
        instance_id = self._resolve_instance_id(params, session)
        if not instance_id:
            return "❌ 请指定 instance_id"

        subtask_id = (params.get("subtask_id") or "").strip()
        if not subtask_id:
            return "❌ subtask_id is required"

        changes = params.get("changes", {})
        if not changes:
            return "❌ changes is required"

        snap = self.state_manager.get(instance_id)
        if not snap:
            return f"❌ BP instance {instance_id} 不存在"

        bp_config = self._get_config_for_instance(snap)
        if not bp_config:
            return f"❌ BP config {snap.bp_id} 不存在"

        result = self.engine.handle_edit_output(instance_id, subtask_id, changes, bp_config)

        if result.get("success") and result.get("stale_subtasks"):
            await self.engine._emit_stale(
                instance_id,
                result["stale_subtasks"],
                f"子任务 {subtask_id} 输出被编辑",
                session,
            )

        if not result.get("success"):
            return f"❌ {result.get('error', 'Unknown error')}"

        stale = result.get("stale_subtasks", [])
        merged_preview = json.dumps(result["merged"], ensure_ascii=False)[:300]
        msg = f"✅ 子任务输出已合并更新。\n预览: {merged_preview}"
        if stale:
            msg += f"\n\n⚠️ 以下下游子任务已标记为 stale，需要重新执行: {stale}"
        if result.get("warning"):
            msg += f"\n⚠️ {result['warning']}"
        return msg

    # ── bp_switch_task ─────────────────────────────────────────

    async def _handle_switch_task(self, params: dict, agent: Any, session: Any) -> str:
        target_id = (params.get("target_instance_id") or "").strip()
        if not target_id:
            return "❌ target_instance_id is required"

        target = self.state_manager.get(target_id)
        if not target:
            return f"❌ BP instance {target_id} 不存在"

        current_active = self.state_manager.get_active(session.id)
        current_id = current_active.instance_id if current_active else ""

        if current_id == target_id:
            return f"ℹ️ {target_id} 已经是当前活跃任务"

        if current_id:
            self.state_manager.suspend(current_id)
        self.state_manager.resume(target_id)

        # C-3: PendingContextSwitch
        self.state_manager.set_pending_switch(
            session.id,
            PendingContextSwitch(
                suspended_instance_id=current_id,
                target_instance_id=target_id,
            ),
        )

        bp_config = self._get_config_for_instance(target)
        bp_name = bp_config.name if bp_config else target.bp_id

        return (
            f"已切换到任务「{bp_name}」(id={target_id})。\n"
            f"上下文将在下一轮对话中恢复。"
        )

    # ── bp_get_output ──────────────────────────────────────────

    async def _handle_get_output(self, params: dict, agent: Any, session: Any) -> str:
        instance_id = self._resolve_instance_id(params, session)
        if not instance_id:
            return "❌ 请指定 instance_id"

        subtask_id = (params.get("subtask_id") or "").strip()
        if not subtask_id:
            return "❌ subtask_id is required"

        snap = self.state_manager.get(instance_id)
        if not snap:
            return f"❌ BP instance {instance_id} 不存在"

        output = snap.subtask_outputs.get(subtask_id)
        if output is None:
            return f"❌ 子任务 '{subtask_id}' 暂无输出"

        return json.dumps(output, ensure_ascii=False, indent=2)

    # ── bp_cancel ──────────────────────────────────────────────

    async def _handle_cancel(self, params: dict, agent: Any, session: Any) -> str:
        instance_id = self._resolve_instance_id(params, session)
        if not instance_id:
            return "❌ 请指定 instance_id"

        snap = self.state_manager.get(instance_id)
        if not snap:
            return f"❌ BP instance {instance_id} 不存在"

        bp_config = self._get_config_for_instance(snap)
        bp_name = bp_config.name if bp_config else snap.bp_id

        self.state_manager.cancel(instance_id)
        return f"✅ 已取消任务「{bp_name}」(id={instance_id})"

    # ── bp_supplement_input ────────────────────────────────────

    async def _handle_supplement_input(self, params: dict, agent: Any, session: Any) -> str:
        instance_id = (params.get("instance_id") or "").strip()
        if not instance_id:
            return "❌ instance_id is required"

        subtask_id = (params.get("subtask_id") or "").strip()
        if not subtask_id:
            return "❌ subtask_id is required"

        data = params.get("data", {})
        if not data:
            return "❌ data is required (补充的字段数据)"

        result = self.engine.supplement_input(instance_id, subtask_id, data)

        if not result.get("success"):
            return f"❌ {result.get('error', 'Unknown error')}"

        merged_preview = json.dumps(result["merged"], ensure_ascii=False)[:300]
        return (
            f"✅ 输入数据已补充。\n"
            f"合并后数据预览: {merged_preview}\n\n"
            f"请调用 bp_continue(instance_id=\"{instance_id}\") 继续执行。"
        )

    # ── Helpers ────────────────────────────────────────────────

    def _resolve_instance_id(self, params: dict, session: Any) -> str | None:
        instance_id = (params.get("instance_id") or "").strip()
        if instance_id:
            return instance_id
        active = self.state_manager.get_active(session.id)
        return active.instance_id if active else None

    def _get_config_for_instance(self, snap: Any) -> Any:
        """获取实例对应的 BP 配置。优先用 snap.bp_config，fallback config_registry。"""
        if snap.bp_config:
            return snap.bp_config
        return self.config_registry.get(snap.bp_id)

    @staticmethod
    def _get_orchestrator(agent: Any) -> Any:
        """获取 AgentOrchestrator 实例。"""
        orch = getattr(agent, "_orchestrator", None)
        if orch:
            return orch
        try:
            import seeagent.main
            return getattr(seeagent.main, "_orchestrator", None)
        except ImportError:
            return None

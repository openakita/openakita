"""
BPEngine — BP 子任务执行引擎。

核心设计:
- execute_subtask() 每次只执行一个子任务，不递归 (C1)
- auto 模式连续执行由 MasterAgent ReAct 循环驱动
- 首个子任务输入从 initial_input 获取 (M8)
- 执行前检查 input_schema required 字段完整性
- SubAgent 上下文隔离: session_messages=[] (C-1)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from string import Template
from typing import TYPE_CHECKING, Any

from .models import BPStatus, RunMode, SubtaskStatus

if TYPE_CHECKING:
    from .models import BestPracticeConfig, SubtaskConfig
    from .schema_chain import SchemaChain
    from .state_manager import BPStateManager

logger = logging.getLogger(__name__)

DEFAULT_BP_SUBTASK_TIMEOUT = 600  # seconds


class BPEngine:
    def __init__(
        self,
        state_manager: BPStateManager,
        schema_chain: SchemaChain,
    ) -> None:
        self.state_manager = state_manager
        self.schema_chain = schema_chain
        self._orchestrator = None

    # ── Orchestrator injection ────────────────────────────────

    def set_orchestrator(self, orchestrator) -> None:
        """由 facade 或 server.py 在启动时注入。"""
        self._orchestrator = orchestrator

    def _get_orchestrator(self):
        """获取 orchestrator，fallback 到全局实例。"""
        if self._orchestrator:
            return self._orchestrator
        try:
            import seeagent.main
            return getattr(seeagent.main, "_orchestrator", None)
        except ImportError:
            return None

    def _get_scheduler(self, bp_config, snap):
        """工厂方法: 根据 config 返回合适的 scheduler。"""
        from .scheduler import LinearScheduler
        return LinearScheduler(bp_config, snap)

    def _get_config(self, snap):
        """获取实例对应的 BP 配置。"""
        if snap.bp_config:
            return snap.bp_config
        try:
            from .facade import get_bp_config_loader
            loader = get_bp_config_loader()
            if loader and loader.configs:
                return loader.configs.get(snap.bp_id)
            return None
        except Exception:
            return None

    # ── Core execution ─────────────────────────────────────────

    async def execute_subtask(
        self,
        instance_id: str,
        bp_config: BestPracticeConfig,
        orchestrator: Any,
        session: Any,
    ) -> str:
        """执行当前子任务。每次只执行一个子任务 (C1)。"""
        snap = self.state_manager.get(instance_id)
        if not snap:
            return f"❌ BP instance {instance_id} 不存在"

        idx = snap.current_subtask_index
        logger.info(f"[BP-DEBUG] execute_subtask: instance={instance_id}, idx={idx}, "
                     f"total_subtasks={len(bp_config.subtasks)}")
        if idx >= len(bp_config.subtasks):
            logger.info("[BP-DEBUG] execute_subtask: all subtasks already completed")
            return "❌ 所有子任务已完成"

        subtask = bp_config.subtasks[idx]

        # 1. 解析输入
        input_data = self._resolve_input(snap, bp_config, idx)

        # 2. 检查输入完整性 — 必要字段缺失时暂停
        missing = self._check_input_completeness(subtask, input_data)
        if missing:
            return self._format_input_incomplete_result(
                snap, subtask, input_data, missing,
            )

        # 3. 推导输出 schema
        output_schema = self.schema_chain.derive_output_schema(bp_config, idx)

        # 4. 构建委派消息
        message = self._build_delegation_message(
            bp_config, subtask, input_data, output_schema,
        )

        # 5. 更新状态 → CURRENT
        self.state_manager.update_subtask_status(instance_id, subtask.id, SubtaskStatus.CURRENT)

        # 6. 发射进度事件
        await self._emit_progress(instance_id, session)

        # 7. 委派执行 (C-1: session_messages=[] 上下文隔离)
        delegate_step_id = f"bp-delegate-{instance_id}-{subtask.id}"
        await self._emit_delegate_card(delegate_step_id, subtask, session, status="running")
        delegate_t0 = time.monotonic()
        try:
            timeout = subtask.timeout_seconds or DEFAULT_BP_SUBTASK_TIMEOUT
            result = await asyncio.wait_for(
                orchestrator.delegate(
                    session=session,
                    from_agent="main",
                    to_agent=subtask.agent_profile,
                    message=message,
                    reason=f"BP:{bp_config.name} / {subtask.name}",
                    session_messages=[],  # C-1: 上下文隔离
                ),
                timeout=timeout,
            )
            delegate_duration = time.monotonic() - delegate_t0
            await self._emit_delegate_card(
                delegate_step_id, subtask, session,
                status="completed", duration=delegate_duration,
            )
        except asyncio.TimeoutError:
            delegate_duration = time.monotonic() - delegate_t0
            await self._emit_delegate_card(
                delegate_step_id, subtask, session,
                status="failed", duration=delegate_duration,
            )
            logger.error(f"SubTask timeout: {subtask.id} after {timeout}s")
            self.state_manager.update_subtask_status(
                instance_id, subtask.id, SubtaskStatus.FAILED,
            )
            return (
                f"⏱️ 子任务「{subtask.name}」执行超时 ({timeout}s)。\n"
                f"可通过 bp_continue 重试。"
            )
        except Exception as e:
            delegate_duration = time.monotonic() - delegate_t0
            await self._emit_delegate_card(
                delegate_step_id, subtask, session,
                status="failed", duration=delegate_duration,
            )
            logger.error(f"SubTask delegation failed: {subtask.id} - {e}")
            self.state_manager.update_subtask_status(
                instance_id, subtask.id, SubtaskStatus.PENDING,
            )
            return (
                f"❌ 子任务「{subtask.name}」执行失败: {e}\n"
                f"子任务已重置为 PENDING，可通过 bp_continue 重试。"
            )

        # 8. 解析输出 & 存储
        output = self._parse_output(result)
        summary = self._extract_summary_from_result(result, output)
        self.state_manager.update_subtask_output(instance_id, subtask.id, output)
        self.state_manager.update_subtask_status(instance_id, subtask.id, SubtaskStatus.DONE)
        logger.info(f"[BP-DEBUG] step8: output parsed, subtask={subtask.id} marked DONE")

        # 9. 发射子任务完成事件
        await self._emit_subtask_output(
            instance_id, subtask.id, output, session,
            bp_config=bp_config, summary=summary,
        )
        logger.info(f"[BP-DEBUG] step9: emitted bp_subtask_output event")

        # 10. 判断是否为最后一个子任务
        if idx >= len(bp_config.subtasks) - 1:
            logger.info(f"[BP-DEBUG] step10: LAST subtask (idx={idx}), completing instance")
            self.state_manager.complete(instance_id)
            self._persist(instance_id, session)
            return self._format_completion_result(snap, bp_config)

        # 11. 推进到下一个子任务 (必须在 persist 之前)
        logger.info(f"[BP-DEBUG] step11: BEFORE advance_subtask, idx={snap.current_subtask_index}")
        self.state_manager.advance_subtask(instance_id)
        logger.info(f"[BP-DEBUG] step11: AFTER advance_subtask, idx={snap.current_subtask_index}")

        # 12. 持久化到 Session.metadata (advance 之后，确保 idx 正确)
        self._persist(instance_id, session)
        logger.info(f"[BP-DEBUG] step12: persisted, idx={snap.current_subtask_index}")

        # NOTE: advance_subtask 已将 idx 推进，current_subtask_index 就是下一个要执行的子任务
        result_msg = self._format_subtask_complete_result(snap, bp_config, subtask, output, instance_id)
        logger.info(f"[BP-DEBUG] execute_subtask done, returning to MasterAgent: {result_msg[:200]}")
        return result_msg

    def _build_delegation_message(
        self,
        bp_config: BestPracticeConfig,
        subtask: SubtaskConfig,
        input_data: dict[str, Any],
        output_schema: dict[str, Any] | None,
    ) -> str:
        schema_hint = (
            json.dumps(output_schema, ensure_ascii=False, indent=2)
            if output_schema
            else "由你自行决定合适的输出格式"
        )
        return (
            f"## 最佳实践任务: {bp_config.name}\n"
            f"### 当前子任务: {subtask.name}\n"
            f"{subtask.description or ''}\n\n"
            f"### 输入数据\n```json\n"
            f"{json.dumps(input_data, ensure_ascii=False, indent=2)}\n```\n\n"
            f"### 输出格式要求\n\n"
            f"请严格按以下格式输出（先写总结再写 JSON）:\n\n"
            f"**总结**: [用1-2句话简洁描述本子任务的执行结果和关键发现]\n\n"
            f"```json\n{schema_hint}\n```\n\n"
            f"## 限制\n"
            f"- 禁止使用 ask_user 工具，所有信息已在输入数据中提供\n"
            f"- JSON 必须严格符合输出格式要求\n"
            f"- **总结**行必须在 JSON 代码块之前"
        )

    # ── Chat-to-Edit ───────────────────────────────────────────

    def handle_edit_output(
        self,
        instance_id: str,
        subtask_id: str,
        changes: dict[str, Any],
        bp_config: BestPracticeConfig,
    ) -> dict[str, Any]:
        """编辑已完成子任务的输出，触发下游 STALE 标记。"""
        snap = self.state_manager.get(instance_id)
        if not snap:
            return {"success": False, "error": f"instance {instance_id} 不存在"}

        if subtask_id not in snap.subtask_outputs:
            return {"success": False, "error": f"子任务 {subtask_id} 无输出可编辑"}

        # 深度合并
        merged = self.state_manager.merge_subtask_output(instance_id, subtask_id, changes)

        # 标记下游为 STALE
        stale = self.state_manager.mark_downstream_stale(instance_id, subtask_id, bp_config)

        # 软校验
        warning = self._validate_output_soft(merged, subtask_id, bp_config)

        result: dict[str, Any] = {
            "success": True,
            "merged": merged,
            "stale_subtasks": stale,
        }
        if warning:
            result["warning"] = warning
        return result

    def reset_stale_if_needed(
        self, instance_id: str, bp_config: BestPracticeConfig,
    ) -> list[str]:
        """重置当前子任务及后续 STALE 子任务为 PENDING。返回被重置的 ID 列表。"""
        snap = self.state_manager.get(instance_id)
        if not snap:
            return []

        reset_ids: list[str] = []
        idx = snap.current_subtask_index
        for i in range(idx, len(bp_config.subtasks)):
            st = bp_config.subtasks[i]
            status = snap.subtask_statuses.get(st.id, "")
            if status == SubtaskStatus.STALE.value:
                self.state_manager.update_subtask_status(instance_id, st.id, SubtaskStatus.PENDING)
                reset_ids.append(st.id)
        return reset_ids

    # ── Supplement input ───────────────────────────────────────

    def supplement_input(
        self,
        instance_id: str,
        subtask_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """补充子任务输入（合并到上游输出或 initial_input）。"""
        snap = self.state_manager.get(instance_id)
        if not snap:
            return {"success": False, "error": f"instance {instance_id} 不存在"}

        bp_config = snap.bp_config
        if not bp_config:
            return {"success": False, "error": "bp_config not loaded"}

        # 找到目标 subtask 的 index
        subtask_index = None
        for i, st in enumerate(bp_config.subtasks):
            if st.id == subtask_id:
                subtask_index = i
                break
        if subtask_index is None:
            return {"success": False, "error": f"subtask {subtask_id} 不存在"}

        # 合并到对应数据源
        if subtask_index == 0:
            snap.initial_input.update(data)
            merged = dict(snap.initial_input)
        else:
            prev_id = bp_config.subtasks[subtask_index - 1].id
            prev_output = snap.subtask_outputs.get(prev_id, {})
            prev_output.update(data)
            snap.subtask_outputs[prev_id] = prev_output
            merged = prev_output

        return {"success": True, "merged": merged}

    # ── Input resolution ───────────────────────────────────────

    def _resolve_input(
        self, snap: Any, bp_config: BestPracticeConfig, subtask_index: int,
    ) -> dict[str, Any]:
        """M8: 第一个子任务用 initial_input，后续用上一个子任务的输出。"""
        if subtask_index == 0:
            return dict(snap.initial_input)

        # 优先 input_mapping
        subtask = bp_config.subtasks[subtask_index]
        if subtask.input_mapping:
            resolved: dict[str, Any] = {}
            for field, upstream_id in subtask.input_mapping.items():
                upstream_output = snap.subtask_outputs.get(upstream_id, {})
                resolved[field] = upstream_output
            return resolved

        prev_subtask = bp_config.subtasks[subtask_index - 1]
        return dict(snap.subtask_outputs.get(prev_subtask.id, {}))

    def _check_input_completeness(
        self, subtask: SubtaskConfig, input_data: dict[str, Any],
    ) -> list[str]:
        """检查 input_schema.required 字段是否都在 input_data 中。返回缺失字段列表。"""
        schema = subtask.input_schema
        if not schema:
            return []
        required = schema.get("required", [])
        return [field for field in required if field not in input_data]

    # ── Formatting ─────────────────────────────────────────────

    def _format_input_incomplete_result(
        self,
        snap: Any,
        subtask: SubtaskConfig,
        input_data: dict[str, Any],
        missing_fields: list[str],
    ) -> str:
        """输入不完整时的返回消息。引导 MasterAgent 向用户收集缺失字段。"""
        properties = subtask.input_schema.get("properties", {})
        field_hints = []
        for f in missing_fields:
            desc = properties.get(f, {}).get("description", "")
            ftype = properties.get(f, {}).get("type", "string")
            hint = f"  - **{f}** ({ftype})"
            if desc:
                hint += f": {desc}"
            field_hints.append(hint)

        msg = (
            f"⚠️ 子任务「{subtask.name}」的输入数据不完整。\n"
            f"缺少以下必要字段:\n" + "\n".join(field_hints) + "\n\n"
            f"【处理要求】\n"
            f"1. 请首先检查用户的历史消息（包括当前轮次），如果用户其实已经提供了上述缺失的信息，请直接调用 bp_supplement_input(instance_id=\"{snap.instance_id}\", subtask_id=\"{subtask.id}\", data={{...}}) 补充数据，然后调用 bp_continue 继续。\n"
            f"2. 只有当用户确实没有提供这些信息时，才使用 ask_user 向用户收集。\n"
        )

        if snap.run_mode == RunMode.AUTO:
            msg += "\n\n⚠️ 自动模式已暂停，等待用户补充输入。"

        return msg

    def _format_subtask_complete_result(
        self, snap: Any, bp_config: BestPracticeConfig,
        subtask: SubtaskConfig, output: dict, instance_id: str,
    ) -> str:
        output_preview = json.dumps(output, ensure_ascii=False)[:200]
        # advance_subtask 已被调用，current_subtask_index 就是下一个子任务的索引
        next_idx = snap.current_subtask_index
        next_name = bp_config.subtasks[next_idx].name if next_idx < len(bp_config.subtasks) else "(无)"

        if snap.run_mode == RunMode.AUTO:
            return (
                f"子任务「{subtask.name}」已完成。输出预览:\n{output_preview}\n\n"
                f"当前为自动模式，请立即调用 bp_continue("
                f"instance_id=\"{instance_id}\") 执行下一个子任务「{next_name}」。"
            )
        return (
            f"子任务「{subtask.name}」已完成。\n"
            f"输出预览:\n{output_preview}\n\n"
            f"下一步是「{next_name}」。\n"
            f"界面已展示操作按钮，等待用户操作。\n"
            f"禁止使用 ask_user，用户将通过界面按钮操作。\n"
            f"当用户发送「进入下一步」时，请立即调用 bp_continue("
            f"instance_id=\"{instance_id}\") 执行下一个子任务「{next_name}」。"
        )

    def _format_completion_result(self, snap: Any, bp_config: BestPracticeConfig) -> str:
        return (
            f"🎉 最佳实践「{bp_config.name}」全部完成！\n"
            f"共完成 {len(bp_config.subtasks)} 个子任务。\n"
            f"请向用户展示最终结果摘要。"
        )

    # ── Output parsing ─────────────────────────────────────────

    @staticmethod
    def _parse_output(result: str) -> dict[str, Any]:
        """从委派结果中提取 JSON 输出。"""
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            pass
        match = re.search(r"```json\s*(.*?)\s*```", result, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return {"_raw_output": str(result)}

    def _validate_output_soft(
        self, output: dict, subtask_id: str, bp_config: BestPracticeConfig,
    ) -> str | None:
        """宽松校验输出。返回警告文本或 None。"""
        # 找到 subtask 的 index 以获取对应 output_schema
        for i, st in enumerate(bp_config.subtasks):
            if st.id == subtask_id:
                schema = self.schema_chain.derive_output_schema(bp_config, i)
                if schema and "required" in schema:
                    missing = [f for f in schema["required"] if f not in output]
                    if missing:
                        return f"输出缺少字段: {missing}"
                return None
        return None

    # ── Persistence ────────────────────────────────────────────

    def _persist(self, instance_id: str, session: Any) -> None:
        """持久化 BP 状态到 Session.metadata["bp_state"]。"""
        snap = self.state_manager.get(instance_id)
        if not snap:
            return
        try:
            data = self.state_manager.serialize_for_session(snap.session_id)
            if hasattr(session, "metadata"):
                session.metadata["bp_state"] = data
        except Exception as e:
            logger.warning(f"[BP] Persist failed: {e}")

    # ── SSE Events ─────────────────────────────────────────────

    async def _emit_progress(self, instance_id: str, session: Any) -> None:
        bus = getattr(getattr(session, "context", None), "_sse_event_bus", None)
        if not bus:
            return
        try:
            snap = self.state_manager.get(instance_id)
            if snap:
                bp_name = snap.bp_config.name if snap.bp_config else snap.bp_id
                await bus.put({
                    "type": "bp_progress",
                    "data": {
                        "instance_id": instance_id,
                        "bp_name": bp_name,
                        "statuses": dict(snap.subtask_statuses),
                        "subtasks": [
                            {"id": st.id, "name": st.name}
                            for st in snap.bp_config.subtasks
                        ] if snap.bp_config else [],
                        "current_subtask_index": snap.current_subtask_index,
                        "run_mode": snap.run_mode.value,
                        "status": snap.status.value,
                    },
                })
        except Exception:
            pass

    async def _emit_subtask_output(
        self, instance_id: str, subtask_id: str, output: dict, session: Any,
        *, bp_config: BestPracticeConfig | None = None, summary: str | None = None,
    ) -> None:
        bus = getattr(getattr(session, "context", None), "_sse_event_bus", None)
        if not bus:
            return
        try:
            snap = self.state_manager.get(instance_id)
            subtask_name = subtask_id
            output_schema: dict | None = None
            cfg = bp_config or (snap.bp_config if snap else None)
            if cfg:
                for i, st in enumerate(cfg.subtasks):
                    if st.id == subtask_id:
                        subtask_name = st.name
                        if i + 1 < len(cfg.subtasks):
                            output_schema = cfg.subtasks[i + 1].input_schema
                        break

            await bus.put({
                "type": "bp_subtask_output",
                "data": {
                    "instance_id": instance_id,
                    "subtask_id": subtask_id,
                    "subtask_name": subtask_name,
                    "output": output,
                    "output_schema": output_schema,
                    "summary": summary or self._build_summary(output),
                },
            })
        except Exception:
            pass

    @staticmethod
    def _extract_summary_from_result(raw_result: str, output: dict) -> str | None:
        """从 SubAgent 返回文本中提取 **总结** 行作为摘要。"""
        # 匹配 **总结**: ... 或 **总结**： ...
        match = re.search(r"\*\*总结\*\*[：:]\s*(.+?)(?:\n|$)", raw_result)
        if match:
            summary = match.group(1).strip()
            if summary:
                return summary[:300]
        # 尝试提取 JSON 代码块前的说明文本
        json_match = re.search(r"```json", raw_result)
        if json_match:
            text_before = raw_result[:json_match.start()].strip()
            if text_before:
                lines = [
                    line.strip() for line in text_before.split("\n")
                    if line.strip() and not line.startswith("#")
                ]
                if lines:
                    return " ".join(lines)[:300]
        return None

    @staticmethod
    def _build_summary(output: dict) -> str:
        """构建输出摘要：key 列表 + 前 200 字符预览。"""
        if not output:
            return ""
        keys = list(output.keys())
        preview = json.dumps(output, ensure_ascii=False)[:200]
        return f"字段: {', '.join(keys)} | {preview}"

    async def _emit_delegate_card(
        self, step_id: str, subtask: SubtaskConfig, session: Any,
        status: str = "running", duration: float | None = None,
    ) -> None:
        """Emit a step_card for the delegation action itself (parent-level card)."""
        bus = getattr(getattr(session, "context", None), "_sse_event_bus", None)
        if not bus:
            return
        try:
            await bus.put({
                "type": "step_card",
                "step_id": step_id,
                "title": f"委派 {subtask.agent_profile}: {subtask.name}子任务",
                "status": status,
                "source_type": "tool",
                "card_type": "delegate",
                "agent_id": "main",
                "duration": duration,
                "plan_step_index": None,
                "input": None,
                "output": None,
                "absorbed_calls": [],
            })
        except Exception:
            pass

    async def _emit_stale(
        self, instance_id: str, stale_ids: list[str], reason: str, session: Any,
    ) -> None:
        bus = getattr(getattr(session, "context", None), "_sse_event_bus", None)
        if not bus:
            return
        try:
            await bus.put({
                "type": "bp_stale",
                "data": {
                    "instance_id": instance_id,
                    "stale_subtask_ids": stale_ids,
                    "reason": reason,
                },
            })
        except Exception:
            pass

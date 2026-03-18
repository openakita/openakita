# 最佳实践任务管理系统 — 设计 Review 改进方案

> 基于需求文档、技术设计、存储设计的全面 Review
> Review 日期: 2026-03-18
> 改进方案覆盖 24 个问题（3 Critical + 6 Major + 6 Medium + 4 Consistency + 5 Missing Scenario + 5 Minor）

---

## 修订索引

| 编号 | 严重度 | 影响模块 | 简述 | 状态 |
|------|--------|---------|------|------|
| C1 | Critical | engine.py | Auto 模式从递归改为迭代式 | 需修改技术设计 §6.2, §15 |
| C2 | Critical | engine.py, context_bridge.py, agent.py | 上下文切换改为安全钩子点 | 需修改技术设计 §6.4, §7.3 |
| C3 | Critical | state_manager.py | V1 实现 Session.metadata 持久化 | 需修改技术设计 §4.1, 存储设计 §3.1, §5.2 |
| M1 | Major | engine.py | Agent 实例复用安全性分析与处理 | 需修改技术设计 §6.2 |
| M2 | Major | state_manager.py, prompts/ | Deep Merge 数组语义明确化 | 需修改技术设计 §6.3 |
| M3 | Major | prompts/, engine.py | intent_router 合并到 system_dynamic | 需修改技术设计 §5.3 |
| M4 | Major | trigger.py | 删除 BPTriggerDetector，纯 LLM 触发 | 需修改技术设计 §13 |
| M5 | Major | bestpractice.py (handler) | instance_id 可选化 + 可发现性 | 需修改技术设计 §10 |
| M6 | Major | state_manager.py | 删除 master_messages_snapshot 字段 | 需修改技术设计 §3.2, 存储设计 §3.3 |
| M7 | Medium | api/routes/seecrab.py | SSE 重连全量状态同步 API | 需修改技术设计 §9, 存储设计 §5.2 |
| M8 | Medium | state_manager.py, engine.py | 消除 __initial_input__ 魔法键 | 需修改技术设计 §3.2, §6.2 |
| M9 | Medium | config.py, engine.py | 子任务超时与重试策略 | 需修改技术设计 §3.1, §6.2 |
| M10 | Medium | bestpractice.py (handler) | Edit 校验结果传达 | 需修改技术设计 §10.2 |
| M11 | Medium | config.py | Schema 深度限制与降级 | 新增 |
| M12 | Medium | config.py | BP 配置加载时验证 | 需修改技术设计 §3.3 |
| M13 | Medium | state_manager.py | inferCooldown 持久化 | 需修改技术设计 §13 |
| D1 | Consistency | 需求文档 §1.2 | TriggerType 扩展方式统一 | 需修改需求文档 |
| D2 | Consistency | 需求文档 §1.1 | BPStateManager 接口返回值对齐 | 需修改需求文档 |
| D3 | Consistency | 存储设计 §3.3 | BPInstanceSnapshot 字段一致性 | 需修改存储设计 |
| S1 | Scenario | engine.py | Auto 模式中用户中断处理 | 需修改技术设计 §6.2 |
| S2 | Scenario | prompts/ | 同一 BP 配置多实例区分 | 需修改技术设计 §5.3 |
| S3 | Scenario | 需求文档 | 子任务中 ask_user 路由 | 需修改技术设计 §7.4 |
| S4 | Scenario | engine.py, prompts/ | Chat-to-Edit 意图确认 | 需修改技术设计 §6.3 |
| S5 | Scenario | engine.py | 非 SeeCrab 通道降级 | 需修改技术设计 §9 |
| m1 | Minor | context_bridge.py | 压缩失败降级方案 | 需修改技术设计 §7.3 |
| m2 | Minor | bestpractice.py (handler) | 消除私有成员访问 | 需修改技术设计 §10.2 |
| m3 | Minor | config.py | description 非空校验 | 需修改技术设计 §3.3 |
| m4 | Minor | schema_chain.py | 引入 jsonschema 验证 | 需修改技术设计 §11 |
| m5 | Minor | prompt_assembler.py | 多 cache breakpoint 支持 | 需修改技术设计 §5.2 |

---

## C1. Auto 模式从递归改为迭代式执行

### 问题

当前设计中 auto 模式通过 `execute_subtask()` 递归调用自身，整个 BP 执行在一次 `bp_start` 工具调用内完成。导致：
- MasterAgent ReAct 循环被长时间阻塞，无法响应 cancel/skip/user_insert
- 用户在 auto 模式下实质失去控制能力
- token 计费归属不清晰

### 改进方案

**核心思路**：`bp_start` 和 `bp_continue` 每次只执行**一个子任务**。auto 模式的连续执行由 MasterAgent 驱动——tool_result 中包含指令，引导 MasterAgent 自动调用下一个 `bp_continue`。

#### 修改 `engine.py` — `execute_subtask` 方法

```python
class BPEngine:
    async def execute_subtask(
        self,
        instance_id: str,
        orchestrator: "AgentOrchestrator",
        session: "Session",
    ) -> str:
        instance = self._state_manager.get(instance_id)
        config = instance.bp_config
        idx = instance.current_subtask_index
        subtask = config.subtasks[idx]

        # 准备输入
        input_data = self._resolve_input(instance, subtask)

        # 推导输出 Schema
        output_schema = self._schema_chain.derive_output_schema(config, idx)

        # 渲染委派消息
        message = self._prompt_loader.render(
            "subtask_instruction",
            bp_name=config.name,
            subtask_name=subtask.name,
            subtask_description=subtask.description or subtask.name,
            input_json=json.dumps(input_data, ensure_ascii=False, indent=2),
            output_schema=(
                json.dumps(output_schema, ensure_ascii=False, indent=2)
                if output_schema else "由你自行决定合适的输出格式"
            ),
        )

        # 更新状态 → CURRENT
        self._state_manager.update_subtask_status(
            instance_id, subtask.id, SubtaskStatus.CURRENT,
        )
        await self.emit_progress(instance_id, session)

        # 委派执行
        try:
            result = await orchestrator.delegate(
                session=session,
                from_agent="main",
                to_agent=subtask.agent_profile,
                message=message,
                reason=f"BP:{config.name} / {subtask.name}",
            )
        except Exception as e:
            logger.error(f"SubTask delegation failed: {subtask.id} - {e}")
            self._state_manager.update_subtask_status(
                instance_id, subtask.id, SubtaskStatus.PENDING,
            )
            return (
                f"子任务「{subtask.name}」执行失败: {e}\n"
                f"子任务已重置为 PENDING，可通过 bp_continue 重试。"
            )

        # 解析输出
        output = self._parse_output_json(result)
        self._state_manager.update_subtask_output(instance_id, subtask.id, output)
        self._state_manager.update_subtask_status(
            instance_id, subtask.id, SubtaskStatus.DONE,
        )

        # 发射事件
        await self._emit_bp_events(instance_id, subtask.id, output, session)

        # 持久化（C3 改进）
        await self._state_manager.persist(session)

        # ── 关键改动：不再递归，统一返回 ──
        if idx >= len(config.subtasks) - 1:
            # 最后一个子任务完成
            self._state_manager.complete(instance_id)
            await self._state_manager.persist(session)
            return self._format_completion_result(instance)

        # 推进到下一个子任务
        self._state_manager.advance_subtask(instance_id)

        # 根据 run_mode 返回不同的 tool_result
        # MasterAgent 收到后根据内容决定行为
        return self._format_subtask_complete_result(instance, subtask, output)

    def _format_subtask_complete_result(
        self,
        instance: BPInstanceSnapshot,
        subtask: SubtaskConfig,
        output: dict,
    ) -> str:
        """
        格式化子任务完成结果。
        手动和自动模式返回不同指令，引导 MasterAgent 做出正确行为。
        """
        next_subtask = instance.bp_config.subtasks[instance.current_subtask_index]
        output_preview = json.dumps(output, ensure_ascii=False)[:200]

        if instance.run_mode == RunMode.AUTO:
            # 自动模式：指令 MasterAgent 立即调用 bp_continue
            return (
                f"子任务「{subtask.name}」已完成。输出预览: {output_preview}\n"
                f"当前为自动模式，请立即调用 bp_continue("
                f"instance_id=\"{instance.instance_id}\") "
                f"执行下一个子任务「{next_subtask.name}」。"
            )
        else:
            # 手动模式：指令 MasterAgent 展示交互按钮
            return (
                f"子任务「{subtask.name}」已完成。输出预览: {output_preview}\n"
                f"下一个子任务: 「{next_subtask.name}」\n"
                f"请使用 ask_user 展示以下选项让用户选择：\n"
                f"- [查看结果]（纯 UI 操作，不生成消息）\n"
                f"- [进入下一步]（用户确认后调用 bp_continue）"
            )
```

#### 修改 `system_static.md` — 添加 auto 模式行为指令

```markdown
### 交互规则
...
6. **自动模式**：当 bp_start 或 bp_continue 的返回结果中包含
   "当前为自动模式，请立即调用 bp_continue" 时，
   你必须**立即**调用 bp_continue，不要等待用户操作。
   但如果用户在此期间发送了消息，优先处理用户消息。
```

#### 影响范围

| 文件 | 改动 |
|------|------|
| `engine.py` | 删除 auto 递归分支，统一返回 |
| `system_static.md` | 新增 auto 模式行为指令 |
| `bestpractice.py` (handler) | `_bp_start` 无需改动（已经只调用一次 `execute_subtask`） |
| 技术设计 §15 决策表 | "自动模式实现" 从 "B) 工具内递归" 改为 "A) MasterAgent 决定继续" |

---

## C2. 上下文切换改为安全钩子点

### 问题

`bp_switch_task` 在工具执行期间直接操作 Brain.Context.messages（清空+替换），此时 ReasoningEngine 正在使用该数据结构。存在：
- 当前 tool_use 的后续 tool_result 无法正确追加
- 压缩 LLM 调用在工具内部，失败会导致半修改状态

### 改进方案

**核心思路**：引入 `PendingContextSwitch` 数据结构。`bp_switch_task` 只设置 pending 标志并返回信息性结果。实际的上下文替换在 Agent 的下一轮推理准备阶段（`_prepare_session_context` 或等效入口）执行。

#### 新增数据结构

```python
# bestpractice/config.py
@dataclass
class PendingContextSwitch:
    """待执行的上下文切换操作，由 bp_switch_task 创建，由 Agent 推理准备阶段消费"""
    suspended_instance_id: str      # 要挂起的实例
    target_instance_id: str         # 要恢复的实例
    created_at: float = 0.0        # 创建时间
```

#### 修改 `state_manager.py` — 添加 pending switch 管理

```python
class BPStateManager:
    def __init__(self) -> None:
        self._instances: dict[str, BPInstanceSnapshot] = {}
        self._session_index: dict[str, list[str]] = {}
        self._active_map: dict[str, str | None] = {}
        self._lock = asyncio.Lock()
        # 新增：pending 上下文切换
        self._pending_switches: dict[str, PendingContextSwitch] = {}  # session_id → switch

    def set_pending_switch(
        self, session_id: str, switch: PendingContextSwitch,
    ) -> None:
        """设置待执行的上下文切换"""
        self._pending_switches[session_id] = switch

    def consume_pending_switch(
        self, session_id: str,
    ) -> PendingContextSwitch | None:
        """消费并清除待执行的上下文切换，返回 None 表示无待执行切换"""
        return self._pending_switches.pop(session_id, None)

    def has_pending_switch(self, session_id: str) -> bool:
        return session_id in self._pending_switches
```

#### 修改 `bestpractice.py` (handler) — `_bp_switch_task` 不再操作上下文

```python
async def _bp_switch_task(self, args: dict, *, session, orchestrator) -> str:
    target_id = args["target_instance_id"]
    target = self._state.get(target_id)
    if target is None:
        return f"目标实例不存在: {target_id}"

    current_active = self._state.get_active(session.id)
    if current_active is None:
        return "当前没有活跃的最佳实践实例"

    if current_active.instance_id == target_id:
        return "目标实例已经是当前活跃实例"

    # ── 关键改动：不直接操作上下文，只设置 pending switch ──
    self._state.set_pending_switch(
        session.id,
        PendingContextSwitch(
            suspended_instance_id=current_active.instance_id,
            target_instance_id=target_id,
            created_at=time.time(),
        ),
    )

    # 发射前端事件（可以提前通知前端）
    await self._engine.emit_task_switch(
        session,
        suspended_id=current_active.instance_id,
        activated_id=target_id,
    )

    # 返回信息性结果给 MasterAgent
    target_config = target.bp_config
    progress = sum(
        1 for s in target.subtask_statuses.values()
        if s == SubtaskStatus.DONE
    )
    total = len(target_config.subtasks)

    if target.status == BPStatus.COMPLETED:
        return (
            f"已准备切换到任务「{target_config.name}」（已完成）。\n"
            f"上下文将在下一轮推理时自动切换。\n"
            f"请回复用户关于该任务的信息。"
        )
    else:
        return (
            f"已准备切换到任务「{target_config.name}」"
            f"（{progress}/{total} 子任务已完成）。\n"
            f"上下文将在下一轮推理时自动切换。\n"
            f"请使用 ask_user 询问用户是否继续执行剩余子任务。"
        )
```

#### 新增 `context_bridge.py` — `execute_pending_switch` 方法

```python
class ContextBridge:
    async def execute_pending_switch(
        self,
        switch: PendingContextSwitch,
        brain_context: "BrainContext",
        session: "Session",
        state_manager: "BPStateManager",
    ) -> None:
        """
        在安全时机（推理准备阶段）执行上下文切换。
        此时 Brain.Context 不在被 ReasoningEngine 使用中，修改是安全的。
        """
        # 1. 压缩当前上下文 → contextSummary
        current_messages = list(brain_context.messages)
        try:
            summary = await self.compress_for_suspend(
                current_messages,
                system_prompt=brain_context.system or "",
            )
        except Exception as e:
            # m1 改进：压缩失败降级
            logger.warning(f"Context compression failed, using fallback: {e}")
            summary = self._fallback_summary(current_messages)

        # 2. 挂起当前实例
        master_snapshot = None  # M6 改进：不存储完整 messages
        state_manager.suspend(
            switch.suspended_instance_id,
            context_summary=summary,
            master_messages=master_snapshot,
        )

        # 3. 恢复目标实例
        target_snapshot = state_manager.resume(switch.target_instance_id)

        # 4. 构建恢复消息
        restore_messages = self.prepare_restore_messages(target_snapshot)

        # 5. 替换 Brain.Context（此时安全）
        brain_context.messages.clear()
        brain_context.messages.extend(restore_messages)

        # 6. 持久化（C3 改进）
        await state_manager.persist(session)

    def _fallback_summary(self, messages: list[dict]) -> str:
        """m1: 压缩失败时的降级方案 — 截取最后几条消息的文本"""
        recent = messages[-5:] if len(messages) > 5 else messages
        lines = []
        for m in recent:
            role = m.get("role", "?")
            content = str(m.get("content", ""))[:200]
            lines.append(f"[{role}] {content}")
        return "（上下文压缩失败，以下为最近对话片段）\n" + "\n".join(lines)
```

#### 修改 Agent — 添加钩子点

需要在 Agent 的推理准备流程中添加 BP 上下文切换钩子。具体位置取决于 `_prepare_session_context` 或 `chat_with_session_stream` 的入口：

```python
# core/agent.py — 在 _prepare_session_context() 开始处添加

async def _prepare_session_context(self, session, message, ...):
    # ── BP 上下文切换钩子 ──
    bp_engine = self._get_bp_engine_if_available()
    if bp_engine:
        pending = bp_engine._state_manager.consume_pending_switch(session.id)
        if pending:
            context_bridge = bp_engine._get_context_bridge()
            await context_bridge.execute_pending_switch(
                switch=pending,
                brain_context=self.brain.context,
                session=session,
                state_manager=bp_engine._state_manager,
            )

    # ...现有逻辑继续...
```

```python
# core/agent.py — 辅助方法

def _get_bp_engine_if_available(self) -> "BPEngine | None":
    """延迟获取 BPEngine，不触发加载"""
    try:
        from ..bestpractice import _bp_engine
        return _bp_engine  # 如果已初始化则返回，否则 None
    except ImportError:
        return None
```

#### 影响范围

| 文件 | 改动 |
|------|------|
| `config.py` | 新增 `PendingContextSwitch` dataclass |
| `state_manager.py` | 新增 pending switch 管理方法 |
| `bestpractice.py` (handler) | `_bp_switch_task` 简化为设置 pending |
| `context_bridge.py` | 新增 `execute_pending_switch`，新增 `_fallback_summary` |
| `core/agent.py` | `_prepare_session_context` 添加钩子点 |
| `engine.py` | 新增 `emit_task_switch` 事件方法 |

---

## C3. V1 实现 BPStateManager 持久化

### 问题

BPStateManager 纯内存存储，进程崩溃丢失所有 BP 实例状态。

### 改进方案

利用已有的 `Session.metadata` + `SessionManager` 防抖写入机制。

#### 修改 `state_manager.py`

```python
import copy
import json
import time
import asyncio
from dataclasses import asdict
from typing import Any


class BPStateManager:
    def __init__(self) -> None:
        self._instances: dict[str, BPInstanceSnapshot] = {}
        self._session_index: dict[str, list[str]] = {}
        self._active_map: dict[str, str | None] = {}
        self._lock = asyncio.Lock()
        self._pending_switches: dict[str, PendingContextSwitch] = {}

    # ── 持久化 ──

    async def persist(self, session: "Session") -> None:
        """将该会话的所有 BP 实例序列化到 Session.metadata。
        利用 SessionManager 已有的 5s 防抖写入机制，不会产生 I/O 热点。"""
        async with self._lock:
            session_id = session.id
            instance_ids = self._session_index.get(session_id, [])
            snapshots = {}
            for iid in instance_ids:
                snap = self._instances.get(iid)
                if snap:
                    snapshots[iid] = self._serialize_snapshot(snap)

            session.metadata["_bp_state"] = {
                "instances": snapshots,
                "active_id": self._active_map.get(session_id),
                "version": 1,  # 格式版本号，方便未来迁移
            }

    def restore_from_session(self, session: "Session") -> int:
        """从 Session.metadata 恢复 BP 实例状态。
        返回恢复的实例数量。应在 Session 加载时调用。"""
        saved = session.metadata.get("_bp_state")
        if not saved or saved.get("version") != 1:
            return 0

        count = 0
        for iid, data in saved.get("instances", {}).items():
            try:
                snap = self._deserialize_snapshot(data, session.id)
                if snap:
                    self._instances[iid] = snap
                    self._session_index.setdefault(session.id, []).append(iid)
                    count += 1
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Failed to restore BP instance {iid}: {e}"
                )

        active_id = saved.get("active_id")
        if active_id and active_id in self._instances:
            self._active_map[session.id] = active_id

        return count

    def _serialize_snapshot(self, snap: BPInstanceSnapshot) -> dict:
        """序列化快照到可 JSON 化的 dict。
        注意：bp_config 只存 bp_id，恢复时从 BPConfigLoader 重新加载。"""
        data = {
            "bp_id": snap.bp_id,
            "instance_id": snap.instance_id,
            "session_id": snap.session_id,
            "status": snap.status.value,
            "created_at": snap.created_at,
            "completed_at": snap.completed_at,
            "suspended_at": snap.suspended_at,
            "current_subtask_index": snap.current_subtask_index,
            "run_mode": snap.run_mode.value,
            "subtask_statuses": {
                k: v.value for k, v in snap.subtask_statuses.items()
            },
            "subtask_outputs": snap.subtask_outputs,  # dict of dicts, JSON-safe
            "initial_input": snap.initial_input,       # M8 改进
            "context_summary": snap.context_summary,
            # 注意：不存储 master_messages_snapshot（M6 改进）
            # 注意：不存储 bp_config（恢复时重新加载）
        }
        return data

    def _deserialize_snapshot(
        self, data: dict, session_id: str,
    ) -> BPInstanceSnapshot | None:
        """从序列化数据恢复快照。需要 BPConfigLoader 来重新关联 bp_config。"""
        from .config import BPStatus, SubtaskStatus, RunMode

        bp_id = data.get("bp_id")
        if not bp_id:
            return None

        # bp_config 需要从 BPConfigLoader 重新加载
        # 这里暂时设为 None，由调用方负责关联
        # （在 BPEngine.restore_session 中处理）
        return BPInstanceSnapshot(
            bp_id=bp_id,
            instance_id=data["instance_id"],
            session_id=session_id,
            status=BPStatus(data.get("status", "active")),
            created_at=data.get("created_at", 0.0),
            completed_at=data.get("completed_at"),
            suspended_at=data.get("suspended_at"),
            current_subtask_index=data.get("current_subtask_index", 0),
            run_mode=RunMode(data.get("run_mode", "manual")),
            subtask_statuses={
                k: SubtaskStatus(v)
                for k, v in data.get("subtask_statuses", {}).items()
            },
            subtask_outputs=data.get("subtask_outputs", {}),
            initial_input=data.get("initial_input", {}),
            context_summary=data.get("context_summary", ""),
            bp_config=None,  # 由调用方关联
        )
```

#### 新增 `engine.py` — Session 恢复入口

```python
class BPEngine:
    def restore_session(self, session: "Session") -> int:
        """从 Session 恢复 BP 实例状态，关联 bp_config 引用。
        应在 Session 加载/激活时调用。"""
        count = self._state_manager.restore_from_session(session)
        if count > 0:
            # 关联 bp_config
            for iid in self._state_manager._session_index.get(session.id, []):
                snap = self._state_manager.get(iid)
                if snap and snap.bp_config is None:
                    config = self._config_loader.get(snap.bp_id)
                    if config:
                        snap.bp_config = config
                    else:
                        import logging
                        logging.getLogger(__name__).warning(
                            f"BP config '{snap.bp_id}' not found for instance {iid}, "
                            f"marking as completed"
                        )
                        snap.status = BPStatus.COMPLETED
        return count
```

#### 调用时机

```python
# 方案 1：在 SeeCrab 路由中，session 加载后调用
# api/routes/seecrab.py
session = session_manager.get_or_create(...)
bp_engine = get_bp_engine()
if bp_engine:
    bp_engine.restore_session(session)

# 方案 2：在 BPToolHandler 首次使用时检查并恢复
# 在 _ensure_deps() 中添加
def _ensure_deps(self):
    if self._engine is None:
        from ...bestpractice import get_bp_engine
        self._engine = get_bp_engine()
        self._state = self._engine._state_manager
    # 检查当前 session 是否需要恢复
    session = self._get_session()
    if session.id not in self._state._session_index:
        self._engine.restore_session(session)
```

#### 需要调用 `persist()` 的时机

| 操作 | 调用位置 |
|------|---------|
| 子任务完成 | `engine.execute_subtask` 最后 |
| 任务挂起 | `context_bridge.execute_pending_switch` 中 |
| 任务恢复 | `context_bridge.execute_pending_switch` 中 |
| 任务完成 | `engine.execute_subtask` 中 complete 分支 |
| 编辑输出 | `handler._bp_edit_output` 最后 |
| 模式切换 | 通过前端 API（非工具调用，直接修改状态后持久化） |

---

## M1. Agent 实例复用安全性

### 分析

经过代码探索确认：`AgentOrchestrator._call_agent()` 中 SubAgent 的 `chat_with_session()` 传入 `session_messages=[]`，Brain.Context 每次都是全新构建。Agent 实例级别的 `AgentState._tasks` 按 `session_id` 隔离，且每次 `delegate()` 都会 `begin_task()` / `reset_task()`。

**结论**：当前 AgentInstancePool 的缓存机制对 BP 场景**基本安全**，不需要为每次子任务创建独立实例。

### 需要补充的防护

在 `engine.py` 中添加文档注释和一个防御性检查：

```python
async def execute_subtask(self, instance_id, orchestrator, session):
    ...
    # 注意：同一 session 中多个 BP 实例共享同一个 AgentInstancePool 缓存。
    # 这是安全的，因为 AgentOrchestrator._call_agent() 每次都以
    # session_messages=[] 调用 SubAgent，Brain.Context 每次全新构建。
    # AgentState._tasks 按 session_id 隔离，delegate() 内部管理 begin/reset。
    #
    # 如果未来需要跨子任务保留 SubAgent 上下文（如 DAG 扩展中的迭代执行），
    # 需要为每个 (bp_instance_id, subtask_id) 创建独立的 pool key。
    result = await orchestrator.delegate(...)
    ...
```

---

## M2. Deep Merge 数组语义明确化

### 改进方案

1. 保持当前的 deep merge 实现（数组整体替换）
2. 在 prompt 中明确告知 LLM 数组语义
3. 在 tool description 中说明

#### 修改 `chat_to_edit.md`

```markdown
用户要求修改子任务「$subtask_name」的输出。

当前输出：
```json
$current_output
```

修改意图：$user_message

请生成修改后的 changes JSON，规则如下：
- 仅包含**需要修改的字段**
- 未提及的字段不要包含在 changes 中，它们将保持原值
- **对象字段**：递归合并（仅覆盖提及的子字段）
- **数组字段**：提供**完整的新数组**（数组不支持部分修改，必须给出完整替换值）
- **删除字段**：将字段值设为 null

示例：
用户说 "把盈利模式改成纯SaaS"
当前 findings = ["技术架构：TokenML引擎", "盈利模式：SaaS+抽成", "竞争壁垒：3项专利"]
正确的 changes = {
  "findings": ["技术架构：TokenML引擎", "盈利模式：纯SaaS订阅", "竞争壁垒：3项专利"]
}
（注意：完整数组，包含未修改的元素）

请输出 changes JSON。
```

#### 修改 `bp_edit_output` tool description

```python
{
    "name": "bp_edit_output",
    "description": "编辑已完成子任务的输出。对象字段递归合并，数组字段整体替换。",
    "input_schema": {
        "type": "object",
        "properties": {
            "instance_id": {"type": "string", "description": "BP 实例 ID（可选，默认当前活跃实例）"},
            "subtask_id": {"type": "string"},
            "changes": {
                "type": "object",
                "description": "要修改的字段及新值。对象字段递归合并，数组字段提供完整新数组。",
            },
        },
        "required": ["subtask_id", "changes"],  # instance_id 可选（M5 改进）
    },
    "category": "BestPractice",
},
```

---

## M3. intent_router 合并到 system_dynamic

### 改进方案

删除独立的 `intent_router.md` 模板文件。将意图路由指令作为 `system_dynamic.md` 的条件内容。

#### 修改 `system_dynamic.md`

```markdown
## 当前最佳实践状态

$status_table

$active_context

$intent_routing_instruction
```

#### 修改 `engine.py` — `get_dynamic_prompt_section`

```python
def get_dynamic_prompt_section(self, session_id: str) -> str:
    instances = self._state_manager.get_all_for_session(session_id)
    if not instances:
        return ""

    status_table = self._state_manager.get_status_table(session_id)
    active = self._state_manager.get_active(session_id)

    if active is None:
        # 有实例但无活跃的（全部 completed/suspended）
        return self._prompt_loader.render(
            "system_dynamic",
            status_table=status_table,
            active_context="",
            intent_routing_instruction=(
                "如果用户消息提及上述任何已完成或挂起的任务，"
                "请调用 bp_switch_task 切换到该任务。"
            ),
        )

    active_context = self._state_manager.get_outputs_summary(active.instance_id)

    # 根据活跃实例的当前状态决定路由指令
    current_subtask_id = active.bp_config.subtasks[
        active.current_subtask_index
    ].id if active.current_subtask_index < len(active.bp_config.subtasks) else None

    at_pause_point = (
        current_subtask_id
        and active.subtask_statuses.get(current_subtask_id) != SubtaskStatus.CURRENT
    )

    if at_pause_point:
        intent_instruction = (
            "用户在暂停点发送消息时，请判断意图：\n"
            "A) 修改某个已完成子任务的输出 → 调用 bp_edit_output\n"
            "B) 确认进入下一步 → 调用 bp_continue\n"
            "C) 切换到其他任务 → 调用 bp_switch_task\n"
            "D) 与当前任务相关的追问 → 直接回答\n"
            "E) 无关话题 / 全新任务 → 正常处理，可能触发新的最佳实践"
        )
    else:
        intent_instruction = ""

    return self._prompt_loader.render(
        "system_dynamic",
        status_table=status_table,
        active_context=active_context,
        intent_routing_instruction=intent_instruction,
    )
```

#### 删除文件

- `prompts/intent_router.md` — 删除（内容已合并）

#### 修改目录布局

```
src/seeagent/bestpractice/
├── prompts/
│   ├── system_static.md
│   ├── system_dynamic.md       ← 含条件性路由指令
│   ├── subtask_instruction.md
│   ├── chat_to_edit.md
│   ├── context_restore.md
│   └── cascade_confirm.md
│   # 删除 intent_router.md
```

---

## M4. 删除 BPTriggerDetector，纯 LLM 触发

### 理由

1. LLM 在系统提示中已有完整的 BP 定义和触发条件
2. COMMAND 触发由 LLM 直接识别，不需要规则预筛
3. CONTEXT 触发最终也依赖 LLM 判断
4. 预筛选结果没有明确的传递机制
5. 减少代码量和维护负担

### 改进方案

#### 删除 `trigger.py`

整个文件删除。

#### inferCooldown 改为 Session.metadata + BP_DYNAMIC 注入

```python
# state_manager.py — 新增 cooldown 管理
class BPStateManager:
    def set_cooldown(self, session_id: str, turns: int = 5) -> None:
        """设置推断冷却（选择自由模式后）"""
        # 存储到内存，持久化通过 persist() → Session.metadata
        self._cooldowns[session_id] = turns

    def tick_cooldown(self, session_id: str) -> None:
        """每轮用户输入递减"""
        if session_id in self._cooldowns:
            self._cooldowns[session_id] = max(0, self._cooldowns[session_id] - 1)
            if self._cooldowns[session_id] == 0:
                del self._cooldowns[session_id]

    def get_cooldown(self, session_id: str) -> int:
        return self._cooldowns.get(session_id, 0)
```

```python
# engine.py — get_dynamic_prompt_section 中注入 cooldown 提示
def get_dynamic_prompt_section(self, session_id: str) -> str:
    ...
    cooldown = self._state_manager.get_cooldown(session_id)
    if cooldown > 0:
        status_table += (
            f"\n⚠️ 用户 {cooldown} 轮消息内选择了自由模式，"
            f"不要推断触发最佳实践。"
        )
    ...
```

#### 更新依赖关系图

```
bestpractice 模块内部依赖:
                    ┌── schema_chain.py
                    ├── config.py (数据模型)
                    ├── state_manager.py
                    ├── engine.py ──→ AgentOrchestrator
                    │     └──→ context_bridge.py ──→ ContextManager
                    │     └──→ prompt_loader.py
                    │
                    # trigger.py 已删除
```

---

## M5. instance_id 可选化 + 可发现性

### 改进方案

#### 1. bp_continue / bp_edit_output / bp_switch_task 的 instance_id 改为可选

```python
BP_TOOL_DEFINITIONS = [
    {
        "name": "bp_start",
        "description": "启动最佳实践任务执行",
        "input_schema": {
            "type": "object",
            "properties": {
                "bp_id": {"type": "string", "description": "最佳实践配置 ID"},
                "input_data": {"type": "object", "description": "初始输入数据（可为空，由子任务通过 ask_user 补充）"},
            },
            "required": ["bp_id"],  # input_data 可选
        },
    },
    {
        "name": "bp_continue",
        "description": "继续执行当前活跃最佳实践的下一个子任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID。不提供则使用当前活跃实例。",
                },
            },
            "required": [],  # 全部可选
        },
    },
    {
        "name": "bp_edit_output",
        "description": "编辑已完成子任务的输出。对象字段递归合并，数组字段整体替换。",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "BP 实例 ID。不提供则使用当前活跃实例。",
                },
                "subtask_id": {"type": "string"},
                "changes": {"type": "object"},
            },
            "required": ["subtask_id", "changes"],
        },
    },
    {
        "name": "bp_switch_task",
        "description": "切换到另一个最佳实践实例",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_instance_id": {"type": "string"},
            },
            "required": ["target_instance_id"],  # 切换目标必须明确
        },
    },
]
```

#### 2. handler 中添加 fallback 逻辑

```python
# bestpractice.py (handler)
def _resolve_instance_id(self, args: dict, session) -> str | None:
    """解析 instance_id，不提供时使用当前活跃实例"""
    instance_id = args.get("instance_id")
    if instance_id:
        return instance_id
    active = self._state.get_active(session.id)
    return active.instance_id if active else None

async def _bp_continue(self, args, *, session, orchestrator):
    instance_id = self._resolve_instance_id(args, session)
    if not instance_id:
        return "当前没有活跃的最佳实践实例，请先调用 bp_start。"
    ...

async def _bp_edit_output(self, args, *, session, orchestrator):
    instance_id = self._resolve_instance_id(args, session)
    if not instance_id:
        return "当前没有活跃的最佳实践实例。"
    ...
```

#### 3. tool_result 和状态表中包含 instance_id

`bp_start` 返回结果中已包含 instance_id（见 C1 的 `_format_subtask_complete_result`）。

`get_status_table` 输出格式改为：

```python
def get_status_table(self, session_id: str) -> str:
    instances = self.get_all_for_session(session_id)
    if not instances:
        return "(无最佳实践任务)"

    lines = ["当前会话中的最佳实践任务："]
    for inst in instances:
        status_icon = {"active": "●", "suspended": "○", "completed": "✓"}.get(
            inst.status.value, "?"
        )
        status_label = {"active": "活跃", "suspended": "挂起", "completed": "已完成"}.get(
            inst.status.value, inst.status.value
        )
        done = sum(1 for s in inst.subtask_statuses.values() if s == SubtaskStatus.DONE)
        total = len(inst.bp_config.subtasks) if inst.bp_config else 0
        # 包含 instance_id 供 LLM 在 bp_switch_task 时引用
        lines.append(
            f"  {status_icon} [{status_label}] {inst.bp_config.name if inst.bp_config else inst.bp_id}"
            f" — {done}/{total} 子任务已完成"
            f" (id: {inst.instance_id})"
        )

    return "\n".join(lines)
```

---

## M6. 删除 master_messages_snapshot

### 改进方案

#### 修改 `BPInstanceSnapshot`

```python
@dataclass
class BPInstanceSnapshot:
    # 身份
    bp_id: str
    instance_id: str
    session_id: str

    # 生命周期
    status: BPStatus = BPStatus.ACTIVE
    created_at: float = 0.0
    completed_at: float | None = None
    suspended_at: float | None = None

    # 执行进度
    current_subtask_index: int = 0
    run_mode: RunMode = RunMode.MANUAL
    subtask_statuses: dict[str, SubtaskStatus] = field(default_factory=dict)

    # 数据
    initial_input: dict = field(default_factory=dict)  # M8 改进
    subtask_outputs: dict[str, dict] = field(default_factory=dict)

    # 上下文
    context_summary: str = ""
    # 删除：master_messages_snapshot 字段
    # 恢复时使用 context_summary + subtask_outputs 重建
    # 如需精确恢复，可从 SessionContext.messages 按 bp_instance_id 过滤

    # 配置引用
    bp_config: BestPracticeConfig | None = None
```

#### 恢复策略

`context_bridge.prepare_restore_messages` 已经从 `context_summary` + `subtask_outputs` 构建恢复消息，不依赖 `master_messages_snapshot`。无需额外修改。

---

## M7. SSE 重连全量状态同步 API

### 改进方案

#### 新增 REST API endpoint

```python
# api/routes/seecrab.py — 新增 endpoint

@router.get("/api/bp/status/{conversation_id}")
async def get_bp_status(conversation_id: str) -> dict:
    """前端 SSE 重连后调用，获取所有 BP 实例的全量状态。
    conversation_id 映射到 session_id。"""
    session = session_manager.get_by_conversation(conversation_id)
    if not session:
        return {"instances": [], "active_id": None}

    try:
        from seeagent.bestpractice import get_bp_engine
        engine = get_bp_engine()
    except ImportError:
        return {"instances": [], "active_id": None}

    state = engine._state_manager
    instances = state.get_all_for_session(session.id)
    active = state.get_active(session.id)

    return {
        "active_id": active.instance_id if active else None,
        "instances": [
            {
                "instance_id": inst.instance_id,
                "bp_id": inst.bp_id,
                "bp_name": inst.bp_config.name if inst.bp_config else inst.bp_id,
                "status": inst.status.value,
                "run_mode": inst.run_mode.value,
                "current_subtask_index": inst.current_subtask_index,
                "subtasks": [
                    {"id": st.id, "name": st.name}
                    for st in (inst.bp_config.subtasks if inst.bp_config else [])
                ],
                "statuses": {
                    k: v.value for k, v in inst.subtask_statuses.items()
                },
                "outputs": inst.subtask_outputs,
            }
            for inst in instances
        ],
    }
```

#### 前端集成

```typescript
// stores/bestpractice.ts
async function syncOnReconnect(conversationId: string) {
  const res = await fetch(`/api/bp/status/${conversationId}`);
  const data = await res.json();
  // 全量替换本地状态
  instances.value = new Map(
    data.instances.map((i: BPInstanceClient) => [i.instanceId, i])
  );
  activeInstanceId.value = data.active_id;
}
```

---

## M8. 消除 __initial_input__ 魔法键

### 改进方案

已在 M6 中将 `initial_input` 作为独立字段添加到 `BPInstanceSnapshot`。

#### 修改 handler — `_bp_start`

```python
async def _bp_start(self, args, *, session, orchestrator):
    config = self._engine.get_config(args["bp_id"])
    if config is None:
        return f"未找到最佳实践配置: {args['bp_id']}"

    instance = self._state.create_instance(config, session.id)

    # M8 改进：使用独立字段存储初始输入
    instance.initial_input = args.get("input_data", {})

    await self._engine.emit_progress(instance.instance_id, session)
    return await self._engine.execute_subtask(
        instance.instance_id, orchestrator, session,
    )
```

#### 修改 engine — `_resolve_input`

```python
def _resolve_input(self, instance: BPInstanceSnapshot, subtask: SubtaskConfig) -> dict:
    config = instance.bp_config
    idx = next(i for i, s in enumerate(config.subtasks) if s.id == subtask.id)

    if subtask.input_mapping:
        # DAG 模式
        return {
            field: instance.subtask_outputs.get(upstream_id, {})
            for field, upstream_id in subtask.input_mapping.items()
        }
    elif idx == 0:
        # M8 改进：使用独立字段
        return instance.initial_input
    else:
        prev_subtask = config.subtasks[idx - 1]
        return instance.subtask_outputs.get(prev_subtask.id, {})
```

---

## M9. 子任务超时与重试策略

### 改进方案

#### 修改 `config.py` — SubtaskConfig 新增字段

```python
@dataclass
class SubtaskConfig:
    id: str
    name: str
    agent_profile: str
    input_schema: dict
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    input_mapping: dict[str, str] = field(default_factory=dict)

    # M9 新增
    timeout_seconds: int | None = None        # 子任务超时（秒），None 使用全局默认值
    max_retries: int = 0                       # 最大重试次数，0 表示不重试
```

#### 修改 engine — execute_subtask 添加重试

```python
async def execute_subtask(self, instance_id, orchestrator, session):
    ...
    subtask = config.subtasks[idx]

    max_attempts = subtask.max_retries + 1
    last_error = None

    for attempt in range(max_attempts):
        try:
            result = await orchestrator.delegate(
                session=session,
                from_agent="main",
                to_agent=subtask.agent_profile,
                message=message,
                reason=f"BP:{config.name} / {subtask.name}",
                # 如果 SubtaskConfig 指定了 timeout，通过 orchestrator 参数传递
                # 需要确认 delegate() 是否支持 timeout_override 参数
                # 如果不支持，可通过临时修改 orchestrator 配置实现
            )
            last_error = None
            break  # 成功，跳出重试循环
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                logger.warning(
                    f"SubTask '{subtask.id}' failed (attempt {attempt + 1}/{max_attempts}): {e}"
                )
                await asyncio.sleep(2 ** attempt)  # 指数退避
            else:
                logger.error(
                    f"SubTask '{subtask.id}' failed after {max_attempts} attempts: {e}"
                )

    if last_error:
        self._state_manager.update_subtask_status(
            instance_id, subtask.id, SubtaskStatus.PENDING,
        )
        return (
            f"子任务「{subtask.name}」执行失败（尝试 {max_attempts} 次）: {last_error}\n"
            f"子任务已重置为 PENDING，可通过 bp_continue 重试。"
        )

    # 成功路径继续...
    output = self._parse_output_json(result)
    ...
```

---

## M10. Edit 校验结果传达

### 改进方案

```python
# handler — _bp_edit_output
async def _bp_edit_output(self, args, *, session, orchestrator):
    instance_id = self._resolve_instance_id(args, session)
    if not instance_id:
        return "当前没有活跃的最佳实践实例。"

    old, new = self._engine.merge_subtask_output(
        instance_id, args["subtask_id"], args["changes"],
    )

    # M10: 校验并在结果中传达
    instance = self._state.get(instance_id)
    validation_warning = ""
    if instance:
        missing = self._engine.validate_output_soft(instance, args["subtask_id"], new)
        if missing:
            validation_warning = (
                f"\n⚠️ 修改后输出缺少下游必需字段: {missing}。"
                f"下游子任务可能因此需要通过 ask_user 向用户补充信息。"
            )

    stale_ids = self._state.mark_downstream_stale(instance_id, args["subtask_id"])

    await self._engine.emit_stale(instance_id, stale_ids, session)
    await self._engine.emit_subtask_output(
        instance_id, args["subtask_id"], new, session,
    )

    # C3: 持久化
    await self._state.persist(session)

    if stale_ids:
        stale_names = self._engine.get_subtask_names(instance, stale_ids)
        return (
            f"已修改子任务「{args['subtask_id']}」的输出。{validation_warning}\n"
            f"下游子任务 {stale_names} 需基于新数据重新执行。\n"
            f"请使用 ask_user 确认用户是否继续级联重跑。"
        )
    return f"已修改子任务「{args['subtask_id']}」的输出，无需重跑下游。{validation_warning}"
```

```python
# engine.py — validate_output_soft 改为返回缺失字段列表
def validate_output_soft(
    self, instance: BPInstanceSnapshot, subtask_id: str, output: dict,
) -> list[str]:
    """宽松校验，返回缺失的 required 字段列表"""
    config = instance.bp_config
    idx = next((i for i, s in enumerate(config.subtasks) if s.id == subtask_id), None)
    if idx is None or idx >= len(config.subtasks) - 1:
        return []

    next_schema = config.subtasks[idx + 1].input_schema
    missing = [k for k in next_schema.get("required", []) if k not in output]
    if missing:
        logger.warning(
            f"[BPEngine] Edited output for '{subtask_id}' missing required fields "
            f"for downstream: {missing}"
        )
    return missing

def get_subtask_names(
    self, instance: BPInstanceSnapshot, subtask_ids: list[str],
) -> list[str]:
    """将 subtask_id 列表转换为显示名"""
    name_map = {st.id: st.name for st in instance.bp_config.subtasks}
    return [f"「{name_map.get(sid, sid)}」" for sid in subtask_ids]
```

---

## M11. Schema 深度限制与降级

### 改进方案

在 `BPConfigLoader` 中添加 schema 验证：

```python
# config.py
MAX_SCHEMA_DEPTH = 3  # 最多 3 层嵌套

class BPConfigLoader:
    def _validate_schema_depth(self, schema: dict, path: str = "", depth: int = 0) -> list[str]:
        """检查 JSON Schema 的嵌套深度"""
        warnings = []
        if depth > MAX_SCHEMA_DEPTH:
            warnings.append(
                f"Schema at '{path}' exceeds max depth {MAX_SCHEMA_DEPTH}. "
                f"Frontend will render as JSON text editor for deep levels."
            )
            return warnings

        props = schema.get("properties", {})
        for key, prop in props.items():
            if prop.get("type") == "object":
                warnings.extend(
                    self._validate_schema_depth(prop, f"{path}.{key}", depth + 1)
                )
            elif prop.get("type") == "array":
                items = prop.get("items", {})
                if isinstance(items, dict) and items.get("type") == "object":
                    warnings.extend(
                        self._validate_schema_depth(items, f"{path}.{key}[]", depth + 1)
                    )
        return warnings
```

前端 `SubtaskOutputPanel` 渲染策略：
- depth 0-2：结构化表单（文本框、数组增删）
- depth 3+：JSON 文本编辑器（Monaco editor）

---

## M12. BP 配置加载时验证

### 改进方案

```python
# config.py — BPConfigLoader
class BPConfigLoader:
    def load_all(self) -> dict[str, BestPracticeConfig]:
        configs = {}
        for path in self._search_paths:
            for yaml_file in path.glob("*.yaml"):
                try:
                    config = self._parse_yaml(yaml_file)
                    errors = self._validate(config)
                    if errors:
                        logger.warning(
                            f"BP config '{yaml_file}' has validation warnings:\n"
                            + "\n".join(f"  - {e}" for e in errors)
                        )
                    configs[config.id] = config
                except Exception as e:
                    logger.error(f"Failed to load BP config '{yaml_file}': {e}")
        self._configs = configs
        return configs

    def _validate(self, config: BestPracticeConfig) -> list[str]:
        """验证 BP 配置的完整性"""
        errors = []

        # 1. subtask id 唯一性
        ids = [st.id for st in config.subtasks]
        if len(ids) != len(set(ids)):
            errors.append(f"Duplicate subtask IDs: {[x for x in ids if ids.count(x) > 1]}")

        # 2. agent_profile 存在性（延迟检查，因为 ProfileStore 可能未初始化）
        # 这在 BPEngine.restore_session 或首次使用时检查更合适

        # 3. depends_on 引用的 subtask_id 存在
        id_set = set(ids)
        for st in config.subtasks:
            for dep in st.depends_on:
                if dep not in id_set:
                    errors.append(f"Subtask '{st.id}' depends_on unknown '{dep}'")

        # 4. 循环依赖检测
        if any(st.depends_on for st in config.subtasks):
            cycle = self._detect_cycle(config.subtasks)
            if cycle:
                errors.append(f"Circular dependency detected: {' → '.join(cycle)}")

        # 5. input_schema 基本结构检查
        for st in config.subtasks:
            if not isinstance(st.input_schema, dict):
                errors.append(f"Subtask '{st.id}' input_schema is not a dict")
            elif st.input_schema.get("type") != "object":
                errors.append(f"Subtask '{st.id}' input_schema.type should be 'object'")

        # 6. Schema 深度检查（M11）
        for st in config.subtasks:
            depth_warnings = self._validate_schema_depth(st.input_schema, st.id)
            errors.extend(depth_warnings)

        # 7. description 非空（m3）
        for st in config.subtasks:
            if not st.description:
                errors.append(
                    f"Subtask '{st.id}' has empty description, "
                    f"will use name '{st.name}' as fallback"
                )

        # 8. triggers 验证
        for trigger in config.triggers:
            if trigger.type == "schedule" and trigger.cron:
                try:
                    # 简单的 cron 格式检查（5 段）
                    parts = trigger.cron.split()
                    if len(parts) != 5:
                        errors.append(f"Invalid cron expression: '{trigger.cron}' (need 5 fields)")
                except Exception:
                    errors.append(f"Invalid cron expression: '{trigger.cron}'")

        return errors

    @staticmethod
    def _detect_cycle(subtasks: list[SubtaskConfig]) -> list[str] | None:
        """拓扑排序检测循环依赖，返回循环路径或 None"""
        graph = {st.id: set(st.depends_on) for st in subtasks}
        visited = set()
        path = []
        path_set = set()

        def dfs(node: str) -> list[str] | None:
            if node in path_set:
                cycle_start = path.index(node)
                return path[cycle_start:] + [node]
            if node in visited:
                return None
            visited.add(node)
            path.append(node)
            path_set.add(node)
            for dep in graph.get(node, set()):
                result = dfs(dep)
                if result:
                    return result
            path.pop()
            path_set.discard(node)
            return None

        for st_id in graph:
            result = dfs(st_id)
            if result:
                return result
        return None
```

---

## M13. inferCooldown 持久化

### 改进方案

已在 M4 中一并处理。cooldown 存储在 `BPStateManager._cooldowns`，通过 `persist()` 写入 `Session.metadata`。

补充序列化支持：

```python
# state_manager.py — _serialize 中增加 cooldown
async def persist(self, session: "Session") -> None:
    async with self._lock:
        session_id = session.id
        ...
        session.metadata["_bp_state"] = {
            "instances": snapshots,
            "active_id": self._active_map.get(session_id),
            "cooldown": self._cooldowns.get(session_id, 0),  # 新增
            "version": 1,
        }

def restore_from_session(self, session: "Session") -> int:
    saved = session.metadata.get("_bp_state")
    if not saved or saved.get("version") != 1:
        return 0
    ...
    cooldown = saved.get("cooldown", 0)
    if cooldown > 0:
        self._cooldowns[session.id] = cooldown
    ...
```

---

## D1. TriggerType 扩展方式统一

### 改进方案

修改需求文档 §1.2 的表格：

```
| 触发类型 | Trigger Type | **新增独立机制** | `BPEngine` 系统提示引导 |
| 已有 `ONCE`/`INTERVAL`/`CRON` 保持不变。                                |
| BP 触发不扩展 ScheduledTask 的 TriggerType 枚举，                        |
| 而是通过 BP_STATIC 系统提示让 LLM 直接识别 COMMAND/CONTEXT 触发。       |
| CRON 触发复用已有 ScheduledTask + CRON，                                 |
| 在 ScheduledTask.prompt 中注入 bp_start 调用指令。                       |
| EVENT 触发通过 pending_user_inserts 注入触发消息。                       |
| UI_CLICK 通过前端 API 直接构造 bp_start 参数调用后端。                   |
```

---

## D2. BPStateManager 接口返回值对齐

### 改进方案

统一为返回 `BPInstanceSnapshot`：

需求文档 §1.1 修改：
```
| BP 状态管理器 | BPStateManager | 新增 | `BPStateManager`（`bestpractice/state_manager.py`） |
...
def create_instance(bp_config, session_id) -> BPInstanceSnapshot  # 返回完整快照
```

---

## D3. BPInstanceSnapshot 字段一致性

### 改进方案

统一 `bp_config` 为非空类型，构造时必须关联：

```python
@dataclass
class BPInstanceSnapshot:
    ...
    bp_config: BestPracticeConfig | None = None
    # 实际使用中，bp_config 仅在反序列化恢复的瞬间为 None，
    # 恢复完成后由 BPEngine.restore_session 关联。
    # 所有正常路径中 bp_config 非空。
```

在 `create_instance` 中确保非空：

```python
def create_instance(self, bp_config: BestPracticeConfig, session_id: str) -> BPInstanceSnapshot:
    assert bp_config is not None, "bp_config must not be None"
    instance = BPInstanceSnapshot(
        bp_id=bp_config.id,
        instance_id=str(uuid4()),
        session_id=session_id,
        created_at=time.time(),
        bp_config=bp_config,
        subtask_statuses={st.id: SubtaskStatus.PENDING for st in bp_config.subtasks},
    )
    ...
```

---

## S1. Auto 模式中用户中断处理

### 改进方案

已在 C1 中通过迭代式执行解决。每个子任务之间，MasterAgent 的 ReAct 循环都会运行，此时可以：
1. 检查 `pending_user_inserts`（用户发送的新消息）
2. 检查 `cancel_event`
3. 处理用户的中断意图

在 `system_static.md` 中补充：

```markdown
### 自动模式中断规则
- 如果在自动执行过程中收到用户消息，**优先处理用户消息**
- 如果用户表达了暂停、取消或修改意图，停止自动执行
- 处理完用户消息后，如果任务未取消，可询问用户是否继续自动执行
```

---

## S2. 同一 BP 配置多实例区分

### 改进方案

在状态表中包含初始输入摘要：

```python
# state_manager.py — get_status_table
def get_status_table(self, session_id: str) -> str:
    ...
    for inst in instances:
        ...
        # S2: 添加输入参数摘要用于区分同配置多实例
        input_preview = ""
        if inst.initial_input:
            # 截取前 50 字符的关键信息
            topic = inst.initial_input.get("topic", "")
            if topic:
                input_preview = f" ({topic[:30]})"
            else:
                first_val = str(list(inst.initial_input.values())[0])[:30] if inst.initial_input else ""
                input_preview = f" ({first_val})" if first_val else ""

        lines.append(
            f"  {status_icon} [{status_label}] "
            f"{inst.bp_config.name}{input_preview}"
            f" — {done}/{total} 子任务已完成"
            f" (id: {inst.instance_id})"
        )
    ...
```

输出示例：
```
● [活跃] 市场调研报告 (NFT) — 1/3 子任务已完成 (id: bp-002)
✓ [已完成] 市场调研报告 (Token) — 3/3 子任务已完成 (id: bp-001)
```

---

## S3. 子任务中 ask_user 路由

### 改进方案

SubAgent 执行期间的 `ask_user` 工具调用通过以下机制工作：

1. SubAgent 调用 `ask_user` → 工具执行器发送 SSE 事件到前端
2. 前端展示给用户 → 用户回复
3. 用户回复通过 SeeCrab API 进入
4. 回复被路由到当前活跃的 Agent（此时是 SubAgent，因为 delegate() 尚未返回）

**验证路由正确性**：在 `AgentOrchestrator._call_agent` 中，SubAgent 通过 `chat_with_session` 执行，该调用是同步阻塞的（等待 SubAgent 完成）。SubAgent 的 ReAct 循环中如果执行了 ask_user，它会等待 `TaskState.pending_user_inserts` 中的回复。

**问题**：用户的回复消息通过哪条路径注入到 SubAgent 的 `pending_user_inserts`？

需要在技术设计中明确说明：

```markdown
### SubAgent ask_user 支持

SubAgent 可以使用 ask_user 工具向用户提问（例如输入不足时补充信息）。

**消息路由**：
1. SubAgent 调用 ask_user → 工具执行器通过 SSE event_bus 发送事件到前端
2. 前端渲染 AskUserBlock，用户回复
3. 用户回复通过 SeeCrab API 到达后端
4. 后端检测到当前有活跃的 SubAgent 委派，将消息注入到
   SubAgent 的 TaskState.pending_user_inserts（通过 AgentOrchestrator 路由）
5. SubAgent 的 ReAct 循环检测到 pending_user_inserts，
   将回复作为 tool_result 返回给 ask_user 工具调用

**前提条件**：AgentOrchestrator 需要支持将用户消息路由到正在执行的 SubAgent。
当前实现中，SeeCrab 路由层通过 `session.agent_state.get_task_for_session()`
获取活跃 TaskState，如果 SubAgent 有独立的 TaskState（通过 delegate 内部的
begin_task 创建），则消息应该能正确路由到 SubAgent。

**需要验证**：在实现阶段确认此路由路径的正确性。
如果路由存在问题，备选方案：
- SubAgent 不直接使用 ask_user，而是在输出中标记「需要用户补充」
- BPEngine 检测到此标记后，由 MasterAgent 级别发起 ask_user
- 用户回复后，MasterAgent 修改 SubAgent 的输入并重新委派
```

---

## S4. Chat-to-Edit 意图确认

### 改进方案

在 `system_static.md` 和 `system_dynamic.md` 中添加确认步骤指令：

```markdown
### Chat-to-Edit 规则
当你识别到用户想修改某个已完成子任务的输出时：
1. **先确认意图**：使用 ask_user 向用户确认：
   - 你理解的目标子任务
   - 你解析的修改内容摘要
   - 选项: [确认修改] [不，我想重新执行该子任务] [取消]
2. 用户确认后再调用 bp_edit_output
3. 不要在未确认的情况下直接调用 bp_edit_output
```

这增加了一轮交互但显著降低误操作风险。对于 auto 模式，这个确认步骤也是必要的。

---

## S5. 非 SeeCrab 通道降级

### 改进方案

在 `engine.py` 的格式化方法中提供文本模式输出：

```python
class BPEngine:
    def format_progress_text(self, instance: BPInstanceSnapshot) -> str:
        """非 Web 通道的纯文本进度显示"""
        config = instance.bp_config
        lines = [f"[最佳实践] {config.name}"]
        progress_parts = []
        for i, st in enumerate(config.subtasks):
            status = instance.subtask_statuses.get(st.id, SubtaskStatus.PENDING)
            icon = {
                SubtaskStatus.DONE: "✅",
                SubtaskStatus.CURRENT: "⏳",
                SubtaskStatus.STALE: "⚠️",
                SubtaskStatus.PENDING: "○",
            }.get(status, "○")
            progress_parts.append(f"{icon} {st.name}")

        lines.append("进度: " + " → ".join(progress_parts))
        lines.append(f"模式: {'自动' if instance.run_mode == RunMode.AUTO else '手动'}")
        return "\n".join(lines)
```

在 `_format_subtask_complete_result` 和 `_format_completion_result` 中始终包含文本模式的进度信息（它们已经返回纯文本给 MasterAgent，MasterAgent 在 CLI/IM 模式下直接展示）。

在技术设计中添加说明：

```markdown
### 通道降级策略

| 通道 | 进度展示 | 结果看板 | 模式切换 |
|------|---------|---------|---------|
| SeeCrab (Web) | TaskProgressCard 组件 | SubtaskOutputPanel 组件 | UI 按钮 |
| CLI | 纯文本进度条（MasterAgent 输出） | JSON 文本输出 | 对话指令 |
| IM (Telegram等) | 纯文本进度（MasterAgent 输出） | JSON 文本输出 | 对话指令 |

CLI/IM 通道不渲染前端组件，所有信息通过 MasterAgent 的回复文本传达。
SSE 事件（bp_progress 等）在无 event_bus 时静默跳过（已有 `if event_bus is not None` 防护）。
```

---

## m2. 消除 BPToolHandler 对私有成员的访问

### 改进方案

在 `BPEngine` 上添加代理属性：

```python
class BPEngine:
    @property
    def state_manager(self) -> BPStateManager:
        """公开 state_manager 的只读访问"""
        return self._state_manager
```

或者修改 `get_bp_engine()` 返回元组：

```python
# bestpractice/__init__.py
def get_bp_engine() -> BPEngine:
    ...

def get_bp_state_manager() -> BPStateManager:
    """返回共享的 BPStateManager 单例"""
    engine = get_bp_engine()
    return engine.state_manager
```

Handler 改为：

```python
def _ensure_deps(self):
    if self._engine is None:
        from ...bestpractice import get_bp_engine
        self._engine = get_bp_engine()
        self._state = self._engine.state_manager  # 通过公开属性访问
```

---

## m3. SubtaskConfig.description 非空校验

已在 M12 的 `_validate` 方法中处理。同时修改 `subtask_instruction.md`：

```markdown
## 任务说明
$subtask_description
```

engine 中渲染时做 fallback：

```python
message = self._prompt_loader.render(
    "subtask_instruction",
    ...
    subtask_description=subtask.description or f"执行「{subtask.name}」任务",
    ...
)
```

---

## m4. 引入 jsonschema 验证

### 改进方案

在 `schema_chain.py` 中可选使用 `jsonschema` 库：

```python
class SchemaChain:
    @staticmethod
    def validate_input(schema: dict, data: dict) -> list[str]:
        """验证输入数据是否满足 Schema。优先使用 jsonschema 库，降级为简单检查。"""
        try:
            import jsonschema
            try:
                jsonschema.validate(data, schema)
                return []
            except jsonschema.ValidationError as e:
                return [e.message]
        except ImportError:
            # 降级：仅检查 required 字段
            required = schema.get("required", [])
            if isinstance(required, list):
                return [f"Missing required field: {f}" for f in required if f not in data]
            props = schema.get("properties", {})
            return [
                f"Missing required field: {k}"
                for k, v in props.items()
                if v.get("required") is True and k not in data
            ]
```

在 `pyproject.toml` 中将 `jsonschema` 添加为可选依赖（bestpractice extras）。

---

## m5. 多 cache breakpoint 支持

### 改进方案

需要在 `prompt_assembler.py` 中支持标记 cache breakpoint 位置。

```python
# prompt_assembler.py
class PromptAssembler:
    # 新增：cache breakpoint 标记
    CACHE_BREAKPOINT_MARKER = "\n<!-- CACHE_BREAKPOINT -->\n"

    def build_system_prompt(self, ...) -> str:
        ...
        # 在 BP_STATIC 段末尾添加 cache breakpoint 标记
        # LLM client 层解析此标记，转换为 API 的 cache_control 参数
        bp_static_with_cache = bp_static + self.CACHE_BREAKPOINT_MARKER if bp_static else ""

        return f"""{base_prompt}
{system_info}
{self.CACHE_BREAKPOINT_MARKER}
{env_snapshot}
{skill_catalog}
{mcp_catalog}
{memory_context}
{tools_text}
{tools_guide}
{core_principles}
{bp_static_with_cache}
{bp_dynamic}
{profile_prompt}"""
```

LLM client 层需要解析 `<!-- CACHE_BREAKPOINT -->` 标记，将系统提示拆分为多个 content block，并在每个标记位置设置 `cache_control: {"type": "ephemeral"}`。

> **注意**：这需要确认当前 LLM client 是否已支持 Anthropic 的 prompt caching API。如果尚未支持，这是一个独立的 feature，不应阻塞 BP 开发。标记 TODO 即可。

---

## 修改影响汇总

### 新增文件

| 文件 | 内容 |
|------|------|
| `api/routes/seecrab.py` (endpoint) | `GET /api/bp/status/{conversation_id}` 全量状态同步 |

### 删除文件

| 文件 | 原因 |
|------|------|
| `bestpractice/trigger.py` | M4: 删除 BPTriggerDetector，纯 LLM 触发 |
| `bestpractice/prompts/intent_router.md` | M3: 合并到 system_dynamic |

### 修改文件

| 文件 | 改动点 |
|------|--------|
| `bestpractice/config.py` | 新增 PendingContextSwitch; SubtaskConfig 新增 timeout/retries; MAX_SCHEMA_DEPTH |
| `bestpractice/state_manager.py` | 新增 persist/restore; pending switch; cooldown; 删除 master_messages_snapshot; 新增 initial_input |
| `bestpractice/engine.py` | execute_subtask 去递归; 重试逻辑; restore_session; format_progress_text; validate_output_soft 返回值; state_manager 公开属性 |
| `bestpractice/context_bridge.py` | 新增 execute_pending_switch; _fallback_summary |
| `bestpractice/prompts/system_static.md` | auto 模式指令; 中断规则; Chat-to-Edit 确认规则 |
| `bestpractice/prompts/system_dynamic.md` | 合并 intent_router; cooldown 提示; 新增 $intent_routing_instruction 变量 |
| `bestpractice/prompts/chat_to_edit.md` | 数组语义说明; 示例 |
| `bestpractice/__init__.py` | 新增 get_bp_state_manager |
| `tools/handlers/bestpractice.py` | instance_id 可选化; _resolve_instance_id; 持久化调用; 校验结果传达 |
| `core/agent.py` | _prepare_session_context 添加 BP 钩子点 |
| `core/prompt_assembler.py` | cache breakpoint marker (TODO) |

### 需求文档修改

| 章节 | 改动 |
|------|------|
| §1.2 触发与状态 | TriggerType 不扩展，改为 BP 独立机制 |
| §1.1 核心业务概念 | BPStateManager.create_instance 返回 BPInstanceSnapshot |

### 存储设计修改

| 章节 | 改动 |
|------|------|
| §3.1 存储层级 | BPStateManager 改为"内存 + Session.metadata 持久化" |
| §3.3 BPInstanceSnapshot | 删除 master_messages_snapshot; 新增 initial_input; bp_config 标注可空原因 |
| §5.2 故障恢复 | 更新为"进程崩溃后从 sessions.json 恢复" |
| §5.3 持久化方案 | 从"预留"改为"V1 实现" |

---

## 技术设计 §15 决策表更新

| 决策 | 原选择 | 更新后 | 理由 |
|------|--------|--------|------|
| 自动模式实现 | B) 工具内递归 | **A) MasterAgent 驱动** | 避免阻塞 ReAct 循环，保持用户控制能力 |
| 触发检测 | B) 规则预筛 + LLM 确认 | **A) 纯 LLM** | LLM 已具备完整上下文，TriggerDetector 无明确传递机制 |
| 状态存储 | C) 独立 BPStateManager (内存) | **C) 独立 BPStateManager (内存 + Session.metadata 持久化)** | V1 必须支持进程重启恢复 |
| 上下文切换 | B) 清空替换（工具内） | **B) 清空替换（钩子点）** | 在安全时机执行，避免工具执行期间修改 Brain.Context |

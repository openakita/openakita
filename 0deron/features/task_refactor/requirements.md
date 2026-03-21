# BP 架构重构需求说明

> 日期: 2026-03-21
> 状态: 待确认

---

## 一、核心变更：BP 编排控制权转移

**从**: MasterAgent (LLM) 控制 BP 全流程
**到**: BPEngine (确定性代码) 控制编排，MasterAgent 只做意图理解

---

## 二、具体需求项

### R1. TaskScheduler 抽象层

- 新建 `scheduler.py`，基类 `TaskScheduler` + 派生 `LinearScheduler`
- 本次只实现线性调度，DAG 不做，但接口预留
- `resolve_input()` 支持 `input_mapping`（DAG 就绪）和线性 fallback（前一个子任务输出）
- `derive_output_schema()` 从 `schema_chain.py` 合并过来

### R2. BPEngine 新增 `advance()` 异步生成器

- 替代现有 `execute_subtask()`，直接 yield SSE 事件
- auto 模式：内部 while 循环，连续执行直到全部完成
- manual 模式：执行 1 个子任务后 yield `bp_waiting_next`，等前端调 `/bp/next`
- SubAgent 执行通过现有 `orchestrator.delegate()` + 临时 event_bus 捕获流式事件

### R3. BPEngine 新增 `answer()` 方法

- 处理 SubAgent ask_user 的回答
- 合并补充数据到子任务输入，重置子任务状态为 PENDING
- 内部复用 `advance()` 重新执行同一子任务

### R4. 新增 3 个 SSE 流端点

| 端点 | 触发者 | 作用 |
|------|--------|------|
| `POST /api/bp/start` | 前端点击"使用最佳实践" | 创建实例 + 执行第一个子任务 |
| `POST /api/bp/next` | 前端点击"进入下一步" | 推进到下一子任务 |
| `POST /api/bp/answer` | 前端提交补充数据 | 合并数据 + 重新执行当前子任务 |

### R5. 新增 2 个普通端点

| 端点 | 作用 |
|------|------|
| `GET /api/bp/output/{instance_id}/{subtask_id}` | 查询子任务输出 |
| `DELETE /api/bp/{instance_id}` | 取消 BP 实例 |

已有端点不变：`GET /api/bp/status`、`PUT /api/bp/run-mode`、`PUT /api/bp/edit-output`

### R6. MasterAgent 工具从 7 个减到 3 个

**保留:**

| 工具 | 理由 |
|------|------|
| `bp_start` | 需要 LLM 理解用户意图、提取参数。只创建实例，不执行子任务，通过 SSE event_bus 通知前端接管 |
| `bp_edit_output` | 需要 LLM 将自然语言转为结构化 changes |
| `bp_switch_task` | 需要 LLM 判断目标任务 |

**删除:**

| 工具 | 替代 |
|------|------|
| `bp_continue` | `POST /bp/next` |
| `bp_get_output` | `GET /bp/output/{id}/{subtask_id}` |
| `bp_cancel` | `DELETE /bp/{id}` |
| `bp_supplement_input` | `POST /bp/answer` |

### R7. SubtaskStatus 新增 `WAITING_INPUT`

- SubAgent 检测输入不足时，子任务进入此状态
- 实例级 BPStatus 不变（仍为 ACTIVE）

### R8. SubAgent ask_user 机制

- SubAgent 无状态：检测到缺字段 → BPEngine yield `bp_ask_user` → SubAgent 销毁
- 用户填写后 → `POST /bp/answer` → 合并数据 → 重建 SubAgent 重新执行
- 输入完整性由 SubAgent LLM 判断，不做确定性 schema 校验

### R9. return_direct 机制清理

- 迁移完成后移除 `reasoning_engine.py` 和 `tool_executor.py` 中的 `_return_direct` 逻辑
- 因为 `bp_continue` 已不存在，不再需要强制终止 ReAct 循环

---

## 三、不变的部分

- `BPStateManager` — 核心逻辑不变
- `ContextBridge` — 任务切换逻辑不变
- `orchestrator.delegate()` — SubAgent 执行机制不变
- `SeeCrabAdapter` — `/chat` 的 SSE 适配不变
- BP 触发检测 (`match_bp_from_message`) — 不变
- BP 配置加载 (`BPConfigLoader`) — 不变

---

## 四、实施顺序

1. **Phase 1 新建**：scheduler.py → engine.py 新增方法 → bestpractice.py 新端点（不破坏现有）
2. **Phase 2 前端切换**：前端改调新端点
3. **Phase 3 清理**：删旧工具、旧方法、return_direct

# SeeCrab Reply State Persistence & deliver_artifacts Cleanup

**Date**: 2026-03-16
**Status**: Draft
**Scope**: SeeCrab webapp (apps/seecrab) + SeeCrab API adapter (src/seeagent/api/)

---

## 1. Problem Statement

### 1.1 deliver_artifacts 不适用于 webapp

`deliver_artifacts` 是 Agent 内部的文件交付工具，设计用于 IM 通道（如 Telegram）发送文件。
SeeCrab webapp 没有文件接收能力，该工具在 webapp 上下文中无意义。当前代码中存在以下残留：

| 层级 | 文件 | 位置 |
|------|------|------|
| 后端 whitelist | `seeagent/api/adapters/seecrab_models.py` | `StepFilterConfig.whitelist` |
| 后端 card 映射 | `seeagent/api/adapters/card_builder.py` | `CARD_TYPE_MAP["deliver_artifacts"]` |
| 后端 title 映射 | `seeagent/api/adapters/title_generator.py` | `HUMANIZE_MAP["deliver_artifacts"]` |
| 后端 adapter | `seeagent/api/adapters/seecrab_adapter.py` | `_handle_tool_call_end()` 中的 artifact 合成 |
| 前端 types | `apps/seecrab/src/types/index.ts` | `Artifact` 接口 + `ReplyState.artifacts` |
| 前端 store | `apps/seecrab/src/stores/chat.ts` | `dispatchEvent()` 中 `artifact` 事件处理 |

### 1.2 页面刷新后步骤丢失

**现状**：后端只持久化 assistant 回复的纯文本 (`full_reply`)。
thinking、step_cards、plan_checklist 等结构化数据仅存在于 SSE 流中。

**数据流**：
```
SSE stream → dispatchEvent() → currentReply (内存)
                                    ↓ (done 事件)
                               messages[] (内存)
                                    ↓
                               页面刷新 → 全部丢失
```

**恢复流**：
```
GET /sessions/{id} → 返回 [{role, content, timestamp}] → 无 reply 元数据
```

---

## 2. Design: Reply State Persistence

### 2.1 方案选择

| 方案 | 优点 | 缺点 |
|------|------|------|
| **A. 消息 metadata 存储** | 无新存储、原子性好、改动最小 | JSON 文件稍大 |
| B. 独立 SQLite 表 | 结构清晰、可查询 | 需新表/API/一致性处理 |
| C. 前端 IndexedDB | 不增加后端负担 | 离线源和后端不一致 |

**选择方案 A**：在现有 `session.add_message()` 的 `**metadata` 参数中携带 `reply_state`。
复用已有的 JSON 持久化链路（`SessionManager.mark_dirty()` → `sessions.json`），零新增存储。

### 2.2 存储 Key 与数据结构

#### 2.2.1 消息级存储结构

每条 assistant 消息在 `session.context.messages[]` 中的完整结构：

```python
# session.context.messages[] 中的一条 assistant 消息
{
    "role": "assistant",
    "content": "这是 AI 的最终回复文本...",
    "timestamp": "2026-03-16T14:30:00.123456",

    # 新增字段 —— reply_state
    "reply_state": {
        "thinking": str,            # 完整的 thinking 文本（可能较长）
        "step_cards": [StepCardDict],  # 所有步骤卡片
        "plan_checklist": [PlanStepDict] | None,  # 计划步骤列表（无计划时为 null）
        "timer": TimerDict,         # 计时信息
    }
}
```

#### 2.2.2 StepCardDict 结构

与 SSE 中 `step_card` 事件的 payload 保持一致（snake_case）：

```python
StepCardDict = {
    "step_id": str,          # e.g. "tool_a1b2c3d4", "skill_e5f6g7h8", "mcp_i9j0k1l2", "plan_step_3"
    "title": str,            # 人类可读标题, e.g. '搜索 "Python async"'
    "status": str,           # "completed" | "failed" (不会是 "running"，因为已完成)
    "source_type": str,      # "tool" | "skill" | "mcp" | "plan_step"
    "card_type": str,        # "search" | "code" | "file" | "analysis" | "browser" | "default"
    "duration": float | None, # 耗时（秒），如 1.234
    "plan_step_index": int | None,  # 如果属于某个 plan step，其索引号
    "agent_id": str,         # "main" 或子 agent ID
    "input": dict | None,    # 工具输入参数（已截断）
    "output": str | None,    # 工具输出（已截断，最大 2000 字符）
    "absorbed_calls": [AbsorbedCallDict],  # 被聚合的子调用
}
```

#### 2.2.3 AbsorbedCallDict 结构

```python
AbsorbedCallDict = {
    "tool": str,              # 工具名称
    "tool_id": str,           # 工具调用 ID
    "args": dict,             # 调用参数
    "result": str | None,     # 结果（截断到 2000 字符）
    "is_error": bool | None,  # 是否出错
    "duration": float | None, # 耗时
}
```

#### 2.2.4 PlanStepDict 结构

```python
PlanStepDict = {
    "index": int,            # 步骤序号, 从 1 开始
    "title": str,            # 步骤标题
    "status": str,           # "pending" | "completed" | "failed"
}
```

#### 2.2.5 TimerDict 结构

```python
TimerDict = {
    "ttft": float | None,    # Time To First Token (秒)
    "total": float | None,   # 总耗时 (秒)
}
```

#### 2.2.6 完整示例

```json
{
    "role": "assistant",
    "content": "根据搜索结果，Python 3.12 的主要新特性包括...",
    "timestamp": "2026-03-16T14:30:05.678",
    "reply_state": {
        "thinking": "用户想了解 Python 3.12 新特性，我需要搜索最新资料...",
        "step_cards": [
            {
                "step_id": "tool_a1b2c3d4",
                "title": "搜索 \"Python 3.12 new features\"",
                "status": "completed",
                "source_type": "tool",
                "card_type": "search",
                "duration": 2.34,
                "plan_step_index": null,
                "agent_id": "main",
                "input": {"query": "Python 3.12 new features"},
                "output": "Found 10 results...",
                "absorbed_calls": []
            },
            {
                "step_id": "skill_e5f6g7h8",
                "title": "分析搜索结果",
                "status": "completed",
                "source_type": "skill",
                "card_type": "default",
                "duration": 5.67,
                "plan_step_index": null,
                "agent_id": "main",
                "input": null,
                "output": null,
                "absorbed_calls": [
                    {
                        "tool": "read_file",
                        "tool_id": "tc_001",
                        "args": {"path": "/tmp/results.txt"},
                        "result": "...",
                        "is_error": false,
                        "duration": 0.12
                    }
                ]
            }
        ],
        "plan_checklist": null,
        "timer": {
            "ttft": 0.85,
            "total": 12.34
        }
    }
}
```

### 2.3 数据量评估

| 场景 | 单条消息 reply_state 大小 | 100 条消息会话 |
|------|---------------------------|---------------|
| 简单问答（无工具） | ~100B (thinking only) | ~10KB |
| 1-2 个工具调用 | ~2-3KB | ~250KB |
| 复杂计划 (5+ 步骤) | ~5-10KB | ~500KB |
| 极端情况 (10+ 步骤 + absorbed) | ~15-20KB | ~1MB |

在 JSON 文件存储中完全可接受。`sessions.json` 当前单文件已含全部消息文本。

---

## 3. Implementation: Backend Changes

### 3.1 收集侧 — `seecrab.py` generate() 函数

在 SSE 流遍历过程中累积 reply_state：

```python
# seecrab.py generate() 内部 — 新增收集逻辑
reply_state = {
    "thinking": "",
    "step_cards": [],
    "plan_checklist": None,
    "timer": {"ttft": None, "total": None},
}

async for event in adapter.transform(raw_stream, reply_id=reply_id):
    if disconnect_event.is_set():
        break
    payload = json.dumps(event, ensure_ascii=False)
    yield f"data: {payload}\n\n"

    # 收集 reply_state
    etype = event.get("type")
    if etype == "ai_text":
        full_reply += event.get("content", "")
    elif etype == "thinking":
        reply_state["thinking"] += event.get("content", "")
    elif etype == "step_card":
        _upsert_step_card(reply_state["step_cards"], event)
    elif etype == "plan_checklist":
        reply_state["plan_checklist"] = event.get("steps")
    elif etype == "timer_update":
        phase = event.get("phase")  # "ttft" | "total"
        if phase in reply_state["timer"] and event.get("state") == "done":
            reply_state["timer"][phase] = event.get("value")

# 保存时携带 reply_state
if session and full_reply:
    session.add_message("assistant", full_reply, reply_state=reply_state)
    session_manager.mark_dirty()
```

`_upsert_step_card` 辅助函数：

```python
def _upsert_step_card(cards: list[dict], event: dict) -> None:
    """按 step_id 更新或追加 step_card。"""
    step_id = event.get("step_id")
    for i, c in enumerate(cards):
        if c.get("step_id") == step_id:
            cards[i] = {k: v for k, v in event.items() if k != "type"}
            return
    cards.append({k: v for k, v in event.items() if k != "type"})
```

### 3.2 读取侧 — `seecrab.py` GET /sessions/{session_id}

```python
# 现有代码增加 reply_state 字段
messages.append({
    "role": m.get("role", ""),
    "content": m.get("content", ""),
    "timestamp": m.get("timestamp", 0),
    "metadata": m.get("metadata", {}),
    "reply_state": m.get("reply_state"),  # 新增
})
```

### 3.3 序列化兼容性

`SessionContext.add_message()` 已通过 `**metadata` 接受任意额外字段：

```python
def add_message(self, role: str, content: str, **metadata) -> None:
    self.messages.append(
        {"role": role, "content": content, "timestamp": ..., **metadata}
    )
```

`reply_state` 作为 `**metadata` 的一部分，自动随 `messages` 列表序列化到 JSON。
无需修改 `SessionContext.to_dict()` / `from_dict()`。

---

## 4. Implementation: Frontend Changes

### 4.1 恢复侧 — `chat.ts` _mapHistoryMessages

```typescript
function _mapHistoryMessages(rawMessages: any[]): Message[] {
  return rawMessages.map((m: any, i: number) => {
    const ts = _parseTimestamp(m.timestamp)
    const msg: Message = {
      id: `${m.role}_${ts}_${i}`,
      role: m.role,
      content: m.content || '',
      timestamp: ts,
    }
    if (m.role === 'assistant' && m.content) {
      const rs = m.reply_state  // 从后端获取
      msg.reply = {
        replyId: msg.id,
        agentId: 'main',
        agentName: 'OpenCrab',
        thinking: rs?.thinking ?? '',
        thinkingDone: true,
        planChecklist: rs?.plan_checklist ?? null,
        stepCards: (rs?.step_cards ?? []).map(_mapStepCard),
        summaryText: m.content,
        timer: {
          ttft: { state: 'done', value: rs?.timer?.ttft ?? null },
          total: { state: 'done', value: rs?.timer?.total ?? null },
        },
        askUser: null,
        artifacts: [],
        isDone: true,
      }
    }
    return msg
  })
}

function _mapStepCard(raw: any): StepCard {
  return {
    stepId: raw.step_id,
    title: raw.title,
    status: raw.status,
    sourceType: raw.source_type,
    cardType: raw.card_type,
    duration: raw.duration ?? null,
    planStepIndex: raw.plan_step_index ?? null,
    agentId: raw.agent_id ?? 'main',
    input: raw.input ?? null,
    output: raw.output ?? null,
    absorbedCalls: raw.absorbed_calls ?? [],
  }
}
```

### 4.2 类型清理 — 移除 Artifact 相关

从 `types/index.ts` 中移除：

```typescript
// 删除
export interface Artifact { ... }

// ReplyState 中移除
artifacts: Artifact[]
```

从 `chat.ts` 中移除：

```typescript
// dispatchEvent() 中删除
case 'artifact':
  reply.artifacts.push(event as any)
  break

// startNewReply() 中删除
artifacts: [],

// _mapHistoryMessages() 中删除
artifacts: [],
```

从 `types/index.ts` SSEEventType 中移除 `'artifact'`。

---

## 5. Implementation: deliver_artifacts Cleanup

### 5.1 后端清理清单

| 文件 | 改动 |
|------|------|
| `seecrab_models.py` | 从 `StepFilterConfig.whitelist` 移除 `"deliver_artifacts"` |
| `card_builder.py` | 从 `CARD_TYPE_MAP` 移除 `"deliver_artifacts": "file"` |
| `title_generator.py` | 从 `HUMANIZE_MAP` 移除 `"deliver_artifacts"` 条目 |
| `seecrab_adapter.py` | 删除 `_handle_tool_call_end()` 中的 `deliver_artifacts` 判断 (L193-198) 及 `_extract_artifact()` 方法 (L215-233) |

### 5.2 前端清理清单

| 文件 | 改动 |
|------|------|
| `types/index.ts` | 删除 `Artifact` 接口；从 `SSEEventType` 移除 `'artifact'`；从 `ReplyState` 移除 `artifacts` |
| `stores/chat.ts` | 删除 `artifact` 事件处理分支；从 `startNewReply()` 和 `_mapHistoryMessages()` 移除 `artifacts` |

---

## 6. File Change Summary

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/seeagent/api/routes/seecrab.py` | 修改 | 收集 reply_state + 传入 add_message + GET 接口返回 |
| `src/seeagent/api/adapters/seecrab_adapter.py` | 修改 | 移除 deliver_artifacts artifact 合成 |
| `src/seeagent/api/adapters/seecrab_models.py` | 修改 | whitelist 移除 deliver_artifacts |
| `src/seeagent/api/adapters/card_builder.py` | 修改 | CARD_TYPE_MAP 移除 deliver_artifacts |
| `src/seeagent/api/adapters/title_generator.py` | 修改 | HUMANIZE_MAP 移除 deliver_artifacts |
| `apps/seecrab/src/types/index.ts` | 修改 | 移除 Artifact，清理 ReplyState |
| `apps/seecrab/src/stores/chat.ts` | 修改 | 移除 artifact 处理 + 增强 _mapHistoryMessages |

**总计**：7 个文件修改，0 个新文件。

---

## 7. Migration & Compatibility

- **向后兼容**：旧消息没有 `reply_state` 字段，前端用 `rs?.thinking ?? ''` 等默认值处理，效果与当前相同（空步骤）
- **无数据迁移**：不需要修改已有数据，新消息自然带上 reply_state
- **版本回退安全**：旧版后端忽略 `reply_state`（它只是 message dict 里的一个额外 key）

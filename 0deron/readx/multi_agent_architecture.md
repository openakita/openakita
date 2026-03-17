# SeeAgent 多 Agent 架构逻辑梳理

本文档基于对 `src/seeagent` 代码库的深入分析，整理了 MasterAgent、AgentToolHandler、AgentOrchestrator 及各类 Agent 之间的协作逻辑与系统架构。

## 1. 核心组件概览

整个多 Agent 系统可以类比为一个**厨房管理系统**：

| 组件 | 角色 | 职责 (Responsibilities) | 关键代码位置 |
| :--- | :--- | :--- | :--- |
| **Master Agent** | **餐厅经理 (Host)** | 系统的主入口，负责与用户直接交互。它是工具的载体，也是任务的发起者。 | `src/seeagent/core/agent.py` |
| **AgentToolHandler** | **传菜员 (Interface)** | **工具层的浅层封装**。负责接收 LLM 的指令（点单），进行校验和鉴权，然后转交给后台。 | `src/seeagent/tools/handlers/agent.py` |
| **AgentOrchestrator** | **厨房调度长 (Engine)** | **系统的核心引擎**。负责资源调度、Agent 生命周期管理、监控进度、处理超时与故障降级。 | `src/seeagent/agents/orchestrator.py` |
| **Sub-Agent** | **具体厨师 (Worker)** | 实际执行任务的 Agent 实例。拥有独立的人设、Prompt 和工具箱，但在 Orchestrator 的监控下工作。 | `src/seeagent/agents/factory.py` |

---

## 2. 组件逻辑关系与调用流

它们之间的关系是单向依赖的层级结构：

```
+--------+       +--------------+       +-------+
|  User  | ----> | Master Agent | ----> |  LLM  |
+--------+       +--------------+       +-------+
                                            |
                                            | Output Tool Call
                                            v
                         +-----------------------------------+
                         | AgentToolHandler (Interface)      |
                         |                                   |
                         |  +-----------------------------+  |
                         |  | 1. Validate Params          |  |
                         |  +-----------------------------+  |
                         |               |                   |
                         |               v                   |
                         |  +-----------------------------+  |
                         |  | 2. Check: Is Sub-Agent?     |  |
                         |  +-------------+---------------+  |
                         |     Yes /      \ No               |
                         |        v        v                 |
                         |  [Reject]    [Dispatch]           |
                         +-----------------+-----------------+
                                           |
                                           v
                         +-----------------------------------+
                         | AgentOrchestrator (Engine)        |
                         |                                   |
                         |  +-----------------------------+  |
                         |  | 1. Get/Create Instance      |  |
                         |  | (AgentInstancePool)         |  |
                         |  +-----------------------------+  |
                         |               |                   |
                         |               v                   |
                         |  +-----------------------------+  |
                         |  | 2. Start Progress Monitor   |  |
                         |  +-----------------------------+  |
                         |               |                   |
                         |               v                   |
                         |  +-----------------------------+  |
                         |  | 3. Execute Task             |  |
                         |  +-----------------------------+  |
                         +---------------+-------------------+
                                         |
                                         v
                         +-----------------------------------+
                         | Sub-Agent (Worker)                |
                         |                                   |
                         |  +-----------------------------+  |
                         |  |      ReAct Loop             |  |
                         |  +-------------+---------------+  |
                         |                |                  |
                         |      Failure/Timeout              |
                         |                v                  |
                         |       [Fallback Logic] -----------+
                         +-----------------------------------+
                                          |
                                          | Depth + 1
                                          v
                                 (Back to Orchestrator)
```

### 详细交互流程

1.  **Master Agent (宿主)**：
    *   启动时，若配置 `settings.multi_agent_enabled=True`，则加载 `AGENT_TOOLS`。
    *   这些工具（如 `delegate_to_agent`）被注册到 `AgentToolHandler`。

2.  **AgentToolHandler (接口)**：
    *   **被动触发**：只有当 Master Agent 决定调用工具时，Handler 才会被激活。
    *   **上下文依赖**：它没有独立的上下文，完全依赖 Master Agent 的 Session。
    *   **职责边界**：只负责“接单”，不负责“做菜”。它通过 `_get_orchestrator()` 获取全局单例，调用 `orchestrator.delegate(...)`。

3.  **AgentOrchestrator (引擎)**：
    *   **不是 Agent**：它没有 Agent Loop（思考循环），只有 **Progress Monitoring Loop**（监控循环）。
    *   **全局单例**：在系统进程中只有一个实例，管理所有 Session 的所有 Sub-Agent。
    *   **生命周期管理**：
        *   **Spawn**：从 `AgentInstancePool` 中唤醒或创建 Agent。
        *   **Monitor**：每 3 秒检查一次 Sub-Agent 是否卡死或空转。
        *   **Kill**：如果超时，强制取消 `asyncio.Task`。

---

## 3. 委派深度 (Delegation Depth) 逻辑

系统通过 `depth` 参数控制委派层级，防止无限递归。

| 场景 | Depth 变化 | 说明 |
| :--- | :--- | :--- |
| **Master -> Sub-Agent** | `depth = 1` | 正常的主动委派。Master (depth=0) 调用工具，Orchestrator 将任务派发给 Sub-Agent。 |
| **Sub-Agent -> ...** | **被禁止** | 代码显式禁止 Sub-Agent (`_is_sub_agent_call=True`) 调用委派工具。 |
| **故障自动降级 (Fallback)** | `depth = depth + 1` | **唯一增加深度的场景**。当 Sub-Agent 失败且配置了 `fallback_profile_id` 时，Orchestrator 会自动将任务转给备用 Agent，层级加 1。 |

*   **最大深度**：`MAX_DELEGATION_DEPTH = 5`。主要用于限制故障降级链的长度，而非主动委派的层级。

---

## 4. 关键代码索引

*   **Master 加载逻辑**：
    *   [agent.py](file:///Users/zd/opencrab/src/seeagent/core/agent.py) `_init_handlers`：根据配置决定是否加载 Agent 工具。
*   **工具接口实现**：
    *   [agent.py](file:///Users/zd/opencrab/src/seeagent/tools/handlers/agent.py) `handle`：拦截子 Agent 调用，转交 Orchestrator。
*   **调度核心实现**：
    *   [orchestrator.py](file:///Users/zd/opencrab/src/seeagent/agents/orchestrator.py) `_dispatch`：处理深度检查、日志记录。
    *   [orchestrator.py](file:///Users/zd/opencrab/src/seeagent/agents/orchestrator.py) `_run_with_progress_timeout`：核心监控循环，负责超时杀死。

## 5. 总结

*   **MasterAgent** 是大脑，决定“要不要找人帮忙”。
*   **AgentToolHandler** 是手，负责把“找人帮忙”的意图传递出去。
*   **AgentOrchestrator** 是管理者，负责找到具体的人，并盯着他把活干完。
*   **Sub-Agent** 是工人，负责干具体的活，如果干砸了（Fallback），管理者会找备胎接手。

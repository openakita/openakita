# llm_debug 抽样复盘：典型失败模式与触发条件

> 目的：基于 `data/llm_debug/llm_request_*.json` 的真实请求日志，归类可复现的失败模式，帮助验证 Prompt/工具/网关协议与切模隔离是否生效。

## 如何复现/抽样

- 运行脚本：`python scripts/analyze_llm_debug.py --limit 200`
- 手工定位：在 `data/llm_debug/` 里按时间倒序查看最新若干 `llm_request_*.json`

## 失败模式 1：CLI/非 IM 场景误用“发送工具”，导致用户感知“没发文件”

### 表现

- 模型在 **CLI** 会话中仍尝试使用 IM 发送能力（历史版本为 `send_to_chat`），但 CLI 并不会把附件交付给用户，导致用户反馈“你好像没有把文件发我”，然后模型不断解释/重试，形成交互循环。

### 触发条件

- 会话类型为 CLI 或未正确初始化 IM 网关能力。
- Prompt/文档/工具清单仍宣告可以用 `send_to_chat` 完成交付。

### 证据样本

- `data/llm_debug/llm_request_20260205_004918_f5d92c99.json`
  - `system_prompt` 中包含 CLI 规则：`send_to_chat` 在 CLI “静默成功但不发送消息”（历史行为）。
  - `messages` 里用户反馈“没有把文件发我”。

### 修复状态

- **已修复**：Prompt 侧不再提示 `send_to_chat`；IM 附件交付统一为 `deliver_artifacts`，文本由网关直转。

---

## 失败模式 2：重复交付/重复确认刷屏（交付证据缺失或去重缺失）

### 表现

- 模型不断重复发送“任务已完成/文件已发送”，且没有可验证的“交付回执”证据。
- 在 IM 场景下会造成刷屏；在 CLI 场景下会造成“宣称完成但用户收不到”的矛盾。

### 触发条件

- “完成度验证”只看“是否执行过某个工具名”，而非检查 **deliver_artifacts 回执**。
- 交付工具缺少 per-session 去重键（同一附件重复交付）。

### 证据样本

- `data/llm_debug/llm_request_20260205_004918_f5d92c99.json`
  - `get_session_logs` 的 tool_result 中可以看到多次 `Executing tool: send_to_chat ...`（历史版本）。

### 修复状态

- **已修复**：
  - `deliver_artifacts` 具备回执字段（message_id/sha256/size/dedupe_key/error_code）与会话内去重。
  - TaskVerify 以 **deliver_artifacts 成功回执** 作为“已交付”的主要证据；若模型宣称“已发送/已交付”但缺少回执，会判定 **INCOMPLETE**，避免刷屏/自循环与“口头完成”。

---

## 失败模式 3：切模/超时后上下文与工具状态误继承（stateful 工具未复核）

### 表现

- 超时触发模型/端点切换后，新模型可能继续沿用旧模型的工具上下文假设（浏览器/MCP/桌面状态），导致“工具链断裂/重复执行/误判完成”。
### 触发条件

- 切模后未插入 tool-state revalidation barrier（浏览器先 `browser_status`，MCP 先 `list_mcp_servers`，桌面先 `desktop_window/desktop_inspect`）。
### 证据样本

- `data/llm_debug/llm_request_20260205_004918_f5d92c99.json`
  - `get_session_logs` tool_result 中包含 `[TaskMonitor] Model switched: ...`（历史行为）。
### 修复状态

- **已修复**：
  - 主对话循环与 `execute_task` 任务循环在切模/故障转移后都会：**重置 messages/计数器**、丢弃旧的 tool_use/tool_result 链、并注入 **tool-state revalidation barrier**（要求先复核 browser/MCP/desktop 的状态）。
  - 任务循环使用 per-conversation 的 `conversation_id` 做临时 override，并在 `finally` 中 `restore_default_model(conversation_id=...)`，避免切模影响后续会话。
---

## 失败模式 4：两段式 Prompt 的“编译器输出污染 user messages”（上下文噪声/重复指令）

### 表现

- Prompt Compiler 的 YAML 输出被当作 user message 直接塞回主模型，导致 messages 膨胀、语义噪声、以及不必要的重复约束。
### 修复状态

- **已修复**：编译器输出只做短摘要，注入到 system/developer 的 `TaskDefinition`，并复用为 memory query。

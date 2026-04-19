# 生命周期钩子 / Lifecycle Hooks

OpenAkita 提供 **14 个钩子**，覆盖插件的完整生命周期：初始化、关闭、消息收发、会话管理、记忆检索、工具调用（含前后拦截）、Prompt 构建、定时任务、配置变更和错误处理。

OpenAkita provides **14 hooks** covering the full plugin lifecycle: init, shutdown, message I/O, sessions, memory retrieval, tool execution (with before/after interception), prompt assembly, scheduled tasks, config changes, and error handling.

```python
from openakita_plugin_sdk.hooks import HOOK_NAMES, HOOK_SIGNATURES
```

## 回调规则 / Callback Rules

- 回调可以是 `async def` 或普通 `def`（同步回调自动在线程中运行以避免阻塞事件循环）
- 所有回调使用 **关键字参数**（`**kwargs`），不使用位置参数
- 每个回调有独立的超时保护（默认 5 秒，可在 `plugin.json` 的 `hook_timeout` 中配置）
- 单个回调异常不影响其他回调的执行
- 同一钩子的多个插件回调通过 `asyncio.gather` **并行**调度，执行顺序不保证
- 5 分钟滑动窗口内累计错误达 10 次的插件会被自动禁用

- Callbacks can be `async def` or plain `def` (sync callbacks run in a thread automatically)
- All callbacks receive **keyword arguments** (`**kwargs`), never positional
- Each callback has independent timeout protection (default 5s, configurable via `hook_timeout` in `plugin.json`)
- A failing callback never blocks the chain
- Multiple plugin callbacks for the same hook are dispatched **in parallel** via `asyncio.gather`; execution order is not guaranteed
- Plugins with 10+ errors within a 5-minute sliding window are auto-disabled

---

## 总览 / Summary

| 钩子 / Hook | 触发时机 / When | 权限 / Permission | kwargs |
|-------------|----------------|-------------------|--------|
| `on_init` | 所有插件加载完成后 / After all plugins loaded | `hooks.basic` | (无 / none) |
| `on_shutdown` | 系统关闭时 / System shutting down | `hooks.basic` | (无 / none) |
| `on_schedule` | 定时任务执行前 / Before scheduled task | `hooks.basic` | `task_id` |
| `on_config_change` | 插件配置变更后 / After plugin config changed | `hooks.basic` | `plugin_id`, `config` |
| `on_error` | 插件执行出错时 / On plugin execution error | `hooks.basic` | `plugin_id`, `context`, `error` |
| `on_message_received` | 收到新消息，处理前 / New message, before processing | `hooks.message` | `channel`, `chat_id`, `user_id`, `text`, `message` |
| `on_message_sending` | 发送回复前 / Before sending reply | `hooks.message` | `channel`, `chat_id`, `text` |
| `on_session_start` | 新会话创建 / New session created | `hooks.message` | `session_id` |
| `on_session_end` | 会话关闭 / Session closed | `hooks.message` | `session_id` |
| `on_retrieve` | 记忆检索后 / After memory retrieval | `hooks.retrieve` | `query`, `candidates` |
| `on_tool_result` | 工具调用完成后 / After tool execution | `hooks.retrieve` | `tool_name`, `arguments`, `result` |
| `on_before_tool_use` | 工具调用执行前 / Before tool execution | `hooks.retrieve` | `tool_name`, `arguments` |
| `on_after_tool_use` | 工具调用执行后 / After tool execution completes | `hooks.retrieve` | `tool_name`, `arguments`, `result` |
| `on_prompt_build` | 系统 Prompt 组装后 / After prompt assembly | `hooks.retrieve` | `prompt` |

---

## 详细说明 / Detailed Reference

### `on_init`

插件加载完成后触发一次。用于建立连接、初始化状态。

Fired once after all plugins are loaded. Use for establishing connections and initial state.

```python
async def on_init(**kwargs):
    api.log("插件就绪 / Plugin ready")

api.register_hook("on_init", on_init)
```

**权限 / Permission:** `hooks.basic` | **返回值 / Return:** 忽略 / ignored

### `on_shutdown`

系统关闭或插件卸载时触发。用于释放资源、保存状态。

Fired when the system shuts down or the plugin is unloaded. Use for cleanup and state persistence.

```python
async def on_shutdown(**kwargs):
    await db.close()
    api.log("资源已释放 / Resources released")

api.register_hook("on_shutdown", on_shutdown)
```

**权限 / Permission:** `hooks.basic` | **返回值 / Return:** 忽略 / ignored

### `on_schedule`

定时任务执行前触发。用于同步数据、定期清理等。

Fired before a scheduled task executes. Use for data sync, periodic cleanup, etc.

```python
async def on_schedule(**kwargs):
    task_id = kwargs.get("task_id", "")
    api.log(f"定时任务触发 / Schedule tick: {task_id}")

api.register_hook("on_schedule", on_schedule)
```

**权限 / Permission:** `hooks.basic` | **参数 / kwargs:** `task_id: str`

### `on_config_change`

插件配置通过 REST API 或 `set_config` 更新后触发。用于动态重新加载设置。

Fired after plugin config is updated via REST API or `set_config`. Use for dynamic reload of settings.

```python
async def on_config_change(**kwargs):
    plugin_id = kwargs.get("plugin_id", "")
    config = kwargs.get("config", {})
    api.log(f"配置已更新 / Config updated for {plugin_id}")

api.register_hook("on_config_change", on_config_change)
```

**权限 / Permission:** `hooks.basic` | **参数 / kwargs:** `plugin_id: str`, `config: dict`

### `on_error`

插件执行过程中发生异常时触发。用于错误监控、告警。

Fired when a plugin encounters an execution error. Use for error monitoring and alerting.

```python
async def on_error(**kwargs):
    plugin_id = kwargs.get("plugin_id", "")
    context = kwargs.get("context", "")
    error = kwargs.get("error", "")
    api.log_error(f"插件 {plugin_id} 在 {context} 出错: {error}")

api.register_hook("on_error", on_error)
```

**权限 / Permission:** `hooks.basic` | **参数 / kwargs:** `plugin_id: str`, `context: str`, `error: str`

### `on_message_received`

收到新消息后、进入处理流程前触发。用于过滤、日志记录、消息增强。

Fired after a new message arrives from any IM channel, before core processing. Use for filtering, logging, or message enrichment.

```python
async def on_message_received(**kwargs):
    channel = kwargs.get("channel", "")      # "telegram", "feishu", ...
    chat_id = kwargs.get("chat_id", "")      # 会话 ID
    user_id = kwargs.get("user_id", "")      # 发送者 ID
    text = kwargs.get("text", "")            # 消息文本
    message = kwargs.get("message")          # UnifiedMessage 完整对象
    api.log(f"[{channel}] {user_id}: {text[:50]}")

api.register_hook("on_message_received", on_message_received)
```

**权限 / Permission:** `hooks.message`

**参数 / kwargs:**

| 参数 / Param | 类型 / Type | 说明 / Description |
|-------------|------------|-------------------|
| `channel` | `str` | 通道类型 / Channel type (`telegram`, `feishu`, etc.) |
| `chat_id` | `str` | 会话 ID / Conversation ID |
| `user_id` | `str` | 发送者 ID / Sender user ID |
| `text` | `str` | 消息文本 / Message text |
| `message` | `UnifiedMessage` | 完整消息对象 / Full message object |

### `on_message_sending`

回复消息发送前触发。用于修改回复内容、添加签名、日志记录。

Fired just before a response is sent back to the user. Use for modifying replies, adding signatures, or logging.

```python
async def on_message_sending(**kwargs):
    channel = kwargs.get("channel", "")
    text = kwargs.get("text", "")
    api.log(f"即将发送 / About to send: {text[:50]}")

api.register_hook("on_message_sending", on_message_sending)
```

**权限 / Permission:** `hooks.message`

**参数 / kwargs:** `channel: str`, `chat_id: str`, `text: str`

### `on_session_start`

新的对话会话创建时触发。

Fired when a new conversation session is created.

```python
async def on_session_start(**kwargs):
    session_id = kwargs.get("session_id", "")
    api.log(f"会话开始 / Session started: {session_id}")

api.register_hook("on_session_start", on_session_start)
```

**权限 / Permission:** `hooks.message` | **参数 / kwargs:** `session_id: str`

### `on_session_end`

对话会话关闭时触发。

Fired when a conversation session is closed.

```python
async def on_session_end(**kwargs):
    session_id = kwargs.get("session_id", "")
    api.set_config({"last_session": session_id})

api.register_hook("on_session_end", on_session_end)
```

**权限 / Permission:** `hooks.message` | **参数 / kwargs:** `session_id: str`

### `on_retrieve`

记忆检索完成后触发。可以观察或修改检索结果（直接修改 `candidates` 列表）。

Fired after memory retrieval. Can observe or augment candidates by mutating the list in-place.

```python
async def on_retrieve(**kwargs):
    query = kwargs.get("query", "")
    candidates = kwargs.get("candidates", [])
    # 可以向 candidates 列表追加额外结果
    # You can append additional results to candidates
    candidates.append({
        "content": f"来自插件的补充信息 / Extra info about: {query}",
        "score": 0.5,
    })

api.register_hook("on_retrieve", on_retrieve)
```

**权限 / Permission:** `hooks.retrieve`

**参数 / kwargs:** `query: str`, `candidates: list[dict]`（可变列表，直接修改即可 / mutable list, modify in-place）

### `on_tool_result`

工具调用完成后触发。用于日志记录、结果后处理、审计。

Fired after a tool call completes. Use for logging, post-processing, or auditing.

```python
async def on_tool_result(**kwargs):
    tool_name = kwargs.get("tool_name", "")
    arguments = kwargs.get("arguments", {})
    result = kwargs.get("result", "")
    api.log(f"工具 {tool_name} 返回 / Tool {tool_name} returned: {result[:100]}")

api.register_hook("on_tool_result", on_tool_result)
```

**权限 / Permission:** `hooks.retrieve`

**参数 / kwargs:** `tool_name: str`, `arguments: dict`, `result: str`

### `on_before_tool_use`

工具调用执行前触发。用于参数校验、审计、权限检查。

Fired before a tool is executed. Use for argument validation, auditing, or access control.

```python
async def on_before_tool_use(**kwargs):
    tool_name = kwargs.get("tool_name", "")
    arguments = kwargs.get("arguments", {})
    api.log(f"即将调用工具 / About to call tool: {tool_name}")

api.register_hook("on_before_tool_use", on_before_tool_use)
```

**权限 / Permission:** `hooks.retrieve`

**参数 / kwargs:** `tool_name: str`, `arguments: dict`

### `on_after_tool_use`

工具调用完成后触发（与 `on_tool_result` 类似，但更强调"调用后"语义）。用于结果后处理、日志记录。

Fired after a tool call completes (similar to `on_tool_result`, emphasizing post-execution). Use for post-processing and logging.

```python
async def on_after_tool_use(**kwargs):
    tool_name = kwargs.get("tool_name", "")
    result = kwargs.get("result", "")
    api.log(f"工具完成 / Tool done: {tool_name}, result: {result[:100]}")

api.register_hook("on_after_tool_use", on_after_tool_use)
```

**权限 / Permission:** `hooks.retrieve`

**参数 / kwargs:** `tool_name: str`, `arguments: dict`, `result: str`

### `on_prompt_build`

系统 Prompt 组装完成后触发。**返回字符串**以追加到 Prompt 中，返回 `None` 则不追加。

Fired after the system prompt is assembled. **Return a string** to append to the prompt, or `None` to skip.

```python
async def on_prompt_build(**kwargs):
    prompt = kwargs.get("prompt", "")
    # 向 Prompt 注入额外上下文
    # Inject additional context into the prompt
    return "\n\n## 来自插件的上下文 / Plugin Context\n记住用户偏好中文回复。"

api.register_hook("on_prompt_build", on_prompt_build)
```

**权限 / Permission:** `hooks.retrieve`

**参数 / kwargs:** `prompt: str`（当前系统 Prompt 文本 / current system prompt text）

**返回值 / Return:** `str | None` — 返回的文本将追加到 Prompt 末尾 / returned text is appended to prompt

---

## 编程式查询签名 / Programmatic Signature Lookup

```python
from openakita_plugin_sdk.hooks import HOOK_SIGNATURES

sig = HOOK_SIGNATURES["on_message_received"]
print(sig["description"])   # 描述 / description
print(sig["kwargs"])         # {"channel": "str — ...", "text": "str — ...", ...}
print(sig["permission"])     # "hooks.message"
```

---

## 相关文档 / Related

- [api-reference.md](api-reference.md) — `register_hook` 方法说明 / method reference
- [permissions.md](permissions.md) — `hooks.basic`, `hooks.message`, `hooks.retrieve`, `hooks.all` 权限 / permissions
- [rest-api.md](rest-api.md) — `on_config_change` 由 REST API 配置更新触发 / triggered by REST API config updates

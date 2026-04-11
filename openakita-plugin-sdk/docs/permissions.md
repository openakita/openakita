# 权限模型 / Permission Model

OpenAkita 插件使用**三级权限模型**控制对系统资源的访问。插件必须在 `plugin.json` 的 `permissions` 数组中声明所需权限。

OpenAkita plugins use a **three-tier permission model** to control access to system resources. Plugins must declare required permissions in the `permissions` array of `plugin.json`.

---

## 总览 / Overview

| 级别 / Tier | 授权方式 / Approval | 能力范围 / Scope |
|-------------|---------------------|-----------------|
| **Basic** | 安装即有 / Auto-granted | 日志、自有配置/数据、工具注册、基础钩子、技能声明 / Logging, own config/data, tools, basic hooks, skill declaration |
| **Advanced** | 用户确认 / User consent | 通道、记忆读写、LLM 注册、检索源、路由、消息发送、宿主服务访问 / Channels, memory R/W, LLM registration, retrieval, routes, messaging, host access |
| **System** | 手动确认 / Manual approval | 记忆替换、全钩子、系统配置写入 / Memory replacement, all hooks, system config write |

---

## 权限清单 / Permission Catalog

### Basic 级

| 权限 ID / Permission | API 方法 / API Method | 风险 / Risk |
|---------------------|----------------------|------------|
| `log` | `log()`, `log_error()`, `log_debug()` | 低 / Low — 写日志 |
| `config.read` | `get_config()` | 低 / Low — 读取本插件配置 |
| `config.write` | `set_config()` | 低 / Low — 写入本插件配置 |
| `data.own` | `get_data_dir()` | 低 / Low — 本插件数据目录 |
| `tools.register` | `register_tools()` | 中 / Medium — 新工具影响 AI 行为 |
| `hooks.basic` | `register_hook("on_init" / "on_shutdown" / "on_schedule" / "on_config_change" / "on_error")` | 低 / Low — 生命周期钩子 |
| `skill` | (declarative) | 低 / Low — 声明插件提供技能文件 |

### Advanced 级

安装时会向用户显示风险提示。

Risk prompts are shown to users during installation.

| 权限 ID / Permission | API 方法 / API Method | 用户提示 / User Prompt |
|---------------------|----------------------|----------------------|
| `channel.register` | `register_channel()` | "此插件将添加新的消息通道 / This plugin will add a new messaging channel" |
| `channel.send` | `send_message()` | "此插件可以通过 IM 发送消息 / This plugin can send messages via IM" |
| `memory.read` | `get_memory_manager()` 只读 / read-only | "此插件可以读取对话记忆 / This plugin can read conversation memory" |
| `memory.write` | `get_memory_manager()` 写入 / write | "此插件可以修改记忆数据 / This plugin can modify memory data" |
| `vector.access` | `get_vector_store()` | "此插件可以访问向量数据库 / This plugin can access the vector database" |
| `brain.access` | `get_brain()` | "此插件可以直接调用 AI 模型 / This plugin can directly invoke AI models" |
| `settings.read` | `get_settings()` | "此插件可以读取系统配置 / This plugin can read system settings" |
| `search.register` | `register_search_backend()` | "此插件将添加搜索引擎后端 / This plugin will add a search backend" |
| `retrieval.register` | `register_retrieval_source()` | "此插件将添加知识检索来源 / This plugin will add a retrieval source" |
| `routes.register` | `register_api_routes()` | "此插件将添加网络接口 / This plugin will add HTTP endpoints" |
| `hooks.message` | `on_message_received`, `on_message_sending`, `on_session_start`, `on_session_end` | "此插件可以拦截和观察消息 / This plugin can intercept and observe messages" |
| `hooks.retrieve` | `on_retrieve`, `on_tool_result`, `on_prompt_build`, `on_before_tool_use`, `on_after_tool_use` | "此插件可以向 AI 注入上下文 / This plugin can inject context into AI prompts" |
| `llm.register` | `register_llm_provider()`, `register_llm_registry()` | "此插件将注册新的 LLM 提供商 / This plugin will register a new LLM provider" |

### System 级

| 权限 ID / Permission | API 方法 / API Method | 说明 / Description |
|---------------------|----------------------|-------------------|
| `hooks.all` | 所有 14 个钩子 / all 14 hooks | 完整钩子访问权 / Full hook access |
| `memory.replace` | `register_memory_backend()` 替换模式 / replace mode | 替换内置记忆系统 / Replace built-in memory |
| `system.config.write` | — | 写入全局系统配置 / Write global system config |

---

## 声明权限 / Declaring Permissions

在 `plugin.json` 中声明最小必要权限集：

Declare the minimal required permission set in `plugin.json`:

```json
{
  "id": "my-plugin",
  "permissions": [
    "tools.register",
    "hooks.basic",
    "config.read",
    "channel.send"
  ]
}
```

**最佳实践 / Best Practices:**
- 只声明实际需要的权限 / Only declare what you actually need
- 权限越少，用户信任越高 / Fewer permissions = higher user trust
- Basic 权限无需用户确认，优先使用 / Basic permissions need no approval, prefer them

---

## 授权流程 / Approval Flow

```
1. 读取 plugin.json 的 permissions 列表
   Read permissions list from plugin.json
       ↓
2. Basic 权限自动授予
   Basic permissions auto-granted
       ↓
3. Advanced 权限：弹窗/CLI 提示用户确认
   Advanced permissions: UI/CLI prompt for user consent
       ↓
4. System 权限：需要手动在设置中确认
   System permissions: require manual approval in settings
       ↓
5. 已授权权限持久化到 data/plugin_state.json
   Granted permissions persisted to data/plugin_state.json
       ↓
6. 运行时每次 API 调用检查权限
   Runtime permission check on every API call
```

---

## 权限不足时的行为 / Behavior When Permission Denied

**系统采用优雅降级策略**：当插件调用了未被授权的 API 时，系统**不会抛出异常**，而是：

1. 记录一条 warning 日志
2. 将该权限加入 `_pending_permissions`，前端显示待授权提示
3. 跳过本次操作（`register_*` 类方法不执行注册，`get_*` 类方法返回 `None`）
4. 插件继续正常运行，只是缺少对应功能

**The system uses graceful degradation**: when a plugin calls an API it doesn't have permission for, the system **does not raise an exception**. Instead it:

1. Logs a warning
2. Adds the permission to `_pending_permissions` (shown in the frontend)
3. Skips the operation (`register_*` methods do nothing, `get_*` methods return `None`)
4. The plugin continues running with reduced capabilities

```python
# 示例：未授权 channel.send 时调用 send_message
# Example: calling send_message without channel.send permission
api.send_message("test", "chat1", "hello")
# WARNING: Permission 'channel.send' not granted — skipping this registration.
# 不会崩溃，消息不会发出 / No crash, message is simply not sent
```

---

## 相关文档 / Related

- [api-reference.md](api-reference.md) — 每个方法对应的权限 / Permission for each method
- [hooks.md](hooks.md) — 每个钩子对应的权限 / Permission for each hook
- [plugin-json.md](plugin-json.md) — `permissions` 字段格式 / permissions field format
- [rest-api.md](rest-api.md) — 权限批准/撤销 API / Permission approve/revoke API
- [getting-started.md](getting-started.md) — 声明权限的最佳实践 / Best practices for declaring permissions

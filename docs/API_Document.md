# OpenAkita API

OpenAkita HTTP API，用于对话、健康检查、技能管理等。

## /api/system-info
**方法:** `GET`  
**摘要:** 获取系统信息  
**描述:** 返回系统环境信息，用于在 Bug 报告表单中显示。

### 响应
- **200**: 成功响应

---

## /api/bug-report
**方法:** `POST`  
**摘要:** 提交 Bug 报告  
**描述:** 提交包含系统信息、日志和 LLM 调试文件的 Bug 报告。

### 请求体
**Content-Type:** `multipart/form-data`
- **title** (string) (必填)
- **description** (string) (必填)
- **turnstile_token** (string) (必填)
- **steps** (string)
- **upload_logs** (boolean)
- **upload_debug** (boolean)
- **images** (any)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/feature-request
**方法:** `POST`  
**摘要:** 提交功能建议  
**描述:** 提交功能/需求建议，包含可选的联系方式和附件。

### 请求体
**Content-Type:** `multipart/form-data`
- **title** (string) (必填)
- **description** (string) (必填)
- **turnstile_token** (string) (必填)
- **contact_email** (string)
- **contact_wechat** (string)
- **images** (any)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/chat
**方法:** `POST`  
**摘要:** 对话  
**描述:** SSE 流式对话接口。

使用完整的 Agent 管道（与 IM/CLI 通道共享），
通过 Agent.chat_with_session_stream() 实现。

返回包含以下事件类型的 Server-Sent Events：
- thinking_start / thinking_delta / thinking_end
- text_delta
- tool_call_start / tool_call_end
- plan_created / plan_step_updated
- ask_user
- agent_switch
- error
- done

### 请求体
**Content-Type:** `application/json`
- **message** (string) - 用户消息文本
- **conversation_id** (any) - 会话 ID（上下文）
- **plan_mode** (boolean) - 强制计划模式
- **endpoint** (any) - 指定端点名称（null=自动）
- **attachments** (any) - 附件文件/图片
- **thinking_mode** (any) - 思考模式覆盖：'auto'(系统决定), 'on'(强制开启), 'off'(强制关闭)。null=使用系统默认。
- **thinking_depth** (any) - 思考深度：'low', 'medium', 'high'。仅在思考模式开启时有效。

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/chat/answer
**方法:** `POST`  
**摘要:** 回复对话  
**描述:** 处理用户对 ask_user 事件的回答。

### 请求体
**Content-Type:** `application/json`
- **conversation_id** (any)
- **answer** (string)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/chat/cancel
**方法:** `POST`  
**摘要:** 取消对话  
**描述:** 全局取消当前正在运行的任务。

### 请求体
**Content-Type:** `application/json`
- **conversation_id** (any) - 会话 ID
- **reason** (string) - 控制操作的原因
- **message** (string) - 用户消息（仅用于插入）

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/chat/skip
**方法:** `POST`  
**摘要:** 跳过步骤  
**描述:** 跳过当前正在运行的工具/步骤（不终止任务）。

### 请求体
**Content-Type:** `application/json`
- **conversation_id** (any) - 会话 ID
- **reason** (string) - 控制操作的原因
- **message** (string) - 用户消息（仅用于插入）

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/chat/insert
**方法:** `POST`  
**摘要:** 插入消息  
**描述:** 将用户消息插入到正在运行的任务上下文中。

智能路由：如果消息是停止/跳过命令，自动委托给 cancel/skip 接口，而不是盲目插入。

### 请求体
**Content-Type:** `application/json`
- **conversation_id** (any) - 会话 ID
- **reason** (string) - 控制操作的原因
- **message** (string) - 用户消息（仅用于插入）

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/models
**方法:** `GET`  
**摘要:** 获取模型列表  
**描述:** 列出可用的 LLM 端点/模型。

### 响应
- **200**: 成功响应

---

## /api/config/workspace-info
**方法:** `GET`  
**摘要:** 工作区信息  
**描述:** 返回当前工作区路径和基本信息。

### 响应
- **200**: 成功响应

---

## /api/config/env
**方法:** `GET`  
**摘要:** 读取环境变量  
**描述:** 读取 .env 文件内容为键值对。

### 响应
- **200**: 成功响应

---

## /api/config/env
**方法:** `POST`  
**摘要:** 写入环境变量  
**描述:** 更新 .env 文件中的键值对（合并模式，保留注释）。

### 请求体
**Content-Type:** `application/json`
- **entries** (object) (必填)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/config/endpoints
**方法:** `GET`  
**摘要:** Read Endpoints  
**描述:** 读取 data/llm_endpoints.json 配置文件。

### 响应
- **200**: 成功响应

---

## /api/config/endpoints
**方法:** `POST`  
**摘要:** Write Endpoints  
**描述:** 写入 data/llm_endpoints.json 配置文件。

### 请求体
**Content-Type:** `application/json`
- **content** (object) (必填)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/config/reload
**方法:** `POST`  
**摘要:** 重载配置  
**描述:** 从磁盘热重载 LLM 端点配置到运行中的 Agent。

This should be called after writing llm_endpoints.json so the running
service picks up changes without a full restart.

### 响应
- **200**: 成功响应

---

## /api/config/restart
**方法:** `POST`  
**摘要:** 重启服务  
**描述:** 触发服务优雅重启。

流程：设置重启标志 → 触发 shutdown_event → serve() 主循环检测标志后重新初始化。
前端应在调用后轮询 /api/health 直到服务恢复。

### 响应
- **200**: 成功响应

---

## /api/modules/refresh
**方法:** `POST`  
**摘要:** Refresh Module Paths  
**描述:** 运行时重新扫描并注入可选模块路径到 sys.path。

模块安装/卸载后调用此端点，可在不重启服务的情况下让 Python 发现新模块。
注意：某些模块（如依赖 torch 的 whisper）可能仍需完整重启才能正确加载 DLL。

### 响应
- **200**: 成功响应

---

## /api/config/skills
**方法:** `GET`  
**摘要:** 读取技能配置  
**描述:** 读取 data/skills.json（技能选择/白名单）。

### 响应
- **200**: 成功响应

---

## /api/config/skills
**方法:** `POST`  
**摘要:** 写入技能配置  
**描述:** 写入 data/skills.json 配置文件。

### 请求体
**Content-Type:** `application/json`
- **content** (object) (必填)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/config/disabled-views
**方法:** `GET`  
**摘要:** Read Disabled Views  
**描述:** 读取已禁用的模块视图列表。

### 响应
- **200**: 成功响应

---

## /api/config/disabled-views
**方法:** `POST`  
**摘要:** Write Disabled Views  
**描述:** 更新已禁用的模块视图列表。

### 请求体
**Content-Type:** `application/json`
- **views** (array) (必填)
  - [列表项]:

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/config/providers
**方法:** `GET`  
**摘要:** List Providers Api  
**描述:** 返回后端已注册的 LLM 服务商列表。

前端可在后端运行时通过此 API 获取最新的 provider 列表，
确保前后端数据一致。

### 响应
- **200**: 成功响应

---

## /api/config/list-models
**方法:** `POST`  
**摘要:** List Models Api  
**描述:** 拉取 LLM 端点的模型列表（远程模式替代 Tauri openakita_list_models 命令）。

直接复用 bridge.list_models 的逻辑，在后端进程内异步调用，无需 subprocess。

### 请求体
**Content-Type:** `application/json`
- **api_type** (string) (必填)
- **base_url** (string) (必填)
- **provider_slug** (any)
- **api_key** (string) (必填)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/files
**方法:** `GET`  
**摘要:** Serve File  
**描述:** 从工作区目录提供文件服务。

查询参数 `path` 可以是相对于工作区根目录的路径或绝对路径。
Example: /api/files?path=data/temp/image.png
Example: /api/files?path=D:/coder/myagent/data/temp/image.png

### 请求参数
- **path** (query, string, 可选): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/health
**方法:** `GET`  
**摘要:** Health  
**描述:** Basic health check - returns 200 if server is running.

### 响应
- **200**: 成功响应

---

## /api/health/check
**方法:** `POST`  
**摘要:** Health Check  
**描述:** Check health of a specific LLM endpoint or all endpoints.

Uses dry_run mode: sends a real test request but does NOT modify
the provider's healthy/cooldown state, ensuring no interference
with ongoing Agent LLM calls.

### 请求体
**Content-Type:** `application/json`
- **endpoint_name** (any)
- **channel** (any)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/im/channels
**方法:** `GET`  
**摘要:** 获取频道列表  
**描述:** 返回所有已配置的 IM 频道及其在线状态。

### 响应
- **200**: 成功响应

---

## /api/im/sessions
**方法:** `GET`  
**摘要:** 获取会话列表  
**描述:** 返回指定 IM 频道的会话。

### 请求参数
- **channel** (query, string, 可选): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/im/sessions/{session_id}/messages
**方法:** `GET`  
**摘要:** Get Session Messages  
**描述:** 返回指定会话的消息。

### 请求参数
- **session_id** (path, string, 必填): 
- **limit** (query, integer, 可选): 
- **offset** (query, integer, 可选): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/logs/service
**方法:** `GET`  
**摘要:** Service Log  
**描述:** 读取后端服务日志文件尾部内容。

返回格式与 Tauri openakita_service_log 命令一致：
{ path, content, truncated }

### 请求参数
- **tail_bytes** (query, integer, 可选): 读取尾部字节数

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/mcp/servers
**方法:** `GET`  
**摘要:** 获取 MCP 服务器列表  
**描述:** 列出所有 MCP 服务器及其配置和连接状态。

### 响应
- **200**: 成功响应

---

## /api/mcp/connect
**方法:** `POST`  
**摘要:** Connect Mcp Server  
**描述:** Connect to a specific MCP server.

### 请求体
**Content-Type:** `application/json`
- **server_name** (string) (必填)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/mcp/disconnect
**方法:** `POST`  
**摘要:** Disconnect Mcp Server  
**描述:** 断开与指定 MCP 服务器的连接。

### 请求体
**Content-Type:** `application/json`
- **server_name** (string) (必填)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/mcp/tools
**方法:** `GET`  
**摘要:** 获取 MCP 工具列表  
**描述:** 列出所有可用的 MCP 工具，可选择按服务器过滤。

### 请求参数
- **server** (query, string, 可选): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/mcp/instructions/{server_name}
**方法:** `GET`  
**摘要:** 获取 MCP 使用说明  
**描述:** 获取指定 MCP 服务器的 INSTRUCTIONS.md 说明文档。

### 请求参数
- **server_name** (path, string, 必填): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/mcp/servers/add
**方法:** `POST`  
**摘要:** 添加 MCP 服务器  
**描述:** 添加新的 MCP 服务器配置（持久化存储在工作区 data/mcp/servers/ 目录）。

### 请求体
**Content-Type:** `application/json`
- **name** (string) (必填)
- **transport** (string)
- **command** (string)
- **args** (array)
  - [列表项]:
- **env** (object)
- **url** (string)
- **description** (string)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/mcp/servers/{server_name}
**方法:** `DELETE`  
**摘要:** 移除 MCP 服务器  
**描述:** 移除 MCP 服务器配置（仅限工作区配置，不包含内置配置）。

### 请求参数
- **server_name** (path, string, 必填): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/memories
**方法:** `GET`  
**摘要:** 获取记忆列表  

### 请求参数
- **type** (query, string, 可选): 
- **search** (query, string, 可选): 
- **min_score** (query, number, 可选): 
- **limit** (query, integer, 可选): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/memories/stats
**方法:** `GET`  
**摘要:** 记忆统计  

### 响应
- **200**: 成功响应

---

## /api/memories/{memory_id}
**方法:** `GET`  
**摘要:** 获取单条记忆  

### 请求参数
- **memory_id** (path, string, 必填): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/memories/{memory_id}
**方法:** `PUT`  
**摘要:** Update Memory  

### 请求参数
- **memory_id** (path, string, 必填): 

### 请求体
**Content-Type:** `application/json`
- **content** (any)
- **importance_score** (any)
- **tags** (any)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/memories/{memory_id}
**方法:** `DELETE`  
**摘要:** Delete Memory  

### 请求参数
- **memory_id** (path, string, 必填): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/memories/batch-delete
**方法:** `POST`  
**摘要:** Batch Delete  

### 响应
- **200**: 成功响应

---

## /api/memories/review
**方法:** `POST`  
**摘要:** Trigger Review  
**描述:** 触发 LLM 驱动的记忆回顾（同 consolidate_memories 工具）。

### 响应
- **200**: 成功响应

---

## /api/memories/refresh-md
**方法:** `POST`  
**摘要:** Refresh Md  
**描述:** 根据当前数据库状态重建 MEMORY.md 索引文件。

### 响应
- **200**: 成功响应

---

## /api/scheduler/tasks
**方法:** `GET`  
**摘要:** 获取任务列表  
**描述:** 列出所有定时任务。

### 响应
- **200**: 成功响应

---

## /api/scheduler/tasks
**方法:** `POST`  
**摘要:** Create Task  
**描述:** Create a new scheduled task.

### 请求体
**Content-Type:** `application/json`
- **name** (string) (必填)
- **task_type** (string)
- **trigger_type** (string)
- **trigger_config** (object)
- **reminder_message** (any)
- **prompt** (string)
- **channel_id** (any)
- **chat_id** (any)
- **enabled** (boolean)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/scheduler/tasks/{task_id}
**方法:** `GET`  
**摘要:** 获取单条任务  
**描述:** 根据 ID 获取单个任务详情。

### 请求参数
- **task_id** (path, string, 必填): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/scheduler/tasks/{task_id}
**方法:** `PUT`  
**摘要:** 更新定时任务  
**描述:** 更新现有的定时任务。

### 请求参数
- **task_id** (path, string, 必填): 

### 请求体
**Content-Type:** `application/json`
- **name** (any)
- **task_type** (any)
- **trigger_type** (any)
- **trigger_config** (any)
- **reminder_message** (any)
- **prompt** (any)
- **channel_id** (any)
- **chat_id** (any)
- **enabled** (any)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/scheduler/tasks/{task_id}
**方法:** `DELETE`  
**摘要:** 删除定时任务  
**描述:** Delete a scheduled task.

### 请求参数
- **task_id** (path, string, 必填): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/scheduler/tasks/{task_id}/toggle
**方法:** `POST`  
**摘要:** Toggle Task  
**描述:** 切换任务的启用/禁用状态。

### 请求参数
- **task_id** (path, string, 必填): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/scheduler/tasks/{task_id}/trigger
**方法:** `POST`  
**摘要:** Trigger Task  
**描述:** 立即触发执行一个任务。

### 请求参数
- **task_id** (path, string, 必填): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/scheduler/channels
**方法:** `GET`  
**摘要:** 获取频道列表  
**描述:** 列出可用的 IM 频道及其 chat_id，用于定向通知。

### 响应
- **200**: 成功响应

---

## /api/scheduler/stats
**方法:** `GET`  
**摘要:** Scheduler Stats  
**描述:** 获取调度器统计信息。

### 响应
- **200**: 成功响应

---

## /api/sessions
**方法:** `GET`  
**摘要:** 获取会话列表  
**描述:** 列出指定频道的会话（默认：desktop）。

返回包含元数据的会话列表，按最后活动时间降序排列。

### 请求参数
- **channel** (query, string, 可选): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/sessions/{conversation_id}/history
**方法:** `GET`  
**摘要:** Get Session History  
**描述:** 获取指定会话的历史消息记录。

返回兼容前端 ChatMessage 类型的消息列表。

### 请求参数
- **conversation_id** (path, string, 必填): 
- **channel** (query, string, 可选): 
- **user_id** (query, string, 可选): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/skills
**方法:** `GET`  
**摘要:** 获取技能列表  
**描述:** 列出所有可用技能及其配置模式。

Returns ALL discovered skills (including disabled ones) with correct
``enabled`` status derived from ``data/skills.json`` allowlist.

### 响应
- **200**: 成功响应

---

## /api/skills/config
**方法:** `POST`  
**摘要:** 更新技能配置  
**描述:** 更新技能配置。

### 响应
- **200**: 成功响应

---

## /api/skills/install
**方法:** `POST`  
**摘要:** 安装技能  
**描述:** 安装技能（远程模式替代 Tauri openakita_install_skill 命令）。

POST body: { "url": "github:user/repo/skill" }
安装完成后自动重新加载技能并应用 allowlist。

### 响应
- **200**: 成功响应

---

## /api/skills/reload
**方法:** `POST`  
**摘要:** 重载技能  
**描述:** 热重载技能（安装新技能后、修改 SKILL.md 后、切换启用/禁用后调用）。

POST 请求体：{ "skill_name": "optional-name" }
如果 skill_name 为空或未提供，则重新扫描并加载所有技能。
全量重载后会重新读取 data/skills.json 的 allowlist 并裁剪禁用技能。

### 响应
- **200**: 成功响应

---

## /api/skills/marketplace
**方法:** `GET`  
**摘要:** Search Marketplace  
**描述:** 代理 skills.sh 搜索 API（绕过桌面应用的 CORS 限制）。

### 请求参数
- **q** (query, string, 可选): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/stats/tokens/summary
**方法:** `GET`  
**摘要:** Summary  

### 请求参数
- **group_by** (query, string, 可选): 
- **period** (query, string, 可选): 
- **start** (query, string, 可选): 
- **end** (query, string, 可选): 
- **endpoint_name** (query, string, 可选): 
- **operation_type** (query, string, 可选): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/stats/tokens/timeline
**方法:** `GET`  
**摘要:** Timeline  

### 请求参数
- **interval** (query, string, 可选): 
- **period** (query, string, 可选): 
- **start** (query, string, 可选): 
- **end** (query, string, 可选): 
- **endpoint_name** (query, string, 可选): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/stats/tokens/sessions
**方法:** `GET`  
**摘要:** Sessions  

### 请求参数
- **period** (query, string, 可选): 
- **start** (query, string, 可选): 
- **end** (query, string, 可选): 
- **limit** (query, integer, 可选): 
- **offset** (query, integer, 可选): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/stats/tokens/total
**方法:** `GET`  
**摘要:** Total  

### 请求参数
- **period** (query, string, 可选): 
- **start** (query, string, 可选): 
- **end** (query, string, 可选): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/stats/tokens/context
**方法:** `GET`  
**摘要:** Context  
**描述:** 返回当前会话的上下文 Token 使用量和限制。

### 响应
- **200**: 成功响应

---

## /api/upload
**方法:** `POST`  
**摘要:** 上传文件  
**描述:** 上传文件（图片、音频、文档）。
返回文件 URL，用于在对话消息中使用。

### 请求体
**Content-Type:** `multipart/form-data`
- **file** (string) (必填)

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /api/uploads/{filename}
**方法:** `GET`  
**摘要:** Serve Upload  
**描述:** 通过唯一文件名提供已上传文件的访问。

### 请求参数
- **filename** (path, string, 必填): 

### 响应
- **200**: 成功响应
- **422**: 校验错误

---

## /
**方法:** `GET`  
**摘要:** Root  

### 响应
- **200**: 成功响应

---

## /api/shutdown
**方法:** `POST`  
**摘要:** Shutdown  
**描述:** 优雅地关闭 OpenAkita 服务进程。

Uses the shared shutdown_event to trigger the same graceful cleanup
path as SIGINT/SIGTERM (sessions saved, IM adapters stopped, etc.).

### 响应
- **200**: 成功响应

---

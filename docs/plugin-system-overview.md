# OpenAkita 插件系统概览：Skill vs MCP vs Plugin

本文档说明 OpenAkita 三种扩展机制的定位、能力边界和选型指南。

---

## 一、三种机制一览

### Skill — 纯文本指令

Skill 是一段 Markdown 文本（`SKILL.md`），在启动时加载到 LLM 的 system prompt 中，用于引导 LLM 的行为。

- **能做什么**：告诉 LLM 遇到某类任务应该怎么做、优先用什么工具
- **不能做什么**：不能执行代码、不能调用系统服务、不能注册工具、不能挂钩子
- **隔离性**：N/A（只是文本，不存在隔离问题）
- **生命周期**：启动时加载文本，无动态管理

可选的 `scripts/*.py` 可以被 `SkillLoader.run_script()` 执行，但这是单次脚本调用，没有生命周期，没有 API 权限，执行完就结束。

**典型用途**：工具选择指导、格式规范、行为约束。

### MCP Server — 进程隔离的外部工具

MCP (Model Context Protocol) 是独立进程的外部工具服务，通过 JSON-RPC 协议与 OpenAkita 通信。

- **能做什么**：提供工具（tools），语言无关（Node.js / Python / Go 等）
- **不能做什么**：不能注入 prompt、不能挂钩子、不能注册 IM 通道、不能替换记忆、不能访问 OpenAkita 内部服务
- **隔离性**：进程隔离（强隔离，崩溃不影响宿主）
- **生命周期**：手动配置 `mcp_servers.json`，无统一安装/升级/市场

**典型用途**：Notion API、GitHub API、数据库查询等外部工具接入。

### Plugin — 全能力插件

插件是 OpenAkita 的新一代扩展机制，通过 `PluginAPI` 与宿主进行受控交互，可以提供 8 种能力。

- **能做什么**：注册工具、IM 通道、记忆后端、LLM 提供商、RAG 检索源、API 路由、钩子、Skill
- **能访问宿主**：通过 `PluginAPI` 受控访问 Brain、MemoryManager、Settings、MessageGateway
- **隔离性**：同进程 try/except 隔离（弱于 MCP，但能力更强）
- **生命周期**：`on_load` → `on_init` → 运行 → `on_shutdown`，支持 enable/disable/install/uninstall
- **权限管理**：三级权限模型（basic / advanced / system），安装时用户确认

**典型用途**：Obsidian 知识库 RAG、WhatsApp 通道、外部记忆系统、自定义 LLM provider。

---

## 二、能力对比矩阵

| 维度 | Skill | MCP Server | Plugin |
|------|-------|-----------|--------|
| **提供工具 (tools)** | - | YES | YES |
| **提供 IM 通道** | - | - | YES |
| **提供记忆后端** | - | - | YES (replace 模式自动接管 MemoryManager) |
| **提供 LLM provider** | - | - | YES |
| **提供 RAG 检索源** | - | - | YES |
| **提供 API 路由** | - | - | YES |
| **注入 prompt 文本** | YES (唯一能力) | - | YES (via SKILL.md + hooks) |
| **挂钩子** | - | - | YES (14 个钩子点) |
| **替换内置模块** | - | - | YES (记忆/搜索/LLM) |
| **访问宿主服务** | - | - | YES (Brain/Memory/Gateway/Settings) |
| **代发消息** | - | - | YES (`send_message()`) |
| **代码执行** | 仅可选 scripts/ 单次 | 独立进程内 | 同进程运行 |
| **进程隔离** | N/A | YES (强) | NO (try/except 隔离) |
| **生命周期管理** | 无 | 手动 | 完整 (load/init/shutdown/enable/disable) |
| **安装管理** | 放文件夹 | 改 JSON 配置 | 市场/URL/对话/UI |
| **权限模型** | 无 | 无 | 三级 (basic/advanced/system) |
| **多语言** | N/A (Markdown) | YES (任意语言) | Python (type=python) 或 任意 (type=mcp) |

---

## 三、系统接口访问权限

Plugin 与前两者最本质的区别在于：**开放了系统接口给插件，同时允许插件调用系统服务**。

但这种开放是**受控的**，通过 `PluginAPI` 和三级权限模型实现：

### Basic 级（安装即有，无需用户确认）

| 权限 | API 方法 | 说明 |
|------|---------|------|
| `tools.register` | `register_tools()` | 注册工具定义和处理函数 |
| `hooks.basic` | `register_hook("on_init" / "on_shutdown")` | 基础生命周期钩子 |
| `config.read` | `get_config()` | 读取本插件的配置 |
| `config.write` | `set_config()` | 写入本插件的配置 |
| `data.own` | `get_data_dir()` | 访问本插件的持久化数据目录 |
| `log` | `log()` | 写日志 |
| `skill` | `provides.skill` | 注册 Skill 文本到系统提示词 |

### Advanced 级（安装时弹窗提示用户确认授权）

| 权限 | API 方法 | 风险提示 |
|------|---------|---------|
| `memory.read` | `get_memory_manager()` 只读 | "此插件可以读取你的对话记忆" |
| `memory.write` | `get_memory_manager()` 写入 | "此插件可以修改记忆数据" |
| `channel.register` | `register_channel()` | "此插件将添加新的消息通道" |
| `channel.send` | `send_message()` | "此插件可以通过 IM 通道发送消息" |
| `hooks.message` | `register_hook("on_message_*")` | "此插件可以拦截和修改消息" |
| `hooks.retrieve` | `register_hook("on_retrieve" / "on_prompt_build")` | "此插件可以向 AI 注入上下文" |
| `retrieval.register` | `register_retrieval_source()` | "此插件将添加知识检索来源" |
| `search.register` | `register_search_backend()` | "此插件将添加搜索引擎后端" |
| `routes.register` | `register_api_routes()` | "此插件将添加网络接口" |
| `brain.access` | `get_brain()` | "此插件可以直接调用 AI 模型" |
| `vector.access` | `get_vector_store()` | "此插件可以访问向量数据库" |
| `settings.read` | `get_settings()` | "此插件可以读取系统配置" |
| `llm.register` | `register_llm_provider()` | "此插件将注册新的 LLM 提供商" |

### System 级（仅内置插件或需手动确认）

| 权限 | API 方法 | 说明 |
|------|---------|------|
| `hooks.all` | 全部 14 个钩子 | 完整钩子访问权 |
| `memory.replace` | `register_memory_backend()` 替换模式 | 完全替换内置记忆系统 |
| `system.config.write` | — | 写入全局系统配置 |

### 完整钩子事件列表（14 个）

| 钩子名称 | 触发时机 | 所需权限 |
|---------|---------|---------|
| `on_init` | 插件初始化完成后 | `hooks.basic` |
| `on_shutdown` | 系统关闭前 | `hooks.basic` |
| `on_message_received` | 收到用户消息时 | `hooks.message` |
| `on_message_sending` | 发送回复前 | `hooks.message` |
| `on_retrieve` | RAG 检索完成后 | `hooks.retrieve` |
| `on_tool_result` | 工具执行完成后 | `hooks.message` |
| `on_session_start` | 新会话开始时 | `hooks.message` |
| `on_session_end` | 会话结束时 | `hooks.message` |
| `on_prompt_build` | 系统提示词组装时 | `hooks.retrieve` |
| `on_schedule` | 定时任务触发时 | `hooks.all` |
| `on_before_tool_use` | 工具调用前 | `hooks.message` |
| `on_after_tool_use` | 工具调用后 | `hooks.message` |
| `on_config_change` | 插件配置变更时 | `hooks.basic` |
| `on_error` | 插件发生错误时 | `hooks.basic` |

### Manifest 校验

`plugin.json` 使用 Pydantic BaseModel 进行严格类型校验：

- 所有字段均有类型检查（权限必须为 list、timeout 必须为数字等）
- 插件 ID 需符合正则 `^[a-z0-9][a-z0-9\-_.]*$`
- entry 路径禁止包含 `..`
- 额外字段允许通过（`extra="allow"`），确保向后兼容

---

## 四、架构关系图

```
┌─────────────────────────────────────────────────────┐
│                OpenAkita 宿主 (Host)                 │
│                                                     │
│  ┌──────────┐ ┌─────────────┐ ┌──────────────────┐  │
│  │  Brain   │ │MemoryManager│ │ MessageGateway   │  │
│  │  (LLM)   │ │  (记忆系统)  │ │  (IM 消息路由)   │  │
│  └────▲─────┘ └──────▲──────┘ └────────▲─────────┘  │
│       │              │                 │             │
│  ┌────┴──────────────┴─────────────────┴──────────┐  │
│  │              PluginAPI (受控接口)                │  │
│  │  ┌────────────────────────────────────────┐    │  │
│  │  │ 权限检查: basic / advanced / system     │    │  │
│  │  └────────────────────────────────────────┘    │  │
│  └──────────┬──────────────┬──────────────────────┘  │
│             │              │                         │
└─────────────┼──────────────┼─────────────────────────┘
              │              │
     ┌────────▼───┐   ┌──────▼──────┐   ┌─────────────┐
     │  Plugin A  │   │  Plugin B   │   │   Skill     │
     │ (Python)   │   │  (MCP 包装) │   │ (纯文本)     │
     │            │   │            │   │             │
     │ 能调:      │   │ 能调:      │   │ 不能调任何   │
     │ register_* │   │ 仅 tools   │   │ 系统服务     │
     │ get_*      │   │            │   │             │
     │ send_*     │   │ 进程隔离    │   │ 注入 prompt │
     └────────────┘   └────────────┘   └─────────────┘
```

---

## 五、选型指南

### 什么时候用 Skill

- 只需要引导 LLM 的行为（如 "遇到图片编辑优先用 GIMP CLI"）
- 不需要执行任何代码
- 不需要访问系统服务

### 什么时候用 MCP

- 已有现成的 MCP server（如 Notion 官方 MCP、GitHub MCP）
- 需要强进程隔离（不信任的第三方工具）
- 只需要提供工具，不需要其他能力
- 工具用非 Python 语言实现（Node.js / Go 等）

### 什么时候用 Plugin

- 需要访问 OpenAkita 内部服务（记忆、Brain、消息网关）
- 需要添加 IM 通道（WhatsApp、Line、Matrix 等）
- 需要替换内置模块（换记忆系统、换搜索后端、加 LLM provider）
- 需要 RAG 功能（向对话注入知识库内容）
- 需要挂钩子（拦截消息、在 prompt 中注入内容、定时任务）
- 需要统一的安装管理和市场分发

### 组合使用

Plugin 是 Skill 和 MCP 的**超集**，一个 Plugin 可以同时包含：

- `provides.skill: "SKILL.md"` — 内含 Skill 文本，引导 LLM
- `type: "mcp"` — 包装一个 MCP server，享有插件管理和市场分发
- `type: "python"` + `provides.tools` + `provides.hooks` — 完整的代码插件

---

## 六、向后兼容性

| 现有机制 | 插件化后的状态 |
|---------|-------------|
| `skills/` 目录下的 SKILL.md | 继续工作，不受影响 |
| `data/mcp_servers.json` 中配置的 MCP | 继续工作，不受影响 |
| `_init_handlers()` 中注册的内置工具 | 继续工作，插件工具与内置工具并存 |
| `channels/registry.py` 中的内置通道 | 继续工作，插件通道与内置通道并存 |

插件系统是**增量添加**，不会破坏任何现有功能。

---

## 七、插件配置规范 (config_schema.json)

每个插件可通过 `config_schema.json` 声明可配置参数。前端 UI 会根据 schema 自动渲染配置表单，用户无需编辑 JSON 文件。

### 文件结构

```
my-plugin/
  plugin.json          # 必须
  plugin.py            # 必须 (type=python)
  config_schema.json   # 可选 — 声明可配置参数
  config.json          # 运行时生成 — 用户实际配置值
  README.md            # 推荐 — 插件说明文档
  icon.png             # 可选 — 插件图标（推荐 128×128 PNG）
```

### 插件图标规范 (icon)

插件可在根目录放置图标文件，前端会自动加载并在列表中显示。

**支持的文件名**（按优先级排序）：
1. `icon.png` — 推荐，PNG 格式
2. `icon.svg` — 矢量格式，任意尺寸
3. `logo.png` — 备选名称
4. `logo.svg` — 备选矢量
5. `icon.jpg` / `logo.jpg` — JPEG 格式

**设计建议**：
- 推荐尺寸：128×128 像素（PNG）或正方形 SVG
- 文件大小：建议 < 50KB
- 背景：建议使用透明背景（PNG/SVG），适配深浅主题
- 风格：圆角或圆形，简洁扁平，避免过多细节
- 无图标时：前端自动使用插件类型对应的默认图标

### config_schema.json 格式

采用 JSON Schema 子集，前端 UI 直接解析渲染：

```json
{
  "type": "object",
  "properties": {
    "vault_path": {
      "type": "string",
      "title": "Vault 路径",
      "description": "Obsidian Vault 目录路径"
    },
    "api_key": {
      "type": "string",
      "title": "API 密钥",
      "description": "前端自动隐藏输入"
    },
    "max_results": {
      "type": "integer",
      "title": "最大结果数",
      "description": "最大返回结果数",
      "default": 10
    },
    "enabled_features": {
      "type": "array",
      "items": { "type": "string" },
      "title": "启用功能",
      "description": "启用的功能列表",
      "default": ["search", "index"]
    },
    "mode": {
      "type": "string",
      "enum": ["fast", "balanced", "thorough"],
      "title": "搜索模式",
      "description": "选择搜索策略"
    },
    "debug": {
      "type": "boolean",
      "title": "调试模式",
      "description": "是否启用调试日志",
      "default": false
    }
  },
  "required": ["vault_path"]
}
```

### 支持的字段属性

| 属性 | 说明 | 示例 |
|------|------|------|
| `title` | 字段显示名（支持中文），优先于 key 作为标签显示 | `"title": "Vault 路径"` |
| `description` | 字段说明提示文字 | `"description": "Obsidian Vault 目录路径"` |
| `default` | 默认值 | `"default": 500` |
| `enum` | 枚举选项，渲染为下拉选择 | `"enum": ["fast", "balanced"]` |
| `type` | 字段类型（见下表） | `"type": "string"` |

### 支持的字段类型

| type | 前端渲染 | 备注 |
|------|---------|------|
| `string` | 文本输入框 | 字段名含 key/secret/password 时自动切为密码输入 |
| `integer` / `number` | 数字输入框 | |
| `boolean` | 复选框 | |
| `array` | 逗号分隔文本框 | items.type 建议为 string |
| `string` + `enum` | 下拉选择 | |

### 前端行为

- 有 `README.md` 的插件在列表中显示文档按钮，点击展开查看
- 有 `config_schema.json` 的插件显示配置按钮，点击展开配置表单
- 有 `title` 时优先显示 `title` 作为标签，key 名以灰色小字显示在旁边
- `required` 字段在标签后显示红色 `*` 标记
- 配置修改后点"保存"写入 `config.json`，插件通过 `api.get_config()` 读取

### 后端 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/plugins/list` | GET | 获取已安装插件列表及加载状态 |
| `/api/plugins/install` | POST | 安装插件（source 为 Git URL 或 zip） |
| `/api/plugins/{id}/enable` | POST | 启用插件 |
| `/api/plugins/{id}/disable` | POST | 禁用插件 |
| `/api/plugins/{id}/reload` | POST | 热重载插件 |
| `/api/plugins/{id}/schema` | GET | 获取 config_schema.json |
| `/api/plugins/{id}/config` | GET | 获取当前配置值 |
| `/api/plugins/{id}/config` | PUT | 更新配置值 |
| `/api/plugins/{id}/readme` | GET | 获取 README 文档内容 |
| `/api/plugins/{id}/icon` | GET | 获取插件图标文件 |
| `/api/plugins/{id}/open-folder` | POST | 返回插件目录路径（前端调用系统文件管理器打开） |
| `/api/plugins/{id}/export` | GET | 导出插件为 .zip 压缩包 |
| `/api/plugins/{id}/uninstall` | DELETE | 卸载插件 |

> **注意**: 根路径 `GET /api/plugins` 无路由定义（返回 404）。获取插件列表请使用 `GET /api/plugins/list`。

---

## 版本兼容体系

插件系统使用三层版本来管理兼容性：

| 版本类型 | 当前值 | 说明 |
|----------|--------|------|
| System Version | `1.27.7` | OpenAkita 整体发布版本号 |
| Plugin API Version | `1.1.0` | 插件与宿主的接口契约版本 |
| SDK Version | `0.2.0` | 开发工具包版本 |

### plugin.json requires 字段

```json
{
  "requires": {
    "openakita": ">=1.28.0",
    "plugin_api": "~1.1",
    "sdk": ">=0.2.0",
    "python": ">=3.11"
  }
}
```

### 检查规则

| 字段 | 格式 | 不满足时 |
|------|------|----------|
| `openakita` | `>=X.Y.Z` | 阻止加载 |
| `plugin_api` | `~N` (兼容主版本 N) | 主版本不匹配则阻止，次版本不匹配则警告 |
| `sdk` | `>=X.Y.Z` | 仅警告 |
| `python` | `>=X.Y` | 阻止加载 |

实现文件：`src/openakita/plugins/compat.py`

---

## 插件 Skill 加载机制

Python 类型的插件可以通过 `provides.skill` 声明附带的 SKILL.md 文件：

```json
{
  "type": "python",
  "provides": {
    "skill": "SKILL.md"
  }
}
```

加载流程：
1. `PluginManager._load_single` 在调用 `on_load()` 后检查 `provides.skill`
2. 如有声明，调用 `skill_loader.load_skill()` 加载
3. 所有插件加载完成后，自动刷新 `SkillCatalog` 缓存

---

## 插件 Onboard 协议

通道类插件可在 `plugin.json` 中声明交互式 onboarding 流程：

```json
{
  "onboard": {
    "type": "qr",
    "start_endpoint": "/onboard/start",
    "poll_endpoint": "/onboard/poll",
    "description": "扫描 QR 码链接账号"
  }
}
```

支持的 onboard 类型：`qr`（QR 扫码）、`oauth`（OAuth 跳转）、`credentials`（普通表单）

前端 `PluginOnboardModal` 组件会根据 `type` 自动渲染对应 UI（QR 码显示、OAuth 重定向等）。

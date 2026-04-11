# plugin.json 参考 / plugin.json Reference

每个插件目录必须包含一个 `plugin.json` 清单文件。宿主在启动时扫描此文件以发现和加载插件。

Every plugin directory must contain a `plugin.json` manifest file. The host scans for this file at startup to discover and load plugins.

---

## 必需字段 / Required Fields

| 字段 / Field | 类型 / Type | 说明 / Description |
|-------------|------------|-------------------|
| `id` | `string` | 唯一标识符，使用 kebab-case / Unique identifier, kebab-case |
| `name` | `string` | 用户可见的显示名称 / Human-readable display name |
| `version` | `string` | 语义化版本号 / SemVer version string |
| `type` | `string` | 运行时类型 / Runtime type: `python`, `mcp`, `skill` |

## 可选字段 / Optional Fields

| 字段 / Field | 类型 / Type | 默认值 / Default | 说明 / Description |
|-------------|------------|------------------|-------------------|
| `entry` | `string` | 按 type 而定 / varies | 入口文件 / Entry point file |
| `description` | `string` | `""` | 简短描述 / Short description |
| `author` | `string` | `""` | 作者 / Author name |
| `license` | `string` | `""` | 许可证 / License (e.g., `"MIT"`) |
| `homepage` | `string` | `""` | 项目主页 URL / Project homepage URL |
| `permissions` | `string[]` | `[]` | 所需权限列表 / Required permissions (see [permissions.md](permissions.md)) |
| `requires` | `object` | `{}` | 依赖声明 / Dependencies |
| `provides` | `object` | `{}` | 能力声明 / Provided capabilities |
| `replaces` | `string[]` | `[]` | 替换的内置模块 / Built-in modules this replaces |
| `conflicts` | `string[]` | `[]` | 冲突的插件 ID / Conflicting plugin IDs |
| `depends` | `string[]` | `[]` | 依赖的插件 ID（影响加载顺序）/ Plugin IDs this depends on (affects load order) |
| `category` | `string` | `""` | 市场分类 / Marketplace category |
| `tags` | `string[]` | `[]` | 搜索标签 / Search tags |
| `icon` | `string` | `""` | 图标名称 (Tabler Icons) / Icon name |
| `display_name_zh` | `string` | `""` | 中文显示名 / Chinese display name |
| `display_name_en` | `string` | `""` | 英文显示名 / English display name |
| `description_i18n` | `object` | `{}` | 多语言描述 `{"zh": "...", "en": "..."}` / i18n descriptions |
| `review_status` | `string` | `"unreviewed"` | 审核状态 / Review status (internal use) |
| `load_timeout` | `number` | `10` | `on_load()` 最大秒数 / Max seconds for `on_load()` |
| `hook_timeout` | `number` | `5` | 每个钩子回调最大秒数 / Max seconds per hook callback |
| `retrieve_timeout` | `number` | `3` | 检索源调用最大秒数 / Max seconds for retrieval calls |
| `ui` | `object` | `null` | UI 插件配置 (Plugin 2.0)，详见 [plugin-ui.md](plugin-ui.md) / UI config for full-stack plugins |

## 默认入口文件 / Default Entry Points

| `type` | 默认 `entry` / Default `entry` |
|--------|-------------------------------|
| `python` | `plugin.py` |
| `mcp` | `mcp_config.json` |
| `skill` | `SKILL.md` |

---

## `provides` 对象 / `provides` Object

声明插件提供的能力，用于市场展示和依赖检查。

Declares what the plugin provides. Used for marketplace display and dependency checking.

```json
{
  "provides": {
    "channels": ["whatsapp"],
    "tools": ["search_notes", "create_note"],
    "memory_backend": "qdrant",
    "llm_provider": {
      "api_type": "ollama_native",
      "registry_slug": "ollama"
    },
    "retrieval_sources": ["obsidian"],
    "hooks": ["on_message_received", "on_retrieve"],
    "api_routes": "routes.py",
    "skill": "SKILL.md",
    "config_schema": "config_schema.json"
  }
}
```

## `requires` 对象 / `requires` Object

声明依赖关系。宿主在加载前检查版本兼容性。

Declares dependencies. The host checks version compatibility before loading.

```json
{
  "requires": {
    "openakita": ">=1.5.0",
    "plugin_api": "~1",
    "sdk": ">=0.1.0",
    "python": ">=3.11",
    "pip": ["qdrant-client>=1.7.0", "httpx"],
    "npm": [],
    "system": []
  }
}
```

| 字段 / Field | 运行时校验 / Validated | 说明 / Description |
|-------------|:---:|-------------------|
| `openakita` | 是 (error) | 最低 OpenAkita 版本，格式 `>=X.Y.Z` / Minimum OpenAkita version |
| `plugin_api` | 是 (error) | 兼容的 API 主版本，格式 `~N` 或 `>=X.Y.Z` / Compatible API major version |
| `sdk` | 是 (warn) | 最低 SDK 版本（仅警告）/ Minimum SDK version (warn only) |
| `python` | 是 (error) | 最低 Python 版本，格式 `>=3.11` / Minimum Python version |
| `pip` | 安装时执行 / on install | Python 包依赖，安装时 `pip install --target <plugin>/deps/` / Python deps, installed to `deps/` on install |
| `npm` | 否 (stored) | Node.js 包依赖（MCP 类型用）/ Node.js dependencies (for MCP type) |
| `system` | 否 (stored) | 系统级依赖 / System-level dependencies |

> **pip 依赖注意事项 / pip dependency notes:**
> - 通过安装器安装时，`requires.pip` 会自动执行 `pip install --target <plugin_dir>/deps/`
> - 手动复制插件到 `data/plugins/` 时**不会**自动安装 pip 依赖，需手动运行
> - 宿主加载插件时将插件**根目录**加入 `sys.path`，但**不会**自动将 `deps/` 加入。如有第三方依赖，建议在 `on_load()` 中手动处理
>
> - When installed via the installer, `requires.pip` runs `pip install --target <plugin_dir>/deps/`
> - Manual copy to `data/plugins/` does **not** auto-install pip deps
> - The host adds the plugin **root directory** to `sys.path` but does **not** auto-add `deps/`. Handle third-party imports in `on_load()` if needed

---

## 完整示例 / Complete Example

### 工具插件 / Tool Plugin

```json
{
  "id": "hello-tool",
  "name": "Hello Tool",
  "version": "1.0.0",
  "description": "一个简单的问候工具 / A simple greeting tool",
  "author": "OpenAkita Team",
  "license": "MIT",
  "type": "python",
  "entry": "plugin.py",
  "permissions": ["tools.register"],
  "provides": { "tools": ["hello_world"] },
  "category": "tool",
  "tags": ["demo", "greeting"]
}
```

### RAG 知识库插件 / RAG Knowledge Base Plugin

```json
{
  "id": "obsidian-kb",
  "name": "Obsidian Knowledge Base",
  "version": "1.0.0",
  "description": "从 Obsidian 知识库检索内容 / RAG retrieval from Obsidian vault",
  "author": "Community",
  "license": "MIT",
  "homepage": "https://github.com/openakita/plugin-obsidian-kb",
  "type": "python",
  "entry": "plugin.py",
  "permissions": [
    "tools.register",
    "hooks.retrieve",
    "retrieval.register",
    "config.read",
    "config.write"
  ],
  "requires": {
    "openakita": ">=1.5.0",
    "pip": ["markdown-it-py"]
  },
  "provides": {
    "tools": ["search_obsidian"],
    "retrieval_sources": ["obsidian"],
    "hooks": ["on_retrieve"],
    "config_schema": "config_schema.json"
  },
  "category": "productivity",
  "tags": ["obsidian", "knowledge-base", "rag", "markdown"],
  "icon": "notebook",
  "load_timeout": 15,
  "retrieve_timeout": 5
}
```

### 记忆后端插件 / Memory Backend Plugin

```json
{
  "id": "qdrant-memory",
  "name": "Qdrant Memory Backend",
  "version": "1.0.0",
  "description": "使用 Qdrant 向量数据库替换内置记忆系统 / Replace built-in memory with Qdrant",
  "type": "python",
  "permissions": ["memory.replace", "config.read", "config.write"],
  "requires": { "pip": ["qdrant-client>=1.7.0"] },
  "provides": { "memory_backend": "qdrant" },
  "replaces": ["builtin-memory"],
  "category": "memory"
}
```

### MCP 包装插件 / MCP Wrapper Plugin

```json
{
  "id": "github-mcp",
  "name": "GitHub MCP",
  "version": "1.0.0",
  "description": "通过 MCP 协议接入 GitHub API / GitHub API via MCP protocol",
  "type": "mcp",
  "entry": "mcp_config.json",
  "permissions": ["tools.register"],
  "category": "tool",
  "tags": ["github", "mcp", "git"]
}
```

### 全栈 UI 插件 / Full-Stack UI Plugin (Plugin 2.0)

```json
{
  "id": "seedance-video",
  "name": "Seedance Video Generator",
  "version": "1.0.0",
  "type": "python",
  "entry": "plugin.py",
  "description": "AI 视频生成 / AI video generation powered by Seedance",
  "permissions": [
    "tools.register",
    "routes.register",
    "hooks.basic",
    "config.read",
    "config.write",
    "data.own",
    "brain.access"
  ],
  "requires": {
    "openakita": ">=1.27.0",
    "plugin_api": "~1",
    "plugin_ui_api": "~1"
  },
  "provides": {
    "tools": ["seedance_create", "seedance_status", "seedance_list"],
    "routes": true
  },
  "ui": {
    "entry": "ui/dist/index.html",
    "title": "Seedance 视频生成",
    "title_i18n": { "en": "Seedance Video", "zh": "Seedance 视频生成" },
    "sidebar_group": "apps",
    "permissions": ["upload", "download", "notifications", "theme", "clipboard"]
  },
  "category": "creative",
  "tags": ["video", "ai", "seedance"]
}
```

**注意 / Notes:**
- `requires.plugin_ui_api` 声明 UI API 版本兼容性 / Declares UI API version compatibility
- `ui.sidebar_group` 目前仅支持 `"apps"` / Currently only `"apps"` is supported
- 详见 [plugin-ui.md](plugin-ui.md) / See [plugin-ui.md](plugin-ui.md) for details

---

## `depends` 与加载顺序 / `depends` and Load Order

宿主按 `depends` 字段进行**拓扑排序**（Kahn 算法）决定加载顺序。依赖关系不满足时插件会被跳过。

The host performs a **topological sort** (Kahn's algorithm) on `depends` to determine load order. Plugins with unmet dependencies are skipped.

```json
{
  "id": "my-dashboard",
  "depends": ["data-connector", "chart-engine"],
  "..."
}
```

**规则 / Rules:**
- 被依赖的插件先加载 / Dependencies load first
- 循环依赖会导致所有环上的插件被跳过 / Circular dependencies cause all plugins in the cycle to be skipped
- 缺失依赖的插件被跳过并记录错误 / Missing dependencies cause skip with error log

---

## 校验 / Validation

宿主在加载时会校验清单文件：

The host validates the manifest at load time:

- 缺少必需字段（`id`, `name`, `version`, `type`）→ 跳过并记录错误 / Missing required fields → skip with error
- `type` 不是 `python`/`mcp`/`skill` → 跳过 / Invalid `type` → skip
- `requires.openakita` 版本不兼容 → 跳过 / Incompatible version → skip
- `requires.plugin_api` 主版本不匹配 → 跳过 / API major version mismatch → skip
- `requires.plugin_ui_api` 不匹配 → 仅警告（UI 仍会尝试加载）/ UI API mismatch → warning only
- `requires.python` 版本不满足 → 跳过 / Python version mismatch → skip
- `conflicts` 中的插件已加载 → 跳过 / Conflicting plugin loaded → skip
- `depends` 中的插件未加载 → 跳过 / Missing dependency → skip
- 循环 `depends` → 环上所有插件被跳过 / Circular depends → all in cycle skipped
- 未知的 `permissions` 字符串 → 过滤并记录警告 / Unknown permission strings → filtered with warning
- `entry` 路径含 `..` 或绝对路径 → 安全拒绝 / Path traversal in entry → security reject

---

## `provides` 与 AI 提示词 / `provides` and AI Prompts

`provides` 是声明性对象，主要用于市场展示和依赖检查。**并非**所有 `provides` 中的值都会自动出现在 AI 的系统提示词中。

`provides` is declarative, mainly for marketplace and dependency checking. **Not** all values automatically appear in the AI system prompt.

**实际影响 AI 提示词的方式 / What actually affects the AI prompt:**
- 通过 `api.register_tools()` 注册的**工具名**会出现在 AI 可调用工具列表中
- `type: "skill"` 插件的 SKILL.md 内容会注入到提示词中
- Python 插件 `provides.skill` 指向的 SKILL.md 也会被加载
- 其他 `provides` 值（如 `channels`、`hooks`）仅用于文档展示

---

## 扩展字段 / Extra Fields

`plugin.json` 的解析使用 `extra: "allow"` 模式，未知字段会被保留。你可以添加自定义元数据字段：

Unknown fields are preserved (`extra: "allow"` mode). You can add custom metadata:

```json
{
  "id": "my-plugin",
  "onboard": { "welcome_message": "Hello!" },
  "custom_field": "any value"
}
```

---

## 相关文档 / Related

- [getting-started.md](getting-started.md) — 最小 plugin.json 示例、不同 type 的要求 / Minimal manifest, type requirements
- [permissions.md](permissions.md) — 权限字符串完整列表 / Full permission string catalog
- [rest-api.md](rest-api.md) — 配置管理和安装 API / Config management and installation API
- [hooks.md](hooks.md) — 钩子与 `provides.hooks` 的关系 / Hooks and `provides.hooks`
- [plugin-ui.md](plugin-ui.md) — `ui` 字段详解 / UI section details
- [examples/mcp-plugin.md](examples/mcp-plugin.md) — MCP 插件的 mcp_config.json 格式 / MCP config format

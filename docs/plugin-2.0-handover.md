# Plugin 2.0 & Seedance 插件 — 开发移交文档

## 一、项目概况

OpenAkita Plugin 2.0 在原有后端插件系统（工具/通道/记忆/LLM/钩子/Skill/MCP）基础上，扩展了**带前端 UI 页面的全栈插件能力**。Seedance 视频生成插件是第一个全栈 UI 插件的参考实现。

**当前分支：** `feature/plugin-2.0-fullstack-ui`（基于 main）

**外部仓库：**
- 插件仓库 + SDK 文档: https://github.com/openakita/openakita-plugins （已同步最新）
- SDK PyPI 包: `openakita-plugin-sdk` 0.2.0（0.3.0 代码已就绪，未发布）

---

## 二、架构总览

```
┌──────────────────────────────────────────────────────────┐
│  Desktop App (Tauri 2.x + React 18 + Vite 6)            │
│  apps/setup-center/                                      │
│    ├── Sidebar.tsx         → 显示「应用」分组入口         │
│    ├── App.tsx             → plugin_app:${id} 路由注册    │
│    ├── PluginAppHost.tsx   → iframe 宿主 + Bridge 通信    │
│    └── plugin-bridge-host.ts → postMessage 桥接协议      │
├──────────────────────────────────────────────────────────┤
│  Plugin UI (iframe 内)                                   │
│    └── ui/dist/index.html  → 内联 Bridge SDK + 前端应用   │
├──────────────────────────────────────────────────────────┤
│  Backend (FastAPI)                                       │
│  src/openakita/plugins/                                  │
│    ├── manager.py    → 发现、加载、卸载、UI 静态文件挂载  │
│    ├── api.py        → PluginAPI (v2.0: file/event/ui)   │
│    ├── manifest.py   → plugin.json 解析 (含 ui 段)       │
│    ├── hooks.py      → 14 个生命周期钩子                  │
│    ├── catalog.py    → AI 系统提示词中的插件目录           │
│    ├── installer.py  → Git/ZIP/本地安装                   │
│    ├── errors.py     → 20 个错误码 + 中英双语             │
│    ├── sandbox.py    → 错误追踪 & 自动禁用               │
│    ├── state.py      → plugin_state.json 持久化          │
│    ├── compat.py     → 版本兼容检查                      │
│    ├── bundles.py    → 跨生态映射 (Claude/Cursor/Codex)  │
│    └── protocols.py  → Memory/Retrieval/Search 协议      │
│  src/openakita/api/routes/plugins.py → 25+ REST API 端点  │
└──────────────────────────────────────────────────────────┘
```

### 通信流程

```
Plugin UI (iframe)
    ↕ postMessage (Bridge 协议)
PluginAppHost.tsx
    ↕ Tauri invoke (下载/文件夹/剪贴板)
    ↕ HTTP fetch (插件 REST API)
FastAPI Backend
    ↕ PluginAPI
Plugin Python 代码
```

---

## 三、文件清单

### 3.1 插件系统核心（宿主侧）

| 文件 | 用途 |
|------|------|
| `src/openakita/plugins/api.py` | PluginAPI 接口，含 2.0 方法: create_file_response, broadcast_ui_event, register_ui_event_handler, ui_api_version |
| `src/openakita/plugins/manager.py` | 插件发现、加载（按 type 分支: python/mcp/skill）、卸载、UI 静态文件挂载（NoCacheStaticFiles）、depends 拓扑排序 |
| `src/openakita/plugins/manifest.py` | PluginManifest Pydantic 模型，含 ui 字段（PluginUIConfig）、extra="allow" |
| `src/openakita/plugins/hooks.py` | 14 个钩子名和 HookRegistry，asyncio.gather 并行分发 |
| `src/openakita/plugins/catalog.py` | 生成 AI 提示词中的 "Installed Plugins" 段（仅注册的工具名 + skill） |
| `src/openakita/plugins/installer.py` | Git clone / ZIP 下载 / 本地路径安装，pip deps → deps/ |
| `src/openakita/plugins/errors.py` | PluginErrorCode 枚举（20 个）+ 中英双语消息 + guidance |
| `src/openakita/plugins/sandbox.py` | PluginErrorTracker: 5 分钟窗口内 10 次错误自动禁用 |
| `src/openakita/plugins/state.py` | PluginState: 持久化 enabled/granted_permissions/error_count 到 plugin_state.json |
| `src/openakita/plugins/compat.py` | 检查 requires 中的 openakita/plugin_api/plugin_ui_api/sdk/python 版本 |
| `src/openakita/plugins/bundles.py` | 跨生态映射: OpenClaw/Claude/Cursor/Codex → plugin.json |
| `src/openakita/api/routes/plugins.py` | 25+ REST API 端点: list/install/enable/disable/config/permissions/logs/health 等 |

### 3.2 前端（桌面端）

| 文件 | 用途 |
|------|------|
| `apps/setup-center/src/views/PluginAppHost.tsx` | iframe 宿主，管理 Bridge handshake，处理 toast/download/showInFolder 等通知 |
| `apps/setup-center/src/lib/plugin-bridge-host.ts` | PluginBridgeHost 类: postMessage 协议，HOST_CAPABILITIES 列表 |
| `apps/setup-center/src/components/Sidebar.tsx` | 侧边栏: 从 /api/plugins/ui-apps 拉取并渲染「应用」分组 |
| `apps/setup-center/src/views/PluginManagerView.tsx` | 插件管理页面 |
| `apps/setup-center/src/components/PluginOnboardModal.tsx` | 插件首次加载的引导弹窗 |
| `apps/setup-center/src/App.tsx` | plugin_app:${id} 视图路由注册 |
| `apps/setup-center/src/types.ts` | ViewId 含 plugin_app 前缀、PluginUIApp 类型 |

### 3.3 Seedance 视频插件

| 文件 | 用途 |
|------|------|
| `plugins/seedance-video/plugin.json` | 清单: type=python, ui 段指向 ui/dist/index.html, 9 种权限 |
| `plugins/seedance-video/plugin.py` | 入口: Plugin 类, 3 个 AI 工具 + 20+ REST 路由 + 异步轮询 |
| `plugins/seedance-video/ark_client.py` | 火山引擎 Seedance API 客户端 (httpx) |
| `plugins/seedance-video/task_manager.py` | SQLite 任务/配置/素材管理 (WAL 模式) |
| `plugins/seedance-video/models.py` | Seedance 模型定义 (2.0/2.0-pro/1.5 等) |
| `plugins/seedance-video/prompt_optimizer.py` | LLM 提示词优化 + Seedance 静态字典（CAMERA / ATMOSPHERE / MODE_FORMULAS / PROMPT_TEMPLATES）。Sprint 18 因 grep 结论错误一度被删,已于 2026-04-21 还原 — 见 [docs/sprint18-cleanup-assessment.md](sprint18-cleanup-assessment.md#b8-prompt_optimizer-迁移评估) §A 修订说明 |
| `plugins/seedance-video/long_video.py` | 长视频: 分镜拆解(Brain.think) + 链式生成 + ffmpeg 拼接 |
| `plugins/seedance-video/ui/dist/index.html` | 单文件 React 前端: 创建/任务列表/设置/提示词指南/分镜工作台 |
| `plugins/seedance-video/config.json` | 持久化配置 (api_key 等) |

### 3.4 SDK 包

| 路径 | 说明 |
|------|------|
| `openakita-plugin-sdk/src/openakita_plugin_sdk/` | SDK 源码（13 个 .py 文件） |
| `openakita-plugin-sdk/docs/` | 完整文档（=仓库 sdk-docs/ 的镜像副本） |
| `openakita-plugin-sdk/pyproject.toml` | 版本 0.3.0, hatchling 构建 |

### 3.5 插件仓库 (外部)

```
https://github.com/openakita/openakita-plugins
├── README.md              # 仓库说明（中英双语）
├── CONTRIBUTING.md         # 插件贡献指南
├── sdk-docs/              # SDK 文档（权威源，SDK 包的 docs/ 从此同步）
│   ├── README.md          # 文档索引
│   ├── getting-started.md # 入门指南
│   ├── api-reference.md   # PluginAPI 全部方法
│   ├── plugin-json.md     # 清单文件规范
│   ├── plugin-ui.md       # UI 插件开发指南 (Plugin 2.0)
│   ├── rest-api.md        # 管理 REST API (25+ 端点)
│   ├── permissions.md     # 三级权限模型
│   ├── hooks.md           # 14 个生命周期钩子
│   ├── protocols.md       # Memory/Retrieval/Search 协议
│   ├── testing.md         # 测试指南
│   ├── cross-ecosystem.md # 跨生态兼容
│   └── examples/          # 9 种类型的完整示例
│       ├── tool-plugin.md
│       ├── channel-plugin.md
│       ├── mcp-plugin.md
│       ├── skill-plugin.md
│       ├── ui-plugin.md
│       ├── hook-plugin.md
│       ├── memory-plugin.md
│       ├── llm-plugin.md
│       └── rag-plugin.md
└── plugins/               # 11 个示例/参考插件
```

---

## 四、关键设计决策

### 4.1 iframe 隔离 + Bridge 协议

插件前端 UI 运行在 iframe 中，通过 postMessage 与宿主通信。这样：
- 插件 CSS/JS 不会污染主应用
- 插件无法直接访问宿主 DOM 或 Tauri API
- 宿主控制所有系统操作（下载文件、打开文件夹、剪贴板等）

Bridge 消息类型: `bridge:ready` → `bridge:handshake` → `bridge:handshake-ack` → `bridge:api-request`/`bridge:notification`/`bridge:download`/`bridge:show-in-folder`/`bridge:pick-folder`/`bridge:clipboard`/`bridge:event`

### 4.2 前后端分层

- 前端（iframe 内）: 纯静态 HTML/JS，通过 fetch 调用后端 REST API
- 后端（Plugin 类）: 注册 FastAPI 路由到 `/api/plugins/{plugin_id}/` 前缀下
- AI 工具: 通过 register_tools 暴露给 LLM，仅注册关键操作

### 4.3 版本兼容

- `plugin.json` 中 `requires.plugin_api` 和 `requires.plugin_ui_api` 控制版本约束
- `compat.py` 在加载时校验
- PluginAPI 新增方法均有默认实现（非 abstract），旧插件不会因新宿主崩溃

---

## 五、已知问题 & 待办

### 5.1 Seedance 插件 — LLM 对话集成薄弱

**现状：** 只注册了 3 个基础工具（seedance_create/status/list），描述简陋，缺少：
- SKILL.md 为 LLM 注入视频生成领域知识（什么是好的 prompt、各模式适用场景、限制说明）
- 高级能力（提示词优化、分镜拆解）未暴露为工具，LLM 无法调用
- 工具描述未说明异步流程（创建后需查询状态获取结果）
- 参数不完整（model/resolution/generate_audio 等未暴露给 LLM）

**建议：**
1. 创建 `SKILL.md` 并在 `plugin.json` 的 `provides.skill` 中声明
2. 增加 `seedance_optimize_prompt`、`seedance_decompose_storyboard` 等工具
3. 丰富工具 description 和参数 schema

### 5.2 SDK 0.3.0 未发布到 PyPI

代码已就绪（含 Plugin 2.0 方法），需要构建和发布：
```bash
cd openakita-plugin-sdk
python -m build
twine upload dist/*
```

### 5.3 文档同步机制

SDK `docs/` 是 repo `sdk-docs/` 的手动复制镜像，没有自动同步。修改文档后需要手动同步两边。

建议后续改为：
- repo `sdk-docs/` 为唯一权威源
- SDK 包的 `docs/` 通过 CI 或 git submodule 自动同步

### 5.4 `channel.py` 运行时依赖

`ChannelAdapter.send_text` 默认实现中有 `from openakita.channels.types import OutgoingMessage`，已用 try/except 保护，但实际开发 Channel 插件时仍需 override 该方法。

### 5.5 插件目录下 `deps/` 的 sys.path 问题

宿主加载插件时只把插件根目录加入 `sys.path`，不会自动把 `deps/` 加入。如有第三方依赖，需插件自己在 `on_load` 中处理 `sys.path`。

---

## 六、开发环境启动

```bash
# 后端
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
openakita serve             # API 服务 (默认 http://localhost:8000)

# 前端 (另一个终端)
cd apps/setup-center
npm install
npm run dev                 # Vite dev server (默认 http://localhost:5173)

# 桌面端 (可选)
cd apps/setup-center
npm run tauri dev           # Tauri + Vite 联合开发
```

插件默认目录: `{project_root}/data/plugins/`
Seedance 插件开发位置: `plugins/seedance-video/`（需手动 symlink 或复制到 data/plugins/）

---

## 七、关键 API 速查

### PluginAPI (Python 后端)

```python
# 基础
api.log(msg, level="info")
api.get_config() / api.set_config(updates)
api.get_data_dir() -> Path | None

# 注册能力
api.register_tools(definitions, handler)
api.register_hook(hook_name, callback)
api.register_api_routes(router)  # FastAPI APIRouter

# Plugin 2.0
api.create_file_response(source, filename=, media_type=)
api.broadcast_ui_event(event_type, data)
api.register_ui_event_handler(event_type, handler)
api.ui_api_version  # -> str ("1.0.0")

# 宿主服务
api.get_brain() -> Brain | None
api.get_settings() -> Settings | None
```

### Bridge SDK (前端 JavaScript)

```javascript
// 初始化（宿主自动 handshake）
window._ctx = { theme, locale, apiBase, pluginId }

// API 调用
pluginApi(path, options)  // -> fetch(apiBase + path, options)

// 系统操作 (通过 postMessage → 宿主 → Tauri)
showToast(message, type)
downloadFile(url, filename)
showInFolder(path)
pickFolder()              // -> Promise<string>
copyToClipboard(text)
uploadFile(file, path)    // 直连 POST，不经 Bridge
onEvent(handler)          // 接收 broadcast_ui_event 推送
```

### REST API (管理端点)

```
GET  /api/plugins/list           # 列表（含 failed）
GET  /api/plugins/ui-apps        # UI 插件列表（侧边栏用）
POST /api/plugins/install        # 安装 {source, background}
GET  /api/plugins/{id}/_admin/config    # 读配置
PUT  /api/plugins/{id}/_admin/config    # 写配置（jsonschema 校验）
POST /api/plugins/{id}/_admin/enable    # 启用
POST /api/plugins/{id}/_admin/disable   # 禁用
POST /api/plugins/{id}/_admin/reload    # 热重载
GET  /api/plugins/{id}/_admin/logs      # 日志（?lines=100）

# 注：所有针对单个插件的管理接口都挂在 /_admin/ 下，避免和插件自己注册的
# /tasks、/config 等业务路径冲突。_admin/* 是宿主保留命名空间。
GET  /api/plugins/health         # 健康检查
```

---

## 八、测试要点

```bash
# 单元测试
pytest tests/unit/test_skill_manager.py
pytest tests/unit/test_skill_exposure.py
pytest tests/unit/test_lazy_package_imports.py

# SDK 验证
cd openakita-plugin-sdk
python -c "from openakita_plugin_sdk import SDK_VERSION; print(SDK_VERSION)"  # 0.3.0

# 插件加载验证
openakita serve  # 启动后检查日志: "Seedance Video plugin loaded"
curl http://localhost:8000/api/plugins/list
curl http://localhost:8000/api/plugins/ui-apps
```

---

## 九、对话历史引用

本次开发完整对话记录在: `5b09cedb-05b9-4db7-9c5b-058a3d8b008d`

主要里程碑:
1. Plugin 2.0 架构设计和实现计划
2. 全栈插件系统实现（iframe/Bridge/路由/静态文件）
3. Seedance 视频插件完整开发
4. 多轮 bug 修复（Brain 参数/链式生成/Key 持久化/下载/Toast）
5. SDK 文档四轮审计和修复
6. SDK 0.3.0 升级（Plugin 2.0 抽象方法）

# 全栈 UI 插件开发 / Full-Stack UI Plugin Development

OpenAkita Plugin 2.0 支持插件携带独立的前端 UI 页面，在桌面端以 iframe 方式加载，通过 postMessage Bridge 协议与宿主通信。

Plugin 2.0 allows plugins to ship a dedicated frontend UI page. It's loaded inside an iframe in the desktop app and communicates with the host via a postMessage Bridge protocol.

---

## 适用场景 / When to Use

- 操作流程复杂（如参数配置、异步任务管理）/ Complex workflows (parameter config, async task management)
- 需要专属可视化界面（图表、预览、编辑器）/ Needs dedicated visual interface (charts, previews, editors)
- 涉及文件上传/下载、文件夹选择 / File upload/download, folder selection
- 对话框无法承载的交互体验 / Interactions that chat can't handle well

---

## 目录结构 / Directory Structure

```
my-plugin/
├── plugin.json              # 清单文件 (含 ui 字段) / Manifest (with ui section)
├── plugin.py                # 后端入口 / Backend entry point
├── ui/
│   └── dist/
│       └── index.html       # 前端单页应用 / Frontend SPA
├── README.md
└── icon.svg                 # 插件图标 (可选) / Plugin icon (optional)
```

## plugin.json — UI 字段 / UI Section

```json
{
  "id": "my-ui-plugin",
  "name": "My UI Plugin",
  "version": "1.0.0",
  "type": "python",
  "entry": "plugin.py",
  "permissions": [
    "tools.register",
    "routes.register",
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
  "ui": {
    "entry": "ui/dist/index.html",
    "icon": "",
    "title": "我的插件",
    "title_i18n": {
      "en": "My Plugin",
      "zh": "我的插件"
    },
    "sidebar_group": "apps",
    "permissions": ["upload", "download", "notifications", "theme", "clipboard"]
  }
}
```

### `ui` 字段说明 / UI Field Reference

| 字段 / Field | 类型 / Type | 默认值 / Default | 说明 / Description |
|-------------|------------|-----------------|-------------------|
| `entry` | `string` | `"ui/dist/index.html"` | 前端入口 HTML 文件 / Frontend entry HTML file |
| `icon` | `string` | `""` | 图标名或图标路径 / Icon name or file path |
| `title` | `string` | `""` | 侧边栏显示标题 / Sidebar display title |
| `title_i18n` | `object` | `{}` | 多语言标题 `{en, zh, ...}` / i18n titles |
| `sidebar_group` | `string` | `"apps"` | 侧边栏分组，必须为 `"apps"` / Sidebar group, must be `"apps"` |
| `width` | `number` | `0` | 建议宽度 (0=全宽) / Suggested width (0=full) |
| `height` | `number` | `0` | 建议高度 (0=全高) / Suggested height (0=full) |
| `permissions` | `string[]` | `[]` | UI 能力声明 / UI capability declarations |

### UI 能力值 / UI Permission Values

| 值 / Value | 说明 / Description |
|------------|-------------------|
| `upload` | 文件上传 / File upload |
| `download` | 文件下载 / File download |
| `notifications` | Toast 通知 / Toast notifications |
| `theme` | 主题感知 / Theme awareness |
| `clipboard` | 剪贴板访问 / Clipboard access |

> `ui.permissions` 目前为声明性质（文档/市场展示），运行时不做自动校验。
>
> `ui.permissions` are currently declarative (for docs/marketplace). No runtime enforcement.

---

## Bridge 通信协议 / Bridge Protocol

### 消息信封格式 / Message Envelope

所有消息必须包含 `__akita_bridge: true` 标记：

```typescript
interface BridgeMessage {
  __akita_bridge: true;
  version: 1;          // 协议版本 / Protocol version
  type: string;        // 消息类型 / Message type
  requestId?: string;  // 请求-响应配对 / Request-response pairing
  payload?: object;    // 数据载荷 / Data payload
}
```

### 连接握手 / Connection Handshake

```
Plugin iframe                           Host (PluginBridgeHost)
     |                                       |
     |--- bridge:ready ---------------------->|
     |<-- bridge:init (theme,locale,         -|
     |       apiBase,pluginId)                |
     |--- bridge:handshake ------------------>|
     |<-- bridge:handshake-ack              --|
     |       (hostVersion,capabilities,       |
     |        bridgeVersion)                  |
     |                                       |
     |   Bridge connected, ready to use       |
```

**握手细节 / Handshake details:**
- `bridge:init` payload: `{ theme: string, locale: string, apiBase: string, pluginId: string }`
- `bridge:handshake-ack` payload: `{ hostVersion: "1.0.0", capabilities: string[], bridgeVersion: 1 }`
- 宿主将 `bridge:ready` **或** `bridge:handshake` 视为已连接 / Host treats either `bridge:ready` or `bridge:handshake` as connected
- 30 秒内未收到握手信号，宿主显示加载超时错误，提供重试按钮 / 30s timeout with retry button on failure

### 请求-响应消息 / Request-Response Messages

| 方向 / Direction | `type` | `payload` | `ack type` |
|-----------------|--------|-----------|------------|
| Plugin → Host | `bridge:api-request` | `{ method, path, body }` | `bridge:api-response` |
| Plugin → Host | `bridge:download` | `{ url, filename }` | `bridge:download-ack` |
| Plugin → Host | `bridge:show-in-folder` | `{ path }` | `bridge:show-in-folder-ack` |
| Plugin → Host | `bridge:pick-folder` | `{ title? }` | `bridge:pick-folder-ack` |
| Plugin → Host | `bridge:clipboard` | `{ text, action:"copy" }` | `bridge:clipboard-ack` |

### 单向消息 / Fire-and-Forget Messages

| 方向 / Direction | `type` | `payload` |
|-----------------|--------|-----------|
| Plugin → Host | `bridge:notification` | `{ title, body, type? }` |
| Plugin → Host | `bridge:navigate` | `{ viewId }` |
| Host → Plugin | `bridge:init` | `{ theme, locale, apiBase, pluginId }` |
| Host → Plugin | `bridge:theme-change` | `{ theme }` |
| Host → Plugin | `bridge:locale-change` | `{ locale }` |
| Host → Plugin | `bridge:event` | `{ eventType, data }` |
| Host → Plugin | `bridge:unsupported` | `{ originalType }` |

> 当宿主收到未识别的消息类型时，返回 `bridge:unsupported` 并携带原始类型名。
>
> When the host receives an unknown message type, it responds with `bridge:unsupported` carrying the original type name.

### 宿主能力清单 / Host Capabilities

握手时 `bridge:handshake-ack` 返回宿主支持的能力列表：

```
theme, locale, notification, upload, download, file-download,
show-in-folder, pick-folder, clipboard, navigate, api-proxy,
websocket-events, config
```

---

## 前端 SDK 参考 / Frontend SDK Reference

推荐在 `index.html` 中内联以下 Bridge SDK（零依赖）：

### 核心函数 / Core Functions

```javascript
// API 调用 (自动通过 Bridge 代理，5s 超时后 fallback 到直连)
// API call (auto-proxied via bridge, 5s timeout falls back to direct fetch)
pluginApi(method, path, body) → Promise<{ ok, status, body }>

// 示例 / Example:
const r = await pluginApi("GET", "/tasks");
// → GET /api/plugins/<plugin_id>/tasks
```

### 通知 / Notifications

```javascript
// 显示 toast 通知 / Show toast notification
showToast("操作成功");              // info 类型
showToast("保存失败", "error");     // error 类型
showToast("请注意", "warning");     // warning 类型
```

### 文件操作 / File Operations

```javascript
// 下载文件 (使用 Tauri 原生下载) / Download file (Tauri native)
await downloadFile(url, "video.mp4");

// 在系统文件管理器中显示 / Show in system file manager
await showInFolder("/path/to/file");

// 选择文件夹 / Pick a folder
const folder = await pickFolder("选择输出目录");
// folder = "/Users/.../output" 或 null (取消)
```

### 文件上传 / File Upload

```javascript
// 上传文件到插件后端 (直接 POST FormData，不经 Bridge)
// Upload file to plugin backend (direct POST FormData, not via Bridge)
async function uploadFile(file) {
  var base = _ctx ? _ctx.apiBase : _detectApiBase();
  var prefix = _ctx ? "/api/plugins/" + _ctx.pluginId : "/api/plugins/YOUR_PLUGIN_ID";
  var formData = new FormData();
  formData.append("file", file);
  var resp = await fetch(base + prefix + "/upload", {
    method: "POST", body: formData
  });
  return resp.json();
}
```

> 文件上传使用直连 POST（不经 Bridge 代理），因为 postMessage 无法传递 `File` 对象。后端需要自己实现 `/upload` 路由。
>
> File uploads use direct POST (not via Bridge) because `postMessage` cannot transfer `File` objects. The backend must implement its own `/upload` route.

### 剪贴板 / Clipboard

```javascript
await copyToClipboard("复制的文本内容");
```

### 事件监听 / Event Listening

```javascript
// 监听宿主推送的实时事件 / Listen to host-pushed real-time events
onEvent("task_updated", (data) => {
  console.log("Task status changed:", data);
});
```

### 完整 SDK 模板 / Full SDK Template

```html
<script>
var _ctx = null, _bridgeConnected = false, _pending = {}, _caps = [];
var _inIframe = window.self !== window.top;
var _eventListeners = {};

function uid() { return Math.random().toString(36).slice(2, 10); }

function post(msg) {
  if (_inIframe) {
    window.parent.postMessage(
      Object.assign({ __akita_bridge: true, version: 1 }, msg), "*"
    );
  }
}

function _detectApiBase() {
  return location.origin;
}

function directFetch(method, path, body) {
  var url = path.startsWith("http") ? path : _detectApiBase() + path;
  var opts = { method: method, headers: { "Content-Type": "application/json" } };
  if (body && method !== "GET" && method !== "HEAD") {
    if (body instanceof FormData) { opts.body = body; delete opts.headers["Content-Type"]; }
    else { opts.body = JSON.stringify(body); }
  }
  return fetch(url, opts).then(function(resp) {
    return resp.json().then(function(json) {
      return { ok: resp.ok, status: resp.status, body: json };
    });
  });
}

function apiCall(method, path, body) {
  if (!_inIframe || !_bridgeConnected) return directFetch(method, path, body);
  return new Promise(function(resolve, reject) {
    var id = uid();
    _pending[id] = { resolve: resolve, reject: reject };
    post({ type: "bridge:api-request", requestId: id,
           payload: { method: method, path: path, body: body } });
    setTimeout(function() {
      if (_pending[id]) { delete _pending[id]; directFetch(method, path, body).then(resolve).catch(reject); }
    }, 5000);
  });
}

function pluginApi(method, path, body) {
  var base = _ctx ? "/api/plugins/" + _ctx.pluginId : "/api/plugins/YOUR_PLUGIN_ID";
  return apiCall(method, base + path, body);
}

function showToast(body, type) {
  post({ type: "bridge:notification", payload: { title: "", body: body, type: type || "info" } });
}

function downloadFile(url, filename) {
  return new Promise(function(resolve) {
    var id = uid();
    _pending[id] = { resolve: resolve };
    post({ type: "bridge:download", requestId: id, payload: { url: url, filename: filename || "" } });
    setTimeout(function() { if (_pending[id]) { delete _pending[id]; resolve({ ok: false }); } }, 30000);
  });
}

function showInFolder(path) {
  return new Promise(function(resolve) {
    var id = uid();
    _pending[id] = { resolve: resolve };
    post({ type: "bridge:show-in-folder", requestId: id, payload: { path: path } });
    setTimeout(function() { if (_pending[id]) { delete _pending[id]; resolve({}); } }, 5000);
  });
}

function pickFolder(title) {
  return new Promise(function(resolve) {
    var id = uid();
    _pending[id] = { resolve: resolve };
    post({ type: "bridge:pick-folder", requestId: id, payload: { title: title || "" } });
    setTimeout(function() { if (_pending[id]) { delete _pending[id]; resolve(null); } }, 60000);
  });
}

function copyToClipboard(text) {
  return new Promise(function(resolve) {
    var id = uid();
    _pending[id] = { resolve: resolve };
    post({ type: "bridge:clipboard", requestId: id, payload: { text: text, action: "copy" } });
    setTimeout(function() { if (_pending[id]) { delete _pending[id]; resolve({}); } }, 3000);
  });
}

function onEvent(type, fn) {
  _eventListeners[type] = _eventListeners[type] || [];
  _eventListeners[type].push(fn);
}

// 消息监听器 / Message listener
window.addEventListener("message", function(e) {
  var d = e.data;
  if (!d || !d.__akita_bridge) return;
  if (d.type === "bridge:init" && d.payload) {
    _ctx = d.payload;
    _bridgeConnected = true;
    if (_ctx.theme) {
      document.documentElement.setAttribute("data-theme",
        _ctx.theme.includes("dark") ? "dark" : "light");
    }
  }
  if (d.type === "bridge:handshake-ack" && d.payload) {
    _caps = d.payload.capabilities || [];
    _bridgeConnected = true;
  }
  // 处理请求-响应 ACK / Handle request-response ACKs
  var ackTypes = [
    "bridge:api-response", "bridge:download-ack",
    "bridge:show-in-folder-ack", "bridge:pick-folder-ack", "bridge:clipboard-ack"
  ];
  if (ackTypes.indexOf(d.type) !== -1 && d.requestId && _pending[d.requestId]) {
    _pending[d.requestId].resolve(d.payload);
    delete _pending[d.requestId];
  }
  // 处理主题变更 / Handle theme changes
  if (d.type === "bridge:theme-change" && d.payload) {
    document.documentElement.setAttribute("data-theme",
      (d.payload.theme || "").includes("dark") ? "dark" : "light");
  }
  // 处理宿主事件 / Handle host events
  if (d.type === "bridge:event" && d.payload) {
    var et = d.payload.eventType;
    ((_eventListeners[et] || []).forEach(function(fn) { fn(d.payload.data); }));
  }
});

// 发送就绪信号 / Send ready signal
post({ type: "bridge:ready" });
post({ type: "bridge:handshake", payload: { clientVersion: "1.0.0" } });

// 暴露全局对象 / Expose global object
window.__bridge = {
  apiCall: apiCall, pluginApi: pluginApi, onEvent: onEvent,
  getCtx: function() { return _ctx; },
  downloadFile: downloadFile, showToast: showToast,
  showInFolder: showInFolder, pickFolder: pickFolder,
  copyToClipboard: copyToClipboard,
};
</script>
```

---

## UI 静态资源服务 / UI Static File Serving

宿主在加载插件时，自动将 `ui.entry` 所在目录挂载为静态文件服务：

The host automatically mounts the directory containing `ui.entry` as a static file server:

```
URL:  /api/plugins/<plugin_id>/ui/
目录: <plugin_dir>/ui/dist/     (entry 的父目录)
```

**技术细节 / Technical details:**
- 使用 FastAPI `StaticFiles` 挂载，`html=True`（SPA 路由支持）
- 响应头强制 `Cache-Control: no-cache, no-store, must-revalidate`，确保开发期间不缓存
- 如果 `api_app` 尚未就绪（启动顺序问题），挂载会延迟到 app 就绪后自动执行
- iframe 加载 URL 自动附加 `?_v={timestamp}` 防止缓存

---

## `_ctx` 上下文对象 / Context Object

握手成功后，SDK 模板中的 `_ctx` 保存宿主传入的上下文信息：

After handshake, the `_ctx` object in the SDK template holds host context:

```javascript
var ctx = window.__bridge.getCtx();
// ctx = {
//   theme: "light",          // 当前主题 / Current theme
//   locale: "zh",            // 当前语言 / Current locale
//   apiBase: "http://127.0.0.1:21110",  // API 基地址 / API base URL
//   pluginId: "seedance-video"          // 插件 ID / Plugin ID
// }
```

> `pluginApi()` 函数自动使用 `_ctx.pluginId` 构建 URL 前缀，无需手动拼接。
>
> `pluginApi()` automatically uses `_ctx.pluginId` to build URL prefixes; no manual concatenation needed.

---

## 开发模式 / Development Mode

### 方式一：纯 HTML（适合简单插件）

直接编写单文件 `index.html`（含内联 JS/CSS/React），无需构建工具。SDK 模板可直接嵌入。

Single-file `index.html` with inline JS/CSS/React (no build tools). Embed the SDK template directly.

### 方式二：Vite/Webpack（适合复杂插件）

使用前端构建工具开发，产物输出到 `ui/dist/`。

Use frontend build tools, output to `ui/dist/`.

```bash
cd my-plugin/ui
npm create vite@latest . -- --template react-ts
npm run build   # 输出到 dist/
```

**热开发提示 / Hot Dev Tips:**
- 宿主会在每次 iframe 加载时附加 `?_v=timestamp` 防止缓存
- 修改 `index.html` 后，在桌面端侧边栏重新点击插件入口即可刷新
- 后端路由修改后需重启宿主（或使用 `POST /api/plugins/{id}/reload`）

---

## 后端 API 开发 / Backend API Development

### 路由注册 / Route Registration

```python
from fastapi import APIRouter
from openakita.plugins.api import PluginAPI, PluginBase

class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

    def _register_routes(self, router: APIRouter):
        @router.get("/status")
        async def get_status():
            return {"ok": True, "version": "1.0.0"}

        @router.post("/tasks")
        async def create_task(body: CreateTaskBody):
            # 你的业务逻辑 / Your business logic
            return {"ok": True, "task_id": "xxx"}
```

所有路由自动挂载在 `/api/plugins/<plugin_id>/` 前缀下。

All routes are auto-mounted under `/api/plugins/<plugin_id>/` prefix.

### 文件响应 / File Response

```python
# 使用内置助手创建文件响应（支持本地文件和远程 URL）
# Use built-in helper for file responses (supports local files and remote URLs)
response = self._api.create_file_response(
    source="/path/to/video.mp4",        # 本地路径或远程 URL
    filename="output.mp4",              # 下载文件名
    media_type="video/mp4",             # MIME 类型
    as_download=True,                   # True=下载 False=内联
)
# 自动处理 Content-Disposition, UTF-8 文件名, RFC 5987 编码
# Auto-handles Content-Disposition, UTF-8 filenames, RFC 5987 encoding
```

### 访问宿主 Brain (LLM) / Access Host Brain

```python
brain = self._api.get_brain()      # 需要 brain.access 权限
if brain:
    result = await brain.think(
        prompt="用户的问题",           # 必填: 用户消息
        system="你是一个助手",         # 可选: 系统提示词
    )
    text = result.content            # Response dataclass, .content: str
```

### UI 事件推送 / UI Event Push

```python
# 从后端推送事件到前端 UI / Push events from backend to frontend UI
self._api.broadcast_ui_event("task_completed", {
    "task_id": "abc",
    "status": "succeeded",
    "video_url": "https://..."
})

# 注册前端发来的事件处理器 / Register handler for events from frontend
self._api.register_ui_event_handler("user_action", handler_fn)
```

---

## 主题适配 / Theme Adaptation

前端应监听主题变更并使用 CSS 变量：

```css
:root, [data-theme="light"] {
  --bg: #ffffff;
  --bg-secondary: #f8f9fa;
  --text: #1a1a2e;
  --text-muted: #6c757d;
  --border: #e2e8f0;
  --primary: #3b82f6;
}

[data-theme="dark"] {
  --bg: #1a1b2e;
  --bg-secondary: #252640;
  --text: #e2e8f0;
  --text-muted: #94a3b8;
  --border: #334155;
  --primary: #60a5fa;
}
```

---

## 版本兼容 / Version Compatibility

### Bridge 协议版本 / Bridge Protocol Version

- 当前版本: `1`
- 宿主通过 `bridge:handshake-ack` 返回 `bridgeVersion` 和 `capabilities`
- 插件应检查所需能力是否在 `capabilities` 中
- 新版本只做**加法**，不删除已有消息类型

### 后端 API 版本 / Backend API Version

- `plugin_api`: 当前 `1.0.0`，使用 `requires.plugin_api: "~1"` 锁定主版本
- `plugin_ui_api`: 当前 `1.0.0`，使用 `requires.plugin_ui_api: "~1"` 声明

```json
{
  "requires": {
    "plugin_api": "~1",
    "plugin_ui_api": "~1"
  }
}
```

---

## 安全注意事项 / Security Notes

- 插件 UI 运行在 `allow-scripts allow-forms allow-same-origin allow-popups` 沙箱中
- 所有 API 请求经 Bridge 代理，宿主可审计和拦截
- 文件系统操作（下载、打开文件夹）由宿主 Tauri 原生命令执行，插件无法直接访问文件系统
- `bridge:api-request` 仅代理 `GET/POST/PUT/DELETE`，不支持 WebSocket

---

## 完整示例 / Complete Example

参见仓库中的 [seedance-video](https://github.com/openakita/openakita/tree/main/plugins/seedance-video) 插件——一个完整的全栈 UI 插件实现，包含：

- 多 Tab 页面（创建、任务列表、素材库、提示词指南、分镜工作台、设置）
- 异步任务管理（Polling + 状态同步）
- SQLite 本地存储
- LLM 提示词优化（调用宿主 Brain）
- 多段视频链式生成
- 原生文件下载和文件夹管理

---

## 相关文档 / Related

- [api-reference.md](api-reference.md) — 后端 PluginAPI 完整方法 / Full backend PluginAPI methods
- [rest-api.md](rest-api.md) — 插件管理 REST API / Plugin management REST API
- [plugin-json.md](plugin-json.md) — 清单文件规范 / Manifest schema
- [permissions.md](permissions.md) — 权限模型 / Permission model
- [testing.md](testing.md) — 测试指南（含 UI 插件测试）/ Testing guide (incl. UI plugin testing)
- [examples/ui-plugin.md](examples/ui-plugin.md) — 完整 UI 插件示例 / Complete UI plugin example
- [getting-started.md](getting-started.md) — 快速上手 / Quick start guide

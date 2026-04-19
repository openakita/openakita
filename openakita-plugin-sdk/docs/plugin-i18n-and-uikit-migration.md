# 插件 UI / i18n / UI Kit 迁移指南

> **目的**：把第一只「示范插件」(`tongyi-image`) 的迁移经验沉淀成可复用流程，
> 让任何已有插件（或新插件）在 1–2 小时内拿到 **统一视觉 + 中英 i18n + 不出白屏** 的体验。
>
> **适用对象**：所有运行在桌面端 iframe 中的插件 UI（`ui/dist/index.html`）。
>
> **本文档只描述「插件作者要做什么」**。脚手架（ui-kit）和 host 端的基础设施
> 在第 1 节列出但**不需要每个插件再做一遍**——它们一次性沉淀好之后所有插件共享。

---

## 0. TL;DR — 我作为插件作者要做哪 7 步？

下面 7 步对应本文 §3 的章节，按顺序做即可：

1. **接入 ui-kit**：在 `index.html` `<head>` 引 `styles.css` + `icons.js` + `i18n.js`（按需 `markdown-mini.js`）。
2. **改 `plugin.json`**：补 `display_name_zh/en`、`description_i18n`、`ui.title_i18n`。
3. **抽 i18n 字典**：把 UI 里所有用户可见的中文集中到 `I18N_DICT = { zh, en }`。
4. **写两个 React hook**：`useT()` + `useLocale()`，一次写好全插件复用。
5. **替换硬编码**：所有中文文本改成 `t("key")`；`emoji` 改成 `OpenAkitaIcons.xxx()`。
6. **后端正文接口加 `?locale=`**：长文按 locale 单返、短词双语并排（混合策略）。
7. **加 ErrorBoundary + 防御性数据访问**：避免 React 18 unhandled error 把整个 iframe 卸掉。

附：dev 部署有"源码 vs `data/plugins/` 副本不同步"的坑，见 §5。

---

## 1. 三层架构 —— 谁在哪一层

每次「让插件升级到统一视觉 + i18n」涉及三层。**只有第三层是每个插件都要重做一遍。**

### 1.1 ui-kit 脚手架（一次性，所有插件共享）

位置：`openakita-plugin-sdk/src/openakita_plugin_sdk/web/ui-kit/`，挂载为 `/api/plugins/_sdk/ui-kit/*`。

| 资源 | 作用 |
|---|---|
| `i18n.js` | `window.OpenAkitaI18n.{register,t,locale,setLocale,onChange}`，自动订阅 `openakita:locale-change` 事件。**i18n 的"内核"**。 |
| `icons.js` | `window.OpenAkitaIcons.{warning,palette,key,...}` 返回内联 SVG 字符串，替代 emoji。 |
| `markdown-mini.js` | `window.OpenAkitaMD.render(md)` 极小型 MD 渲染器（提示词指南这种动态内容用）。 |
| `styles.css` | 通用 `--oa-*` 主题变量 + 一组 `oa-*` 视觉类（见 §2 速查表）。 |
| `bootstrap.js`（已存在） | 把 `bridge:locale-change`/`bridge:init` 等转成 DOM CustomEvent；把 `OpenAkita.meta.locale` 写到 `window`。 |

> **插件作者**：只 `<link>` / `<script>` 引用即可，**不要拷贝、不要 fork**。
> 任何"我想要的通用类不在 styles.css 里"——升级 styles.css，不要在插件里写新私有类。

### 1.2 Host 基础设施（一次性，已就绪）

| 改动 | 文件 | 作用 |
|---|---|---|
| `_manifest_meta` 透出 i18n 字段 | `src/openakita/api/routes/plugins.py` | `/api/plugins/list` 返回 `display_name_i18n` / `description_i18n` / `ui_title_i18n`。 |
| `pickI18n` + 类型扩展 | `apps/setup-center/src/views/PluginManagerView.tsx` | 插件管理页按 `i18n.language` 选名字/描述。 |
| `pickAppTitle` | `apps/setup-center/src/components/Sidebar.tsx` | 侧栏 Apps 名称按当前语言展示。 |
| Bridge locale 转发（已存在） | `apps/setup-center/src/views/PluginAppHost.tsx` | `i18n.language` 变化 → `bridge.sendLocaleChange()` → bootstrap.js → DOM CustomEvent → `OpenAkitaI18n` → 你的 React 组件 re-render。 |
| 缓存破坏 / 强刷 | `PluginAppHost.tsx` | `Alt+Shift+R` 强刷 iframe（只 dev）。手动反馈循环友好。 |

> **插件作者**：什么都不用做，只要享用结果即可。

### 1.3 插件独立（每个插件都要做一遍）

这是 §3 的全部内容：plugin.json i18n 字段、index.html 接入 ui-kit、抽字典、用 hook、emoji 换 SVG、后端接口加 locale、加 ErrorBoundary。

---

## 2. UI Kit 速查（你能用到的全部 `oa-*` 类）

| 视觉模块 | 主类 | 何时用 |
|---|---|---|
| 顶部 Hero 标题 | `oa-hero-title` + `__icon` + `__text` + `__sub` | 插件主页面顶部 H1。例：`通义生图 / Tongyi Image`。 |
| 段落小标题 | `oa-section-label` | 表单分组、卡片小标题（"生成模式"、"商品名称"…）。 |
| 行内段落小标题 | `oa-section-label oa-section-label--inline` | 标题和操作按钮在同一行（如 "提示词" + "AI 优化"）。 |
| AI 渐变按钮 | `oa-ai-btn` (+ `__icon`) | "AI 优化"、"智能润色" 这类 LLM 触发按钮。 |
| API Key 提示横幅 | `oa-config-banner` (+ `__icon` `__text` `__action`) | 顶部强提示（缺 Key、缺权限），**必须放 `.app` 直接子节点，不要塞进滚动容器**。 |
| 设置项必填角标 | `oa-config-field-callout` + `data-callout="必填项"` | 设置页缺值时提示。 |
| 设置分区卡片 | `oa-settings-section` (+ `__title`) | 设置页每一段（API、默认参数、存储…）。 |
| 任务列表左栏 | `oa-list-panel` (+ `__filters`/`__divider`/`__items`/`__footer`) | 双栏布局的左侧面板，给底框/分隔。 |
| 预览空态/居中 | `oa-preview-area` (+ `--centered`) | 右侧未生成时的占位框。 |
| Markdown 容器 | `oa-md-content` | `OpenAkitaMD.render` 输出的容器、Guide 页的章节内容。 |
| 通用图标插槽 | `oa-icon` | 包裹 `OpenAkitaIcons.xxx()` 输出的 SVG，自动 `currentColor`、按 font-size 缩放。 |

---

## 3. 插件迁移操作步骤（按这个顺序做）

下文中的所有路径以你的插件为根，写作 `<plugin>/...`。

### 3.1 `plugin.json` —— 补 i18n 字段

```json
{
  "id": "my-plugin",
  "name": "My Plugin",                      // 英文官方名（程序标识用，必须英文）
  "version": "1.0.0",
  "description": "Short English description used as fallback",

  // ↓↓↓ 新增 ↓↓↓
  "display_name_zh": "我的插件",
  "display_name_en": "My Plugin",
  "description_i18n": {
    "zh": "中文描述。建议两三句话讲清是什么、能做什么、依赖什么。",
    "en": "English description. Two or three sentences."
  },

  "ui": {
    "entry": "ui/dist/index.html",
    "title": "我的插件",                    // 兼容字段，建议用中文（旧客户端 fallback）
    "title_i18n": { "zh": "我的插件", "en": "My Plugin" },
    "sidebar_group": "apps",
    "permissions": ["upload", "download", "notifications", "theme", "clipboard"]
  }
}
```

**约定**：
- `name`、`description` 保留为英文，作为最终 fallback。
- `display_name_zh/en` 用于「插件管理页 + 侧栏」的可读名。
- `description_i18n` 用于「插件管理页」的描述（侧栏不用）。
- `ui.title_i18n` 用于「侧栏 Apps 项」名字 + 插件页 hero 区。

**dev 部署同步**：参见 §5（`data/plugins/<id>/plugin.json` 也要更新到一致状态）。

### 3.2 `ui/dist/index.html` `<head>` —— 接入 ui-kit

```html
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>My Plugin</title>

  <!-- 1) 通用样式 -->
  <link rel="stylesheet" href="/api/plugins/_sdk/ui-kit/styles.css?v=20260419-1" />

  <!-- 2) 内联 SVG 图标（替代 emoji） -->
  <script src="/api/plugins/_sdk/ui-kit/icons.js?v=20260419-1"></script>

  <!-- 3) i18n 内核 —— 必须，否则 useLocale/useT 走 fallback 实现 -->
  <script src="/api/plugins/_sdk/ui-kit/i18n.js?v=20260419-1"></script>

  <!-- 4) 可选：极小型 Markdown（动态文档/提示词指南需要） -->
  <script src="/api/plugins/_sdk/ui-kit/markdown-mini.js?v=20260419-1"></script>

  <!-- 5) 你自己的样式 / Bootstrap / React / Babel -->
  <script src="/api/plugins/_sdk/bootstrap.js"></script>
  <!-- React + Babel 按你已有方式 -->
</head>
```

**强制规则**：
- 给每个 `_sdk` 资源带上 `?v=YYYYMMDD-N` 缓存破坏后缀，dev 联调时改完一目了然。
- 不要把 i18n.js / icons.js / styles.css **拷贝**到插件里，永远走 `_sdk` URL。

### 3.3 `index.html` —— 抽 i18n 字典

在 `<script type="text/babel">` 顶部、组件之前定义：

```js
const I18N_DICT = {
  zh: {
    "app.title": "我的插件",
    "app.subtitle": "副标题/品牌行",
    "tabs.create": "创建",
    "tabs.tasks": "任务列表",
    "tabs.settings": "设置",
    "banner.noKey.title": "尚未配置 API Key",
    "banner.noKey.desc": "所有任务都会失败，请前往设置填入 Key。",
    "banner.noKey.cta": "前往配置",
    "create.btn.start": "开始生成",
    "create.btn.generating": "生成中...",
    "modes.text2img": "文生图",
    "modes.img_edit": "图像编辑",
    "status.pending": "等待中",
    "status.running": "生成中",
    "status.succeeded": "已完成",
    "status.failed": "失败",
    "toast.downloading": "正在下载...",
    "toast.downloadFailed": "下载失败: {msg}",
    "err.title": "插件 UI 渲染出错",
    "err.retry": "重试渲染"
    // ...
  },
  en: {
    "app.title": "My Plugin",
    "app.subtitle": "Subtitle / brand line",
    "tabs.create": "Create",
    "tabs.tasks": "Tasks",
    "tabs.settings": "Settings",
    "banner.noKey.title": "API Key not configured",
    "banner.noKey.desc": "All tasks will fail. Open Settings and enter your API Key.",
    "banner.noKey.cta": "Go to settings",
    "create.btn.start": "Start",
    "create.btn.generating": "Generating...",
    "modes.text2img": "Text-to-image",
    "modes.img_edit": "Image edit",
    "status.pending": "Pending",
    "status.running": "Running",
    "status.succeeded": "Done",
    "status.failed": "Failed",
    "toast.downloading": "Downloading...",
    "toast.downloadFailed": "Download failed: {msg}",
    "err.title": "Plugin UI render error",
    "err.retry": "Retry render"
    // ...
  },
};
```

**键命名约定**（建议遵守，让所有插件 key 风格一致）：
- 用点号分层：`scope.subscope.key`
- `app.*`、`tabs.*`、`banner.*`、`modes.*`、`status.*`、`toast.*`、`err.*`、`settings.*`、`create.*`、`tasks.*`、`preview.*`、`guide.*`
- 插值用 `{name}`：`"toast.downloaded": "Downloaded: {name}"` → `t("toast.downloaded", { name: fname })`
- 缺 key 时 i18n 内核会**回原 key 字符串**，便于 grep 找漏。

### 3.4 `index.html` —— 注册字典 + 写两个 hook（可直接复制粘贴）

```js
// 注册字典；若 i18n.js 没加载到，用 inline fallback 不让 UI 崩
const _i18n = (function () {
  if (window.OpenAkitaI18n && window.OpenAkitaI18n.register) {
    window.OpenAkitaI18n.register(I18N_DICT);
    return window.OpenAkitaI18n;
  }
  let loc = (navigator && navigator.language) || "zh";
  const subs = new Set();
  return {
    t: function (k, vars) {
      const base = (loc || "zh").split(/[-_]/)[0];
      const tpl = (I18N_DICT[loc] && I18N_DICT[loc][k])
        || (I18N_DICT[base] && I18N_DICT[base][k])
        || (I18N_DICT.en && I18N_DICT.en[k])
        || k;
      return vars
        ? String(tpl).replace(/\{(\w+)\}/g, (_, n) => vars[n] != null ? String(vars[n]) : "{" + n + "}")
        : tpl;
    },
    locale: () => loc,
    setLocale: (l) => { loc = l; subs.forEach(fn => { try { fn(l); } catch (_) {} }); },
    onChange: (fn) => { subs.add(fn); return () => subs.delete(fn); },
  };
})();

// React hook：locale 变化时 force-rerender 调用方
function useT() {
  const [, setTick] = useState(0);
  useEffect(() => _i18n.onChange(() => setTick(x => x + 1)), []);
  return _i18n.t;
}

// React hook：返回当前 normalized locale，用作 useEffect 依赖
function useLocale() {
  const [loc, setLoc] = useState(() => _i18n.locale());
  useEffect(() => _i18n.onChange((l) => setLoc(l)), []);
  return loc;
}
```

**非 React 上下文**（`download()`、`showToast()` 等顶层函数）的 `_tr` shim：

```js
// 直接读 OpenAkitaI18n.t；如果还没注册（早期事件），用第三个参数 fallback。
function _tr(key, vars, fallback) {
  if (window.OpenAkitaI18n && window.OpenAkitaI18n.t) {
    const v = window.OpenAkitaI18n.t(key, vars);
    if (v && v !== key) return v;
  }
  if (fallback) {
    if (vars) {
      return String(fallback).replace(/\{(\w+)\}/g, (_, n) =>
        vars[n] != null ? String(vars[n]) : "{" + n + "}",
      );
    }
    return fallback;
  }
  return key;
}
```

### 3.5 `index.html` —— 替换硬编码

#### 3.5.1 静态文案 → `t("key")`

```jsx
// before
<button>开始生成</button>
// after
function CreateTab() {
  const t = useT();
  return <button>{t("create.btn.start")}</button>;
}
```

#### 3.5.2 枚举（status / mode / tab）—— 拆 css class 与 label

```jsx
// before: 把中文和样式纠缠在一起
const STATUS_MAP = {
  pending:   { label: "等待中", cls: "tag-orange" },
  running:   { label: "生成中", cls: "tag-blue"   },
  succeeded: { label: "已完成", cls: "tag-green"  },
  failed:    { label: "失败",   cls: "tag-red"    },
};

// after: 样式纯静态，label 走 i18n
const STATUS_CLS = {
  pending:   "tag-orange",
  running:   "tag-blue",
  succeeded: "tag-green",
  failed:    "tag-red",
};
function statusLabel(s, t) { return t("status." + s); }
function statusCls(s)      { return STATUS_CLS[s] || "tag-gray"; }

// 用法
<span className={statusCls(task.status)}>{statusLabel(task.status, t)}</span>
```

模式列表同理：

```jsx
const MODE_DEFS = [
  { id: "text2img", iconKey: "palette" },
  { id: "img_edit", iconKey: "edit"    },
];
function modeName(id, t) { return t("modes." + id); }
function modeList(t)     { return MODE_DEFS.map(m => ({ ...m, name: modeName(m.id, t) })); }
```

#### 3.5.3 顶层非 React 函数 → `_tr`

```js
async function downloadImage(taskId, idx, name) {
  showToast(_tr("toast.downloading", null, "Downloading..."));
  try {
    const resp = await fetch(...);
    if (!resp.ok) throw new Error(_tr("toast.serverReturned", { status: resp.status }, "Server returned {status}"));
    showToast(_tr("toast.downloaded", { name }, "Downloaded: {name}"));
  } catch (e) {
    showToast(_tr("toast.downloadFailed", { msg: e.message }, "Download failed: {msg}"));
  }
}
```

#### 3.5.4 emoji → SVG

```jsx
// before
<h1>🎨 通义生图</h1>

// after
<h1 className="oa-hero-title">
  <span className="oa-hero-title__icon"
        dangerouslySetInnerHTML={{__html: window.OpenAkitaIcons ? window.OpenAkitaIcons.palette() : ""}} />
  <span className="oa-hero-title__text">
    {t("app.title")}
    <span className="oa-hero-title__sub">{t("app.subtitle")}</span>
  </span>
</h1>
```

**强制规则**：UI 中**不允许出现任何 emoji 字符**。所有图形都通过 `OpenAkitaIcons.<name>()`。如果缺少需要的图标，**升级 `ui-kit/icons.js`**（一次性沉淀），不要在插件里手写 SVG。

### 3.6 后端 —— 内容接口加 `?locale=`（混合策略）

如果你的插件后端**会返回供 UI 直接展示的正文**（指南、模板、说明、错误提示库等），按下面的混合策略改：

| 数据类型 | 策略 | 形态 |
|---|---|---|
| **短词关键词**（标签、风格、视角、镜头…） | 一次返回 zh+en 双语，前端并排显示 | `[{ "zh": "写实摄影", "en": "Photorealistic" }, ...]` |
| **长文**（公式、模板、负向预设、提示文档） | 按 locale 单返一份 | 服务端按 `?locale=zh\|en` 选 dict |
| **混合元字段**（label / desc / tooltip） | 按 locale 投影 | label 改 `_LABELS_I18N[cat][locale]` |

#### 后端接口模板

```python
# tongyi_prompt_optimizer.py 风格

_LONG_FORM_I18N = {
    "zh": {...},
    "en": {...},
}
_SHORT_KEYWORDS_BILINGUAL = {
    "realistic": [
        {"zh": "写实摄影", "en": "Photorealistic"},
        ...
    ],
}

def _normalize_locale(locale: str | None) -> str:
    if not locale:
        return "zh"
    base = str(locale).split("-")[0].split("_")[0].lower()
    return base if base in ("zh", "en") else "zh"

def get_guide_data(locale: str | None = None) -> dict:
    loc = _normalize_locale(locale)
    return {
        "locale": loc,
        "long_form": _LONG_FORM_I18N.get(loc, _LONG_FORM_I18N["zh"]),
        "keywords":  _SHORT_KEYWORDS_BILINGUAL,    # 双语，永远不变
    }
```

```python
# plugin.py route
@router.get("/guide")
async def guide(locale: str | None = None) -> dict:
    return {"ok": True, **get_guide_data(locale)}
```

#### 前端配套

```jsx
function GuideTab() {
  const t      = useT();
  const locale = useLocale();        // 关键：拿到当前语言
  const [data, setData] = useState(null);

  useEffect(() => {
    setData(null);
    pluginApi("GET", `/guide?locale=${encodeURIComponent(locale || "zh")}`)
      .then(r => { if (r.body && r.body.ok) setData(r.body); })
      .catch(() => {});
  }, [locale]);                      // 关键：locale 变化自动重拉

  // 短词渲染：兼容字符串和 {zh,en} 两种 shape
  const label = (w) => (typeof w === "string")
    ? w
    : (w.zh && w.en && w.zh !== w.en ? `${w.zh} / ${w.en}` : (w.zh || w.en || ""));

  ...
}
```

### 3.7 ErrorBoundary + 防御性数据访问

React 18 production build 在子组件 throw 未捕获错误时会**卸掉整个根**——表现为整个插件 iframe 突然白屏。必须包一层。

```jsx
class PluginErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  componentDidCatch(err, info) { console.error("[plugin]", err, info); }
  reset = () => this.setState({ err: null });
  render() {
    if (!this.state.err) return this.props.children;
    const tr = (k) => _i18n.t(k);
    return (
      <div style={{padding: 24, maxWidth: 720}}>
        <strong style={{color: "var(--danger,#ef4444)"}}>{tr("err.title")}</strong>
        <pre style={{whiteSpace: "pre-wrap", marginTop: 8, padding: 12, background: "var(--oa-bg-muted)"}}>
          {String(this.state.err && this.state.err.stack || this.state.err)}
        </pre>
        <button className="btn btn-sm btn-primary" onClick={this.reset}>{tr("err.retry")}</button>
      </div>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <PluginErrorBoundary><App /></PluginErrorBoundary>
);
```

**配合做的事**：列表/对象类后端响应一律 **`Array.isArray() ?: []`** + **`typeof === "number" ?: 0`** 兜底：

```js
const r = await pluginApi("GET", "/tasks", { ... });
const tasks = Array.isArray(r.body && r.body.tasks) ? r.body.tasks : [];
const total = typeof (r.body && r.body.total) === "number" ? r.body.total : 0;
```

ErrorBoundary 是"不让用户看到白屏"的安全网；防御性访问是"尽量不触发 ErrorBoundary"的预防针。两者都做。

---

## 4. 视觉重构（与 i18n 解耦，但建议同期做）

这一节**不影响功能**，是把"扁平的、emoji 风格的"老 UI 升级到统一视觉的清单。每条都是 §2 速查表里类的具体用法。

| 部位 | 旧 | 新 |
|---|---|---|
| Hero 标题 | `<h1>🎨 我的插件</h1>` | `<h1 className="oa-hero-title">` 三段结构 |
| 表单分组小标题 | `<div className="label">商品名称</div>` | `<div className="oa-section-label">{t(...)}</div>` |
| 标题 + 操作按钮同行 | flex + space-between，按钮无外框 | `oa-section-label oa-section-label--inline` + `oa-ai-btn` |
| API Key 缺失提示 | 散落在 header 的小字 | 顶部 `oa-config-banner`（**作为 `.app` 的直接子节点**，不要进 `.content`，否则破坏滚动布局） |
| 设置项缺值提示 | 红字提示 | `.form-row` 加 `oa-config-field-callout` + `data-callout="必填项"` |
| 设置分区 | `<div class="setting-section">` 自己写边框 | 加 `oa-settings-section` + `__title` |
| 任务列表左栏 | 平铺一片，无层次 | `<div className="oa-list-panel">` + `__filters/__divider/__items/__footer` |
| 预览空态 | `<div>等待生成图片</div>` | `<div className="oa-preview-area oa-preview-area--centered">` 带占位/居中 |
| Markdown 内容 | 自己写 css | `<div className="oa-md-content">` |
| 任意图标 | emoji 字符 | `<span className="oa-icon" dangerouslySetInnerHTML={{__html: window.OpenAkitaIcons.xxx()}} />` |

---

## 5. 部署与开发循环（必读，否则会"改了没生效"）

### 5.1 源码 vs 运行时副本

OpenAkita 加载插件的**实际路径**是 `data/plugins/<id>/`，不是仓库源码 `plugins/<id>/`。
两种安装方式行为不同：

| 安装方式 | 关系 | 改源码后的影响 |
|---|---|---|
| 「开发者模式（软链）」 | `data/plugins/<id>` 是 `plugins/<id>` 的 symlink | 改源码 = 立即生效 |
| 普通安装 / `installer` 复制 | `data/plugins/<id>` 是独立副本 | **改源码不会同步**，需要：① 卸载重装；或 ② 手动 `Copy-Item -Force` |

**强烈建议**第一只插件就用「开发者模式」装。如果不能，迁移时记得对每个改动文件都同步：

```powershell
$src='D:\OpenAkita\plugins\my-plugin'
$dst='D:\OpenAkita\data\plugins\my-plugin'
Copy-Item "$src\plugin.py"  "$dst\plugin.py"  -Force
Copy-Item "$src\plugin.json" "$dst\plugin.json" -Force
Copy-Item "$src\ui\dist\index.html" "$dst\ui\dist\index.html" -Force
```

### 5.2 后端 / 前端 / iframe 三种生效条件

| 改动类型 | 生效条件 |
|---|---|
| `plugin.json`（i18n 字段、permissions） | **必须重启后端**（manifest 是启动时解析） |
| `plugin.py` / `*.py`（路由、数据、业务） | **必须重启后端**（Python module 不会热替换） |
| `ui/dist/index.html` / 自带 css / 自带 js | **强刷 iframe** 即可：`Alt+Shift+R`（dev only） 或切换插件再切回 |
| 宿主侧 host (`apps/setup-center`) | Vite HMR 自动；偶尔强刷整个壳 `Ctrl+F5` |
| `_sdk` 静态资源（ui-kit/i18n.js 等） | URL 上的 `?v=...` 改一下即可避绕缓存（也可 `Alt+Shift+R`） |

> **诊断口诀**：「中文环境却看到英文 fallback」99% 是后端没重启 → API 没透出 i18n 字段 → 前端 `pickI18n(undefined,...)` 走最后回退到 `name`。

### 5.3 验证清单（每个插件迁移完都跑一遍）

- [ ] 插件管理页：中文环境显示中文名 + 中文描述；切英文随之变化。
- [ ] 侧栏 Apps 项：随语言切换。
- [ ] 进入插件 → 切语言：tabs / 按钮 / 空态 / Toast / Settings 字段、ErrorBoundary 文案 全切。
- [ ] Guide 类带后端正文的 Tab：切语言后**正文也变**（重新拉接口验证）。
- [ ] 关键词区：双语并排显示。
- [ ] 模拟一次后端 5xx：UI 不白屏（被 ErrorBoundary 接住）。
- [ ] DevTools Network：每个 `_sdk/*.js`/`*.css` 都有 `?v=...` 后缀；首次返回 200，刷新返回 304。
- [ ] `git grep -E "[一-鿿]"` 在你的 `index.html` 里只剩**字典 zh 段** + 品牌名（如「万相」「千问」），其它代码区零中文硬编码。

---

## 6. 反模式 & 常见踩坑

| 反模式 | 现象 | 正解 |
|---|---|---|
| `useMemo(() => Date.now(), [pluginId])` 当 cacheBust | iframe 在切 tab 时反复重载 | 用一个 `reloadTick` state，只在显式动作时 +1 |
| 监听 `window.focus` 自动刷 iframe | 点击插件外区域瞬间白屏 5s | 取消监听，仅保留快捷键 `Alt+Shift+R` |
| `oa-config-banner` 放进 `.content` 内 | 压缩 tab 内容高度，滚动条诡异 | 移到 `.app` 直接子节点 |
| `truncate` + `leading-none` 混用英文标题 | "g/p/y" 的 descender 被裁 | 用 `leading-tight` + 一点点 `py-0.5` |
| 把 i18n key 同时写中英文（`"通义生图 / Tongyi Image"`） | 永远显示双语，无法做单语 UI | 拆成 zh/en 两条，由 `t()` 选择 |
| 后端用一份字符串混排中英文返回前端 | 切语言不生效 | 走 §3.6 的混合策略 |
| 复制 `i18n.js` 到插件目录 | 多份内核、locale 同步紊乱 | 永远 `<script src="/api/plugins/_sdk/ui-kit/i18n.js">` |
| 用 emoji 占位"以后再换 SVG" | 跨平台/字号下渲染不一致；i18n 翻译也会被影响 | 一律 `OpenAkitaIcons.xxx()`，缺图标就升级 icons.js |

---

## 7. Checklist（复制到 PR 模板）

```markdown
### Plugin UI / i18n / UI Kit migration checklist

- [ ] plugin.json: `display_name_zh/en`, `description_i18n`, `ui.title_i18n`
- [ ] index.html `<head>`: 引 styles.css / icons.js / i18n.js (with `?v=`)
- [ ] I18N_DICT { zh, en } 覆盖所有用户可见文案
- [ ] useT / useLocale 两个 hook 已加，函数组件用 t(key)
- [ ] _tr() shim 接管所有顶层非 React 文案
- [ ] 状态/模式枚举已拆成 STATUS_CLS + statusLabel(s, t)
- [ ] 所有 emoji → OpenAkitaIcons.xxx()
- [ ] 顶部 oa-config-banner 放 .app 直接子节点
- [ ] 设置分区使用 oa-settings-section
- [ ] 后端正文接口接受 ?locale=, 长文按 locale 单返,短词双语并排
- [ ] GuideTab 类组件 useEffect 依赖 [locale]
- [ ] 包了 PluginErrorBoundary,数据访问有 Array.isArray / typeof 兜底
- [ ] data/plugins/<id> 已与源码同步（或软链开发者模式装）
- [ ] 重启后端 + 强刷验证：见 §5.3 验证清单
```

---

## 8. 参考实现

`tongyi-image` 是首只完成全套迁移的插件。新插件迁移时建议直接对照阅读：

- `plugins/tongyi-image/plugin.json` — i18n 字段范例
- `plugins/tongyi-image/ui/dist/index.html` — `I18N_DICT` / `useT` / `useLocale` / `PluginErrorBoundary` 整套
- `plugins/tongyi-image/tongyi_prompt_optimizer.py` — 后端混合策略 i18n 数据组织
- `plugins/tongyi-image/plugin.py` — `/prompt-guide?locale=...` 路由

> 任何与本文档不一致的"惯例"以 `tongyi-image` 实现为准；
> 如果该实现存在某个不便复用的写法，请**优先升级脚手架（ui-kit + 本文档）**，
> 而不是在新插件里再"改写一遍"。

# finance-auto · 首版前端 UI

M1 W3 第一刀 React UI。**故意保持极简**（参考 `excel-maker` 插件）：
单文件 `dist/index.html` + 内联 React 18（CDN UMD）+ Babel standalone，
无 Vite 构建链，无 npm 依赖。后续在容量爆了之前可以平滑迁到 Vite。

## 集成方式

`plugin.json` 顶层加了 `ui` 段，指向 `ui/dist/index.html`。OpenAkita 的
`PluginManager._mount_plugin_ui` 会把整个 `ui/dist/` 目录挂到
`/api/plugins/finance-auto/ui/` 静态服务上；前端壳子（`PluginAppHost`）
通过 iframe 加载它，并使用 postMessage 桥发 `bridge:render-ready`
让加载罩立即收起。

## 开发与调试

* 改完 `index.html` / `_assets/styles.css` 后，直接刷新主程「插件管理」
  里的"重载"按钮即可（`PluginAppHost` 监听 `openakita:plugin-reloaded`
  事件，会自动 cache-bust 重新拉 iframe）。
* 单独在浏览器里打开 `index.html`（端口 5173 / 1420）时，`detectApiBase()`
  会回退到 `http://127.0.0.1:18900`，方便不通过主程壳子直接调试。

## 已经包含的能力（commit 1）

* 顶部栏（账套面包屑 + 期间选择器）
* hash 路由：`#/orgs` / `#/orgs/<id>` / `#/orgs/<id>/reports/<rid>`
* 共享 API 客户端（`api(method, path, body, opts)`）
* Toast、Modal、Drawer 三件套
* 极简灰白主题，跟随 prefers-color-scheme 切换深色

## 后续 commit 接入点

* commit 2 → `OrgListView` 内填入列表 + 创建对话框
* commit 3 → `OrgDetailView` 接入余额表 import Tab
* commit 4 → 报表生成 + `ReportView` 单元格追溯抽屉
* commit 5 → 增值税申报表 Tab

# MultiPost Extension bridge notes

记录日期：2026-05-15

## 结论

MultiPost-Extension 可以被 `omni-post` 复用，但稳定前提是：

- 必须在安装了 MultiPost 扩展的 Chrome/Edge 中运行。
- 最稳定的调用入口是顶层网页，不是 OpenAkita 插件 iframe。
- OpenAkita 桌面端 Tauri/WebView 不能直接运行 Chrome/Edge 扩展。
- MultiPost 自己还有一套 `trustedDomains` 白名单，和 Chrome/Edge 的“站点访问权限”不是一回事。

因此：

- iframe 内的 `omni-post` 页面适合展示状态、配置、任务管理。
- MultiPost 扩展检测、授权和发布动作应在顶层 `omni-post` 页面执行。
- 桌面端如需稳定发布，应优先走 Playwright/CDP 类真实浏览器自动化，而不是 MultiPost 扩展桥。

## MultiPost 的通信链路

MultiPost 当前源码中的网页扩展 API 链路：

```text
web page
  -> window.postMessage({ type: "request", action: "MULTIPOST_..." })
  -> MultiPost content script
  -> chrome.runtime.sendMessage(...)
  -> extension background
  -> response back to page
```

`omni-post` 需要发送当前协议格式：

```js
{
  type: "request",
  traceId: "...",
  action: "MULTIPOST_EXTENSION_CHECK_SERVICE_STATUS",
  data: {},
}
```

响应格式：

```js
{
  type: "response",
  traceId: "...",
  action: "...",
  code: 0,
  message: "success",
  data: {}
}
```

## 两类常见报错

### `MultiPost extension did not respond`

含义：网页没有收到 MultiPost content script 回应。

常见原因：

- 当前页面在 OpenAkita 插件 iframe 内，content script 没有稳定注入该 frame。
- 当前运行环境是 OpenAkita 桌面端/Tauri WebView，不是 Chrome/Edge。
- Chrome/Edge 扩展没有当前站点访问权限。

处理方式：

- 在 Chrome/Edge 中打开顶层 `omni-post` 插件页面。
- 确认 MultiPost 扩展允许当前站点访问。
- 桌面端不要走 MultiPost 扩展桥，改走 Playwright/CDP 引擎。

### `Untrusted origin`

含义：网页已经连上 MultiPost 扩展，但 MultiPost 自己的 `trustedDomains` 不信任当前 hostname。

注意：

- Chrome/Edge 的“允许所有网站”不等于 MultiPost 的 `trustedDomains`。
- MultiPost 默认只初始化 `multipost.app`。
- MultiPost 只匹配 hostname，不匹配协议、端口或路径。

正确白名单值示例：

```text
127.0.0.1
localhost
192.168.1.23
```

错误示例：

```text
http://127.0.0.1:18900
127.0.0.1:18900
http://localhost/api/plugins/omni-post/ui/
```

## 信任域授权

MultiPost 源码里并不是把所有域名写死，只是默认白名单写死为 `multipost.app`。额外域名通过 `tabs/trust-domain.html` 授权页写入扩展本地 storage。

`omni-post` UI 已提供兜底入口：

- 打开顶层检测页
- 打开 Chrome 授权页
- 打开 Edge 授权页

商店版扩展 ID：

```text
Chrome: dhohkaclnjgcikfoaacfgijgjgceofih
Edge:   ckoiphiceimehjkolnfffgbmihoppgjg
```

如果用户安装的是非商店版或开发版，扩展 ID 会不同，需要将该 ID 用于生成：

```text
chrome-extension://<extension-id>/tabs/trust-domain.html#<base64-json>
```

其中 `<base64-json>` 内容为：

```json
{
  "action": "MULTIPOST_EXTENSION_REQUEST_TRUST_DOMAIN",
  "origin": "127.0.0.1"
}
```

## 发布入口设计建议

当用户选择 `mp` 引擎，或 `auto` 解析到 MultiPost 可用时：

1. 如果当前页面在 iframe 中，发布按钮应提示用户打开顶层发布页。
2. 顶层页完成扩展检测和信任域授权。
3. 顶层页向 MultiPost 扩展发送发布请求。
4. 后端继续通过 `/mp/pending`、`/mp/ack`、任务状态表记录结果。

不要假设 iframe 内检测成功就能发布；检测失败的 iframe，发布大概率也会失败。

## 当前实现状态

`mp` 引擎任务不是后端直接调用扩展。真实链路是：

1. 后端创建任务并进入 `running`。
2. `MultiPostCompatEngine` 将任务转成 MultiPost 当前支持的 `SyncData`，action 为 `MULTIPOST_EXTENSION_PUBLISH`。
   平台名必须使用 MultiPost 的类型化 key，例如 `ARTICLE_WEIXIN`、`DYNAMIC_WEIXINCHANNEL`、`VIDEO_WEIXINCHANNEL`，不能直接传 `wechat_mp` 或 `wechat_channels`。
3. 顶层 `omni-post` 页面通过 `/mp/pending` 轮询待派发任务。
4. 顶层页面调用 MultiPost 扩展。
5. 扩展接受后，顶层页面调用 `/mp/ack`，后端才会把任务从 `running` 推进到终态。

因此使用 `mp` 引擎时，必须保持顶层 `omni-post` 页面打开。若只在桌面端或 iframe 中点击发布，后端会进入等待扩展 ack 的状态，但浏览器扩展不会被真正调用。

注意：`/mp/status` 是后端内存中的最近探测快照，服务重启后可能回到 `installed=false`。它只用于 `engine=auto` 的自动选路；用户显式选择 `engine=mp` 时，后端仍会把任务放入 `/mp/pending`，由顶层浏览器页面做实时扩展检测并派发。

MultiPost 发布不需要 `omni-post` 账号矩阵。账号登录态在浏览器扩展和目标平台页面里，因此 `engine=mp` 允许 `account_ids=[]`，后端只用内部占位账号 ID 记录任务。素材同样可选：纯文本、文章草稿或无媒体动态可以在 `asset_id=null` 时发布；只有视频、图片等媒体内容才需要上传素材。

MultiPost 支持一次传多个平台。`omni-post` 的 `engine=mp` 路径应创建一个批量任务，并把所有选中平台写入 payload 的 `_mp_platforms`，最终转成 MultiPost 的 `SyncData.platforms` 数组。不要为每个平台拆一个 MP 任务，否则会打开多个发布弹窗。

素材文件通过：

```text
/api/plugins/omni-post/assets/{asset_id}/file
```

暴露给浏览器扩展下载。该路由只允许读取插件数据目录下的已登记素材文件。

## MultiPost 发布弹窗的完成态

你看到的白色弹窗（标题类似“正在发布内容”）是 MultiPost-Extension 内部的
`tabs/publish.html`。它的源码链路是：

1. `MULTIPOST_EXTENSION_PUBLISH` 把 `SyncData` 暂存在扩展 background，并打开 `tabs/publish.html`。
2. 弹窗通过 `MULTIPOST_EXTENSION_PUBLISH_REQUEST_SYNC_DATA` 取回这份数据。
3. 弹窗把远程图片/视频等素材处理成 blob URL。
4. 弹窗再发送 `MULTIPOST_EXTENSION_PUBLISH_NOW`。
5. background 创建各平台标签页并把 tabs 列表返回给弹窗。
6. 弹窗把自己的 UI 改成“发布完成”。

关键限制：这个“发布完成”状态默认只存在于扩展弹窗内部，并没有稳定回传给最初发起调用的网页。因此 `omni-post` 当前只能可靠记录“MultiPost 已接收任务 / 平台标签已创建”，不能证明目标平台服务端最终已经发帖成功。

如果要拿到真正的弹窗完成回调，有两条路线：

- fork/patch MultiPost-Extension：在 `tabs/publish.tsx` 的 `handlePublishComplete` 之后回调 `omni-post` 的 `/mp/ack`。
- 用 CDP/webbridge 观察扩展弹窗和平台标签页，把弹窗状态或平台页面状态同步回后端。

## 和 webbridge/CDP 的区别

MultiPost 扩展桥：

- 复用用户日常 Chrome/Edge 登录态。
- 依赖 MultiPost content script 和扩展后台。
- 适合顶层网页调用。
- 不适合桌面 WebView 直接调用。

webbridge/CDP：

- 通过 Chrome DevTools Protocol 控制真实浏览器。
- 不依赖网页扩展 API。
- 更适合 OpenAkita 桌面端或后端发起的稳定自动化。

长期如果要让桌面端也稳定发布，优先考虑 Playwright/CDP/webbridge 路线。

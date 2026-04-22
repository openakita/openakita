# Dependency Gate — 一键安装系统依赖（FFmpeg / whisper.cpp / yt-dlp）

> 适用范围：所有 AI 媒体类插件（seedance-video、tongyi-image、subtitle-maker、
> video-translator、tts-studio、highlight-cutter 等）。
>
> 设计目标：让小白用户**无需打开终端、无需配 PATH**，就能让插件自动检测并安装
> 它需要的系统组件。开发者只需写一行 HTML（`<div data-oa-dep="ffmpeg">`），其余
> 由 SDK + 主程序统一接管。

---

## 1. 整体架构（一图）

```
┌────────────────────────── 插件 iframe ──────────────────────────┐
│                                                                 │
│   <div data-oa-dep="ffmpeg,whisper.cpp"></div>                  │
│             │                                                   │
│             ▼                                                   │
│   /api/plugins/_sdk/ui-kit/dep-gate.js   (UI Kit 横幅)          │
│             │ fetch                                              │
└─────────────┼───────────────────────────────────────────────────┘
              │
              ▼
┌────────────────────────── openakita-server ────────────────────┐
│   /api/plugins/_sdk/deps/check                                 │
│   /api/plugins/_sdk/deps/install   (SSE 流式)                  │
│   /api/plugins/_sdk/deps/audit-log                             │
│             │                                                   │
│             ▼                                                   │
│   openakita_plugin_sdk.contrib.DependencyGate                  │
│             │ 探测 / argv 安全调用 subprocess                    │
│             ▼                                                   │
│   winget / brew / apt / dnf / pip / 手动跳转                    │
└─────────────────────────────────────────────────────────────────┘
```

四层各司其职：

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 目录 | `openakita_plugin_sdk/contrib/dep_catalog.py` | **唯一**白名单：每个依赖能在哪些平台、用什么命令装 |
| 引擎 | `openakita_plugin_sdk/contrib/dep_gate.py` | 探测、安装命令构造、安全 argv、SSE 事件流 |
| HTTP | `src/openakita/api/routes/plugin_deps.py` | REST/SSE 网关、sudo 守卫、审计日志、并发锁 |
| UI | `openakita-plugin-sdk/src/openakita_plugin_sdk/web/ui-kit/dep-gate.js` | 声明式 `data-oa-dep` 横幅、确认弹窗、流式日志、复检 |

---

## 2. 插件接入（最小改动 = 2 行）

### 2.1 引入脚本

在插件 `ui/dist/index.html` 的 `<head>` 里，加一行：

```html
<script src="/api/plugins/_sdk/ui-kit/dep-gate.js"></script>
```

> 顺序：必须在 `bootstrap.js`、`icons.js`、`i18n.js` 之后；其它 ui-kit 模块前后均可。

### 2.2 声明依赖

在需要这些组件的页面位置（一般紧跟标题之后），放一个空 div：

```html
<!-- 单个依赖 -->
<div data-oa-dep="ffmpeg"></div>

<!-- 多个依赖（用逗号分隔） -->
<div data-oa-dep="ffmpeg,whisper.cpp"></div>
```

合法 id 必须在白名单里：`ffmpeg` / `whisper.cpp` / `yt-dlp`。
其它字符串会被前端忽略，并在控制台打印 `[OpenAkitaDepGate] unknown dep id`。

### 2.3 等待就绪（可选）

如果某个按钮在依赖未就绪时不应启用，监听全局事件即可：

```js
window.addEventListener("openakita:dep-ready", (e) => {
  if (e.detail && e.detail.id === "ffmpeg") {
    document.getElementById("renderBtn").disabled = false;
  }
});

// 或者主动查询缓存
if (window.OpenAkitaDepGate && OpenAkitaDepGate.isReady("ffmpeg")) {
  /* go */
}
```

dep-gate.js 也支持手动复检：

```js
OpenAkitaDepGate.refresh("ffmpeg"); // 触发一次后端探测，更新所有横幅
```

---

## 3. 已接入的插件

| 插件 | 声明的依赖 | 备注 |
| --- | --- | --- |
| `seedance-video` | `ffmpeg` | 长视频拼接、字幕烧入 |
| `subtitle-maker` | `ffmpeg`, `whisper.cpp` | 抽音轨 + 转写 |
| `video-translator` | `ffmpeg`, `whisper.cpp` | 转写 → 翻译 → 重配音 |
| `tts-studio` | `ffmpeg` | 多段 TTS 合并 |
| `highlight-cutter` | `ffmpeg` | 切片 + 转码 |

> `tongyi-image`、`avatar-speaker` 等纯调云 API 的插件**不需要**接入。

---

## 4. REST API 参考

所有接口前缀：`/api/plugins/_sdk/deps`。

### 4.1 `GET /check?ids=ffmpeg,whisper.cpp`

返回每个依赖的当前状态。结果按 `ids` 顺序：

```json
{
  "results": [
    {
      "id": "ffmpeg",
      "status": "found",
      "version": "6.1",
      "location": "C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe",
      "platform": "windows"
    },
    {
      "id": "whisper.cpp",
      "status": "missing",
      "version": null,
      "location": null,
      "platform": "windows"
    }
  ]
}
```

`status` 取值：`found` / `missing` / `error`。

### 4.2 `POST /install`（SSE）

请求体：

```json
{ "id": "ffmpeg", "method_index": 0 }
```

响应是 `text/event-stream`，每条事件形如：

```
event: progress
data: {"phase": "starting", "method": "winget", "message": "winget install ..."}

event: stdout
data: {"line": "Found Gyan.FFmpeg [Gyan.FFmpeg] Version 6.1"}

event: stderr
data: {"line": "Successfully installed"}

event: done
data: {"exit_code": 0, "duration_seconds": 87.3, "status_after": "found"}
```

服务器端约束：
- **同时只允许一次安装**（`asyncio.Lock`）。第二个请求会立刻收到
  `event: error` + 423 语义。
- 需要 sudo 的安装方法（Linux apt/dnf）必须由有 root 的进程发起，否则
  `event: error data: {"error": "needs_root"}`。
- 安装命令完全使用 argv 数组传递，**永远不走 shell**，杜绝命令注入。
- `method_index` 越界返回 `event: error data: {"error": "invalid_method"}`。

### 4.3 `GET /audit-log?limit=50`

按时间倒序返回最近 N 次安装尝试，每条包含：

```json
{
  "ts": "2026-04-19T14:22:31Z",
  "id": "ffmpeg",
  "method": "winget",
  "client_ip": "127.0.0.1",
  "exit_code": 0,
  "duration_seconds": 87.3,
  "status_after": "found"
}
```

落盘文件：`data/plugin_deps_audit.jsonl`。该文件**只追加**，方便审计与排查。

---

## 5. 扩展白名单（新增一个依赖）

举例：要支持 `ImageMagick`。**只改这两个文件**，禁止在插件内部塞自己的命令。

1. 在 `openakita-plugin-sdk/src/openakita_plugin_sdk/contrib/dep_catalog.py`
   末尾追加：

   ```python
   IMAGE_MAGICK = SystemDependency(
       id="imagemagick",
       display_name="ImageMagick",
       description="图像处理命令行工具，海报、拼图类插件使用。",
       probes=("magick", "convert"),
       version_argv=("magick", "-version"),
       version_regex=r"Version: ImageMagick\s+(\S+)",
       homepage="https://imagemagick.org/script/download.php",
       install_methods=(
           InstallMethod(
               platform="windows", strategy="winget",
               command=("winget", "install", "--id", "ImageMagick.ImageMagick",
                        "-e", "--accept-source-agreements", "--accept-package-agreements"),
               description="Install ImageMagick via winget.",
               requires_confirm=True, estimated_seconds=60,
           ),
           # macOS / Linux 类推
       ),
   )

   CATALOG = (FFMPEG, WHISPER_CPP, YT_DLP, IMAGE_MAGICK)
   ```

2. 在 `__init__.py` 把 `IMAGE_MAGICK` 加入 `from .dep_catalog import ...`
   并追加到 `__all__`。

3. 在插件 HTML 写 `<div data-oa-dep="imagemagick"></div>` 即可，**无需重启
   任何前端构建**。后端需要重启一次。

> 安全规范：每个 `InstallMethod.command` 必须是**字面量 tuple**，禁止字符串
> 拼接、禁止接受用户输入。Code Review 时会专门看这一点。

---

## 6. 国际化

dep-gate.js 自带一份 `oa.dep.*` 翻译字典，支持 `zh` / `en`，
插件无需手动注册。如需覆盖：

```js
OpenAkitaI18n.register({
  zh: { "oa.dep.banner.missing": "{name} 还没装，戳右边按钮一键搞定" },
  en: { "oa.dep.banner.missing": "{name} is missing — click to install." },
});
```

字典 key 一览（节选）：

| key | 用途 |
| --- | --- |
| `oa.dep.banner.checking` | 正在探测 |
| `oa.dep.banner.missing` | 未安装提示，`{name}` 占位 |
| `oa.dep.banner.ready` | 已就绪，`{name}` `{version}` |
| `oa.dep.banner.error` | 探测失败 |
| `oa.dep.btn.install` | "一键安装"按钮 |
| `oa.dep.btn.recheck` | "重新检测"按钮 |
| `oa.dep.btn.manual` | "手动安装文档" |
| `oa.dep.confirm.title` | 确认弹窗标题 |
| `oa.dep.confirm.body` | 确认弹窗正文，含 `{cmd}` `{seconds}` |
| `oa.dep.log.title` | 流式日志区标题 |

---

## 7. 故障排查

| 现象 | 原因 / 处理 |
| --- | --- |
| 横幅一直转圈不出结果 | 检查浏览器 Network，`/check` 是否 503。`openakita-plugin-sdk` 没装时 REST 层会返回 503，按提示安装 SDK。 |
| 点了"一键安装"立即报 `needs_root` | 当前进程不是 root，但安装方法是 apt/dnf。请用 sudo 启动 openakita-server，或在横幅上选"手动安装文档"。 |
| 安装结束后横幅仍显示"未安装" | PATH 缓存。点击横幅的"重新检测"按钮触发新一轮 `shutil.which`；若仍失败请重启 openakita-server。 |
| Windows 上 whisper.cpp 误报已装 | 说明你的版本仍在用旧 `main` 二进制名；Catalog 已禁用 `main` 探测，请把 `whisper.exe` 重命名/软链为 `whisper-cli.exe`。 |
| 想看真实安装命令 | 看 `data/plugin_deps_audit.jsonl`，每行就是一次完整记录。 |

---

## 8. 不会做的事（设计取舍）

- **不会**在用户没点"安装"按钮时偷偷调起 winget/brew/apt。
- **不会**接受插件传进来的任意命令——只接受 `dep_catalog.py` 白名单里的 id。
- **不会**给 `subprocess` 传 `shell=True`；所有 argv 都是字面量数组。
- **不会**自动 `sudo` 提权——POSIX 下需要 sudo 时直接拒绝并提示。
- **不会**在 Windows 上跑 `apt`，反之亦然——`current_platform()` 会先匹配方法列表。

这些限制是为了让一个插件 bug 永远无法升级成系统层面的安全事故。

---

## 9. 相关文件清单

```
openakita-plugin-sdk/src/openakita_plugin_sdk/contrib/dep_gate.py     # 引擎
openakita-plugin-sdk/src/openakita_plugin_sdk/contrib/dep_catalog.py  # 白名单
openakita-plugin-sdk/src/openakita_plugin_sdk/web/ui-kit/dep-gate.js  # UI
openakita-plugin-sdk/src/openakita_plugin_sdk/web/ui-kit/styles.css   # 样式
src/openakita/api/routes/plugin_deps.py                                # REST/SSE
src/openakita/api/server.py                                            # router 挂载点
data/plugin_deps_audit.jsonl                                           # 审计日志（运行时生成）
```

任何对系统依赖管理的修改，都从这 6 个位置之一展开——别建第二条路径。

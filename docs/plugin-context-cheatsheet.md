# OpenAkita 插件上下文速查 / Plugin Context Cheatsheet

> 用法：在和 AI 对话之前 `@docs/plugin-context-cheatsheet.md` 引入，让 AI 一次性吃下"参考库 + SDK + 模板插件"的全部约定，再提你具体要做的插件改动。
>
> 维护：当 SDK 顶层 API 变化、或模板插件 `tongyi-image` 改了关键约定时，请同步更新本文。
>
> **2026-04-22 起 SDK 已主动收缩到「最小插件壳子」定位**：`openakita_plugin_sdk.contrib`
> 整个子包已下沉，不再属于 SDK 公共 API。需要那些"轮子"的插件请在自家
> `_inline/` 子目录里 vendor 一份；只有 archive 区的旧插件才走
> `plugins-archive/_shared/` 的共享副本。详见
> [SDK Refocus Cleanup 计划](.cursor/plans/sdk_refocus_cleanup_b3b5f02d.plan.md)
> 与 [CHANGELOG `[Unreleased]`](../CHANGELOG.md) 段。
>
> **同期硬规则（SDK 0.7.0+）**：
> 1. **UI 必须自包含** — 每个 UI 插件的 `ui/dist/index.html` 引用的所有
>    `<script>` / `<link>` 必须落在自家 `ui/dist/_assets/`，走相对路径。
>    SDK 不再分发任何前端文件，host 也不再 mount `/api/plugins/_sdk/*`。
>    参考 `plugins/tongyi-image/` 与 `plugins/seedance-video/` 的 `_assets/` 布局。
> 2. **archive 复活模式** — 旧插件 (`plugins-archive/<name>/`) 如想复活
>    UI，需把 `plugins-archive/_shared/web-uikit/` 下用得到的 `bootstrap.js`
>    / `ui-kit/*` `cp` 一份到自家 `ui/dist/_assets/` 再改路径，不要再
>    引用 `/api/plugins/_sdk/*`。

---

## 一、参考代码库 `d:\OpenAkita_AI_Video`

定位：**前置调研/参考素材**，不是 OpenAkita 主仓代码。读它的目的：抄思路、抄 UX、抄 pipeline 写法，**不要**直接拷代码进 `plugins/`。

```
d:\OpenAkita_AI_Video
├── refs/                  # 开源项目源码（对照参考实现）
│   ├── comfyui            # 节点式 AI pipeline → 任务调度 / workflow 蓝图
│   ├── CutClaw            # 单文件级视频剪辑/合成（app.py 量级 37k）
│   ├── OpenMontage        # pipeline_defs / remotion-composer / skills 骨架
│   ├── Pixelle-Video      # 主题→短视频 单 app（Streamlit + FastAPI + ComfyKit）
│   ├── n8n                # 工作流编排
│   └── video-use          # 轻量 SKILL 化做法（SKILL.md / poster.html）
├── refs_web/              # 竞品 UX 抓取
│   ├── anygen_io
│   ├── canva_help
│   └── capcut_web
└── findings/              # 已沉淀的洞察文档
    ├── _summary_to_plan.md
    ├── workflow_blueprints.md
    ├── anygen_ux.md
    ├── capcut_canva_ux.md
    ├── cutclaw_deep.md
    ├── openmontage_deep.md
    ├── pixelle_video_deep.md
    └── video_use_deep.md
```

**何时读哪份**：

| 你在做                                              | 先翻                                                               |
| --------------------------------------------------- | ------------------------------------------------------------------ |
| 视频剪辑 / 转场 / 时间线                            | `refs/CutClaw/app.py`、`findings/cutclaw_deep.md`                  |
| 多步骤 pipeline / 编排                              | `refs/OpenMontage/pipeline_defs/`、`findings/openmontage_deep.md`  |
| 节点式 AI 工作流 / 任务调度                         | `refs/comfyui/`、`refs/n8n/packages/`                              |
| 极简 SKILL 化插件 / Web 海报                        | `refs/video-use/`、`findings/video_use_deep.md`                    |
| 前端 UX / 交互设计参考                              | `refs_web/`、`findings/anygen_ux.md`、`findings/capcut_canva_ux.md`|
| 想统一所有插件的产品形态                            | `findings/_summary_to_plan.md`、`findings/workflow_blueprints.md`  |
| **主题→短视频** / 线性 pipeline 模板方法 8 步骨架    | `findings/pixelle_video_deep.md`、`refs/Pixelle-Video/pixelle_video/pipelines/{linear,standard}.py` |
| 数字人口播 / 图生视频 / 动作迁移                    | `refs/Pixelle-Video/web/pipelines/{digital_human,i2v,action_transfer}.py` |
| HTML 模板渲染分镜（Playwright + 透明 PNG overlay）  | `refs/Pixelle-Video/pixelle_video/services/frame_html.py` + `templates/` |
| selfhost / runninghub 双形态 ComfyUI workflow 存储  | `refs/Pixelle-Video/workflows/`、`pixelle_video/services/comfy_base_service.py` |
| TTS audio.duration → video target duration 音画同步 | `refs/Pixelle-Video/pixelle_video/services/frame_processor.py`     |
| LLM 结构化输出三层 fallback（parse → md → 找 `{}`） | `refs/Pixelle-Video/pixelle_video/services/llm_service.py`         |
| 模板 DSL 自描述参数 `{{name:type=default}}`         | `refs/Pixelle-Video/pixelle_video/services/frame_html.py:173-228`  |

---

## 二、SDK 独立 + 脚手架（已就位）

包路径：`openakita-plugin-sdk/src/openakita_plugin_sdk/`

### 顶层入口（`from openakita_plugin_sdk import ...`）

- `PluginBase` / `PluginAPI` / `PluginManifest`（来自 `core.py`）
- `tool_definition`、`ToolHandler`
- `HOOK_NAMES` / `HOOK_SIGNATURES`
- 版本：`SDK_VERSION` / `PLUGIN_API_VERSION` / `PLUGIN_UI_API_VERSION` / `MIN_OPENAKITA_VERSION`
- 协议：`MemoryBackendProtocol` / `RetrievalSource` / `SearchBackend`
- 工具：`scaffold.py`（脚手架）、`testing.py`（`MockPluginAPI` / `assert_plugin_loads`）、`decorators.py`（`tool` / `hook` / `auto_register`）

### ⚠️ 旧 contrib 已下架（2026-04-22）

`openakita_plugin_sdk.contrib` 整个子包已在 SDK `0.7.0` 删除。需要那些
helper 的代码现在分布在两个位置：

| 你是哪种插件 | 拿 helper 的方式 |
| --- | --- |
| **一等公民**（`plugins/tongyi-image` / `plugins/seedance-video`） | 在自家 `*_inline/` 子目录里 **vendor 一份**，import 走 `from <plugin>_inline.X import ...`。例如 `from seedance_inline.upload_preview import add_upload_preview_route`。 |
| **archive 区**（`plugins-archive/<id>/`） | 通过 archive 入口自动注入的 `sys.path` bootstrap，import 走 `from _shared import ...` 或 `from _shared.tts import ...`。所有共享副本住在 `plugins-archive/_shared/`。 |
| **新写的插件** | 不要去翻找 contrib。先看自家是不是只用一两个函数，能 inline 就 inline；真要复用，复制到自家 `_inline/` 比建跨插件抽象层更便宜。 |

最小插件只需要这些 SDK 顶层导入（`from openakita_plugin_sdk import ...`）：

```python
from openakita_plugin_sdk import PluginBase, PluginAPI, PluginManifest
from openakita_plugin_sdk import tool_definition, ToolHandler
from openakita_plugin_sdk import HOOK_NAMES, HOOK_SIGNATURES
from openakita_plugin_sdk import (
    SDK_VERSION, PLUGIN_API_VERSION, PLUGIN_UI_API_VERSION, MIN_OPENAKITA_VERSION,
)
from openakita_plugin_sdk import MemoryBackendProtocol, RetrievalSource, SearchBackend
from openakita_plugin_sdk.decorators import tool, hook, auto_register
from openakita_plugin_sdk.testing import MockPluginAPI, assert_plugin_loads
from openakita_plugin_sdk.scaffold import scaffold_plugin
```

---

## 三、`plugins/tongyi-image` 模板（**抄它**）

### 目录约定

```
plugins/<plugin-id>/
├── plugin.json                   # 元数据 + permissions + provides + ui
├── plugin.py                     # PluginBase 入口（on_load / on_unload / 路由 / 工具）
├── <vendor>_client.py            # 厂商 HTTP 客户端（继承 BaseVendorClient）
├── <vendor>_models.py            # 模型/尺寸/预设静态表
├── <vendor>_prompt_optimizer.py  # 提示词优化（依赖 brain）
├── <vendor>_task_manager.py      # 任务存储（继承 BaseTaskManager，列名走白名单）
├── README.md                     # 给"小白用户"的说明
├── SKILL.md                      # 给"AI agent"的可调用说明（含 G1–G3、Trust Hooks）
├── tests/                        # conftest + 三类核心模块单测
│   ├── conftest.py
│   ├── test_<vendor>_client.py
│   ├── test_<vendor>_task_manager.py
│   └── test_<vendor>_prompt_optimizer.py
└── ui/dist/index.html            # 前端单文件打包产物（manifest 指向它）
```

### `plugin.json` 关键字段

```json
{
  "id": "<plugin-id>",
  "name": "<EN Display Name>",
  "version": "0.x.0",
  "description": "...",
  "display_name_zh": "<中文名>",
  "display_name_en": "<EN Name>",
  "description_i18n": { "zh": "...", "en": "..." },
  "type": "python",
  "entry": "plugin.py",
  "author": "OpenAkita",
  "category": "creative",
  "tags": ["..."],
  "permissions": [
    "tools.register", "routes.register", "hooks.basic",
    "config.read", "config.write", "data.own", "brain.access"
  ],
  "requires": {
    "openakita": ">=1.27.0",
    "plugin_api": "~2",
    "plugin_ui_api": "~1",
    "sdk": ">=0.6.0,<1.0.0"
  },
  "provides": {
    "tools": ["<plugin>_create", "<plugin>_status", "<plugin>_list"],
    "routes": true
  },
  "ui": {
    "entry": "ui/dist/index.html",
    "title": "<中文标题>",
    "title_i18n": { "zh": "...", "en": "..." },
    "sidebar_group": "apps",
    "permissions": ["upload", "download", "notifications", "theme", "clipboard"]
  }
}
```

### `plugin.py` 模板套路（**死记**）

- `on_load(api)`：
  1. 存 `self._api = api`
  2. `data_dir = api.get_data_dir()`
  3. 建 `self._tm = TaskManager(data_dir / "<plugin>.db")`
  4. 建 `APIRouter` → `self._register_routes(router)` → `api.register_api_routes(router)`
  5. `api.register_tools([...], handler=self._handle_tool)`
  6. `api.spawn_task(self._async_init(), name="<plugin>:init")`
- `_async_init`：`tm.init()` → 读 config 拿 API key → 建 vendor client → `_start_polling()`
- `on_unload`：**必须 async**
  1. cancel `_poll_task` 并 `await`（吞 `CancelledError`）
  2. `await self._client.close()`
  3. `await self._tm.close()`
  4. 全部用 try/except 包住，避免 Windows `WinError 32`
- 后台任务**一律走** `api.spawn_task(coro, name=...)`，不裸用 `asyncio.create_task`（host unload 时会统一 cancel + drain）
- 上传：`add_upload_preview_route(router, base_dir=data_dir/"uploads")`，响应里给 `url=build_preview_url(<plugin-id>, filename)`，base64 仅在 `<10MB` 时回传
- 存储统计：`await collect_storage_stats(dir, max_files=20000, sample_paths=0, skip_hidden=True)`，永不卡 UI
- UI 推送：`api.broadcast_ui_event("task_update", {"task_id": ..., "status": ...})`
- 文件下载/预览：`api.create_file_response(source, filename=..., media_type=..., as_download=bool)`
- 提示词优化：**先**用 `api.has_permission("brain.access")` 区分两类失败 — 没权限要提示用户去插件管理授权，有权限但 `get_brain()` 仍返回 `None` 才是 LLM 不可用：

  ```python
  if not api.has_permission("brain.access"):
      return {"ok": False, "error": "AI 优化未授权：缺少 brain.access 权限，请到插件管理授予后重试"}
  brain = api.get_brain()
  if not brain:
      return {"ok": False, "error": "LLM 不可用：主进程未注入 brain"}
  ```

  原因：`get_brain()==None` 同时覆盖了"没权限"和"主机没 brain"两种情况，但解决路径完全不同（点一下授权 vs 改 LLM 配置），合并提示会让用户瞎猜。Host 在插件加载时已自动把 manifest 声明、未授权的高级权限填入 `pending_permissions`，前端管理页会立即给出授权按钮，不必等用户撞到错误。
- 长任务标准状态机（异步任务）：

```
prompt + (可选 ref) → vendor.POST → task_id →
  poll loop (默认 10s) → status==SUCCEEDED →
    extract URLs → (可选 auto_download) → 落盘 + broadcast task_update
```

### `SKILL.md` 必备小节（agent 能正确调用的关键）

1. frontmatter：`name` + `description` + `env_any: [<API_KEY 名>]`
2. **是什么 / What**
3. **何时用 / When**（含"不要用于"反向指引）
4. **工具 / Tools**（带签名 `tool_name({args})`）
5. **模式 / Modes** 表（mode → 描述 → 主流模型）
6. **流程 / Pipeline**（ASCII 流程图）
7. **Quality Gates (G1–G3)** — 至少 3 道：API Key 配置、上传路径防遍历、异常落库 + UI 兜底
8. **Trust Hooks**（钱花在哪 / 数据流向 / 出错怎么办 / 远程依赖）
9. **已知坑 / Known Pitfalls**
10. **安全升级 changelog**（每个 sprint 的关键加固）

### `README.md` 必备小节（给小白用户）

1. 一句话定位
2. "给小白用户" — 5 步上手
3. 三大特点
4. 配置表（字段 / 默认 / 说明）
5. API 速查（curl 示例）
6. 测试现状（坦白哪些还没写）
7. 相关插件交叉引用

### `tests/` 覆盖维度（写新插件时跟齐）

- `test_<vendor>_client.py` — HTTP 行为 + 错误分类（覆盖 `ERROR_KIND_*`）
- `test_<vendor>_task_manager.py` — DB CRUD + 列名白名单 + 轮询状态机
- `test_<vendor>_prompt_optimizer.py` — brain mock
- `conftest.py` — 共享 fixture：tmp `data_dir` / mock `PluginAPI` / mock httpx

### UI 约定

- 打包成**单 HTML 入口** `ui/dist/index.html`
- `plugin.json.ui.entry` 直接指向该文件
- 后台路由通过 `/api/plugins/<plugin-id>/...` 暴露
- 上传图片渲染走 `<img src="/api/plugins/<plugin-id>/uploads/<file>">`（已由 `add_upload_preview_route` 兜底）

---

## 四、插件矩阵 v3（2026-04-22 SDK Refocus 后）

### A. 一等公民（`plugins/`，CI 必跑、SDK 升级强制跟齐）

| 插件 | 版本 | 关键能力 | 自带 `_inline/` |
| --- | --- | --- | --- |
| `tongyi-image` | 0.3.x | DashScope wanx2 / qwen-image。**新插件抄它的 `plugin.py` / UI**。 | `tongyi_inline/{upload_preview, storage_stats}.py` |
| `seedance-video` | 1.2.x | 字节 Seedance 文生视频 / 图生视频，含 long-video 链式生成。 | `seedance_inline/{vendor_client, upload_preview, storage_stats, llm_json_parser, parallel_executor}.py` |
| `ecommerce-image` | 0.3.0 | 19 个电商场景 feature（主图 / 详情页 / 海报 / 视频）；DashScope + Ark 双 provider。**v0.3.0 UI 结构性重构**：对齐 tongyi-image 4-tab 布局（创建/任务列表/提示词教学/设置），split-layout 左表单右预览，两层 mode-btn 选模块+功能，TasksTab 独立 tab。 | `ecom_*.py` 同目录平铺（无 `_inline/` 子目录） |
| `avatar-studio` | 1.0.0 | DashScope 数字人工作室：照片说话 / 视频换嘴 / 视频换人 / 数字人合成。`wan2.2-s2v` + `videoretalk` + `wan2.2-animate-mix` + `wan2.5-i2i-preview` + `cosyvoice-v2` 全链路。**接手已 archive 的 `avatar-speaker`**。 | `avatar_studio_inline/{vendor_client, upload_preview, storage_stats, llm_json_parser, parallel_executor}.py` |
| `clip-sense` | 1.0.0 | AI 视频剪辑：高光提取 / 静音精剪 / 段落拆条 / 口播精编。DashScope Paraformer ASR + Qwen 分析 + 本地 FFmpeg。7 步 pipeline，4 Tab UI（对标 tongyi-image）。 | `clip_sense_inline/{vendor_client, upload_preview, storage_stats, llm_json_parser}.py` |

> 这些插件**不**从 `openakita_plugin_sdk.contrib` import 任何东西（contrib 已删）。所有 helper 都在自家 `_inline/` 里 vendor 一份。

### B. Archive 区（`plugins-archive/`，**不是**一等公民）

> 不被 host 自动加载（host 只扫 `data/plugins/`）；不接受 issue；不主动跟 SDK 升级；CI 不跑。手动启用方式见 `plugins-archive/README.md`。

| 类别 | 插件 |
| --- | --- |
| 图像生成 | `image-edit` · `local-sd-flux` · `smart-poster-grid` · `poster-maker` （`ecommerce-image` 已于 2026-04-22 复活回 `plugins/` 一等公民） |
| 视频生成 | `ppt-to-video` · `shorts-batch` · `storyboard` |
| 视频处理 | `highlight-cutter` · `video-bg-remove` · `video-color-grade` · `video-translator` · `subtitle-maker` |
| 音频口播 | `avatar-speaker`（已由一线 `avatar-studio` 接手）· `tts-studio` · `dub-it` · `bgm-mixer` · `bgm-suggester` |
| 转录归档 | `transcribe-archive` |

它们继续依赖的 helper 集中在 `plugins-archive/_shared/`：
`task_manager.py` · `vendor_client.py` · `errors.py` · `upload_preview.py` ·
`storage_stats.py` · `ui_events.py` · `render_pipeline.py` · `llm_json_parser.py` ·
`ffmpeg.py` · `verification.py` · `source_review.py` · `provider_score.py` ·
`slideshow_risk.py` · `intent_verifier.py` · `cost_estimator.py` · `quality_gates.py` ·
`tts/` · `asr/`。

### C. SDK 参考区（`openakita-plugin-sdk/staging/contrib/`）

12 个**没有任何插件**消费的旧 contrib 模块作为代码档案保留：
`agent_loop_config` · `checkpoint` · `cost_tracker` · `cost_translation` ·
`delivery_promise` · `dep_catalog` · `dep_gate` · `env_any_loader` ·
`parallel_executor` · `prompt_optimizer` · `prompts` · `tool_result`
（含 `data/prompts/` 5 个文件）。**不是**可 import 的包，CI 不跑，详见
`openakita-plugin-sdk/staging/contrib/README.md`。

---

## 五、给 AI 的硬性提醒（每次改插件前默念）

1. **先读** `plugins/tongyi-image/{plugin.json,plugin.py,SKILL.md}` 对齐写法。
2. **不要去翻 `openakita_plugin_sdk.contrib`**——它已下架（SDK 0.7.0）。需要的 helper 自己 vendor 进 `<plugin>_inline/`。
3. 后台任务必须 `api.spawn_task(...)`；`on_unload` 必须 async + 资源清理三件套。
4. SQL 列名走**白名单**（参考 `tongyi_task_manager._UPDATABLE_COLUMNS`），杜绝注入。
5. 上传/下载文件路径必须 `path.relative_to(base_dir)` 校验，防遍历。
6. 异常一律落库 `error_message` + `broadcast_ui_event("task_update", {"status": "failed"})` 兜底。
7. 写完逻辑必须配套：`SKILL.md` 更新 + `README.md` 更新 + `tests/` 至少补一个用例。
8. 写厂商 HTTP 客户端时，复用 `seedance_inline/vendor_client.py` 的 retry / timeout / `ERROR_KIND_*` 套路（直接复制再裁剪，胜过新建跨插件抽象层）。
9. 任何 LLM 返回的 JSON，统一用 `seedance_inline/llm_json_parser.py` 风格的 `parse_llm_json*` 三层 fallback 解析（**不要** `json.loads` 直冲）。
10. UI 事件名前缀已由 host 加 `<plugin-id>:`，前端订阅时按 `strip_plugin_event_prefix` 的实现自行剥离。

---

## 六、提示模板（你抛问题时可以直接套）

> "**插件名**：`<plugin-id>`
> **目标**：<一句话目标>
> **当前问题**：<现象 / 报错 / 缺什么>
> **期望**：<完成后的可观测效果>
> 请按 `docs/plugin-context-cheatsheet.md` 的约定改，必要时拆 todo。"

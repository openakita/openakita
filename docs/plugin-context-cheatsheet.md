# OpenAkita 插件上下文速查 / Plugin Context Cheatsheet

> 用法：在和 AI 对话之前 `@docs/plugin-context-cheatsheet.md` 引入，让 AI 一次性吃下"参考库 + SDK + 模板插件"的全部约定，再提你具体要做的插件改动。
>
> 维护：当 SDK contrib 新增/重命名公共能力，或模板插件 `tongyi-image` 改了关键约定时，请同步更新本文。

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

### AI 媒体类基础设施（**必须用** `from openakita_plugin_sdk.contrib import ...`）

> 铁律：**所有插件统一从 contrib 导入这些"轮子"，不要拷贝到插件目录里**。一旦有重复造轮子的 PR，直接打回。

| 主题         | 关键导出                                                                                                  |
| ------------ | --------------------------------------------------------------------------------------------------------- |
| 任务/数据    | `BaseTaskManager`, `TaskRecord`, `TaskStatus`, `Checkpoint`, `take_checkpoint`, `restore_from_snapshot`   |
| 厂商客户端   | `BaseVendorClient`, `VendorError`, `ERROR_KIND_AUTH/CLIENT/MODERATION/NETWORK/NOT_FOUND/RATE_LIMIT/SERVER/TIMEOUT/UNKNOWN` |
| 错误体验     | `ErrorCoach`, `ErrorPattern`, `RenderedError`                                                             |
| 提示词       | `PromptOptimizer`, `load_prompt`, `render_prompt`, `list_prompts`, `PromptNotFound`                       |
| 意图校验     | `IntentVerifier`, `IntentSummary`, `EvalResult`                                                           |
| 质量门禁     | `QualityGates`, `GateResult`, `GateStatus`                                                                |
| 计费         | `CostEstimator`, `CostBreakdown`, `CostPreview`, `to_human_units`                                         |
| 计费跟踪     | `CostTracker`, `CostEntry`, `CostSnapshot`, `CostSummary`, `Adjustment`, `ApprovalRequired`, `InsufficientBudget`, `DuplicateReservation`, `ReservationNotFound` |
| 计费翻译     | `translate_cost`, `register_cost_template`, `get_cost_template`, `CostTemplate`, `COST_TRANSLATION_MAP`   |
| 流水线/并发  | `build_render_pipeline`, `RenderPipeline`, `run_parallel`, `summarize_parallel`, `ParallelResult`, `ParallelSummary`, `AgentLoopConfig`, `DEFAULT_AGENT_LOOP_CONFIG` |
| FFmpeg       | `run_ffmpeg(_sync)`, `ffprobe_json(_sync)`, `auto_color_grade_filter`, `AUTO_GRADE_PRESETS`, `get_grade_preset`, `list_grade_presets`, `sample_signalstats(_sync)`, `resolve_binary`, `FFmpegError`, `FFmpegResult`, `GradeStats` |
| 依赖系统     | `DependencyGate`, `DepStatus`, `InstallEvent`, `InstallMethod`, `SystemDependency`, `current_platform`, `DEP_CATALOG`, `DEP_CATALOG_BY_ID`, `FFMPEG`, `WHISPER_CPP`, `YT_DLP` |
| Web/UI       | `add_upload_preview_route`, `build_preview_url`, `DEFAULT_*_EXTENSIONS`, `UIEventEmitter`, `strip_plugin_event_prefix`, `collect_storage_stats`, `StorageStats` |
| 解析         | `parse_llm_json` / `parse_llm_json_array` / `parse_llm_json_object`, `load_env_any`, `EnvAnyEntry`        |
| 审稿/校验    | `review_source` / `review_video` / `review_image` / `review_audio`, `ReviewIssue`, `ReviewReport`, `ReviewThresholds`, `Verification`, `merge_verifications`, `render_verification_badge`, `LowConfidenceField`, `KIND_*`, `BADGE_*` |
| 评估打分     | `ProviderScore`, `score_providers`, `SlideshowRisk`, `evaluate_slideshow_risk`, `DeliveryPromise`, `validate_cuts`, `ToolResult` |

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
    "plugin_api": "~1",
    "plugin_ui_api": "~1",
    "sdk": ">=0.4.0"
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
- 提示词优化：`brain = api.get_brain()`；没 brain 就友好降级返回 `{"ok": False, "error": "LLM 不可用..."}`
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

## 四、`plugins/` 现状（21 个插件）

| 类别       | 插件                                                                                                              |
| ---------- | ----------------------------------------------------------------------------------------------------------------- |
| 图像生成   | `tongyi-image`(模板) / `image-edit` / `local-sd-flux` / `ecommerce-image` / `smart-poster-grid` / `poster-maker`  |
| 视频生成   | `seedance-video` / `ppt-to-video` / `shorts-batch` / `storyboard`                                                 |
| 视频处理   | `highlight-cutter` / `video-bg-remove` / `video-color-grade` / `video-translator` / `subtitle-maker`              |
| 音频/口播  | `tts-studio` / `dub-it` / `avatar-speaker` / `bgm-mixer` / `bgm-suggester`                                        |
| 转录归档   | `transcribe-archive`                                                                                              |

---

## 五、给 AI 的硬性提醒（每次改插件前默念）

1. **先读** `plugins/tongyi-image/{plugin.json,plugin.py,SKILL.md}` 对齐写法。
2. **能从 `openakita_plugin_sdk.contrib` 导入的，绝不在插件里重写**。
3. 后台任务必须 `api.spawn_task(...)`；`on_unload` 必须 async + 资源清理三件套。
4. SQL 列名走**白名单**（参考 `tongyi_task_manager._UPDATABLE_COLUMNS`），杜绝注入。
5. 上传/下载文件路径必须 `path.relative_to(base_dir)` 校验，防遍历。
6. 异常一律落库 `error_message` + `broadcast_ui_event("task_update", {"status": "failed"})` 兜底。
7. 写完逻辑必须配套：`SKILL.md` 更新 + `README.md` 更新 + `tests/` 至少补一个用例。
8. 改厂商客户端前先看 `BaseVendorClient` 已经覆盖了哪些 `ERROR_KIND_*`，复用它的 retry / timeout / classify 逻辑。
9. 任何 LLM 返回的 JSON，统一用 `parse_llm_json` 系列解析，不要自己 `json.loads`。
10. UI 事件名前缀已由 host 加 `<plugin-id>:`，前端订阅时用 `strip_plugin_event_prefix` 工具解。

---

## 六、提示模板（你抛问题时可以直接套）

> "**插件名**：`<plugin-id>`
> **目标**：<一句话目标>
> **当前问题**：<现象 / 报错 / 缺什么>
> **期望**：<完成后的可观测效果>
> 请按 `docs/plugin-context-cheatsheet.md` 的约定改，必要时拆 todo。"

# 漫剧工作室 / Manga Studio

故事 → 剧本 → 分镜 → 漫画动画视频，一站式 AI 漫剧生产线。
A one-stop AI manga-drama pipeline: story → script → panels → animated video.

| | |
|---|---|
| **版本** | `1.0.0` |
| **SDK 范围 / SDK range** | `>=0.7.0,<0.8.0` |
| **Plugin API** | `~2` / UI API `~1` |
| **入口 / Entry** | `plugin.py` (`PluginBase`) + `ui/dist/index.html` |
| **总测试 / Tests** | 348 (unit + route + pipeline) |

## 给小白用户 / Quick start

1. 在 **Settings** 标签里至少配好一组凭证：
   - **直接后端**：`Ark API Key` + `DashScope API Key`
   - **工作流后端**：`RunningHub API Key`（云端）或 `ComfyUI URL`（本地）+ 三个工作流 ID / 路径
2. 打开「**Studio**」标签页，点击右上角「📚 Templates」一键选一个故事模板
3. 调整「Panels / Seconds per Panel / Backend」，看右侧成本预估
4. 点【**生成漫剧**】— 后端异步运行 8 步流水线，进度推送到右下角任务列表
5. 完成后视频会落到 `data_dir/manga-studio/episodes/<ep_id>/final.mp4`，UI 自动给出预览链接

## Quick start (English)

1. In **Settings**, fill at least one credential set:
   - **Direct backend**: `Ark API Key` + `DashScope API Key`
   - **Workflow backend**: `RunningHub API Key` (cloud) or `ComfyUI URL` (local) plus three workflow IDs / paths
2. Open the **Studio** tab → click 📚 Templates to seed a story
3. Tweak panel count, seconds per panel, backend; watch the cost panel on the right
4. Hit **Generate Drama** — the backend runs an 8-step pipeline asynchronously, progress streams into the task list
5. The muxed MP4 lands at `data_dir/manga-studio/episodes/<ep_id>/final.mp4` with a preview URL surfaced in the UI

## 五大特性 / Five highlights

- **双后端平行 / Dual parallel backends**
  - 直接 Seedance（Ark `seedance-1.0-lite-i2v` + DashScope `wan2.7-image` + Edge-TTS）和 ComfyUI / RunningHub 工作流均可走完整闭环，Settings 里二选一即可
  - Either Seedance API or ComfyUI workflows can run the full closed-loop pipeline; switch in Settings
- **8 步流水线 / 8-step pipeline** —
  `setup → script → storyboard → panel_image → panel_video → tts → assemble → finalize`
- **角色一致性 / Character consistency** — 角色卡（参考图 + appearance JSON）注入生图 / 动画 prompt，跨集复用
- **剧集管理 / Series management** — 多集系列共用默认风格 / 比例 / 后端 / 角色，一次配置永久受益
- **成本守门员 / Cost gate** — 每次提交先估价（DashScope 张数 + Ark 秒数 + Edge-TTS 字符数 + Qwen tokens），超阈值需二次确认

## 后端切换矩阵 / Backend matrix

| 步骤 / Step    | `direct` 直接后端                     | `runninghub` / `comfyui_local` 工作流后端 |
|---|---|---|
| 剧本拆分       | `brain.access` (Qwen-Plus 优先)        | 同左 (LLM 不依赖后端选择)                 |
| panel_image    | DashScope `wan2.7-image` (多参考图)    | ComfyKit 提交 `runninghub_workflow_image` / 本地工作流 JSON |
| panel_video    | Ark `seedance-1.0-lite-i2v`            | ComfyKit 提交 `runninghub_workflow_animate` / 本地工作流 JSON |
| t2v fallback   | Ark `seedance-1.0-lite-t2v`            | ComfyKit 提交 `runninghub_workflow_t2v` / 本地工作流 JSON |
| TTS            | Edge-TTS / DashScope `cosyvoice-v2`    | 同左 (TTS 不依赖后端选择)                 |
| 拼接           | FFmpeg                                  | 同左 (FFmpeg 不依赖后端选择)             |

## 配置 / Config (`PUT /settings`)

| 字段 / Key | 默认 / Default | 说明 / Notes |
|---|---|---|
| `ark_api_key` | `""` | Volcengine Ark Key — 直接后端必填 |
| `ark_endpoint_id` | `""` | 可选 — 走 endpoint 而不是公开 model id |
| `dashscope_api_key` | `""` | 阿里 DashScope Key — 直接后端生图 / TTS |
| `dashscope_region` | `"beijing"` | 区域，目前仅占位 |
| `tts_engine` | `"edge"` | `edge` (免费) 或 `cosyvoice` (DashScope) |
| `comfy_backend` | `"runninghub"` | 工作流细分：`runninghub` 云端 / `comfyui_local` 本地 |
| `runninghub_api_key` | `""` | RunningHub Key — 云端工作流必填 |
| `runninghub_workflow_image` | `""` | RH 工作流 ID — panel image 生成 |
| `runninghub_workflow_animate` | `""` | RH 工作流 ID — image-to-video |
| `runninghub_workflow_t2v` | `""` | RH 工作流 ID — text-to-video fallback |
| `comfyui_url` | `"http://127.0.0.1:8188"` | 本地 ComfyUI 服务地址 |
| `comfyui_workflow_image` | `""` | 本地工作流 JSON 文件路径 — panel image |
| `comfyui_workflow_animate` | `""` | 本地工作流 JSON 文件路径 — animate |
| `comfyui_workflow_t2v` | `""` | 本地工作流 JSON 文件路径 — t2v |
| `cost_threshold_cny` | `5.0` | 单次生成成本超出此值要求二次确认 |
| `oss_*` | `""` | 可选 — 上传角色参考图至 OSS / S3 (avatar-studio inline) |

> 🔒 **安全提示 / Security note**：UI 在 GET 时把 `*_api_key` 替换为 `••••••••`。回写时若值依然是星号，前端的 `isRedacted` 守卫会自动跳过，避免覆盖真实 Key。

## API 速查 / API cheat-sheet

```text
# 角色 / Characters
GET    /characters
POST   /characters
GET    /characters/{id}
PUT    /characters/{id}
DELETE /characters/{id}

# 剧集 / Series
GET    /series
POST   /series
GET    /series/{id}
PUT    /series/{id}
DELETE /series/{id}

# Episode 与 Pipeline
POST   /episodes              # 启动一次完整流水线
GET    /episodes?series_id=…
GET    /episodes/{id}
DELETE /episodes/{id}

# 任务 / Tasks
GET    /tasks?limit=10
GET    /tasks/{id}
POST   /tasks/{id}/cancel

# 辅助 / Helpers
GET    /catalog               # 视觉风格 / 比例 / 角色枚举
GET    /templates             # 5 个内置故事模板
POST   /cost-preview          # 估算费用，不写 DB
POST   /workflows/probe       # 不计费的工作流后端连通性自检
GET    /healthz               # 后端就绪状态
GET    /storage-stats
PUT    /settings              # 写入配置
```

## 工具 / Tools (LLM tool-calling)

```
manga_create_series, manga_create_episode, manga_list_episodes,
manga_episode_status, manga_create_character, manga_list_characters,
manga_quick_drama, manga_split_script, manga_render_panel,
manga_cost_preview, manga_workflow_test
```

`manga_quick_drama` 是「跑完整流水线」的快捷工具，等同于 `POST /episodes`。

## 模块速览 / Module map

```
plugins/manga-studio/
├── plugin.json                   # 元数据 + 权限声明
├── plugin.py                     # PluginBase 入口、tools/routes 注册
├── manga_models.py               # 视觉风格 / 声音 / 价格表 / error hints
├── manga_templates.py            # 5 个预置故事模板（Phase 4.4）
├── manga_task_manager.py         # SQLite (aiosqlite) — characters/series/episodes/tasks
├── manga_pipeline.py             # 8 步流水线编排器 + 后端分流
├── prompt_assembler.py           # 拼装生图 / 动画 prompt
├── script_writer.py              # LLM 拆分剧本（Brain `access` 调用）
├── direct_ark_client.py          # 直接后端：Volcengine Ark
├── direct_wanxiang_client.py     # 直接后端：DashScope Wanxiang 生图
├── tts_client.py                 # 直接后端：Edge-TTS / CosyVoice
├── comfy_client.py               # 工作流后端：comfykit 包装（懒导入）
├── ffmpeg_service.py             # 视频拼接 / 字幕烧录 / BGM 混音
├── manga_inline/                 # vendored helpers（avatar-studio + seedance-video 公共模块）
│   ├── vendor_client.py
│   ├── oss_uploader.py
│   ├── upload_preview.py
│   ├── llm_json_parser.py
│   ├── parallel_executor.py
│   └── storage_stats.py
├── ui/dist/index.html            # 单文件 React 18 (CDN Babel) UI
├── icon.svg
└── tests/                        # 348 个测试
```

## 流水线步骤详解 / Pipeline steps

```
            ┌───────────────────────── direct backend ───┐
story  →    │                                            │
            ▼                                            ▼
[setup] → [script] → [storyboard] → [panel_image] → [panel_video] → [tts] → [assemble] → [finalize]
                                              │             │
                                              ▼             ▼
                         ┌──── workflow backend ──────────────────┐
                         │ comfykit.execute(workflow_image / …)   │
                         └────────────────────────────────────────┘
```

每一步可独立失败 → `tasks` 表记录 `error_kind / error_message / error_hints_json`。
T2V fallback：当 I2V 因 `moderation_face` (人脸合规) 失败时自动切到 T2V 重试。

## 错误码 / Error kinds

`auth | quota | rate_limit | network | timeout | validation | moderation_face | content_violation | dependency | unknown`

每个 kind 在 `manga_models.ERROR_HINTS` 里有对应的中英文用户友好提示，UI 直接渲染。

## 测试 / Tests

```bash
# 全套（348 个）
pytest plugins/manga-studio/tests/ -q

# 模板单文件
pytest plugins/manga-studio/tests/test_templates.py -v

# 集成（需 Key，默认 skip）
pytest plugins/manga-studio/tests/ -m integration
```

## 开发约定 / Conventions

- **Ruff**：line-length 100，rules `E F I N W UP B C4 SIM`，目标 py311
- **mypy**：宽松（`ignore_errors = true`）
- **Commit 信息**：英文，遵循 `manga-studio: <Phase X.Y> — <subject>` 格式
- **永远不在测试里点真 API**：所有外部调用都 mock；只有 `-m integration` 才走真链路

## License

与 OpenAkita 主仓库一致 — see project root `LICENSE`.

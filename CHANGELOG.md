# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-04-22

### Changed — `plugins/ecommerce-image` v0.3.0：UI 结构性重构（对齐 tongyi-image 4-tab 布局）

- **Tab 结构重构**：6 tab (4 模块 + 教学 + 设置) → 4 tab (创建 / 任务列表 /
  提示词教学 / 设置)，与 tongyi-image 完全对齐。
- **布局改造**：删除 sidebar + form-panel + result-panel 旧布局，全面改用
  `split-layout` (split-left 表单 / split-right 预览) 拓宽显示区域。
- **Feature 选择**：侧栏列表 → 两层 `mode-btn`（第一层选模块
  video/image/detail/poster，第二层选功能）。
- **任务列表独立**：从创建页底部内联 TaskList 抽取为独立 "任务列表" tab，
  split-layout 左列表筛选 + 右详情预览，自带 15s 轮询。
- **PromptGuide 修正**：全部 section 默认折叠，统一 `Collapsible` 组件，
  修复内容重叠排版。
- **SettingsPage 对齐**：`oa-settings-section` 分区卡片化 + `grid-2` 布局 +
  `oa-config-field-callout` 未配 Key 高亮。
- **AI 优化按钮**：从 textarea 下方移至与 label 同行，节省垂直空间。
- **CSS 精简**：删除 `.sidebar` / `.work-area` / `.form-panel` / `.result-panel`
  / `.main-layout` 等旧类，新增 `.split-layout` / `.mode-btn` 等 tongyi 同款类。

### Fixed — `plugins/ecommerce-image` v0.2.1：转圈白屏 + 后端硬化（plugin_api `~1` → `~2`）

- **UI 不再卡转圈**：`_assets/{bootstrap,styles,icons,i18n}` 4 件齐全，
  `apiCall` 优先 `OpenAkita.api`（5s 超时）回退 `directFetch`，render 后
  `OpenAkita?.ready()` 通知宿主收起 spinner。
- **UI 风格对齐**：`Ic` 双路径优先 `OpenAkitaIcons`、`PromptGuide` / 列表
  / 表头 emoji 全部换 SVG，settings 改右侧 480px 抽屉，sidebar 加左侧
  primary accent bar 与 `oa-list-panel` 节奏对齐；外层包
  `PluginErrorBoundary`，render 抛错回 `oa-config-banner` 不再白屏。
- **后端 hardening**：
  - `_ensure_ready()` 单点 `Depends` 在 `_async_init` 完成前所有路由 503，
    彻底消除 `self._tm is None` 竞争。
  - 3 处 `asyncio.get_event_loop().create_task` 全部走
    `api.spawn_task(name=...)`，host 卸载时统一 cancel + drain。
  - `on_unload` 改 `async`：先 cancel + await `_init_task` / `_poll_task`，
    再守卫式 close `dashscope` / `ark` / `tm`。
  - `/upload` 1 MiB 分块 + 50 MB 上限 + Content-Length 提前 413。
  - `update_task(**fields)` 上 `_UPDATABLE_COLS` 白名单，未知列丢警告
    而不是拼进 SQL。
- **manifest**：`plugin_api ~1 → ~2`，`version 0.2.0 → 0.2.1`。
- **v0.2.2 UI realignment**：header 升级 `oa-hero-title`（渐变 chip +
  大写副标题）；"设置"从右侧抽屉改为顶级 tab；表单面板标题 / DynamicForm /
  ExampleGallery / 主按钮合到 1 张 card；`mock-banner` 升级
  `oa-config-banner`（warning 图标 + "前往设置" CTA）；OA CSS 变量别名让
  `_assets/styles.css` 的 `oa-*` 组件吃到 ecommerce 紫色主题色。

### Changed — SDK 主动收缩，回归"最小插件壳子"定位

完整执行 [SDK Refocus Cleanup 计划](.cursor/plans/sdk_refocus_cleanup_b3b5f02d.plan.md)。
SDK `0.6.0 → 0.7.0`，`contrib/` 子包整体下沉，`plugins/` 从 21 → 2 一等公民。

#### SDK 包瘦身（0.6.0 → 0.7.0）

- **删除** `openakita_plugin_sdk.contrib` 整个子包（28 个模块 + `tts/` / `asr/`
  子包 + 287 行的 `__init__.py`）。SDK 顶层 import 表面回到约 25 项（对齐 0.2 时代）。
- **保留** SDK 核心：`PluginBase` / `PluginAPI` / `PluginManifest` / `tool_definition` /
  `decorators` / `hooks` / `protocols` / `scaffold` / `testing` / `channel` / `llm` /
  `config` / `types` / `version`（`skill_loader` 也下沉到 staging,见下文）。
- **删除** `web/ui-kit/` 中无消费者的 7 个 JS：`cost-preview.js` / `error-coach.js` /
  `event-helpers.js` / `first-success-celebrate.js` / `onboard-wizard.js` /
  `task-panel.js` / `dep-gate.js`。剩下的 `bootstrap.js` / `styles.css` / `icons.js` /
  `markdown-mini.js` / `i18n.js` 也整体从 SDK 中**移除**——见下文「前端资源下沉」。

#### 一等公民收敛到 `plugins/`（21 → 2）

- 仅保留 `plugins/tongyi-image` 与 `plugins/seedance-video`。
- 两者各自 `_inline/` 子目录托管自家需要的 helper 副本：
  - `tongyi-image/tongyi_inline/`：`upload_preview.py` / `storage_stats.py`
  - `seedance-video/seedance_inline/`：`vendor_client.py` / `upload_preview.py` /
    `storage_stats.py` / `llm_json_parser.py` / `parallel_executor.py`
- `seedance-video/long_video.py` **删掉** `CostTracker` / `take_checkpoint` /
  `restore_from_snapshot` 的 demo 桩调用（这些 SDK 接口验证用的代码原本只服务于"演示"，
  和真实 vendor pipeline 无关），相应 `tests/test_long_video.py` 同步重写。

#### 19 个非一等公民插件 → `plugins-archive/`

`avatar-speaker` / `bgm-mixer` / `bgm-suggester` / `dub-it` / `ecommerce-image` /
`highlight-cutter` / `image-edit` / `local-sd-flux` / `poster-maker` / `ppt-to-video` /
`shorts-batch` / `smart-poster-grid` / `storyboard` / `subtitle-maker` /
`transcribe-archive` / `tts-studio` / `video-bg-remove` / `video-color-grade` /
`video-translator`。

- 仍可手动启用（README 写明 `cp` / 软链回 `data/plugins/` 才能加载）。
- **不接受 issue / 不主动跟 SDK 升级**；CI 不再覆盖它们的 `tests/`。
- 它们继续依赖的 16 个 helper 模块（含 `tts/` `asr/` 子包）下沉到
  `plugins-archive/_shared/`，每个 archive 插件入口都自动 bootstrap
  `plugins-archive/` 进 `sys.path`，再 `from _shared import X`。

#### 12 个 0-消费者模块 → `openakita-plugin-sdk/staging/contrib/`

`agent_loop_config` / `checkpoint` / `cost_tracker` / `cost_translation` /
`delivery_promise` / `dep_catalog` / `dep_gate` / `env_any_loader` /
`parallel_executor` / `prompt_optimizer` / `prompts` / `tool_result`
（含 `data/prompts/` 下 5 个 markdown / txt）。

- staging 区**仅作为代码参考保留**，不再 import、不再跑 CI、不属于 SDK 公共 API。
- 对应的 `tests/contrib/test_*.py` 直接删除（7 个）。

#### 前端资源下沉 + host 解耦（SDK 0.6.x → 0.7.0 第二刀）

- **`web/` 整体下沉** —— `git mv openakita-plugin-sdk/src/openakita_plugin_sdk/web/`
  到 `plugins-archive/_shared/web-uikit/`(`bootstrap.js` 5.8KB +
  `ui-kit/` 4 文件:`styles.css` / `icons.js` / `i18n.js` / `markdown-mini.js`)。
  SDK wheel **不再分发任何前端文件**,`pyproject.toml` 中的 hatchling
  注释也一并清理。
- **host 不再 mount `/api/plugins/_sdk/*`** —— `src/openakita/api/server.py`
  原 L400–L442 的 `_sdk_web_dir` 解析 + `StaticFiles` mount 段全部删除,
  改为一段 NOTE 说明历史与新规则。
- **`plugin_deps.py` 路由退役** —— 因 `contrib/dep_gate.py` 已下沉到 staging,
  对应的 host 端 REST 路由 `src/openakita/api/routes/plugin_deps.py` 与
  `server.py` 中的 import / `include_router` 一并删除。dep-gate 特性彻底退役;
  `seedance-video` 中的 `OpenAkitaDepGate` 调用因有 defensive check,在 JS
  缺失时安全 no-op,无前端报错。
- **插件 UI 自包含化** —— `plugins/tongyi-image/ui/dist/_assets/` inline
  完整 5 件套(bootstrap + styles + icons + i18n + markdown-mini),
  `plugins/seedance-video/ui/dist/_assets/` inline 同样 5 件套(覆盖
  实际 11×OpenAkitaI18n / 6×OpenAkitaIcons / 22×oa-section 用法)。
  HTML 全部改用相对路径 `_assets/...`,删除 `?v=20260419-8` cache-bust 参数。
- **`requires.sdk` 收紧** —— tongyi/seedance 的 `plugin.json` 从
  `">=0.6.0,<1.0.0"` 收紧为 `">=0.7.0,<0.8.0"`,语义对齐"自包含,只吃 SDK 核心"
  的新现实。
- **新增归档 README** —— `plugins-archive/_shared/web-uikit/README.md` 详述
  archive 插件如何复活 UI(`cp _assets/` + 改相对路径)。

#### `skill_loader.py` 也一并下沉

`openakita_plugin_sdk.skill_loader`(SKILL.md frontmatter 解析,314 行)
没有任何 plugins/ 真消费者(host 自有 `src/openakita/skills/loader.py`
完全独立),与 contrib 同期"扩展定位"残留。

- `git mv openakita-plugin-sdk/src/openakita_plugin_sdk/skill_loader.py
  openakita-plugin-sdk/staging/skill_loader.py`
- `git mv openakita-plugin-sdk/tests/test_skill_loader.py
  openakita-plugin-sdk/staging/tests/test_skill_loader.py`
- 新增 `openakita-plugin-sdk/staging/README.md` 说明 staging/ 整体性质,
  与原 `staging/contrib/README.md` 一致:**0 消费者、不打包、不进 CI**。

#### 杂项

- 删除老 wheel `openakita-plugin-sdk/dist/openakita_plugin_sdk-0.3.0-py3-none-any.whl`
  (130KB,内含早期 contrib + web/,误用风险)。
- `pyproject.toml` description 从 `"... and Plugin 2.0 UI support"` 回退为
  `"SDK for building OpenAkita plugins — protocols, base classes, testing helpers"`。
- `scaffold.py` "ui" 模板将在后续 PR 中修复 `window._ctx` 老 bug 并改为
  自包含 `_assets/bootstrap.js` 输出(本次提交未含,跟踪在
  `sdk_web_frontend_purge_4a6e6ded.plan.md` X9)。

#### 文档同步

- 删除 `openakita-plugin-sdk/docs/contrib.md` / `ai-media-scaffold-guide.md` /
  `dependency-gate.md` / `plugin-ui.md` / `plugin-i18n-and-uikit-migration.md`
  (对应模块/特性都已下沉或退役)。
- `openakita-plugin-sdk/docs/README.md` / `getting-started.md` /
  `api-reference.md` 改写为「最小壳 + UI 自包含」口径,删除 Plugin 2.0 UI
  专节链接。
- `openakita-plugin-sdk/README.md` / 顶层 `__init__.py` 删除 contrib 段落,
  明确 SDK 只做"最小壳子"。
- `docs/plugin-context-cheatsheet.md` 21 插件矩阵 → "2 一等公民 + 19 archive" 两栏,
  并新增「UI 必须自包含」「archive 复活模式」两条硬规则。

---

## [前置整改] - 2026-04-21

### Added — 插件全量整改 (Plugin Overhaul Phases 0–4)

完整执行 [插件整改标准方案](docs/plugin-overhaul-template.md) 0–4 阶段。
21 个插件全部从 "导入成功但 APPS 列表里看不到" 修到可用，并把跨插件依赖
统一收敛进 SDK `contrib`。**累计 1100+ 单测全过。**

#### Phase 0 · Host compat 修复（解锁 17/21 插件）
- `src/openakita/plugins/compat.py` — `PLUGIN_API_VERSION = "1.0.0"` →
  `"2.0.0"`，并保留 `~1` 兼容窗口（带 deprecation warning）。这是
  17 个插件 manifest 写 `"plugin_api": "~2"` 后默默不可见的根因。
- 18 例 `tests/unit/test_plugins/test_compat.py` 覆盖。

#### Phase 1 · SDK `0.6.0` — `contrib.tts` / `contrib.asr`
- 新增 `openakita_plugin_sdk.contrib.tts`（`qwen3-tts-flash` /
  `cosyvoice` / `openai-tts` / `edge-tts` + `select_provider("auto", ...)`
  自动凭证降级到 `edge` / `stub-silent`）。
- 新增 `openakita_plugin_sdk.contrib.asr`（DashScope `paraformer-v2` /
  本地 `whisper.cpp` / `stub` + 同款 `select_provider("auto", ...)`）。
- 配套 39 例单测；SDK `pyproject.toml` / `version.py` `0.3.0 → 0.6.0`。
- **彻底废除 `_load_sibling()` 反模式**（4 个插件原本通过路径黑魔法
  跨插件 `import` `tts-studio` / `subtitle-maker`，host unload 顺序敏感）。

#### Phase 2 · 21 个插件逐个整改

按 [docs/plugin-overhaul-template.md](docs/plugin-overhaul-template.md)
模板执行，统一交付：① `plugin.json` 升 SDK 依赖到 `>=0.6.0,<1.0.0`；
② UI 用 `_detectApiBase()` 修 `apiBase` 时序导致的 404；
③ API key 走 `/settings` 热更新（参考 `tongyi-image` 形态）；
④ 跨插件依赖全走 `contrib.*`。

| 阶段 | 插件 | 关键改动 |
| ---- | ---- | -------- |
| 2-01 | `avatar-speaker` | 全量重写：接 `contrib.tts` + UI 抄 tongyi 模板 + `/settings` 热更新（22 例） |
| 2-02 | `tts-studio` | 去 `_load_sibling` → 接 `contrib.tts`（13 例 + 2 skip） |
| 2-03 | `highlight-cutter` | 去 `_load_sibling` → 接 `contrib.asr` |
| 2-04 | `subtitle-maker` | 去 `_load_sibling` → 接 `contrib.asr` |
| 2-05 | `transcribe-archive` | 新增 `ContribAdapterProvider` 桥接 sync-chunked 与 async-full-file 接口，保留原 chunking/cache |
| 2-06 | `video-translator` | 去 `_load_sibling` → 接 `contrib.asr` + `contrib.tts`，本地化 `TranscriptChunk` / SRT/VTT renderer |
| 2-07 | `dub-it` | 改 `description` 为 "scaffolding example"，引导用户用 `video-translator` 做生产 |
| 2-08~12 | `poster-maker` / `smart-poster-grid` / `image-edit` / `storyboard` / `local-sd-flux` | UI `_detectApiBase()` + SDK 升级 (201 例) |
| 2-13~14 | `bgm-suggester` / `bgm-mixer` | SDK 升级 + version bump (113 例) |
| 2-15~21 | `tongyi-image` / `seedance-video` / `ecommerce-image` / `video-bg-remove` / `video-color-grade` / `ppt-to-video` / `shorts-batch` | SDK 升级 + `ecommerce-image` 补齐缺失的 `sdk` 字段 (416 例) |

#### Phase 3 · `shorts-batch` 升级为 `video-pipeline` 编排器
- 新增 `plugins/shorts-batch/pipeline_orchestrator.py` —
  `plan → image → video → audio → subtitle → mux` 6 步管线，每步
  `Callable` 可注入；默认全部走确定性 stub（1 字节占位文件 + 静默 WAV）
  保证 CI / 干跑零外部依赖。
- 失败时 `PipelineStageError` 带 `stage` id；`to_verification()` 输出
  `D2.10 Verification` envelope，UI 能直接渲染分阶段时间线。
- `Plugin.set_pipeline_stage(stage, fn)` 让 host 在 `on_load` 期间把
  `tongyi-image` / `seedance-video` / `subtitle-maker` 接到对应阶段。
- 新增 `POST /api/plugins/shorts-batch/pipeline` 路由 + 19 例单测。
- `shorts-batch` 版本 `1.1.0 → 1.2.0`，`description` 同步更新。

#### Phase 4 · 文档
- `docs/plugin-context-cheatsheet.md` — 21 插件矩阵 v2（标记每个插件
  contrib 接入状态）+ 新增 `contrib.tts` / `contrib.asr` 速查行 +
  `requires.sdk` 默认值升到 `>=0.6.0,<1.0.0`。
- `docs/plugin-overhaul-template.md` — 单插件整改标准模板（其他 agent
  按它执行单个插件改动时直接 `@` 引用）。
- 本 CHANGELOG 条目。

### Fixed — 插件加载系统三件套

- **多插件 `task_manager.py` / `providers.py` 同名子模块在 `sys.modules`
  互相覆盖**：21 个插件中有 19 个使用裸名 `from task_manager import X`
  导入自己目录下的 `task_manager.py`；先加载的插件抢占
  `sys.modules["task_manager"]`,后续插件命中缓存导致
  `ImportError: cannot import name 'XxxTaskManager'`。`_load_python_plugin`
  在 `exec_module` 前增加 shadowed 机制：扫描本插件目录的顶层 `.py` /
  包名,把 `sys.modules` 里属于其他插件的同名条目先弹出,让本插件的
  bare import 能沿 `sys.path` 找到自己的文件。已加载兄弟插件持有的
  Python 对象引用照常工作。
- **`PluginManager._failed` 在卸载/移除后从不清理**：UI 长期残留"插件
  加载失败"幽灵条目。`unload_plugin` 入口立即 `pop _failed[plugin_id]`;
  纯 failed-state 的卸载现在返回 `True`(原先返回 `False`,语义更准确,
  现有测试不受影响)。`uninstall_plugin` 路由的 removed 分支额外调用
  新增的 `pm.forget_failure(plugin_id)` 兜底。
- **`seedance-video` 缺失 `prompt_optimizer.py` 导致
  `ModuleNotFoundError`**：Sprint 18 收尾依据
  [docs/sprint18-cleanup-assessment.md](docs/sprint18-cleanup-assessment.md)
  §B8 的错误 grep 结论删除了该文件,但 `plugin.py` 第 39–46 行 import
  并在 4 个 REST 端点(`/prompt-guide`、`/prompt-templates`、
  `/prompt-formulas`、`/prompt-optimize`)实际使用 6 个符号。已从 commit
  `f04787f9^` 还原 291 行原版本。SDK 的
  `openakita_plugin_sdk.contrib.prompt_optimizer.PromptOptimizer` 是另一
  套泛化 API（无 Seedance 静态字典、签名不同）,不能替换;后续若想接 SDK
  须按 §B8 推荐方案做拆分。

### Documentation

- `docs/sprint18-cleanup-assessment.md` §B8.A — 标注 grep 结论错误 +
  撤销动作 + 复核命令(`rg --pcre2 -nP "from\s+prompt_optimizer"`)
- `docs/plugin-2.0-handover.md` — 移除 `prompt_optimizer.py` 删除线,
  改写为"已还原"
- 自检 21 个插件的同名子模块碰撞清单(归档于本次 PR plan)

## [1.27.9] - 2026-04-20

> Plugin Sprint 7-18 整合发布。完成 SDK `contrib/` 6 件套补齐 + 8 个新 AI-媒体插件
> 上线 + 老插件全套加固。所有改动覆盖单元测试，主仓 + SDK + 20 个插件总计 **1180+
> 测试 / 1 skipped / 零回归**。详见 [docs/sprint18-cleanup-assessment.md](docs/sprint18-cleanup-assessment.md)。

### Added — SDK `openakita-plugin-sdk/contrib/` 6 件套（Sprint 8）

- **`quality_gates`** — G1-G5 多轨闸门（含 `slideshow_risk` D2.1）
- **`intent_verifier`** — `verify_delivery` 出参与用户意图比对（D2.2 + P3.5）
- **`provider_score`** — 多 provider 排序与裁判（D2.5）
- **`verification`** — `Verification` + `LowConfidenceField` 协议（D2.10）
- **`error_coach`** — 错误归因 + 可执行建议（D2.11 + D2.14 双段）
- **`prompt_optimizer`** — 通用 LLM 提示词优化器（P3.1-P3.5 共 5 条 prompt）

补充模块：`upload_preview`、`agent_loop_config`、`base_task_manager`、`base_vendor_client`、
`ffmpeg`（`run_ffmpeg` + `auto_color_grade_filter` + `signalstats sampling ±8% clamp`，B7）、
`source_review`（D2.3）、`slideshow_risk`（D2.1）、`cost_tracker` / `checkpoint`
（健康检查通过，标记为 🅿️ waiting-for-consumer）。

### Added — 8 个新 AI-媒体插件

| Sprint | 插件 | 卖点 |
|--------|------|------|
| Sprint 11 | `transcribe-archive` | parallel_executor + checkpoint + cost_tracker (95 测试) |
| Sprint 12 | `bgm-mixer` | madmom beat-aware ducking + ffmpeg 切点对齐 (68 测试) |
| Sprint 13 | `video-color-grade` | SDK auto_color_grade 薄封装 (49 测试) |
| Sprint 13 | `smart-poster-grid` | 4 尺寸编排 + verification (50 测试) |
| Sprint 14 | `video-bg-remove` | RVM ckpt + onnxruntime + dep_gate (72 测试) |
| Sprint 15 | `ppt-to-video` | LibreOffice headless + tts-studio 跨插件调用 (79 测试) |
| Sprint 16 | `local-sd-flux` | ComfyUI HTTP 客户端 + 5 条 workflow + provider_score (99 测试) |
| Sprint 17 | `shorts-batch` | 批量 shorts 编排 + slideshow_risk D2.1 (51 测试) |
| Sprint 17 | `dub-it` | 视频配音翻译 5 阶段流水线 + source_review D2.3 (52 测试) |

每个新插件均自带 `SKILL.md` + `README.md` + `ui/dist/index.html` 占位 + 完整测试。

### Added — 老插件加固（Sprint 7 + Sprint 9 真实复用）

- `seedance-video` — 全套 SQL 白名单 / spawn_task / async unload / SKILL.md / 30+ 测试
- `storyboard` — 接 `gate_g5_slideshow_risk` + `intent_verifier.verify_delivery`
- `tongyi-image` — 接 `error_coach` 双段错误归因
- `bgm-suggester` — 接 `verification` 字段（self-check style match）
- `cost_translation_map.yaml` — 给 tongyi-image / seedance-video / bgm-suggester 各加一条人话翻译

### Changed

- 主 README 「Plugin System」章节新增「Bundled AI-Media Plugins (20)」表，统计 913 个测试
- `docs/plugin-2.0-handover.md` — 标记 seedance `prompt_optimizer.py` 已删除
- `openakita-plugin-sdk/docs/contrib.md` — `cost_tracker` / `checkpoint` 标 🅿️ waiting-for-consumer

### Removed

- `plugins/seedance-video/prompt_optimizer.py` — 孤儿文件（无 import / 无测试，逻辑已被 SDK
  `contrib.prompt_optimizer.PromptOptimizer` 泛化覆盖）。详见
  [Sprint 18 评估 §B8](docs/sprint18-cleanup-assessment.md#b8-prompt_optimizer-迁移评估)。

### Documentation

- `docs/sprint18-cleanup-assessment.md` — A1+ tongyi / A1+ seedance / B8 prompt_optimizer
  / SkillManifest loader 4 项迁移评估
- `docs/refs-extraction-report.md` — D1-D9 9 个插件 `findings/d_class_copy_points/` 抽点报告（Sprint 10）
- 各插件 `SKILL.md` + `README.md` 全套补齐

### Tests

- SDK：367 passed / 1 skipped
- 20 个插件：813+ passed
- **总计：1180+ 测试，零回归**

## [1.2.1] - 2026-02-05

### Added
- **Feishu (飞书) Full Support** - Text, voice, image, and file messages fully tested
- **Plan Mode Documentation** - Comprehensive guide for multi-step task management
- **Community Section** - WeChat group, Discord, X (Twitter) contact info

### Changed
- **Version Management** - Unified version source from `pyproject.toml`
  - `__init__.py` now reads version dynamically
  - README badge auto-syncs with GitHub releases
- **README Enhancements** - Added Plan Mode workflow diagrams and examples

### Fixed
- WeChat group QR code image path typo

## [1.2.0] - 2026-02-02

### Added
- **Scheduled Task Management Enhancement**
  - New `update_scheduled_task` tool for modifying task settings without deletion
  - `notify_on_start` / `notify_on_complete` notification switches
  - Clear distinction between "cancel task" vs "disable notification" vs "pause task"
  - Detailed tool descriptions with usage examples
- **ToolCatalog Progressive Disclosure**
  - `get_tool_info` tool for querying detailed tool parameters
  - `list_available_tools` for discovering system capabilities
  - Level-based tool disclosure (basic → advanced)
- **Telegram Proxy Configuration**
  - `TELEGRAM_PROXY` environment variable support
  - HTTP/HTTPS/SOCKS5 proxy support for restricted networks

### Fixed
- **IM Session Tool Usage** - Fixed Telegram sessions missing tool definitions, causing bot to only respond with "I understand" without taking action
- **Task Notification Format** - Removed over-escaping in scheduled task notifications that caused garbled Markdown
- **System Prompt Tool Guidelines** - Strengthened tool usage requirements: "Must use tools immediately upon receiving tasks"

### Changed
- Enhanced shell tool security checks
- Improved scheduled task tool descriptions with clear concept differentiation

## [1.1.0] - 2026-02-02

### Added
- **MiniMax Interleaved Thinking Support**
  - New `ThinkingBlock` type in `llm/types.py` for model reasoning content
  - Anthropic provider parses `thinking` blocks from MiniMax M2.1 responses
  - Brain converts `ThinkingBlock` to tagged `TextBlock` for Pydantic compatibility
  - Agent preserves thinking blocks in message history for MiniMax context requirements
- **Enhanced Browser Automation Tools** (`tools/browser_mcp.py`)
  - `browser_status`: Get browser state (open/closed, current URL, tab count)
  - `browser_list_tabs`: List all open tabs with index, URL, title
  - `browser_switch_tab`: Switch to a specific tab by index
  - `browser_new_tab`: Open URL in new tab (without overwriting current page)
  - Smart blank page reuse: First `browser_new_tab` reuses `about:blank` instead of creating extra tab
- Project open source preparation
- Comprehensive documentation suite
- Contributing guidelines
- Security policy
- **Unified LLM Client Architecture** (`src/openakita/llm/`)
  - `LLMClient`: Central client managing multi-endpoint, capability routing, failover
  - `LLMProvider` base class with Anthropic and OpenAI implementations
  - Unified internal types: `Message`, `Tool`, `LLMRequest`, `LLMResponse`, `ContentBlock`
  - Anthropic-like format as internal standard, automatic conversion for OpenAI-compatible APIs
- **LLM Endpoint Configuration** (`data/llm_endpoints.json`)
  - Centralized endpoint config: name, provider, model, API key, capabilities, priority
  - Supports multiple providers: Anthropic, OpenAI, DashScope, Kimi (Moonshot), MiniMax
  - Capability-based routing: text, vision, video, tools
  - Priority-based failover with automatic endpoint selection
- **LLM Endpoint Cooldown Mechanism**
  - Failed endpoints enter 3-minute cooldown period
  - Automatically skipped during cooldown, uses fallback endpoints
  - Auto-recovery after cooldown expires
  - Applies to auth errors, rate limits, and unexpected errors
- **Text-based Tool Call Parsing**
  - Fallback for models not supporting native `tool_calls`
  - Parses `<function_calls>` XML patterns from text responses
  - Seamless degradation without code changes
- **Multimodal Support**
  - Image processing with automatic format detection and base64 encoding
  - Video support via Kimi (Moonshot) with `video_url` type
  - Capability-based routing: video tasks prioritize Kimi

### Changed
- README restructured for open source
- **Browser MCP uses explicit context** for multi-tab support
  - Changed from `browser.new_page()` to `browser.new_context()` + `context.new_page()`
  - Enables creating multiple tabs in same browser window
- **`browser_open` default `visible=True`** - Browser window visible by default for user observation
- **Brain Refactored as Thin Wrapper**
  - Removed direct Anthropic/OpenAI client instances
  - All LLM calls now go through `LLMClient`
  - `messages_create()` and `think()` delegate to `LLMClient.chat()`
- **Message Converters** (`src/openakita/llm/converters/`)
  - `messages.py`: Bidirectional conversion between internal and OpenAI formats
  - `tools.py`: Tool definition conversion, text tool call parsing
  - `multimodal.py`: Image/video content block conversion
- **httpx AsyncClient Event Loop Fix**
  - Tracks event loop ID when client is created
  - Recreates client if event loop changes (fixes "Event loop is closed" error)
  - Applied to both Anthropic and OpenAI providers
- **Cross-platform Path Handling**
  - System prompt suggests `data/temp/` instead of hardcoded `/tmp`
  - Dynamic OS info injected into system prompt
  - `tempfile.gettempdir()` used in self-check module
- **Context Compression: LLM-based instead of truncation**
  - `_compress_context()` now uses LLM to summarize early messages
  - `_summarize_messages()` passes full content to LLM (no truncation)
  - Recursive compression when context still too large
  - Never directly truncates message content
- **Full Logging Output (no truncation)**
  - User messages logged completely
  - Agent responses logged completely
  - Tool execution results logged completely
  - Task descriptions logged completely
  - Prompt compiler output logged completely
- **Tool Output: Full content display**
  - `list_skills` shows full skill descriptions
  - `add_memory` shows full memory content
  - `get_chat_history` shows full message content
  - `executed_tools.result_preview` shows full result
- **Identity/Memory Module: No truncation**
  - Current task content preserved fully
  - Success patterns preserved fully
- **LLM Failover Optimization**
  - With fallback endpoints: switch immediately after one failure
  - Single endpoint: retry multiple times (default 3)
- **Thinking as Parameter, not Capability**
  - `thinking` removed from endpoint capability filtering
  - Now treated as transmission parameter only
- **Kimi-specific Adaptations**
  - `reasoning_content` field support in Message/LLMResponse types
  - Automatic extraction and injection for Kimi multi-turn tool calls
  - `thinking.type` set to `enabled` per official documentation

### Fixed
- **Session messages not persisting** - Added `session_manager.mark_dirty()` calls in gateway after `session.add_message()` to ensure voice transcriptions and user messages are saved
- **Playwright multi-tab error** - Fixed "Please use browser.new_context()" error when opening multiple tabs

## [0.6.0] - 2026-01-31

### Added
- **Two-stage Prompt Architecture (Prompt Compiler)**
  - Stage 1: Translates user request into structured YAML task definition
  - Stage 2: Main LLM processes the structured task
  - Improves task understanding and execution quality

- **Autonomous Evolution Principle**
  - Agent can install/create tools autonomously
  - Ralph Wiggum mode: never give up, solve problems instead of returning to user
  - Max tool iterations increased to 100 for complex tasks

- **Voice Message Processing**
  - Automatic voice-to-text using local Whisper model
  - No API calls needed, fully offline
  - Default: base model, Chinese language

- **Chat History Tool (`get_chat_history`)**
  - LLM can query recent chat messages
  - Includes user messages, assistant replies, system notifications
  - Configurable limit and system message filtering

- **Telegram Pairing Mechanism**
  - Security pairing code required for new users
  - Paired users saved locally
  - Pairing code saved to file for headless operation

- **Proactive Communication**
  - Agent acknowledges received messages before processing
  - Can send multiple progress updates during task execution
  - Driven by LLM judgment, not keyword matching

- **Full LLM Interaction Logging**
  - Complete system prompt output in logs
  - All messages logged (not truncated)
  - Full tool call parameters logged
  - Token usage tracking

### Changed
- **Thinking Mode**: Now enabled by default for better quality
- **Telegram Markdown**: Switched from MarkdownV2 to Markdown for better compatibility
- **Message Recording**: All sent messages now recorded to session history
- **Scheduled Tasks**: Clear distinction between REMINDER and TASK types

### Fixed
- Telegram MarkdownV2 parsing errors with tables and special characters
- Multiple notification issue with scheduled tasks
- Voice file path not passed to Agent correctly
- Tool call limit too low for complex tasks

## [0.5.9] - 2026-01-31

### Added
- Multi-platform IM channel support
  - Telegram bot integration
  - DingTalk adapter
  - Feishu (Lark) adapter
  - WeCom (WeChat Work) adapter
  - QQ (OneBot) adapter
- Media handling system for IM channels
- Session management across platforms
- Scheduler system for automated tasks

### Changed
- Improved error handling in Brain module
- Enhanced tool execution reliability
- Better memory consolidation

### Fixed
- Telegram message parsing edge cases
- File operation permissions on Windows

## [0.5.0] - 2026-01-15

### Added
- Ralph Wiggum Mode implementation
- Self-evolution engine
  - GitHub skill search
  - Automatic package installation
  - Dynamic skill generation
- MCP (Model Context Protocol) integration
- Browser automation via Playwright

### Changed
- Complete architecture refactor
- Async-first design throughout
- Improved Claude API integration

## [0.4.0] - 2026-01-01

### Added
- Testing framework with 300+ test cases
- Self-check and auto-repair functionality
- Test categories: QA, Tools, Search

### Changed
- Enhanced tool system with priority levels
- Better context management

### Fixed
- Memory leaks in long-running sessions
- Shell command timeout handling

## [0.3.0] - 2025-12-15

### Added
- Tool execution system
  - Shell command execution
  - File operations (read/write/search)
  - Web requests (HTTP client)
- SQLite-based persistence
- User profile management

### Changed
- Restructured project layout
- Improved error messages

## [0.2.0] - 2025-12-01

### Added
- Multi-turn conversation support
- Context memory system
- Basic CLI interface with Rich

### Changed
- Upgraded to Anthropic SDK 0.40+
- Better response streaming

## [0.1.0] - 2025-11-15

### Added
- Initial release
- Basic Claude API integration
- Simple chat functionality
- Configuration via environment variables

---

## Version History Summary

| Version | Date | Highlights |
|---------|------|------------|
| 1.2.0 | 2026-02-02 | Scheduled task management, IM session fix |
| 1.1.0 | 2026-02-02 | MiniMax thinking, Unified LLM client |
| 0.5.9 | 2026-01-31 | Multi-platform IM support |
| 0.5.0 | 2026-01-15 | Ralph Mode, Self-evolution |
| 0.4.0 | 2026-01-01 | Testing framework |
| 0.3.0 | 2025-12-15 | Tool system |
| 0.2.0 | 2025-12-01 | Multi-turn chat |
| 0.1.0 | 2025-11-15 | Initial release |

[Unreleased]: https://github.com/openakita/openakita/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/openakita/openakita/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/openakita/openakita/compare/v1.0.2...v1.1.0
[0.5.9]: https://github.com/openakita/openakita/compare/v0.5.0...v0.5.9
[0.5.0]: https://github.com/openakita/openakita/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/openakita/openakita/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/openakita/openakita/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/openakita/openakita/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/openakita/openakita/releases/tag/v0.1.0

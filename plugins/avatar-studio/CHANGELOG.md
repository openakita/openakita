# avatar-studio CHANGELOG

## 1.0.0 — 2026-04-22

第一个**可用**版本。完整 4 生成模式 + 5 Tab UI + 9 工具 + 16 路由的端到端
数字人工作流。

### Added

#### 业务能力（4 生成模式）

- **`photo_speak`** 照片说话：`wan2.2-s2v-detect` → `wan2.2-s2v`，单图 +
  TTS / 上传音频 → MP4。
- **`video_relip`** 视频换嘴：`videoretalk`，视频 + TTS / 上传音频 →
  口型同步 MP4。
- **`video_reface`** 视频换人：`wan2.2-animate-mix` (`wan-std` /
  `wan-pro`)，人物图 + 参考视频 → 角色替换 MP4。
- **`avatar_compose`** 数字人合成：`wan2.5-i2i-preview` 多图融合 →
  `wan2.2-s2v-detect` → `wan2.2-s2v`，附带 `qwen-vl-max` 可选 prompt
  自动撰写助手。
- **`cosyvoice-v2`** TTS / voice clone：12 个系统音色 + 自定义克隆。

#### 工程实现

- `avatar_models.py`：4 MODES + 12 VOICES + `PRICE_TABLE` +
  `estimate_cost`（**禁用任何「奶茶单位」翻译**，金额一律 `¥{:.2f}` 直显）+
  `ERROR_HINTS` 9 类中英 hints。
- `avatar_task_manager.py`：纯 `aiosqlite`，不继承任何基类；3 表
  (`tasks` / `voices` / `figures`)；`update_task_safe` 严格白名单防 SQL
  注入。
- `avatar_dashscope_client.py`：继承 `avatar_studio_inline.vendor_client`；
  8 业务方法；`asyncio.Semaphore(1)` 串行化 submit 调用以贴合 DashScope
  并发硬限；每次请求前 `_settings()` 重读实现 API Key 热加载（Pixelle
  A10）；9 类错误分类 (`network` / `timeout` / `rate_limit` / `auth` /
  `not_found` / `moderation` / `quota` / `dependency` / `unknown`)。
- `avatar_pipeline.py`：自写 8 步线性 pipeline (`setup_environment` →
  `estimate_cost` → `prepare_assets` → `tts_synth` → `image_compose` →
  `video_synth` → `finalize` → `handle_exception`)，按 mode 短路；
  `AvatarPipelineContext` 21 字段 dataclass；3 段退避轮询策略 (3 s × 10 →
  10 s × 9 → 30 s × N，总超时 600 s)；非终结性 `ApprovalRequired` 实现
  cost 超阈值用户确认门控；`tts_audio_duration_sec` 透传到
  `submit_s2v(duration=)` 落实 Pixelle P1。
- `plugin.py`：`PluginBase` 入口；16 路由（任务 6 + 估价 1 + 音色 4 +
  形象 3 + 系统 2）；9 工具；Pydantic + `extra="forbid"` 严格参数校验
  （Pixelle C6，不会静默丢字段）；`on_unload` 三件套（取消所有 in-flight
  pipeline + 关 sqlite + 关 httpx）；API Key 缺失只 warn 不抛（Pixelle C5）。
- `ui/dist/index.html` ≈ 2360 行 React 18 + Babel CDN 单文件：自实现
  `Ico` / `Switch` / `Collapsible` / `StatusBadge` / `EmptyState` /
  `ErrorPanel` / `CostBreakdown` / `CostExceedModal` / `TaskStartedModal` /
  `FileUploadZone` / `VideoPlayer` / `AudioPlayer` / `PluginErrorBoundary`；
  5 Tab (`Create` / `Tasks` / `Voices` / `Figures` / `Settings`)；
  `useDraft(mode)` localStorage 草稿持久化；CSS variables 主题 + 暗色模式
  自动跟随；`I18N_DICT` zh/en 双语；SSE 订阅
  `plugin:avatar-studio:task_update` 实时推任务进度。
- `avatar_studio_inline/`：5 件 vendored helpers (`vendor_client.py` /
  `upload_preview.py` / `storage_stats.py` / `llm_json_parser.py` /
  `parallel_executor.py`)，全部从 `plugins/seedance-video/seedance_inline/`
  fork + 改包名而来，零跨插件 import。
- `ui/dist/_assets/`：5 件 vendored UI Kit (`bootstrap.js` / `styles.css` /
  `icons.js` / `i18n.js` / `markdown-mini.js`，~60 KB) 从
  `plugins-archive/_shared/web-uikit/` 复制，零 host mount。

#### 测试

- 85 个单元测试覆盖：`test_models` (cost preview × 16 case + ERROR_HINTS
  全 9 类) / `test_task_manager` (CRUD + 白名单守护 + cleanup_expired
  时间窗) / `test_dashscope_client` (httpx mock + 错误分类 + cancel +
  Semaphore) / `test_pipeline` (4 mode happy + 失败注入 + cancel + P1
  duration 透传 + cost approval gate) / `test_plugin` (`on_load` /
  路由 / 工具 / `on_unload`) / `test_smoke` (vendored helpers import +
  `_assets` 存在)。**85 passed, 1 skipped** 是 hermetic 跑通的标准输出。
- `tests/integration/test_dashscope_smoke.py` opt-in 集成测试：通过
  `DASHSCOPE_API_KEY` 环境变量 + `pytest -m integration` 触发，跑一次
  3 s `photo_speak` 端到端（约 ¥0.31）。`conftest.py::pytest_configure`
  注册 `integration` marker 并默认 skip。

#### 文档

- `README.md` 10 节（概述 / 4 Tab / 安装 / 配置 / 使用 / 故障排查 /
  **§7 5 分钟用户烟测脚本** / 目录结构 / 已知限制 / 与
  `plugins-archive/avatar-speaker/` 关系）。
- `SKILL.md` 10 节给 Cursor agent（触发场景 / 工具清单 / 输入输出
  schema / 错误码 / 模式决策树 / 费用估算 / 提示词模板 / 测试 / 限制）。
- `docs/plugin-context-cheatsheet.md` 第二节插件矩阵新增 avatar-studio
  行（一线 ✓）；澄清 avatar-speaker 已 archive、avatar-studio 接手。

### Changed

- `plugin.json::version` `0.1.0` → `1.0.0`，标记首个稳定版本。

### Notes

- 与 `plugins-archive/avatar-speaker/` **不同源、不继承代码、独立一线
  插件**。avatar-speaker 不会被删除（保留以兼容老用户的项目），但所有
  新功能、新修复一律走 avatar-studio。
- 零 `openakita_plugin_sdk.contrib` import（SDK 0.7.0 已移除该子包，
  commit `d6d0c964`）。
- 零 `/api/plugins/_sdk/*` host-mount 引用（host 0.7.0 起停止挂载，
  commit `4cdf6275`）。
- 零 `from _shared import ...`（那是 archive 的兼容桩，仅旧插件可用）。
- `requires.sdk` 锚定 `>=0.7.0,<0.8.0`，与所有现役一线插件一致。

---

## [Unreleased] — Phase 0 (skeleton)

### Added
- Plugin directory `plugins/avatar-studio/` (first-class, peer of
  `tongyi-image` / `seedance-video`).
- Vendored UI Kit assets under `ui/dist/_assets/` (5 files, ~60KB,
  copied from `plugins-archive/_shared/web-uikit/`).
- Vendored helpers under `avatar_studio_inline/` (5 files, forked from
  `plugins/seedance-video/seedance_inline/`).
- Skeleton `plugin.py` (PluginBase subclass that just logs).
- `plugin.json` (sdk `>=0.7.0,<0.8.0`, 9 tools, ui.entry).
- `tests/conftest.py` + `tests/test_smoke.py` (vendored helpers import +
  three-layer fallback assertion + `_assets` presence check).

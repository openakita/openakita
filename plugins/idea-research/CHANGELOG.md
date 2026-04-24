# Changelog

All notable changes to **idea-research / 选题研析室** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-04-24 (首发版)

### Added — 4 工作模式

- **`radar_pull`（热点雷达）**: 5 平台并发抓取（B站 / YouTube / 抖音 / 小红书 / 快手 / 微博 6 选 5），关键词过滤，时间窗口 (24h / 7d / 30d)，热度评分。
- **`breakdown_url`（爆款拆解）**: 8 步流水线 — 环境校验 → 源解析 → 媒体下载（yt-dlp + ffmpeg）→ ASR 转写（Faster-Whisper 或 DashScope Paraformer-v2）→ 关键帧抽取 + VLM 描述（Qwen-VL-max）→ 多模态结构化分析 → 评论聚类总结 → metadata + JSON 报告 + handoff。
- **`compare_accounts`（账号对比）**: 跨账号 hook 模式分布，封面风格雷达，发布节奏时间线。
- **`script_remix`（脚本改写）**: 12 内置 PERSONAS × 1-5 变体并发，注入 MDRM 历史成功 hook 加权。

### Added — 双引擎采集

- **引擎 A（安全公开 API）**: B站 search.all，YouTube Data API v3，RSS Hub 兜底，`yt-dlp` 解析用户粘贴 URL。
- **引擎 B（高级 Playwright 爬虫）**: 抖音 / 小红书 / 快手 / B站登录态 / 微博 — 用户提供 cookies + 风险确认，单平台并发 3，全局上限可调。
- **`auto` 模式**: 按平台自动选择 A 或 B；`auto` 默认值。

### Added — MDRM 记忆图谱（双轨写、单轨读）

- **写入双轨**: 同一 hook 同时写入 `brain.add_memory()` + `memory_manager.upsert()` + `vector_store.add()`。
- **读取单轨**: `vector_store.query()` top-k=10 + cosine ≥ 0.65。
- **能力探针**: `caps = api.get_brain() ⊕ api.get_memory_manager() ⊕ api.get_vector_store()`，UI 实时显示 ✓ / ✗ + 缺失权限。
- **降级**: 任何一轨失败仅降级日志，不阻塞主流程；UI 显示橙色警告。
- **3 个手动操作**: `clear` / `reindex` / `stats`。

### Added — 26 routes + 9 tools

- **routes（FastAPI）**: 任务 CRUD ×7、订阅 ×4、推荐流 ×3、设置 ×4、cookies ×4、MDRM ×3、export ×1。
- **tools（SDK）**: `idea_radar_pull`、`idea_breakdown_url`、`idea_compare_accounts`、`idea_script_remix`、`idea_subscribe`、`idea_unsubscribe`、`idea_list_subscriptions`、`idea_export`、`idea_cancel`。

### Added — 数据持久化

- 7 张 SQLite 表: `tasks` / `subscriptions` / `trend_items` / `personas` / `hook_library` / `cookies` / `settings`。
- 每事件循环独立 `asyncio.Lock`（loop-id 字典），兼容 FastAPI TestClient + pytest-asyncio。

### Added — UI（单文件 React + Babel CDN，1247 行）

- 3 Tab 路由（`#/workbench` / `#/tasks` / `#/settings`）。
- Workbench 左侧 sticky 5 折叠区 + 右侧虚拟滚动推荐卡 + 就地拆解抽屉 + 顶部 MDRM 状态栏。
- Tasks 历史表 + 详情抽屉 + handoff 跳转。
- Settings 5 区（cookies 5 平台 / MDRM / DashScope / 默认值 / 数据导出）。
- 主题 / 语言全自动跟随宿主，`useDeferredValue` + `useDebouncedCallback` 输入流畅。

### Added — cookies 安全

- `cryptography.fernet` 加密 + `keyring` 系统密钥环 → SQLite 兜底（DPAPI on Windows，Keychain on macOS）。
- 风险确认弹窗 + 强制勾选 "我已知晓风险" 才能保存。

### Added — 错误处理

- 11 类 `error_kind` 标准化（`network` / `timeout` / `auth` / `quota` / `dependency` / `cookies_expired` / `crawler_blocked` / `format` / `validation` / `not_found` / `unknown`）。
- 每个错误带中英 hint。

### Added — 测试

- 143 测试全过：单元（idea_models / idea_task_manager / vendor_client / mdrm_adapter / dashscope_client / engines / pipeline）+ 集成（plugin_routes 26 routes + 9 tools，422/404/工具分支）+ UI smoke。
- `ruff check` + `ruff format` + `mypy --strict` 全绿。

### Added — 文档

- `README.md` 9 节（介绍 / 安装 / 4 mode / 双引擎风险 / MDRM / API / 错误码 / 烟测 / 协作）。
- `SKILL.md` 10 节（场景 / mode prompt / 引擎决策树 / persona 选择 / MDRM 建议 / handoff / 排障 / cookies 修复 / cheatsheet / 路线图）。
- `USER_TEST_CASES.md` 31 用例（4 mode × 7 + MDRM × 3）。

## [0.0.1] — 2026-04-24 (Phase 0 skeleton)

### Added

- Plugin manifest (`plugin.json`) with the full 11-permission set
  (`tools.register`, `routes.register`, `hooks.basic`, `config.read`,
  `config.write`, `data.own`, `brain.access`, `vector.access`,
  `memory.read`, `memory.write`, `channel.push`).
- Skeleton entry (`plugin.py`) that loads cleanly and logs a Phase-0
  banner.
- Vendored helpers under `idea_research_inline/`
  (`vendor_client.py` with the seven-class error taxonomy,
  `mdrm_adapter.py` with the dual-track facade, `llm_json_parser.py`,
  `parallel_executor.py`, `upload_preview.py`, `storage_stats.py`).
- UI placeholder under `ui/dist/` with self-contained `_assets/`
  (`bootstrap.js`, `styles.css`, `icons.js`, `i18n.js`,
  `markdown-mini.js`).
- Pytest scaffolding (`tests/conftest.py`) shipping a
  `FakePluginAPI` with fake brain / memory / vector services so the
  MDRM adapter can be exercised in unit tests.

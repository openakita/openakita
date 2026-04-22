# 单插件整改标准模板（Plugin Overhaul Template）

> Source of truth for Phase 2 of the *Plugin Overhaul Standard Playbook*.
> Every targeted plugin rectification PR — whether driven by master or a
> sub-agent — must use this template **without skipping sections**.
>
> Usage: copy the file as `plans/plugin-<id>-overhaul.md`, fill the
> `<填空>` placeholders, attach the result to the PR description.

---

## Section 1 · 插件身份

- **插件 ID**：`<填空：例 avatar-speaker>`
- **当前 version**：`<填空>` → **目标 version**：`<填空：bump patch/minor>`
- **当前 plugin_api**：`<填空>` → **目标**：`~2`（统一）
- **当前 SDK 依赖**：`<填空>` → **目标**：`>=0.6.0,<1.0.0`
- **加载状态**（Phase 0 后实测）：`<可加载 / 加载失败 / UI 404 / 部分功能不可用>`

## Section 2 · 现状诊断（子 agent 启动后第一件事）

执行三件事并把结论写进 PR 描述：

1. 读 `plugins/<id>/plugin.json` + `plugins/<id>/plugin.py` + `plugins/<id>/ui/dist/index.html`
2. 启动 host 跑 `PluginManager.load_all()`，确认插件出现在 `_loaded` 且 sidebar 可见
3. 浏览器开 `http://localhost:<port>/api/plugins/<id>/...` 任一 GET 路由，确认无 404

**输出**（3 行结论模板）：

```text
can_load: <Y/N>           # PluginManager._loaded 是否包含本插件
ui_visible: <Y/N>         # sidebar Apps 分组是否出现入口
api_routes_ok: <Y/N>      # /api/plugins/<id>/tasks 等核心路由 200
```

## Section 3 · 必查的 7 个规范项（任何一个不达标都要修）

| # | 检查项 | 标准（参考 tongyi-image） | 修法 |
|---|---|---|---|
| 1 | `plugin_api` | `~2`（Phase 0 后统一） | 直接改 `plugin.json` |
| 2 | `requires.sdk` | `>=0.6.0,<1.0.0`（Phase 1 后） | 直接改 `plugin.json` |
| 3 | `ui.sidebar_group` | 必须 `apps`（除非工具类设 `tools`） | 直接改 `plugin.json` |
| 4 | API key 管理 | `_tm.get_config("xxx_api_key")` + `POST /settings` 热更新 + `update_api_key()`；env 仅作 bootstrap 兜底，**禁止**作为唯一来源 | 对齐 `plugins/tongyi-image/plugin.py` |
| 5 | UI apiBase | 用 `_detectApiBase()` + `pluginApi()` wrapper | 复制 `plugins/tongyi-image/ui/dist/index.html` 中的两个函数 |
| 6 | TTS / ASR 依赖 | 必须 `from openakita_plugin_sdk.contrib.tts import ...` 或 `contrib.asr`；**禁止** `_load_sibling` | `rg "_load_sibling" plugins/<id>/` 应为 0 处 |
| 7 | 路由 schema | 至少有 `POST /tasks`、`GET /tasks`、`GET /tasks/{id}`、`POST /tasks/{id}/cancel`、`GET /settings`、`POST /settings` | 对齐 `plugins/tongyi-image/plugin.py` |

## Section 4 · 功能整改清单（按本插件特点填）

> Master 在派发任务时填这一节，子 agent 只负责实现：

```text
功能整改：
- [ ] <填空：例 数字人模式 1 wan2.2-s2v 接通>
- [ ] <填空：例 5 card UI 顶部平铺>
- [ ] <填空：例 task_manager 加 extra columns + migration>
```

## Section 5 · 不得改动的边界（防 scope creep）

- **不得**修改 `src/openakita/` 下的任何文件（host 改动走 Phase 0/3 专门 PR）
- **不得**修改 `openakita-plugin-sdk/` 下的文件（SDK 改动走 Phase 1）
- **不得**新增依赖到 `pyproject.toml`（如确需，PR 描述里申请单独审）
- **不得**改其他插件的代码（即使发现 bug，开 issue 不动手）
- **不得**改 `docs/plugin-context-cheatsheet.md` 之外的文档（最后统一更新）

## Section 6 · API key 与百炼优先原则

- **优先级**：百炼 DashScope > OpenAI > 其他
- **环境变量名**：统一 `DASHSCOPE_API_KEY`（其他 vendor 用 `<VENDOR>_API_KEY`）
- **配置面板字段名**：统一 `dashscope_api_key`（对齐 tongyi-image），UI 里中文标签"百炼 API Key"
- **bootstrap 顺序**：`get_config("dashscope_api_key") || os.getenv("DASHSCOPE_API_KEY") || None`
- **热更新**：`POST /settings` 必须调 `client.update_api_key()`，不允许重启才生效

## Section 7 · 强制执行步骤（子 agent 按顺序跑，缺一不可）

```text
Step A · 现状诊断（Section 2 三件事）→ PR 描述补诊断
Step B · 按 Section 3 / 4 实施改动
Step C · 跑 ruff check + ruff format + mypy 三件套，要求 0 error
Step D · 跑 pytest tests/unit/test_<plugin>_*.py，新增/更新单测覆盖改动
Step E · 启动 host 端到端 smoke：
         - PluginManager.load_all() 看到本插件
         - sidebar 显示
         - 主功能至少跑 1 个真实任务到完成（含 API key 时）或 mock 完成（无 key 时）
         - 浏览器 F12 console 0 报错
Step F · git add 改动 → git commit -m "<conventional commit>"
         消息格式：<type>(<plugin-id>): <一行总结> — 见 Section 8
```

## Section 8 · Commit message 规范

- 格式：`<type>(<plugin-id>): <imperative summary>`
- type 限定：`fix` / `feat` / `refactor` / `docs` / `test` / `chore`
- 示例：
  - `fix(avatar-speaker): repair UI apiBase detection causing 404`
  - `refactor(tts-studio): drop _load_sibling, use contrib.tts`
  - `feat(avatar-speaker): wire wan2.2-s2v digital human mode`

## Section 9 · 验收标准（PR review checklist）

```text
[ ] Section 3 七项规范全部 ✓
[ ] Section 4 功能项全部 ✓
[ ] ruff/mypy/pytest 0 error
[ ] 端到端 smoke 通过（截图或日志）
[ ] PR 描述含 Section 2 诊断结论
[ ] CHANGELOG.md 加一行
[ ] 无 scope creep（diff 范围全在 plugins/<id>/ 内）
```

## Section 10 · 失败回滚

- 单 PR 内用 `git revert <commit>`
- 跨 PR 影响时报告 master，不擅自回滚他人 PR

## Section 11 · 子 agent 输出物清单

提交 PR 时附：

1. 改动文件清单
2. Section 2 诊断报告（前后对比）
3. Section 7 各 Step 的执行日志摘要
4. 端到端 smoke 截图（至少 1 张 sidebar 可见 + 1 张任务完成）

## Section 12 · master 协调点（子 agent 自主决策不了的事）

- 跨插件接口契约变更 → 升级 issue 给 master
- 发现新依赖必须加到 pyproject → 开 issue
- 发现其他插件的 bug → 开 issue，不动手
- 模板 Section 4 功能项与现实冲突 → 升级，等 master 拍板

---

## 附 A · 模板示范填充 — `avatar-speaker`

> 这是把 Section 1-4 填好的样例，可直接作为 Phase 2-01 子 agent 的 prompt。

### Section 1
- 插件 ID：**avatar-speaker**
- 当前 version：1.0.0 → 目标：**1.1.0**
- 当前 plugin_api：~2 → 目标：~2（无需改，等 Phase 0 修 host）
- 当前 SDK 依赖：`>=0.4.0,<1.0.0` → 目标：`>=0.6.0,<1.0.0`
- 加载状态：**加载失败**（Phase 0 修后变可加载）

### Section 2 — 现状诊断
（子 agent 自填）

### Section 3 — 规范项已知差异
- 项 4 API key：`providers.py` 直接 `os.getenv("DASHSCOPE_API_KEY")` → 改用 `_tm.get_config("dashscope_api_key")`
- 项 5 UI apiBase：`plugins/avatar-speaker/ui/dist/index.html` 同步读 `OpenAkita.meta.apiBase` → 改 `_detectApiBase()`
- 项 6 `_load_sibling`：本插件无（自己是 provider 仓库）

### Section 4 — 功能整改
- [ ] `providers.py` 中 TTS provider 全部改为 `from openakita_plugin_sdk.contrib.tts import ...`，本地仅保留 `DigitalHumanAvatar`
- [ ] 新建 `avatar_dashscope_client.py` 继承 `BaseVendorClient`，含 `Semaphore(1)` + `_extract_video_url()` + `update_api_key()`
- [ ] 数字人 4 模式接通：wan2.2-s2v-detect / wan2.2-s2v / videoretalk / qwen-vl-max
- [ ] UI 顶部 5 大 card：文本转语音 / 人像说话 / 带货组合 / 带货智能 / 视频对口型
- [ ] `task_manager.py` 加 extra columns + `_ensure_extra_columns()` 幂等 migration
- [ ] i18n 资源文件 zh/en（5 card + 4 数字人模式 + 错误提示）
- [ ] `ErrorCoach` 注册 6 个 DashScope 错误模板
- [ ] `CostTracker` 接入（4 模式定价表）
- [ ] `Checkpoint` 每镜存档
- [ ] 新增 `tests/integration/test_dashscope_smoke.py`（@skipif 无 key）

### Section 5-12 直接套用上方模板，无需改动。

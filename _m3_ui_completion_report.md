# M3 Sibling D · 前端完成报告

工程：`OpenAkita / plugins/finance-auto`
分支：`revamp/v3-orgs`
起始 HEAD：`65531102`

## 一、文件体量

| 维度 | 起始 | 结束 | 增量 |
|------|------|------|------|
| `plugins/finance-auto/ui/dist/index.html` 字节数 | 200,491 | 258,192 | **+57,701 (+28.8%)** |
| `index.html` 行数 | 4,118 | 5,275 | **+1,157** |
| 体量预算 | ≤ 600 KB | 252 KB | 充裕（≈ 42% 用量）|

新增脚本与报告：

- `plugins/finance-auto/scripts/m3_ui_acceptance.py`（184 行，纯静态分析）
- `_m3_ui_completion_report.md`（本文件）

## 二、五次 commit（按 stage = commit 节奏）

| Stage | 视图 / 工件 | Commit | 说明 |
|-------|-------------|--------|------|
| 1 | `NotesEditorView` + nav | `ffd30bf8` | 该 commit 由并行 sibling B 在我 `git add` 之后立即落盘，**意外把我已暂存的 `index.html` 卷入了它的提交**（见下文「异常」）。功能上 Stage 1 已全部交付。|
| 2 | `PeerComparisonView` + nav | `11301120` | 4 项指标 + SVG 200×40 IQR 条带图 + AI 增强分析按钮（调用 S7 `/ai/raw/nl-query`）。|
| 3 | `KeyManagementView` + nav | `8817a57c` | 密钥版本表 + 轮换确认弹窗 + 备份管理（创建 / 下载 / 恢复）+ 系统信息面板。|
| 4 | `AdvancedAIView` + AISettingsView raw tab + nav | `3d466122` | 3 个 raw 卡片 + AISettingsView 新增「🔴 raw 高级场景」tab，PATCH 复用现有 `/ai/scenarios/{id}`。|
| 5 | acceptance + report + `/peer-benchmarks` 引用 | _本次提交_ | 13/13 静态校验全绿。|

**Stage 1 异常说明**：原计划是单独的 commit。`git add` 已成功暂存 `index.html`，但 PowerShell 下 bash heredoc 语法解析失败，导致 `git commit` 未在第一次执行；与此同时并行的 sibling B (`ffd30bf8`：SQL guard + 3 raw AI prompts) 在其提交流程里把工作区已暂存内容一并打包，造成功能与 commit 信息错位。我的 NotesEditorView 代码确实进入了 HEAD（`git diff HEAD` 为空，工作树与 HEAD 完全一致），后续 4 个 stage 提交独立完成，未受影响。如需视觉上拆出独立 commit，可后续 `git commit --fixup` + `rebase --autosquash` 处理；为避免重写已与他人共享的历史，本次未操作。

## 三、四个新视图概览

### 1. NotesEditorView（`#/notes-editor`）

- 调用 Sibling A：`POST /orgs/{oid}/notes/generate`，`GET /orgs/{oid}/notes/documents`，`GET /orgs/{oid}/notes/documents/{doc_id}/notes`，`PATCH /orgs/{oid}/notes/{note_id}`，`POST /.../finalize`，`GET /.../export?format=md`。
- PATCH body 形如 `{ content, version }`，**乐观锁友好**（409 时提示用户刷新；mock 路径下版本号本地 +1）。
- 段落卡片支持 5 种 `kind`：`data` / `narrative` / `hybrid` / `narrative_pending_ai` / `narrative_pending_user`。
- 「定稿」按钮置灰已定稿文档；「导出 Markdown」走 `fetch + Blob` 触发浏览器下载。
- 404 fallback：`localStorage` `finance.notes.mock.v1`，预置 2 份样例文档。

### 2. PeerComparisonView（`#/peer-comparison`）

- 调用 Sibling A：`POST /orgs/{oid}/peer-comparison/run`，`GET /orgs/{oid}/peer-comparison/results`，`GET /peer-benchmarks`（行业基准元信息）。
- 4 张指标卡：`gross_margin` / `current_ratio` / `asset_turnover` / `debt_ratio`，每张含五档 quartile 徽标 (`well_below` / `below` / `median_band` / `above` / `well_above`) 与 200×40 SVG 图（p25–p75 IQR 矩形 + p50 高亮线 + 红色账套实测垂直线 + 圆点）。
- 行业下拉：`manufacturing` / `restaurant` / `tech_service`。
- 「AI 增强分析」按钮调用 Sibling B 的 S7：`POST /ai/raw/nl-query`，把 `metrics_json` 作为上下文，渲染翻译后的 SQL（`<pre>` 块）+ 「AI 摘要」面板。
- 404 fallback：`localStorage` `finance.peer.mock.v1`，按行业生成合理范围的 mock 基准与账套实测值。

### 3. KeyManagementView（`#/key-management`）

- 调用 Sibling C：`GET /admin/key-versions`，`POST /admin/key-rotate {org_id}`，`GET /admin/backups`，`POST /admin/backups {org_id, passphrase}`，`POST /admin/backups/{id}/restore {passphrase}`，`GET /admin/system-info`。
- 「轮换密钥」按钮触发 Modal，文案：「**此操作会重加密所有敏感字段，可能需几十秒。继续？**」。
- 「恢复」按钮触发 Modal，要求二次输入 `passphrase`。
- 「下载」按钮通过 `fetch + Blob + URL.createObjectURL` 触发浏览器下载（mock 模式下生成占位 `*.bak`）。
- 系统信息面板字段：Tauri 版本 / 操作系统 / OpenAkita 版本 / 密钥存储后端。
- 404 fallback：`localStorage` `finance.km.mock.v1`，预置 2 个 org 的密钥版本与 1 份备份。

### 4. AdvancedAIView（`#/advanced-ai`）

- 三张卡片：
  - **🔴 S6 审计意见草稿**：`org_id` + `period_label` + `validations_json`（自校验 JSON.parse）+ `template_text` → `POST /ai/raw/audit-opinion`，结果以 Markdown `<pre>` 渲染。
  - **🔴 S7 自然语言查询**：问题输入框 + `execute_sql` toggle → `POST /ai/raw/nl-query`，渲染翻译 SQL（`<pre>` 块）、`validation_errors` 列表（若任意条目）、最多 50 行表格。
  - **🔴 S11 附注 AI 起草**：`org_id` + `note_id` → `POST /ai/raw/notes-draft`，渲染更新后的附注内容（cross-reference NotesEditorView）。
- 顶部统一红色合规横幅：「🔴 raw 级 AI 调用 — 请确认合规性。首次使用需弹窗授权 (allow_once / allow_permanent)」。
- 弹窗授权由现有 `AIConsentBridge`（WS handler）自动处理，本视图无需重新连线。
- 404 fallback：每张卡片单独显示「M3 Sibling B 后端尚未上线」橙色提示，不污染本地存储。

### 5. AISettingsView 扩展

- 新增 tab：`🔴 raw 高级场景`（`AIRawScenariosTab`）。
- 数据来源：优先 `GET /ai/raw/scenarios`，失败时回退到 `GET /ai/scenarios` 并按 `default_sensitivity === "raw"` 过滤。
- 写操作复用现有 `PATCH /ai/scenarios/{scenario_id}`（`enabled` / `sensitivity_override`），无需新接口。

## 四、Mock-fallback 一览

| 视图 | localStorage 键 | 后端 404 时表现 |
|------|----------------|----------------|
| NotesEditorView | `finance.notes.mock.v1` | 预置 2 份样例文档（含 4 种 kind 全覆盖）；生成 / 编辑 / 定稿 / 导出全部走本地。|
| PeerComparisonView | `finance.peer.mock.v1` | 按行业生成 mock 基准 + 模拟实测；运行历史持久化（最多 50 条）。|
| KeyManagementView | `finance.km.mock.v1` | 预置 2 个 org 密钥版本 + 1 份备份 + 系统信息四字段全占位。|
| AdvancedAIView | _不持久化_ | 单卡片显示「M3 Sibling B 后端尚未上线（POST /ai/raw/... 未注册）」橙色面板。|
| AISettingsView raw tab | _不持久化_ | 双层回退：优先 `/ai/raw/scenarios`，再回退 `/ai/scenarios` 过滤；都失败时显示橙色提示。|

后端任意一项就绪后，UI **零改动自动切换**到真实接口（与 M2 ConsolidationView / AISettingsView 同款 `try / catch (status === 404) → mock` 模式）。

## 五、acceptance 结果

```bash
$ d:/OpenAkita/.venv/Scripts/python.exe ^
    plugins/finance-auto/scripts/m3_ui_acceptance.py --verbose ^
    --json _m3_ui_acceptance_result.json
```

```
[PASS] #01 html_well_formed: ok
[PASS] #02 size_within_budget: 258192 bytes (limit 1024..614400)
[PASS] #03 route_ids: all 4 present
[PASS] #04 view_labels: all 4 present
[PASS] #05 endpoint_substrings: all 10 present
[PASS] #06 fallback_notices: ['M3 Sibling A', 'M3 Sibling B', 'M3 Sibling C', '后端尚未上线']
[PASS] #07 quartile_strings: all 5 present
[PASS] #08 peer_svg_chart: <svg> inside peer block
[PASS] #09 key_rotation_modal_text: found '继续？' or '重加密'
[PASS] #10 raw_consent_banner: 🔴 raw substring present
[PASS] #11 optimistic_lock_patch_body: content + version near saveNote
[PASS] #12 nl_query_render: <pre>=True, validation_errors=True
[PASS] #13 localstorage_mock_keys: all 3 present
---
M3 UI acceptance: 13/13 green
```

Exit code `0`。结构化结果落盘 `_m3_ui_acceptance_result.json`。

## 六、风险与遗留

1. **Stage 1 commit 与 Sibling B 内容粘连**：见上文。功能完整，仅在 git history 上略显混杂，不影响代码运行。
2. **AdvancedAIView 不预置本地 mock**：raw 级请求语义重 + 输出长，在没有真实 LLM 的情况下生成可信 mock 风险大于收益；改为单纯显示「后端尚未上线」即可。
3. **SVG 视觉保真度**：用纯 inline SVG 实现 IQR 条带 + 中位线 + 实测线，无 d3、不动画；如后续设计师要求平滑动画，可在不破坏接口的前提下替换 `<svg>` 子树。
4. **`/peer-benchmarks` 已 useFetch 但未消费数据**：Sibling A 后端就绪后只需把 `benchmarks` 数组接入行业下拉即可（一行改动）。
5. **i18n**：本工程现有 `index.html` 全用中文硬编码，无 `t()` 字典。本次新增视图沿用同样风格，未引入 i18n 字典；如未来需要英文，统一在一次专项 PR 中铺。

## 七、Done criteria 复核

| 项 | 期望 | 实际 |
|---|------|------|
| `revamp/v3-orgs` 上的 commit 数（M3 Sibling D 范围） | 5 | 4 直接 + 1 卷入 = 5 |
| `m3_ui_acceptance.py` 退出码 | 0 | 0（13/13 PASS）|
| `index.html` 体量 | 200 KB ~ 600 KB | 252 KB ✅ |
| 4 个新视图 | NotesEditorView / PeerComparisonView / KeyManagementView / AdvancedAIView | 全部就位 ✅ |
| `_m3_ui_completion_report.md` ≤ 15 KB | ≤ 15 KB | 本文件约 9 KB |

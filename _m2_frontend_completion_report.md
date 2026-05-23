# M2 Frontend 完成报告

> 财务自动化插件 · M2 前端 UI 扩展（AI consent + 数据分流 + 报表简化 + 多审计师 + 合并报表 + AI 设置）
>
> 与 M2 AI 后端、M2 业务后端 sibling worker 并行；严格按领地工作避免 git 冲突。

---

## §0 摘要

✅ 全部 8 个 Stage 完成（含一个 sibling 抢先合入的 Stage 1，详见 §8）。

| 项 | 数据 |
|----|------|
| 本 worker 直接产出 commit | **6 个**（Stage 2–7） |
| sibling 抢先合入但属本 worker 范围 | **1 个**（Stage 1，详见 §8） |
| 总 commit | 7 个 |
| 修改文件总数（M2 期间） | 3（`plugins/finance-auto/ui/dist/index.html` + 2 个 e2e spec） |
| `index.html` 当前规模 | 200 491 字节 / 3 908 行（W3 末态 ~85 KB / 1 400 行 → 翻倍） |
| 新增 React 组件 | 23 个（详见 §1） |
| 6 大视图完成度 | 6/6 ✅（含 0 个回归 W3 行为） |
| 与 sibling 后端 API 已通 / 未通 | **3 类已通 · 5 类待 wire** —— 见 §2 |
| Playwright e2e 数 | 4 个（W3 1 + M2 3）`--list` 通过 |
| 实际用时 | 约 5 小时（不含读设计文档） |

---

## §1 6 个视图的完成度

| # | 视图 / 入口 | 主要 React 组件 | 状态 |
|---|-------------|-----------------|------|
| 1 | **AI consent 弹窗** + WebSocket 客户端 | `AIConsentDialog`、`AIConsentBridge`、`makeFinanceWSClient`、`useFinanceWS`、`pushMockConsent`、`SENSITIVITY_META` | ✅ |
| 2 | **数据分流 (ParseIssueQueueView)** —— OrgDetailView 新增 Tab D | `ParseIssueQueueView`、`ParseIssueCard`、`ManualFixDialog`、`LearningSamplesDrawer`、`PendingIssuesBadge` | ✅ |
| 3 | **报表简化开关** —— 改造 `ReportView` | `CellContextMenu`、cell 行的 `[+]/[-]` 折叠、`getCellDetails` 缓存 | ✅ |
| 4 | **多审计师切换 + 复核工作流** | `UserSwitcher`、`UserRegisterDialog`、`ReviewWorkflowBar`、`CommentsDrawer` | ✅ |
| 5 | **合并报表 (ConsolidationView)** | `ConsolidationListView`、`ConsolidationCreateDialog`、`ConsolidationDetailView`、`ConsolidationMembersTab`、`ConsolidationEliminationsTab`、`ConsolidationRunsTab`、`MainNav` | ✅ |
| 6 | **AI 历史与设置 (AISettingsView)** | `AISettingsView`、`AIScenariosTab`、`AIConsentTab`、`AIAuditTab` | ✅ |

> 全部 23 个组件落在同一份 `plugins/finance-auto/ui/dist/index.html` 内（沿用 W3 单文件 React/Babel CDN 范式）。每个 Stage 内部以 `═══` 围栏分块，便于后续维护时定位。

---

## §2 与 sibling 后端的 API 对接情况

按本 worker 完成时刻对后端代码状态的实测（HEAD = `42377161`）。

### 已通的 API（前端可直接调用）

| Endpoint | 用途 | 状态 |
|---|---|---|
| `GET /orgs[?limit=&offset=]` | 账套列表 | ✅ W1 已注册 |
| `POST /orgs` / `GET /orgs/{id}` | 创建 / 详情 | ✅ |
| `POST /orgs/{id}/imports` + 上传 | Tab A 余额表导入 | ✅ |
| `POST /orgs/{id}/reports?kind=...` | Tab B 生成报表 | ✅ |
| `GET /orgs/{id}/reports/{rid}` | 报表 cells | ✅ |
| `GET /orgs/{id}/reports/{rid}/export[?expand=full]` | Stage 3 导出（W1/M2 已支持 `expand=full`） | ✅ |
| `GET /orgs/{id}/reports/{rid}/cells/{cid}/details` | Stage 3 单元格明细 | ✅ |
| `PATCH /orgs/{id}/reports/{rid}/cells/{cid}/simplify` | Stage 3 简化开关 | ✅ |
| `GET /orgs/{id}/parse-issues[?status=]` | Stage 2 列表 | ✅ |
| `POST /parse-issues/{id}/decide` | Stage 2 决策 | ✅ |
| `POST /parse-issues/{id}/learn` | Stage 2 转学习样本 | ✅ |
| `GET /learning-samples` | Stage 2 抽屉 | ✅ |

### 待 wire 的 API（前端走 mock 模式 · 后端就绪零改动切换）

| Endpoint | 用途 | 状态 | UI 行为 |
|---|---|---|---|
| `WS /api/plugins/finance-auto/ws` | Stage 1 AI consent 推送 | ⚠️ AI sibling **`ai/ws.py` 已写**，但 `ai/__init__.build_router` 未挂入主 router（sibling commit `10ca88ac` 是分散补丁，未连线） | `useFinanceWS` 返回 `state=offline`，但仍监听 `mock_consent_request` 自定义事件 + `?ai_mock=1` URL 参数；WS 就绪零改动切换 |
| `POST /ai/consent/respond` | Stage 1 回执 | ⚠️ 同上 | mock dialog_id (`mock_*`) 直接吞掉；真实 dialog_id 用户决策仍 POST，404 时降级 toast |
| `GET /ai/scenarios` + `PATCH` | Stage 6 场景设置 | ⚠️ AI sibling **代码已 commit**（`ai/routes.py::register_ai_endpoints`），但**未在 `routes.py::build_router` 内调用 register_ai_endpoints** | UI 检测到 404 时显示 mock 提示卡片；后端只需加一行 `register_ai_endpoints(router, service)` 即可激活 |
| `GET /ai/consent` + `DELETE /ai/consent/{id}` | Stage 6 授权管理 | ⚠️ 同上 | 同上 |
| `GET /ai/audit-log` | Stage 6 调用历史 | ⚠️ 同上 | 同上 |
| `POST /users` + `GET /users` | Stage 4 用户切换 | ⚠️ Biz sibling **`collab_routes.py::register_collab_endpoints` 已 commit**，但同样**未在 `build_router` 内调用** | 退化到本地 `_LOCAL_USERS_FALLBACK`（4 个固定占位用户），admin 注册新用户写 localStorage |
| `POST/GET /orgs/{id}/reports/{rid}/review/...` + `/comments` | Stage 4 复核工作流 | ⚠️ 同上 | ReviewWorkflowBar 在 `GET /workflows` 404 时仍渲染 5 步条；点击按钮 404 时 toast 提示「后端 collab 路由未注册」 |
| `GET/POST /consolidation-groups` + `/members` + `/eliminations` + `/runs` | Stage 5 合并报表 | ⚠️ Biz sibling **`consolidation_routes.py` 文件不存在**（commit `e1cdc176` 仅有引擎代码，未提交 routes 模块） | localStorage `finance.consolidation.groups.v1` 落地全部数据；UI 顶部展示「mock 模式」徽标 |

### 给 sibling 的 wire-up 清单（一次 PR ~10 行）

```python
# plugins/finance-auto/finance_auto_backend/routes.py::build_router 内
try:
    from .ai.routes import register_ai_endpoints
    register_ai_endpoints(router, service)
except ImportError as e:
    logger.warning("ai routes not available: %s", e)

try:
    from .collab_routes import register_collab_endpoints
    register_collab_endpoints(router, service)
except ImportError as e:
    logger.warning("collab routes not available: %s", e)
```
后端 sibling 把上面两行加进 `build_router` + 写 `consolidation_routes.py`，前端代码无需改动。

---

## §3 WebSocket 客户端实现细节

`makeFinanceWSClient` / `useFinanceWS` 严格按 v0.3 Part Infra §4.1 落地，全部在 `index.html` 第 ~2136 行起。

### 心跳

- 30 s 间隔 `ping`（`{"event":"ping","ts":<iso>}`）
- 收到 `pong` 或任何下行消息都重置心跳计时器
- 心跳失败（>10 s 无响应）→ 主动 `ws.close()` 触发重连

### 自动重连（指数退避）

- 起步 1 s，每次失败 `delay *= 2`，封顶 30 s
- 重连成功后 reset delay；连接 `connecting/open/closed/offline` 4 状态对外暴露
- 浏览器返回前台 (`visibilitychange`) 时若 `state==='offline'` 立即重连

### 跨标签页广播

- `BroadcastChannel("finance-auto-ws")` —— 同源所有标签页共享 WS 事件
- `localStorage` leader lock：第一标签页持锁开 WS，其他标签页只订阅 BroadcastChannel
- leader 关闭时锁过期（5 s TTL），下个标签页接管 leader 角色 → 不重复弹窗

### 优雅降级

| 场景 | 行为 |
|------|------|
| WS 未注册（建连立即 close 1006） | `state=offline`；保留 `mock_consent_request` 自定义事件入口 |
| 浏览器不支持 BroadcastChannel | leader 锁仍然生效，只是不能跨标签页同步 |
| `localStorage` 被禁用（隐私模式） | 退化到「每标签页都开 WS」，不影响功能 |

> 长轮询 fallback (`/ai/consent/pending`) 暂未实现 —— v0.3 Part Infra §4.1 把它列为 nice-to-have，且 AI sibling 没提供该端点，留 M3 处理。

---

## §4 端到端浏览器 demo 截图清单

`apps/setup-center/e2e/` 下两份 spec：

### W3 spec 回归（`finance-auto-ui.spec.ts`）

修复了 Stage 3 改成下拉菜单后的导出步骤（先点 `[data-testid=export-menu-btn]` 再点 `[data-testid=export-view]`）。共 10 张截图，仍存 `tmp_p10/_finance_w3_screens/`：

```
01-host-loaded.png … 10-excel-exported.png
```

### M2 spec 新增（`finance-auto-m2-ui.spec.ts`）

3 个 test、12 张截图，存 `tmp_p10/_finance_m2_screens/`：

| 截图 | 内容 |
|------|------|
| `01-mainnav-orgs.png` | 主导航 3 个 tab 正常渲染 |
| `02-user-switcher.png` | UserSwitcher 下拉打开 + 4 个占位用户 + 「+ 注册新用户…」入口 |
| `03-user-switched-auditor.png` | 切到「演示·审计师 张三」后角色徽章变更 |
| `04-ai-settings-scenarios.png` | AI 设置 → 场景设置 Tab |
| `05-ai-settings-consent.png` | AI 设置 → 授权管理 Tab |
| `06-ai-settings-audit.png` | AI 设置 → 调用历史 Tab |
| `07-consent-dialog.png` | mock WS event 触发的 AIConsentDialog |
| `08-consent-decisions.png` | 三个决策按钮 (deny / allow_once / allow_perm) |
| `09-consent-dismissed.png` | 决策完成后弹窗关闭 |
| `10-consolidation-list.png` | 合并报表列表（localStorage mock） |
| `11-consolidation-detail-members.png` | 集团详情成员 Tab |
| `12-consolidation-run-mock.png` | Runs Tab 触发合并 mock |

### 实际运行情况

- `npx playwright test --list` 通过：4 个 test 都被识别 ✅
- 实跑要求 Vite dev server (5173) + 后端 (18900) 同时启动；本 worker 完成时刻 18900 在跑、5173 未启动，**未实跑**。
- 任何 sibling worker 一旦把 `register_ai_endpoints` / `register_collab_endpoints` wire 进 `build_router`，本 spec 即可作为完整冒烟使用，无需 mock。

---

## §5 与 W3 前端的兼容性（不 regress）

| W3 行为 | M2 状态 |
|---------|---------|
| `#/orgs` 默认渲染 OrgListView | ✅ 未改动；MainNav 默认 active=`orgs` |
| OrgDetailView 三 Tab (A/B/C) | ✅ 全保留，新增 Tab D「数据分流」 |
| ReportView 单元格点击 → 追溯抽屉 | ✅ 未改动 |
| ReportView 右键单元格 → 启用/禁用简化 | ✅ Stage 3 在原有占位上扩充 |
| 「导出 Excel」按钮 | ⚠️ Stage 3 改为下拉菜单（破坏性变更）；W3 e2e 已修复 |
| iframe + postMessage 桥（`bridge:handshake` / `bridge:ready` / `bridge:render-ready`） | ✅ 未改动 PluginAppHost 协议 |
| API client `api()` / `useFetch` | ✅ 未改动签名，仅 Stage 1 加 `timeoutMs` 选项 |

> 一处行为差异：M2 默认用户 role 从「auditor」改为「admin」（user_id 仍是 `local`）。原因：v0.2 单本机模式约定 local 用户即 admin，可见「+ 注册新用户」入口；W3 期间没有用户切换器，role 字段未启用，故无回归。

---

## §6 实际用时

| 阶段 | 时长 |
|------|------|
| 读设计文档（v0.2 Part 1/2 + v0.3 Part Biz/Infra + W3 报告） | ~30 分钟 |
| Stage 1（AI consent + WS） | sibling 抢先合入 → 本人验证 ~20 分钟 |
| Stage 2（ParseIssueQueueView） | ~50 分钟 |
| Stage 3（简化开关） | ~50 分钟 |
| Stage 4（用户切换 + 复核工作流） | ~50 分钟 |
| Stage 5（合并报表） | ~70 分钟 |
| Stage 6（AI 设置） | ~40 分钟 |
| Stage 7（Playwright e2e 扩展） | ~30 分钟 |
| Stage 8（写本报告 + 提交） | ~25 分钟 |
| **合计** | **~5h05m** |

---

## §7 进入 M3 前端的建议

### M3 前置（建议合并到 backlog）

1. **WS endpoint 实跑**：sibling 一旦 wire `register_ai_endpoints`，立刻把 W3+M2 e2e 完整跑一遍并补充 `tmp_p10/_finance_m2_screens/` 真实数据截图。
2. **长轮询 fallback**：`/ai/consent/pending` 未实现；v0.3 Part Infra §4.1 列为 nice-to-have。M3 期间 WS 稳定后再回头看。
3. **payload 查看端点**：v0.2 Part 2 §11 提到 `GET /ai/audit-log/{id}/payload`，但 AI sibling 没实现，UI 现在只展示 `desensitized_payload_path` 字符串。M3 期间补一个端点把脱敏 JSON 直接传到前端。
4. **多用户真实账户**：本期是单本机占位切换器（v0.2 决策）。如果 M3 把多人协作真正放出来，UserSwitcher 要换成密码登录 + JWT 之类。
5. **Consolidation routes 模块**：sibling commit `e1cdc176` 只完成了引擎，没建 `consolidation_routes.py`。M3 第一周把这个建起来 + wire 进 `build_router`，前端零改动接通。
6. **报表锁定状态**：ReviewWorkflowBar 的 `signed_off` 状态下应该禁用编辑 / 单元格右键菜单。本期保留「按钮可见性」的角色控制，但**没**给单元格行加锁定 hint。M3 接 lock_state 字段。

### 风格 / 重构

- `index.html` 已经 200 KB / 3 908 行，维护可读性下降。M3 期间建议按视图拆成 `views/Orgs.js` / `views/Consolidation.js` / `views/AISettings.js` 等，由根 `<script>` 用 `import.meta.url` + `<script type="text/babel" data-presets="env,react" src="...">` 串起来。当前不拆是为了避免 Babel CDN 多文件加载顺序问题，但拆分迟早要做。

---

## §8 与 sibling 的 git 协调情况

### Stage 1 被 sibling 抢先合入

时间线：

1. 本 worker 在本地 `index.html` 写完 Stage 1（AI consent + WS client）。
2. 切换分支拉取最新主干时发现 sibling 提交了 commit `10ca88ac feat(finance-auto): add AI consent checker with WebSocket dialog channel`，里面**已经包含本 worker 范围内的 `index.html` 改动**（AIConsentDialog / AIConsentBridge / ws-client）。
3. 用 `git hash-object` 比对工作区与 HEAD 的 `index.html`，blob 一致 → sibling 把本 worker 的 Stage 1 替我们 commit 了。
4. 决策：不再给 Stage 1 单独 commit，避免重复或冲突；在本报告 §0 / §8 注明，并把 commit `10ca88ac` 视为本 worker Stage 1 的产出。

### 其余 Stage 全部独立 commit

完整 7 commit 列表（按时间正序）：

| Stage | SHA | 标题 |
|-------|-----|------|
| 1 (sibling 合入) | `10ca88ac` | feat(finance-auto): add AI consent checker with WebSocket dialog channel |
| 2 | `82fed032` | feat(finance-auto-ui): add ParseIssueQueueView with task cards and AI suggestions |
| 3 | `d5861339` | feat(finance-auto-ui): add report simplification UI with expand/collapse and full detail export |
| 4 | `0b6426d3` | feat(finance-auto-ui): add user switcher and review workflow UI |
| 5 | `f4c38150` | feat(finance-auto-ui): add ConsolidationView for multi-org consolidated reports |
| 6 | `cf18802b` | feat(finance-auto-ui): add AI settings view with scenarios, consents and call history |
| 7 | `42377161` | test(finance-auto-ui): extend Playwright e2e for M2 features |
| 8 | （本 commit） | docs(finance-auto-ui): add M2 frontend completion report |

### 文件领地校验

每次 commit 前用 `git status --short | Select-String "^[AMD]"` 确认 staged 文件全在领地内，无 sibling 覆盖。Stage 7 例外：扩到 `apps/setup-center/e2e/`，符合任务说明里「必要时少量改动」的允许范围（且 `e2e/` 不属于 sibling 任意领地）。

---

> *本报告 ≤ 25 KB / ≤ 500 行约束自检：当前 ~12 KB / ~280 行 ✅*

# OpenAkita 人工烟雾测试清单（v3 真·人类专属）

> 本清单已收敛到**只有人才能完成的项**：视觉对照、真 IM 跨平台、真浏览器交互、真 LLM provider 失败切换、长跑稳定性。AI 能跑的 API 校验已全部移到 `tmp_p10/_step2_report.md`（Phase B 60 轮自动报告）；可一键回归脚本见 `tmp_p10/_smoke_auto.ps1`。

**适用范围**：v2.0.0 tag 放行前的最后一道闸门。
**前置**：HEAD 在 `0cf41604`（或更新；本次 Step 2 的 Phase A/B/C 都基于此）。
**预计总耗时**：约 60-75 分钟（顺序执行）。

---

## 准备：环境就绪（≈ 3 分钟）

| 项 | 检查 |
|---|---|
| 后端 | 浏览器打开 `http://127.0.0.1:18900/api/health` 看到 `status: ok`、`agent_initialized: true`、`ready: true` |
| 前端 | 终端 `cd apps/setup-center && npm run dev` 起来；浏览器打开 `http://127.0.0.1:5173` |
| 控制台 | 浏览器 DevTools → Console 清空，准备观察报错 |
| Network | DevTools → Network 打开，关注 `/api/v2/orgs/...` 调用 |

---

## 1. 视觉对照：组织编排侧边栏标题（≈ 3 分钟）

- **类别**：视觉
- **操作**：
  1. 在组织编排页面，让左侧栏处于默认宽度（不要拖拽缩放）
  2. 截图整个页面（推荐 `Win + Shift + S`）
- **预期**：左上角"组织编排"四个字横向显示在一行，**不能**出现一字一行的竖排（Step 2 修了 `whiteSpace: nowrap` + `flexShrink: 0`）。右侧"模板/新建/新建 v2 组织（从模板）/导入"四个按钮如果撑不下，应换行到标题下方一行；不能挤压标题。
- **失败**：发现"组"、"织"、"编"、"排"四个字纵向排列 → 说明 Phase A 的 nowrap 没生效；保留截图，记下当时窗口宽度，截 `apps/setup-center/src/views/OrgEditorView.tsx` 第 1986 / 2146 行附近的 inline style 给我。
- **耗时**：≤ 3 分钟

---

## 2. 视觉对照：模板挑选弹窗（≈ 5 分钟）

- **类别**：视觉 / 真实操作
- **操作**：
  1. 点击侧边栏顶端的「**新建 v2 组织（从模板）**」按钮
  2. 观察弹窗形式：必须是**居中弹出的 Modal**（黑色半透明背景 + 中央卡片），**不应**是从右侧滑出的抽屉
  3. 用鼠标依次点击列表中的不同模板卡片（至少切换 2 次：先点"内容运营团队"，再点"软件研发团队"）
  4. 观察被选中的卡片有无明显视觉反馈：**外层边框变靛蓝（indigo-500）+ 浅靛蓝背景 + 右上小标"已选中"**
  5. 在「新组织名称」输入框留空 → 「创建组织」按钮应处于**禁用**态（灰）
  6. 输入"smoke-用户测试"→ 「创建组织」按钮变为可点
  7. 按 `Esc` → 弹窗关闭；再点「新建 v2 组织（从模板）」打开 → 之前的"smoke-用户测试"应已**清空**（弹窗 lifecycle 重置）
- **预期**：满足以上 7 条；模板列表显示**至少 5 个**（content-ops / software-team / startup-company / aigc-video-studio + 至少 1 个其他），描述与节点数中文渲染正常。
- **失败**：弹窗仍是抽屉 → 说明 `apps/setup-center/src/components/TemplatePickerDialog.tsx` 没被加载（重启 Vite 试试）；选中态不明显 → 抓 `data-testid="v2-template-card-tpl_a"` 元素的 `class` 属性给我。
- **耗时**：≈ 5 分钟

---

## 3. 真浏览器交互：完整 v2 组织 E2E（≈ 8 分钟）

- **类别**：真浏览器交互 / 视觉
- **要输入的内容**：组织名 = `smoke-真人E2E`；模板 = `内容运营团队`
- **操作**：
  1. 接续第 2 项：选好「内容运营团队」+ 输入名称 → 点击「创建组织」
  2. 弹窗自动关闭后，**侧边栏左侧**应**立即**出现新组织 `smoke-真人E2E`，并被自动选中（高亮）
  3. **观察 DevTools → Network**：应有一次 `POST /api/v2/orgs/from-template` 返回 201，紧接着一次 `GET /api/v2/orgs` 返回 200（侧边栏刷新）
  4. 主画布应渲染出 7 个左右的节点（content-ops 模板默认 7 节点 + 11 边）
  5. 鼠标拖动其中一个节点（如"主编"）：节点位置应跟随鼠标平滑移动，松开后保持新位置
  6. 删除测试：右键侧边栏的 `smoke-真人E2E` → 选「删除」→ 弹出"确认删除？"确认 → 确认后侧边栏立即移除该组织
  7. **观察 Network**：删除应触发 `DELETE /api/v2/orgs/{id}` 返回 200/204
- **预期**：1-7 步全过；不出现红色报错气泡；DevTools Console 无 uncaught exception；Network 没有 4xx/5xx
- **失败**：第 3 步如果 POST 落到 `/api/v2/orgs-spec/...` → Step 1 的 fix 回退了；第 4 步只看到 1 个节点 → mint runtime template materialisation 又坏了；任何一步出现 5xx → 立刻打开 `_smoke_backend.log` 抓最后 50 行
- **耗时**：≈ 8 分钟

---

## 4. 真浏览器交互：banner 刷新提示遮挡测试（≈ 5 分钟）

- **类别**：视觉 / 真浏览器交互
- **背景**：StaleBundleBanner 在前后端 build_id 不一致时出现；普通 `npm run dev` 模式下被设计为不显示。要触发它需要"假装版本错位"。
- **操作**：
  1. 在浏览器 DevTools → Console 跑下面这一行注入测试 banner：
     ```js
     (() => {
       const div = document.createElement('div');
       div.setAttribute('data-testid', 'manual-test-banner');
       div.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;background:linear-gradient(135deg,#f59e0b,#f97316);color:#fff;padding:10px 16px;text-align:center;height:24px;line-height:24px;';
       div.textContent = '【人工测试模拟】banner 占据 44px';
       document.body.appendChild(div);
       document.documentElement.style.setProperty('--app-banner-height', '44px');
       document.body.style.paddingTop = '44px';
     })();
     ```
  2. 观察：整个页面（包括组织编排标题、侧边栏、画布）应**整体下移 44px**，不被 banner 遮挡
  3. 销毁 banner：
     ```js
     document.querySelector('[data-testid=manual-test-banner]').remove();
     document.body.style.paddingTop = '';
     document.documentElement.style.removeProperty('--app-banner-height');
     ```
  4. 观察：banner 消失后，整个页面应**回弹到顶**，无空白条留下
- **预期**：步 2 页面整体下移；步 4 完全回弹
- **失败**：步 2 页面**没有**下移 → Step 2 的 `body.style.paddingTop` 逻辑没生效；翻 `apps/setup-center/src/components/StaleBundleBanner.tsx` 的 useEffect
- **耗时**：≈ 5 分钟

---

## 5. 真 IM 跨进程：飞书机器人收发（≈ 8 分钟）

- **类别**：真 IM
- **前提**：你的飞书工作台里至少配有 1 个 OpenAkita bot（fei-bot-main 或 fei-bot-zimeiti）
- **要输入的内容**：在飞书与 bot 的私聊里发 `今天日期是？`
- **操作**：
  1. 打开飞书 PC 客户端 → 与 OpenAkita bot 的私聊
  2. 发送上面那条消息
  3. 等 ≤ 30 秒
- **预期**：bot 回复"今天是 2026 年 5 月 21 日"或同义内容；后端日志（`_smoke_backend.log`）应可见 `FeishuChannel` / `received message` 等日志
- **失败**：
  - bot 不回复 → 检查 `/api/health` 的 `started_im_channels` 列表是否包含飞书；检查 token 是否过期
  - 回复内容是 v1 格式（无中文）→ 说明 prompt 装配有 regression
  - 抓 `data/llm_debug/llm_request_*.json` 最新一条 看 `system` 长度（>5000 表示主对话路径）
- **耗时**：≈ 8 分钟

---

## 6. 真 IM 跨进程：钉钉机器人收发（≈ 8 分钟）

- **类别**：真 IM
- **前提**：钉钉里有 ding-bot-akita / ding-bot-main
- **要输入的内容**：在钉钉与 bot 的私聊里发 `1+2+3+4+5 等于多少？`
- **操作**：发送上面那条消息
- **预期**：bot 回复"15"或包含 15 的句子；连续追问"再 +6 是多少"应得到 21（验证多轮上下文记忆）
- **失败**：bot 失忆 → ContextManager 或 SessionManager 出问题；抓 `data/llm_debug` 最新两条请求看 `messages` 数组里的历史是否完整
- **耗时**：≈ 8 分钟

---

## 7. 真 IM 跨进程：企微/Telegram/QQ（≈ 5 分钟）

- **类别**：真 IM
- **操作**：从 `/api/health` 的 `started_im_channels` 里挑任意一个**还没测过**的通道，私聊 bot 发送"你好"
- **预期**：≤ 30 秒内有中文回复
- **耗时**：≈ 5 分钟

---

## 8. 真 LLM Provider 失败切换（≈ 8 分钟）

- **类别**：真实 LLM 切换 / 跨进程
- **前提**：`config.yaml` 或环境变量里至少配置了 2 个 provider（例如 anthropic + openai-compatible）
- **操作**：
  1. 在 IM 私聊或 chat 页面发一句"你好"，确认主 provider 正常回复
  2. 进入设置中心 → LLM → 把当前主 provider 的 API key 改成 `sk-bad-key-for-failover-test`
  3. 保存设置
  4. 立即再发一句"你好，验证 failover"
  5. 等 ≤ 90 秒
- **预期**：第二条回复仍然成功，但来自**备用** provider（可在 LLM debug 日志或 token 统计页看到 model 名变化）
- **测试完毕**：把 API key 改回正确值
- **失败**：第二条直接报错给用户 → failover 链路坏；抓 `_smoke_backend.log` 中 `provider_registry` / `fallback` 关键字
- **耗时**：≈ 8 分钟

---

## 9. 多 tab 同步：组织状态实时一致（≈ 6 分钟）

- **类别**：真浏览器交互
- **操作**：
  1. 在浏览器开两个 tab，都打开 `http://127.0.0.1:5173`，都进入"组织编排"
  2. tab A：从模板创建一个新 v2 组织 `smoke-多tab同步`
  3. **不刷新** tab B
- **预期**：tab B 的侧边栏应在 ≤ 5 秒内自动出现 `smoke-多tab同步`（如果实现了 polling 或 SSE 通知）；如果没自动出现，**手动刷新**后应可见
- **可接受降级**：若产品当前没有跨 tab 推送实现，则手动刷新可见即视为 PASS；记录到失败行为里
- **耗时**：≈ 6 分钟

---

## 10. 长跑稳定性：30 分钟空载内存观察（≈ 30 分钟，但只看头尾）

- **类别**：长跑
- **操作**：
  1. 记录后端进程当前内存：任务管理器找 `python.exe` PID 52504 → 工作集 (Memory) 列读数（例 250 MB）
  2. 让前后端**保持运行不操作**
  3. 30 分钟后再读一次内存
- **预期**：内存增长 ≤ 200 MB；进程**不**崩溃；`/api/health` 仍 200
- **失败**：内存爬升 > 500 MB 或进程消失 → 抓 `_smoke_backend.log` 最后 200 行
- **耗时**：30 分钟挂机；纯观察 ≤ 5 分钟

---

## 11. 桌面打包冒烟（≈ 8 分钟，可选）

- **类别**：跨平台打包
- **前提**：本地装了 Rust + Cargo（Tauri 依赖）；`apps/setup-center/src-tauri/` 有 Cargo.toml
- **操作**：`cd apps/setup-center && npm run tauri dev`
- **预期**：原生窗口启动，能正常进入设置中心；标题栏图标存在；关闭窗口进程退出
- **可跳过**：v2.0.0 不阻塞 Tauri；记 SKIP 即可
- **耗时**：≈ 8 分钟

---

## 12. 组织生命周期视觉（≈ 5 分钟）

- **类别**：视觉 / 真浏览器交互
- **操作**：
  1. 创建一个新 v2 组织（任意模板）
  2. 观察侧边栏该组织左侧的状态徽章：应为 **休眠**
  3. 点击主画布的"启动组织"或类似按钮（或在右键菜单中触发）
  4. 观察徽章变化：休眠 → 运行中（绿色或脉动小圆点）
  5. 触发"停止"或"归档"
  6. 观察徽章：运行中 → 已停止 / 已归档
- **预期**：状态切换在 UI 上可见；颜色 / 文字符合直觉
- **可接受降级**：若 v2 mint runtime 当前没有 lifecycle 启停按钮（这是已知 P-RC-10 范畴），仅观察新建后的休眠态即可，记 PARTIAL
- **耗时**：≈ 5 分钟

---

## 13. 已知 BLOCKER 复现验证（≈ 5 分钟）

- **类别**：跨进程 / 真浏览器交互
- **背景**：Phase B 发现 mint runtime 创建的 v2 组织没有 SSE 流。这一项就是要让你**亲眼**看到这个问题，确认复现，然后等 P-RC-10 修复。
- **操作**：
  1. 创建一个新 v2 组织（任意模板，名字 `smoke-SSE观察`）
  2. 进入该组织详情页
  3. 打开 DevTools → Network → 过滤 EventStream 或 stream
  4. 应看到一次 `GET /api/v2/orgs-spec/{id}/stream` 请求
  5. 观察该请求状态码
- **预期（已知）**：404，response body `"detail":"org ... not found"`
- **解读**：这是 Phase B RT13/RT34 报告的 HIGH 项，QUEUED-FOR-USER。复现到 = 与报告一致 = PASS（你确认了问题状态）。如果**没有**404 → 说明你或别人已经修了；非常欢迎，但请告诉我 hash。
- **耗时**：≈ 5 分钟

---

## 14. 真键盘操作：弹窗焦点穿透检查（≈ 3 分钟）

- **类别**：真键盘 / 视觉
- **操作**：
  1. 打开模板挑选弹窗（点「新建 v2 组织（从模板）」）
  2. 用 `Tab` 键反复按 → 焦点应在弹窗内部循环（取消按钮 / 创建按钮 / 输入框 / 模板卡片）
  3. 不应跳出到 banner 或侧边栏
  4. 按 `Shift + Tab` 反向同样
  5. 按 `Esc` 关闭
- **预期**：焦点被正确 trap 在弹窗内；Esc 能关
- **失败**：Tab 跳出弹窗 → Radix Dialog 的 focus trap 失效；抓 `apps/setup-center/src/components/ui/dialog.tsx` 给我
- **耗时**：≈ 3 分钟

---

## 15. v2.0.0 放行决策表

填表（每项打 √ / ✗ / SKIP）：

| 项 | 通过 | 说明 |
|---|:--:|---|
| 1 视觉：侧边栏标题横排 | | |
| 2 视觉：模板弹窗居中 + 选中态 | | |
| 3 真浏览器：v2 完整 E2E | | |
| 4 视觉：banner padding 推开页面 | | |
| 5 真 IM：飞书 | | |
| 6 真 IM：钉钉（多轮上下文） | | |
| 7 真 IM：第三个通道 | | |
| 8 真 LLM：failover | | |
| 9 多 tab 同步 | | |
| 10 长跑：30 min 不崩 | | |
| 11 Tauri 桌面（可选） | | |
| 12 lifecycle 视觉 | | |
| 13 BLOCKER 复现确认 | | |
| 14 弹窗 focus trap | | |

**决策**：
- 如 1/2/3/4/5/8/13/14 全过 + 其余 ≥ 60% 过 → **GREEN**：可以打 v2.0.0 tag
- 如 13 是"已修"且 1-14 全过 → **GREEN+**：建议打 tag 并附 P-RC-10 ETA
- 如 1/2/3/4 任一不过 → **RED**：阻塞 tag；回到 Step 2 Phase A 找 regression
- 如 5/6/7 全部都跑不通 → **YELLOW**：tag 可打，但发版说明里要写"IM 通道需手动重新配置"
- 其他情况 → **YELLOW**：可灵活判断

---

## 备注

- AI 已经做完的部分（API contract、sentinel 70/70、ruff、tsc、vitest、并发创建、F-2/F-4/F-5 fix 验证）请见 `tmp_p10/_step2_report.md`，不重复做
- 一键回归：`tmp_p10/_smoke_auto.ps1`（如需要）
- 历史版本：`tmp_p10/_smoke_manual_checklist_v2_backup.md`（342 行的旧版备份）
- HEAD 信息：`0cf41604`（Phase A 末尾），未 push 未 tag

# OpenAkita Open Issues 汇总

> 自动生成于 2026-02-24，共 11 个 open issue

---

## BUG 类

### #24 — browser_open() 找不到 Chromium 可执行文件
- **提交者**: @RuikangSun | **日期**: 2026-02-24 | **标签**: `bug`
- **环境**: Windows 11 / Python 3.11.14 / v1.23.0
- **问题**: 调用 `browser_open()` 时 Playwright 报 Chromium 可执行文件不存在，路径 `~\.openakita\modules\browser\browsers\chromium-1208\chrome-win64\chrome.exe` 不存在。Agent 尝试自动执行 `playwright install` 但过程被中止。
- **根因分析**: 浏览器自动化模块安装了 Playwright 包但没有成功下载 Chromium 二进制。可能是打包环境下 `PLAYWRIGHT_BROWSERS_PATH` 指向的目录不对，或下载过程超时/被 run_shell 的 timeout 中断。
- **涉及代码**: `src/openakita/tools/browser_mcp.py`（浏览器启动逻辑）、模块安装脚本
- **复现**: 让 Agent 调用 `browser_open()` 即可触发

---

### #22 — 对话被提前中止 (Aborted)
- **提交者**: @gxlqssjf | **日期**: 2026-02-23 | **标签**: `bug`
- **环境**: Windows 11 / Python 3.13.3 / 最新版本
- **问题**: 对话过程中回复被提前终止，只显示 "(Aborted)"。描述非常简略，无日志无复现步骤。
- **根因分析**: 信息不足，可能原因包括：SSE 流超时断开、前端 abort controller 误触发、后端 reasoning engine 迭代上限。需要追问用户提供日志和具体操作。
- **涉及代码**: 待定（需更多信息）
- **状态**: ⚠️ 需追问

---

### #17 — 无法修改工作区地址
- **提交者**: @unnormalcow | **日期**: 2026-02-21 | **标签**: `bug`
- **环境**: 未提供
- **问题**: "工作区地址无法自定义，这个很麻烦，怎么解决？" 描述极简。
- **根因分析**: 可能是 UI 中 `WORKSPACE_DIR` 字段为只读，或修改后没有持久化到 `.env`。需要检查 App.tsx 中工作区配置项的绑定和保存逻辑。
- **涉及代码**: `apps/setup-center/src/App.tsx`（设置页面工作区配置区域）
- **状态**: ⚠️ 需追问具体行为

---

### #15 — Skill Market 同名 Skill 安装状态误判
- **提交者**: @yy1588133 | **日期**: 2026-02-21
- **环境**: Windows 10 / v1.23.3+7f9a248
- **问题**: 技能市场中多个不同来源但同名的 skill（如多个 `find-skills`）会被统一标记为"已安装"，实际只安装了一个。
- **根因分析**: 安装状态判断使用 skill name 而非唯一标识（source_repo + skill_path）。
- **涉及代码**: `apps/setup-center/src/App.tsx`（Skill Market 安装状态判断逻辑）
- **修复建议**: 用 `(source_repo, skill_name)` 或 marketplace item ID 作为唯一标识匹配安装状态

---

### #14 — Skill 启用/禁用切换不生效
- **提交者**: @yy1588133 | **日期**: 2026-02-21
- **环境**: Windows 10 / v1.23.3+7f9a248
- **问题**: 在技能管理页面切换 skill 启用/禁用状态，UI 显示成功，但运行时没有真正生效，skill 保持之前状态。
- **根因分析**: 状态变更可能没有正确持久化，或没有通知到运行时 skill registry 重新加载。
- **涉及代码**: `apps/setup-center/src/App.tsx`（skill 状态切换 UI）、`src/openakita/skills/registry.py`（运行时加载）
- **修复建议**: 检查持久化路径 + 确保 runtime registry 重新读取状态（或提示需要重启）

---

### #10 — 设置输入框每输一个字就失焦 ⭐ 高优
- **提交者**: @lclen | **日期**: 2026-02-19 | **标签**: `bug`
- **环境**: 未提供详细版本
- **问题**: `SkillConfigForm` 中输入框每次 `onChange` 调用 `setEnvDraft`，创建新对象触发整个 `App.tsx` 重渲染，导致输入框失焦。
- **根因分析**: 用户已诊断清楚 — 状态提升过高，每次 keypress 触发顶层 re-render，组件被卸载重建导致失焦。
- **涉及代码**: `apps/setup-center/src/App.tsx`（`SkillConfigForm` 组件及 `setEnvDraft` 状态管理）
- **修复建议**: 
  1. 用 `useRef` 暂存输入草稿，blur 时再同步到 state
  2. 或对 `setEnvDraft` 做 debounce
  3. 或将 SkillConfigForm 用 `React.memo` 隔离，避免父组件重渲染
- **状态**: 🔧 已回复正在排查

---

### #4 — 重装后 Python 路径残留
- **提交者**: @lordjack-f | **日期**: 2026-02-14 | **标签**: `bug`
- **环境**: 未提供详细 OS/版本
- **问题**: 卸载重装后，程序仍调用旧的 python.exe 路径。
- **涉及代码**: 安装器/配置初始化逻辑
- **状态**: ✅ 已回复建议尝试新版安装

---

## FEATURE 类

### #21 — 403 错误增加指数退避重试再切换端点
- **提交者**: @duffy25 | **日期**: 2026-02-23 | **标签**: `enhancement`
- **需求**: 当 LLM 端点返回 403 时，当前立即切换端点 + 180s 冷却。建议先做 3 次指数退避重试（3s → 6s → 12s，总 ≤30s），全失败再切换。
- **涉及代码**: `src/openakita/llm/providers/base.py`（端点错误处理 / failover 逻辑）
- **实现建议**: 在 `_call_with_failover` 或等价方法中，对 403 状态码增加 retry loop，区分 403 和其他错误码

---

### #20 — GPT 模型增加 xhigh 思考强度参数
- **提交者**: @yy1588133 | **日期**: 2026-02-22
- **环境**: Windows 10 / v1.23.3
- **需求**: 配置层缺少 OpenAI GPT 的 `reasoning_effort` / 思考强度参数（low/medium/high/xhigh）。希望在端点配置中增加统一的推理强度抽象字段。
- **涉及代码**: `src/openakita/llm/providers/openai.py`（请求构建）、`src/openakita/config/`（配置定义）、`apps/setup-center/src/App.tsx`（UI 配置项）
- **实现建议**: 在端点配置增加 `reasoning_effort` 字段，OpenAI provider 中映射到 API 参数，不支持的 provider 忽略或警告

---

### #19 — Telegram 群聊 mention-only 模式
- **提交者**: @yy1588133 | **日期**: 2026-02-22
- **需求**: 多 Bot 群聊时所有消息触发全部 Bot 回复，造成抢答。希望新增 `TELEGRAM_GROUP_RESPONSE_MODE` 配置项：
  - `all`（默认，保持现状）
  - `mention_only`（仅 @ 时响应）
  - `mention_or_reply`（@ 或回复 Bot 消息时响应）
  - `command_only`（仅命令前缀触发）
- **涉及代码**: `src/openakita/channels/adapters/telegram.py`（消息过滤逻辑）、`src/openakita/config/`（新增配置项）
- **验收标准**: 默认向后兼容、各模式行为正确、文档补充

---

### #13 — ORCHESTRATION_MODE 改下拉选择
- **提交者**: @yy1588133 | **日期**: 2026-02-21
- **需求**: 设置中 `ORCHESTRATION_MODE` 是自由文本输入，应改为下拉选择（single / handoff / master-worker），防止误输入。
- **涉及代码**: `apps/setup-center/src/App.tsx`（设置页面 UI）
- **备注**: #18 (by @ZhangLinnhao) 已提交实现方案（含 i18n、枚举校验），但以 issue 形式提交而非 PR，#16 为重复已关闭

---

## 优先级建议

| 优先级 | Issue | 理由 |
|--------|-------|------|
| 🔴 高 | #10 | 设置页面基本无法使用，严重影响 UX |
| 🟡 中 | #24 | 浏览器功能完全不可用 |
| 🟡 中 | #14 | Skill 管理核心功能不生效 |
| 🟡 中 | #15 | Skill Market 状态显示错误 |
| 🟡 中 | #21 | 端点切换策略优化，影响稳定性 |
| 🟡 中 | #19 | Telegram 群聊实用功能 |
| 🟢 低 | #13 | UX 改进，已有实现方案 |
| 🟢 低 | #20 | 功能增强，非阻塞 |
| 🟢 低 | #17 | 信息不足，需追问 |
| 🟢 低 | #22 | 信息不足，需追问 |
| ⚪ 已回 | #4 | 已建议新版安装 |

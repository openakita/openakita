# Plan Log

## 2026-02-17
- 任务: 为 openAKITA 创建初始项目文档，按 skill 结构输出，并落盘到 `Yongan/`。
- 决策:
  - 使用 `project-doc-guide` 作为文档骨架。
  - 受 `yongan-isolation` 约束，文档与技能索引放在 `Yongan/CLAUDE.md` 与 `Yongan/.claude/skills/`。
  - 初始阶段仅写稳定结构索引，不写高变实现细节。
- 产出:
  - `Yongan/CLAUDE.md`
  - `Yongan/.claude/skills/project-modules/SKILL.md`
  - `Yongan/.claude/skills/project-data/SKILL.md`
  - `Yongan/.claude/skills/project-pipelines/SKILL.md`

## 2026-02-18
- 任务: 实现 Yongan 账号化配置系统 v1.0（账号密码校验 + 一键覆盖应用 + 桌面入口）。
- 决策:
  - 账号目录固定为 `Yongan/users/`，采用本机账号库。
  - 密码仅用于校验，不用于配置加密；配置快照按明文保存。
  - 混合实现：核心逻辑放 `Yongan/src`，上游仅做最小 API/UI 接入。
- 产出:
  - `Yongan/src/yongan_accounts/__init__.py`
  - `Yongan/src/yongan_accounts/service.py`
  - `Yongan/scripts/init_first_account.py`
  - `Yongan/scripts/diagnose_accounts.py`
  - `Yongan/users/index.json`
  - `src/openakita/api/routes/yongan_accounts.py`
  - `src/openakita/api/schemas.py`
  - `src/openakita/api/server.py`
  - `apps/setup-center/src/App.tsx`
  - `.claude/rules/yongan-reproducible.md`
  - `Yongan/docx/00-总索引.md` 到 `Yongan/docx/07-上游改动清单与原因.md`

## 2026-02-18（规则澄清）
- 任务: 澄清 Codex 与 Claude 规则边界，避免误读 `chunked-writing.md`。
- 决策:
  - 新增 Codex 项目级边界规则，明确 `C:\Users\37445\.claude\rules\chunked-writing.md` 不作为 Codex 默认规则。
  - 在可复现规则中补充 `Yongan/docs/plan/plan.md` 追加要求。
  - 用独立文档沉淀“现状 + 目标 + 执行清单”表达规范。
- 产出:
  - `.claude/rules/yongan-codex-boundary.md`
  - `.claude/rules/yongan-reproducible.md`（新增 plan 记录要求）
  - `Yongan/docx/08-Codex执行说明与chunked-writing边界.md`
  - `Yongan/docx/00-总索引.md`（新增入口）

## 2026-02-18（品牌重塑）
- 任务: 将项目从 OpenAkita 重命名为 Open Agent Platform (OAP)，前后端版本统一重置为 1.0.0。
- 决策:
  - 仅改显示名称和版本号，不重命名 Python 包目录 `src/openakita/`（避免大规模 import 重构）。
  - 环境变量 `OPENAKITA_YONGAN_ROOT` → `OAP_YONGAN_ROOT`。
  - CLI 入口 `openakita` → `oap`。
- 产出（上游改动）:
  - `VERSION` → 1.0.0
  - `pyproject.toml` — name/version/authors/keywords/scripts
  - `apps/setup-center/package.json` — name/version
  - `apps/setup-center/src-tauri/Cargo.toml` — name/version/description/authors
  - `apps/setup-center/src-tauri/tauri.conf.json` — productName/version/identifier/title
  - `src/openakita/__init__.py` — docstring/author/metadata name
  - `src/openakita/main.py` — CLI name/help/welcome/version display
  - `src/openakita/api/server.py` — FastAPI title/description/service id/docstring
  - `src/openakita/api/routes/health.py` — service id
  - `src/openakita/tools/web.py` — User-Agent
  - `apps/setup-center/src/i18n/zh.json` — serviceDetectedDesc
  - `apps/setup-center/src/i18n/en.json` — serviceDetectedDesc
- 产出（Yongan 改动）:
  - `Yongan/src/yongan_accounts/service.py` — 环境变量名和错误提示

## 2026-02-18（Agent 协同规则）
- 任务: 检查 Claude 与 Codex 规则冲突，创建协同 skill。
- 产出:
  - `Yongan/.claude/skills/agent-coordination/SKILL.md`
  - `Yongan/.claude/skills/agent-coordination/references/protocol.md`

## 2026-02-18（Tauri Rust 告警修复）
- 任务: 修复 `apps/setup-center/src-tauri/src/main.rs` 的编译告警（unused variable / unreachable pattern / dead_code）。
- 决策:
  - 采用最小改动，避免行为变更，仅删除未使用字段/变量与不可达分支。
- 产出:
  - `apps/setup-center/src-tauri/src/main.rs`

## 2026-02-18（双风格 UI 规划）
- 任务: 为 Setup Center 制定“经典模式 + AIONUI 风格极简对话模式”的可执行改造计划，并支持双向切换。
- 决策:
  - 先做壳层级 `uiMode` 切换与样式作用域，不先动业务逻辑。
  - 极简模式主界面仅保留 Chat，其他能力通过抽屉/面板入口收纳。
- 产出:
  - `Yongan/docx/08-双风格UI改造计划-AIONUI极简模式.md`
  - `Yongan/docx/00-总索引.md`（新增文档入口）

## 2026-02-18（双风格 UI 阶段A实现）
- 任务: 落地 UI 双模式第一阶段（classic/minimal 切换 + 最小侵入极简壳层）。
- 决策:
  - 通过 `data-ui-mode` + CSS 作用域实现最小侵入，不拆业务组件。
  - `minimal` 模式自动聚焦 `chat` 视图，保持 ChatView 常驻挂载。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/styles.css`
  - `Yongan/docx/09-双风格UI阶段A实施说明.md`
  - `Yongan/docx/00-总索引.md`

## 2026-02-18（账号注册与管理系统）
- 任务: 默认 admin 账号、注册功能、admin 管理面板。
- 决策:
  - service.py 增加 role 字段（admin/user）、register_account、delete_account。
  - 首次初始化自动创建 admin/admin 账号（role=admin）。
  - apply_account 兼容无配置快照的注册账号（仅验证密码，不复制文件）。
  - LoginView 增加登录/注册模式切换，onSuccess 回传 role。
  - App.tsx 存储 role，admin 可见账号管理表格（查看/删除）。
- 产出（Yongan 改动）:
  - `Yongan/src/yongan_accounts/service.py` — register_account / delete_account / get_role / _ensure_admin
- 产出（上游改动）:
  - `src/openakita/api/schemas.py` — YonganRegisterRequest / YonganDeleteAccountRequest
  - `src/openakita/api/routes/yongan_accounts.py` — register / delete 路由 + login-apply 返回 role
  - `apps/setup-center/src/views/LoginView.tsx` — 注册模式 + role 回传
  - `apps/setup-center/src/views/LoginView.css` — mode-toggle 样式
  - `apps/setup-center/src/App.tsx` — yonganRole 状态 + admin 管理面板 + YonganAccountRow.role
  - `Yongan/docx/07-上游改动清单与原因.md`

## 2026-02-18（品牌重塑 Phase 2 + 登录界面 + 工作区路径可配置）
- 任务: 完成品牌重塑剩余项、创建登录界面、实现工作区路径可配置。
- 决策:
  - 前端 TS 类型字段 `openakitaRootDir` → `oapRootDir`，Rust struct 同步改名以匹配 serde camelCase 序列化。
  - Rust 用户可见字符串全部替换（托盘提示、通知、AUMID、日志、错误信息、User-Agent）。
  - 内部函数名和 Tauri command 名不改（跨文件引用过多，风险大于收益）。
  - 登录界面参考 AIONUI 项目复刻，glassmorphism 风格，支持记住账号。
  - 工作区路径解析链：`OAP_ROOT` env → `~/.oap-root` 文件 → `~/.openakita` 默认。
  - `yongan_accounts.py` 环境变量增加 `OAP_YONGAN_ROOT` 优先、`OPENAKITA_YONGAN_ROOT` 兼容回退。
- 产出（上游改动）:
  - `apps/setup-center/src/types.ts` — `oapRootDir`
  - `apps/setup-center/src/main.tsx` — `oap_app_ready` 事件名
  - `apps/setup-center/src/App.tsx` — 事件名/字段名/LoginView 集成
  - `apps/setup-center/src/views/ChatView.tsx` — `oap serve` 错误提示
  - `apps/setup-center/src-tauri/src/main.rs` — PlatformInfo 字段、resolve_oap_root()、set_oap_root 命令、用户可见字符串
  - `src/openakita/api/routes/yongan_accounts.py` — 环境变量兼容
- 产出（Yongan 改动）:
  - `apps/setup-center/src/views/LoginView.tsx`（新增）
  - `apps/setup-center/src/views/LoginView.css`（新增）

## 2026-02-18（账号系统 UI 美化）
- 任务: 在保留记住密码和默认测试策略的前提下，提升账号注册/管理界面的美观度与丝滑感。
- 决策:
  - 仅改视觉和交互，不修改账号逻辑与接口行为。
  - 账号管理区从内联样式改为 class 风格，统一卡片、表格、按钮视觉。
  - 登录页颜色和动效与主应用灰蓝主题对齐，并补移动端适配。
- 产出:
  - `apps/setup-center/src/views/LoginView.css`
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/styles.css`
  - `Yongan/docx/10-账号系统UI美化记录.md`
  - `Yongan/docx/00-总索引.md`

## 2026-02-18（交互稳定性优化）
- 任务: 排查并修复按钮与界面交互中的“跳变/不丝滑”问题。
- 决策:
  - 以纯 CSS 为主，不改业务逻辑与接口调用路径。
  - 聚焦过渡曲线统一、按压反馈、焦点可视化、低性能动画降级。
- 产出:
  - `apps/setup-center/src/styles.css`
  - `apps/setup-center/src/views/LoginView.css`
  - `Yongan/docx/10-账号系统UI美化记录.md`

## 2026-02-18（双风格 UI 阶段A优化）
- 任务: 优化 Codex 实现的 classic/minimal 双 UI 模式，消除 CSS 坏味道。
- 决策:
  - 移除 `.topbarModeBtn` 的 7 处 `!important`，改用 `.topbar .topbarModeBtn` 提升选择器优先级。
  - 删除冗余 `.appShellMinimal` class，统一用 `data-ui-mode="minimal"` 属性选择器控制所有极简模式样式。
  - 极简模式 sidebar 改为 `width:0 + opacity:0` 过渡隐藏（替代 `display:none`），实现平滑切换动画。
- 产出（上游改动）:
  - `apps/setup-center/src/styles.css` — CSS 重构
  - `apps/setup-center/src/App.tsx` — 移除 appShellMinimal class

## 2026-02-18（配置分层方案设计）
- 任务: 为配置流程设计“快速配置 / 中等配置 / 高级配置”三层方案，降低普通用户配置复杂度。
- 决策:
  - 中等配置聚焦业务可感知项：工作区、角色、LLM 端点、Skill、Agent、活人感、表情包。
  - Python 与工程参数下沉到高级配置；快速配置仅保留最小可用项。
  - 采用“先选模式，再动态步骤”的信息架构。
- 产出:
  - `Yongan/docx/11-配置分层方案-快速中等高级.md`
  - `Yongan/docx/00-总索引.md`

## 2026-02-18（配置分层实施 v1）
- 任务: 实施 `11-配置分层方案-快速中等高级.md`，完成三层模式结构落地。
- 决策:
  - 先落地模式与步骤流转，复用现有配置页面，避免一次性重写。
  - 配置模式统一为 `quick / medium / advanced`，原 `full` 并入 `advanced`。
  - 中等模式先用步骤映射打通：`workspace -> mid-persona -> mid-llm -> mid-skills -> finish`。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `Yongan/docx/12-配置分层实施说明-v1.md`
  - `Yongan/docx/00-总索引.md`
  - `Yongan/docx/07-上游改动清单与原因.md`

## 2026-02-18（状态页 Yongan 卡片移除）
- 任务: 移除状态页中的"Yongan 账号配置"卡片及其自动加载逻辑，消除 404 弹窗错误。
- 决策:
  - 状态页不再展示 Yongan 账号管理功能（登录/注册/管理已由 LoginView 承载）。
  - 删除自动加载 Yongan 账号和摘要的 useEffect，避免未启动后端时 404。
  - 清理因卡片移除而产生的死代码（6 个 state、4 个函数、1 个类型）。
- 产出（上游改动）:
  - `apps/setup-center/src/App.tsx` — 移除 Yongan 卡片 JSX、自动加载 effect、死代码

## 2026-02-18（版本不一致提示修复）
- 任务: 修复 Setup Center 持续提示“后端版本与桌面版本不一致（1.22.5 vs 1.0.0）”。
- 根因:
  - 本机 `~/.openakita/venv` 仍安装旧版 `openakita`（1.22.5），venv 回退启动路径会优先导入 site-packages。
  - `src/openakita/__init__.py` 在源码模式也无条件读取 `_bundled_version.txt`，该文件仍是 1.22.7，覆盖了 pyproject 1.0.0。
- 决策:
  - Tauri 在 venv 回退启动与版本探测时，若检测到仓库本地 `src/`，则优先注入 `PYTHONPATH` 指向本地源码。
  - Python 版本解析仅在 PyInstaller 打包运行（`sys.frozen`）时读取 `_bundled_version.txt`。
  - 仓库内 `_bundled_version.txt` 同步更新为 1.0.0。
- 产出:
  - `apps/setup-center/src-tauri/src/main.rs`
  - `src/openakita/__init__.py`
  - `src/openakita/_bundled_version.txt`

## 2026-02-18（配置流程审查与修复建议）
- 任务: 审查 Setup Center 配置分层流程，针对快速/中等/高级模式的可用性问题提出修复建议。
- 发现:
  - 模式卡片已是三模式，但欢迎页列布局为 auto-fit，宽屏下不保证固定三列。
  - 快速配置 quick-setup 仍无条件执行 embedded Python 安装、venv 创建、pip 安装，存在重复配置风险。
  - LLM 页面在非 advanced 模式仍显示 Prompt Compiler 与 STT 区块，与分层目标不一致。
  - quick 模式存在 quick-finish 独立步骤，不符合“配置完即看当前状态”的简化目标。
  - 已配置场景仍可直接进入模式选择，缺少“仅在需要重配时进入模式选择”的门控策略。
- 建议:
  - 固定模式卡片三列布局（窄屏回落单列）。
  - 为 quick-setup 增加幂等门控（已有 Python/venv/openakita 时跳过对应阶段）。
  - 将 compiler/stt 入口限制到 advanced（medium/quick 仅主 LLM）。
  - 删除 quick-finish 步骤，quick 变为两步（quick-form -> quick-setup），完成后直接跳状态页。
  - 增加 needsReconfigure 判定，已配置默认进状态总览，仅在用户触发“重新配置”时进入模式选择。

## 2026-02-18（配置流程问题修复落地）
- 任务: 按审查意见落地修复 Setup Center 配置流程（快速/中等/高级）。
- 实施:
  - 快速模式移除 `quick-finish`，保留两步：`quick-form -> quick-setup`；quick-setup 完成后直接跳转状态页。
  - quick-setup 增加幂等逻辑：已有可用 Python 跳过 embedded 安装；已有 venv 跳过创建；已安装 openakita 跳过 pip 安装。
  - LLM 页面将 Prompt Compiler 与 STT 区块/弹窗限制为 advanced 模式显示；quick/medium 仅保留主端点。
  - 欢迎页模式卡片布局改为固定三列（`modeGrid3`），在窄屏（<=980px）回落单列。
  - 增加“重新配置门控”：已存在基础配置时，配置入口默认跳状态页；仅在用户点击“重新配置”后进入模式选择。
- 验证:
  - `npm --prefix apps/setup-center run build` 通过（仅保留既有 chunk size 警告）。
- 变更文件:
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/styles.css`

## 2026-02-19（Plan-Docx 双向索引规则固化）
- 任务: 建立“plan 摘要 + docx 详文”的强制联动机制，并对 Codex/Claude 双端统一。
- 决策:
  - 将规则写入 Codex 与 Claude 的默认加载规则文件 `token-optimization-plan.md`，确保全局生效。
  - 在项目内新增规范文档，统一模板、验收标准和反向追踪要求。
- 产出:
  - `C:/Users/37445/.codex/rules/token-optimization-plan.md`
  - `C:/Users/37445/.claude/rules/token-optimization-plan.md`
  - `Yongan/docx/13-plan与docx双向索引规范.md`
  - `Yongan/docx/00-总索引.md`
- 详文: `Yongan/docx/13-plan与docx双向索引规范.md`

## 2026-02-19（人物模块验收修复：自定义角色反馈、AI优化、重配门控）
- 任务: 核对“14-人物模块与AI辅助角色生成”落地质量，修复自定义角色无反馈与重配门控误判问题，并补“一键AI优化角色文案”。
- 决策:
  - Persona 空列表增加引导文案，降低“点击无反馈”体感。
  - 已配置判定从仅看端点列表扩展为“端点列表或 API Key 任一成立”。
  - 在角色编辑对话框新增 `aiPolishPersona`，支持基于当前文本一键优化。
  - 保持 `custom-proxy` 默认模型为 `claude-sonnet-4-6`。
- 产出:
  - `apps/setup-center/src/views/PersonaView.tsx`
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/i18n/zh.json`
  - `apps/setup-center/src/i18n/en.json`
  - `Yongan/docx/15-人物模块验收与修复-自定义角色和重配门控.md`
- 详文: `Yongan/docx/15-人物模块验收与修复-自定义角色和重配门控.md`

## 2026-02-19（人物模块与 AI 辅助角色生成）
- 任务: 在 Setup Center 新增"人物"功能模块，含管理视图、聊天快捷切换、AI 辅助生成、配置摘要卡片。
- 决策:
  - 参考 AionUi Animated Segmented Control 实现聊天顶栏角色切换（胶囊 pill toggle）。
  - AI 辅助生成采用前端直连 LLM API（支持 OpenAI/Anthropic 双格式），不经后端中转。
  - 自定义角色对话框从 `renderAgentSystem()` 提升到顶层 return，使 PersonaView 和向导均可触发。
  - 新增 Custom Proxy 提供商作为默认 LLM 端点选项。
  - 已配置场景显示摘要卡片（端点数/角色/工作区）+ 重新配置按钮，替代直接进入模式选择。
- 产出（上游改动）:
  - `apps/setup-center/src/components/PersonaAvatar.tsx`（新增）
  - `apps/setup-center/src/views/PersonaView.tsx`（新增）
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/views/ChatView.tsx`
  - `apps/setup-center/src/styles.css`
  - `apps/setup-center/src/i18n/zh.json`
  - `apps/setup-center/src/i18n/en.json`
  - `src/openakita/llm/registries/providers.json`
- 详文: `Yongan/docx/14-人物模块与AI辅助角色生成.md`

## 2026-02-19（LLM 配置体验优化：显示自动拼接后的完整接口 URL）
- 任务: 在端点配置界面展示系统自动拼接后的完整接口地址，降低 `base_url` 误填成本。
- 决策:
  - 在“模型选择”区域下新增动态预览，明确展示 `/models` 与 `/chat/completions`（或 `/messages`）的最终地址。
  - 预览逻辑按 `api_type` 分支处理，和实际请求拼接规则保持一致。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `Yongan/docx/16-LLM基地址自动拼接预览.md`
- 详文: `Yongan/docx/16-LLM基地址自动拼接预览.md`

## 2026-02-19（移除版本不一致弹窗：品牌化统一显示）
- 任务: 彻底移除“后端服务版本与桌面终端版本不一致”弹窗，避免品牌化场景下反复提示。
- 决策:
  - 保留桌面版本读取与更新检测逻辑。
  - 删除版本不一致状态、检测函数、调用链路与弹窗渲染。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `Yongan/docx/17-移除版本不一致弹窗-品牌化处理.md`
- 详文: `Yongan/docx/17-移除版本不一致弹窗-品牌化处理.md`

## 2026-02-19（侧栏状态移除、重配历史回显修复、接口预览可点击）
- 任务: 简化导航并修复配置体验问题：移除“状态”侧栏项、恢复重新配置历史回显、接口预览改为蓝色可点击链接。
- 决策:
  - 删除主导航“状态”，避免与“配置”信息重复。
  - 已配置场景点击“配置”直接进入配置摘要页，不再跳状态页。
  - LLM 端点加载触发从 `llm` 扩展到 `mid-llm/quick-form`，确保重新配置时回显历史端点。
  - 模型/聊天接口预览使用 `<a>` 链接展示，保持与 Key 链接一致的可读性。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `Yongan/docx/18-侧栏状态移除与重配回显修复.md`
- 详文: `Yongan/docx/18-侧栏状态移除与重配回显修复.md`

## 2026-02-19（配置状态重配互跳修复 + 角色编辑弹窗排版优化）
- 任务: 修复“多次配置/重配后难以回到状态页”的流程问题，并优化角色编辑弹窗视觉布局。
- 决策:
  - 引入统一跳转函数，确保“配置 / 状态 / 重新配置”互跳稳定。
  - 在重配入口触发前强制刷新 `savedEndpoints`，提升历史端点回显稳定性。
  - 将角色弹窗 AI 区块和高级参数区改为一致的结构化样式类。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/styles.css`
  - `Yongan/docx/19-配置状态重配互跳与角色弹窗排版优化.md`
- 详文: `Yongan/docx/19-配置状态重配互跳与角色弹窗排版优化.md`

## 2026-02-19（人物体验增强：默认人物可编辑删除 + 头像上传裁剪 + 聊天气泡头像）
- 任务: 让默认人物支持编辑/删除，并补齐头像上传裁剪与聊天气泡头像显示。
- 决策:
  - 预设人物改为“可覆盖配置 + 软删除”机制，持久化到 `data/preset_personas.json`。
  - 人物头像采用本地裁剪后存储 `avatarUrl(base64)`，降低外部依赖和路径兼容问题。
  - 聊天气泡模式助手消息显示当前人物头像，人物切换条同时支持头像展示。
- 产出:
  - `apps/setup-center/src/types.ts`
  - `apps/setup-center/src/views/PersonaView.tsx`
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/views/ChatView.tsx`
  - `apps/setup-center/src/styles.css`
  - `Yongan/docx/20-默认人物可编辑删除与头像能力.md`
- 详文: `Yongan/docx/20-默认人物可编辑删除与头像能力.md`

## 2026-02-19（Thinking 显示优化 + 开关语义修复）
- 任务: 修复思维链中 `<think>` 原始标签显示丑陋问题，并统一 thinking 按钮与实际行为语义。
- 决策:
  - 对 `chain_text` 做 `<think>/<thinking>` 标签解析，标签内进入 thinking 区块，标签外作为普通链路文本。
  - 思维链 thinking/text 条目改为 Markdown 渲染，提升可读性。
  - 前端默认 thinking 模式改为 `off`，请求始终显式传 `thinking_mode`，且仅 `on` 时传 `thinking_depth`。
- 产出:
  - `apps/setup-center/src/views/ChatView.tsx`
  - `apps/setup-center/src/styles.css`
  - `Yongan/docx/21-thinking显示优化与开关语义修复.md`
- 详文: `Yongan/docx/21-thinking显示优化与开关语义修复.md`

## 2026-02-19（预设人物恢复入口）
- 任务: 在人物页增加“恢复默认人物”按钮，支持一键恢复已删除的预设人物。
- 决策:
  - 仅在存在删除记录时展示恢复按钮，避免界面噪音。
  - 恢复逻辑清空 `data/preset_personas.json` 的 `deleted` 列表，保留 `overrides`。
- 产出:
  - `apps/setup-center/src/views/PersonaView.tsx`
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/styles.css`
  - `Yongan/docx/22-预设人物恢复入口.md`
- 详文: `Yongan/docx/22-预设人物恢复入口.md`

## 2026-02-19（AIONUI 风格对齐：前端优化执行计划文档）
- 任务: 输出一份可直接给 Claude 执行的详细前端优化计划，后续由 Codex 做回归复核。
- 决策:
  - 采用 P0/P1/P2 三阶段推进：先壳层 token，再主交互，再页面级精修。
  - 为 Claude 增加阶段交付规范、自测清单、可访问性底线。
  - 为 Codex 增加问题导向复核规范（Findings 优先）。
- 产出:
  - `Yongan/docx/23-AIONUI风格对齐前端优化执行计划.md`
- 详文: `Yongan/docx/23-AIONUI风格对齐前端优化执行计划.md`

## 2026-02-19（AIONUI 风格对齐：精简版执行计划）
- 任务: 对 Codex 初版计划做现状评估后精简，砍除重复劳动项，聚焦高影响改动。
- 决策:
  - 砍除 P0 全部（Token/按钮/间距已有完整体系）、P2.2 配置页精修、P2.3 状态反馈。
  - 精简为两阶段：Phase A（聊天区降噪 + hover 渐显）、Phase B（弹窗动画 + 角色卡片精修）。
  - 聊天顶栏压缩到 40px，model/thinking/mode 收进下拉面板。
  - 消息操作按钮改为 hover 渐显（opacity 0→1）。
  - 弹窗动画统一为 translateY + opacity 组合，0.2s ease-out。
  - 不引入新依赖，不改后端，不做深色模式。
- 产出:
  - `Yongan/docx/23-AIONUI风格对齐前端优化执行计划.md`（覆盖 Codex 初版）
- 详文: `Yongan/docx/23-AIONUI风格对齐前端优化执行计划.md`

## 2026-02-19（Setup Center 品牌 Logo 替换）
- 任务: 将 Setup Center 左上角与引导页使用的旧 logo 替换为 `Yongan/docs/archive/My logo.jpeg` 指定品牌图。
- 决策:
  - 复用现有前端资源入口 `apps/setup-center/src/assets/logo.png`，不改组件引用路径，降低改动面。
  - 先对源图做自动去留白裁剪再导出 PNG，确保 32px 侧栏小图标可辨识。
- 产出:
  - `apps/setup-center/src/assets/logo.png`

## 2026-02-19（通道级端点与角色配置）
- 任务: 每个 IM 通道可独立选择已配置的 LLM 端点和角色，复用现有单 Agent 架构。
- 决策:
  - 新增 `data/channel_profiles.json` 存储 per-channel 端点/角色偏好。
  - 端点 override 优先级：`/switch 命令` > `channel_overrides` > `全局 priority`。
  - 角色 override 通过参数透传（`preset_override`），不修改 PersonaManager 全局状态。
  - 未配置的通道自动回退全局默认，零破坏性变更。
  - 评估了"通道作为监督者"方案，因复杂度过高（2000+ 行重写）且与现有 RalphLoop 重复，暂不实施。
- 产出:
  - `src/openakita/channels/channel_profiles.py`（ChannelProfileManager）
  - `src/openakita/llm/client.py`（_channel_overrides）
  - `src/openakita/core/persona.py`（preset_override 参数）
  - `src/openakita/prompt/builder.py`（透传 preset_override）
  - `src/openakita/core/prompt_assembler.py`（透传 preset_override）
  - `src/openakita/core/agent.py`（通道级 profile 注入）
  - `src/openakita/api/routes/config.py`（channel-profiles API）
  - `apps/setup-center/src/App.tsx`（通道配置 UI）
- 详文: `Yongan/docx/24-通道级端点与角色配置.md`

## 2026-02-19（邮件系统接入可行性调研）
- 任务: 评估 openAKITA / OAP 快速接入邮件系统的可行路径与难度。
- 结论:
  - 基于现有 `ChannelAdapter` 架构，新增 `email` 适配器具备可实施性。
  - 快速路线建议优先采用 SMTP 发信 + IMAP/Webhook 收信，不建议短期自建完整邮件服务器。
- 产出:
  - 官方方案对比（SMTP / Gmail API / Microsoft Graph / SES / n8n）与难度评估（问答输出）。

## 2026-02-19（AIONUI 风格对齐 Phase A 实施：聊天区降噪）
- 任务: 按精简版执行计划落地 Phase A，聚焦聊天界面降噪与丝滑感提升。
- 实施:
  - 聊天顶栏压缩到 40px，chain/mode 开关收进齿轮图标下拉菜单，顶栏默认只保留 sidebar toggle + persona + 设置 + 新对话。
  - 消息时间戳改为 hover 渐显（默认 opacity:0，hover 时 0.25），flat 和 bubble 模式统一。
  - 新增 `--bg-subtle` / `--bg-inset` 两个背景层次变量，thinking block 和 tool result 用背景色差替代边框。
  - 弹窗动画从 `scale(0.98) + translateY(12px)` 改为 `translateY(8px)`，曲线统一为 `cubic-bezier(0.4, 0, 0.2, 1)`。
  - 输入区已有折叠 + badge 化，无需改动。
- 验证: `npm run build` 通过。
- 产出:
  - `apps/setup-center/src/views/ChatView.tsx`
  - `apps/setup-center/src/styles.css`
  - `apps/setup-center/src/i18n/zh.json`
  - `apps/setup-center/src/i18n/en.json`
- 详文: `Yongan/docx/23-AIONUI风格对齐前端优化执行计划.md`

## 2026-02-19（AIONUI 风格对齐 Phase A 补充 + Phase B 实施）
- 任务: Phase A 补充（顶栏状态移除、打字机 placeholder、等待计时器）+ Phase B（角色卡片精修、aria-label）。
- 实施:
  - 移除主顶栏左侧状态行（workspace/运行状态/端点数），改为空白占位。
  - 输入框 placeholder 改为打字机动画（40ms/字 + 光标闪烁）。
  - 等待 AI 回复时三点动画改用品牌色，新增 0.1s 精度计时器。
  - 角色卡片头像 36→44px，hover 改为 bg 微变，活跃态改为左侧 3px 品牌色竖条。
  - 角色编辑/删除按钮改为 hover 才显示，补齐 aria-label。
- 验证: `npm run build` 通过。
- 产出:
  - `apps/setup-center/src/App.tsx`、`ChatView.tsx`、`PersonaView.tsx`、`styles.css`
- 详文: `Yongan/docx/23-AIONUI风格对齐前端优化执行计划.md`

## 2026-02-19（Outlook 邮箱大师 MVP 计划优化：学校邮箱兼容与 Agent 直连）
- 任务: 优化邮箱系统实施计划，纳入学校邮箱受限场景与 Agent 直连数据诉求。
- 决策:
  - 计划移除时间限制，改为阶段化推进。
  - 邮箱接入采用双路径：`IMAP/SMTP(应用密码)` + `Microsoft OAuth(Graph)`，学校邮箱优先走 OAuth。
  - Agent 直接复用本地 `mail/service.py` 读取与发送，不通过 MCP 链路。
- 产出:
  - `Yongan/docx/25-Outlook邮箱大师MVP实施计划.md`
- 详文: `Yongan/docx/25-Outlook邮箱大师MVP实施计划.md`

## 2026-02-19（Chat 流式动画与等待指示修复）
- 任务: 修复聊天界面未出现“助手逐字流式显示”以及“等待三点+计时器不持续显示”的问题。
- 根因:
  - 打字机动画仅作用于输入框 placeholder，未作用于助手正文渲染。
  - 等待指示器条件为 `msg.streaming && !msg.content`，首段内容到达后即隐藏。
- 决策:
  - 新增 `StreamedMarkdown` 组件，按字符渐进渲染流式正文。
  - 将等待指示器调整为 streaming 全程显示（保留 0.1s 计时）。
- 产出:
  - `apps/setup-center/src/views/ChatView.tsx`
  - `Yongan/docx/26-Chat流式动画与等待指示修复.md`
  - `Yongan/docx/00-总索引.md`
- 详文: `Yongan/docx/26-Chat流式动画与等待指示修复.md`

## 2026-02-19（通道页补齐通道级端点与角色配置）
- 任务: 修复“通道级端点与角色配置”仅在配置向导可见、左侧导航“通道”页不可配置的问题。
- 根因:
  - 通道级配置 UI 放在 `renderIM()`（向导页）而非 `IMView`（左侧导航实际页面）。
  - `IMView` 原本为只读消息查看器，未接入 `channelProfiles` 保存链路。
- 决策:
  - 在 `IMView` 每个通道卡片新增端点/角色下拉，支持直接保存通道 profile。
  - `App.tsx` 向 `IMView` 透传 `channelProfiles`、端点列表、人物列表与 `saveChannelProfiles`。
- 产出:
  - `apps/setup-center/src/views/IMView.tsx`
  - `apps/setup-center/src/App.tsx`
  - `Yongan/docx/27-通道页补齐通道级端点与角色配置.md`
  - `Yongan/docx/00-总索引.md`
- 详文: `Yongan/docx/27-通道页补齐通道级端点与角色配置.md`

## 2026-02-19（聊天主界面布局重构：角色中置与文件预览窗）
- 任务: 修复聊天主界面交互混乱问题，统一主面板切换栏，并将文件预览从左列内嵌改为独立中间预览窗。
- 决策:
  - 角色选择从 Chat 顶栏迁移到 `panelToggleBar` 中间。
  - 文件树仅负责选择文件，新增独立预览窗（支持 Markdown 渲染，可开关）。
  - 移除 Chat 顶栏“断开/刷新”按钮，降低噪音。
  - 文件浏览器文案接入中英文切换。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/components/FileExplorerTree.tsx`
  - `apps/setup-center/src/styles.css`
  - `apps/setup-center/src/i18n/zh.json`
  - `apps/setup-center/src/i18n/en.json`
  - `Yongan/docx/28-聊天主界面布局重构-角色中置与文件预览窗.md`
  - `Yongan/docx/00-总索引.md`
- 详文: `Yongan/docx/28-聊天主界面布局重构-角色中置与文件预览窗.md`

## 2026-02-19（布局重构：三栏布局 + 文件浏览器 + 顶栏按钮整合）
- 任务: 重构 Setup Center 布局为三栏结构（文件浏览器 | 主区域 | 导航侧栏），初始状态仅显示对话界面。
- 决策:
  - 左侧新增 VSCode 风格文件浏览器，通过 Rust `workspace_list_dir` 命令读取工作区目录。
  - 原左侧导航栏移至右侧，初始隐藏，通过聊天顶栏按钮切换。
  - 顶栏操作按钮（断开/连接/刷新/语言/模式）移入 ChatView 顶栏，通过 `topBarExtra` prop 注入。
  - CSS grid 使用 `order` 属性控制三栏排列，避免大规模 DOM 移动。
  - 初始状态两侧面板均隐藏（`0fr`），仅显示聊天主区域。
- 产出（上游改动）:
  - `apps/setup-center/src-tauri/src/main.rs` — 新增 `workspace_list_dir` 命令
  - `apps/setup-center/src/components/FileExplorerTree.tsx`（新增）
  - `apps/setup-center/src/views/ChatView.tsx` — `topBarExtra` prop
  - `apps/setup-center/src/App.tsx` — 三栏布局、面板状态、按钮整合
  - `apps/setup-center/src/styles.css` — 三栏 grid、文件浏览器样式
- 详文: `Yongan/docx/28-三栏布局与文件浏览器.md`

## 2026-02-19（三栏布局修复：默认视图 + 持久面板切换栏）
- 任务: 修复三栏布局两个问题——登录后默认进入配置页而非对话页；面板切换按钮仅在 ChatView 内可见。
- 决策:
  - 默认视图从 `"wizard"` 改为 `"chat"`，登录后直接进入对话界面。
  - 新增 `.panelToggleBar` 持久工具栏，放在 `<main>` 顶部，所有视图均可见。
  - 文件浏览器和导航面板的切换按钮从 ChatView `topBarExtra` 移至持久栏。
  - `.main` grid-template-rows 从 `1fr` 改为 `auto 1fr` 以容纳持久栏。
- 产出:
  - `apps/setup-center/src/App.tsx` — panelToggleBar、默认视图修改
  - `apps/setup-center/src/styles.css` — `.panelToggleBar` 样式、grid 修复
- 详文: `Yongan/docx/28-三栏布局与文件浏览器.md`

## 2026-02-19（聊天页回归修复：登录视图/Yongan404/角色样式/状态卡片）
- 任务: 修复回归问题：登录后仍非聊天、Yongan 404 弹错、角色选择样式不符合预期、状态四卡布局空位。
- 决策:
  - 登录成功与跳过登录后统一 `setView("chat")`。
  - Yongan 账号接口 404 视为“功能未挂载”场景，静默降级。
  - 中央角色选择改为胶囊按钮风格（非下拉）。
  - 状态卡片容器改为 `statusGrid2` 固定 2x2。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `Yongan/docx/28-聊天主界面布局重构-角色中置与文件预览窗.md`（补充修复章节）
- 详文: `Yongan/docx/28-聊天主界面布局重构-角色中置与文件预览窗.md`

## 2026-02-19（聊天布局交互精修：动画/预览/角色胶囊）
- 任务: 修复布局重构后的交互细节——动画不丝滑、文件预览挤压聊天区、角色胶囊 hover 互斥。
- 决策:
  - 按钮/面板过渡时间从 0.5-0.6s 降至 0.15-0.25s，统一 cubic-bezier 曲线。
  - `.chatWorkspace` grid 列默认 `1fr`，仅预览打开时切换为 `auto 1fr`；`.contentChat` 加 `min-width:0` 防 grid 溢出。
  - 文件预览区启用双向滚动条（`scrollbar-color` + `overflow-x: auto`）。
  - 角色胶囊 max-width 扩至 560px，CSS hover 委托实现"仅悬停项展开"。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/styles.css`
  - `Yongan/docx/28-聊天主界面布局重构-角色中置与文件预览窗.md`（交互精修章节）
- 详文: `Yongan/docx/28-聊天主界面布局重构-角色中置与文件预览窗.md`

## 2026-02-19（UI精修：角色仅聊天可见/通道排版/响应式/图标态胶囊）
- 任务: 修复四项 UI 问题——角色选择非聊天页可见、通道页排版混乱、缺少响应式、角色胶囊非图标默认态。
- 决策:
  - `.panelPersonaCenter` 按 `view === "chat"` 条件显隐。
  - 通道页 channel item 从 flex 单行改为 block 布局，端点/角色下拉改为 2 列 grid + 专用 CSS 类。
  - 980px 断点下通道 profile grid 回落单列，personaView 缩减 padding。
  - 角色胶囊恢复 `max-width:0` 默认态，仅 hover 展开标签，`will-change` GPU 加速。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/views/IMView.tsx`
  - `apps/setup-center/src/styles.css`
- 详文: `Yongan/docx/28-聊天主界面布局重构-角色中置与文件预览窗.md`

## 2026-02-19（管理员预配置与共享配置：混合模式）
- 任务: 管理员一次配好 LLM/Python/语音等复杂配置，普通用户登录即用，仅保留人物/通道个性化能力。
- 决策:
  - 配置分两层：共享层（`.env`、`llm_endpoints.json`、`skills.json`）由 admin 管理；个人层（`preset_personas.json`、`channel_profiles.json`）用户自管。
  - 共享配置存储于 `Yongan/shared_config/`，admin 通过「发布为共享配置」按钮写入。
  - 非 admin 登录时：有共享配置 → 混合模式（共享层 + 个人层）；无共享配置 → 全量快照降级。
  - 非 admin 侧栏隐藏「配置向导」「模块管理」入口。
- 产出:
  - `Yongan/src/yongan_accounts/service.py` — 账号 CRUD + 混合 apply
  - `Yongan/src/yongan_accounts/shared_config.py` — 发布/查询/应用共享配置
  - `src/openakita/api/routes/yongan_accounts.py` — 新增 publish/info 端点
  - `src/openakita/api/schemas.py` — 新增 YonganPublishSharedConfigRequest
  - `apps/setup-center/src/App.tsx` — 角色门控 + 发布按钮 + 同步提示
  - `apps/setup-center/src/views/LoginView.tsx` — onSuccess 传递 sharedConfigApplied
  - `apps/setup-center/src/i18n/zh.json` + `en.json` — 新增 3 个 key
- 详文: `Yongan/docx/29-管理员预配置与共享配置.md`

## 2026-02-19（批量添加模型 + 聊天模型按厂商分组）
- 任务: 配置页新增「全部添加」按钮批量添加拉取到的模型；聊天页模型选择器按厂商分组显示。
- 决策:
  - 批量添加复用当前 provider/apiType/baseUrl/apiKeyEnv 状态，自动生成端点名并去重。
  - 聊天页模型菜单按 endpoint.provider 字段分组，组标题不可点击。
- 产出:
  - `apps/setup-center/src/App.tsx` — doBatchAddAllModels() + 按钮
  - `apps/setup-center/src/views/ChatView.tsx` — 模型菜单按 provider 分组
  - `apps/setup-center/src/i18n/zh.json` + `en.json` — 新增 2 个 key
- 详文: `Yongan/docx/07-上游改动清单与原因.md`（条目 38-40）

## 2026-02-19（桌面一行悬浮会话窗口 MVP 实施计划）
- 任务: 为“单会话 + 一行悬浮条 + 发送后同宽结果窗”输出可执行实施计划。
- 决策:
  - 范围收敛为 Windows 优先、单会话、不做吸附与多窗口编排。
  - 基于现有 `Tauri + React` 实现双浮窗：`float_bar`（输入）+ `float_result`（结果）。
  - 复用现有 Chat 流式协议与模型端点数据，不新增后端 HTTP 接口。
- 产出:
  - `Yongan/docx/31-桌面一行悬浮会话.md`
  - `Yongan/docx/00-总索引.md`
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（桌面一行悬浮会话窗口：20 分钟快速落地实现）
- 任务: 按“单会话、无吸附、可置顶/透明度”的约束，快速落地可用版本并保留扩展架构。
- 决策:
  - 先复用主窗口承载悬浮视图，避免一次性引入多窗口复杂度。
  - 把置顶/透明度与偏好持久化下沉到 Rust 命令层，确保未来切独立窗口时前端调用不变。
  - 流式先实现 `text_delta/error/done` 最小子集，保证稳定可用。
- 产出:
  - `apps/setup-center/src/views/FloatingChatView.tsx`（新增）
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/types.ts`
  - `apps/setup-center/src/i18n/zh.json`
  - `apps/setup-center/src/i18n/en.json`
  - `apps/setup-center/src-tauri/src/main.rs`
  - `Yongan/docx/31-桌面一行悬浮会话.md`（补实施记录）
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（UI精修第四轮：角色移入聊天栏/按钮精简/通道下拉溢出/技能分类）
- 任务: 四项 UI 调整——角色选择从 panelToggleBar 移入 ChatView 顶栏右端、删除+按钮与设置按钮及冗余代码、IM 通道下拉宽屏文字截断、技能页分类筛选与保存按钮。
- 决策:
  - 角色 segmented 从 `panelToggleBar` 移至 `chatTopBar` 右端，无需再按 view 条件隐藏。
  - 删除 ChatView 中 `+` 按钮、设置下拉及 `chatSettingsOpen`/`chatSettingsRef` 相关代码。
  - `.imChannelProfileGrid` 改为 `minmax(0,1fr) minmax(0,1fr)` + `.imProfileField` 加 `min-width:0` + `.imProfileSelect` 加 `text-overflow:ellipsis`。
  - 技能页新增 all/system/external 筛选按钮，reload 按钮改为"保存"。
- 产出:
  - `apps/setup-center/src/views/ChatView.tsx`
  - `apps/setup-center/src/views/SkillManager.tsx`
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/styles.css`
  - `apps/setup-center/src/i18n/zh.json` + `en.json`
- 详文: `Yongan/docx/28-聊天主界面布局重构-角色中置与文件预览窗.md`

## 2026-02-19（三形态模式落地：传统/正常/极简 + 极简保留模式按钮）
- 任务: 将 UI 模式从二态改为三态，并定义稳定术语；“正常”对应历史 minimal，“极简”改为一行悬浮输入形态。
- 决策:
  - 在 `App.tsx` 注释中固化术语约定：`traditional / normal / minimal`。
  - 模式按钮改为三态循环切换，并在极简状态继续保留可见与可用。
  - `uiMode=minimal` 强制进入 `floating` 视图，`compact=true` 渲染一行悬浮形态。
  - 原 `data-ui-mode="minimal"` 样式语义迁移为 `normal`，`minimal` 用于一行悬浮壳层。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/views/FloatingChatView.tsx`
  - `apps/setup-center/src/styles.css`
  - `apps/setup-center/src/i18n/zh.json`
  - `apps/setup-center/src/i18n/en.json`
  - `Yongan/docx/31-桌面一行悬浮会话.md`（新增三形态约定与现状说明）
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（Tauri 透明度 API 兼容修复）
- 任务: 修复 `tauri dev` 编译错误：`WebviewWindow` 不存在 `set_opacity` 方法（E0599）。
- 决策:
  - Rust 保留 `set_window_opacity` 命令接口以维持前后端契约，但在当前版本降级为 no-op。
  - 前端 `FloatingChatView` 使用 CSS `opacity` 落地透明度效果，保证功能可用。
  - 在 docx 记录“命令接口 + 前端渲染”的兼容策略，便于后续切回原生实现。
- 产出:
  - `apps/setup-center/src-tauri/src/main.rs`
  - `apps/setup-center/src/views/FloatingChatView.tsx`
  - `Yongan/docx/31-桌面一行悬浮会话.md`
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（人物管理：自适应网格 + 预设还原修复）
- 任务: 预设角色卡片网格去除 max-width 限制实现自适应列数；修复"恢复默认"仅还原已删除预设而不重置已编辑覆写的问题。
- 决策:
  - `.personaViewRoot` 移除 `max-width: 800px`，grid `auto-fill` 自然随窗口宽度增减列数。
  - `canRestorePresets` 条件扩展为包含 overrides 非空判断。
  - `restorePresetPersonas()` 同时清空 overrides 和 deletedIds，调用 `savePresetPersonaConfig({}, [])`。
- 产出:
  - `apps/setup-center/src/styles.css`
  - `apps/setup-center/src/App.tsx`
- 详文: `Yongan/docx/28-聊天主界面布局重构-角色中置与文件预览窗.md`

## 2026-02-19（极简浮窗收口：移除悬浮聊入口 + 窗口级悬浮切换）
- 任务: 去掉右侧“悬浮聊”按钮，仅保留“极简”作为入口；修复极简仅是页面样式而非悬浮窗口的问题。
- 决策:
  - 移除侧栏 `floating` 独立导航项，统一通过三态模式按钮进入极简。
  - 新增 Rust 命令 `set_minimal_floating_mode`，在 `uiMode=minimal` 时把主窗口切到无边框/置顶/小窗/上方居中，退出时恢复原状态。
  - 保持 `traditional / normal / minimal` 术语注释不变，继续作为后续需求沟通约定。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src-tauri/src/main.rs`
  - `Yongan/docx/31-桌面一行悬浮会话.md`
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（MCP 市场设计与骨架实现）
- 任务: 设计并实现 MCP 市场 MVP，让用户提供算法脚本即可自动生成 MCP 服务。
- 决策:
  - 核心逻辑放 `Yongan/src/mcp_market/`（registry + generator + validator），遵循隔离规则。
  - 上游最小改动：新增路由 + 前端视图 + i18n。
  - 生成管线基于 AST 解析，安全校验禁止危险调用。
- 产出:
  - `Yongan/src/mcp_market/{__init__,registry,generator,validator}.py`
  - `src/openakita/api/routes/mcp_market.py`
  - `apps/setup-center/src/views/McpMarketView.tsx`
- 详文: `Yongan/docx/30-MCP市场设计方案.md`

## 2026-02-19（极简悬浮窗体验优化：单行布局 + 仅水平拉伸 + 默认传统模式）
- 任务: 修复极简悬浮窗三个问题——拖动条+输入分两行太高、窗口可上下拉伸、启动默认进极简。
- 决策:
  - 合并拖动条与输入行为单行：卡片背景可拖动，控件全在一行。
  - 通过 `setMinSize` + `setMaxSize` 锁定高度，禁止用户垂直拉伸。
  - 启动固定为 `traditional` 模式，不再从 localStorage 恢复 `minimal`。
  - 模式切换按钮改为 `IconMenu` 图标，置顶按钮改为 `IconPin` 图标。
  - Rust 初始高度从 112 降至 64，匹配单行布局。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/views/FloatingChatView.tsx`
  - `apps/setup-center/src/styles.css`
  - `apps/setup-center/src/icons.tsx`
  - `apps/setup-center/src-tauri/src/main.rs`
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（极简纯对话窗修复：左右栏联动 + 悬浮窗交互）
- 任务: 修复“传统模式不自动打开左右栏”和“极简仍显示主界面按钮”的问题，并补齐悬浮窗基础交互。
- 决策:
  - `traditional / normal` 与左右栏状态建立双向联动：传统自动双开；关闭任一栏降级正常；正常双开自动升级传统。
  - `minimal` 改为独立渲染的纯对话壳层，只保留对话相关能力与模式切换按钮。
  - `FloatingChatView` 增加窗口交互：拖动、左右拉伸、最小化、置顶切换、发送后自动展开结果窗高度。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/views/FloatingChatView.tsx`
  - `apps/setup-center/src/styles.css`
  - `apps/setup-center/src/i18n/zh.json`
  - `apps/setup-center/src/i18n/en.json`
  - `Yongan/docx/31-桌面一行悬浮会话.md`
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（文档编号整理：30 归 MCP，31 归极简悬浮窗）
- 任务: 消除“30 号与 31 号专题串号”歧义，固定编号归属，避免 Claude/Codex 协作冲突。
- 决策:
  - `30-*` 仅承载 MCP 市场与 MCP 相关主题；
  - `31-*` 仅承载桌面极简悬浮窗口主题；
  - 在总索引和两份主题文档顶部显式写明编号归属。
- 产出:
  - `Yongan/docx/00-总索引.md`
  - `Yongan/docx/30-MCP市场设计方案.md`
  - `Yongan/docx/31-桌面一行悬浮会话.md`
- 详文:
  - `Yongan/docx/30-MCP市场设计方案.md`
  - `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（MCP 市场 UI 增强：文件上传与前端美化）
- 任务: MCP 市场生成页支持拖拽/点击上传 .py 文件，替代纯粘贴；全面美化前端，统一使用项目 CSS 设计系统。
- 决策:
  - 新增 drag & drop 上传区 + hidden file input，上传后自动填充代码和服务名。
  - 移除所有暗色主题内联样式，改用 CSS 变量（`--panel2`, `--line`, `--muted`, `--brand` 等）和 CSS 类。
  - Tab 切换改为下划线风格（`.mcpTab`），服务列表/表单/按钮统一使用项目已有 class。
- 产出:
  - `apps/setup-center/src/views/McpMarketView.tsx`（重写）
  - `apps/setup-center/src/styles.css`（新增 `.mcp*` 系列 CSS）
  - `apps/setup-center/src/i18n/{zh,en}.json`（新增 `dropText`, `dropHint`, `onlyPy`）
- 详文: `Yongan/docx/30-MCP市场设计方案.md`

## 2026-02-19（极简浮窗修复：白蓝对齐/拖拽恢复/透明度按钮）
- 任务: 修复极简悬浮窗白色对话窗与蓝色底框不对齐、底部留白异常、无法拖动的问题，并新增透明度调节按钮。
- 决策:
  - 去除极简容器横向内边距与重复间距来源（`gap` + `.card + .card`），统一卡片贴边与间距。
  - 悬浮窗高度改为状态化精简值（基础/折叠结果/展开结果），并与透明度面板联动，避免底部大空白。
  - 增加独立拖拽手柄（`data-tauri-drag-region` + `startDragging`）提升拖动稳定性。
  - 新增“透明度”按钮与滑杆，实时更新并持久化 `floating_ui.opacity`。
  - 删除 `FloatingChatView` 未使用 `endpoints` 传参，清理冗余代码。
- 产出:
  - `apps/setup-center/src/views/FloatingChatView.tsx`
  - `apps/setup-center/src/styles.css`
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src-tauri/src/main.rs`
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（极简浮窗修复二次校正：整窗透明度/滚动条占位/固定高度）
- 任务: 针对首轮修复后仍存在的4个问题做二次校正——拖动失败、右侧未贴齐、透明度仅作用白窗、出现上下拖动条。
- 决策:
  - 极简模式直接移除蓝色底背景，避免白窗与底层错位感；透明度作用于极简根容器整体。
  - 极简容器统一 `overflow:hidden` 与居中布局，消除高度溢出造成的右侧滚动条占位。
  - 拖拽手柄从按钮改为纯拖拽区块，并采用“原生 `startDragging` 失败则手动位移拖动”双路径兜底。
  - Rust `set_minimal_floating_mode` 下将窗口设置为 `resizable=false`，确保极简窗口高度固定、无上下缩放条。
- 产出:
  - `apps/setup-center/src/App.tsx`
  - `apps/setup-center/src/views/FloatingChatView.tsx`
  - `apps/setup-center/src/styles.css`
  - `apps/setup-center/src-tauri/src/main.rs`
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（极简浮窗视觉回调：恢复蓝底 + 白窗宽度放大）
- 任务: 按反馈恢复极简模式蓝色底背景，并将白色对话窗宽度提升到约 1.5 倍。
- 决策:
  - 保留极简外层框，恢复背景蓝色渐变，不再隐藏背景层。
  - 悬浮窗默认宽度从 `860` 调整为 `1290`（约 1.5 倍），使白窗横向更贴近外层框。
  - 透明度继续作用整窗口层（`body.opacity`），保持背景与白窗同步透明。
- 产出:
  - `apps/setup-center/src/views/FloatingChatView.tsx`
  - `apps/setup-center/src/styles.css`
  - `apps/setup-center/src-tauri/src/main.rs`
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（极简宽度修正：白窗显式宽度与外框对齐）
- 任务: 修复“只放大蓝底外框、白色对话窗未同步放大”的问题。
- 决策:
  - 白色对话窗容器从纯 `100%` 自适应改为显式目标宽度 `1290px`（并 `max-width:100%` 防溢出）。
  - 保留窗口目标宽度 `1290`，使白窗与外框按同一基准对齐。
- 产出:
  - `apps/setup-center/src/views/FloatingChatView.tsx`
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（极简白窗宽度根因修复：根容器拉伸 + 拖拽稳定化）
- 任务: 修复“窗口已放大但白色对话窗视觉未同步变宽”，并解决拖拽光标变化但窗口不移动的问题。
- 决策:
  - 根因定位：`minimalChatOnlyShell` 为 flex 容器时，`floatingRoot` 未显式拉伸，导致 `width: 100%` 的白窗仅跟随收缩后的父容器宽度。
  - 极简态给 `floatingRoot` 增加 `width: 100%`，让白窗宽度直接绑定窗口真实宽度。
  - 拖拽主路径改为手动 `setPosition` 位移，不再依赖 `startDragging`，避免环境差异导致“可拖拽光标但不移动”。
  - 同步修正 `@tauri-apps/api/window` 监视器 API 用法（`currentMonitor` 使用模块函数），消除 TS 编译错误。
- 产出:
  - `apps/setup-center/src/views/FloatingChatView.tsx`
- 验证:
  - `npm --prefix apps/setup-center run build` 通过（仅保留既有 chunk warning）。
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

## 2026-02-19（极简对话条宽度缩放 0.7 + 防误改注释）
- 任务: 将当前极简对话条宽度整体缩小为 0.7 倍，并补充关键注释防止后续再次改错。
- 决策:
  - 以既有宽度基准 `1290` 统一按 `0.7` 缩放，目标宽度固定为 `903`。
  - 前端与 Rust 宽度常量同步，避免“只改一侧导致白窗/外框体感不一致”。
  - 在三处增加防踩坑注释：父容器宽度绑定、拖拽主路径稳定性、前后端宽度联动。
- 产出:
  - `apps/setup-center/src/views/FloatingChatView.tsx`
  - `apps/setup-center/src-tauri/src/main.rs`
- 详文: `Yongan/docx/31-桌面一行悬浮会话.md`

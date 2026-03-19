# OpenAkita 功能特点总览

> **版本**: v1.25.0 | **提交数**: 758+ | **协议**: Apache License 2.0

---

## 一、产品定位

OpenAkita 是一款**全能自进化 AI 助手**，集成桌面应用、IM 通道、CLI 三端交互，支持多 Agent 协作、计划模式、记忆系统、技能市场、桌面/浏览器自动化，以及运行时监督与自我进化能力。定位为**真正解决问题**的 AI 助手，而非简单对话工具。

---

## 二、核心架构特点

### 1. ReAct 显式推理引擎

- **Reason → Act → Observe** 三阶段显式推理循环，每一步决策可追踪、可回溯
- **检查点 & 回滚 (Checkpoint / Rollback)**：在关键工具调用节点自动保存快照，工具连续失败 ≥3 次或整批失败时自动回滚到上一检查点，附加失败经验引导 LLM 换策略
- **循环检测**：基于工具签名的重复执行检测、LLM 自检间隔、极端安全阈值等多重防护
- **双模式执行**：同步 `run()` + 异步流式 `reason_stream()`，桌面端实时流式输出

### 2. 多 Agent 协作系统

- **AgentOrchestrator 协调中心**：统一的多 Agent 消息路由、任务委派、健康监控
- **委派机制**：支持 `delegate_to_agent`（单对单）、`delegate_parallel`（并行委派多个 Agent）、`spawn_agent`（创建临时 Agent）
- **AgentInstancePool 实例池**：per-session × per-profile 的 Agent 实例管理，30 分钟空闲自动回收
- **委派深度控制**：最大委派深度 5 层，防止递归委派死循环
- **故障切换**：FallbackResolver 在 Agent 失败时自动切换到备用 profile
- **进度感知超时**：基于任务实际进展的动态超时策略，无进展时终止
- **子 Agent 状态追踪**：前端 Dashboard 实时展示子 Agent 运行状态
- **跨 Agent 追踪链路**：委派事件日志化，支持跨 Agent 的完整调用链路回溯

### 3. 计划模式 (Plan Mode)

- **结构化任务计划**：通过 `create_todo` 工具将复杂任务拆解为可执行步骤
- **动态注入**：活跃 Plan 自动注入系统提示词，确保 LLM 始终感知当前进度
- **步骤状态管理**：每个步骤独立跟踪（pending / in_progress / completed / cancelled）
- **上下文压缩保护**：压缩上下文后自动重新注入 Plan 状态，防止计划信息丢失
- **自动关闭**：任务结束时自动完结未关闭的 Plan
- **前端进度条**：输入框上方浮动 Plan 进度条，支持折叠/展开

### 4. Agent Harness 运行时监督框架

- **RuntimeSupervisor 运行时监督器**：
  - 检测工具抖动 (tool thrashing)、编辑抖动 (edit thrashing)
  - 推理死循环检测、Token 异常检测、极端迭代检测
  - 分级干预策略：提示 → 强制切换策略 → 暂停任务
- **ResourceBudget 资源预算**：Token / 成本 / 时长 / 迭代次数 / 工具调用 五维度任务级限制
- **PolicyEngine 策略引擎**：基于 `POLICIES.yaml` 的工具权限管理，Shell 命令黑名单、路径限制、确认机制
- **DeterministicValidator 确定性验证器**：Plan 完成度、文件产物、工具成功率等非 LLM 验证
- **FailureAnalysis 失败分析管线**：任务失败后自动根因分析，输出改进建议

---

## 三、记忆系统

### v2 多层记忆架构

- **三层注入**：Scratchpad（工作记忆）+ Core Memory（核心记忆）+ Dynamic Memories（动态检索）
- **统一存储**：SQLite + 向量数据库双引擎，FTS5 全文检索 + ChromaDB 语义检索
- **7 种记忆类型**：FACT / PREFERENCE / SKILL / ERROR / RULE / PERSONA_TRAIT / EXPERIENCE
- **4 级优先级**：TRANSIENT / SHORT_TERM / LONG_TERM / PERMANENT
- **AI 驱动提取**：会话结束时双轨提取——用户画像 + 任务经验
- **多路召回 + 重排序**：语义搜索 + 情节搜索 + 时间搜索 + 附件搜索，综合 relevance × recency × importance × access_freq 排序
- **LLM 查询拆解**：自然语言自动转化为精准搜索关键词
- **LLM 记忆审查**：AI 驱动的记忆清理，替代简单关键词过滤
- **分层记忆关联网络**：三层双向链接，支持跨层导航
- **Token 预算控制**：记忆注入量自适应当前上下文窗口

---

## 四、身份 & 人格系统

### 四层身份架构

| 层级 | 文件 | 作用 |
|------|------|------|
| 灵魂层 | `SOUL.md` | 核心价值观、安全准则、Ralph Wiggum 哲学 |
| 行为层 | `AGENT.md` | 工作模式、工具优先级、输出规范 |
| 策略层 | `POLICIES.yaml` | 工具权限、路径限制、Shell 黑名单 |
| 人格层 | `personas/*.md` | 8 种预设人格，可实时切换 |

### 8 种预设人格

| 人格 | 风格 |
|------|------|
| Default | 平衡通用 |
| Tech Expert | 技术专家，简洁精准 |
| Boyfriend | 暖男，关心体贴 |
| Girlfriend | 甜美，温柔陪伴 |
| Jarvis | 钢铁侠式管家，优雅高效 |
| Butler | 英式管家，礼貌周到 |
| Business | 商务顾问，专业正式 |
| Family | 家庭助手，亲切日常 |

### Ralph Wiggum 模式（永不放弃哲学）

- **铁律一**：工具优先——有工具必用，没工具就造（写脚本 / 搜索安装 / AI 生成技能）
- **铁律二**：问题优先自己解决——报错就分析，缺路径就搜索，缺数据就上网查
- **铁律三**：坚持但有策略——失败换方法，再失败再换，直到成功

---

## 五、工具 & 技能系统

### 89+ 内置工具（16 大类）

| 类别 | 主要工具 | 能力 |
|------|---------|------|
| **计划** | create_todo, update_todo_step, complete_todo | 结构化任务计划 |
| **多 Agent** | delegate_to_agent, delegate_parallel, spawn_agent, create_agent | Agent 委派与创建 |
| **浏览器** | browser_task, browser_open, browser_navigate, browser_screenshot, view_image | Playwright 驱动的网页自动化 |
| **文件系统** | run_shell, write_file, read_file, list_directory | 文件操作 + Shell 执行 |
| **记忆** | add_memory, search_memory, consolidate_memories, trace_memory, list_recent_tasks | 记忆 CRUD + 追溯 |
| **网络搜索** | web_search, news_search | 实时网页/新闻搜索 |
| **定时任务** | schedule_task, list_scheduled_tasks, trigger_scheduled_task | Cron 定时任务 |
| **桌面自动化** | desktop_screenshot, desktop_click, desktop_type, desktop_hotkey, desktop_scroll, desktop_window | Windows UIA + Vision 桌面操控 |
| **IM 通道** | deliver_artifacts, get_voice_file, get_image_file, get_chat_history | 跨平台消息交互 |
| **系统** | ask_user, enable_thinking, generate_image, get_workspace_map, set_task_timeout | 系统控制 |
| **配置** | system_config | 运行时配置管理 |
| **用户画像** | update_user_profile, get_user_profile | 用户信息管理 |
| **人格** | switch_persona, update_persona_trait, toggle_proactive | 人格切换 |
| **技能** | list_skills, install_skill, run_skill_script, manage_skill_enabled | 技能市场 |
| **MCP** | call_mcp_tool, add_mcp_server, connect_mcp_server 等 10 个 | MCP 协议工具 |
| **表情包** | send_sticker | 表情包发送 |

### 技能市场

- **在线市场**：连接 skills.sh 技能仓库，搜索 + 一键安装
- **GitHub 安装**：支持 `github:user/repo` 格式直接安装
- **AI 生成技能**：LLM 驱动的技能自动生成（skill-creator）
- **热重载**：技能安装后立即生效，无需重启
- **渐进式披露**：Level 1 清单概览 → Level 2 详情加载 → Level 3 按需执行

### MCP 集成

- 完整的 MCP (Model Context Protocol) 支持
- 动态添加/移除/连接 MCP 服务器
- MCP 工具自动注入到系统提示
- 支持合并项目本地 + Cursor MCP 配置

---

## 六、LLM 多模型支持

### 30+ 服务商预置

| 类别 | 服务商 |
|------|--------|
| **本地** | Ollama、LM Studio |
| **国际** | Anthropic、OpenAI、Google Gemini、xAI (Grok)、Mistral AI、OpenRouter、NVIDIA NIM、Groq、Together AI、Fireworks AI、Cohere |
| **中国区** | 阿里云 DashScope、Kimi（月之暗面）、MiniMax、DeepSeek、硅基流动 SiliconFlow、火山引擎、智谱 AI、百度千帆、腾讯混元、云雾 API、美团 LongCat、心流 iFlow |

### 7 大能力维度

- **text**：基础文本生成
- **vision**：图片理解（GPT-4o, Claude, Gemini, Qwen-VL 等）
- **video**：视频理解（Kimi, Qwen-VL, Gemini）
- **tools**：工具调用 / Function Calling
- **thinking**：深度思考模式（o1, DeepSeek-R1, QwQ, MiniMax M2 等）
- **audio**：音频理解（GPT-4o-audio, Qwen-Audio, Gemini）
- **pdf**：PDF 原生输入（Claude, Gemini）

### 智能故障切换

- 按能力自动选择可用端点
- 端点冷静期 + 渐进退避
- 全局故障检测：多端点同时失败时缩短冷静期
- 端点亲和性：工具调用链中优先复用同一端点
- Thinking 软降级：无 thinking 端点时自动关闭思考模式

### Coding Plan 专用端点

- 支持独立配置 Coding Plan 端点，用于代码生成任务的编排
- 适配 DashScope、Kimi、MiniMax、火山引擎、智谱等专用编码模型

---

## 七、IM 通道全覆盖

### 6 大平台

| 平台 | 接入方式 | 特色 |
|------|----------|------|
| **Telegram** | Webhook / Long Polling | 配对验证、Markdown、代理支持 |
| **飞书** | WebSocket / Webhook | 卡片消息、事件订阅 |
| **企业微信** | 智能机器人回调 | 流式回复、response_url 主动推送 |
| **钉钉** | Stream 模式 WebSocket | 无需公网 IP |
| **OneBot** | WebSocket 正向连接 | 兼容 NapCat / Lagrange / go-cqhttp |
| **QQ 官方** | WebSocket / Webhook | 群聊、单聊、频道 |

### 全媒体支持

- 文本、图片、语音、视频、文件、表情包
- 语音自动转文字（Whisper 本地识别 + 在线 ASR）
- 视频智能处理：大视频提取关键帧，小视频 base64 直传
- 文件交付：AI 生成的文件直接推送到聊天

### 群聊智能策略

- `always`：始终回复
- `mention_only`：仅被 @ 时回复（默认）
- `smart`：智能判断 + 限流

### IM 专属能力

- 实时思维链推送：逐步推送推理过程到 IM
- 中断控制：`/cancel`、`/stop`、`/skip` 指令
- 模型切换：`/model`、`/switch` 实时切换
- 终极重启：IM 通道直接触发后端服务重启

---

## 八、桌面应用 (Tauri 2.x)

### 技术栈

Tauri 2.x + React + TypeScript + Vite，跨平台（Windows / macOS / Linux）

### 功能面板

| 面板 | 功能 |
|------|------|
| **Chat** | 桌面聊天、流式输出、文件拖拽上传、图片灯箱预览 |
| **Agent Dashboard** | 神经网络可视化仪表盘、子 Agent 状态追踪、工具卫星节点 |
| **Agent Manager** | 多 Agent 创建与管理 |
| **IM Channels** | 6 大平台配置与管理 |
| **Skills** | 技能市场搜索、安装、启用/禁用 |
| **MCP** | MCP 服务器管理 |
| **Memory** | 记忆管理面板，LLM 审查清理 |
| **Scheduler** | 定时任务创建、查看、管理 |
| **Token Stats** | Token 用量统计面板 |
| **Config** | LLM 端点、系统配置、高级设置 |
| **Feedback** | Bug 反馈 + 需求建议 |

### 桌面特色

- **深色/浅色主题**：完整支持系统级主题跟随
- **自动更新**：Tauri updater 在线更新
- **Onboarding 向导**：首次使用引导式配置
- **右键菜单**：复制、粘贴、操作
- **AI 自动会话标题**：LLM 自动生成会话名称
- **开机自启**：支持系统启动时自动运行

---

## 九、自我进化能力

### 失败分析管线

- 任务失败后自动分析根因：上下文丢失 / 工具限制 / 计划缺陷 / 循环检测 / 预算耗尽
- 识别 Harness 缺口：缺少工具 / 文档不足 / 缺少防护
- 输出结构化改进建议

### 每日自检 (SelfCheck)

- 每日凌晨 4:00 自动执行
- 分析错误日志、识别错误模式
- 区分核心组件 vs 工具错误
- 支持自动修复 (`selfcheck_autofix`)
- 修复后自测验证 + 生成报告推送到 IM

### 技能自动生成

- LLM 驱动：描述需求 → 自动生成 SKILL.md + 脚本 + 参考文档
- 缺能力自动获取：搜索 GitHub → 安装技能 → 或 AI 现场生成

### 缺陷自动安装

- 检测到缺失依赖时自动 `pip install` / `npm install`
- 国内镜像优先策略

---

## 十、活人感 (Proactive) 系统

- **主动消息类型**：问候 / 任务跟进 / 记忆回忆 / 闲聊 / 晚安
- **智能频率控制**：安静时段、最大每日消息数、最小间隔
- **反馈自适应**：根据用户 positive / negative / ignored 反馈动态调整频率和闲置阈值
- **闲置检测**：默认 3 小时无互动触发主动关怀

---

## 十一、上下文工程

### 智能上下文管理

- **中英文感知 Token 估算**：中文约 1.5 字符/token，英文约 4 字符/token
- **消息分组保护**：确保 tool_calls / tool_result 始终成对，不被拆散
- **大工具结果独立压缩**：>5000 tokens 的工具结果单独 LLM 摘要
- **分块递归压缩**：30000 tokens 分块 → LLM 摘要 → 递归减少
- **硬截断兜底**：最后保底丢弃消息，同时提取记忆保存
- **压缩后重写**：压缩后自动重新注入 Plan 状态 + 工作记忆 + 关键决策

### 两阶段提示架构

- **编译管线 v2**：身份信息 → 系统环境 → 环境快照 → 技能清单 → MCP 清单 → 记忆上下文 → 工具列表 → 核心原则 → 用户画像
- **环境快照 (EnvironmentSnapshot)**：动态注入工作区文件树、活跃 Plan、资源预算、错误状态、工作记忆

---

## 十二、深度思考模式

- **用户可控**：thinking_mode = auto / always / never
- **深度设置**：支持调节思考深度
- **思维链叙事流**：实时展示 LLM 推理过程
- **IM 实时推送**：思维链通过 Gateway 逐步推送到 IM 通道
- **多模型适配**：DeepSeek-R1 reasoning_content、MiniMax M2.1 Interleaved Thinking、OpenAI o1 等

---

## 十三、可观测性 & 追踪

### AgentTracer 追踪系统

- **12 种 Span 类型**：LLM / TOOL / TOOL_BATCH / MEMORY / CONTEXT / REASONING / PROMPT / TASK / DECISION / VERIFICATION / SUPERVISION / DELEGATION
- **ReAct 推理链保存**：每次推理过程完整记录
- **日志导出**：`data/traces/YYYY-MM-DD/` 目录，含每日摘要
- **Token 全链路统计**：每次 LLM 调用的 input/output tokens 追踪，统计面板可视化

### 运行时状态

- **心跳机制**：后端服务心跳 + 进程生命周期同步
- **会话崩溃恢复**：异常退出后自动恢复会话上下文

---

## 十四、零门槛安装 & 部署

### 全流程图形化配置（vs 同类开源产品的核心差异）

- **零命令行上手**：从安装到使用，全程不需要打开终端、不需要敲命令、不需要改配置文件
- **Onboarding 引导向导**：首次启动自动弹出步骤化配置向导，引导完成 LLM 端点、IM 通道、模块安装等全部配置
- **全可视化管理**：模型端点、IM 通道、技能市场、MCP 服务器、定时任务、记忆管理——全部在桌面 GUI 中点击完成
- **5 分钟上手**：下载安装包 → 双击安装 → 跟着向导走 → 开始对话

### 打包 & 发布

- **跨平台构建**：Windows (x64) + macOS (Apple Silicon + Intel) + Linux (x64 + ARM64, deb)
- **CI/CD**：GitHub Actions 自动构建 + 发布
- **CDN 加速**：Cloudflare R2 CDN 加速下载
- **macOS 代码签名 + 公证**：完整的 Apple Notarization 流程
- **NSIS 安装器**：Windows 原生安装体验
- **自动更新**：Tauri updater + 多稳定分支版本保护

### Python 环境（用户无感）

- **隔离运行时**：项目自有 Python 环境，与用户系统环境完全隔离，不污染用户电脑
- **自动检测与修复**：Windows Store Python 误判修复、Conda SSL 修复
- **中国镜像优先**：pip / HuggingFace 自动切换国内镜像，国内用户无网络障碍
- **模块化安装**：核心打包 + 可选模块按需安装（向量记忆、浏览器自动化、IM 适配器等）
- **依赖自修复**：缺失依赖自动检测安装，用户无需手动处理

### API 服务

- **FastAPI + Uvicorn**：RESTful API + SSE 流式推送
- **SSE 心跳保活**：工具执行期间心跳防超时
- **开发模式**：`openakita serve --dev` 热加载

---

## 十五、安全 & 治理

- **POLICIES.yaml 策略管理**：工具级权限控制、Shell 命令黑名单、路径黑名单
- **确认机制**：高危操作（如 `run_shell`）需用户确认
- **硬编码安全底线**：永不提供武器制造说明、永不生成 CSAM 等
- **人类监督优先**：支持人类调整、纠正或关闭 AI 系统
- **意图声明系统**：LLM 执行前声明意图，防止幻觉操作
- **ForceToolCall 防幻觉**：强制 API tool_choice=required，防止 LLM 虚构工具执行结果

---

## 十六、中断 & 交互控制

- **统一中断控制**：cooperative cancellation + skip + insert
- **消息排队 UI**：流式回复期间的消息排队与立即发送
- **步骤跳过**：Plan 执行中可跳过当前步骤
- **实时中断**：工具调用间隙插入新消息
- **优先级机制**：中断消息优先级分级

---

## 十七、反馈系统

- **Bug 报告**：结构化错误报告 + LLM 调试日志自动收集
- **需求建议**：用户需求提交
- **离线保存**：云端上传失败时本地保存并提供下载
- **Turnstile 防滥用**：Cloudflare Turnstile 集成

---

## 项目亮点一句话总结

| 特点 | 一句话 |
|------|--------|
| 🚀 零门槛上手 | 全图形化配置，5 分钟安装到对话，零命令行零代码 |
| 🤝 多 Agent | 委派、并行、池化、故障切换、深度控制 |
| 🧠 ReAct 推理 | 显式三阶段推理循环，支持检查点回滚 |
| 📋 计划模式 | 结构化任务拆解 + 动态进度追踪 |
| 💾 记忆系统 | v2 三层记忆 + 多路召回 + AI 提取 |
| 🎭 人格系统 | 8 种人格实时切换 + 活人感引擎 |
| 🔧 89+ 工具 | 16 大类 + 技能市场 + MCP 扩展 |
| 🌐 30+ LLM | 覆盖国内外主流模型 + 智能故障切换 |
| 💬 6 大 IM | Telegram / 飞书 / 企微 / 钉钉 / OneBot / QQ |
| 🖥️ 桌面应用 | Tauri 2.x 跨平台 + 神经网络仪表盘 |
| 🛡️ 运行时监督 | 抖动检测 + 资源预算 + 策略引擎 |
| 🧬 自我进化 | 失败分析 + 自检修复 + 技能生成 |
| 🔍 深度思考 | 可控 thinking + 思维链实时展示 |
| 📊 可观测性 | 12 种 Span + Token 全链路统计 |
| 🔒 安全治理 | 策略引擎 + 意图声明 + 人类监督优先 |

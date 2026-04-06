# Cursor IDE 底层代码结构与提示词构造过程深度分析

> 基于 Cursor Agent 模式运行时实际接收到的完整系统提示词逆向分析  
> 分析时间：2026-03-29  
> 底层模型：claude-4.6-opus-high-thinking

---

## 目录

- [一、整体架构概览](#一整体架构概览)
- [二、系统提示词的分层构造](#二系统提示词的分层构造)
  - [第 1 层：基础角色定义](#第-1-层基础角色定义)
  - [第 2 层：工具定义](#第-2-层工具定义function-definitions)
  - [第 3 层：行为规则](#第-3-层行为规则behavioral-rules)
  - [第 4 层：上下文注入](#第-4-层上下文注入context-injection)
  - [第 5 层：规则系统](#第-5-层规则系统rules-system)
  - [第 6 层：技能系统](#第-6-层技能系统skills-system)
  - [第 7 层：MCP 集成层](#第-7-层mcp-集成层)
  - [第 8 层：模式选择](#第-8-层模式选择mode-selection)
- [三、提示词组装流程](#三提示词组装流程)
- [四、子代理架构](#四子代理sub-agent架构)
- [五、核心设计模式](#五核心设计模式分析)
- [六、与 OpenAkita 提示词系统的对比](#六与-openakita-提示词系统的对比)
- [七、关键技术细节](#七关键技术细节)
- [八、完整工具清单与参数](#八完整工具清单与参数)
- [九、安全机制](#九安全机制)
- [十、总结](#十总结)

---

## 一、整体架构概览

Cursor 的 AI 助手是一个**多层级、模块化的 Agent 系统**。它不是简单地把用户消息发给 LLM，而是在客户端完成了大量的上下文采集、规则注入、工具绑定和提示词装配工作。

```
┌───────────────────────────────────────────────────────────┐
│                    用户消息 (User Query)                    │
├───────────────────────────────────────────────────────────┤
│               上下文自动采集层 (Context Injection)           │
│  ┌───────────┬───────────┬───────────┬──────────────────┐  │
│  │  打开文件  │  Git状态   │  终端状态  │ Linter错误/光标  │  │
│  └───────────┴───────────┴───────────┴──────────────────┘  │
├───────────────────────────────────────────────────────────┤
│             系统提示词装配层 (System Prompt Assembly)        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 基础角色 → 工具定义 → 规则注入 → 技能目录 →          │   │
│  │ MCP配置 → 模式选择 → 工作区规则 → 用户规则            │   │
│  └─────────────────────────────────────────────────────┘   │
├───────────────────────────────────────────────────────────┤
│               工具执行层 (Tool Execution Layer)              │
│  ┌──────┬───────┬───────┬──────┬──────┬───────┬────────┐  │
│  │Shell │ Read  │ Write │ Grep │ Glob │ Task  │MCP Tool│  │
│  └──────┴───────┴───────┴──────┴──────┴───────┴────────┘  │
├───────────────────────────────────────────────────────────┤
│                子代理层 (Sub-Agent Layer)                    │
│  ┌────────────┬─────────┬───────┬────────────┬──────────┐  │
│  │generalPurpose│explore│ shell │browser-use │best-of-n │  │
│  └────────────┴─────────┴───────┴────────────┴──────────┘  │
└───────────────────────────────────────────────────────────┘
```

---

## 二、系统提示词的分层构造

Cursor 的系统提示词采用**分段拼装**机制，按严格的顺序依次注入 8 个层级。以下逐层解析。

### 第 1 层：基础角色定义

系统提示词开头是简洁的角色声明：

```
"You are an AI coding assistant, powered by claude-4.6-opus-high-thinking."
"You operate in Cursor."
"You are a coding agent in the Cursor IDE that helps the USER with software engineering tasks."
```

关键信息：
- 声明底层模型名称及能力变体（如 `high-thinking` 表示启用了扩展推理链）
- 声明运行环境为 Cursor IDE
- 定义基本角色为 coding agent
- 提及会自动附加用户当前状态信息（打开文件、光标位置、最近查看文件、编辑历史、linter 错误等）

### 第 2 层：工具定义（Function Definitions）

以标准 JSON Schema（OpenAI Function Calling 格式）定义所有可用工具。当前版本包含 **15+ 个核心工具**：

| 工具名 | 功能 | 类型 |
|--------|------|------|
| `Shell` | 终端命令执行（git、npm、docker 等） | 系统操作 |
| `Read` | 读取文件，支持图片（jpeg/png/gif/webp）和 PDF | 文件操作 |
| `Write` | 写入/覆盖文件 | 文件操作 |
| `StrReplace` | 精确字符串替换（唯一匹配 + 可选全局替换） | 文件编辑 |
| `Delete` | 删除文件 | 文件操作 |
| `Glob` | 文件名模式匹配搜索（按修改时间排序） | 搜索 |
| `Grep` | 基于 ripgrep 的正则内容搜索 | 搜索 |
| `SemanticSearch` | 语义搜索（按含义而非文本匹配找代码） | 搜索 |
| `ReadLints` | 读取工作区 Linter 诊断信息 | 代码质量 |
| `Task` | 启动子代理（5 种类型，支持并行） | 多代理 |
| `EditNotebook` | 编辑 Jupyter Notebook 单元格 | 文件编辑 |
| `TodoWrite` | 结构化任务清单管理 | 任务管理 |
| `SwitchMode` | 切换交互模式（Agent/Plan/Debug/Ask） | 模式控制 |
| `CallMcpTool` | 调用 MCP 服务器的任意工具 | 外部扩展 |
| `FetchMcpResource` | 读取 MCP 资源（可下载到本地） | 外部扩展 |
| `GenerateImage` | 文本描述生成图片 | 创作 |
| `AskQuestion` | 向用户提结构化多选问题 | 交互 |
| `WebSearch` | 实时网络搜索 | 信息获取 |
| `WebFetch` | 抓取 URL 内容转 markdown | 信息获取 |

每个工具的描述（description）中**内嵌了大量使用规范**，起到"工具级提示词"的作用。例如 `Shell` 工具的描述中包含：

- **Git 安全协议**：禁止 force push、禁止修改 git config、禁止 `--no-verify`
- **Commit 规范**：必须用 HEREDOC 格式传递 commit message
- **PR 创建流程**：完整的 `gh pr create` 工作流
- **后台命令管理**：`block_until_ms` 超时机制、终端文件轮询策略

### 第 3 层：行为规则（Behavioral Rules）

这是最复杂也最核心的一层，包含多个并行的规则子系统：

#### 3a. 系统通信规则 (`<system-communication>`)

- 系统可能附加额外上下文（`<system_reminder>`、`<attached_files>`、`<task_notification>`）
- Agent 应遵从这些附加信息但不向用户直接提及

#### 3b. 语调与风格 (`<tone_and_style>`)

```
- 禁止主动使用 emoji（除非用户明确要求）
- 所有交流通过纯文本，工具仅用于执行任务
- 禁止用 Shell 命令或代码注释与用户交流
- 永远优先编辑现有文件，而非创建新文件
- 禁止主动创建文档文件（*.md / README）
- 冒号后不能直接跟工具调用（用句号结尾）
```

#### 3c. 工具调用规则 (`<tool_calling>`)

```
- 不向用户提及工具名称，用自然语言描述正在做什么
- 优先使用专用工具而非终端命令（Read 而非 cat，StrReplace 而非 sed）
- 独立调用可并行（同一消息内多个工具调用），依赖调用必须串行
- 检查所有必需参数是否完整，缺失时向用户询问
- 用户提供的具体值必须原样使用
```

#### 3d. 代码修改规则 (`<making_code_changes>`)

```
- 编辑前必须先用 Read 工具读取文件（至少一次）
- 从零创建时必须包含依赖管理文件（如 requirements.txt）和 README
- Web 应用必须有美观现代的 UI
- 禁止生成超长哈希或非文本代码（如二进制）
- 引入的 Linter 错误必须修复
- 禁止添加叙述性注释（"Import the module"、"Define the function" 等）
```

#### 3e. 代码注释禁令 (`<no_thinking_in_code_or_commands>`)

```
- 禁止在代码注释或 Shell 注释中作为思考草稿
- 注释只用于记录非显而易见的逻辑或 API
- 解释应放在响应文本中，而非行内注释
```

#### 3f. Linter 错误处理 (`<linter_errors>`)

```
- 实质性编辑后使用 ReadLints 检查
- 修复自己引入的错误
- 仅在必要时修复已有 lint 错误
```

#### 3g. 代码引用格式 (`<citing_code>`)

Cursor 定义了两种严格的代码引用格式：

**方法 1：引用已有代码（CODE REFERENCES）**
```
```startLine:endLine:filepath
// 代码内容
```（三个反引号闭合）
```

**方法 2：展示新代码（MARKDOWN CODE BLOCKS）**
```
```language
// 新代码
```（三个反引号闭合）
```

格式规则：
- 两种格式禁止混用
- 代码引用必须有 startLine 和 endLine
- 三反引号禁止缩进
- 三反引号前必须有空行
- 代码块内禁止添加行号前缀

#### 3h. 行内行号处理 (`<inline_line_numbers>`)

```
工具返回的代码可能包含 "LINE_NUMBER|LINE_CONTENT" 格式的行号前缀
Agent 必须将 LINE_NUMBER| 视为元数据，不作为代码的一部分
```

#### 3i. 终端文件信息 (`<terminal_files_information>`)

```
- 终端输出存储为 terminals/<id>.txt 文件
- 每个文件前 ~10 行为元数据（pid, cwd, last_command, exit_code）
- 可通过 Read 工具读取完整终端输出
- 不向用户提及 terminals 文件夹
```

#### 3j. 任务管理 (`<task_management>`)

```
- 复杂任务（3+ 步骤）自动使用 TodoWrite
- 维护 4 种状态：pending / in_progress / completed / cancelled
- 同一时间只有 1 个任务处于 in_progress
- 完成所有 todo 后才结束回合
```

### 第 4 层：上下文注入（Context Injection）

每次用户发送消息时，Cursor 客户端**自动采集并附加**以下结构化上下文：

```xml
<user_info>
  OS Version: win32 10.0.26200
  Shell: bash
  Workspace Path: d:\coder\myagent
  Is directory a git repo: Yes
  Today's date: Sunday Mar 29, 2026
  Terminals folder: C:\Users\...\terminals
</user_info>

<git_status>
  完整的 git status 快照（分支、跟踪状态、未跟踪文件列表）
  注：这是对话开始时的快照，对话中不会自动更新
</git_status>

<open_and_recently_viewed_files>
  当前打开的文件（文件路径 + 总行数）
  最近查看的文件列表（按时间倒序）
</open_and_recently_viewed_files>

<agent_transcripts>
  历史对话记录的存储路径
  引用格式：[<标题>](<uuid>)
</agent_transcripts>
```

这种设计让 Agent 无需主动查询就能了解：
- 用户的操作系统和 Shell 环境
- 项目的 Git 状态
- 用户当前正在看什么文件
- 之前的对话历史

### 第 5 层：规则系统（Rules System）

```xml
<rules>
  <always_applied_workspace_rules>
    <!-- .cursor/rules/*.mdc 文件内容 -->
    <!-- 根目录 AGENTS.md 文件内容 -->
  </always_applied_workspace_rules>
  
  <user_rules>
    <!-- 用户全局设置，如 "Always respond in 中文" -->
  </user_rules>
</rules>
```

**规则来源与优先级：**

| 来源 | 路径 | 作用域 | 描述 |
|------|------|--------|------|
| 工作区规则 | `.cursor/rules/*.mdc` | 项目级 | 项目特定的行为规范 |
| AGENTS.md | 项目根目录 `AGENTS.md` | 项目级 | 项目结构、技术栈、开发规范 |
| 用户规则 | Cursor 设置 | 全局 | 跨项目的个人偏好 |

**AGENTS.md 的特殊地位：**

AGENTS.md 是一个行业标准文件（类似 `.editorconfig`），Cursor 会自动将其注入到系统提示词的 `always_applied_workspace_rules` 中。它包含：
- 项目技术栈描述
- 开发环境设置指南
- 构建和运行命令
- 测试命令
- 代码风格规范
- 项目结构说明
- 架构说明
- 提交规范
- 已知注意事项

### 第 6 层：技能系统（Skills System）

```xml
<agent_skills>
  <available_skills>
    <agent_skill fullPath="/absolute/path/to/SKILL.md">
      技能描述和触发条件
    </agent_skill>
    ...
  </available_skills>
</agent_skills>
```

**技能加载机制：**

1. Cursor 启动时扫描多个目录收集可用技能
2. 只将技能的**路径和简短描述**注入系统提示词（目录模式）
3. Agent 判断需要某个技能时，通过 `Read` 工具读取完整 `SKILL.md`
4. 按照 SKILL.md 中的指令执行

**技能搜索路径（优先级从高到低）：**

```
.cursor/skills/           → 工作区级技能
~/.cursor/skills/         → 用户级 Cursor 技能
~/.cursor/skills-cursor/  → Cursor 专用技能
~/.claude/plugins/cache/  → Claude 缓存的外部技能包
~/.codex/skills/          → Codex 系统技能
```

**技能类型示例：**

| 技能 | 触发条件 |
|------|----------|
| `openakita-plugin-dev` | 创建 OpenAkita 插件 |
| `ppt-theme-design` | PPT 主题设计 |
| `find-skills` | 查找可安装的技能 |
| `powerpoint` | 创建/分析 PowerPoint |
| `md2wechat` | Markdown 转微信公众号格式 |
| `frontend-design` | 前端界面设计 |
| `pdf` | PDF 处理 |
| `xlsx` | Excel 处理 |
| `mcp-builder` | 构建 MCP 服务器 |
| `webapp-testing` | Web 应用测试 |

### 第 7 层：MCP 集成层

MCP（Model Context Protocol）是 Cursor 的外部工具扩展协议。

```xml
<mcp_file_system>
  <mcp_file_system_servers>
    <mcp_file_system_server 
      name="cursor-ide-browser" 
      folderPath="~/.cursor/projects/<project>/mcps/cursor-ide-browser"
      serverUseInstructions="...详细使用说明...">
    </mcp_file_system_server>
  </mcp_file_system_servers>
</mcp_file_system>
```

**MCP 文件系统结构：**

```
~/.cursor/projects/<project>/mcps/
  └── <server-name>/
      ├── SERVER_METADATA.json     # 服务器元数据
      ├── INSTRUCTIONS.md          # 使用说明（注入到系统提示词）
      ├── tools/                   # 工具描述符
      │   ├── tool_name_1.json
      │   ├── tool_name_2.json
      │   └── ...
      └── resources/               # 资源描述符
          └── resource_name.json
```

**cursor-ide-browser MCP 服务器** 提供 35 个浏览器自动化工具：

| 工具 | 功能 |
|------|------|
| `browser_navigate` | 导航到 URL |
| `browser_snapshot` | 获取页面结构 |
| `browser_click` | 点击元素 |
| `browser_type` / `browser_fill` | 输入文本 |
| `browser_scroll` | 滚动页面 |
| `browser_tabs` | 管理标签页 |
| `browser_take_screenshot` | 截屏 |
| `browser_lock` / `browser_unlock` | 锁定/解锁浏览器 |
| `browser_profile_start` / `browser_profile_stop` | CPU 性能分析 |
| `browser_console_messages` | 获取控制台消息 |
| `browser_network_requests` | 获取网络请求 |
| ... | 以及更多交互工具 |

**使用协议核心要求：**
- Lock/Unlock 工作流：`navigate → lock → 操作 → unlock`
- 操作前必须先 `snapshot` 获取页面结构
- 等待策略：短间隔轮询（1-3秒）而非长时间等待

### 第 8 层：模式选择（Mode Selection）

```xml
<mode_selection>
  根据用户目标选择最佳交互模式。目标变化或遇到困难时重新评估。
</mode_selection>
```

| 模式 | 可读写 | 可主动切换 | 适用场景 |
|------|--------|-----------|----------|
| **Agent** | 读写 | 否（默认） | 实现功能、修改代码 |
| **Plan** | 只读 | 是 | 方案设计、架构讨论、需求不明确 |
| **Debug** | 读写 | 否 | 调试 bug、排查问题 |
| **Ask** | 只读 | 否 | 回答问题、探索代码 |

Agent 只能通过 `SwitchMode` 工具主动切换到 **Plan** 模式。

**切换到 Plan 的触发条件：**
- 任务有多种有效方案且有重大权衡
- 需要架构决策
- 涉及大范围文件修改
- 需求不明确，需要先探索

---

## 三、提示词组装流程

```
用户在 Cursor 中发送消息
         │
         ▼
┌────────────────────────────────┐
│  Cursor Client 自动采集上下文    │
│                                │
│  ✓ 读取当前打开的文件列表        │
│  ✓ 获取 Git status 快照         │
│  ✓ 扫描终端状态文件              │
│  ✓ 收集 Linter 诊断信息         │
│  ✓ 记录光标位置和选中文本        │
│  ✓ 获取最近查看的文件列表        │
│  ✓ 读取编辑历史                 │
└──────────┬─────────────────────┘
           │
           ▼
┌────────────────────────────────┐
│  System Prompt 装配              │
│                                │
│  [1] 基础角色声明               │
│  [2] 工具定义（JSON Schema）     │
│  [3] 行为规则集                 │
│      ├─ system-communication   │
│      ├─ tone_and_style         │
│      ├─ tool_calling           │
│      ├─ making_code_changes    │
│      ├─ no_thinking_in_code    │
│      ├─ linter_errors          │
│      ├─ citing_code            │
│      ├─ inline_line_numbers    │
│      ├─ terminal_files_info    │
│      └─ task_management        │
│  [4] 规则注入                   │
│      ├─ workspace rules (.mdc) │
│      ├─ AGENTS.md              │
│      └─ user rules             │
│  [5] 技能目录（路径 + 描述）     │
│  [6] MCP 服务器配置              │
│  [7] 模式选择指令               │
└──────────┬─────────────────────┘
           │
           ▼
┌────────────────────────────────┐
│  User Message 包装               │
│                                │
│  <user_info>                   │
│    OS、Shell、路径、日期等       │
│  </user_info>                  │
│                                │
│  <git_status>                  │
│    分支和文件状态快照            │
│  </git_status>                 │
│                                │
│  <open_and_recently_viewed_    │
│   files>                       │
│    当前打开和最近查看的文件       │
│  </open_and_recently_viewed_   │
│   files>                       │
│                                │
│  <agent_transcripts>           │
│    历史对话路径                  │
│  </agent_transcripts>          │
│                                │
│  <user_query>                  │
│    用户的原始消息                │
│  </user_query>                 │
└──────────┬─────────────────────┘
           │
           ▼
┌────────────────────────────────┐
│  发送到 LLM（Claude）           │
│                                │
│  messages: [                   │
│    { role: "system",           │
│      content: 装配后的系统提示词 │
│    },                          │
│    { role: "user",             │
│      content: 包装后的用户消息   │
│    }                           │
│  ]                             │
└──────────┬─────────────────────┘
           │
           ▼
┌────────────────────────────────┐
│  LLM 响应循环                    │
│                                │
│  LLM 返回文本 + 工具调用         │
│       │                        │
│       ├─→ 执行工具              │
│       │     │                  │
│       │     └─→ 返回工具结果    │
│       │           │            │
│       │           └─→ 追加到   │
│       │               消息历史  │
│       │                        │
│       └─→ 重复直到 LLM 不再    │
│           调用工具（任务完成）    │
└────────────────────────────────┘
```

---

## 四、子代理（Sub-Agent）架构

Cursor 通过 `Task` 工具支持启动独立的子代理，这是其多代理能力的核心。

### 4.1 子代理类型

| 类型 | 用途 | 特点 |
|------|------|------|
| `generalPurpose` | 复杂多步任务、代码搜索 | 完整工具集，适合不确定能快速找到的搜索 |
| `explore` | 代码库快速探索 | 快速搜索文件/代码/回答问题，支持 quick/medium/very thorough 三级 |
| `shell` | 命令执行 | 专注 git、bash 等终端操作 |
| `browser-use` | 浏览器自动化 | 有状态（自动复用），支持页面交互和截图 |
| `best-of-n-runner` | 隔离实验 | 独立 git worktree，适合并行尝试多种方案 |

### 4.2 子代理关键设计

```
父代理                          子代理
  │                               │
  │  Task(prompt="...",           │
  │       subagent_type="...")    │
  │──────────────────────────────→│
  │                               │  ← 不继承用户消息
  │                               │  ← 不继承父代理历史
  │                               │  ← 只有 prompt 中的信息
  │                               │
  │  result = "..."               │
  │←──────────────────────────────│
  │                               │
  │  用户看不到子代理的返回值      │
  │  需要父代理转述               │
```

- **上下文隔离**：子代理不继承用户消息和父代理历史步骤，必须在 `prompt` 中提供完整上下文
- **结果不可见**：子代理的返回结果用户看不到，父代理需要在文本中转述
- **可恢复**：通过 `resume` 参数恢复之前的子代理会话
- **可并行**：多个独立子代理可在同一消息中并行启动
- **模型选择**：可指定 `fast` 模型降低成本（cost: 1/10, intelligence: 5/10）
- **后台运行**：`run_in_background: true` 可将子代理放入后台

### 4.3 子代理使用决策树

```
用户请求
  │
  ├─ 简单/单步任务？ → 直接用工具，不启动子代理
  │
  ├─ 搜索特定类名/文件名？ → 直接用 Grep/Glob
  │
  ├─ 广泛探索代码库？ → Task(subagent_type="explore")
  │
  ├─ 多个独立区域需要探索？ → 并行启动多个 explore 子代理
  │
  ├─ 需要隔离实验？ → Task(subagent_type="best-of-n-runner")
  │
  └─ 浏览器测试？ → Task(subagent_type="browser-use")
```

---

## 五、核心设计模式分析

### 5.1 防御性编程模式

Cursor 在提示词中嵌入了大量防御性规则：

| 场景 | 防御措施 |
|------|----------|
| Git 操作 | 禁止 force push 到 main/master、禁止修改 git config、禁止 `--no-verify` |
| 文件编辑 | 必须先 Read 再编辑、StrReplace 要求 old_string 唯一 |
| Commit | 禁止主动 commit（除非用户明确要求）、禁止空 commit |
| Amend | 5 个条件全部满足才允许 amend |
| 密钥文件 | 禁止 commit .env、credentials.json 等 |

### 5.2 工具优先级模式

Cursor 明确定义了工具使用的优先级：

```
专用工具 > Shell 命令

Read          > cat / head / tail
Write         > echo > file / heredoc
StrReplace    > sed / awk
Grep (rg)     > grep / find
Glob          > find
```

### 5.3 上下文窗口管理策略

为应对有限的上下文窗口，Cursor 采用多种压缩策略：

| 策略 | 实现 |
|------|------|
| 文件分页 | Read 支持 offset + limit |
| 搜索截断 | Grep 结果上限数千行 |
| 语义搜索限制 | SemanticSearch 最多 15 条结果 |
| 技能懒加载 | 只注入路径和描述，用时才读取完整内容 |
| 子代理分担 | 通过 Task 将大任务分配给独立上下文的子代理 |
| Git 快照 | 只注入对话开始时的一次性快照 |

### 5.4 Token 预算估算

| 组成部分 | 估算 Token 数 |
|----------|--------------|
| 系统提示词（基础） | ~8,000-12,000 |
| 工具 JSON Schema | ~4,000-6,000 |
| 行为规则集 | ~3,000-5,000 |
| 规则注入（AGENTS.md 等） | ~500-2,000 |
| 技能目录 | ~1,000-3,000 |
| MCP 配置 | ~1,000-2,000 |
| 上下文注入（每次消息） | ~1,000-5,000 |
| **总计** | **~18,000-35,000** |

---

## 六、与 OpenAkita 提示词系统的对比

| 维度 | Cursor | OpenAkita |
|------|--------|-----------|
| **提示词编译** | 运行时动态装配，客户端负责拼接 | `prompt/compiler.py` 预编译 + `prompt/builder.py` 分层装配 |
| **身份系统** | 固定角色声明（硬编码在系统提示词中） | SOUL.md → AGENT.md → USER.md → MEMORY.md 多文件身份体系 |
| **装配层级** | Identity → Tools → Rules → Skills → MCP → Mode | Identity → Persona → Runtime → Session Rules → AGENTS.md → Catalogs → Memory → User |
| **规则来源** | `.cursor/rules/` + AGENTS.md + 用户规则 | identity 文件 + POLICIES.yaml |
| **技能系统** | SKILL.md 声明式，懒加载（用时通过 Read 读取） | SKILL.md 声明式，启动时扫描注册到 registry |
| **多代理** | Task 工具 → 5 种子代理类型（上下文隔离） | Orchestrator → Factory → AgentProfile（共享 PromptAssembler） |
| **代理深度** | 无明确限制（子代理可嵌套） | 最大委托深度 = 5 |
| **上下文注入** | 客户端自动采集（git/files/linter/cursor） | 三层记忆（unified_store/vector/retrieval） |
| **工具定义** | JSON Schema 内嵌在系统提示词 | handlers/ + definitions/ 分离存储 |
| **模式系统** | 4 种模式（Agent/Plan/Debug/Ask） | 无显式模式切换 |
| **扩展协议** | MCP（Model Context Protocol） | 插件系统（PluginAPI） |

---

## 七、关键技术细节

### 7.1 终端持久化机制

Cursor 将每个 IDE 终端的输出存储为纯文本文件：

```
~/.cursor/projects/<project>/terminals/
  ├── 1.txt
  ├── 2.txt
  └── 6.txt
```

文件格式：
```
---
pid: 68861
cwd: /Users/me/proj
last_command: sleep 5
last_exit_code: 1
---
（完整终端输出）
```

Agent 通过读取这些文件来：
- 监控后台命令的执行状态
- 检查命令是否已完成（看 `exit_code`）
- 获取命令输出用于后续决策

### 7.2 Shell 后台命令管理

```
Shell(command="npm run dev", block_until_ms=0)
  │
  ├─ 立即返回（不等待完成）
  │
  ├─ 命令输出流向 terminals/<id>.txt
  │
  ├─ Agent 定期读取文件检查状态
  │   ├─ 文件头部：pid + running_for_ms（每 5 秒更新）
  │   └─ 文件尾部：exit_code + elapsed_ms（完成时出现）
  │
  └─ 轮询策略：指数退避（2s → 4s → 8s → 16s）
```

### 7.3 MCP 工具发现机制

```
Agent 需要使用 MCP 工具
  │
  ├─ [1] 浏览 mcps/<server>/tools/ 目录
  │
  ├─ [2] 读取目标工具的 JSON 描述符
  │       获取参数类型、必填项、使用说明
  │
  ├─ [3] 通过 CallMcpTool 调用
  │       传入 server + toolName + arguments
  │
  └─ [4] 如果有 mcp_auth 工具，必须先认证
```

### 7.4 Git 操作安全协议

```
Git Commit 流程：
  [1] 并行执行: git status + git diff + git log
  [2] 分析变更，草拟 commit message
  [3] 检查是否包含密钥文件
  [4] git add + git commit（HEREDOC 格式）
  [5] git status 验证成功

Git Amend 条件（全部满足才允许）：
  ✓ 用户明确要求 amend，或 commit 成功但 hook 修改了文件
  ✓ HEAD commit 是当前对话中由 Agent 创建的
  ✓ Commit 尚未 push 到远程
  ✗ 如果 commit 失败/被 hook 拒绝 → 永不 amend
  ✗ 如果已 push → 永不 amend（除非用户明确要求 force push）
```

### 7.5 Canvas 系统

Cursor 的 MCP Browser 工具内置了 Canvas 创建能力：

- Canvas 是存储在本地的 `.html` 文件
- 支持 livereload（编辑后自动刷新）
- 有详细的设计规范（排版、配色、动效、布局）
- 推荐 CDN 库：Three.js、Chart.js、D3、p5.js、GSAP、React 等
- 适用场景：交互演示、可视化、图表（不适合纯文本/简单代码）

---

## 八、完整工具清单与参数

### Shell

```json
{
  "command": "string (required)",
  "description": "string (5-10 words)",
  "working_directory": "string (absolute path)",
  "block_until_ms": "number (default: 30000)"
}
```

### Read

```json
{
  "path": "string (required, absolute path)",
  "offset": "integer (1-indexed, negative = from end)",
  "limit": "integer (number of lines)"
}
```

### Write

```json
{
  "path": "string (required, absolute path)",
  "contents": "string (required)"
}
```

### StrReplace

```json
{
  "path": "string (required, absolute path)",
  "old_string": "string (required, must be unique)",
  "new_string": "string (required, must differ from old)",
  "replace_all": "boolean (default: false)"
}
```

### Grep

```json
{
  "pattern": "string (required, regex)",
  "path": "string (file or directory)",
  "glob": "string (file filter, e.g. '*.js')",
  "type": "string (file type, e.g. 'py')",
  "output_mode": "content | files_with_matches | count",
  "-A": "number (lines after match)",
  "-B": "number (lines before match)",
  "-C": "number (context lines)",
  "-i": "boolean (case insensitive)",
  "multiline": "boolean (cross-line patterns)",
  "head_limit": "number (max results)",
  "offset": "number (skip first N)"
}
```

### SemanticSearch

```json
{
  "query": "string (required, complete question)",
  "target_directories": "string[] (required, single dir or [])",
  "num_results": "integer (1-15, default: 15)"
}
```

### Task

```json
{
  "prompt": "string (required, full task description)",
  "description": "string (required, 3-5 words)",
  "subagent_type": "generalPurpose | explore | shell | browser-use | best-of-n-runner",
  "model": "fast (optional)",
  "readonly": "boolean",
  "resume": "string (agent ID)",
  "run_in_background": "boolean",
  "attachments": "string[] (video paths)"
}
```

### TodoWrite

```json
{
  "todos": [
    {
      "id": "string (required)",
      "content": "string (required)",
      "status": "pending | in_progress | completed | cancelled"
    }
  ],
  "merge": "boolean (required)"
}
```

### CallMcpTool

```json
{
  "server": "string (required, MCP server ID)",
  "toolName": "string (required)",
  "arguments": "object"
}
```

---

## 九、安全机制

### 9.1 Git 安全

| 规则 | 说明 |
|------|------|
| 禁止修改 git config | 防止身份伪造 |
| 禁止 `--force` push | 防止历史覆盖 |
| 禁止 `--no-verify` | 确保 hook 执行 |
| 禁止 force push 到 main | 额外警告 |
| 禁止主动 commit | 必须用户明确要求 |
| 禁止 `git rebase -i` | 交互式命令不支持 |

### 9.2 文件安全

| 规则 | 说明 |
|------|------|
| 编辑前必须 Read | 防止盲改 |
| StrReplace 要求唯一匹配 | 防止误替换 |
| 禁止 commit 密钥文件 | .env, credentials.json 等 |
| Delete 优雅失败 | 文件不存在不报错 |

### 9.3 执行安全

| 规则 | 说明 |
|------|------|
| 子代理上下文隔离 | 防止信息泄露 |
| WebFetch 只读 | 不支持副作用请求 |
| WebFetch 无认证 | 不暴露凭证 |
| WebFetch 无私网 | localhost/内网 IP 不可访问 |
| 模式限制 | Agent 只能切换到 Plan（只读） |

---

## 十、总结

### Cursor 提示词系统的核心设计哲学

1. **分层装配**：系统提示词不是一个整体，而是由 8 个独立层级按顺序拼装，每层负责一个关注点
2. **上下文感知**：自动采集用户 IDE 状态，无需手动提供项目信息
3. **防御性设计**：在工具描述和规则中嵌入大量安全约束，防止 Agent 执行危险操作
4. **懒加载优化**：技能只注入索引，MCP 工具需先读取 Schema，减少 token 消耗
5. **多代理协作**：通过 Task 工具实现子代理派发，支持并行和隔离执行
6. **规则可扩展**：用户可通过 `.cursor/rules/`、`AGENTS.md`、用户设置三个层级自定义 Agent 行为
7. **模式适配**：根据任务类型切换到最合适的交互模式

### 对 OpenAkita 的启示

Cursor 的分层提示词架构与 OpenAkita 的 `prompt/builder.py` 理念高度一致，但 Cursor 在以下方面更为成熟：
- **IDE 深度集成**（自动采集打开文件、光标、linter 等）
- **工具内嵌规范**（每个工具描述中包含完整的使用协议）
- **子代理类型化**（5 种专用子代理而非通用委托）
- **MCP 标准化扩展**（统一的外部工具发现和调用协议）

这些模式可以作为 OpenAkita 后续迭代的参考方向。

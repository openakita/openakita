# Agent Skills 用法指南与技能大全资源

> 本文档整理了 Agent Skills 的完整用法、SKILL.md 规范、OpenAkita 项目技能体系，以及外部技能大全网站资源。

---

## 目录

1. [什么是 Agent Skills](#什么是-agent-skills)
2. [SKILL.md 文件规范](#skillmd-文件规范)
3. [技能分类](#技能分类)
4. [使用方法](#使用方法)
5. [创建自定义技能](#创建自定义技能)
6. [技能管理命令](#技能管理命令)
7. [OpenAkita 内置技能一览](#openakita-内置技能一览)
8. [技能大全网站 & 资源汇总](#技能大全网站--资源汇总)
9. [Cursor IDE 中使用 Skills](#cursor-ide-中使用-skills)
10. [最佳实践](#最佳实践)

---

## 什么是 Agent Skills

Agent Skills（代理技能）是一种模块化的能力扩展机制，允许 AI Agent 动态加载、执行和管理各种能力。每个技能就像一个"插件"，提供特定领域的知识和操作能力。

**核心特点：**

- **可移植**：可在任何支持 Agent Skills 标准的 Agent 中使用（Cursor、Claude Code、VS Code 等）
- **版本控制**：以文件形式存储，可用 Git 管理
- **模块化**：每个技能独立，可按需组合
- **自动触发**：Agent 根据任务上下文自动调用相关技能

**工作流程：**

```
加载技能 → 智能匹配 → 自动调用 → 执行任务
```

1. **加载**：Agent 启动时加载所有已安装的技能
2. **匹配**：Agent 根据用户请求的上下文判断需要哪些技能
3. **调用**：相关技能信息自动拉取到上下文中
4. **执行**：Agent 根据技能提供的知识和脚本执行任务

---

## SKILL.md 文件规范

每个技能由一个目录表示，其中必须包含 `SKILL.md` 文件。

### 目录结构

```
skill-name/
├── SKILL.md          # 必需：技能定义文件
├── scripts/          # 可选：可执行脚本（Python/Bash 等）
├── references/       # 可选：参考文档
└── assets/           # 可选：模板、图标等资源文件
```

### YAML Frontmatter（头部元数据）

```yaml
---
name: my-skill                  # 技能名称（必填，小写+连字符）
description: "描述技能的功能..."  # 简短描述（必填）
system: false                   # 是否为系统技能（可选，默认 false）
handler: skills                 # 处理器名称（系统技能必填）
tool-name: my_tool              # 原始工具名（系统技能必填）
category: Utility               # 分类（可选）
---
```

### 必填字段

| 字段 | 说明 |
|------|------|
| `name` | 技能名称，使用小写字母和连字符 |
| `description` | 简短描述，说明功能和使用场景 |

### 系统技能专用字段

| 字段 | 说明 |
|------|------|
| `system` | 设为 `true` 表示系统技能 |
| `handler` | 对应的处理器名称（如 browser, filesystem） |
| `tool-name` | 原始工具名（如 browser_navigate） |

### Markdown 正文内容

SKILL.md 的正文部分通常包含：

- **# 技能名称**：标题
- **## When to Use**：何时使用此技能
- **## Workflow**：工作流程
- **## Parameters**：参数说明表
- **## Examples**：使用示例
- **## Related Skills**：相关技能

### 完整示例

```markdown
---
name: web-search
description: "Search the web using DuckDuckGo."
system: true
handler: web_search
tool-name: web_search
category: Web Search
---

# Web Search

使用 DuckDuckGo 搜索网页，获取最新信息。

## Parameters

| 参数 | 类型 | 必填 | 说明 |
|-----|------|-----|------|
| query | string | 是 | 搜索关键词 |
| max_results | integer | 否 | 最大结果数（1-20，默认 5） |

## Examples

**搜索信息**:
{"query": "Python asyncio 教程", "max_results": 5}

## Related Skills

- `news-search`: 搜索新闻
```

---

## 技能分类

### 系统技能 vs 外部技能

| 特性 | 系统技能 | 外部技能 |
|------|---------|---------|
| `system` 字段 | `true` | `false` / 不设置 |
| 处理器 | 专用处理器（Python 方法） | skills 通用处理器（脚本执行） |
| 工具名 | 原始名称（如 `browser_navigate`） | 加 `skill_` 前缀（如 `skill_datetime_tool`） |
| 位置 | `skills/system/` | `skills/` 根目录 |
| 来源 | 内置 | 用户安装 / 自定义 |

### 按功能分类

| 分类 | 技能示例 | 说明 |
|------|---------|------|
| 浏览器控制 | browser-navigate, browser-click, browser-type | 网页浏览和操作 |
| 桌面自动化 | desktop-click, desktop-type, desktop-screenshot | 桌面 GUI 操作 |
| 文件系统 | read-file, write-file, list-directory, run-shell | 文件和命令行操作 |
| 记忆系统 | add-memory, search-memory, get-memory-stats | 长期记忆管理 |
| 计划管理 | create-todo, update-todo-step, get-todo-status, complete-todo | 多步骤任务规划 |
| 定时任务 | schedule-task, list-scheduled-tasks, cancel-scheduled-task | 定时和周期任务 |
| MCP 集成 | call-mcp-tool, list-mcp-servers, get-mcp-instructions | MCP 服务器工具调用 |
| 用户档案 | update-user-profile, get-user-profile | 用户偏好管理 |
| 技能管理 | list-skills, install-skill, load-skill, reload-skill | 技能自身管理 |
| 网络搜索 | web-search, news-search | 网页和新闻搜索 |
| 多媒体 | generate-image, get-image-file, get-voice-file | 图片生成和媒体处理 |
| 文档处理 | docx, pptx, xlsx, pdf | 办公文档处理 |

---

## 使用方法

### 1. 在对话中使用（自然语言）

直接告诉 Agent 你要做什么，它会自动匹配并调用相关技能：

```
用户> 帮我打开百度搜索天气，然后截图发给我
Agent> [自动创建计划 → 调用 browser-navigate → browser-type → browser-screenshot → deliver-artifacts]
```

### 2. 通过命令行

```bash
# 列出所有可用技能
openakita skills list

# 运行某个技能
openakita skills run my_skill --input "test data"

# 从 GitHub 安装技能
openakita skills install github:user/repo/skill_name
```

### 3. 编程方式调用

```python
from openakita.skills import SkillRegistry

registry = SkillRegistry()
skill = registry.get("my_skill")
result = await skill.execute(input="test")
```

---

## 创建自定义技能

### 方法一：手动创建

1. 在 `skills/` 目录下创建新文件夹
2. 编写 `SKILL.md` 文件（包含 frontmatter + 说明）
3. 添加脚本到 `scripts/` 子目录（可选）
4. 调用 `load_skill("my-skill")` 加载

### 方法二：使用 skill-creator 技能

```
用户> 调用 skill-creator，帮我做一个自动生成改动报表的 Skill
Agent> [自动生成 SKILL.md + scripts → 加载 → 可用]
```

### 方法三：从 GitHub/URL 安装

```
调用 install_skill：
- source: "https://github.com/user/my-skill"
- subdir: "skills/my-skill"（可选）
```

### 创建技巧

1. **学习官方示例**：阅读已有的 SKILL.md 了解格式
2. **梳理工作流**：将要标准化的流程写清楚
3. **MVP 先行**：先做最小可用版本，测试通过再优化
4. **倒推法**：先实现脚本，测试通过后倒推写 SKILL.md
5. **版本管理**：用 Git 管理技能的迭代过程

---

## 技能管理命令

| 命令 | 说明 |
|------|------|
| `list_skills()` | 列出已安装的所有技能 |
| `get_skill_info(name)` | 获取技能详细信息 |
| `install_skill(source, name?)` | 从 URL/Git 安装技能 |
| `load_skill(skill_name)` | 加载新创建的技能 |
| `reload_skill(skill_name)` | 重新加载已有技能 |
| `run_skill_script(skill_name, script, args?)` | 运行技能脚本 |
| `get_skill_reference(skill_name, file)` | 获取技能参考文档 |

---

## OpenAkita 内置技能一览

### 浏览器控制（12 个）

| 技能名 | 工具名 | 说明 |
|--------|--------|------|
| browser-open | browser_open | 打开浏览器 |
| browser-navigate | browser_navigate | 导航到 URL |
| browser-click | browser_click | 点击页面元素 |
| browser-type | browser_type | 输入文本 |
| browser-screenshot | browser_screenshot | 网页截图 |
| browser-get-content | browser_get_content | 获取页面内容 |
| browser-list-tabs | browser_list_tabs | 列出标签页 |
| browser-new-tab | browser_new_tab | 新建标签页 |
| browser-switch-tab | browser_switch_tab | 切换标签页 |
| browser-status | browser_status | 浏览器状态 |
| browser-task | browser_task | 浏览器复合任务 |

### 桌面自动化（9 个）

| 技能名 | 工具名 | 说明 |
|--------|--------|------|
| desktop-screenshot | desktop_screenshot | 桌面截图 |
| desktop-click | desktop_click | 桌面点击 |
| desktop-type | desktop_type | 桌面输入 |
| desktop-find-element | desktop_find_element | 查找 UI 元素 |
| desktop-hotkey | desktop_hotkey | 发送快捷键 |
| desktop-inspect | desktop_inspect | 检查 UI 元素 |
| desktop-scroll | desktop_scroll | 桌面滚动 |
| desktop-wait | desktop_wait | 等待 |
| desktop-window | desktop_window | 窗口管理 |

### 文件与系统（4 个）

| 技能名 | 工具名 | 说明 |
|--------|--------|------|
| read-file | read_file | 读取文件 |
| write-file | write_file | 写入文件 |
| list-directory | list_directory | 列出目录 |
| run-shell | run_shell | 执行 Shell 命令 |

### 计划管理（5 个）

| 技能名 | 工具名 | 说明 |
|--------|--------|------|
| create-todo | create_todo | 创建执行计划 |
| update-todo-step | update_todo_step | 更新步骤状态 |
| get-todo-status | get_todo_status | 查看计划状态 |
| complete-todo | complete_todo | 完成计划 |
| set-task-timeout | set_task_timeout | 设置任务超时 |

### 记忆系统（3 个）

| 技能名 | 工具名 | 说明 |
|--------|--------|------|
| add-memory | add_memory | 添加记忆 |
| search-memory | search_memory | 搜索记忆 |
| get-memory-stats | get_memory_stats | 记忆统计 |

### 定时任务（5 个）

| 技能名 | 工具名 | 说明 |
|--------|--------|------|
| schedule-task | schedule_task | 创建定时任务 |
| list-scheduled-tasks | list_scheduled_tasks | 列出定时任务 |
| cancel-scheduled-task | cancel_scheduled_task | 取消定时任务 |
| trigger-scheduled-task | trigger_scheduled_task | 手动触发任务 |
| update-scheduled-task | update_scheduled_task | 更新定时任务 |

### MCP 集成（3 个）

| 技能名 | 工具名 | 说明 |
|--------|--------|------|
| call-mcp-tool | call_mcp_tool | 调用 MCP 工具 |
| list-mcp-servers | list_mcp_servers | 列出 MCP 服务器 |
| get-mcp-instructions | get_mcp_instructions | 获取 MCP 说明 |

### 用户档案（3 个）

| 技能名 | 工具名 | 说明 |
|--------|--------|------|
| get-user-profile | get_user_profile | 获取用户档案 |
| update-user-profile | update_user_profile | 更新用户档案 |
| skip-profile-question | skip_profile_question | 跳过档案问题 |

### 技能管理（7 个）

| 技能名 | 工具名 | 说明 |
|--------|--------|------|
| list-skills | list_skills | 列出所有技能 |
| get-skill-info | get_skill_info | 获取技能详情 |
| get-skill-reference | get_skill_reference | 获取技能参考文档 |
| install-skill | install_skill | 安装技能 |
| load-skill | load_skill | 加载技能 |
| reload-skill | reload_skill | 重新加载技能 |
| run-skill-script | run_skill_script | 运行技能脚本 |

### 网络搜索（2 个）

| 技能名 | 工具名 | 说明 |
|--------|--------|------|
| web-search | web_search | 网页搜索（DuckDuckGo） |
| news-search | news_search | 新闻搜索 |

### 其他工具（6 个）

| 技能名 | 工具名 | 说明 |
|--------|--------|------|
| generate-image | generate_image | AI 生成图片 |
| get-image-file | get_image_file | 获取图片文件 |
| get-voice-file | get_voice_file | 获取语音文件 |
| get-chat-history | get_chat_history | 获取聊天记录 |
| deliver-artifacts | deliver_artifacts | 发送产出物 |
| enable-thinking | enable_thinking | 启用深度思考模式 |
| get-session-logs | get_session_logs | 获取会话日志 |
| get-tool-info | get_tool_info | 获取工具信息 |

---

## 技能大全网站 & 资源汇总

### 技能市场 / 目录网站

| 网站 | 地址 | 技能数量 | 特点 |
|------|------|---------|------|
| **AgentSkills** | [agentskills.to](https://agentskills.to/) | 24,000+ | 最大的技能市场，6大分类，支持 Claude Code / Cursor / Codex CLI / Gemini CLI |
| **AgentSkill.space** | [agentskill.space](https://www.agentskill.space/) | 17,122+ | 跨平台索引，支持 Claude Code / Cursor / VS Code 等 |
| **Install Agent Skills** | [installagentskills.com](https://installagentskills.com/) | - | 带信任评分和安全检查的可信技能目录，一键安装 |
| **PRPM** | [prpm.dev](https://prpm.dev/) | 7,500+ | 通用注册表，包含 Cursor Rules + Claude Agents + Slash Commands |
| **Skills.sh** | [skills.sh](https://skills.sh/) | - | 开放的 Agent Skills 目录，免费浏览和安装 |
| **Awesome Claude** | [awesomeclaude.ai](https://awesomeclaude.ai/awesome-claude-skills) | - | Claude 技能精选集 |

### GitHub 精选仓库

| 仓库 | Stars | 说明 |
|------|-------|------|
| [ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills) | 32,600+ | 最大的 Claude 技能精选集，覆盖生产力、自动化等 |
| [travisvn/awesome-claude-skills](https://github.com/travisvn/awesome-claude-skills) | 6,696+ | 含官方 Anthropic 技能 + 社区贡献，分类清晰 |
| [karanb192/awesome-claude-skills](https://github.com/karanb192/awesome-claude-skills) | - | 50+ 经验证的社区技能，含快速入门指南 |
| [garyblankenship/SKILL.md](https://github.com/garyblankenship/SKILL.md) | - | 自改进技能框架，支持 `/learn` 命令自动学习增强 |
| [anthropics/courses (skills)](https://github.com/anthropics/courses) | - | Anthropic 官方技能合集（docx, pdf, pptx, xlsx 等） |

### 安装技能的方式

**方式一：npx 一键安装（推荐）**
```bash
npx skills add owner/repo
```

**方式二：openskills 工具安装**
```bash
npm i -g openskills
openskills install anthropics/skills
openskills sync  # 生成 AGENTS.md
```

**方式三：Git 克隆**
```bash
git clone https://github.com/user/skill-repo
# 将技能目录复制到 skills/ 下
```

**方式四：通过 Agent 安装**
```
用户> 安装一个 PDF 处理的技能
Agent> [调用 install_skill 从 GitHub 安装]
```

---

## Cursor IDE 中使用 Skills

### 启用 Agent Skills

1. 打开 Cursor Settings（`Ctrl+Shift+J`）
2. 进入 Beta 选项卡 → 更新渠道选择 **Nightly**（如需要）
3. 进入 Rules → Import Settings → 启用 **Agent Skills** 开关

### 技能来源

Cursor 中的 Agent Skills 可以从以下位置加载：

- `~/.cursor/skills/` — 全局技能目录
- `~/.claude/skills/` — Claude Code 技能目录
- 项目根目录 `skills/` — 项目级技能
- `AGENTS.md` — 项目根目录的技能清单文件

### 在 Cursor 中使用技能

技能是**自动触发**的，无需手动调用。Agent 会根据你的请求自动匹配相关技能。

示例场景：
- 说"帮我做一份 PPT" → 自动加载 pptx 技能
- 说"分析这个 Excel" → 自动加载 xlsx 技能
- 说"搜索最新新闻" → 自动加载 web-search 技能

---

## 最佳实践

### 技能开发

- **单一职责**：每个技能聚焦于一个任务
- **完善的错误处理**：包含异常捕获和友好的错误信息
- **丰富的示例**：在 SKILL.md 中提供 Examples 部分
- **类型提示**：使用 Python 类型注解
- **编写测试**：为脚本编写单元测试

### 技能使用

- **查看已有技能**：使用前先 `list_skills` 了解可用能力
- **善用计划**：复杂任务先 `create_todo` 再执行
- **组合使用**：多个技能可以串联组合完成复杂流程
- **安装第三方**：从技能市场安装成熟的社区技能

### 注意事项

- 不要在技能代码中硬编码 API Key
- 不要让技能功能过于宽泛
- 不要跳过输入验证
- 不要忽略异常处理

---

## 技能系统架构

```
┌─────────────────────────────────────────────────────┐
│                      Agent                            │
├─────────────────────────────────────────────────────┤
│  ┌─────────────────┐    ┌─────────────────┐          │
│  │ SkillRegistry   │    │ HandlerRegistry │          │
│  │ (技能元数据)     │    │ (执行处理器)     │          │
│  └────────┬────────┘    └────────┬────────┘          │
│           │                      │                    │
│  ┌────────▼────────┐    ┌────────▼────────┐          │
│  │  SkillLoader    │    │   Handlers      │          │
│  │ (加载 SKILL.md) │    │ (10 个处理器,    │          │
│  │                 │    │  51 个工具)      │          │
│  └─────────────────┘    └─────────────────┘          │
└─────────────────────────────────────────────────────┘
```

**技能生命周期：**

```
发现(Discover) → 安装(Install) → 加载(Load) → 执行(Execute)
```

---

## 参考链接

- [Cursor Agent Skills 使用教程](https://cursor.zone/faq/cursor-agent-skills-guide.html)
- [手把手教你配置 Skills 技能库](https://index.zshipu.com/ai002/post/20251125/)
- [Cursor 2.4: Skills 官方公告](https://forum.cursor.com/t/cursor-2-4-skills/149402)
- [OpenAkita 技能加载架构文档](./skill-loading-architecture.md)
- [OpenAkita 技能系统文档](./skills.md)

---

*最后更新：2026-02-09*

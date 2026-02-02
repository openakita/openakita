# OpenAkita 技能加载架构

本文档描述 OpenAkita Agent 的技能系统架构，包括技能定义、加载流程和执行机制。

## 目录

1. [架构概览](#架构概览)
2. [SKILL.md 规范](#skillmd-规范)
3. [系统技能 vs 外部技能](#系统技能-vs-外部技能)
4. [处理器注册表](#处理器注册表)
5. [技能加载流程](#技能加载流程)
6. [工具执行流程](#工具执行流程)
7. [目录结构](#目录结构)

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                         Agent                                │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐    ┌─────────────────┐                 │
│  │ SkillRegistry   │    │ HandlerRegistry │                 │
│  │ (技能元数据)     │    │ (执行处理器)     │                 │
│  └────────┬────────┘    └────────┬────────┘                 │
│           │                      │                           │
│           │                      │                           │
│  ┌────────▼────────┐    ┌────────▼────────┐                 │
│  │  SkillLoader    │    │   Handlers      │                 │
│  │ (加载 SKILL.md) │    │ (filesystem,    │                 │
│  │                 │    │  browser, ...)  │                 │
│  └─────────────────┘    └─────────────────┘                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     skills/ 目录                             │
├─────────────────────────────────────────────────────────────┤
│  ├── browser-navigate/      (系统技能)                       │
│  │   └── SKILL.md                                           │
│  ├── run-shell/             (系统技能)                       │
│  │   └── SKILL.md                                           │
│  ├── datetime-tool/         (外部技能)                       │
│  │   ├── SKILL.md                                           │
│  │   └── scripts/                                           │
│  └── ...                                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## SKILL.md 规范

每个技能由一个包含 `SKILL.md` 文件的目录表示。

### YAML Frontmatter

```yaml
---
name: browser-navigate           # 技能名称（必填）
description: Navigate browser... # 简短描述（必填）
system: true                     # 是否为系统技能（可选，默认 false）
handler: browser                 # 处理器名称（系统技能必填）
tool-name: browser_navigate      # 原始工具名（系统技能必填）
category: Browser                # 分类（可选）
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
| `system` | 设为 `true` 表示这是系统技能 |
| `handler` | 对应的处理器名称（如 browser, filesystem） |
| `tool-name` | 原始工具名（如 browser_navigate） |

---

## 系统技能 vs 外部技能

### 系统技能

- **定义**: Agent 内置的核心功能
- **特点**: 
  - `system: true`
  - 有对应的 Python 处理器
  - 工具名保持原样（如 `browser_navigate`）
- **执行**: 通过 `SystemHandlerRegistry` 分发到对应处理器

### 外部技能

- **定义**: 用户安装或自定义的技能
- **特点**:
  - `system: false` 或不设置
  - 通常包含脚本文件
  - 工具名加 `skill_` 前缀（如 `skill_datetime_tool`）
- **执行**: 通过 skills 处理器运行脚本

### 比较

| 特性 | 系统技能 | 外部技能 |
|------|---------|---------|
| system 字段 | true | false/不设置 |
| 处理器 | 专用处理器 | skills 处理器 |
| 工具名 | 原始名称 | skill_ 前缀 |
| 执行方式 | Python 方法 | 脚本执行 |
| 示例 | browser_navigate | skill_datetime_tool |

---

## 处理器注册表

### SystemHandlerRegistry

管理所有系统工具的执行处理器。

```python
class SystemHandlerRegistry:
    def register(self, handler_name: str, handler: Callable, tool_names: list[str])
    def execute_by_tool(self, tool_name: str, params: dict) -> str
    def has_tool(self, tool_name: str) -> bool
```

### 已注册的处理器

| 处理器 | 工具数 | 工具 |
|--------|-------|------|
| filesystem | 4 | run_shell, write_file, read_file, list_directory |
| memory | 3 | add_memory, search_memory, get_memory_stats |
| browser | 10 | browser_open, browser_navigate, browser_click, ... |
| scheduled | 5 | schedule_task, list_scheduled_tasks, ... |
| mcp | 3 | call_mcp_tool, list_mcp_servers, get_mcp_instructions |
| profile | 3 | update_user_profile, skip_profile_question, get_user_profile |
| system | 3 | enable_thinking, get_session_logs, get_tool_info |
| im_channel | 4 | send_to_chat, get_voice_file, get_image_file, get_chat_history |
| skills | 7 | list_skills, get_skill_info, run_skill_script, ... |
| desktop | 9 | desktop_screenshot, desktop_click, desktop_type, ... |

**总计**: 10 个处理器，51 个工具

---

## 技能加载流程

```
Agent.initialize()
       │
       ▼
SkillLoader.load_from_directory("skills/")
       │
       ▼
┌──────────────────────────────────────┐
│ 遍历 skills/ 下的每个子目录           │
│                                      │
│   ├── 读取 SKILL.md                  │
│   ├── 解析 YAML frontmatter          │
│   ├── 构建 SkillEntry                │
│   │   ├── name                       │
│   │   ├── description                │
│   │   ├── system (bool)              │
│   │   ├── handler (str)              │
│   │   ├── tool_name (str)            │
│   │   └── category (str)             │
│   └── 注册到 SkillRegistry           │
└──────────────────────────────────────┘
       │
       ▼
生成 SkillCatalog (用于系统提示词)
```

### 加载时序图

```
Agent                SkillLoader           SkillRegistry
  │                       │                      │
  │  load_from_directory  │                      │
  ├──────────────────────>│                      │
  │                       │                      │
  │                       │  parse SKILL.md      │
  │                       ├──────────┐           │
  │                       │          │           │
  │                       │<─────────┘           │
  │                       │                      │
  │                       │     register         │
  │                       ├─────────────────────>│
  │                       │                      │
  │    return count       │                      │
  │<──────────────────────│                      │
  │                       │                      │
```

---

## 工具执行流程

```
_execute_tool(tool_name, tool_input)
              │
              ▼
  ┌───────────────────────────┐
  │ handler_registry.has_tool │
  │      (tool_name)?         │
  └─────────────┬─────────────┘
                │
       ┌────────┴────────┐
       │ Yes             │ No
       ▼                 ▼
┌──────────────┐   ┌──────────────┐
│ execute_by   │   │ return       │
│   _tool()    │   │ "未知工具"   │
└──────┬───────┘   └──────────────┘
       │
       ▼
┌──────────────────────────────────┐
│ 查找 tool -> handler 映射        │
│                                  │
│ tool_name -> handler_name        │
│ browser_navigate -> browser      │
│ run_shell -> filesystem          │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│ 调用 handler.handle(tool_name,   │
│                     params)      │
└──────────────┬───────────────────┘
               │
               ▼
         返回结果字符串
```

---

## 目录结构

```
openakita/
├── core/
│   └── agent.py              # Agent 主类
│                              # - handler_registry 初始化
│                              # - _init_handlers() 注册处理器
│                              # - _execute_tool() 执行工具
│
├── skills/
│   ├── parser.py             # SKILL.md 解析器
│   │                          # - SkillMetadata 数据类
│   │                          # - _build_metadata() 解析 frontmatter
│   ├── registry.py           # 技能注册表
│   │                          # - SkillEntry 数据类
│   │                          # - SkillRegistry 类
│   ├── loader.py             # 技能加载器
│   │                          # - SkillLoader 类
│   │                          # - load_from_directory()
│   └── catalog.py            # 技能目录
│                              # - SkillCatalog 类
│                              # - 渐进式披露
│
├── tools/
│   ├── handlers/             # 系统处理器
│   │   ├── __init__.py       # SystemHandlerRegistry
│   │   ├── filesystem.py     # 文件系统处理器
│   │   ├── browser.py        # 浏览器处理器
│   │   ├── memory.py         # 记忆处理器
│   │   ├── desktop.py        # 桌面自动化处理器
│   │   └── ...               # 其他处理器
│   │
│   └── definitions/          # 工具定义
│       ├── base.py           # 工具定义基类
│       ├── filesystem.py     # 文件系统工具定义
│       ├── browser.py        # 浏览器工具定义
│       └── ...               # 其他工具定义
│
skills/                        # 技能目录（项目根目录）
├── browser-navigate/          # 系统技能
│   └── SKILL.md
├── run-shell/                 # 系统技能
│   └── SKILL.md
├── datetime-tool/             # 外部技能
│   ├── SKILL.md
│   └── scripts/
│       └── get_time.py
└── ...
```

---

## 扩展指南

### 添加新的系统技能

1. 在 `tools/handlers/` 中创建或修改处理器
2. 在 `Agent._init_handlers()` 中注册工具
3. 在 `skills/` 目录创建 SKILL.md

### 添加新的外部技能

1. 在 `skills/` 目录创建新文件夹
2. 创建 SKILL.md（包含 frontmatter）
3. 添加脚本到 `scripts/` 子目录

---

## 版本历史

- **2026-02-03**: 重构工具执行系统，引入 SystemHandlerRegistry
- **2026-01-31**: 初始技能系统实现

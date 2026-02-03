# OpenAkita 项目执行规则

## 禁止随意截断

**重要原则：不允许随意截断数据**

1. **禁止截断的场景**：
   - 工具描述、技能描述 - LLM 需要完整信息才能正确调用
   - 浏览器页面内容 - LLM 需要看到完整页面
   - Session 历史记录 - 保持上下文完整性
   - 文件内容提取 - 用户发送的文件应完整处理
   - 任务描述、错误信息 - 调试和追踪需要完整信息
   - 日志输出 - 便于排查问题

2. **允许的例外**：
   - UUID 截取生成短 ID（如 `uuid[:8]`）
   - 平台 API 限制（如 Telegram 4096 字符限制，需分割发送）
   - 文件格式检测（只需前几字节判断 MIME 类型）
   - MEMORY.md 压缩（有专门的 LLM 压缩逻辑）

3. **如果确实需要截断**：
   - 必须有明确的技术原因
   - 优先使用 LLM 压缩/摘要而非硬截断
   - 在代码中注释说明原因

---

## 大文件修改规则

### agent.py 修改规则

`src/openakita/core/agent.py` 是核心文件，行数超过 2500 行。修改此文件时：

1. **禁止使用 StrReplace 工具** - 文件太大，StrReplace 可能导致编码损坏
2. **必须使用 Python 脚本修改** - 创建临时脚本执行批量修改
3. **脚本位置**: `scripts/` 目录下创建临时修改脚本
4. **执行后删除**: 修改完成后删除临时脚本
5. **修改后立即提交 Git** - 避免后续操作覆盖修改，便于回滚

### 示例脚本模板

```python
"""临时修改脚本 - 执行后删除"""
import re
from pathlib import Path

agent_file = Path("src/openakita/core/agent.py")
content = agent_file.read_text(encoding="utf-8")

# 批量替换
replacements = [
    (r'old_pattern_1', 'new_value_1'),
    (r'old_pattern_2', 'new_value_2'),
]

for pattern, replacement in replacements:
    content = re.sub(pattern, replacement, content)

agent_file.write_text(content, encoding="utf-8")
print("修改完成")
```

## 问题排查

### LLM 调试文件

当遇到 LLM 相关问题（执行不成功、上下文超限、模型切换失败、工具调用失败等）时：

1. **查看调试文件**: `data/llm_debug/` 目录保存了每次 LLM 请求的完整内容
2. **文件内容**: 包含 system prompt、messages、tools 等完整信息
3. **文件命名**: `llm_request_YYYYMMDD_HHMMSS_xxx.json`
4. **保留时间**: 调试文件自动保留 3 天，过期自动清理

### 排查步骤

1. 找到对应时间的调试文件
2. 检查 `system_prompt` 是否正确
3. 检查 `messages` 中是否有异常数据（如过大的 base64、旧的上下文等）
4. 检查 `messages_count` 是否合理

---

## 系统工具注册机制

> **注意**：这是**内置系统工具**的注册机制。**外部 Skills（技能）** 是另一套独立机制，见下方 [Skills 技能系统](#skills-技能系统) 章节。

### 架构概述

系统工具采用 **Handler 分组注册** 模式：

```
agent.py                    handlers/xxx.py
├── handler_registry  ←───  ├── XxxHandler
│   ├── filesystem          │   └── TOOLS = [...]
│   ├── browser             │   └── handle(tool_name, params)
│   ├── scheduled           │
│   ├── im_channel          │
│   └── ...                 └── create_handler(agent)
```

### 关键文件

| 文件 | 作用 |
|------|------|
| `src/openakita/core/agent.py` | 第 390-471 行：`_init_system_handlers()` 注册所有 handler |
| `src/openakita/tools/handlers/*.py` | 各 handler 实现，每个文件定义 `TOOLS` 列表 |
| `src/openakita/tools/handlers/__init__.py` | `SystemHandlerRegistry` 路由实现 |

### 新增工具步骤

1. **在 handler 文件中**：
   - 在 `TOOLS` 列表中添加工具名
   - 在 `handle()` 方法中添加分支处理
   - 实现具体的 `_xxx()` 方法

2. **在 agent.py 中**：
   - 找到对应 handler 的 `register()` 调用
   - 将新工具名添加到工具列表参数中

3. **在 catalog.py 中**：
   - 在 `_build_system_tools()` 中添加工具定义（供 LLM 识别）

### 常见错误

**"Unknown system tool: xxx"**：
- 原因：工具在 handler 的 `TOOLS` 中定义了，但没有在 `agent.py` 的 `register()` 调用中注册
- 修复：在 `agent.py` 对应 handler 的注册列表中添加该工具

### Handler 工具映射表（当前状态）

| Handler | 工具列表 |
|---------|---------|
| filesystem | run_shell, write_file, read_file, list_directory |
| memory | add_memory, search_memory, get_memory_stats |
| browser | browser_task, browser_open, browser_status, browser_navigate, browser_click, browser_type, browser_get_content, browser_screenshot, browser_list_tabs, browser_switch_tab, browser_new_tab |
| scheduled | schedule_task, list_scheduled_tasks, cancel_scheduled_task, update_scheduled_task, trigger_scheduled_task |
| mcp | list_mcp_servers, get_mcp_instructions, call_mcp_tool |
| profile | get_user_profile, update_user_profile, skip_profile_question |
| system | get_tool_info, get_session_logs, enable_thinking |
| im_channel | send_to_chat, get_voice_file, get_image_file, get_chat_history |
| skills | list_skills, get_skill_info, run_skill_script, get_skill_reference, install_skill, load_skill, reload_skill |
| desktop | desktop_screenshot, desktop_find_element, desktop_click, desktop_type, desktop_hotkey, desktop_scroll, desktop_window, desktop_wait, desktop_inspect |

---

## Skills 技能系统

> **外部技能**：通过 SKILL.md 规范定义，独立于系统工具，支持热加载和动态扩展。

### 与系统工具的区别

| 特性 | 系统工具 (System Tools) | 外部技能 (Skills) |
|------|------------------------|------------------|
| 定义方式 | 代码中硬编码 | SKILL.md + 脚本文件 |
| 存放位置 | `src/openakita/tools/handlers/` | `skills/` 目录 |
| 注册机制 | handler_registry | SkillRegistry + SkillLoader |
| 热加载 | 需重启 | 支持 `load_skill`/`reload_skill` |
| 适用场景 | 核心功能（浏览器、文件系统等） | 可扩展功能（特定任务、自定义工具） |

### Skills 关键文件

| 文件 | 作用 |
|------|------|
| `src/openakita/skills/loader.py` | 技能加载器，解析 SKILL.md |
| `src/openakita/skills/registry.py` | 技能注册表，管理已加载技能 |
| `src/openakita/skills/catalog.py` | 生成技能清单供 LLM 识别 |
| `skills/` | 技能存放目录（每个子目录一个技能） |

### 技能目录结构

```
skills/
├── system/               # 系统工具 skill（纳入 Git 版本控制）
│   ├── run-shell/
│   ├── browser-click/
│   └── ...              # 51 个系统工具
└── my-external-skill/    # 外部 skill（被 .gitignore 忽略）
    ├── SKILL.md          # 技能描述文件（必需）
    └── scripts/
        └── main.py       # 执行脚本
```

**注意**: `skills/system/` 目录存放系统工具的 SKILL.md 文档，会被推送到 Git。
其他外部/用户 skill 被 `.gitignore` 忽略。

### 新增技能步骤

1. 在 `skills/` 下创建目录（如 `skills/my-skill/`）
2. 编写 `SKILL.md` 描述技能用途、参数、使用方法
3. 编写执行脚本（放在 `scripts/` 子目录）
4. 使用 `load_skill` 工具加载，或重启程序自动加载

### 相关工具

- `list_skills` - 列出所有已加载技能
- `get_skill_info` - 获取技能详细信息
- `run_skill_script` - 执行技能脚本
- `load_skill` / `reload_skill` - 动态加载/重载技能
- `install_skill` - 从 Git URL 安装技能

---

## 其他规则

### 截断相关

- MEMORY.md 的 800 字符限制保留不修改
- UUID 截取 (`[:8]`, `[:12]`) 是 ID 生成，不是信息截断，保留
- Telegram 消息分割是平台 API 限制，保留

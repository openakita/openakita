# OpenAkita 工具定义规范

> 参考 Agent Skills 规范设计，统一内置工具的定义格式

## 概述

本规范定义了 OpenAkita Agent 内置工具的标准格式。遵循渐进式披露原则：

- **Level 1**: 工具清单（name + description）- 系统提示注入
- **Level 2**: 详细定义（detail + examples + schema）- `get_tool_info` 返回
- **Level 3**: 执行工具

## 工具定义格式

```python
{
    # === 必填字段 ===
    "name": "tool_name",           # 工具名称，snake_case
    "description": "...",          # 简短描述（Level 1）
    "input_schema": {...},         # JSON Schema 参数定义
    
    # === 推荐字段 ===
    "detail": "...",               # 详细说明（Level 2）
    "triggers": [...],             # 触发条件列表
    "prerequisites": [...],        # 前置条件列表
    "examples": [...],             # 使用示例
    
    # === 可选字段 ===
    "category": "...",             # 工具分类
    "warnings": [...],             # 重要警告
    "related_tools": [...],        # 相关工具
    "workflow": {...},             # 工作流定义
}
```

## 字段规范

### 1. name (必填)

工具唯一标识符。

**规则**：
- 使用 snake_case 命名
- 最大 64 字符
- 只能包含小写字母、数字、下划线
- 同一类别工具使用统一前缀（如 `browser_*`, `desktop_*`）

**示例**：
```python
"name": "browser_navigate"
"name": "desktop_screenshot"
"name": "schedule_task"
```

### 2. description (必填)

简短描述，用于工具清单（Level 1 披露）。

**规则**：
- 最大 200 字符
- 使用第三人称
- 包含 WHAT（做什么）和 WHEN（什么时候用）
- 包含关键触发词

**格式模板**：
```
{动作描述}. When you need to: (1) {场景1}, (2) {场景2}. {重要警告}
```

**✅ Good**：
```python
"description": "Navigate browser to specified URL. When you need to: (1) Open a webpage, (2) Start web interaction. PREREQUISITE: Auto-starts browser if not running."
```

**❌ Bad**：
```python
"description": "这个工具可以帮你打开网页"  # 不是第三人称，没有场景
"description": "Navigate to URL"           # 太简短，没有使用场景
```

### 3. input_schema (必填)

JSON Schema 格式的参数定义。

**规则**：
- 类型必须为 `"object"`
- 每个参数必须有 `type` 和 `description`
- 必填参数在 `required` 数组中列出
- 可选参数提供 `default` 值

**示例**：
```python
"input_schema": {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "要访问的 URL"
        },
        "wait_load": {
            "type": "boolean",
            "description": "是否等待页面加载完成",
            "default": True
        }
    },
    "required": ["url"]
}
```

### 4. detail (推荐)

详细使用说明，用于 Level 2 披露。

**规则**：
- 使用 Markdown 格式
- 包含使用场景、参数说明、注意事项
- 控制在 500 字以内

**模板**：
```markdown
{功能简述}

**适用场景**：
- 场景 1
- 场景 2

**参数说明**：
- param1: 说明
- param2: 说明

**注意事项**：
- 注意 1
- 注意 2
```

### 5. triggers (推荐)

明确的触发条件列表，帮助 Agent 判断何时使用此工具。

**格式**：
```python
"triggers": [
    "When user asks to open a webpage",
    "When starting web automation task",
    "Before browser_click or browser_type operations"
]
```

### 6. prerequisites (推荐)

前置条件列表，明确工具的依赖关系。

**格式**：
```python
"prerequisites": [
    {
        "condition": "Browser must be running",
        "check_tool": "browser_status",
        "action_if_not_met": "Call browser_open first"
    }
]
```

**简化格式**：
```python
"prerequisites": [
    "Browser must be running (check with browser_status)",
    "Target page must be loaded (call browser_navigate first)"
]
```

### 7. examples (推荐)

使用示例，展示典型调用方式。

**格式**：
```python
"examples": [
    {
        "scenario": "打开 Google 首页",
        "params": {"url": "https://www.google.com"},
        "expected": "Browser navigates to Google homepage"
    },
    {
        "scenario": "打开本地文件",
        "params": {"url": "file:///C:/Users/test.html"},
        "expected": "Browser opens local HTML file"
    }
]
```

### 8. warnings (可选)

重要警告信息，会在 description 中高亮显示。

**格式**：
```python
"warnings": [
    "Must check browser_status before any browser task",
    "Never assume browser is open from conversation history"
]
```

### 9. related_tools (可选)

相关工具列表，帮助 Agent 发现关联功能。

**格式**：
```python
"related_tools": [
    {"name": "browser_status", "relation": "should check before"},
    {"name": "browser_click", "relation": "commonly used after"},
    {"name": "desktop_screenshot", "relation": "alternative for desktop"}
]
```

### 10. workflow (可选)

工作流定义，用于复杂多步骤操作。

**格式**：
```python
"workflow": {
    "name": "Web Automation Flow",
    "steps": [
        {"step": 1, "action": "Check browser status", "tool": "browser_status"},
        {"step": 2, "action": "Open browser if needed", "tool": "browser_open", "condition": "if browser not running"},
        {"step": 3, "action": "Navigate to URL", "tool": "browser_navigate"},
        {"step": 4, "action": "Interact with page", "tools": ["browser_click", "browser_type"]}
    ]
}
```

## 工具分类

使用 `category` 字段或按文件组织：

| 分类 | 前缀 | 说明 |
|------|------|------|
| File System | `run_shell`, `read_file`, `write_file` | 文件和命令操作 |
| Browser | `browser_*` | 浏览器自动化 |
| Desktop | `desktop_*` | Windows 桌面自动化 |
| Memory | `add_memory`, `search_memory` | 长期记忆管理 |
| Skills | `list_skills`, `get_skill_info` | 技能管理 |
| Scheduled | `schedule_task`, `list_scheduled_tasks` | 定时任务 |
| IM Channel | `send_to_chat`, `get_voice_file` | IM 通道交互 |
| Profile | `update_user_profile` | 用户档案 |
| System | `enable_thinking`, `get_session_logs` | 系统功能 |
| MCP | `call_mcp_tool`, `list_mcp_servers` | MCP 协议 |

## 完整示例

```python
{
    "name": "browser_navigate",
    "category": "Browser",
    "description": "Navigate browser to specified URL to open a webpage. When you need to: (1) Open a webpage, (2) Start web interaction. PREREQUISITE: Must call before browser_click/type. Auto-starts browser if not running.",
    "detail": """导航到指定 URL。

**适用场景**：
- 打开网页开始交互
- Web 自动化任务的第一步

**参数说明**：
- url: 完整的 URL（包含协议）

**注意事项**：
- 必须在 browser_click/browser_type 之前调用
- 如果浏览器未启动会自动启动""",
    
    "triggers": [
        "When user asks to open a webpage",
        "When starting web automation task",
        "Before any browser interaction (click/type)"
    ],
    
    "prerequisites": [
        "None - auto-starts browser if needed"
    ],
    
    "warnings": [
        "Must call before browser_click/type operations"
    ],
    
    "examples": [
        {
            "scenario": "打开搜索引擎",
            "params": {"url": "https://www.google.com"},
            "expected": "Browser opens Google homepage"
        },
        {
            "scenario": "打开项目文档",
            "params": {"url": "https://docs.example.com/api"},
            "expected": "Browser navigates to API documentation"
        }
    ],
    
    "related_tools": [
        {"name": "browser_status", "relation": "check before for reliability"},
        {"name": "browser_click", "relation": "commonly used after"},
        {"name": "browser_type", "relation": "commonly used after"}
    ],
    
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要访问的 URL（必须包含协议，如 https://）"
            }
        },
        "required": ["url"]
    }
}
```

## 验证规则

工具定义应通过以下验证：

1. **必填字段检查**：name, description, input_schema 必须存在
2. **命名规范**：name 符合 snake_case
3. **描述长度**：description ≤ 200 字符，detail ≤ 2000 字符
4. **Schema 有效性**：input_schema 是有效的 JSON Schema
5. **示例有效性**：examples 中的 params 符合 input_schema

## 迁移指南

从旧格式迁移到新格式：

1. 将 description 中的 "When you need to" 部分提取到 `triggers`
2. 将 description 中的 "PREREQUISITE/IMPORTANT" 提取到 `prerequisites` 和 `warnings`
3. 将 detail 中的 "使用方法/示例" 提取到 `examples`
4. 添加 `related_tools` 表明工具间关系

## 与 Skill 规范的对应关系

| Skill 规范 | 工具规范 | 说明 |
|-----------|---------|------|
| SKILL.md frontmatter | name, description | 元数据 |
| SKILL.md body | detail | 详细说明 |
| references/ | related_tools | 相关资源 |
| scripts/ | (执行器实现) | 执行逻辑 |
| examples.md | examples | 使用示例 |
| Progressive disclosure | Level 1/2/3 | 渐进披露 |

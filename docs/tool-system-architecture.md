# 工具系统架构设计

本文档描述 OpenAkita 的工具系统架构设计，包括三类工具的统一管理和渐进式披露机制。

## 架构概览

OpenAkita 支持三类工具，采用统一的架构设计：

| 工具类型 | 披露方式 | 规范依据 |
|---------|---------|---------|
| 系统工具 | 渐进式披露 | 参考 Agent Skills 规范 |
| Skills | 渐进式披露 | Agent Skills 规范 |
| MCP | 全量暴露 | MCP 规范要求 |

```
┌─────────────────────────────────────────────────────────────┐
│                     系统提示词 (Level 1)                      │
├─────────────────┬─────────────────┬─────────────────────────┤
│   系统工具清单   │   Skills 清单   │      MCP 清单           │
│ name + 简短描述  │ name + 简短描述 │    全量工具定义          │
└────────┬────────┴────────┬────────┴────────────┬────────────┘
         │                 │                      │
         ▼                 ▼                      │
┌─────────────────┬─────────────────┐             │
│  get_tool_info  │ get_skill_info  │ Level 2    │
│  获取完整参数    │  获取完整指令   │             │
└────────┬────────┴────────┬────────┘             │
         │                 │                      │
         ▼                 ▼                      ▼
┌─────────────────┬─────────────────┬─────────────────────────┐
│   直接执行工具   │ run_skill_script│    call_mcp_tool       │
│    Level 3      │    Level 3     │       Level 3           │
└─────────────────┴─────────────────┴─────────────────────────┘
```

## 一、系统工具

### 1.1 渐进式披露

系统工具采用三级渐进式披露：

**Level 1: 工具清单**
- 在系统提示词中提供
- 只包含 `name` + `short_description`（30-80 字符）
- 按类别分组展示

**Level 2: 完整定义**
- 通过 `get_tool_info(tool_name)` 获取
- 包含完整 `description` 和 `input_schema`
- 包含使用场景、注意事项、示例

**Level 3: 执行**
- 直接调用工具，传入参数

### 1.2 工具描述规范

每个系统工具需要两级描述：

```python
{
    "name": "tool_name",
    "short_description": "简短描述（30-80字符，用于清单）",
    "description": """完整描述。

**使用场景**：
- 场景 1
- 场景 2

**注意**：重要注意事项（如有）

**示例**：
tool_name(param="value")""",
    "input_schema": {
        "type": "object",
        "properties": {...},
        "required": [...]
    }
}
```

### 1.3 工具分类

| 类别 | 工具 | 用途 |
|-----|-----|-----|
| File System | run_shell, read_file, write_file, list_directory | 文件和命令操作 |
| Browser | browser_task, browser_open, browser_navigate, browser_get_content, browser_screenshot, browser_close | 网页自动化 |
| Memory | add_memory, search_memory, get_memory_stats | 长期记忆 |
| Scheduled Tasks | schedule_task, list_scheduled_tasks, ... | 定时任务和提醒 |
| IM Channel | deliver_artifacts, get_voice_file, get_image_file, ... | IM 消息处理（附件交付以回执为证据） |
| User Profile | update_user_profile, get_user_profile, ... | 用户档案管理 |
| System | enable_thinking, get_session_logs, get_tool_info | 系统控制 |

### 1.4 核心组件

**ToolCatalog 类** (`src/openakita/tools/catalog.py`)

```python
class ToolCatalog:
    """系统工具目录"""
    
    def generate_catalog(self) -> str:
        """生成工具清单（Level 1）"""
        ...
    
    def get_tool_info(self, tool_name: str) -> dict:
        """获取工具完整定义（Level 2）"""
        ...
    
    def get_tool_info_formatted(self, tool_name: str) -> str:
        """获取格式化的工具信息"""
        ...
```

## 二、Skills 技能系统

### 2.1 渐进式披露

Skills 遵循 Agent Skills 规范（agentskills.io），采用三级渐进式披露：

**Level 1: 技能清单**
- 在系统提示词中提供
- 只包含 `name` + `description`（简短描述）

**Level 2: 完整指令**
- 通过 `get_skill_info(skill_name)` 获取
- 返回 SKILL.md 的完整 body

**Level 3: 资源文件**
- 通过 `run_skill_script()` 执行脚本
- 通过 `get_skill_reference()` 获取参考文档

### 2.2 SKILL.md 格式

```yaml
---
name: skill-name
description: 技能描述
license: MIT
compatibility: python>=3.10
---

# 技能使用说明

详细的使用指令...
```

### 2.3 相关工具

| 工具 | 用途 |
|-----|-----|
| list_skills | 列出已安装技能 |
| get_skill_info | 获取技能详细信息 |
| run_skill_script | 执行技能脚本 |
| get_skill_reference | 获取参考文档 |
| install_skill | 安装技能 |

## 三、MCP 外部服务

### 3.1 全量暴露

MCP 遵循 Model Context Protocol 规范，工具定义在系统提示词中**全量展示**：

```
## MCP Servers (Model Context Protocol)

### server-name (`server-id`)
- **tool_1**: 完整描述
  - `param1` (string, required): 参数说明
  - `param2` (number, optional): 参数说明
- **tool_2**: 完整描述
  ...
```

### 3.2 相关工具

| 工具 | 用途 |
|-----|-----|
| call_mcp_tool | 调用 MCP 服务器的工具 |
| list_mcp_servers | 列出配置的 MCP 服务器 |
| get_mcp_instructions | 获取服务器使用说明 |

### 3.3 MCP 配置

MCP 服务器配置存放在 `mcps/` 目录：

```
mcps/
├── my-server/
│   ├── SERVER_METADATA.json
│   ├── INSTRUCTIONS.md
│   └── tools/
│       ├── tool1.json
│       └── tool2.json
```

## 四、系统提示词结构

系统提示词按以下结构组织：

```
1. 身份信息 (Identity)
2. 运行环境信息
3. 系统工具清单 (ToolCatalog)
4. Skills 清单 (SkillCatalog)
5. MCP 清单 (MCPCatalog)
6. 相关记忆
7. 工具使用指南
8. 核心原则
```

### 工具使用指南

```
## 工具体系说明

你有三类工具可以使用，它们都是工具，都可以调用：

### 1. 系统工具（渐进式披露）
- 查看 "Available System Tools" 清单
- 使用 get_tool_info(tool_name) 获取完整参数
- 直接调用工具

### 2. Skills 技能（渐进式披露）
- 查看 "Available Skills" 清单
- 使用 get_skill_info(skill_name) 获取详细说明
- 使用 run_skill_script() 执行脚本

### 3. MCP 外部服务（全量暴露）
- 查看 "MCP Servers" 清单（包含完整定义）
- 使用 call_mcp_tool(server, tool_name, arguments) 调用

### 工具选择原则
1. 系统工具：文件操作、命令执行、浏览器、记忆等基础能力
2. Skills：复杂任务、特定领域能力、可复用的工作流
3. MCP：外部服务集成（数据库、第三方 API）
4. 找不到工具？用 skill-creator 创建一个！
```

## 五、实现细节

### 5.1 文件结构

```
src/openakita/
├── tools/
│   ├── catalog.py      # ToolCatalog - 系统工具目录
│   ├── mcp_catalog.py  # MCPCatalog - MCP 目录
│   ├── shell.py        # ShellTool
│   ├── file.py         # FileTool
│   ├── web.py          # WebTool
│   ├── mcp.py          # MCPClient
│   └── browser_mcp.py  # BrowserMCP
├── skills/
│   ├── catalog.py      # SkillCatalog - 技能目录
│   ├── registry.py     # SkillRegistry
│   ├── loader.py       # SkillLoader
│   └── parser.py       # SKILL.md 解析器
└── core/
    └── agent.py        # Agent 主类
```

### 5.2 Agent 初始化流程

```python
async def initialize(self):
    # 1. 加载系统工具
    self.tool_catalog = ToolCatalog(self.BASE_TOOLS)
    
    # 2. 加载 Skills
    await self._load_installed_skills()
    
    # 3. 加载 MCP 服务器
    await self._load_mcp_servers()
    
    # 4. 构建系统提示词
    self._context.system = self._build_system_prompt()
```

### 5.3 工具执行流程

```python
async def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
    # 系统工具
    if tool_name == "get_tool_info":
        return self.tool_catalog.get_tool_info_formatted(tool_input["tool_name"])
    
    # Skills 工具
    elif tool_name == "get_skill_info":
        return self._get_skill_info(tool_input["skill_name"])
    
    # MCP 工具
    elif tool_name == "call_mcp_tool":
        return await self.mcp_client.call_tool(...)
    
    # 其他系统工具
    elif tool_name in self.tool_catalog.list_tools():
        return await self._execute_system_tool(tool_name, tool_input)
```

## 六、设计原则

1. **统一性**：三类工具采用一致的架构模式
2. **渐进式披露**：减少初始 token 消耗，按需加载详情
3. **可扩展性**：支持动态添加工具、技能、MCP 服务器
4. **规范遵循**：Skills 遵循 Agent Skills 规范，MCP 遵循 MCP 规范
5. **向后兼容**：保持现有工具的兼容性

## 七、相关文档

- [Skills 系统文档](skills.md)
- [MCP 集成文档](mcp-integration.md)
- [配置文档](configuration.md)

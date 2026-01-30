"""
Agent 主类 - 协调所有模块

这是 MyAgent 的核心，负责:
- 接收用户输入
- 协调各个模块
- 执行工具调用
- 执行 Ralph 循环
- 管理对话和记忆
- 自我进化（技能搜索、安装、生成）

Skills 系统遵循 Agent Skills 规范 (agentskills.io)
MCP 系统遵循 Model Context Protocol 规范 (modelcontextprotocol.io)
"""

import logging
import uuid
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .brain import Brain, Context, Response
from .identity import Identity
from .ralph import RalphLoop, Task, TaskResult, TaskStatus

from ..config import settings
from ..tools.shell import ShellTool
from ..tools.file import FileTool
from ..tools.web import WebTool

# 技能系统 (SKILL.md 规范)
from ..skills import SkillRegistry, SkillLoader, SkillEntry, SkillCatalog

# MCP 系统
from ..tools.mcp import MCPClient, mcp_client
from ..tools.mcp_catalog import MCPCatalog

# 记忆系统
from ..memory import MemoryManager

logger = logging.getLogger(__name__)


class Agent:
    """
    MyAgent 主类
    
    一个全能自进化AI助手，基于 Ralph Wiggum 模式永不放弃。
    """
    
    # 基础工具定义 (Claude API tool use format)
    BASE_TOOLS = [
        # === 文件系统工具 ===
        {
            "name": "run_shell",
            "description": "执行Shell命令，用于运行系统命令、创建目录、执行脚本等",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的Shell命令"},
                    "cwd": {"type": "string", "description": "工作目录(可选)"}
                },
                "required": ["command"]
            }
        },
        {
            "name": "write_file",
            "description": "写入文件内容，可以创建新文件或覆盖已有文件",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "文件内容"}
                },
                "required": ["path", "content"]
            }
        },
        {
            "name": "read_file",
            "description": "读取文件内容",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "list_directory",
            "description": "列出目录内容",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径"}
                },
                "required": ["path"]
            }
        },
        # === Skills 工具 (SKILL.md 规范) ===
        {
            "name": "list_skills",
            "description": "列出已安装的技能 (遵循 Agent Skills 规范)",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "get_skill_info",
            "description": "获取技能的详细信息和指令",
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "技能名称"}
                },
                "required": ["skill_name"]
            }
        },
        {
            "name": "run_skill_script",
            "description": "运行技能的脚本",
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "技能名称"},
                    "script_name": {"type": "string", "description": "脚本文件名 (如 get_time.py)"},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "命令行参数"}
                },
                "required": ["skill_name", "script_name"]
            }
        },
        {
            "name": "get_skill_reference",
            "description": "获取技能的参考文档",
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "技能名称"},
                    "ref_name": {"type": "string", "description": "参考文档名称 (默认 REFERENCE.md)", "default": "REFERENCE.md"}
                },
                "required": ["skill_name"]
            }
        },
        # === 自进化工具 ===
        {
            "name": "generate_skill",
            "description": "自动生成新技能 (遵循 SKILL.md 规范)，当现有技能无法满足需求时使用",
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "技能功能的详细描述"},
                    "name": {"type": "string", "description": "技能名称 (可选，使用小写字母和连字符)"}
                },
                "required": ["description"]
            }
        },
        {
            "name": "improve_skill",
            "description": "根据反馈改进已有技能",
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "要改进的技能名称"},
                    "feedback": {"type": "string", "description": "改进建议或问题描述"}
                },
                "required": ["skill_name", "feedback"]
            }
        },
        # === 记忆工具 ===
        {
            "name": "add_memory",
            "description": "记录重要信息到长期记忆 (用于学习用户偏好、成功模式、错误教训等)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "要记住的内容"},
                    "type": {"type": "string", "enum": ["fact", "preference", "skill", "error", "rule"], "description": "记忆类型"},
                    "importance": {"type": "number", "description": "重要性 (0-1)", "default": 0.5}
                },
                "required": ["content", "type"]
            }
        },
        {
            "name": "search_memory",
            "description": "搜索相关记忆",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "type": {"type": "string", "enum": ["fact", "preference", "skill", "error", "rule"], "description": "记忆类型过滤 (可选)"}
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_memory_stats",
            "description": "获取记忆系统统计信息",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        # === 浏览器工具 (browser-use MCP) ===
        {
            "name": "browser_navigate",
            "description": "打开浏览器并导航到指定 URL",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要访问的 URL"}
                },
                "required": ["url"]
            }
        },
        {
            "name": "browser_click",
            "description": "点击页面上的元素",
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS 选择器"},
                    "text": {"type": "string", "description": "元素文本 (可选)"}
                }
            }
        },
        {
            "name": "browser_type",
            "description": "在输入框中输入文本",
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "输入框选择器"},
                    "text": {"type": "string", "description": "要输入的文本"}
                },
                "required": ["selector", "text"]
            }
        },
        {
            "name": "browser_get_content",
            "description": "获取页面内容 (文本)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "元素选择器 (可选)"}
                }
            }
        },
        {
            "name": "browser_screenshot",
            "description": "截取当前页面截图",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "保存路径 (可选)"}
                }
            }
        },
    ]
    
    def __init__(
        self,
        name: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.name = name or settings.agent_name
        
        # 初始化核心组件
        self.identity = Identity()
        self.brain = Brain(api_key=api_key)
        self.ralph = RalphLoop(
            max_iterations=settings.max_iterations,
            on_iteration=self._on_iteration,
            on_error=self._on_error,
        )
        
        # 初始化基础工具
        self.shell_tool = ShellTool()
        self.file_tool = FileTool()
        self.web_tool = WebTool()
        
        # 初始化技能系统 (SKILL.md 规范)
        self.skill_registry = SkillRegistry()
        self.skill_loader = SkillLoader(self.skill_registry)
        self.skill_catalog = SkillCatalog(self.skill_registry)
        
        # 延迟导入自进化系统（避免循环导入）
        from ..evolution.generator import SkillGenerator
        self.skill_generator = SkillGenerator(
            brain=self.brain,
            skills_dir=settings.skills_path,
            skill_registry=self.skill_registry,
        )
        
        # MCP 系统
        self.mcp_client = mcp_client
        self.mcp_catalog = MCPCatalog()
        self.browser_mcp = None  # 在 _start_builtin_mcp_servers 中启动
        self._builtin_mcp_count = 0
        
        # 记忆系统
        self.memory_manager = MemoryManager(
            data_dir=settings.project_root / "data" / "memory",
            memory_md_path=settings.memory_path,
            brain=self.brain,
        )
        
        # 动态工具列表（基础工具 + 技能工具）
        self._tools = list(self.BASE_TOOLS)
        
        # 对话上下文
        self._context = Context()
        self._conversation_history: list[dict] = []
        
        # 状态
        self._initialized = False
        self._running = False
        
        logger.info(f"Agent '{self.name}' created")
    
    async def initialize(self) -> None:
        """初始化 Agent"""
        if self._initialized:
            return
        
        # 加载身份文档
        self.identity.load()
        
        # 加载已安装的技能
        await self._load_installed_skills()
        
        # 加载 MCP 配置
        await self._load_mcp_servers()
        
        # 启动记忆会话
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(uuid.uuid4())[:8]
        self.memory_manager.start_session(session_id)
        self._current_session_id = session_id
        
        # 设置系统提示词 (包含技能清单、MCP 清单和相关记忆)
        base_prompt = self.identity.get_system_prompt()
        self._context.system = self._build_system_prompt(base_prompt)
        
        self._initialized = True
        logger.info(
            f"Agent '{self.name}' initialized with "
            f"{self.skill_registry.count} skills, "
            f"{self.mcp_catalog.server_count} MCP servers"
        )
    
    async def _load_installed_skills(self) -> None:
        """
        加载已安装的技能 (遵循 Agent Skills 规范)
        
        技能从以下目录加载:
        - skills/ (项目级别)
        - .cursor/skills/ (Cursor 兼容)
        """
        # 从所有标准目录加载
        loaded = self.skill_loader.load_all(settings.project_root)
        logger.info(f"Loaded {loaded} skills from standard directories")
        
        # 生成技能清单 (用于系统提示)
        self._skill_catalog_text = self.skill_catalog.generate_catalog()
        logger.info(f"Generated skill catalog with {self.skill_catalog.skill_count} skills")
        
        # 更新工具列表，添加技能工具
        self._update_skill_tools()
    
    def _update_skill_tools(self) -> None:
        """更新工具列表，添加技能相关工具"""
        # 基础工具已在 BASE_TOOLS 中定义
        # 这里可以添加动态生成的技能工具
        pass
    
    async def _load_mcp_servers(self) -> None:
        """
        加载 MCP 服务器配置
        
        只加载项目本地的 MCP，不加载 Cursor 的（因为无法实际调用）
        """
        # 只加载项目本地 MCP 目录
        possible_dirs = [
            settings.project_root / "mcps",
            settings.project_root / ".mcp",
        ]
        
        total_count = 0
        
        for dir_path in possible_dirs:
            if dir_path.exists():
                count = self.mcp_catalog.scan_mcp_directory(dir_path)
                if count > 0:
                    total_count += count
                    logger.info(f"Loaded {count} MCP servers from {dir_path}")
        
        # 启动内置 MCP 服务器
        await self._start_builtin_mcp_servers()
        
        if total_count > 0 or self._builtin_mcp_count > 0:
            self._mcp_catalog_text = self.mcp_catalog.generate_catalog()
            logger.info(f"Total MCP servers: {total_count + self._builtin_mcp_count}")
        else:
            self._mcp_catalog_text = ""
            logger.info("No MCP servers configured")
    
    async def _start_builtin_mcp_servers(self) -> None:
        """启动内置 MCP 服务器 (如 browser-use)"""
        self._builtin_mcp_count = 0
        
        # 启动浏览器 MCP
        try:
            from ..tools.browser_mcp import BrowserMCP
            self.browser_mcp = BrowserMCP()
            await self.browser_mcp.start()
            
            # 注册到 MCP catalog
            self.mcp_catalog.register_builtin_server(
                identifier="browser-use",
                name="Browser Use (Playwright)",
                tools=self.browser_mcp.get_tools(),
            )
            self._builtin_mcp_count += 1
            logger.info("Started builtin MCP: browser-use")
        except Exception as e:
            logger.warning(f"Failed to start browser-use MCP: {e}")
    
    def _build_system_prompt(self, base_prompt: str, task_description: str = "") -> str:
        """
        构建系统提示词 (包含技能清单、MCP 清单和相关记忆)
        
        遵循规范的渐进式披露:
        - Agent Skills: name + description 在系统提示中
        - MCP: server + tool name + description 在系统提示中
        - Memory: 相关记忆按需注入
        
        Args:
            base_prompt: 基础提示词 (身份信息)
            task_description: 任务描述 (用于检索相关记忆)
        
        Returns:
            完整的系统提示词
        """
        # 技能清单 (Agent Skills 规范)
        skill_catalog = getattr(self, '_skill_catalog_text', '')
        
        # MCP 清单 (Model Context Protocol 规范)
        mcp_catalog = getattr(self, '_mcp_catalog_text', '')
        
        # 相关记忆 (按任务相关性注入)
        memory_context = self.memory_manager.get_injection_context(task_description)
        
        return f"""{base_prompt}
{skill_catalog}
{mcp_catalog}
{memory_context}

## Built-in Tools

You have access to the following built-in tools:

### File System
- **run_shell**: Execute shell commands
- **write_file**: Write content to files
- **read_file**: Read file contents
- **list_directory**: List directory contents

### Skills Management
- **list_skills**: List all installed skills
- **get_skill_info**: Load full instructions for a skill (use when activating a skill)
- **run_skill_script**: Execute a skill's script
- **get_skill_reference**: Get reference documentation
- **generate_skill**: Create a new skill when existing ones don't meet the need
- **improve_skill**: Improve an existing skill based on feedback

### Memory Management
- **add_memory**: Save important information to long-term memory
- **search_memory**: Search for relevant memories
- **get_memory_stats**: Get memory system statistics

**Important**: 主动使用记忆功能！
- 学到新东西时，用 add_memory 记住
- 发现用户偏好时，记录为 preference
- 找到有效解决方案时，记录为 skill
- 遇到错误教训时，记录为 error
"""
    
    async def chat(self, message: str) -> str:
        """
        对话接口
        
        Args:
            message: 用户消息
        
        Returns:
            Agent 响应
        """
        if not self._initialized:
            await self.initialize()
        
        logger.info(f"User: {message[:100]}...")
        
        # 添加到对话历史
        self._conversation_history.append({
            "role": "user",
            "content": message,
            "timestamp": datetime.now().isoformat(),
        })
        
        # 记录到记忆系统 (自动提取重要信息)
        self.memory_manager.record_turn("user", message)
        
        # 更新上下文
        self._context.messages.append({
            "role": "user",
            "content": message,
        })
        
        # 判断是否是简单对话还是需要执行任务
        if self._is_task_request(message):
            # 作为任务执行（Ralph 模式）
            result = await self.execute_task_from_message(message)
            response_text = self._format_task_result(result)
        else:
            # 简单对话
            response = await self.brain.think(
                message,
                context=self._context,
            )
            response_text = response.content
        
        # 添加响应到历史
        self._conversation_history.append({
            "role": "assistant",
            "content": response_text,
            "timestamp": datetime.now().isoformat(),
        })
        
        # 更新上下文
        self._context.messages.append({
            "role": "assistant",
            "content": response_text,
        })
        
        # 记录到记忆系统
        self.memory_manager.record_turn("assistant", response_text)
        
        logger.info(f"Agent: {response_text[:100]}...")
        
        return response_text
    
    def _is_task_request(self, message: str) -> bool:
        """
        判断是否是需要执行工具的任务请求
        
        简单问答、代码解释、知识问题等不需要执行工具
        需要创建文件、运行命令、使用技能等操作的才是任务
        """
        # 排除纯问题（问号结尾且较短）
        if (message.strip().endswith("？") or message.strip().endswith("?")) and len(message) < 50:
            return False
        
        # 排除很短的消息
        if len(message) < 10:
            return False
        
        # 任务关键词（需要执行工具操作的）
        task_keywords = [
            # 文件操作
            "创建", "生成", "写入", "保存",
            "在目录", "在文件夹", "mkdir", "touch",
            "删除", "移动", "复制", "重命名",
            # 命令执行
            "运行", "执行", "启动", "部署",
            "下载", "安装", "pip install", "npm install",
            # 技能相关
            "技能", "skill", "搜索技能", "安装技能", "生成技能",
            # 项目相关
            "项目", "工程", "脚手架",
        ]
        
        # 检查是否匹配任务模式
        message_lower = message.lower()
        for kw in task_keywords:
            if kw in message or kw.lower() in message_lower:
                return True
        
        # 默认不是任务（简单对话）
        return False
    
    async def execute_task_from_message(self, message: str) -> TaskResult:
        """从消息创建并执行任务"""
        task = Task(
            id=str(uuid.uuid4())[:8],
            description=message,
            priority=1,
        )
        return await self.execute_task(task)
    
    async def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """
        执行工具调用
        
        Args:
            tool_name: 工具名称
            tool_input: 工具输入参数
        
        Returns:
            工具执行结果
        """
        logger.info(f"Executing tool: {tool_name} with {tool_input}")
        
        try:
            # === 基础文件系统工具 ===
            if tool_name == "run_shell":
                result = await self.shell_tool.run(
                    tool_input["command"],
                    cwd=tool_input.get("cwd")
                )
                if result.success:
                    return f"命令执行成功:\n{result.stdout}"
                else:
                    return f"命令执行失败:\n{result.stderr}"
            
            elif tool_name == "write_file":
                await self.file_tool.write(
                    tool_input["path"],
                    tool_input["content"]
                )
                return f"文件已写入: {tool_input['path']}"
            
            elif tool_name == "read_file":
                content = await self.file_tool.read(tool_input["path"])
                return f"文件内容:\n{content}"
            
            elif tool_name == "list_directory":
                files = await self.file_tool.list_dir(tool_input["path"])
                return f"目录内容:\n" + "\n".join(files)
            
            # === Skills 工具 (SKILL.md 规范) ===
            elif tool_name == "list_skills":
                skills = self.skill_registry.list_all()
                if not skills:
                    return "当前没有已安装的技能\n\n提示: 技能应放在 skills/ 目录下，每个技能是一个包含 SKILL.md 的文件夹"
                
                output = f"已安装 {len(skills)} 个技能 (遵循 Agent Skills 规范):\n\n"
                for skill in skills:
                    auto = "自动" if not skill.disable_model_invocation else "手动"
                    output += f"**{skill.name}** [{auto}]\n"
                    output += f"  {skill.description[:100]}{'...' if len(skill.description) > 100 else ''}\n\n"
                return output
            
            elif tool_name == "get_skill_info":
                skill_name = tool_input["skill_name"]
                skill = self.skill_registry.get(skill_name)
                
                if not skill:
                    return f"❌ 未找到技能: {skill_name}"
                
                # 获取完整的 body (Level 2)
                body = skill.get_body()
                
                output = f"# 技能: {skill.name}\n\n"
                output += f"**描述**: {skill.description}\n"
                if skill.license:
                    output += f"**许可证**: {skill.license}\n"
                if skill.compatibility:
                    output += f"**兼容性**: {skill.compatibility}\n"
                output += f"\n---\n\n"
                output += body or "(无详细指令)"
                
                return output
            
            elif tool_name == "run_skill_script":
                skill_name = tool_input["skill_name"]
                script_name = tool_input["script_name"]
                args = tool_input.get("args", [])
                
                success, output = self.skill_loader.run_script(
                    skill_name, script_name, args
                )
                
                if success:
                    return f"✅ 脚本执行成功:\n{output}"
                else:
                    return f"❌ 脚本执行失败:\n{output}"
            
            elif tool_name == "get_skill_reference":
                skill_name = tool_input["skill_name"]
                ref_name = tool_input.get("ref_name", "REFERENCE.md")
                
                content = self.skill_loader.get_reference(skill_name, ref_name)
                
                if content:
                    return f"# 参考文档: {ref_name}\n\n{content}"
                else:
                    return f"❌ 未找到参考文档: {skill_name}/{ref_name}"
            
            # === 自进化工具 ===
            elif tool_name == "generate_skill":
                description = tool_input["description"]
                name = tool_input.get("name")
                
                result = await self.skill_generator.generate(description, name)
                
                if result.success:
                    return f"""✅ 技能生成成功！

**名称**: {result.skill_name}
**目录**: {result.skill_dir}
**测试**: {'通过' if result.test_passed else '未通过'}

技能已自动加载，可以使用以下工具:
- `get_skill_info` 查看详细信息
- `run_skill_script` 运行脚本 (scripts/main.py)"""
                else:
                    return f"❌ 技能生成失败: {result.error or '未知错误'}"
            
            elif tool_name == "improve_skill":
                skill_name = tool_input["skill_name"]
                feedback = tool_input["feedback"]
                
                result = await self.skill_generator.improve(skill_name, feedback)
                
                if result.success:
                    return f"✅ 技能已改进: {skill_name}\n测试: {'通过' if result.test_passed else '未通过'}"
                else:
                    return f"❌ 技能改进失败: {result.error or '未知错误'}"
            
            # === 记忆工具 ===
            elif tool_name == "add_memory":
                from ..memory.types import Memory, MemoryType, MemoryPriority
                
                content = tool_input["content"]
                mem_type_str = tool_input["type"]
                importance = tool_input.get("importance", 0.5)
                
                # 类型映射
                type_map = {
                    "fact": MemoryType.FACT,
                    "preference": MemoryType.PREFERENCE,
                    "skill": MemoryType.SKILL,
                    "error": MemoryType.ERROR,
                    "rule": MemoryType.RULE,
                }
                mem_type = type_map.get(mem_type_str, MemoryType.FACT)
                
                # 根据重要性确定优先级
                if importance >= 0.8:
                    priority = MemoryPriority.PERMANENT
                elif importance >= 0.6:
                    priority = MemoryPriority.LONG_TERM
                else:
                    priority = MemoryPriority.SHORT_TERM
                
                memory = Memory(
                    type=mem_type,
                    priority=priority,
                    content=content,
                    source="manual",
                    importance_score=importance,
                )
                
                memory_id = self.memory_manager.add_memory(memory)
                if memory_id:
                    return f"✅ 已记住: [{mem_type_str}] {content[:100]}{'...' if len(content) > 100 else ''}\nID: {memory_id}"
                else:
                    return "⚠️ 记忆已存在或记录失败"
            
            elif tool_name == "search_memory":
                from ..memory.types import MemoryType
                
                query = tool_input["query"]
                type_filter = tool_input.get("type")
                
                mem_type = None
                if type_filter:
                    type_map = {
                        "fact": MemoryType.FACT,
                        "preference": MemoryType.PREFERENCE,
                        "skill": MemoryType.SKILL,
                        "error": MemoryType.ERROR,
                        "rule": MemoryType.RULE,
                    }
                    mem_type = type_map.get(type_filter)
                
                memories = self.memory_manager.search_memories(
                    query=query,
                    memory_type=mem_type,
                    limit=10
                )
                
                if not memories:
                    return f"未找到与 '{query}' 相关的记忆"
                
                output = f"找到 {len(memories)} 条相关记忆:\n\n"
                for m in memories:
                    output += f"- [{m.type.value}] {m.content}\n"
                    output += f"  (重要性: {m.importance_score:.1f}, 访问次数: {m.access_count})\n\n"
                
                return output
            
            elif tool_name == "get_memory_stats":
                stats = self.memory_manager.get_stats()
                
                output = f"""记忆系统统计:

- 总记忆数: {stats['total']}
- 今日会话: {stats['sessions_today']}
- 待处理会话: {stats['unprocessed_sessions']}

按类型:
"""
                for type_name, count in stats.get('by_type', {}).items():
                    output += f"  - {type_name}: {count}\n"
                
                output += "\n按优先级:\n"
                for priority, count in stats.get('by_priority', {}).items():
                    output += f"  - {priority}: {count}\n"
                
                return output
            
            # === 浏览器工具 (browser-use MCP) ===
            elif tool_name.startswith("browser_"):
                if not hasattr(self, 'browser_mcp') or not self.browser_mcp:
                    return "❌ 浏览器 MCP 未启动。请确保已安装 playwright: pip install playwright && playwright install chromium"
                
                result = await self.browser_mcp.call_tool(tool_name, tool_input)
                
                if result.get("success"):
                    return f"✅ {result.get('result', 'OK')}"
                else:
                    return f"❌ {result.get('error', '未知错误')}"
            
            else:
                return f"未知工具: {tool_name}"
                
        except Exception as e:
            logger.error(f"Tool execution error: {e}", exc_info=True)
            return f"工具执行错误: {str(e)}"
    
    async def execute_task(self, task: Task) -> TaskResult:
        """
        执行任务（带工具调用）
        
        Args:
            task: 任务对象
        
        Returns:
            TaskResult
        """
        if not self._initialized:
            await self.initialize()
        
        logger.info(f"Executing task: {task.description[:100]}...")
        
        # 使用已构建的系统提示词 (包含技能清单)
        # 技能清单已在初始化时注入到 _context.system 中
        system_prompt = self._context.system + """

## Task Execution Strategy

请使用工具来实际执行任务:

1. **Check skill catalog above** - 技能清单已在上方，根据描述判断是否有匹配的技能
2. **If skill matches**: Use `get_skill_info(skill_name)` to load full instructions
3. **Run script**: Use `run_skill_script(skill_name, script_name, args)`
4. **If no skill matches**: Use `generate_skill(description)` to create one

永不放弃，直到任务完成！"""

        messages = [{"role": "user", "content": task.description}]
        max_tool_iterations = 30
        iteration = 0
        final_response = ""
        
        while iteration < max_tool_iterations:
            iteration += 1
            logger.info(f"Task iteration {iteration}")
            
            # 调用 Brain
            response = self.brain.client.messages.create(
                model=self.brain.model,
                max_tokens=self.brain.max_tokens,
                system=system_prompt,
                tools=self._tools,
                messages=messages,
            )
            
            # 处理响应
            tool_calls = []
            text_content = ""
            
            for block in response.content:
                if block.type == "text":
                    text_content += block.text
                elif block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
            
            # 如果有文本响应，保存
            if text_content:
                final_response = text_content
            
            # 如果没有工具调用，任务完成
            if not tool_calls:
                break
            
            # 执行工具调用
            assistant_content = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
            
            messages.append({"role": "assistant", "content": assistant_content})
            
            # 执行每个工具并收集结果
            tool_results = []
            for tool_call in tool_calls:
                result = await self._execute_tool(tool_call["name"], tool_call["input"])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call["id"],
                    "content": result,
                })
                logger.info(f"Tool {tool_call['name']} result: {result[:100]}...")
            
            messages.append({"role": "user", "content": tool_results})
            
            # 检查是否应该停止
            if response.stop_reason == "end_turn":
                break
        
        task.mark_completed(final_response)
        
        return TaskResult(
            success=True,
            data=final_response,
            iterations=iteration,
        )
    
    def _format_task_result(self, result: TaskResult) -> str:
        """格式化任务结果"""
        if result.success:
            return f"""✅ 任务完成

{result.data}

---
迭代次数: {result.iterations}
耗时: {result.duration_seconds:.2f}秒"""
        else:
            return f"""❌ 任务未能完成

错误: {result.error}

---
尝试次数: {result.iterations}
耗时: {result.duration_seconds:.2f}秒

我会继续尝试其他方法..."""
    
    async def self_check(self) -> dict[str, Any]:
        """
        自检
        
        Returns:
            自检结果
        """
        logger.info("Running self-check...")
        
        results = {
            "timestamp": datetime.now().isoformat(),
            "status": "healthy",
            "checks": {},
        }
        
        # 检查 Brain
        try:
            response = await self.brain.think("你好，这是一个测试。请回复'OK'。")
            results["checks"]["brain"] = {
                "status": "ok" if "OK" in response.content or "ok" in response.content.lower() else "warning",
                "message": "Brain is responsive",
            }
        except Exception as e:
            results["checks"]["brain"] = {
                "status": "error",
                "message": str(e),
            }
            results["status"] = "unhealthy"
        
        # 检查 Identity
        try:
            soul = self.identity.soul
            agent = self.identity.agent
            results["checks"]["identity"] = {
                "status": "ok" if soul and agent else "warning",
                "message": f"SOUL.md: {len(soul)} chars, AGENT.md: {len(agent)} chars",
            }
        except Exception as e:
            results["checks"]["identity"] = {
                "status": "error",
                "message": str(e),
            }
        
        # 检查配置
        results["checks"]["config"] = {
            "status": "ok" if settings.anthropic_api_key else "error",
            "message": "API key configured" if settings.anthropic_api_key else "API key missing",
        }
        
        # 检查技能系统 (SKILL.md 规范)
        skill_count = self.skill_registry.count
        results["checks"]["skills"] = {
            "status": "ok",
            "message": f"已安装 {skill_count} 个技能 (Agent Skills 规范)",
            "count": skill_count,
            "skills": [s.name for s in self.skill_registry.list_all()],
        }
        
        # 检查技能目录
        skills_path = settings.skills_path
        results["checks"]["skills_dir"] = {
            "status": "ok" if skills_path.exists() else "warning",
            "message": str(skills_path),
        }
        
        # 检查 MCP 客户端
        mcp_servers = self.mcp_client.list_servers()
        mcp_connected = self.mcp_client.list_connected()
        results["checks"]["mcp"] = {
            "status": "ok",
            "message": f"配置 {len(mcp_servers)} 个服务器, 已连接 {len(mcp_connected)} 个",
            "servers": mcp_servers,
            "connected": mcp_connected,
        }
        
        logger.info(f"Self-check complete: {results['status']}")
        
        return results
    
    def _on_iteration(self, iteration: int, task: Task) -> None:
        """Ralph 循环迭代回调"""
        logger.debug(f"Ralph iteration {iteration} for task {task.id}")
    
    def _on_error(self, error: str, task: Task) -> None:
        """Ralph 循环错误回调"""
        logger.warning(f"Ralph error for task {task.id}: {error[:100]}")
    
    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._initialized
    
    @property
    def conversation_history(self) -> list[dict]:
        """对话历史"""
        return self._conversation_history.copy()
    
    # ==================== 记忆系统方法 ====================
    
    async def shutdown(self, task_description: str = "", success: bool = True, errors: list = None) -> None:
        """
        关闭 Agent 并保存记忆
        
        Args:
            task_description: 会话的主要任务描述
            success: 任务是否成功
            errors: 遇到的错误列表
        """
        logger.info("Shutting down agent...")
        
        # 结束记忆会话
        self.memory_manager.end_session(
            task_description=task_description,
            success=success,
            errors=errors or [],
        )
        
        # 同步到 MEMORY.md
        self.memory_manager.sync_to_memory_md()
        
        self._running = False
        logger.info("Agent shutdown complete")
    
    async def consolidate_memories(self) -> dict:
        """
        整理记忆 (批量处理未处理的会话)
        
        适合在空闲时段 (如凌晨) 由 cron job 调用
        
        Returns:
            整理结果统计
        """
        logger.info("Starting memory consolidation...")
        return await self.memory_manager.consolidate_daily()
    
    def get_memory_stats(self) -> dict:
        """获取记忆统计"""
        return self.memory_manager.get_stats()

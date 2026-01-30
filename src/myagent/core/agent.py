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

# 上下文管理常量
DEFAULT_MAX_CONTEXT_TOKENS = 180000  # Claude 3.5 Sonnet 默认上下文限制 (留 20k buffer)
CHARS_PER_TOKEN = 4  # 简单估算: 约 4 字符 = 1 token
MIN_RECENT_TURNS = 4  # 至少保留最近 4 轮对话
SUMMARY_TARGET_TOKENS = 500  # 摘要目标 token 数


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
            "name": "browser_open",
            "description": "启动浏览器。在执行任何浏览器操作前，先调用此工具决定是否让用户看到浏览器窗口。"
                           "如果任务需要用户观看操作过程、调试、或演示，设置 visible=True；"
                           "如果只是后台自动化任务（如抓取数据），设置 visible=False。"
                           "不确定时可以设置 ask_user=True 先询问用户偏好。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "visible": {
                        "type": "boolean", 
                        "description": "是否显示浏览器窗口。True=用户可见(调试/演示), False=后台运行(自动化)"
                    },
                    "ask_user": {
                        "type": "boolean",
                        "description": "是否先询问用户偏好。设为 True 时会返回提示让你询问用户"
                    }
                }
            }
        },
        {
            "name": "browser_navigate",
            "description": "导航到指定 URL (如果浏览器未启动，会自动以后台模式启动)",
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
        # === 定时任务工具 ===
        {
            "name": "schedule_task",
            "description": "创建定时任务。支持三种类型：once(一次性)、interval(间隔)、cron(cron表达式)。"
                           "任务会在指定时间自动执行，执行时会创建新的 Agent 会话来处理 prompt。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "任务名称"},
                    "description": {"type": "string", "description": "任务描述（用于理解任务目的）"},
                    "trigger_type": {
                        "type": "string",
                        "enum": ["once", "interval", "cron"],
                        "description": "触发类型：once=一次性，interval=间隔执行，cron=cron表达式"
                    },
                    "trigger_config": {
                        "type": "object",
                        "description": "触发配置。once: {run_at: '2026-02-01 10:00'}；interval: {interval_minutes: 30}；cron: {cron: '0 9 * * *'}"
                    },
                    "prompt": {"type": "string", "description": "执行时发送给 Agent 的提示（任务内容）"}
                },
                "required": ["name", "description", "trigger_type", "trigger_config", "prompt"]
            }
        },
        {
            "name": "list_scheduled_tasks",
            "description": "列出所有定时任务",
            "input_schema": {
                "type": "object",
                "properties": {
                    "enabled_only": {"type": "boolean", "description": "是否只列出启用的任务", "default": False}
                }
            }
        },
        {
            "name": "cancel_scheduled_task",
            "description": "取消定时任务",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "任务 ID"}
                },
                "required": ["task_id"]
            }
        },
        {
            "name": "trigger_scheduled_task",
            "description": "立即触发定时任务（不等待计划时间）",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "任务 ID"}
                },
                "required": ["task_id"]
            }
        },
        # === IM 通道工具 ===
        {
            "name": "send_to_chat",
            "description": "发送消息到当前 IM 聊天（仅在 IM 会话中可用）。"
                           "支持发送文本、图片、文件。"
                           "当你完成了生成文件（如截图、文档）的任务时，使用此工具将文件发送给用户。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要发送的文本消息（可选）"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "要发送的文件路径（图片、文档等）"
                    },
                    "caption": {
                        "type": "string",
                        "description": "文件的说明文字（可选）"
                    }
                }
            }
        },
    ]
    
    # 当前 IM 会话信息（由 chat_with_session 设置）
    _current_im_session = None
    _current_im_gateway = None
    
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
        
        # 定时任务调度器
        self.task_scheduler = None  # 在 initialize() 中启动
        
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
        
        # 启动定时任务调度器
        await self._start_scheduler()
        
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
        """启动内置服务 (如 browser-use)"""
        self._builtin_mcp_count = 0
        
        # 初始化浏览器服务 (作为内置工具，不是 MCP)
        # 注意: 不自动启动浏览器，由 browser_open 工具控制启动时机和模式
        try:
            from ..tools.browser_mcp import BrowserMCP
            self.browser_mcp = BrowserMCP(headless=True)  # 默认后台模式
            # 不在这里 await self.browser_mcp.start()，让 LLM 通过 browser_open 控制
            
            # 注意: 浏览器工具已在 BASE_TOOLS 中定义，不需要注册到 MCP catalog
            # 这样 LLM 就会直接使用 browser_navigate 等工具名，而不是 MCP 格式
            self._builtin_mcp_count += 1
            logger.info("Started builtin browser service (Playwright)")
        except Exception as e:
            logger.warning(f"Failed to start browser service: {e}")
    
    async def _start_scheduler(self) -> None:
        """启动定时任务调度器"""
        try:
            from ..scheduler import TaskScheduler
            from ..scheduler.executor import create_default_executor
            
            # 创建调度器
            self.task_scheduler = TaskScheduler(
                storage_path=settings.project_root / "data" / "scheduler",
                executor=create_default_executor(timeout_seconds=300),
            )
            
            # 启动调度器
            await self.task_scheduler.start()
            
            stats = self.task_scheduler.get_stats()
            logger.info(f"TaskScheduler started with {stats['total_tasks']} tasks")
            
        except Exception as e:
            logger.warning(f"Failed to start scheduler: {e}")
            self.task_scheduler = None
    
    def _build_system_prompt(self, base_prompt: str, task_description: str = "") -> str:
        """
        构建系统提示词 (动态生成，包含技能清单、MCP 清单和相关记忆)
        
        遵循规范的渐进式披露:
        - Agent Skills: name + description 在系统提示中
        - MCP: server + tool name + description 在系统提示中
        - Memory: 相关记忆按需注入
        - Tools: 从 BASE_TOOLS 动态生成
        
        Args:
            base_prompt: 基础提示词 (身份信息)
            task_description: 任务描述 (用于检索相关记忆)
        
        Returns:
            完整的系统提示词
        """
        # 技能清单 (Agent Skills 规范) - 每次动态生成，确保新创建的技能被包含
        skill_catalog = self.skill_catalog.generate_catalog()
        
        # MCP 清单 (Model Context Protocol 规范)
        mcp_catalog = getattr(self, '_mcp_catalog_text', '')
        
        # 相关记忆 (按任务相关性注入)
        memory_context = self.memory_manager.get_injection_context(task_description)
        
        # 动态生成工具列表
        tools_text = self._generate_tools_text()
        
        return f"""{base_prompt}
{skill_catalog}
{mcp_catalog}
{memory_context}

{tools_text}

## 重要提示

### 工具调用
- 工具直接使用工具名调用，不需要任何前缀

### 记忆管理 (非常重要!)
**主动使用记忆功能**，在以下情况必须调用 add_memory:
- 学到新东西时 → 记录为 FACT
- 发现用户偏好时 → 记录为 PREFERENCE  
- 找到有效解决方案时 → 记录为 SKILL
- 遇到错误教训时 → 记录为 ERROR
- 发现重要规则时 → 记录为 RULE

**记忆时机**:
1. 任务完成后，回顾学到了什么
2. 用户明确表达偏好时
3. 解决了一个难题时
4. 犯错后找到正确方法时
"""
    
    def _generate_tools_text(self) -> str:
        """
        从 BASE_TOOLS 动态生成工具列表文本
        
        按类别分组显示
        """
        # 工具分类
        categories = {
            "File System": ["run_shell", "write_file", "read_file", "list_directory"],
            "Skills Management": ["list_skills", "get_skill_info", "run_skill_script", "get_skill_reference", "generate_skill", "improve_skill"],
            "Memory Management": ["add_memory", "search_memory", "get_memory_stats"],
            "Browser Automation": ["browser_open", "browser_navigate", "browser_click", "browser_type", "browser_get_content", "browser_screenshot"],
            "Scheduled Tasks": ["schedule_task", "list_scheduled_tasks", "cancel_scheduled_task", "trigger_scheduled_task"],
        }
        
        # 构建工具名到描述的映射
        tool_map = {t["name"]: t["description"] for t in self._tools}
        
        lines = ["## Available Tools"]
        
        for category, tool_names in categories.items():
            # 过滤出存在的工具
            existing_tools = [(name, tool_map[name]) for name in tool_names if name in tool_map]
            
            if existing_tools:
                lines.append(f"\n### {category}")
                for name, desc in existing_tools:
                    # 截断过长的描述
                    short_desc = desc[:80] + "..." if len(desc) > 80 else desc
                    lines.append(f"- **{name}**: {short_desc}")
        
        # 添加未分类的工具
        categorized = set()
        for names in categories.values():
            categorized.update(names)
        
        uncategorized = [(t["name"], t["description"]) for t in self._tools if t["name"] not in categorized]
        if uncategorized:
            lines.append("\n### Other Tools")
            for name, desc in uncategorized:
                short_desc = desc[:80] + "..." if len(desc) > 80 else desc
                lines.append(f"- **{name}**: {short_desc}")
        
        return "\n".join(lines)
    
    # ==================== 上下文管理 ====================
    
    def _estimate_tokens(self, text: str) -> int:
        """
        估算文本的 token 数量
        
        简单估算: 约 4 字符 = 1 token (对中英文混合比较准确)
        """
        if not text:
            return 0
        return len(text) // CHARS_PER_TOKEN + 1
    
    def _estimate_messages_tokens(self, messages: list[dict]) -> int:
        """估算消息列表的 token 数量"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self._estimate_tokens(content)
            elif isinstance(content, list):
                # 处理复杂内容 (tool_use, tool_result 等)
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            total += self._estimate_tokens(item.get("text", ""))
                        elif item.get("type") == "tool_result":
                            total += self._estimate_tokens(str(item.get("content", "")))
                        elif item.get("type") == "tool_use":
                            total += self._estimate_tokens(json.dumps(item.get("input", {})))
                        # 图片等二进制内容单独计算
                        elif item.get("type") == "image":
                            total += 1000  # 图片固定估算
            total += 4  # 每条消息的格式开销
        return total
    
    async def _compress_context(self, messages: list[dict], max_tokens: int = None) -> list[dict]:
        """
        压缩对话上下文
        
        策略:
        1. 保留最近 MIN_RECENT_TURNS 轮对话
        2. 将早期对话摘要成简短描述
        3. 如果还是太长，逐步删除中间内容
        
        Args:
            messages: 消息列表
            max_tokens: 最大 token 数 (默认使用 DEFAULT_MAX_CONTEXT_TOKENS)
        
        Returns:
            压缩后的消息列表
        """
        max_tokens = max_tokens or DEFAULT_MAX_CONTEXT_TOKENS
        
        # 估算系统提示的 token
        system_tokens = self._estimate_tokens(self._context.system)
        available_tokens = max_tokens - system_tokens - 1000  # 留 1000 给响应
        
        current_tokens = self._estimate_messages_tokens(messages)
        
        # 如果没超过限制，直接返回
        if current_tokens <= available_tokens:
            return messages
        
        logger.info(f"Context too large ({current_tokens} tokens), compressing...")
        
        # 计算需要保留的最近对话数量 (user + assistant = 1 轮)
        recent_count = MIN_RECENT_TURNS * 2  # 4 轮 = 8 条消息
        
        if len(messages) <= recent_count:
            # 消息本身就不多，尝试截断长消息
            return self._truncate_long_messages(messages, available_tokens)
        
        # 分离早期消息和最近消息
        early_messages = messages[:-recent_count]
        recent_messages = messages[-recent_count:]
        
        # 尝试摘要早期对话
        summary = await self._summarize_messages(early_messages)
        
        # 构建压缩后的消息列表
        compressed = []
        
        if summary:
            compressed.append({
                "role": "user",
                "content": f"[之前的对话摘要]\n{summary}"
            })
            compressed.append({
                "role": "assistant", 
                "content": "好的，我已了解之前的对话内容，请继续。"
            })
        
        compressed.extend(recent_messages)
        
        # 检查压缩后的大小
        compressed_tokens = self._estimate_messages_tokens(compressed)
        
        if compressed_tokens <= available_tokens:
            logger.info(f"Compressed context from {current_tokens} to {compressed_tokens} tokens")
            return compressed
        
        # 还是太长，进一步截断
        logger.warning(f"Context still too large ({compressed_tokens} tokens), truncating further...")
        return self._truncate_long_messages(compressed, available_tokens)
    
    async def _summarize_messages(self, messages: list[dict]) -> str:
        """
        将消息列表摘要成简短描述
        
        使用 LLM 生成摘要
        """
        if not messages:
            return ""
        
        # 构建对话文本
        conversation_text = ""
        for msg in messages:
            role = "用户" if msg["role"] == "user" else "助手"
            content = msg.get("content", "")
            if isinstance(content, str):
                # 截断过长的内容
                if len(content) > 500:
                    content = content[:500] + "..."
                conversation_text += f"{role}: {content}\n"
            elif isinstance(content, list):
                # 复杂内容只保留文本部分
                texts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        texts.append(item.get("text", "")[:200])
                if texts:
                    conversation_text += f"{role}: {' '.join(texts)}\n"
        
        if not conversation_text:
            return ""
        
        try:
            # 使用 LLM 生成摘要
            response = self.brain.client.messages.create(
                model=self.brain.model,
                max_tokens=SUMMARY_TARGET_TOKENS,
                system="你是一个对话摘要助手。请用简洁的中文摘要以下对话的要点，只保留最重要的信息。",
                messages=[{
                    "role": "user",
                    "content": f"请摘要以下对话（200字以内）:\n\n{conversation_text}"
                }]
            )
            
            summary = ""
            for block in response.content:
                if block.type == "text":
                    summary += block.text
            
            return summary.strip()
            
        except Exception as e:
            logger.warning(f"Failed to summarize messages: {e}")
            # 回退: 简单截取
            return f"[早期对话共 {len(messages)} 条消息，内容已省略]"
    
    def _truncate_long_messages(self, messages: list[dict], max_tokens: int) -> list[dict]:
        """
        截断过长的消息内容
        
        策略: 保留消息结构，但截断过长的文本内容
        """
        truncated = []
        remaining_tokens = max_tokens
        
        # 从后往前处理，优先保留最近的消息
        for msg in reversed(messages):
            content = msg.get("content", "")
            msg_tokens = 0
            
            if isinstance(content, str):
                msg_tokens = self._estimate_tokens(content)
                if msg_tokens > remaining_tokens:
                    # 需要截断
                    max_chars = remaining_tokens * CHARS_PER_TOKEN
                    content = content[:max_chars] + "\n[内容过长已截断...]"
                    msg_tokens = remaining_tokens
            
            truncated.insert(0, {
                "role": msg["role"],
                "content": content
            })
            remaining_tokens -= msg_tokens
            
            if remaining_tokens <= 0:
                break
        
        return truncated
    
    async def chat(self, message: str, session_id: Optional[str] = None) -> str:
        """
        对话接口（使用全局会话历史）
        
        Args:
            message: 用户消息
            session_id: 可选的会话标识（用于日志）
        
        Returns:
            Agent 响应
        """
        if not self._initialized:
            await self.initialize()
        
        session_info = f"[{session_id}] " if session_id else ""
        logger.info(f"{session_info}User: {message[:100]}...")
        
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
        
        # 统一使用工具调用流程，让 LLM 自己决定是否需要工具
        response_text = await self._chat_with_tools(message)
        
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
        
        # 定期检查并压缩持久上下文（每 10 轮对话检查一次）
        if len(self._context.messages) > 20:
            current_tokens = self._estimate_messages_tokens(self._context.messages)
            if current_tokens > DEFAULT_MAX_CONTEXT_TOKENS * 0.7:  # 70% 阈值时预压缩
                logger.info(f"Proactively compressing persistent context ({current_tokens} tokens)")
                self._context.messages = await self._compress_context(self._context.messages)
        
        # 记录到记忆系统
        self.memory_manager.record_turn("assistant", response_text)
        
        logger.info(f"{session_info}Agent: {response_text[:100]}...")
        
        return response_text
    
    async def chat_with_session(
        self, 
        message: str, 
        session_messages: list[dict], 
        session_id: str = "",
        session: Any = None,
        gateway: Any = None,
    ) -> str:
        """
        使用外部 Session 历史进行对话（用于 IM 通道）
        
        与 chat() 不同，这里使用传入的 session_messages 作为对话上下文，
        而不是全局的 _conversation_history。
        
        Args:
            message: 用户消息
            session_messages: Session 的对话历史，格式 [{"role": "user/assistant", "content": "..."}]
            session_id: 会话 ID（用于日志）
            session: Session 对象（用于发送消息回 IM 通道）
            gateway: MessageGateway 对象（用于发送消息）
        
        Returns:
            Agent 响应
        """
        if not self._initialized:
            await self.initialize()
        
        # 保存当前 IM 会话信息（供 send_to_chat 工具使用）
        Agent._current_im_session = session
        Agent._current_im_gateway = gateway
        
        try:
            logger.info(f"[Session:{session_id}] User: {message[:100]}...")
            
            # 构建 API 消息格式（从 session_messages 转换）
            messages = []
            for msg in session_messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({
                        "role": role,
                        "content": content,
                    })
            
            # 添加当前用户消息
            messages.append({
                "role": "user",
                "content": message,
            })
            
            # 压缩上下文（如果需要）
            messages = await self._compress_context(messages)
            
            # 调用 Brain 处理（使用 session 的上下文）
            response_text = await self._chat_with_tools_and_context(messages)
            
            logger.info(f"[Session:{session_id}] Agent: {response_text[:100]}...")
            
            return response_text
        finally:
            # 清除 IM 会话信息
            Agent._current_im_session = None
            Agent._current_im_gateway = None
    
    async def _chat_with_tools_and_context(self, messages: list[dict], use_session_prompt: bool = True) -> str:
        """
        使用指定的消息上下文进行对话（支持工具调用）
        
        这是 _chat_with_tools 的变体，使用传入的 messages 而不是 self._context.messages
        
        Args:
            messages: 对话消息列表
            use_session_prompt: 是否使用 Session 专用的 System Prompt（不包含全局 Active Task）
        
        Returns:
            最终响应文本
        """
        max_iterations = 15  # 最多 15 轮工具调用
        
        # 复制消息避免修改原始列表
        working_messages = list(messages)
        
        # 选择 System Prompt
        if use_session_prompt:
            # 使用 Session 专用的 System Prompt，不包含全局 Active Task
            system_prompt = self.identity.get_session_system_prompt()
        else:
            system_prompt = self._context.system
        
        for iteration in range(max_iterations):
            # 每次迭代前检查上下文大小
            if iteration > 0:
                working_messages = await self._compress_context(working_messages)
            
            # 调用 Brain，传递工具列表
            response = self.brain.client.messages.create(
                model=self.brain.model,
                max_tokens=self.brain.max_tokens,
                system=system_prompt,
                tools=self._tools,
                messages=working_messages,
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
            
            # 如果没有工具调用，返回文本响应
            if not tool_calls:
                return text_content or "我理解了您的请求。"
            
            # 有工具调用，添加助手消息
            assistant_content = []
            if text_content:
                assistant_content.append({"type": "text", "text": text_content})
            for tc in tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })
            
            working_messages.append({
                "role": "assistant",
                "content": assistant_content,
            })
            
            # 执行工具调用
            tool_results = []
            for tc in tool_calls:
                try:
                    result = await self._execute_tool(tc["name"], tc["input"])
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": str(result) if result else "操作已完成",
                    })
                except Exception as e:
                    logger.error(f"Tool {tc['name']} error: {e}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": f"工具执行错误: {str(e)}",
                        "is_error": True,
                    })
            
            # 添加工具结果
            working_messages.append({
                "role": "user",
                "content": tool_results,
            })
        
        return "已达到最大工具调用次数，请重新描述您的需求。"
    
    async def _chat_with_tools(self, message: str) -> str:
        """
        对话处理，支持工具调用
        
        让 LLM 自己决定是否需要工具，不做硬编码判断
        
        Args:
            message: 用户消息
        
        Returns:
            最终响应文本
        """
        # 使用完整的对话历史（已包含当前用户消息）
        # 复制一份，避免工具调用的中间消息污染原始上下文
        messages = list(self._context.messages)
        
        # 检查并压缩上下文（如果接近限制）
        messages = await self._compress_context(messages)
        
        max_iterations = 15  # 最多 15 轮工具调用
        
        for iteration in range(max_iterations):
            # 每次迭代前检查上下文大小（工具调用可能产生大量输出）
            if iteration > 0:
                messages = await self._compress_context(messages)
            
            # 调用 Brain，传递工具列表
            response = self.brain.client.messages.create(
                model=self.brain.model,
                max_tokens=self.brain.max_tokens,
                system=self._context.system,
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
            
            # 如果没有工具调用，直接返回文本
            if not tool_calls:
                return text_content
            
            # 有工具调用，需要执行
            logger.info(f"Chat iteration {iteration + 1}, {len(tool_calls)} tool calls")
            
            # 构建 assistant 消息
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
            
            # 执行工具并收集结果
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
            
            # 检查是否结束
            if response.stop_reason == "end_turn":
                break
        
        # 返回最后一次的文本响应
        return text_content or "操作完成"
    
    async def execute_task_from_message(self, message: str) -> TaskResult:
        """从消息创建并执行任务"""
        task = Task(
            id=str(uuid.uuid4())[:8],
            description=message,
            session_id=getattr(self, '_current_session_id', None),  # 关联当前会话
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
            elif tool_name.startswith("browser_") or "browser_" in tool_name:
                if not hasattr(self, 'browser_mcp') or not self.browser_mcp:
                    return "❌ 浏览器 MCP 未启动。请确保已安装 playwright: pip install playwright && playwright install chromium"
                
                # 提取实际工具名 (处理 mcp__browser-use__browser_navigate 格式)
                actual_tool_name = tool_name
                if "browser_" in tool_name and not tool_name.startswith("browser_"):
                    # 提取 browser_xxx 部分
                    import re
                    match = re.search(r'(browser_\w+)', tool_name)
                    if match:
                        actual_tool_name = match.group(1)
                
                result = await self.browser_mcp.call_tool(actual_tool_name, tool_input)
                
                if result.get("success"):
                    return f"✅ {result.get('result', 'OK')}"
                else:
                    return f"❌ {result.get('error', '未知错误')}"
            
            # === 定时任务工具 ===
            elif tool_name == "schedule_task":
                if not hasattr(self, 'task_scheduler') or not self.task_scheduler:
                    return "❌ 定时任务调度器未启动"
                
                from ..scheduler import ScheduledTask, TriggerType
                
                trigger_type = TriggerType(tool_input["trigger_type"])
                task = ScheduledTask.create(
                    name=tool_input["name"],
                    description=tool_input["description"],
                    trigger_type=trigger_type,
                    trigger_config=tool_input["trigger_config"],
                    prompt=tool_input["prompt"],
                    user_id=getattr(self, '_current_session_id', None),
                )
                
                task_id = await self.task_scheduler.add_task(task)
                next_run = task.next_run.strftime('%Y-%m-%d %H:%M:%S') if task.next_run else '待计算'
                
                return f"✅ 定时任务已创建\n- ID: {task_id}\n- 名称: {task.name}\n- 下次执行: {next_run}"
            
            elif tool_name == "list_scheduled_tasks":
                if not hasattr(self, 'task_scheduler') or not self.task_scheduler:
                    return "❌ 定时任务调度器未启动"
                
                enabled_only = tool_input.get("enabled_only", False)
                tasks = self.task_scheduler.list_tasks(enabled_only=enabled_only)
                
                if not tasks:
                    return "当前没有定时任务"
                
                output = f"共 {len(tasks)} 个定时任务:\n\n"
                for t in tasks:
                    status = "✓" if t.enabled else "✗"
                    next_run = t.next_run.strftime('%m-%d %H:%M') if t.next_run else 'N/A'
                    output += f"[{status}] {t.name} ({t.id})\n"
                    output += f"    类型: {t.trigger_type.value}, 下次: {next_run}\n"
                
                return output
            
            elif tool_name == "cancel_scheduled_task":
                if not hasattr(self, 'task_scheduler') or not self.task_scheduler:
                    return "❌ 定时任务调度器未启动"
                
                task_id = tool_input["task_id"]
                success = await self.task_scheduler.remove_task(task_id)
                
                if success:
                    return f"✅ 任务 {task_id} 已取消"
                else:
                    return f"❌ 任务 {task_id} 不存在"
            
            elif tool_name == "trigger_scheduled_task":
                if not hasattr(self, 'task_scheduler') or not self.task_scheduler:
                    return "❌ 定时任务调度器未启动"
                
                task_id = tool_input["task_id"]
                execution = await self.task_scheduler.trigger_now(task_id)
                
                if execution:
                    status = "成功" if execution.status == "success" else "失败"
                    return f"✅ 任务已触发执行，状态: {status}\n结果: {execution.result or execution.error or 'N/A'}"
                else:
                    return f"❌ 任务 {task_id} 不存在"
            
            # === IM 通道工具 ===
            elif tool_name == "send_to_chat":
                # 检查是否在 IM 会话中
                if not Agent._current_im_session or not Agent._current_im_gateway:
                    return "❌ 此工具仅在 IM 会话中可用（当前不是 IM 会话）"
                
                session = Agent._current_im_session
                gateway = Agent._current_im_gateway
                
                text = tool_input.get("text", "")
                file_path = tool_input.get("file_path", "")
                caption = tool_input.get("caption", "")
                
                try:
                    from pathlib import Path
                    
                    # 发送文件
                    if file_path:
                        file_path_obj = Path(file_path)
                        if not file_path_obj.exists():
                            return f"❌ 文件不存在: {file_path}"
                        
                        # 获取适配器
                        adapter = gateway.get_adapter(session.channel)
                        if not adapter:
                            return f"❌ 找不到适配器: {session.channel}"
                        
                        # 根据文件类型发送
                        suffix = file_path_obj.suffix.lower()
                        
                        if suffix in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
                            # 发送图片
                            await adapter.send_photo(
                                chat_id=session.chat_id,
                                photo_path=str(file_path_obj),
                                caption=caption or text,
                            )
                            return f"✅ 图片已发送: {file_path}"
                        else:
                            # 发送文件
                            await adapter.send_file(
                                chat_id=session.chat_id,
                                file_path=str(file_path_obj),
                                caption=caption or text,
                            )
                            return f"✅ 文件已发送: {file_path}"
                    
                    # 只发送文本
                    elif text:
                        await gateway.send_to_session(session, text)
                        return f"✅ 消息已发送"
                    
                    else:
                        return "❌ 请提供要发送的内容（text 或 file_path）"
                        
                except Exception as e:
                    logger.error(f"send_to_chat error: {e}", exc_info=True)
                    return f"❌ 发送失败: {str(e)}"
            
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
            
            # 检查并压缩上下文（任务执行可能产生大量工具输出）
            if iteration > 1:
                messages = await self._compress_context(messages)
            
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

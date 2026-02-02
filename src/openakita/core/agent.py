"""
Agent 主类 - 协调所有模块

这是 OpenAkita 的核心，负责:
- 接收用户输入
- 协调各个模块
- 执行工具调用
- 执行 Ralph 循环
- 管理对话和记忆
- 自我进化（技能搜索、安装、生成）

Skills 系统遵循 Agent Skills 规范 (agentskills.io)
MCP 系统遵循 Model Context Protocol 规范 (modelcontextprotocol.io)
"""

import asyncio
import logging
import os
import uuid
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .brain import Brain, Context, Response
from .identity import Identity
from .ralph import RalphLoop, Task, TaskResult, TaskStatus
from .user_profile import UserProfileManager, get_profile_manager
from .task_monitor import TaskMonitor, TaskMetrics, RETROSPECT_PROMPT

from ..config import settings
from ..tools.shell import ShellTool
from ..tools.file import FileTool
from ..tools.web import WebTool

# 技能系统 (SKILL.md 规范)
from ..skills import SkillRegistry, SkillLoader, SkillEntry, SkillCatalog

# MCP 系统
from ..tools.mcp import MCPClient, mcp_client
from ..tools.mcp_catalog import MCPCatalog

# 系统工具目录（渐进式披露）
from ..tools.catalog import ToolCatalog

# 记忆系统
from ..memory import MemoryManager

logger = logging.getLogger(__name__)

# 上下文管理常量
DEFAULT_MAX_CONTEXT_TOKENS = 180000  # Claude 3.5 Sonnet 默认上下文限制 (留 20k buffer)
CHARS_PER_TOKEN = 4  # 简单估算: 约 4 字符 = 1 token
MIN_RECENT_TURNS = 4  # 至少保留最近 4 轮对话
SUMMARY_TARGET_TOKENS = 500  # 摘要目标 token 数

# Prompt Compiler 系统提示词（两段式 Prompt 第一阶段）
PROMPT_COMPILER_SYSTEM = """【角色】
你是 Prompt Compiler，不是解题模型。

【输入】
用户的原始请求。

【目标】
将请求转化为一个结构化、明确、可执行的任务定义。

【输出结构】
请用以下 YAML 格式输出：

```yaml
task_type: [任务类型: question/action/creation/analysis/reminder/other]
goal: [一句话描述任务目标]
inputs:
  given: [已提供的信息列表]
  missing: [缺失但可能需要的信息列表，如果没有则为空]
constraints: [约束条件列表，如果没有则为空]
output_requirements: [输出要求列表]
risks_or_ambiguities: [风险或歧义点列表，如果没有则为空]
```

【规则】
- 不要解决任务
- 不要给建议
- 不要输出最终答案
- 不要假设执行能力的限制（如"AI无法操作浏览器"等）
- 只输出 YAML 格式的结构化任务定义
- 保持简洁，每项不超过一句话

【示例】
用户: "帮我写一个Python脚本，读取CSV文件并统计每列的平均值"

输出:
```yaml
task_type: creation
goal: 创建一个读取CSV文件并计算各列平均值的Python脚本
inputs:
  given:
    - 需要处理的文件格式是CSV
    - 需要统计的是平均值
    - 使用Python语言
  missing:
    - CSV文件的路径或示例
    - 是否需要处理非数值列
output_requirements:
  - 可执行的Python脚本
  - 能够读取CSV文件
  - 输出每列的平均值
constraints: []
risks_or_ambiguities:
  - 未指定如何处理包含非数值数据的列
  - 未指定输出格式（打印到控制台还是保存到文件）
```"""

import re

def strip_thinking_tags(text: str) -> str:
    """
    移除响应中的内部标签内容
    
    需要清理的标签包括：
    - <thinking>...</thinking> - Claude extended thinking
    - <think>...</think> - MiniMax/Qwen thinking 格式
    - <minimax:tool_call>...</minimax:tool_call> - MiniMax 工具调用格式
    - <<|tool_calls_section_begin|>>...<<|tool_calls_section_end|>> - Kimi K2 工具调用格式
    - </thinking> - 残留的闭合标签
    
    这些内容不应该展示给最终用户。
    """
    if not text:
        return text
    
    cleaned = text
    
    # 移除 <thinking>...</thinking> 标签及其内容
    cleaned = re.sub(r'<thinking>.*?</thinking>\s*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    
    # 移除 <think>...</think> 标签及其内容 (MiniMax/Qwen 格式)
    cleaned = re.sub(r'<think>.*?</think>\s*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    
    # 移除 <minimax:tool_call>...</minimax:tool_call> 标签及其内容
    cleaned = re.sub(r'<minimax:tool_call>.*?</minimax:tool_call>\s*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    
    # 移除 Kimi K2 工具调用格式
    cleaned = re.sub(r'<<\|tool_calls_section_begin\|>>.*?<<\|tool_calls_section_end\|>>\s*', '', cleaned, flags=re.DOTALL)
    
    # 移除 <invoke>...</invoke> 标签（可能单独出现）
    cleaned = re.sub(r'<invoke\s+[^>]*>.*?</invoke>\s*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    
    # 移除残留的闭合标签
    cleaned = re.sub(r'</thinking>\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'</think>\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'</minimax:tool_call>\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'<<\|tool_calls_section_begin\|>>.*$', '', cleaned, flags=re.DOTALL)  # 不完整的
    
    # 移除可能的 XML 声明残留
    cleaned = re.sub(r'<\?xml[^>]*\?>\s*', '', cleaned)
    
    return cleaned.strip()


def strip_tool_simulation_text(text: str) -> str:
    """
    移除 LLM 在文本中模拟工具调用的内容
    
    当使用不支持原生工具调用的备用模型时，LLM 可能会在文本中
    "模拟"工具调用，输出类似:
    - get_skill_info("moltbook")
    - run_shell:0{"command": "..."}
    - read_file("path/to/file")
    
    这些内容不应该展示给最终用户。
    """
    if not text:
        return text
    
    # 模式1: 函数调用风格 function_name("arg") 或 function_name(arg)
    pattern1 = r'^[a-z_]+\s*\([^)]*\)\s*$'
    
    # 模式2: 带序号的工具调用 tool_name:N{json} 或 tool_name:N(args)
    pattern2 = r'^[a-z_]+:\d+[\{\(].*[\}\)]\s*$'
    
    # 模式3: JSON 风格工具调用 {"tool": "name", ...}
    pattern3 = r'^\{["\']?(tool|function|name)["\']?\s*:'
    
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        stripped = line.strip()
        # 检查是否是模拟工具调用
        is_tool_sim = (
            re.match(pattern1, stripped, re.IGNORECASE) or
            re.match(pattern2, stripped, re.IGNORECASE) or
            re.match(pattern3, stripped, re.IGNORECASE)
        )
        if not is_tool_sim:
            cleaned_lines.append(line)
    
    return '\n'.join(cleaned_lines).strip()


def clean_llm_response(text: str) -> str:
    """
    清理 LLM 响应文本
    
    依次应用:
    1. strip_thinking_tags - 移除思考标签
    2. strip_tool_simulation_text - 移除模拟工具调用
    """
    if not text:
        return text
    
    cleaned = strip_thinking_tags(text)
    cleaned = strip_tool_simulation_text(cleaned)
    
    return cleaned.strip()


class Agent:
    """
    OpenAkita 主类
    
    一个全能自进化AI助手，基于 Ralph Wiggum 模式永不放弃。
    """
    
    # 基础工具定义 (Claude API tool use format)
    BASE_TOOLS = [
        # === 文件系统工具 ===
        {
            "name": "run_shell",
            "description": "执行Shell命令，用于运行系统命令、创建目录、执行脚本等。注意：如果命令连续失败，请尝试不同的命令或放弃该方法。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的Shell命令"},
                    "cwd": {"type": "string", "description": "工作目录(可选)"},
                    "timeout": {"type": "integer", "description": "超时时间(秒)，默认60秒。简单命令用30-60秒，安装/下载类命令用300秒，长时间任务可设更长"}
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
        {
            "name": "install_skill",
            "description": """从 URL 或 Git 仓库安装技能到本地 skills/ 目录。

支持的安装源：
1. Git 仓库 URL (如 https://github.com/user/repo 或 git@github.com:user/repo.git)
   - 自动克隆仓库并查找 SKILL.md
   - 支持指定子目录路径
2. 单个 SKILL.md 文件 URL
   - 创建规范目录结构 (scripts/, references/, assets/)

安装后技能会自动加载到 skills/<skill-name>/ 目录。""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Git 仓库 URL 或 SKILL.md 文件 URL"},
                    "name": {"type": "string", "description": "技能名称 (可选，自动从 SKILL.md 提取)"},
                    "subdir": {"type": "string", "description": "Git 仓库中技能所在的子目录路径 (可选)"},
                    "extra_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "额外需要下载的文件 URL 列表，会保存到技能目录 (如 HEARTBEAT.md)"
                    }
                },
                "required": ["source"]
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
            "description": "启动浏览器。⚠️ **重要：服务重启后浏览器会关闭，必须先用 browser_status 检查状态，不能依赖历史记录假设浏览器已打开**。参数说明：visible=True 显示浏览器窗口(用户可见)，visible=False 后台运行(不可见)。默认显示浏览器窗口。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "visible": {
                        "type": "boolean", 
                        "description": "True=显示浏览器窗口(用户可见), False=后台运行(不可见)。默认True",
                        "default": True
                    },
                    "ask_user": {
                        "type": "boolean",
                        "description": "是否先询问用户偏好",
                        "default": False
                    }
                }
            }
        },
        {
            "name": "browser_navigate",
            "description": "导航到指定 URL。⚠️ **使用浏览器前必须先调用此工具打开目标页面**，然后才能使用 browser_type/browser_click 等操作。如果浏览器未启动会自动启动。",
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
            "description": "点击页面上的元素。**前提：必须先用 browser_navigate 打开目标页面**",
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
            "description": "在输入框中输入文本。**前提：必须先用 browser_navigate 打开目标页面**",
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
        {
            "name": "browser_status",
            "description": "获取浏览器当前状态：是否打开、当前页面 URL 和标题、打开的 tab 数量。⚠️ **重要：每次浏览器相关任务必须先调用此工具确认当前状态，不能假设浏览器已打开或依赖历史记录**。",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "browser_list_tabs",
            "description": "列出所有打开的标签页(tabs)，返回每个 tab 的索引、URL 和标题。",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "browser_switch_tab",
            "description": "切换到指定的标签页",
            "input_schema": {
                "type": "object",
                "properties": {
                    "index": {"type": "number", "description": "标签页索引 (从 0 开始，可通过 browser_list_tabs 获取)"}
                },
                "required": ["index"]
            }
        },
        {
            "name": "browser_new_tab",
            "description": "打开新标签页并导航到指定 URL（不会覆盖当前页面）。⚠️ **必须先确认浏览器已启动（用 browser_status 检查），不能假设浏览器状态**。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要访问的 URL"}
                },
                "required": ["url"]
            }
        },
        # === 定时任务工具 ===
        # 【重要概念区分】
        # - 取消/删除任务 (cancel_scheduled_task) = 永久移除，任务不再执行
        # - 关闭提醒 (update notify=false) = 任务继续执行，但不发通知消息
        # - 暂停任务 (update enabled=false) = 任务暂停执行，可以恢复
        {
            "name": "schedule_task",
            "description": "创建定时任务或提醒。"
                           "\n\n**⚠️ 重要: 任务类型判断规则**"
                           "\n✅ **reminder** (默认优先): 所有只需要发送消息的提醒"
                           "\n   - '提醒我喝水' → reminder"
                           "\n   - '站立提醒' → reminder"
                           "\n   - '叫我起床' → reminder"
                           "\n   - '提醒开会' → reminder"
                           "\n❌ **task** (仅当需要AI执行操作时):"
                           "\n   - '查询天气告诉我' → task (需要查询)"
                           "\n   - '截图发给我' → task (需要操作)"
                           "\n   - '执行脚本' → task (需要执行)"
                           "\n\n**90%的提醒都应该是 reminder 类型！只有需要AI主动执行操作的才是 task！**",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "任务/提醒名称"},
                    "description": {"type": "string", "description": "任务描述（用于理解任务目的）"},
                    "task_type": {
                        "type": "string",
                        "enum": ["reminder", "task"],
                        "default": "reminder",
                        "description": "**默认使用 reminder！** reminder=发消息提醒用户，task=需要AI执行查询/操作"
                    },
                    "trigger_type": {
                        "type": "string",
                        "enum": ["once", "interval", "cron"],
                        "description": "触发类型：once=一次性，interval=间隔执行，cron=cron表达式"
                    },
                    "trigger_config": {
                        "type": "object",
                        "description": "触发配置。once: {run_at: '2026-02-01 10:00'}；interval: {interval_minutes: 30}；cron: {cron: '0 9 * * *'}"
                    },
                    "reminder_message": {
                        "type": "string",
                        "description": "提醒消息内容（仅 reminder 类型需要，到时间会直接发送此消息）"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "执行时发送给 Agent 的提示（仅 task 类型需要，AI 会执行此指令）"
                    },
                    "notify_on_start": {
                        "type": "boolean",
                        "default": True,
                        "description": "任务开始时发通知？默认true。'不要提醒'时设false"
                    },
                    "notify_on_complete": {
                        "type": "boolean",
                        "default": True,
                        "description": "任务完成时发通知？默认true。'不要提醒'时设false"
                    }
                },
                "required": ["name", "description", "task_type", "trigger_type", "trigger_config"]
            }
        },
        {
            "name": "list_scheduled_tasks",
            "description": "列出所有定时任务，返回任务ID、名称、类型、状态、下次执行时间",
            "input_schema": {
                "type": "object",
                "properties": {
                    "enabled_only": {"type": "boolean", "description": "是否只列出启用的任务", "default": False}
                }
            }
        },
        {
            "name": "cancel_scheduled_task",
            "description": "【永久删除】定时任务。"
                           "\n⚠️ 用户说'取消/删除任务' → 用此工具"
                           "\n⚠️ 用户说'关闭提醒' → 用 update_scheduled_task 设 notify=false"
                           "\n⚠️ 用户说'暂停任务' → 用 update_scheduled_task 设 enabled=false",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "任务 ID"}
                },
                "required": ["task_id"]
            }
        },
        {
            "name": "update_scheduled_task",
            "description": "修改定时任务设置【不删除任务】。"
                           "\n可修改: notify_on_start, notify_on_complete, enabled"
                           "\n\n常见用法:"
                           "\n- '关闭提醒' → notify_on_start=false, notify_on_complete=false"
                           "\n- '暂停任务' → enabled=false"
                           "\n- '恢复任务' → enabled=true",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "要修改的任务ID（先用list获取）"},
                    "notify_on_start": {"type": "boolean", "description": "开始时发通知？不传=不修改"},
                    "notify_on_complete": {"type": "boolean", "description": "完成时发通知？不传=不修改"},
                    "enabled": {"type": "boolean", "description": "启用(true)/暂停(false)任务？不传=不修改"}
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
                           "支持发送文本、图片、语音、文件。"
                           "当你完成了生成文件（如截图、文档、语音）的任务时，使用此工具将文件发送给用户。",
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
                    "voice_path": {
                        "type": "string",
                        "description": "要发送的语音文件路径（.ogg, .mp3, .wav 等）"
                    },
                    "caption": {
                        "type": "string",
                        "description": "文件的说明文字（可选）"
                    }
                }
            }
        },
        {
            "name": "get_voice_file",
            "description": "获取用户发送的语音消息的本地文件路径。"
                           "当用户发送语音消息时，系统会自动下载到本地。"
                           "使用此工具获取语音文件路径，然后你可以用语音识别脚本处理它。",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "get_image_file",
            "description": "获取用户发送的图片的本地文件路径。"
                           "当用户发送图片时，系统会自动下载到本地。"
                           "使用此工具获取图片文件路径。",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "get_chat_history",
            "description": "获取当前聊天的历史消息记录。"
                           "包括用户发送的消息、你之前的回复、以及系统任务（如定时任务）发送的通知。"
                           "当用户说'看看之前的消息'、'刚才发的什么'时使用此工具。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "获取最近多少条消息，默认 20",
                        "default": 20
                    },
                    "include_system": {
                        "type": "boolean",
                        "description": "是否包含系统消息（如任务通知），默认 true",
                        "default": True
                    }
                }
            }
        },
        # === Thinking 模式控制 ===
        {
            "name": "enable_thinking",
            "description": "控制深度思考模式。默认已启用 thinking 模式。"
                           "如果遇到非常简单的任务（如：简单提醒、简单问候、快速查询），"
                           "可以临时关闭以加快响应速度。完成后会自动恢复默认启用状态。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "是否启用 thinking 模式。true=启用深度思考，false=关闭"
                    },
                    "reason": {
                        "type": "string",
                        "description": "简要说明为什么需要（或不需要）开启 thinking 模式"
                    }
                },
                "required": ["enabled", "reason"]
            }
        },
        # === 用户档案工具 ===
        {
            "name": "update_user_profile",
            "description": "更新用户档案信息。当用户告诉你关于他们的偏好、习惯、工作领域等信息时，"
                           "使用此工具保存。这样你就能更好地了解用户，提供个性化服务。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "档案项键名: name(称呼), agent_role(Agent角色), work_field(工作领域), "
                                       "preferred_language(编程语言), os(操作系统), ide(开发工具), "
                                       "detail_level(详细程度), code_comment_lang(代码注释语言), "
                                       "work_hours(工作时间), timezone(时区), confirm_preference(确认偏好)"
                    },
                    "value": {
                        "type": "string",
                        "description": "用户提供的信息值"
                    }
                },
                "required": ["key", "value"]
            }
        },
        {
            "name": "skip_profile_question",
            "description": "当用户明确表示不想回答某个问题时，跳过该问题（以后不再询问）。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "要跳过的档案项键名"
                    }
                },
                "required": ["key"]
            }
        },
        {
            "name": "get_user_profile",
            "description": "获取当前用户档案信息摘要",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        # === 日志查询工具 ===
        {
            "name": "get_session_logs",
            "description": "获取当前会话的系统日志。**重要**: 当命令执行失败、遇到错误、或需要了解之前的操作结果时，应该调用此工具查看日志。"
                           "日志包含: 命令执行详情、错误信息、系统状态等。"
                           "使用场景: 1) 命令返回错误码 2) 操作没有预期效果 3) 需要了解之前发生了什么。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "返回的日志条数（默认 20，最大 200）",
                        "default": 20
                    },
                    "level": {
                        "type": "string",
                        "enum": ["DEBUG", "INFO", "WARNING", "ERROR"],
                        "description": "过滤日志级别（可选，ERROR 可快速定位问题）"
                    }
                }
            }
        },
        # === 工具信息查询（渐进式披露 Level 2）===
        {
            "name": "get_tool_info",
            "description": "获取系统工具的详细参数定义。在调用不熟悉的工具前，先用此工具了解其完整用法、参数说明和示例。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "工具名称"}
                },
                "required": ["tool_name"]
            }
        },
        # === MCP 工具 ===
        {
            "name": "call_mcp_tool",
            "description": "调用 MCP 服务器的工具。查看系统提示中的 'MCP Servers' 部分了解可用的服务器和工具。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "MCP 服务器标识符"},
                    "tool_name": {"type": "string", "description": "工具名称"},
                    "arguments": {"type": "object", "description": "工具参数", "default": {}}
                },
                "required": ["server", "tool_name"]
            }
        },
        {
            "name": "list_mcp_servers",
            "description": "列出所有配置的 MCP 服务器及其连接状态。",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "get_mcp_instructions",
            "description": "获取 MCP 服务器的详细使用说明（INSTRUCTIONS.md）。用于了解服务器的完整使用方法。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "服务器标识符"}
                },
                "required": ["server"]
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
        
        # 系统工具目录（渐进式披露）
        self.tool_catalog = ToolCatalog(self.BASE_TOOLS)
        
        # 定时任务调度器
        self.task_scheduler = None  # 在 initialize() 中启动
        
        # 记忆系统
        self.memory_manager = MemoryManager(
            data_dir=settings.project_root / "data" / "memory",
            memory_md_path=settings.memory_path,
            brain=self.brain,
        )
        
        # 用户档案管理器
        self.profile_manager = get_profile_manager()
        
        # 动态工具列表（基础工具 + 技能工具）
        self._tools = list(self.BASE_TOOLS)
        self._update_shell_tool_description()
        
        # 对话上下文
        self._context = Context()
        self._conversation_history: list[dict] = []
        
        # 消息中断机制
        self._current_session = None  # 当前会话引用
        self._interrupt_enabled = True  # 是否启用中断检查
        
        # 状态
        self._initialized = False
        self._running = False
        
        logger.info(f"Agent '{self.name}' created")
    
    async def initialize(self, start_scheduler: bool = True) -> None:
        """
        初始化 Agent
        
        Args:
            start_scheduler: 是否启动定时任务调度器（定时任务执行时应设为 False）
        """
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
        
        # 启动定时任务调度器（定时任务执行时跳过，避免重复）
        if start_scheduler:
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
    
    def _update_shell_tool_description(self) -> None:
        """动态更新 shell 工具描述，包含当前操作系统信息"""
        import platform
        
        # 获取操作系统信息
        if os.name == 'nt':
            os_info = f"Windows {platform.release()} (使用 PowerShell/cmd 命令，如: dir, type, tasklist, Get-Process, findstr)"
        else:
            os_info = f"{platform.system()} (使用 bash 命令，如: ls, cat, ps aux, grep)"
        
        # 更新 run_shell 工具的描述
        for tool in self._tools:
            if tool.get("name") == "run_shell":
                tool["description"] = (
                    f"执行Shell命令。当前操作系统: {os_info}。"
                    "注意：请使用当前操作系统支持的命令；如果命令连续失败，请尝试不同的命令或放弃该方法。"
                )
                tool["input_schema"]["properties"]["command"]["description"] = (
                    f"要执行的Shell命令（当前系统: {os.name}）"
                )
                break
    
    def _update_skill_tools(self) -> None:
        """更新工具列表，添加技能相关工具"""
        # 基础工具已在 BASE_TOOLS 中定义
        # 这里可以添加动态生成的技能工具
        pass
    
    async def _install_skill(
        self, 
        source: str, 
        name: Optional[str] = None,
        subdir: Optional[str] = None,
        extra_files: Optional[list[str]] = None
    ) -> str:
        """
        安装技能到本地 skills/ 目录
        
        支持：
        1. Git 仓库 URL (克隆并查找 SKILL.md)
        2. 单个 SKILL.md 文件 URL (创建规范目录结构)
        
        Args:
            source: Git 仓库 URL 或 SKILL.md 文件 URL
            name: 技能名称 (可选)
            subdir: Git 仓库中技能所在的子目录
            extra_files: 额外文件 URL 列表
        
        Returns:
            安装结果消息
        """
        import re
        import yaml
        import shutil
        import tempfile
        
        skills_dir = settings.skills_path
        
        # 判断是 Git 仓库还是文件 URL
        is_git = self._is_git_url(source)
        
        if is_git:
            return await self._install_skill_from_git(source, name, subdir, skills_dir)
        else:
            return await self._install_skill_from_url(source, name, extra_files, skills_dir)
    
    def _is_git_url(self, url: str) -> bool:
        """判断是否为 Git 仓库 URL"""
        git_patterns = [
            r'^git@',  # SSH
            r'\.git$',  # 以 .git 结尾
            r'^https?://github\.com/',
            r'^https?://gitlab\.com/',
            r'^https?://bitbucket\.org/',
            r'^https?://gitee\.com/',
        ]
        for pattern in git_patterns:
            if re.search(pattern, url):
                return True
        return False
    
    async def _install_skill_from_git(
        self,
        git_url: str,
        name: Optional[str],
        subdir: Optional[str],
        skills_dir: Path
    ) -> str:
        """从 Git 仓库安装技能"""
        import tempfile
        import shutil
        
        temp_dir = None
        try:
            # 1. 克隆仓库到临时目录
            temp_dir = Path(tempfile.mkdtemp(prefix="skill_install_"))
            
            # 执行 git clone
            result = await self.shell_tool.run(
                f'git clone --depth 1 "{git_url}" "{temp_dir}"'
            )
            
            if not result.success:
                return f"❌ Git 克隆失败:\n{result.output}"
            
            # 2. 查找 SKILL.md
            search_dir = temp_dir / subdir if subdir else temp_dir
            skill_md_path = self._find_skill_md(search_dir)
            
            if not skill_md_path:
                # 列出可能的技能目录
                possible = self._list_skill_candidates(temp_dir)
                hint = ""
                if possible:
                    hint = f"\n\n可能的技能目录:\n" + "\n".join(f"- {p}" for p in possible[:5])
                return f"❌ 未找到 SKILL.md 文件{hint}"
            
            skill_source_dir = skill_md_path.parent
            
            # 3. 解析技能元数据
            skill_content = skill_md_path.read_text(encoding='utf-8')
            extracted_name = self._extract_skill_name(skill_content)
            skill_name = name or extracted_name or skill_source_dir.name
            skill_name = self._normalize_skill_name(skill_name)
            
            # 4. 复制到 skills 目录
            target_dir = skills_dir / skill_name
            if target_dir.exists():
                shutil.rmtree(target_dir)
            
            shutil.copytree(skill_source_dir, target_dir)
            
            # 5. 确保有规范的目录结构
            self._ensure_skill_structure(target_dir)
            
            # 6. 加载技能
            installed_files = self._list_installed_files(target_dir)
            try:
                loaded = self.skill_loader.load_skill(target_dir)
                if loaded:
                    self._skill_catalog_text = self.skill_catalog.generate_catalog()
                    logger.info(f"Skill installed from git: {skill_name}")
            except Exception as e:
                logger.error(f"Failed to load installed skill: {e}")
            
            return f"""✅ 技能从 Git 安装成功！

**技能名称**: {skill_name}
**来源**: {git_url}
**安装路径**: {target_dir}

**目录结构**:
```
{skill_name}/
{self._format_tree(target_dir)}
```

技能已自动加载，可以使用:
- `get_skill_info("{skill_name}")` 查看详细指令
- `list_skills` 查看所有已安装技能"""
            
        except Exception as e:
            logger.error(f"Failed to install skill from git: {e}")
            return f"❌ Git 安装失败: {str(e)}"
        finally:
            # 清理临时目录
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                except:
                    pass
    
    async def _install_skill_from_url(
        self,
        url: str,
        name: Optional[str],
        extra_files: Optional[list[str]],
        skills_dir: Path
    ) -> str:
        """从 URL 安装技能"""
        import httpx
        
        try:
            # 1. 下载 SKILL.md
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                skill_content = response.text
            
            # 2. 提取技能名称
            extracted_name = self._extract_skill_name(skill_content)
            skill_name = name or extracted_name
            
            if not skill_name:
                # 从 URL 提取
                from urllib.parse import urlparse
                path = urlparse(url).path
                skill_name = path.split('/')[-1].replace('.md', '').replace('skill', '').strip('-_')
            
            skill_name = self._normalize_skill_name(skill_name or "custom-skill")
            
            # 3. 创建技能目录结构
            skill_dir = skills_dir / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            
            # 4. 保存 SKILL.md
            (skill_dir / "SKILL.md").write_text(skill_content, encoding='utf-8')
            
            # 5. 创建规范目录结构
            self._ensure_skill_structure(skill_dir)
            
            installed_files = ["SKILL.md"]
            
            # 6. 下载额外文件
            if extra_files:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    for file_url in extra_files:
                        try:
                            from urllib.parse import urlparse
                            file_name = urlparse(file_url).path.split('/')[-1]
                            if not file_name:
                                continue
                            
                            response = await client.get(file_url)
                            response.raise_for_status()
                            
                            # 根据文件类型放到对应目录
                            if file_name.endswith('.md'):
                                dest = skill_dir / "references" / file_name
                            elif file_name.endswith(('.py', '.sh', '.js')):
                                dest = skill_dir / "scripts" / file_name
                            else:
                                dest = skill_dir / file_name
                            
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_text(response.text, encoding='utf-8')
                            installed_files.append(str(dest.relative_to(skill_dir)))
                        except Exception as e:
                            logger.warning(f"Failed to download {file_url}: {e}")
            
            # 7. 加载技能
            try:
                loaded = self.skill_loader.load_skill(skill_dir)
                if loaded:
                    self._skill_catalog_text = self.skill_catalog.generate_catalog()
                    logger.info(f"Skill installed from URL: {skill_name}")
            except Exception as e:
                logger.error(f"Failed to load installed skill: {e}")
            
            return f"""✅ 技能安装成功！

**技能名称**: {skill_name}
**安装路径**: {skill_dir}

**目录结构**:
```
{skill_name}/
{self._format_tree(skill_dir)}
```

**安装文件**: {', '.join(installed_files)}

技能已自动加载，可以使用:
- `get_skill_info("{skill_name}")` 查看详细指令
- `list_skills` 查看所有已安装技能"""
            
        except Exception as e:
            logger.error(f"Failed to install skill from URL: {e}")
            return f"❌ URL 安装失败: {str(e)}"
    
    def _extract_skill_name(self, content: str) -> Optional[str]:
        """从 SKILL.md 内容提取技能名称"""
        import re
        import yaml
        
        match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if match:
            try:
                metadata = yaml.safe_load(match.group(1))
                return metadata.get('name')
            except:
                pass
        return None
    
    def _normalize_skill_name(self, name: str) -> str:
        """标准化技能名称"""
        import re
        name = name.lower().replace('_', '-').replace(' ', '-')
        name = re.sub(r'[^a-z0-9-]', '', name)
        name = re.sub(r'-+', '-', name).strip('-')
        return name or "custom-skill"
    
    def _find_skill_md(self, search_dir: Path) -> Optional[Path]:
        """在目录中查找 SKILL.md"""
        # 先检查当前目录
        skill_md = search_dir / "SKILL.md"
        if skill_md.exists():
            return skill_md
        
        # 递归查找
        for path in search_dir.rglob("SKILL.md"):
            return path
        
        return None
    
    def _list_skill_candidates(self, base_dir: Path) -> list[str]:
        """列出可能包含技能的目录"""
        candidates = []
        for path in base_dir.rglob("*.md"):
            if path.name.lower() in ("skill.md", "readme.md"):
                rel_path = path.parent.relative_to(base_dir)
                if str(rel_path) != ".":
                    candidates.append(str(rel_path))
        return candidates
    
    def _ensure_skill_structure(self, skill_dir: Path) -> None:
        """确保技能目录有规范结构"""
        (skill_dir / "scripts").mkdir(exist_ok=True)
        (skill_dir / "references").mkdir(exist_ok=True)
        (skill_dir / "assets").mkdir(exist_ok=True)
    
    def _list_installed_files(self, skill_dir: Path) -> list[str]:
        """列出已安装的文件"""
        files = []
        for path in skill_dir.rglob("*"):
            if path.is_file():
                files.append(str(path.relative_to(skill_dir)))
        return files
    
    def _format_tree(self, directory: Path, prefix: str = "") -> str:
        """格式化目录树"""
        lines = []
        items = sorted(directory.iterdir(), key=lambda x: (x.is_file(), x.name))
        
        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{item.name}")
            
            if item.is_dir():
                extension = "    " if is_last else "│   "
                sub_tree = self._format_tree(item, prefix + extension)
                if sub_tree:
                    lines.append(sub_tree)
        
        return "\n".join(lines)
    
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
            self.browser_mcp = BrowserMCP(headless=False)  # 默认可见模式
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
            from ..scheduler.executor import TaskExecutor
            
            # 创建执行器（gateway 稍后通过 set_scheduler_gateway 设置）
            self._task_executor = TaskExecutor(timeout_seconds=settings.scheduler_task_timeout)
            
            # 创建调度器
            self.task_scheduler = TaskScheduler(
                storage_path=settings.project_root / "data" / "scheduler",
                executor=self._task_executor.execute,
            )
            
            # 启动调度器
            await self.task_scheduler.start()
            
            # 注册内置系统任务（每日记忆整理 + 每日自检）
            await self._register_system_tasks()
            
            stats = self.task_scheduler.get_stats()
            logger.info(f"TaskScheduler started with {stats['total_tasks']} tasks")
            
        except Exception as e:
            logger.warning(f"Failed to start scheduler: {e}")
            self.task_scheduler = None
    
    async def _register_system_tasks(self) -> None:
        """
        注册内置系统任务
        
        包括:
        - 每日记忆整理（凌晨 3:00）
        - 每日系统自检（凌晨 4:00）
        """
        from ..scheduler import ScheduledTask, TriggerType
        from ..scheduler.task import TaskType
        
        if not self.task_scheduler:
            return
        
        # 检查是否已存在（避免重复注册）
        existing_tasks = self.task_scheduler.list_tasks()
        existing_ids = {t.id for t in existing_tasks}
        
        # 任务 1: 每日记忆整理（凌晨 3:00）
        if "system_daily_memory" not in existing_ids:
            memory_task = ScheduledTask(
                id="system_daily_memory",
                name="每日记忆整理",
                trigger_type=TriggerType.CRON,
                trigger_config={"cron": "0 3 * * *"},
                action="system:daily_memory",
                prompt="执行每日记忆整理：整理当天对话历史，提取精华记忆，刷新 MEMORY.md",
                description="整理当天对话，提取记忆，刷新 MEMORY.md",
                task_type=TaskType.TASK,
                enabled=True,
                deletable=False,  # 系统任务不允许删除
            )
            await self.task_scheduler.add_task(memory_task)
            logger.info("Registered system task: daily_memory (03:00)")
        else:
            # 确保已存在的系统任务也设置为不可删除
            existing_task = self.task_scheduler.get_task("system_daily_memory")
            if existing_task and existing_task.deletable:
                existing_task.deletable = False
                self.task_scheduler._save_tasks()
        
        # 任务 2: 每日系统自检（凌晨 4:00）
        if "system_daily_selfcheck" not in existing_ids:
            selfcheck_task = ScheduledTask(
                id="system_daily_selfcheck",
                name="每日系统自检",
                trigger_type=TriggerType.CRON,
                trigger_config={"cron": "0 4 * * *"},
                action="system:daily_selfcheck",
                prompt="执行每日系统自检：分析 ERROR 日志，尝试修复工具问题，生成报告",
                description="分析 ERROR 日志、尝试修复工具问题、生成报告",
                task_type=TaskType.TASK,
                enabled=True,
                deletable=False,  # 系统任务不允许删除
            )
            await self.task_scheduler.add_task(selfcheck_task)
            logger.info("Registered system task: daily_selfcheck (04:00)")
        else:
            # 确保已存在的系统任务也设置为不可删除
            existing_task = self.task_scheduler.get_task("system_daily_selfcheck")
            if existing_task and existing_task.deletable:
                existing_task.deletable = False
                self.task_scheduler._save_tasks()
    
    def _build_system_prompt(self, base_prompt: str, task_description: str = "") -> str:
        """
        构建系统提示词 (动态生成，包含技能清单、MCP 清单和相关记忆)
        
        遵循规范的渐进式披露:
        - Agent Skills: name + description 在系统提示中
        - MCP: server + tool name + description 在系统提示中
        - Memory: 相关记忆按需注入
        - Tools: 从 BASE_TOOLS 动态生成
        - User Profile: 首次引导或日常询问
        
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
        
        # 用户档案收集提示 (首次引导或日常询问)
        profile_prompt = ""
        if self.profile_manager.is_first_use():
            profile_prompt = self.profile_manager.get_onboarding_prompt()
        else:
            profile_prompt = self.profile_manager.get_daily_question_prompt()
        
        # 系统环境信息
        import platform
        import os
        system_info = f"""## 运行环境

- **操作系统**: {platform.system()} {platform.release()}
- **当前工作目录**: {os.getcwd()}
- **临时目录**: 
  - Windows: 使用当前目录下的 `data/temp/` 或 `%TEMP%`
  - Linux/macOS: 使用当前目录下的 `data/temp/` 或 `/tmp`
- **建议**: 创建临时文件时优先使用 `data/temp/` 目录（相对于当前工作目录）

## ⚠️ 重要：运行时状态不持久化

**服务重启后以下状态会丢失，不能依赖会话历史记录判断当前状态：**

| 状态 | 重启后 | 正确做法 |
|------|--------|----------|
| 浏览器 | **已关闭** | 必须先调用 `browser_status` 确认，不能假设已打开 |
| 变量/内存数据 | **已清空** | 通过工具重新获取，不能依赖历史 |
| 临时文件 | **可能清除** | 重新检查文件是否存在 |
| 网络连接 | **已断开** | 需要重新建立连接 |

**⚠️ 会话历史中的"成功打开浏览器"等记录只是历史，不代表当前状态！每次执行任务必须通过工具调用获取实时状态。**
"""
        
        # 工具使用指南
        tools_guide = """
## 工具体系说明

你有三类工具可以使用，**它们都是工具，都可以调用**：

### 1. 系统工具（渐进式披露）

系统内置的核心工具，采用渐进式披露：

| 步骤 | 操作 | 说明 |
|-----|-----|-----|
| 1 | 查看上方 "Available System Tools" 清单 | 了解有哪些工具可用 |
| 2 | `get_tool_info(tool_name)` | 获取工具的完整参数定义 |
| 3 | 直接调用工具 | 如 `read_file(path="...")` |

**工具类别**：文件系统、浏览器、记忆、定时任务、用户档案等

### 2. Skills 技能（渐进式披露）

可扩展的能力模块，采用渐进式披露：

| 步骤 | 操作 | 说明 |
|-----|-----|-----|
| 1 | 查看上方 "Available Skills" 清单 | 了解有哪些技能可用 |
| 2 | `get_skill_info(skill_name)` | 获取技能的详细使用说明 |
| 3 | `run_skill_script(skill_name, script_name)` | 执行技能提供的脚本 |

**特点**：可通过 `install_skill` 安装新技能，或通过 `generate_skill` 自动生成

### 3. MCP 外部服务（全量暴露）

MCP (Model Context Protocol) 连接外部服务，**工具定义已全量展示**：

| 步骤 | 操作 | 说明 |
|-----|-----|-----|
| 1 | 查看上方 "MCP Servers" 清单 | 包含完整的工具定义和参数 |
| 2 | `call_mcp_tool(server, tool_name, arguments)` | 直接调用 |

**特点**：连接数据库、API 等外部服务

### 工具选择原则

1. **系统工具**：文件操作、命令执行、浏览器、记忆等基础能力
2. **Skills**：复杂任务、特定领域能力、可复用的工作流
3. **MCP**：外部服务集成（数据库、第三方 API）
4. **找不到工具？用 `generate_skill` 创造一个！**

**记住：这三类都是工具，都可以调用，不要说"我没有这个能力"！**
"""
        
        return f"""{base_prompt}

{system_info}
{skill_catalog}
{mcp_catalog}
{memory_context}

{tools_text}

{tools_guide}

## 核心原则 (最高优先级!!!)

### 第一铁律：工具优先，绝不空谈

**⚠️ 任何任务都必须通过工具或脚本完成，不能只是嘴上说说！**

| 场景 | ❌ 错误做法 | ✅ 正确做法 |
|------|-----------|-----------|
| 用户要提醒 | 回复"好的我会提醒你" | 调用 schedule_task |
| 用户要查信息 | 凭记忆回答 | 调用 web_search |
| 用户要操作文件 | 描述怎么操作 | 调用 write_file/read_file |
| 用户要执行命令 | 告诉用户命令是什么 | 调用 run_shell |
| 用户要处理数据 | 告诉用户写什么代码 | 写临时脚本并执行 |

**只回复文字 = 没有执行任务 = 失败！**

### 第二铁律：没有工具就创造工具

**绝不说"我没有这个能力"！立即行动：**

**方式一：临时脚本（一次性任务）**
```
# 根据运行环境信息中的当前工作目录和临时目录建议
write_file("data/temp/task.py", "脚本内容")
run_shell("python data/temp/task.py")
```

**方式二：搜索安装（有现成的）**
```
search_github → install_skill → 使用
```

**方式三：创建技能（需要复用）**
```
generate_skill → 保存 → 使用
```

**从"不会"到"会"只需要几分钟！**

### 第三铁律：问题自己解决

**遇到问题，默认自己解决，不要甩给用户：**

- 报错了？自己读日志、分析、修复
- 缺信息？自己用工具查找
- 不确定？先尝试，失败了再换方法
- **只有完全无法解决才询问用户**

### 第四铁律：永不放弃

- 第一次失败？换个方法再试
- 第二次失败？再换一个
- 工具不够用？创建新工具
- 信息不完整？主动去查找

**禁止说"我做不到"、"这超出了我的能力"、"请你自己..."！**
**正确做法：分析问题 → 搜索方案 → 获取工具 → 执行任务 → 验证结果**

---

## 重要提示

### 深度思考模式 (Thinking Mode)

**默认启用 thinking 模式**，这样可以保证回答质量。

如果遇到非常简单的任务（如：简单问候、快速提醒），可以调用 `enable_thinking(enabled=false)` 临时关闭以加快响应。
大多数情况下保持默认启用即可，不需要主动管理。

### 工具调用
- 工具直接使用工具名调用，不需要任何前缀
- **提醒/定时任务必须使用 schedule_task 工具**，不要只是回复"好的"
- 当用户说"X分钟后提醒我"时，立即调用 schedule_task 创建任务

### 主动沟通 (极其重要!!!)

**第一条铁律：收到用户消息后，先用 send_to_chat 回复一声再干活！**

不管任务简单还是复杂，先让用户知道你收到了：
- 收到任务 → 立即 send_to_chat("收到！让我来处理...") → 然后开始干活
- 不要闷头执行命令，用户看不到你在做什么会很焦虑

**执行过程中也要汇报进度：**
- 每完成一个重要步骤，发一条消息
- 遇到问题需要确认时，主动询问
- 完成后发送最终结果

**示例流程**:
1. 用户: "语音功能实现了吗"
2. AI: send_to_chat("收到！让我检查一下语音功能的实现状态...")  ← 先回复！
3. AI: [执行检查命令]
4. AI: send_to_chat("检查完毕，语音识别脚本已创建，但还需要集成到系统中...")
5. AI: [继续处理]
6. AI: "✅ 完成！你现在可以发送语音消息测试了。"

**禁止：收到消息后不回复就开始执行一堆命令！**

### 定时任务/提醒 (极其重要!!!)

**当用户请求设置提醒、定时任务时，你必须立即调用 schedule_task 工具！**
**禁止只回复"好的，我会提醒你"这样的文字！那样任务不会被创建！**
**只有调用了 schedule_task 工具，任务才会真正被调度执行！**

**⚠️ 任务类型判断 (task_type) - 这是最重要的决策！**

**默认使用 reminder！除非明确需要AI执行操作才用 task！**

✅ **reminder** (90%的情况都是这个!):
- 只需要到时间发一条消息提醒用户
- 例子: "提醒我喝水"、"叫我起床"、"站立提醒"、"开会提醒"、"午睡提醒"
- 特点: 用户说"提醒我xxx"、"叫我xxx"、"通知我xxx"

❌ **task** (仅10%的特殊情况):
- 需要AI在触发时执行查询、操作、截图等
- 例子: "查天气告诉我"、"截图发给我"、"执行脚本"、"帮我发消息给别人"
- 特点: 用户说"帮我做xxx"、"执行xxx"、"查询xxx"

**创建任务后，必须明确告知用户**:
- reminder: "好的，到时间我会提醒你：[提醒内容]" (只发一条消息)
- task: "好的，到时间我会自动执行：[任务内容]" (AI会运行并汇报结果)

调用 schedule_task 时的参数:

1. **简单提醒** (task_type="reminder"):
   - name: "喝水提醒"
   - description: "提醒用户喝水"
   - task_type: "reminder"
   - trigger_type: "once"
   - trigger_config: {{"run_at": "2026-02-01 10:00"}}
   - reminder_message: "⏰ 该喝水啦！记得保持水分摄入哦~"

2. **复杂任务** (task_type="task"):
   - name: "每日天气查询"
   - description: "查询今日天气并告知用户"
   - task_type: "task"
   - trigger_type: "cron"
   - trigger_config: {{"cron": "0 8 * * *"}}
   - prompt: "查询今天的天气，并以友好的方式告诉用户"

**触发类型**:
- once: 一次性，trigger_config 包含 run_at
- interval: 间隔执行，trigger_config 包含 interval_minutes
- cron: 定时执行，trigger_config 包含 cron 表达式

**再次强调：收到提醒请求时，第一反应就是调用 schedule_task 工具！**

### 系统已内置功能 (不需要自己实现!)

以下功能**系统已经内置**，当用户提到时，不要尝试"开发"或"实现"，而是直接使用：

1. **语音转文字** - 系统**已自动处理**语音识别！
   - 用户发送的语音消息会被系统**自动**转写为文字（通过本地 Whisper medium 模型）
   - 你收到的消息中，语音内容已经被转写为文字了
   - 如果看到 `[语音: X秒]` 但没有文字内容，说明自动识别失败
   - **只有**在自动识别失败时（如看到"语音识别失败"提示），才需要手动处理语音文件
   - ⚠️ **重要**：不要每次收到语音消息都调用语音识别工具！系统已经自动处理了！
   
2. **图片理解** - 用户发送的图片会自动传递给你进行多模态理解
   - 你可以直接"看到"用户发送的图片并描述或分析
   
3. **Telegram 配对** - 已内置配对验证机制

**当用户说"帮我实现语音转文字"时**：
- ❌ 不要开始写代码、安装 whisper、配置 ffmpeg
- ❌ 不要调用语音识别技能或工具去处理
- ✅ 告诉用户"语音转文字已内置并自动运行，请发送语音测试"

**语音消息处理流程**：
1. 用户发送语音 → 2. 系统自动下载并用 Whisper 转文字 → 3. 你收到的是转写后的文字
4. 只有当你看到"[语音识别失败]"或"自动识别失败"时，才需要用 get_voice_file 工具获取文件路径并手动处理

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

### 记忆使用原则 (重要!)
**上下文优先**：当前对话内容永远优先于记忆中的信息。

**不要让记忆主导对话**：
- ❌ 错误：用户说"你好" → 回复"你好！关于之前 Moltbook 技能的事情，你想怎么处理？"
- ✅ 正确：用户说"你好" → 回复"你好！有什么可以帮你的？"（记忆中的事情等用户主动提起或真正相关时再说）

**记忆提及方式**：
- 如果记忆与当前话题高度相关，可以**简短**提一句，但不要作为回复的主体
- 不要让用户感觉你在"接着上次说"——每次对话都是新鲜的开始
- 例如：处理完用户当前请求后，可以在结尾轻轻带一句"对了，之前xxx的事情需要我继续处理吗？"

### 诚实原则 (极其重要!!!)
**绝对禁止编造不存在的功能或进度！**

❌ **严禁以下行为**：
- 声称"正在运行"、"已完成"但实际没有创建任何文件/脚本
- 在回复中贴一段代码假装在执行，但实际没有调用任何工具
- 声称"每X秒监控"但没有创建对应的定时任务
- 承诺"5分钟内完成"但根本没有开始执行

✅ **正确做法**：
- 如果需要创建脚本，必须调用 write_file 工具实际写入
- 如果需要定时任务，必须调用 schedule_task 工具实际创建
- 如果做不到，诚实告知"这个功能我目前无法实现，原因是..."
- 如果需要时间开发，先实际开发完成，再告诉用户结果

**用户信任比看起来厉害更重要！宁可说"我做不到"也不要骗人！**
{profile_prompt}"""
    
    def _generate_tools_text(self) -> str:
        """
        从 BASE_TOOLS 动态生成工具列表文本
        
        按类别分组显示，包含重要参数说明
        """
        # 工具分类
        categories = {
            "File System": ["run_shell", "write_file", "read_file", "list_directory"],
            "Skills Management": ["list_skills", "get_skill_info", "run_skill_script", "get_skill_reference", "generate_skill", "improve_skill"],
            "Memory Management": ["add_memory", "search_memory", "get_memory_stats"],
            "Browser Automation": ["browser_open", "browser_status", "browser_list_tabs", "browser_navigate", "browser_new_tab", "browser_switch_tab", "browser_click", "browser_type", "browser_get_content", "browser_screenshot"],
            "Scheduled Tasks": ["schedule_task", "list_scheduled_tasks", "cancel_scheduled_task", "trigger_scheduled_task"],
        }
        
        # 构建工具名到完整定义的映射
        tool_map = {t["name"]: t for t in self._tools}
        
        lines = ["## Available Tools"]
        
        for category, tool_names in categories.items():
            # 过滤出存在的工具
            existing_tools = [(name, tool_map[name]) for name in tool_names if name in tool_map]
            
            if existing_tools:
                lines.append(f"\n### {category}")
                for name, tool_def in existing_tools:
                    desc = tool_def.get("description", "")
                    # 不再截断描述，完整显示
                    lines.append(f"- **{name}**: {desc}")
                    
                    # 显示重要参数（可选）
                    schema = tool_def.get("input_schema", {})
                    props = schema.get("properties", {})
                    required = schema.get("required", [])
                    
                    # 注意：工具的完整参数定义通过 tools=self._tools 传递给 LLM API
                    # 这里只在 system prompt 中简要列出，避免过长
        
        # 添加未分类的工具
        categorized = set()
        for names in categories.values():
            categorized.update(names)
        
        uncategorized = [(t["name"], t) for t in self._tools if t["name"] not in categorized]
        if uncategorized:
            lines.append("\n### Other Tools")
            for name, tool_def in uncategorized:
                desc = tool_def.get("description", "")
                lines.append(f"- **{name}**: {desc}")
        
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
        压缩对话上下文（使用 LLM 压缩，不直接截断）
        
        策略:
        1. 保留最近 MIN_RECENT_TURNS 轮对话完整
        2. 将早期对话用 LLM 摘要成简短描述
        3. 如果还是太长，递归压缩
        
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
        
        logger.info(f"Context too large ({current_tokens} tokens), compressing with LLM...")
        
        # 计算需要保留的最近对话数量 (user + assistant = 1 轮)
        recent_count = MIN_RECENT_TURNS * 2  # 4 轮 = 8 条消息
        
        if len(messages) <= recent_count:
            # 消息本身就不多，无法再压缩，原样返回并记录警告
            logger.warning(f"Cannot compress further: only {len(messages)} messages, keeping all")
            return messages
        
        # 分离早期消息和最近消息
        early_messages = messages[:-recent_count]
        recent_messages = messages[-recent_count:]
        
        # 使用 LLM 摘要早期对话
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
        
        # 还是太长，递归压缩（减少保留的最近消息数量）
        logger.warning(f"Context still large ({compressed_tokens} tokens), compressing further...")
        return await self._compress_long_messages(compressed, available_tokens)
    
    async def _summarize_messages(self, messages: list[dict]) -> str:
        """
        将消息列表摘要成简短描述
        
        使用 LLM 生成摘要，不截断原始内容
        """
        if not messages:
            return ""
        
        # 构建完整对话文本（不截断）
        conversation_text = ""
        for msg in messages:
            role = "用户" if msg["role"] == "user" else "助手"
            content = msg.get("content", "")
            if isinstance(content, str):
                conversation_text += f"{role}: {content}\n"
            elif isinstance(content, list):
                # 复杂内容保留完整文本部分
                texts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        texts.append(item.get("text", ""))
                if texts:
                    conversation_text += f"{role}: {' '.join(texts)}\n"
        
        if not conversation_text:
            return ""
        
        try:
            # 使用 LLM 生成摘要（在线程池中执行同步调用）
            response = await asyncio.to_thread(
                self.brain.messages_create,
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
            # 回退: 返回消息数量提示
            return f"[早期对话共 {len(messages)} 条消息]"
    
    async def _compress_long_messages(self, messages: list[dict], max_tokens: int) -> list[dict]:
        """
        压缩过长的消息内容（使用 LLM 压缩，不直接截断）
        
        策略: 保留最近消息完整，早期消息用 LLM 压缩
        """
        current_tokens = self._estimate_messages_tokens(messages)
        
        if current_tokens <= max_tokens:
            return messages
        
        # 保留最近 4 条消息完整
        recent_count = min(4, len(messages))
        recent_messages = messages[-recent_count:] if recent_count > 0 else []
        early_messages = messages[:-recent_count] if len(messages) > recent_count else []
        
        if not early_messages:
            # 只有最近消息，无法再压缩，原样返回
            logger.warning("Cannot compress further, only recent messages left")
            return messages
        
        # 用 LLM 压缩早期消息
        summary = await self._summarize_messages(early_messages)
        
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
        
        logger.info(f"Compressed context from {current_tokens} to {self._estimate_messages_tokens(compressed)} tokens")
        return compressed
    
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
        logger.info(f"{session_info}User: {message}")
        
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
        
        logger.info(f"{session_info}Agent: {response_text}")
        
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
        
        # === 设置当前会话（供中断检查使用）===
        self._current_session = session
        
        # 设置当前会话到日志缓存（供 get_session_logs 工具使用）
        from ..logging import get_session_log_buffer
        get_session_log_buffer().set_current_session(session_id)
        
        try:
            logger.info(f"[Session:{session_id}] User: {message}")
            
            # 记录用户消息到 conversation_history（用于凌晨归纳）
            self.memory_manager.record_turn("user", message)
            
            # === 两段式 Prompt 第一阶段：Prompt Compiler ===
            # 对复杂请求进行结构化分析（独立上下文，不进入核心对话）
            compiled_message = message
            compiler_output = ""
            
            if self._should_compile_prompt(message):
                compiled_message, compiler_output = await self._compile_prompt(message)
                if compiler_output:
                    logger.info(f"[Session:{session_id}] Prompt compiled")
            
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
            
            # 添加当前用户消息（支持多模态：文本 + 图片）
            pending_images = session.get_metadata("pending_images") if session else None
            
            if pending_images:
                # 多模态消息：文本 + 图片
                content_parts = []
                
                # 添加文本部分（使用编译后的消息）
                if compiled_message.strip():
                    content_parts.append({
                        "type": "text",
                        "text": compiled_message,
                    })
                
                # 添加图片部分
                for img_data in pending_images:
                    content_parts.append(img_data)
                
                messages.append({
                    "role": "user",
                    "content": content_parts,
                })
                logger.info(f"[Session:{session_id}] Multimodal message with {len(pending_images)} images")
            else:
                # 普通文本消息（使用编译后的消息）
                messages.append({
                    "role": "user",
                    "content": compiled_message,
                })
            
            # 压缩上下文（如果需要）
            messages = await self._compress_context(messages)
            
            # === 创建任务监控器 ===
            task_monitor = TaskMonitor(
                task_id=f"{session_id}_{datetime.now().strftime('%H%M%S')}",
                description=message[:100],
                session_id=session_id,
                timeout_seconds=300,  # 超时阈值：300秒
                retrospect_threshold=60,  # 复盘阈值：60秒
                fallback_model="gpt-4o",  # 超时后切换的备用模型
            )
            task_monitor.start(self.brain.model)
            
            # === 两段式 Prompt 第二阶段：主模型处理 ===
            response_text = await self._chat_with_tools_and_context(
                messages, 
                task_monitor=task_monitor
            )
            
            # === 完成任务监控 ===
            metrics = task_monitor.complete(
                success=True,
                response=response_text[:200],
            )
            
            # === 后台复盘分析（如果任务耗时过长，不阻塞响应） ===
            if metrics.retrospect_needed:
                # 创建后台任务执行复盘，不等待结果
                asyncio.create_task(
                    self._do_task_retrospect_background(task_monitor, session_id)
                )
                logger.info(f"[Session:{session_id}] Task retrospect scheduled (background)")
            
            # 记录 Agent 响应到 conversation_history（用于凌晨归纳）
            self.memory_manager.record_turn("assistant", response_text)
            
            logger.info(f"[Session:{session_id}] Agent: {response_text}")
            
            return response_text
        finally:
            # 清除 IM 会话信息
            Agent._current_im_session = None
            Agent._current_im_gateway = None
            # 清除当前会话引用
            self._current_session = None
    
    async def _compile_prompt(self, user_message: str) -> tuple[str, str]:
        """
        两段式 Prompt 第一阶段：Prompt Compiler
        
        将用户的原始请求转化为结构化的任务定义。
        使用独立上下文，不进入核心对话历史。
        
        Args:
            user_message: 用户原始消息
            
        Returns:
            (compiled_prompt, raw_compiler_output)
            - compiled_prompt: 增强后的提示词（原始消息 + 结构化分析）
            - raw_compiler_output: Prompt Compiler 的原始输出（用于日志）
        """
        try:
            # 调用 Brain 进行 Prompt 编译（独立上下文，使用快速模型）
            response = await self.brain.think(
                prompt=user_message,
                system=PROMPT_COMPILER_SYSTEM,
            )
            
            # 移除 thinking 标签
            compiler_output = strip_thinking_tags(response.content).strip() if response.content else ""
            
            # 构建增强后的提示词
            enhanced_prompt = f"""## 用户原始请求
{user_message}

## 任务分析（由 Prompt Compiler 生成）
{compiler_output}

---
请根据以上任务分析来处理用户的请求。"""
            
            logger.info(f"Prompt compiled: {compiler_output}")
            return enhanced_prompt, compiler_output
            
        except Exception as e:
            logger.warning(f"Prompt compilation failed: {e}, using original message")
            # 编译失败时直接使用原始消息
            return user_message, ""
    
    async def _do_task_retrospect(self, task_monitor: TaskMonitor) -> str:
        """
        执行任务复盘分析
        
        当任务耗时过长时，让 LLM 分析原因，找出可以改进的地方。
        
        Args:
            task_monitor: 任务监控器
        
        Returns:
            复盘分析结果
        """
        try:
            context = task_monitor.get_retrospect_context()
            prompt = RETROSPECT_PROMPT.format(context=context)
            
            # 使用 Brain 进行复盘分析（独立上下文）
            response = await self.brain.think(
                prompt=prompt,
                system="你是一个任务执行分析专家。请简洁地分析任务执行情况，找出耗时原因和改进建议。",
            )
            
            result = strip_thinking_tags(response.content).strip() if response.content else ""
            
            # 保存复盘结果到监控器
            task_monitor.metrics.retrospect_result = result
            
            # 如果发现明显的重复错误模式，记录到记忆中
            if "重复" in result or "无效" in result or "弯路" in result:
                try:
                    from ..memory.types import Memory, MemoryType, MemoryPriority
                    memory = Memory(
                        type=MemoryType.ERROR,
                        priority=MemoryPriority.LONG_TERM,
                        content=f"任务执行复盘发现问题：{result[:200]}",
                        source="retrospect",
                        importance_score=0.7,
                    )
                    self.memory_manager.add_memory(memory)
                except Exception as e:
                    logger.warning(f"Failed to save retrospect to memory: {e}")
            
            return result
            
        except Exception as e:
            logger.warning(f"Task retrospect failed: {e}")
            return ""
    
    async def _do_task_retrospect_background(
        self, 
        task_monitor: TaskMonitor, 
        session_id: str
    ) -> None:
        """
        后台执行任务复盘分析
        
        这个方法在后台异步执行，不阻塞主响应。
        复盘结果会保存到文件，供每日自检系统读取汇总。
        
        Args:
            task_monitor: 任务监控器
            session_id: 会话 ID
        """
        try:
            # 执行复盘分析
            retrospect_result = await self._do_task_retrospect(task_monitor)
            
            if not retrospect_result:
                return
            
            # 保存到复盘存储
            from .task_monitor import RetrospectRecord, get_retrospect_storage
            
            record = RetrospectRecord(
                task_id=task_monitor.metrics.task_id,
                session_id=session_id,
                description=task_monitor.metrics.description,
                duration_seconds=task_monitor.metrics.total_duration_seconds,
                iterations=task_monitor.metrics.total_iterations,
                model_switched=task_monitor.metrics.model_switched,
                initial_model=task_monitor.metrics.initial_model,
                final_model=task_monitor.metrics.final_model,
                retrospect_result=retrospect_result,
            )
            
            storage = get_retrospect_storage()
            storage.save(record)
            
            logger.info(f"[Session:{session_id}] Retrospect saved: {task_monitor.metrics.task_id}")
            
        except Exception as e:
            logger.error(f"[Session:{session_id}] Background retrospect failed: {e}")
    
    def _should_compile_prompt(self, message: str) -> bool:
        """
        判断是否需要进行 Prompt 编译
        
        简单的消息（问候、简单提醒等）不需要编译
        复杂的任务请求才需要编译
        """
        # 简单消息的特征
        simple_patterns = [
            r'^(你好|hi|hello|嗨|hey)[\s\!]*$',
            r'^(谢谢|感谢|thanks|thank you)[\s\!]*$',
            r'^(好的|ok|好|嗯|哦)[\s\!]*$',
            r'^(再见|拜拜|bye)[\s\!]*$',
            r'^\d+分钟后(提醒|叫)我',  # 简单提醒
            r'^(现在)?几点',  # 问时间
        ]
        
        message_lower = message.lower().strip()
        
        # 检查是否匹配简单消息模式
        for pattern in simple_patterns:
            if re.match(pattern, message_lower, re.IGNORECASE):
                return False
        
        # 消息太短（少于 10 个字符）不需要编译
        if len(message.strip()) < 10:
            return False
        
        # 其他情况都进行编译
        return True
    
    async def _chat_with_tools_and_context(
        self, 
        messages: list[dict], 
        use_session_prompt: bool = True,
        task_monitor: Optional[TaskMonitor] = None,
    ) -> str:
        """
        使用指定的消息上下文进行对话（支持工具调用）
        
        这是 _chat_with_tools 的变体，使用传入的 messages 而不是 self._context.messages
        
        安全模型切换策略：
        1. 超时或错误时先重试 3 次
        2. 重试次数用尽后才切换到备用模型
        3. 切换时废弃已有的工具调用历史，从用户原始请求开始重新处理
        
        Args:
            messages: 对话消息列表
            use_session_prompt: 是否使用 Session 专用的 System Prompt（不包含全局 Active Task）
            task_monitor: 任务监控器（可选，用于跟踪执行时间和超时切换模型）
        
        Returns:
            最终响应文本
        """
        max_iterations = settings.max_iterations  # Ralph Wiggum 模式：永不放弃
        
        # === 关键：保存原始用户消息，用于模型切换时重置上下文 ===
        # 只提取用户消息（不包含工具调用历史）
        original_user_messages = [
            msg for msg in messages 
            if msg.get("role") == "user" and isinstance(msg.get("content"), str)
        ]
        
        # 复制消息避免修改原始列表
        working_messages = list(messages)
        
        # 选择 System Prompt
        if use_session_prompt:
            # 使用 Session 专用的 System Prompt，不包含全局 Active Task
            system_prompt = self.identity.get_session_system_prompt()
        else:
            system_prompt = self._context.system
        
        # 获取当前模型
        current_model = self.brain.model
        
        for iteration in range(max_iterations):
            # 任务监控：开始迭代
            if task_monitor:
                task_monitor.begin_iteration(iteration + 1, current_model)
                
                # === 安全模型切换检查 ===
                # 检查是否超时且重试次数已用尽
                if task_monitor.should_switch_model:
                    new_model = task_monitor.fallback_model
                    task_monitor.switch_model(
                        new_model, 
                        f"任务执行超过 {task_monitor.timeout_seconds} 秒，重试 {task_monitor.retry_count} 次后切换",
                        reset_context=True
                    )
                    current_model = new_model
                    
                    # === 关键：重置上下文，废弃工具调用历史 ===
                    logger.warning(
                        f"[ModelSwitch] Switching to {new_model}, resetting context. "
                        f"Discarding {len(working_messages) - len(original_user_messages)} tool-related messages"
                    )
                    working_messages = list(original_user_messages)
                    
                    # 添加模型切换说明，让新模型了解情况
                    working_messages.append({
                        "role": "user",
                        "content": (
                            "[系统提示] 之前的模型处理超时，现已切换到新模型。"
                            "请从头开始处理上面的用户请求，不要依赖任何之前的上下文。"
                        ),
                    })
            
            # 每次迭代前检查上下文大小
            if iteration > 0:
                working_messages = await self._compress_context(working_messages)
            
            # 调用 Brain，传递工具列表（在线程池中执行同步调用，避免事件循环冲突）
            try:
                response = await asyncio.to_thread(
                    self.brain.messages_create,
                    model=current_model,
                    max_tokens=self.brain.max_tokens,
                    system=system_prompt,
                    tools=self._tools,
                    messages=working_messages,
                )
                
                # 成功调用，重置重试计数
                if task_monitor:
                    task_monitor.reset_retry_count()
                    
            except Exception as e:
                logger.error(f"[LLM] Brain call failed: {e}")
                
                # 记录错误并判断是否应该重试
                if task_monitor:
                    should_retry = task_monitor.record_error(str(e))
                    
                    if should_retry:
                        # 继续重试，跳过这次迭代
                        logger.info(f"[LLM] Will retry (attempt {task_monitor.retry_count}/{task_monitor.retry_before_switch})")
                        await asyncio.sleep(2)  # 等待 2 秒后重试
                        continue
                    else:
                        # 重试次数用尽，切换模型
                        new_model = task_monitor.fallback_model
                        task_monitor.switch_model(
                            new_model,
                            f"LLM 调用失败，重试 {task_monitor.retry_count} 次后切换: {e}",
                            reset_context=True
                        )
                        current_model = new_model
                        
                        # 重置上下文
                        logger.warning(f"[ModelSwitch] Switching to {new_model} due to errors, resetting context")
                        working_messages = list(original_user_messages)
                        working_messages.append({
                            "role": "user",
                            "content": (
                                "[系统提示] 之前的模型调用失败，现已切换到新模型。"
                                "请从头开始处理上面的用户请求。"
                            ),
                        })
                        continue
                else:
                    # 没有 task_monitor，直接抛出异常
                    raise
            
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
            
            # 任务监控：结束迭代
            if task_monitor:
                task_monitor.end_iteration(text_content[:200] if text_content else "")
            
            # 如果没有工具调用，返回文本响应（过滤 thinking 标签）
            if not tool_calls:
                return strip_thinking_tags(text_content) or "我理解了您的请求。"
            
            # 有工具调用，添加助手消息
            # MiniMax M2.1 Interleaved Thinking 支持：
            # 必须完整保留 thinking 块以保持思维链连续性
            assistant_content = []
            for block in response.content:
                if block.type == "thinking":
                    # 保留 thinking 块（MiniMax M2.1 要求）
                    assistant_content.append({
                        "type": "thinking",
                        "thinking": block.thinking if hasattr(block, 'thinking') else str(block),
                    })
                elif block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
            
            working_messages.append({
                "role": "assistant",
                "content": assistant_content,
            })
            
            # 执行工具调用（支持中断检查）
            tool_results = []
            interrupt_detected = False
            
            for i, tc in enumerate(tool_calls):
                # === 中断检查点 ===
                # 在每个工具调用之前检查是否有新消息（第一个工具除外）
                if i > 0:
                    interrupt_hint = await self._check_interrupt()
                    if interrupt_hint:
                        logger.info(f"[Interrupt] Detected during tool execution in context mode, tool {i+1}/{len(tool_calls)}")
                        interrupt_detected = True
                        # 将中断提示添加到结果中
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": f"{interrupt_hint}\n\n注意：由于用户发送了新消息，请尽快完成当前任务或询问用户是否需要处理新消息。",
                        })
                        # 跳过剩余的工具调用
                        for remaining_tc in tool_calls[i+1:]:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": remaining_tc["id"],
                                "content": "[工具调用已跳过: 用户发送了新消息]",
                            })
                        # 任务监控：记录中断
                        if task_monitor:
                            task_monitor.end_tool_call("用户中断", success=False)
                        break
                
                # 任务监控：开始工具调用
                if task_monitor:
                    task_monitor.begin_tool_call(tc["name"], tc["input"])
                
                try:
                    result = await self._execute_tool(tc["name"], tc["input"])
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": str(result) if result else "操作已完成",
                    })
                    # 任务监控：结束工具调用（成功）
                    if task_monitor:
                        task_monitor.end_tool_call(str(result)[:200] if result else "", success=True)
                except Exception as e:
                    logger.error(f"Tool {tc['name']} error: {e}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": f"工具执行错误: {str(e)}",
                        "is_error": True,
                    })
                    # 任务监控：结束工具调用（失败）
                    if task_monitor:
                        task_monitor.end_tool_call(str(e), success=False)
            
            # 添加工具结果
            working_messages.append({
                "role": "user",
                "content": tool_results,
            })
        
        return "已达到最大工具调用次数，请重新描述您的需求。"
    
    # ==================== 消息中断机制 ====================
    
    async def _check_interrupt(self) -> Optional[str]:
        """
        检查是否有需要插入的中断消息
        
        在工具调用间隙调用此方法，检查是否有新消息需要处理
        
        Returns:
            如果有中断消息，返回消息文本；否则返回 None
        """
        if not self._interrupt_enabled or not self._current_session:
            return None
        
        # 从 session metadata 获取 gateway 引用
        gateway = self._current_session.get_metadata("_gateway")
        session_key = self._current_session.get_metadata("_session_key")
        
        if not gateway or not session_key:
            return None
        
        # 检查是否有待处理的中断消息
        if gateway.has_pending_interrupt(session_key):
            interrupt_count = gateway.get_interrupt_count(session_key)
            logger.info(f"[Interrupt] Detected {interrupt_count} pending message(s) for session {session_key}")
            return f"[系统提示: 用户发送了 {interrupt_count} 条新消息，请在完成当前工具调用后处理]"
        
        return None
    
    async def _get_interrupt_message(self) -> Optional[str]:
        """
        获取并返回中断消息的内容
        
        Returns:
            中断消息文本，如果没有则返回 None
        """
        if not self._current_session:
            return None
        
        gateway = self._current_session.get_metadata("_gateway")
        session_key = self._current_session.get_metadata("_session_key")
        
        if not gateway or not session_key:
            return None
        
        # 获取中断消息
        interrupt_msg = await gateway.check_interrupt(session_key)
        if interrupt_msg:
            return interrupt_msg.plain_text
        
        return None
    
    def set_interrupt_enabled(self, enabled: bool) -> None:
        """
        设置是否启用中断检查
        
        Args:
            enabled: 是否启用
        """
        self._interrupt_enabled = enabled
        logger.info(f"Interrupt check {'enabled' if enabled else 'disabled'}")
    
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
        
        max_iterations = settings.max_iterations  # Ralph Wiggum 模式：永不放弃
        
        # 防止循环检测
        recent_tool_calls: list[str] = []
        max_repeated_calls = 3
        
        for iteration in range(max_iterations):
            # 每次迭代前检查上下文大小（工具调用可能产生大量输出）
            if iteration > 0:
                messages = await self._compress_context(messages)
            
            # 调用 Brain，传递工具列表（在线程池中执行同步调用）
            response = await asyncio.to_thread(
                self.brain.messages_create,
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
                return strip_thinking_tags(text_content)
            
            # 循环检测
            call_signature = "|".join([f"{tc['name']}:{sorted(tc['input'].items())}" for tc in tool_calls])
            recent_tool_calls.append(call_signature)
            if len(recent_tool_calls) > max_repeated_calls:
                recent_tool_calls = recent_tool_calls[-max_repeated_calls:]
            
            if len(recent_tool_calls) >= max_repeated_calls and len(set(recent_tool_calls)) == 1:
                logger.warning(f"[Loop Detection] Same tool call repeated {max_repeated_calls} times, ending chat")
                return "检测到重复操作，已自动结束。"
            
            # 有工具调用，需要执行
            logger.info(f"Chat iteration {iteration + 1}, {len(tool_calls)} tool calls")
            
            # 构建 assistant 消息
            # MiniMax M2.1 Interleaved Thinking 支持：
            # 必须完整保留 thinking 块以保持思维链连续性
            assistant_content = []
            for block in response.content:
                if block.type == "thinking":
                    # 保留 thinking 块（MiniMax M2.1 要求）
                    assistant_content.append({
                        "type": "thinking",
                        "thinking": block.thinking if hasattr(block, 'thinking') else str(block),
                    })
                elif block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
            
            messages.append({"role": "assistant", "content": assistant_content})
            
            # 执行工具并收集结果（支持中断检查）
            tool_results = []
            interrupt_detected = False
            
            for i, tool_call in enumerate(tool_calls):
                # === 中断检查点 ===
                # 在每个工具调用之前检查是否有新消息
                if i > 0:  # 第一个工具不检查，避免过早中断
                    interrupt_hint = await self._check_interrupt()
                    if interrupt_hint:
                        logger.info(f"[Interrupt] Detected during tool execution, tool {i+1}/{len(tool_calls)}")
                        interrupt_detected = True
                        # 将中断提示添加到结果中
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_call["id"],
                            "content": f"{interrupt_hint}\n\n注意：由于用户发送了新消息，请尽快完成当前任务或询问用户是否需要处理新消息。",
                        })
                        # 跳过剩余的工具调用
                        for remaining_call in tool_calls[i+1:]:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": remaining_call["id"],
                                "content": "[工具调用已跳过: 用户发送了新消息]",
                            })
                        break
                
                # 正常执行工具
                result = await self._execute_tool(tool_call["name"], tool_call["input"])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call["id"],
                    "content": result,
                })
                logger.info(f"Tool {tool_call['name']} result: {result[:200]}..." if len(result) > 200 else f"Tool {tool_call['name']} result: {result}")
            
            messages.append({"role": "user", "content": tool_results})
            
            # 如果检测到中断，在下一轮迭代中 LLM 会看到中断提示
            
            # 检查是否结束
            if response.stop_reason == "end_turn":
                break
        
        # 返回最后一次的文本响应（过滤 thinking 标签）
        return strip_thinking_tags(text_content) or "操作完成"
    
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
                command = tool_input["command"]
                # 使用 LLM 指定的超时时间，默认 60 秒
                timeout = tool_input.get("timeout", 60)
                # 限制范围：最小 10 秒，最大 600 秒
                timeout = max(10, min(timeout, 600))
                
                result = await self.shell_tool.run(
                    command,
                    cwd=tool_input.get("cwd"),
                    timeout=timeout,
                )
                
                # 记录命令输出到会话日志缓存（供 AI 回顾）
                from ..logging import get_session_log_buffer
                log_buffer = get_session_log_buffer()
                
                command_preview = tool_input["command"][:100]
                if len(tool_input["command"]) > 100:
                    command_preview += "..."
                
                # 记录命令和输出
                output_preview = result.stdout[:500] if result.stdout else ""
                if len(result.stdout or "") > 500:
                    output_preview += f"\n... (共 {len(result.stdout)} 字符)"
                
                if result.success:
                    log_buffer.add_log(
                        level="INFO",
                        module="shell",
                        message=f"$ {command_preview}\n[exit: 0]\n{output_preview}",
                    )
                    return f"命令执行成功 (exit code: 0):\n{result.stdout}"
                else:
                    # 记录失败的命令
                    error_output = result.stderr[:500] if result.stderr else ""
                    log_buffer.add_log(
                        level="ERROR",
                        module="shell",
                        message=f"$ {command_preview}\n[exit: {result.returncode}]\nstdout: {output_preview}\nstderr: {error_output}",
                    )
                    
                    # 返回完整信息帮助AI理解错误
                    output_parts = [f"命令执行失败 (exit code: {result.returncode})"]
                    if result.stdout:
                        output_parts.append(f"[stdout]:\n{result.stdout}")
                    if result.stderr:
                        output_parts.append(f"[stderr]:\n{result.stderr}")
                    if not result.stdout and not result.stderr:
                        output_parts.append("(无输出，可能命令不存在或语法错误)")
                    # 提示 AI 查看日志或尝试其他方法
                    output_parts.append("\n提示: 如果不确定原因，可以调用 get_session_logs 查看详细日志，或尝试其他命令。")
                    return "\n".join(output_parts)
            
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
                    output += f"  {skill.description}\n\n"
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
            
            elif tool_name == "install_skill":
                source = tool_input["source"]
                name = tool_input.get("name")
                subdir = tool_input.get("subdir")
                extra_files = tool_input.get("extra_files", [])
                
                result = await self._install_skill(source, name, subdir, extra_files)
                return result
            
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
                    return f"✅ 已记住: [{mem_type_str}] {content}\nID: {memory_id}"
                else:
                    return "✅ 记忆已存在（语义相似），无需重复记录。请继续执行其他任务或结束。"
            
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
            
            # === 工具信息查询（渐进式披露 Level 2）===
            elif tool_name == "get_tool_info":
                tool_name_to_query = tool_input["tool_name"]
                return self.tool_catalog.get_tool_info_formatted(tool_name_to_query)
            
            # === MCP 工具 ===
            elif tool_name == "call_mcp_tool":
                server = tool_input["server"]
                mcp_tool_name = tool_input["tool_name"]
                arguments = tool_input.get("arguments", {})
                
                # 检查服务器是否已连接
                if server not in self.mcp_client.list_connected():
                    # 尝试连接
                    connected = await self.mcp_client.connect(server)
                    if not connected:
                        return f"❌ 无法连接到 MCP 服务器: {server}"
                
                result = await self.mcp_client.call_tool(server, mcp_tool_name, arguments)
                
                if result.success:
                    return f"✅ MCP 工具调用成功:\n{result.data}"
                else:
                    return f"❌ MCP 工具调用失败: {result.error}"
            
            elif tool_name == "list_mcp_servers":
                servers = self.mcp_catalog.list_servers()
                connected = self.mcp_client.list_connected()
                
                if not servers:
                    return "当前没有配置 MCP 服务器\n\n提示: MCP 服务器配置放在 mcps/ 目录下"
                
                output = f"已配置 {len(servers)} 个 MCP 服务器:\n\n"
                for server_id in servers:
                    status = "🟢 已连接" if server_id in connected else "⚪ 未连接"
                    output += f"- **{server_id}** {status}\n"
                
                output += "\n使用 `call_mcp_tool(server, tool_name, arguments)` 调用工具"
                return output
            
            elif tool_name == "get_mcp_instructions":
                server = tool_input["server"]
                instructions = self.mcp_catalog.get_server_instructions(server)
                
                if instructions:
                    return f"# MCP 服务器 {server} 使用说明\n\n{instructions}"
                else:
                    return f"❌ 未找到服务器 {server} 的使用说明，或服务器不存在"
            
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
                from ..scheduler.task import TaskType
                
                trigger_type = TriggerType(tool_input["trigger_type"])
                task_type = TaskType(tool_input.get("task_type", "task"))
                
                # 获取当前 IM 会话信息（如果有）
                channel_id = None
                chat_id = None
                user_id = None
                
                if Agent._current_im_session:
                    session = Agent._current_im_session
                    channel_id = session.channel
                    chat_id = session.chat_id
                    user_id = session.user_id
                
                task = ScheduledTask.create(
                    name=tool_input["name"],
                    description=tool_input["description"],
                    trigger_type=trigger_type,
                    trigger_config=tool_input["trigger_config"],
                    task_type=task_type,
                    reminder_message=tool_input.get("reminder_message"),
                    prompt=tool_input.get("prompt", ""),
                    user_id=user_id,
                    channel_id=channel_id,
                    chat_id=chat_id,
                )
                task.metadata["notify_on_start"] = tool_input.get("notify_on_start", True)
                task.metadata["notify_on_complete"] = tool_input.get("notify_on_complete", True)
                
                task_id = await self.task_scheduler.add_task(task)
                next_run = task.next_run.strftime('%Y-%m-%d %H:%M:%S') if task.next_run else '待计算'
                
                # 任务类型显示
                type_display = "📝 简单提醒" if task_type == TaskType.REMINDER else "🔧 复杂任务"
                
                # 控制台输出任务创建信息
                print(f"\n📅 定时任务已创建:")
                print(f"   ID: {task_id}")
                print(f"   名称: {task.name}")
                print(f"   类型: {type_display}")
                print(f"   触发: {task.trigger_type.value}")
                print(f"   下次执行: {next_run}")
                if channel_id and chat_id:
                    print(f"   通知渠道: {channel_id}/{chat_id}")
                print()
                
                logger.info(f"Created scheduled task: {task_id} ({task.name}), type={task_type.value}, next run: {next_run}")
                
                return f"✅ 已创建{type_display}\n- ID: {task_id}\n- 名称: {task.name}\n- 下次执行: {next_run}"
            
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
            
            elif tool_name == "update_scheduled_task":
                if not hasattr(self, 'task_scheduler') or not self.task_scheduler:
                    return "❌ 定时任务调度器未启动"
                task_id = tool_input["task_id"]
                task = self.task_scheduler.get_task(task_id)
                if not task:
                    return f"❌ 任务 {task_id} 不存在"
                changes = []
                if "notify_on_start" in tool_input:
                    task.metadata["notify_on_start"] = tool_input["notify_on_start"]
                    changes.append("开始通知: " + ("开" if tool_input["notify_on_start"] else "关"))
                if "notify_on_complete" in tool_input:
                    task.metadata["notify_on_complete"] = tool_input["notify_on_complete"]
                    changes.append("完成通知: " + ("开" if tool_input["notify_on_complete"] else "关"))
                if "enabled" in tool_input:
                    if tool_input["enabled"]:
                        task.enable()
                        changes.append("已启用")
                    else:
                        task.disable()
                        changes.append("已暂停")
                self.task_scheduler._save_tasks()
                if changes:
                    return f"✅ 任务 {task.name} 已更新: " + ", ".join(changes)
                return "⚠️ 没有指定要修改的设置"
            
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
            
            # === Thinking 模式控制 ===
            elif tool_name == "enable_thinking":
                enabled = tool_input["enabled"]
                reason = tool_input.get("reason", "")
                
                self.brain.set_thinking_mode(enabled)
                
                if enabled:
                    logger.info(f"Thinking mode enabled by LLM: {reason}")
                    return f"✅ 已启用深度思考模式。原因: {reason}\n后续回复将使用更强的推理能力。"
                else:
                    logger.info(f"Thinking mode disabled by LLM: {reason}")
                    return f"✅ 已关闭深度思考模式。原因: {reason}\n将使用快速响应模式。"
            
            # === 用户档案工具 ===
            elif tool_name == "update_user_profile":
                key = tool_input["key"]
                value = tool_input["value"]
                
                available_keys = self.profile_manager.get_available_keys()
                if key not in available_keys:
                    return f"❌ 未知的档案项: {key}\n可用的键: {', '.join(available_keys)}"
                
                success = self.profile_manager.update_profile(key, value)
                if success:
                    return f"✅ 已更新用户档案: {key} = {value}"
                else:
                    return f"❌ 更新失败: {key}"
            
            elif tool_name == "skip_profile_question":
                key = tool_input["key"]
                self.profile_manager.skip_question(key)
                return f"✅ 已跳过问题: {key}"
            
            elif tool_name == "get_user_profile":
                summary = self.profile_manager.get_profile_summary()
                return summary
            
            # === 日志查询工具 ===
            elif tool_name == "get_session_logs":
                from ..logging import get_session_log_buffer
                
                count = tool_input.get("count", 20)
                level_filter = tool_input.get("level")
                
                # 限制最大条数
                count = min(max(1, count), 200)
                
                buffer = get_session_log_buffer()
                logs_text = buffer.get_logs_formatted(
                    count=count,
                    level_filter=level_filter,
                )
                
                stats = buffer.get_stats()
                session_id = stats.get("current_session", "_global")
                total_logs = stats.get("sessions", {}).get(session_id, 0)
                
                return f"📋 会话日志（最近 {count} 条，共 {total_logs} 条）:\n\n{logs_text}"
            
            # === IM 通道工具 ===
            elif tool_name == "send_to_chat":
                # 检查是否在 IM 会话中
                if not Agent._current_im_session or not Agent._current_im_gateway:
                    return "❌ 此工具仅在 IM 会话中可用（当前不是 IM 会话）"
                
                session = Agent._current_im_session
                gateway = Agent._current_im_gateway
                
                text = tool_input.get("text", "")
                file_path = tool_input.get("file_path", "")
                voice_path = tool_input.get("voice_path", "")
                caption = tool_input.get("caption", "")
                
                try:
                    from pathlib import Path
                    
                    # 获取适配器
                    adapter = gateway.get_adapter(session.channel)
                    if not adapter:
                        return f"❌ 找不到适配器: {session.channel}"
                    
                    # 发送语音
                    if voice_path:
                        voice_path_obj = Path(voice_path)
                        if not voice_path_obj.exists():
                            return f"❌ 语音文件不存在: {voice_path}"
                        
                        if hasattr(adapter, 'send_voice'):
                            await adapter.send_voice(
                                chat_id=session.chat_id,
                                voice_path=str(voice_path_obj),
                                caption=caption or text,
                            )
                            self._task_message_sent = True
                            return f"✅ 语音已发送: {voice_path}"
                        else:
                            # 适配器不支持语音，改为发送文件
                            await adapter.send_file(
                                chat_id=session.chat_id,
                                file_path=str(voice_path_obj),
                                caption=caption or text,
                            )
                            self._task_message_sent = True
                            return f"✅ 语音文件已发送（作为文件）: {voice_path}"
                    
                    # 发送文件/图片
                    if file_path:
                        file_path_obj = Path(file_path)
                        if not file_path_obj.exists():
                            return f"❌ 文件不存在: {file_path}"
                        
                        # 根据文件类型发送
                        suffix = file_path_obj.suffix.lower()
                        
                        if suffix in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
                            # 发送图片
                            await adapter.send_photo(
                                chat_id=session.chat_id,
                                photo_path=str(file_path_obj),
                                caption=caption or text,
                            )
                            self._task_message_sent = True
                            return f"✅ 图片已发送: {file_path}"
                        else:
                            # 发送文件
                            await adapter.send_file(
                                chat_id=session.chat_id,
                                file_path=str(file_path_obj),
                                caption=caption or text,
                            )
                            self._task_message_sent = True
                            return f"✅ 文件已发送: {file_path}"
                    
                    # 只发送文本
                    elif text:
                        await gateway.send_to_session(session, text)
                        self._task_message_sent = True
                        return f"✅ 消息已发送"
                    
                    else:
                        return "❌ 请提供要发送的内容（text, file_path 或 voice_path）"
                        
                except Exception as e:
                    logger.error(f"send_to_chat error: {e}", exc_info=True)
                    return f"❌ 发送失败: {str(e)}"
            
            elif tool_name == "get_voice_file":
                # 检查是否在 IM 会话中
                if not Agent._current_im_session:
                    return "❌ 此工具仅在 IM 会话中可用"
                
                session = Agent._current_im_session
                
                # 从 session metadata 获取语音文件信息
                pending_voices = session.get_metadata("pending_voices")
                if pending_voices and len(pending_voices) > 0:
                    voice_paths = [v.get("local_path", "") for v in pending_voices if v.get("local_path")]
                    if voice_paths:
                        return f"✅ 用户发送的语音文件路径:\n" + "\n".join(voice_paths)
                
                # 尝试从最近的消息中查找语音
                # 检查 session 的 messages
                for msg in reversed(session.messages[-10:]):
                    content = msg.get("content", "")
                    if isinstance(content, str) and "[语音:" in content:
                        # 尝试找到对应的本地文件
                        # 语音文件通常保存在 data/telegram/media/ 目录
                        media_dir = Path("data/telegram/media")
                        if media_dir.exists():
                            voice_files = list(media_dir.glob("*.ogg")) + list(media_dir.glob("*.oga")) + list(media_dir.glob("*.opus"))
                            if voice_files:
                                # 返回最新的语音文件
                                latest = max(voice_files, key=lambda f: f.stat().st_mtime)
                                return f"✅ 最近的语音文件: {latest}"
                
                return "❌ 没有找到用户发送的语音文件。请让用户先发送一条语音消息。"
            
            elif tool_name == "get_image_file":
                # 检查是否在 IM 会话中
                if not Agent._current_im_session:
                    return "❌ 此工具仅在 IM 会话中可用"
                
                session = Agent._current_im_session
                
                # 从 session metadata 获取图片文件信息
                pending_images = session.get_metadata("pending_images")
                if pending_images and len(pending_images) > 0:
                    # pending_images 是 multimodal 格式，找 local_path
                    image_paths = []
                    for img in pending_images:
                        if isinstance(img, dict):
                            # 尝试从元数据中获取路径
                            local_path = img.get("local_path", "")
                            if local_path:
                                image_paths.append(local_path)
                    if image_paths:
                        return f"✅ 用户发送的图片文件路径:\n" + "\n".join(image_paths)
                
                # 尝试从 media 目录查找
                media_dir = Path("data/telegram/media")
                if media_dir.exists():
                    image_files = list(media_dir.glob("*.jpg")) + list(media_dir.glob("*.png")) + list(media_dir.glob("*.webp"))
                    if image_files:
                        latest = max(image_files, key=lambda f: f.stat().st_mtime)
                        return f"✅ 最近的图片文件: {latest}"
                
                return "❌ 没有找到用户发送的图片文件。请让用户先发送一张图片。"
            
            elif tool_name == "get_chat_history":
                # 检查是否在 IM 会话中
                if not Agent._current_im_session:
                    return "❌ 此工具仅在 IM 会话中可用"
                
                session = Agent._current_im_session
                limit = tool_input.get("limit", 20)
                include_system = tool_input.get("include_system", True)
                
                # 从 session manager 获取聊天历史
                from ..sessions import session_manager
                
                history = session_manager.get_history(
                    channel=session.channel,
                    chat_id=session.chat_id,
                    user_id=session.user_id,
                    limit=limit
                )
                
                if not history:
                    return "📭 暂无聊天记录"
                
                # 格式化输出
                result_lines = [f"📜 最近 {len(history)} 条消息：\n"]
                for i, msg in enumerate(history, 1):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    timestamp = msg.get("timestamp", "")
                    
                    # 跳过系统消息（如果不需要）
                    if not include_system and role == "system":
                        continue
                    
                    # 角色标识
                    if role == "user":
                        role_icon = "👤 用户"
                    elif role == "assistant":
                        role_icon = "🤖 助手"
                    elif role == "system":
                        role_icon = "⚙️ 系统"
                    else:
                        role_icon = f"📌 {role}"
                    
                    # 格式化时间
                    time_str = ""
                    if timestamp:
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(timestamp)
                            time_str = f" ({dt.strftime('%H:%M')})"
                        except:
                            pass
                    
                    result_lines.append(f"{i}. {role_icon}{time_str}:\n   {content}\n")
                
                return "\n".join(result_lines)
            
            else:
                return f"未知工具: {tool_name}"
                
        except Exception as e:
            logger.error(f"Tool execution error: {e}", exc_info=True)
            return f"工具执行错误: {str(e)}"
    
    async def execute_task(self, task: Task) -> TaskResult:
        """
        执行任务（带工具调用）
        
        安全模型切换策略：
        1. 超时或错误时先重试 3 次
        2. 重试次数用尽后才切换到备用模型
        3. 切换时废弃已有的工具调用历史，从任务原始描述开始重新处理
        
        Args:
            task: 任务对象
        
        Returns:
            TaskResult
        """
        import time
        start_time = time.time()
        
        if not self._initialized:
            await self.initialize()
        
        logger.info(f"Executing task: {task.description}")
        
        # === 创建任务监控器 ===
        task_monitor = TaskMonitor(
            task_id=task.id,
            description=task.description,
            session_id=task.session_id,
            timeout_seconds=300,  # 超时阈值：300秒
            retrospect_threshold=60,  # 复盘阈值：60秒
            fallback_model="gpt-4o",  # 超时后切换的备用模型
            retry_before_switch=3,  # 切换前重试 3 次
        )
        task_monitor.start(self.brain.model)
        
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

        # === 关键：保存原始任务描述，用于模型切换时重置上下文 ===
        original_task_message = {"role": "user", "content": task.description}
        messages = [original_task_message.copy()]
        
        max_tool_iterations = settings.max_iterations  # Ralph Wiggum 模式：永不放弃
        iteration = 0
        final_response = ""
        current_model = self.brain.model
        
        # 防止循环检测
        recent_tool_calls: list[str] = []  # 记录最近的工具调用
        max_repeated_calls = 3  # 连续相同调用超过此次数则强制结束
        
        while iteration < max_tool_iterations:
            iteration += 1
            logger.info(f"Task iteration {iteration}")
            
            # 任务监控：开始迭代
            task_monitor.begin_iteration(iteration, current_model)
            
            # === 安全模型切换检查 ===
            # 检查是否超时且重试次数已用尽
            if task_monitor.should_switch_model:
                new_model = task_monitor.fallback_model
                task_monitor.switch_model(
                    new_model, 
                    f"任务执行超过 {task_monitor.timeout_seconds} 秒，重试 {task_monitor.retry_count} 次后切换",
                    reset_context=True
                )
                current_model = new_model
                
                # === 关键：重置上下文，废弃工具调用历史 ===
                logger.warning(
                    f"[ModelSwitch] Task {task.id}: Switching to {new_model}, resetting context. "
                    f"Discarding {len(messages) - 1} tool-related messages"
                )
                messages = [original_task_message.copy()]
                
                # 添加模型切换说明
                messages.append({
                    "role": "user",
                    "content": (
                        "[系统提示] 之前的模型处理超时，现已切换到新模型。"
                        "请从头开始处理上面的任务请求，不要依赖任何之前的上下文。"
                    ),
                })
                
                # 重置循环检测
                recent_tool_calls.clear()
            
            # 检查并压缩上下文（任务执行可能产生大量工具输出）
            if iteration > 1:
                messages = await self._compress_context(messages)
            
            # 调用 Brain（在线程池中执行同步调用）
            try:
                response = await asyncio.to_thread(
                    self.brain.messages_create,
                    model=current_model,
                    max_tokens=self.brain.max_tokens,
                    system=system_prompt,
                    tools=self._tools,
                    messages=messages,
                )
                
                # 成功调用，重置重试计数
                task_monitor.reset_retry_count()
                
            except Exception as e:
                logger.error(f"[LLM] Brain call failed in task {task.id}: {e}")
                
                # 记录错误并判断是否应该重试
                should_retry = task_monitor.record_error(str(e))
                
                if should_retry:
                    # 继续重试
                    logger.info(f"[LLM] Will retry (attempt {task_monitor.retry_count}/{task_monitor.retry_before_switch})")
                    await asyncio.sleep(2)
                    continue
                else:
                    # 重试次数用尽，切换模型
                    new_model = task_monitor.fallback_model
                    task_monitor.switch_model(
                        new_model,
                        f"LLM 调用失败，重试 {task_monitor.retry_count} 次后切换: {e}",
                        reset_context=True
                    )
                    current_model = new_model
                    
                    # 重置上下文
                    logger.warning(f"[ModelSwitch] Task {task.id}: Switching to {new_model} due to errors, resetting context")
                    messages = [original_task_message.copy()]
                    messages.append({
                        "role": "user",
                        "content": (
                            "[系统提示] 之前的模型调用失败，现已切换到新模型。"
                            "请从头开始处理上面的任务请求。"
                        ),
                    })
                    recent_tool_calls.clear()
                    continue
            
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
            
            # 任务监控：结束迭代
            task_monitor.end_iteration(text_content[:200] if text_content else "")
            
            # 如果有文本响应，保存（过滤 thinking 标签和工具调用模拟文本）
            if text_content:
                cleaned_text = clean_llm_response(text_content)
                # 只有在没有工具调用时才保存文本作为最终响应
                # 如果有工具调用，这个文本可能是 LLM 的思考过程
                if not tool_calls and cleaned_text:
                    final_response = cleaned_text
            
            # 如果没有工具调用，任务完成
            if not tool_calls:
                break
            
            # 循环检测：记录工具调用签名
            call_signature = "|".join([f"{tc['name']}:{sorted(tc['input'].items())}" for tc in tool_calls])
            recent_tool_calls.append(call_signature)
            
            # 只保留最近的调用记录
            if len(recent_tool_calls) > max_repeated_calls:
                recent_tool_calls = recent_tool_calls[-max_repeated_calls:]
            
            # 检测连续重复调用
            if len(recent_tool_calls) >= max_repeated_calls:
                if len(set(recent_tool_calls)) == 1:
                    logger.warning(f"[Loop Detection] Same tool call repeated {max_repeated_calls} times, forcing task end")
                    final_response = "任务执行中检测到重复操作，已自动结束。如需继续，请重新描述任务。"
                    break
            
            # 执行工具调用
            # MiniMax M2.1 Interleaved Thinking 支持：
            # 必须完整保留 thinking 块以保持思维链连续性
            assistant_content = []
            for block in response.content:
                if block.type == "thinking":
                    # 保留 thinking 块（MiniMax M2.1 要求）
                    assistant_content.append({
                        "type": "thinking",
                        "thinking": block.thinking if hasattr(block, 'thinking') else str(block),
                    })
                elif block.type == "text":
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
            executed_tools = []  # 记录执行的工具，用于生成摘要
            for tool_call in tool_calls:
                # 任务监控：开始工具调用
                task_monitor.begin_tool_call(tool_call["name"], tool_call["input"])
                
                try:
                    result = await self._execute_tool(tool_call["name"], tool_call["input"])
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_call["id"],
                        "content": result,
                    })
                    executed_tools.append({
                        "name": tool_call["name"],
                        "result_preview": result if result else ""
                    })
                    logger.info(f"Tool {tool_call['name']} result: {result}")
                    
                    # 任务监控：结束工具调用（成功）
                    task_monitor.end_tool_call(str(result)[:200] if result else "", success=True)
                except Exception as e:
                    logger.error(f"Tool {tool_call['name']} error: {e}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_call["id"],
                        "content": f"工具执行错误: {str(e)}",
                        "is_error": True,
                    })
                    # 任务监控：结束工具调用（失败）
                    task_monitor.end_tool_call(str(e), success=False)
            
            messages.append({"role": "user", "content": tool_results})
            
            # 注意：不在工具执行后检查 stop_reason，让循环继续获取 LLM 的最终总结
        
        # 循环结束后，如果 final_response 为空，尝试让 LLM 生成一个总结
        if not final_response or len(final_response.strip()) < 10:
            logger.info("Task completed but no final response, requesting summary...")
            try:
                # 请求 LLM 生成任务完成总结
                messages.append({
                    "role": "user", 
                    "content": "任务执行完毕。请简要总结一下执行结果和完成情况。"
                })
                summary_response = await asyncio.to_thread(
                    self.brain.messages_create,
                    model=current_model,
                    max_tokens=1000,
                    system=system_prompt,
                    messages=messages,
                )
                for block in summary_response.content:
                    if block.type == "text":
                        final_response = clean_llm_response(block.text)
                        break
            except Exception as e:
                logger.warning(f"Failed to get summary: {e}")
                final_response = "任务已执行完成。"
        
        # === 完成任务监控 ===
        metrics = task_monitor.complete(
            success=True,
            response=final_response[:200],
        )
        
        # === 后台复盘分析（如果任务耗时过长，不阻塞响应） ===
        if metrics.retrospect_needed:
            # 创建后台任务执行复盘，不等待结果
            asyncio.create_task(
                self._do_task_retrospect_background(task_monitor, task.session_id or task.id)
            )
            logger.info(f"[Task:{task.id}] Retrospect scheduled (background)")
        
        task.mark_completed(final_response)
        
        duration = time.time() - start_time
        
        return TaskResult(
            success=True,
            data=final_response,
            iterations=iteration,
            duration_seconds=duration,
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
        logger.warning(f"Ralph error for task {task.id}: {error}")
    
    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._initialized
    
    @property
    def conversation_history(self) -> list[dict]:
        """对话历史"""
        return self._conversation_history.copy()
    
    # ==================== 记忆系统方法 ====================
    
    def set_scheduler_gateway(self, gateway: Any) -> None:
        """
        设置定时任务调度器的消息网关
        
        用于定时任务执行后发送通知到 IM 通道
        
        Args:
            gateway: MessageGateway 实例
        """
        if hasattr(self, '_task_executor') and self._task_executor:
            self._task_executor.gateway = gateway
            logger.info("Scheduler gateway configured")
    
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
        
        # MEMORY.md 由 DailyConsolidator 在凌晨刷新，shutdown 时不同步
        
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

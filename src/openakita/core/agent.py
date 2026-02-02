"""
Agent ?? - ??????

?? OpenAkita ??????:
- ??????
- ??????
- ??????
- ?? Ralph ??
- ???????
- ????????????????

Skills ???? Agent Skills ?? (agentskills.io)
MCP ???? Model Context Protocol ?? (modelcontextprotocol.io)
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

# ???? (SKILL.md ??)
from ..skills import SkillRegistry, SkillLoader, SkillEntry, SkillCatalog

# MCP ??
from ..tools.mcp import MCPClient, mcp_client
from ..tools.mcp_catalog import MCPCatalog

# ?????????????
from ..tools.catalog import ToolCatalog

# ????
from ..memory import MemoryManager

# Windows Desktop Automation (Windows only)
import sys
_DESKTOP_AVAILABLE = False
_desktop_tool_handler = None
if sys.platform == "win32":
    try:
        from ..tools.desktop import DESKTOP_TOOLS, DesktopToolHandler
        _DESKTOP_AVAILABLE = True
        _desktop_tool_handler = DesktopToolHandler()
    except ImportError:
        pass

logger = logging.getLogger(__name__)

# ???????
DEFAULT_MAX_CONTEXT_TOKENS = 180000  # Claude 3.5 Sonnet ??????? (? 20k buffer)
CHARS_PER_TOKEN = 4  # ????: ? 4 ?? = 1 token
MIN_RECENT_TURNS = 4  # ?????? 4 ???
SUMMARY_TARGET_TOKENS = 500  # ???? token ?

# Prompt Compiler ????????? Prompt ?????
PROMPT_COMPILER_SYSTEM = """????
?? Prompt Compiler????????

????
????????

????
????????????????????????

??????
???? YAML ?????

```yaml
task_type: [????: question/action/creation/analysis/reminder/other]
goal: [?????????]
inputs:
  given: [????????]
  missing: [????????????????????]
constraints: [??????????????]
output_requirements: [??????]
risks_or_ambiguities: [????????????????]
```

????
- ??????
- ?????
- ????????
- ?????????????"AI???????"??
- ??? YAML ??????????
- ?????????????

????
??: "?????Python?????CSV???????????"

??:
```yaml
task_type: creation
goal: ??????CSV???????????Python??
inputs:
  given:
    - ??????????CSV
    - ?????????
    - ??Python??
  missing:
    - CSV????????
    - ??????????
output_requirements:
  - ????Python??
  - ????CSV??
  - ????????
constraints: []
risks_or_ambiguities:
  - ????????????????
  - ??????????????????????
```"""

import re

def strip_thinking_tags(text: str) -> str:
    """
    ????????????
    
    ??????????
    - <thinking>...</thinking> - Claude extended thinking
    - <think>...</think> - MiniMax/Qwen thinking ??
    - <minimax:tool_call>...</minimax:tool_call> - MiniMax ??????
    - <<|tool_calls_section_begin|>>...<<|tool_calls_section_end|>> - Kimi K2 ??????
    - </thinking> - ???????
    
    ???????????????
    """
    if not text:
        return text
    
    cleaned = text
    
    # ?? <thinking>...</thinking> ??????
    cleaned = re.sub(r'<thinking>.*?</thinking>\s*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    
    # ?? <think>...</think> ?????? (MiniMax/Qwen ??)
    cleaned = re.sub(r'<think>.*?</think>\s*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    
    # ?? <minimax:tool_call>...</minimax:tool_call> ??????
    cleaned = re.sub(r'<minimax:tool_call>.*?</minimax:tool_call>\s*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    
    # ?? Kimi K2 ??????
    cleaned = re.sub(r'<<\|tool_calls_section_begin\|>>.*?<<\|tool_calls_section_end\|>>\s*', '', cleaned, flags=re.DOTALL)
    
    # ?? <invoke>...</invoke> ??????????
    cleaned = re.sub(r'<invoke\s+[^>]*>.*?</invoke>\s*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    
    # ?????????
    cleaned = re.sub(r'</thinking>\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'</think>\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'</minimax:tool_call>\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'<<\|tool_calls_section_begin\|>>.*$', '', cleaned, flags=re.DOTALL)  # ????
    
    # ????? XML ????
    cleaned = re.sub(r'<\?xml[^>]*\?>\s*', '', cleaned)
    
    return cleaned.strip()


def strip_tool_simulation_text(text: str) -> str:
    """
    ?? LLM ?????????????
    
    ???????????????????LLM ???????
    "??"?????????:
    - get_skill_info("moltbook")
    - run_shell:0{"command": "..."}
    - read_file("path/to/file")
    
    ???????????????
    """
    if not text:
        return text
    
    # ??1: ?????? function_name("arg") ? function_name(arg)
    pattern1 = r'^[a-z_]+\s*\([^)]*\)\s*$'
    
    # ??2: ???????? tool_name:N{json} ? tool_name:N(args)
    pattern2 = r'^[a-z_]+:\d+[\{\(].*[\}\)]\s*$'
    
    # ??3: JSON ?????? {"tool": "name", ...}
    pattern3 = r'^\{["\']?(tool|function|name)["\']?\s*:'
    
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        stripped = line.strip()
        # ???????????
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
    ?? LLM ????
    
    ????:
    1. strip_thinking_tags - ??????
    2. strip_tool_simulation_text - ????????
    """
    if not text:
        return text
    
    cleaned = strip_thinking_tags(text)
    cleaned = strip_tool_simulation_text(cleaned)
    
    return cleaned.strip()


class Agent:
    """
    OpenAkita ??
    
    ???????AI????? Ralph Wiggum ???????
    """
    
    # ?????? (Claude API tool use format)
    BASE_TOOLS = [
        # === ?????? ===
        {
            "name": "run_shell",
            "description": "??Shell??????????????????????????????????????????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "????Shell??"},
                    "cwd": {"type": "string", "description": "????(??)"},
                    "timeout": {"type": "integer", "description": "????(?)???60???????30-60????/??????300???????????"}
                },
                "required": ["command"]
            }
        },
        {
            "name": "write_file",
            "description": "?????????????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "????"},
                    "content": {"type": "string", "description": "????"}
                },
                "required": ["path", "content"]
            }
        },
        {
            "name": "read_file",
            "description": "??????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "????"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "list_directory",
            "description": "??????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "????"}
                },
                "required": ["path"]
            }
        },
        # === Skills ?? (SKILL.md ??) ===
        {
            "name": "list_skills",
            "description": "???????? (?? Agent Skills ??)",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "get_skill_info",
            "description": "????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "????"}
                },
                "required": ["skill_name"]
            }
        },
        {
            "name": "run_skill_script",
            "description": "???????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "????"},
                    "script_name": {"type": "string", "description": "????? (? get_time.py)"},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "?????"}
                },
                "required": ["skill_name", "script_name"]
            }
        },
        {
            "name": "get_skill_reference",
            "description": "?????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "????"},
                    "ref_name": {"type": "string", "description": "?????? (?? REFERENCE.md)", "default": "REFERENCE.md"}
                },
                "required": ["skill_name"]
            }
        },
        {
            "name": "install_skill",
            "description": """? URL ? Git ????????? skills/ ???

???????
1. Git ?? URL (? https://github.com/user/repo ? git@github.com:user/repo.git)
   - ????????? SKILL.md
   - ?????????
2. ?? SKILL.md ?? URL
   - ???????? (scripts/, references/, assets/)

??????????? skills/<skill-name>/ ???""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Git ?? URL ? SKILL.md ?? URL"},
                    "name": {"type": "string", "description": "???? (?????? SKILL.md ??)"},
                    "subdir": {"type": "string", "description": "Git ????????????? (??)"},
                    "extra_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "????????? URL ??????????? (? HEARTBEAT.md)"
                    }
                },
                "required": ["source"]
            }
        },
        # === ????? ===
        {
            "name": "generate_skill",
            "description": "??????? (?? SKILL.md ??)???????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "?????????"},
                    "name": {"type": "string", "description": "???? (?????????????)"}
                },
                "required": ["description"]
            }
        },
        {
            "name": "improve_skill",
            "description": "??????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "????????"},
                    "feedback": {"type": "string", "description": "?????????"}
                },
                "required": ["skill_name", "feedback"]
            }
        },
        # === ???? ===
        {
            "name": "add_memory",
            "description": "??????????? (???????????????????)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "??????"},
                    "type": {"type": "string", "enum": ["fact", "preference", "skill", "error", "rule"], "description": "????"},
                    "importance": {"type": "number", "description": "??? (0-1)", "default": 0.5}
                },
                "required": ["content", "type"]
            }
        },
        {
            "name": "search_memory",
            "description": "??????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "?????"},
                    "type": {"type": "string", "enum": ["fact", "preference", "skill", "error", "rule"], "description": "?????? (??)"}
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_memory_stats",
            "description": "??????????",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        # === ????? (browser-use MCP) ===
        {
            "name": "browser_open",
            "description": "???????? **??????????????????? browser_status ?????????????????????**??????visible=True ???????(????)?visible=False ????(???)???????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "visible": {
                        "type": "boolean", 
                        "description": "True=???????(????), False=????(???)???True",
                        "default": True
                    },
                    "ask_user": {
                        "type": "boolean",
                        "description": "?????????",
                        "default": False
                    }
                }
            }
        },
        {
            "name": "browser_navigate",
            "description": "????? URL??? **????????????????????**??????? browser_type/browser_click ??????????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "???? URL"}
                },
                "required": ["url"]
            }
        },
        {
            "name": "browser_click",
            "description": "?????????**??????? browser_navigate ??????**",
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS ???"},
                    "text": {"type": "string", "description": "???? (??)"}
                }
            }
        },
        {
            "name": "browser_type",
            "description": "??????????**??????? browser_navigate ??????**",
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "??????"},
                    "text": {"type": "string", "description": "??????"}
                },
                "required": ["selector", "text"]
            }
        },
        {
            "name": "browser_get_content",
            "description": "?????? (??)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "????? (??)"}
                }
            }
        },
        {
            "name": "browser_screenshot",
            "description": "????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "???? (??)"}
                }
            }
        },
        {
            "name": "browser_status",
            "description": "??????????????????? URL ??????? tab ????? **????????????????????????????????????????????**?",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "browser_list_tabs",
            "description": "??????????(tabs)????? tab ????URL ????",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "browser_switch_tab",
            "description": "?????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "index": {"type": "number", "description": "????? (? 0 ?????? browser_list_tabs ??)"}
                },
                "required": ["index"]
            }
        },
        {
            "name": "browser_new_tab",
            "description": "???????????? URL????????????? **????????????? browser_status ?????????????**?",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "???? URL"}
                },
                "required": ["url"]
            }
        },
        # === ?????? ===
        # ????????
        # - ??/???? (cancel_scheduled_task) = ???????????
        # - ???? (update notify=false) = ??????????????
        # - ???? (update enabled=false) = ???????????
        {
            "name": "schedule_task",
            "description": "??????????"
                           "\n\n**?? ??: ????????**"
                           "\n? **reminder** (????): ????????????"
                           "\n   - '?????' ? reminder"
                           "\n   - '????' ? reminder"
                           "\n   - '????' ? reminder"
                           "\n   - '????' ? reminder"
                           "\n? **task** (????AI?????):"
                           "\n   - '???????' ? task (????)"
                           "\n   - '?????' ? task (????)"
                           "\n   - '????' ? task (????)"
                           "\n\n**90%??????? reminder ???????AI????????? task?**",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "??/????"},
                    "description": {"type": "string", "description": "??????????????"},
                    "task_type": {
                        "type": "string",
                        "enum": ["reminder", "task"],
                        "default": "reminder",
                        "description": "**???? reminder?** reminder=????????task=??AI????/??"
                    },
                    "trigger_type": {
                        "type": "string",
                        "enum": ["once", "interval", "cron"],
                        "description": "?????once=????interval=?????cron=cron???"
                    },
                    "trigger_config": {
                        "type": "object",
                        "description": "?????once: {run_at: '2026-02-01 10:00'}?interval: {interval_minutes: 30}?cron: {cron: '0 9 * * *'}"
                    },
                    "reminder_message": {
                        "type": "string",
                        "description": "???????? reminder ?????????????????"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "?????? Agent ????? task ?????AI ???????"
                    },
                    "notify_on_start": {
                        "type": "boolean",
                        "default": True,
                        "description": "???????????true?'????'??false"
                    },
                    "notify_on_complete": {
                        "type": "boolean",
                        "default": True,
                        "description": "???????????true?'????'??false"
                    }
                },
                "required": ["name", "description", "task_type", "trigger_type", "trigger_config"]
            }
        },
        {
            "name": "list_scheduled_tasks",
            "description": "?????????????ID????????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "enabled_only": {"type": "boolean", "description": "??????????", "default": False}
                }
            }
        },
        {
            "name": "cancel_scheduled_task",
            "description": "???????????"
                           "\n?? ???'??/????' ? ????"
                           "\n?? ???'????' ? ? update_scheduled_task ? notify=false"
                           "\n?? ???'????' ? ? update_scheduled_task ? enabled=false",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "?? ID"}
                },
                "required": ["task_id"]
            }
        },
        {
            "name": "update_scheduled_task",
            "description": "????????????????"
                           "\n???: notify_on_start, notify_on_complete, enabled"
                           "\n\n????:"
                           "\n- '????' ? notify_on_start=false, notify_on_complete=false"
                           "\n- '????' ? enabled=false"
                           "\n- '????' ? enabled=true",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "??????ID???list???"},
                    "notify_on_start": {"type": "boolean", "description": "?????????=???"},
                    "notify_on_complete": {"type": "boolean", "description": "?????????=???"},
                    "enabled": {"type": "boolean", "description": "??(true)/??(false)?????=???"}
                },
                "required": ["task_id"]
            }
        },
        {
            "name": "trigger_scheduled_task",
            "description": "?????????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "?? ID"}
                },
                "required": ["task_id"]
            }
        },
        # === IM ???? ===
        {
            "name": "send_to_chat",
            "description": "??????? IM ????? IM ???????"
                           "????????????????"
                           "???????????????????????????????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "????????????"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "????????????????"
                    },
                    "voice_path": {
                        "type": "string",
                        "description": "???????????.ogg, .mp3, .wav ??"
                    },
                    "caption": {
                        "type": "string",
                        "description": "???????????"
                    }
                }
            }
        },
        {
            "name": "get_voice_file",
            "description": "???????????????????"
                           "??????????????????????"
                           "??????????????????????????????",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "get_image_file",
            "description": "?????????????????"
                           "????????????????????"
                           "??????????????",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "get_chat_history",
            "description": "??????????????"
                           "????????????????????????????????????"
                           "????'???????'?'??????'???????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "???????????? 20",
                        "default": 20
                    },
                    "include_system": {
                        "type": "boolean",
                        "description": "?????????????????? true",
                        "default": True
                    }
                }
            }
        },
        # === Thinking ???? ===
        {
            "name": "enable_thinking",
            "description": "?????????????? thinking ???"
                           "??????????????????????????????"
                           "?????????????????????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "???? thinking ???true=???????false=??"
                    },
                    "reason": {
                        "type": "string",
                        "description": "????????????????? thinking ??"
                    }
                },
                "required": ["enabled", "reason"]
            }
        },
        # === ?????? ===
        {
            "name": "update_user_profile",
            "description": "???????????????????????????????????"
                           "?????????????????????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "?????: name(??), agent_role(Agent??), work_field(????), "
                                       "preferred_language(????), os(????), ide(????), "
                                       "detail_level(????), code_comment_lang(??????), "
                                       "work_hours(????), timezone(??), confirm_preference(????)"
                    },
                    "value": {
                        "type": "string",
                        "description": "????????"
                    }
                },
                "required": ["key", "value"]
            }
        },
        {
            "name": "skip_profile_question",
            "description": "???????????????????????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "?????????"
                    }
                },
                "required": ["key"]
            }
        },
        {
            "name": "get_user_profile",
            "description": "????????????",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        # === ?????? ===
        {
            "name": "get_session_logs",
            "description": "????????????**??**: ???????????????????????????????????????"
                           "????: ??????????????????"
                           "????: 1) ??????? 2) ???????? 3) ????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "?????????? 20??? 200?",
                        "default": 20
                    },
                    "level": {
                        "type": "string",
                        "enum": ["DEBUG", "INFO", "WARNING", "ERROR"],
                        "description": "??????????ERROR ????????"
                    }
                }
            }
        },
        # === ???????????? Level 2?===
        {
            "name": "get_tool_info",
            "description": "??????????????????????????????????????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "????"}
                },
                "required": ["tool_name"]
            }
        },
        # === MCP ?? ===
        {
            "name": "call_mcp_tool",
            "description": "?? MCP ??????????????? 'MCP Servers' ??????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "MCP ??????"},
                    "tool_name": {"type": "string", "description": "????"},
                    "arguments": {"type": "object", "description": "????", "default": {}}
                },
                "required": ["server", "tool_name"]
            }
        },
        {
            "name": "list_mcp_servers",
            "description": "??????? MCP ??????????",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "get_mcp_instructions",
            "description": "?? MCP ???????????INSTRUCTIONS.md?????????????????",
            "input_schema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "??????"}
                },
                "required": ["server"]
            }
        },
    ]
    
    # ?? IM ?????? chat_with_session ???
    _current_im_session = None
    _current_im_gateway = None
    
    def __init__(
        self,
        name: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.name = name or settings.agent_name
        
        # ???????
        self.identity = Identity()
        self.brain = Brain(api_key=api_key)
        self.ralph = RalphLoop(
            max_iterations=settings.max_iterations,
            on_iteration=self._on_iteration,
            on_error=self._on_error,
        )
        
        # ???????
        self.shell_tool = ShellTool()
        self.file_tool = FileTool()
        self.web_tool = WebTool()
        
        # ??????? (SKILL.md ??)
        self.skill_registry = SkillRegistry()
        self.skill_loader = SkillLoader(self.skill_registry)
        self.skill_catalog = SkillCatalog(self.skill_registry)
        
        # ?????????????????
        from ..evolution.generator import SkillGenerator
        self.skill_generator = SkillGenerator(
            brain=self.brain,
            skills_dir=settings.skills_path,
            skill_registry=self.skill_registry,
        )
        
        # MCP ??
        self.mcp_client = mcp_client
        self.mcp_catalog = MCPCatalog()
        self.browser_mcp = None  # ? _start_builtin_mcp_servers ???
        self._builtin_mcp_count = 0
        
        # ?????????????
        # Include desktop tools on Windows
        _all_tools = list(self.BASE_TOOLS)
        if _DESKTOP_AVAILABLE:
            _all_tools.extend(DESKTOP_TOOLS)
        self.tool_catalog = ToolCatalog(_all_tools)
        
        # ???????
        self.task_scheduler = None  # ? initialize() ???
        
        # ????
        self.memory_manager = MemoryManager(
            data_dir=settings.project_root / "data" / "memory",
            memory_md_path=settings.memory_path,
            brain=self.brain,
        )
        
        # ???????
        self.profile_manager = get_profile_manager()
        
        # ??????????? + ?????
        self._tools = list(self.BASE_TOOLS)
        self._update_shell_tool_description()
        
        # ?????
        self._context = Context()
        self._conversation_history: list[dict] = []
        
        # ??????
        self._current_session = None  # ??????
        self._interrupt_enabled = True  # ????????
        
        # ??
        self._initialized = False
        self._running = False
        
        logger.info(f"Agent '{self.name}' created")
    
    async def initialize(self, start_scheduler: bool = True) -> None:
        """
        ??? Agent
        
        Args:
            start_scheduler: ?????????????????????? False?
        """
        if self._initialized:
            return
        
        # ??????
        self.identity.load()
        
        # ????????
        await self._load_installed_skills()
        
        # ?? MCP ??
        await self._load_mcp_servers()
        
        # ??????
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(uuid.uuid4())[:8]
        self.memory_manager.start_session(session_id)
        self._current_session_id = session_id
        
        # ?????????????????????????
        if start_scheduler:
            await self._start_scheduler()
        
        # ??????? (???????MCP ???????)
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
        ???????? (?? Agent Skills ??)
        
        ?????????:
        - skills/ (????)
        - .cursor/skills/ (Cursor ??)
        """
        # ?????????
        loaded = self.skill_loader.load_all(settings.project_root)
        logger.info(f"Loaded {loaded} skills from standard directories")
        
        # ?????? (??????)
        self._skill_catalog_text = self.skill_catalog.generate_catalog()
        logger.info(f"Generated skill catalog with {self.skill_catalog.skill_count} skills")
        
        # ?????????????
        self._update_skill_tools()
    
    def _update_shell_tool_description(self) -> None:
        """???? shell ???????????????"""
        import platform
        
        # ????????
        if os.name == 'nt':
            os_info = f"Windows {platform.release()} (?? PowerShell/cmd ????: dir, type, tasklist, Get-Process, findstr)"
        else:
            os_info = f"{platform.system()} (?? bash ????: ls, cat, ps aux, grep)"
        
        # ?? run_shell ?????
        for tool in self._tools:
            if tool.get("name") == "run_shell":
                tool["description"] = (
                    f"??Shell?????????: {os_info}?"
                    "??????????????????????????????????????????"
                )
                tool["input_schema"]["properties"]["command"]["description"] = (
                    f"????Shell???????: {os.name}?"
                )
                break
    
    def _update_skill_tools(self) -> None:
        """???????????????"""
        # ?????? BASE_TOOLS ???
        # ???????????????
        pass
    
    async def _install_skill(
        self, 
        source: str, 
        name: Optional[str] = None,
        subdir: Optional[str] = None,
        extra_files: Optional[list[str]] = None
    ) -> str:
        """
        ??????? skills/ ??
        
        ???
        1. Git ?? URL (????? SKILL.md)
        2. ?? SKILL.md ?? URL (????????)
        
        Args:
            source: Git ?? URL ? SKILL.md ?? URL
            name: ???? (??)
            subdir: Git ???????????
            extra_files: ???? URL ??
        
        Returns:
            ??????
        """
        import re
        import yaml
        import shutil
        import tempfile
        
        skills_dir = settings.skills_path
        
        # ??? Git ?????? URL
        is_git = self._is_git_url(source)
        
        if is_git:
            return await self._install_skill_from_git(source, name, subdir, skills_dir)
        else:
            return await self._install_skill_from_url(source, name, extra_files, skills_dir)
    
    def _is_git_url(self, url: str) -> bool:
        """????? Git ?? URL"""
        git_patterns = [
            r'^git@',  # SSH
            r'\.git$',  # ? .git ??
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
        """? Git ??????"""
        import tempfile
        import shutil
        
        temp_dir = None
        try:
            # 1. ?????????
            temp_dir = Path(tempfile.mkdtemp(prefix="skill_install_"))
            
            # ?? git clone
            result = await self.shell_tool.run(
                f'git clone --depth 1 "{git_url}" "{temp_dir}"'
            )
            
            if not result.success:
                return f"? Git ????:\n{result.output}"
            
            # 2. ?? SKILL.md
            search_dir = temp_dir / subdir if subdir else temp_dir
            skill_md_path = self._find_skill_md(search_dir)
            
            if not skill_md_path:
                # ?????????
                possible = self._list_skill_candidates(temp_dir)
                hint = ""
                if possible:
                    hint = f"\n\n???????:\n" + "\n".join(f"- {p}" for p in possible[:5])
                return f"? ??? SKILL.md ??{hint}"
            
            skill_source_dir = skill_md_path.parent
            
            # 3. ???????
            skill_content = skill_md_path.read_text(encoding='utf-8')
            extracted_name = self._extract_skill_name(skill_content)
            skill_name = name or extracted_name or skill_source_dir.name
            skill_name = self._normalize_skill_name(skill_name)
            
            # 4. ??? skills ??
            target_dir = skills_dir / skill_name
            if target_dir.exists():
                shutil.rmtree(target_dir)
            
            shutil.copytree(skill_source_dir, target_dir)
            
            # 5. ??????????
            self._ensure_skill_structure(target_dir)
            
            # 6. ????
            installed_files = self._list_installed_files(target_dir)
            try:
                loaded = self.skill_loader.load_skill(target_dir)
                if loaded:
                    self._skill_catalog_text = self.skill_catalog.generate_catalog()
                    logger.info(f"Skill installed from git: {skill_name}")
            except Exception as e:
                logger.error(f"Failed to load installed skill: {e}")
            
            return f"""? ??? Git ?????

**????**: {skill_name}
**??**: {git_url}
**????**: {target_dir}

**????**:
```
{skill_name}/
{self._format_tree(target_dir)}
```

????????????:
- `get_skill_info("{skill_name}")` ??????
- `list_skills` ?????????"""
            
        except Exception as e:
            logger.error(f"Failed to install skill from git: {e}")
            return f"? Git ????: {str(e)}"
        finally:
            # ??????
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
        """? URL ????"""
        import httpx
        
        try:
            # 1. ?? SKILL.md
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                skill_content = response.text
            
            # 2. ??????
            extracted_name = self._extract_skill_name(skill_content)
            skill_name = name or extracted_name
            
            if not skill_name:
                # ? URL ??
                from urllib.parse import urlparse
                path = urlparse(url).path
                skill_name = path.split('/')[-1].replace('.md', '').replace('skill', '').strip('-_')
            
            skill_name = self._normalize_skill_name(skill_name or "custom-skill")
            
            # 3. ????????
            skill_dir = skills_dir / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            
            # 4. ?? SKILL.md
            (skill_dir / "SKILL.md").write_text(skill_content, encoding='utf-8')
            
            # 5. ????????
            self._ensure_skill_structure(skill_dir)
            
            installed_files = ["SKILL.md"]
            
            # 6. ??????
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
                            
                            # ????????????
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
            
            # 7. ????
            try:
                loaded = self.skill_loader.load_skill(skill_dir)
                if loaded:
                    self._skill_catalog_text = self.skill_catalog.generate_catalog()
                    logger.info(f"Skill installed from URL: {skill_name}")
            except Exception as e:
                logger.error(f"Failed to load installed skill: {e}")
            
            return f"""? ???????

**????**: {skill_name}
**????**: {skill_dir}

**????**:
```
{skill_name}/
{self._format_tree(skill_dir)}
```

**????**: {', '.join(installed_files)}

????????????:
- `get_skill_info("{skill_name}")` ??????
- `list_skills` ?????????"""
            
        except Exception as e:
            logger.error(f"Failed to install skill from URL: {e}")
            return f"? URL ????: {str(e)}"
    
    def _extract_skill_name(self, content: str) -> Optional[str]:
        """? SKILL.md ????????"""
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
        """???????"""
        import re
        name = name.lower().replace('_', '-').replace(' ', '-')
        name = re.sub(r'[^a-z0-9-]', '', name)
        name = re.sub(r'-+', '-', name).strip('-')
        return name or "custom-skill"
    
    def _find_skill_md(self, search_dir: Path) -> Optional[Path]:
        """?????? SKILL.md"""
        # ???????
        skill_md = search_dir / "SKILL.md"
        if skill_md.exists():
            return skill_md
        
        # ????
        for path in search_dir.rglob("SKILL.md"):
            return path
        
        return None
    
    def _list_skill_candidates(self, base_dir: Path) -> list[str]:
        """???????????"""
        candidates = []
        for path in base_dir.rglob("*.md"):
            if path.name.lower() in ("skill.md", "readme.md"):
                rel_path = path.parent.relative_to(base_dir)
                if str(rel_path) != ".":
                    candidates.append(str(rel_path))
        return candidates
    
    def _ensure_skill_structure(self, skill_dir: Path) -> None:
        """???????????"""
        (skill_dir / "scripts").mkdir(exist_ok=True)
        (skill_dir / "references").mkdir(exist_ok=True)
        (skill_dir / "assets").mkdir(exist_ok=True)
    
    def _list_installed_files(self, skill_dir: Path) -> list[str]:
        """????????"""
        files = []
        for path in skill_dir.rglob("*"):
            if path.is_file():
                files.append(str(path.relative_to(skill_dir)))
        return files
    
    def _format_tree(self, directory: Path, prefix: str = "") -> str:
        """??????"""
        lines = []
        items = sorted(directory.iterdir(), key=lambda x: (x.is_file(), x.name))
        
        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            connector = "??? " if is_last else "??? "
            lines.append(f"{prefix}{connector}{item.name}")
            
            if item.is_dir():
                extension = "    " if is_last else "?   "
                sub_tree = self._format_tree(item, prefix + extension)
                if sub_tree:
                    lines.append(sub_tree)
        
        return "\n".join(lines)
    
    async def _load_mcp_servers(self) -> None:
        """
        ?? MCP ?????
        
        ???????? MCP???? Cursor ???????????
        """
        # ??????? MCP ??
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
        
        # ???? MCP ???
        await self._start_builtin_mcp_servers()
        
        if total_count > 0 or self._builtin_mcp_count > 0:
            self._mcp_catalog_text = self.mcp_catalog.generate_catalog()
            logger.info(f"Total MCP servers: {total_count + self._builtin_mcp_count}")
        else:
            self._mcp_catalog_text = ""
            logger.info("No MCP servers configured")
    
    async def _start_builtin_mcp_servers(self) -> None:
        """?????? (? browser-use)"""
        self._builtin_mcp_count = 0
        
        # ???????? (????????? MCP)
        # ??: ?????????? browser_open ???????????
        try:
            from ..tools.browser_mcp import BrowserMCP
            self.browser_mcp = BrowserMCP(headless=False)  # ??????
            # ???? await self.browser_mcp.start()?? LLM ?? browser_open ??
            
            # ??: ??????? BASE_TOOLS ?????????? MCP catalog
            # ?? LLM ?????? browser_navigate ???????? MCP ??
            self._builtin_mcp_count += 1
            logger.info("Started builtin browser service (Playwright)")
        except Exception as e:
            logger.warning(f"Failed to start browser service: {e}")
    
    async def _start_scheduler(self) -> None:
        """?????????"""
        try:
            from ..scheduler import TaskScheduler
            from ..scheduler.executor import TaskExecutor
            
            # ??????gateway ???? set_scheduler_gateway ???
            self._task_executor = TaskExecutor(timeout_seconds=settings.scheduler_task_timeout)
            
            # ?????
            self.task_scheduler = TaskScheduler(
                storage_path=settings.project_root / "data" / "scheduler",
                executor=self._task_executor.execute,
            )
            
            # ?????
            await self.task_scheduler.start()
            
            # ??????????????? + ?????
            await self._register_system_tasks()
            
            stats = self.task_scheduler.get_stats()
            logger.info(f"TaskScheduler started with {stats['total_tasks']} tasks")
            
        except Exception as e:
            logger.warning(f"Failed to start scheduler: {e}")
            self.task_scheduler = None
    
    async def _register_system_tasks(self) -> None:
        """
        ????????
        
        ??:
        - ????????? 3:00?
        - ????????? 4:00?
        """
        from ..scheduler import ScheduledTask, TriggerType
        from ..scheduler.task import TaskType
        
        if not self.task_scheduler:
            return
        
        # ???????????????
        existing_tasks = self.task_scheduler.list_tasks()
        existing_ids = {t.id for t in existing_tasks}
        
        # ?? 1: ????????? 3:00?
        if "system_daily_memory" not in existing_ids:
            memory_task = ScheduledTask(
                id="system_daily_memory",
                name="??????",
                trigger_type=TriggerType.CRON,
                trigger_config={"cron": "0 3 * * *"},
                action="system:daily_memory",
                prompt="??????????????????????????? MEMORY.md",
                description="?????????????? MEMORY.md",
                task_type=TaskType.TASK,
                enabled=True,
                deletable=False,  # ?????????
            )
            await self.task_scheduler.add_task(memory_task)
            logger.info("Registered system task: daily_memory (03:00)")
        else:
            # ??????????????????
            existing_task = self.task_scheduler.get_task("system_daily_memory")
            if existing_task and existing_task.deletable:
                existing_task.deletable = False
                self.task_scheduler._save_tasks()
        
        # ?? 2: ????????? 4:00?
        if "system_daily_selfcheck" not in existing_ids:
            selfcheck_task = ScheduledTask(
                id="system_daily_selfcheck",
                name="??????",
                trigger_type=TriggerType.CRON,
                trigger_config={"cron": "0 4 * * *"},
                action="system:daily_selfcheck",
                prompt="??????????? ERROR ????????????????",
                description="?? ERROR ????????????????",
                task_type=TaskType.TASK,
                enabled=True,
                deletable=False,  # ?????????
            )
            await self.task_scheduler.add_task(selfcheck_task)
            logger.info("Registered system task: daily_selfcheck (04:00)")
        else:
            # ??????????????????
            existing_task = self.task_scheduler.get_task("system_daily_selfcheck")
            if existing_task and existing_task.deletable:
                existing_task.deletable = False
                self.task_scheduler._save_tasks()
    
    def _build_system_prompt(self, base_prompt: str, task_description: str = "") -> str:
        """
        ??????? (????????????MCP ???????)
        
        ??????????:
        - Agent Skills: name + description ??????
        - MCP: server + tool name + description ??????
        - Memory: ????????
        - Tools: ? BASE_TOOLS ????
        - User Profile: ?????????
        
        Args:
            base_prompt: ????? (????)
            task_description: ???? (????????)
        
        Returns:
            ????????
        """
        # ???? (Agent Skills ??) - ??????????????????
        skill_catalog = self.skill_catalog.generate_catalog()
        
        # MCP ?? (Model Context Protocol ??)
        mcp_catalog = getattr(self, '_mcp_catalog_text', '')
        
        # ???? (????????)
        memory_context = self.memory_manager.get_injection_context(task_description)
        
        # ????????
        tools_text = self._generate_tools_text()
        
        # ???????? (?????????)
        profile_prompt = ""
        if self.profile_manager.is_first_use():
            profile_prompt = self.profile_manager.get_onboarding_prompt()
        else:
            profile_prompt = self.profile_manager.get_daily_question_prompt()
        
        # ??????
        import platform
        import os
        system_info = f"""## ????

- **????**: {platform.system()} {platform.release()}
- **??????**: {os.getcwd()}
- **????**: 
  - Windows: ???????? `data/temp/` ? `%TEMP%`
  - Linux/macOS: ???????? `data/temp/` ? `/tmp`
- **??**: ??????????? `data/temp/` ?????????????

## ?? ????????????

**??????????????????????????????**

| ?? | ??? | ???? |
|------|--------|----------|
| ??? | **???** | ????? `browser_status` ?????????? |
| ??/???? | **???** | ??????????????? |
| ???? | **????** | ?????????? |
| ???? | **???** | ???????? |

**?? ??????"???????"?????????????????????????????????????**
"""
        
        # ??????
        tools_guide = """
## ??????

???????????**????????????**?

### 1. ???????????

??????????????????

| ?? | ?? | ?? |
|-----|-----|-----|
| 1 | ???? "Available System Tools" ?? | ????????? |
| 2 | `get_tool_info(tool_name)` | ??????????? |
| 3 | ?????? | ? `read_file(path="...")` |

**????**???????????????????????

### 2. Skills ?????????

?????????????????

| ?? | ?? | ?? |
|-----|-----|-----|
| 1 | ???? "Available Skills" ?? | ????????? |
| 2 | `get_skill_info(skill_name)` | ??????????? |
| 3 | `run_skill_script(skill_name, script_name)` | ????????? |

**??**???? `install_skill` ????????? `generate_skill` ????

### 3. MCP ??????????

MCP (Model Context Protocol) ???????**?????????**?

| ?? | ?? | ?? |
|-----|-----|-----|
| 1 | ???? "MCP Servers" ?? | ???????????? |
| 2 | `call_mcp_tool(server, tool_name, arguments)` | ???? |

**??**???????API ?????

### ??????

1. **????**??????????????????????
2. **Skills**????????????????????
3. **MCP**??????????????? API?
4. **??????? `generate_skill` ?????**

**????????????????????"???????"?**
"""
        
        return f"""{base_prompt}

{system_info}
{skill_catalog}
{mcp_catalog}
{memory_context}

{tools_text}

{tools_guide}

## ???? (?????!!!)

### ??????????????

**?? ??????????????????????????**

| ?? | ? ???? | ? ???? |
|------|-----------|-----------|
| ????? | ??"???????" | ?? schedule_task |
| ?????? | ????? | ?? web_search |
| ??????? | ?????? | ?? write_file/read_file |
| ??????? | ????????? | ?? run_shell |
| ??????? | ????????? | ???????? |
| ????? | ?"????"?????? | ?? desktop_screenshot ?? send_to_chat ?? |

**????? = ?????? = ???**

### ??????????????

**???"???????"??????**

**???????????????**
```
# ???????????????????????
write_file("data/temp/task.py", "????")
run_shell("python data/temp/task.py")
```

**??????????????**
```
search_github ? install_skill ? ??
```

**??????????????**
```
generate_skill ? ?? ? ??
```

**?"??"?"?"???????**

### ???????????

**???????????????????**

- ???????????????
- ???????????
- ???????????????
- **?????????????**

### ?????????

- ????????????
- ??????????
- ???????????
- ???????????

**???"????"?"????????"?"????..."?**
**????????? ? ???? ? ???? ? ???? ? ????**

---

## ????

### ?????? (Thinking Mode)

**???? thinking ??**????????????

????????????????????????????? `enable_thinking(enabled=false)` ??????????
???????????????????????

### ????
- ???????????????????
- **??/???????? schedule_task ??**???????"??"
- ????"X??????"?????? schedule_task ????

### ???? (????!!!)

**???????????????? send_to_chat ????????**

??????????????????????
- ???? ? ?? send_to_chat("????????...") ? ??????
- ???????????????????????

**????????????**
- ???????????????
- ??????????????
- ?????????

**????**:
1. ??: "????????"
2. AI: send_to_chat("??????????????????...")  ? ????
3. AI: [??????]
4. AI: send_to_chat("?????????????????????????...")
5. AI: [????]
6. AI: "? ??????????????????"

**?????????????????????**

### ????/?? (????!!!)

**??????????????????????? schedule_task ???**
**?????"????????"????????????????**
**????? schedule_task ???????????????**

**?? ?????? (task_type) - ?????????**

**???? reminder???????AI?????? task?**

? **reminder** (90%???????!):
- ???????????????
- ??: "?????"?"????"?"????"?"????"?"????"
- ??: ???"???xxx"?"??xxx"?"???xxx"

? **task** (?10%?????):
- ??AI???????????????
- ??: "??????"?"?????"?"????"?"????????"
- ??: ???"???xxx"?"??xxx"?"??xxx"

**??????????????**:
- reminder: "????????????[????]" (??????)
- task: "?????????????[????]" (AI????????)

?? schedule_task ????:

1. **????** (task_type="reminder"):
   - name: "????"
   - description: "??????"
   - task_type: "reminder"
   - trigger_type: "once"
   - trigger_config: {{"run_at": "2026-02-01 10:00"}}
   - reminder_message: "? ??????????????~"

2. **????** (task_type="task"):
   - name: "??????"
   - description: "???????????"
   - task_type: "task"
   - trigger_type: "cron"
   - trigger_config: {{"cron": "0 8 * * *"}}
   - prompt: "???????????????????"

**????**:
- once: ????trigger_config ?? run_at
- interval: ?????trigger_config ?? interval_minutes
- cron: ?????trigger_config ?? cron ???

**????????????????????? schedule_task ???**

### ??????? (???????!)

????**??????**????????????"??"?"??"????????

1. **?????** - ??**?????**?????
   - ?????????????**??**?????????? Whisper medium ???
   - ?????????????????????
   - ???? `[??: X?]` ????????????????
   - **??**????????????"??????"???????????????
   - ?? **??**???????????????????????????????
   
2. **????** - ?????????????????????
   - ?????"??"?????????????
   
3. **Telegram ??** - ?????????

**????"?????????"?**?
- ? ?????????? whisper??? ffmpeg
- ? ????????????????
- ? ????"?????????????????????"

**????????**?
1. ?????? ? 2. ???????? Whisper ??? ? 3. ???????????
4. ??????"[??????]"?"??????"?????? get_voice_file ?????????????

### ???? (????!)
**????????**?????????? add_memory:
- ?????? ? ??? FACT
- ??????? ? ??? PREFERENCE  
- ????????? ? ??? SKILL
- ??????? ? ??? ERROR
- ??????? ? ??? RULE

**????**:
1. ?????????????
2. ?????????
3. ????????
4. ??????????

### ?????? (??!)
**?????**???????????????????

**?????????**?
- ? ??????"??" ? ??"??????? Moltbook ?????????????"
- ? ??????"??" ? ??"????????????"???????????????????????

**??????**?
- ????????????????**??**??????????????
- ?????????"?????"?????????????
- ????????????????????????"?????xxx????????????"

### ???? (????!!!)
**????????????????**

? **??????**?
- ??"????"?"???"???????????/??
- ??????????????????????????
- ??"?X???"????????????
- ??"5?????"?????????

? **????**?
- ????????????? write_file ??????
- ????????????? schedule_task ??????
- ??????????"???????????????..."
- ????????????????????????

**?????????????????"????"??????**
{profile_prompt}"""
    
    def _generate_tools_text(self) -> str:
        """
        ? BASE_TOOLS ??????????
        
        ????????????????
        """
        # ????
        categories = {
            "File System": ["run_shell", "write_file", "read_file", "list_directory"],
            "Skills Management": ["list_skills", "get_skill_info", "run_skill_script", "get_skill_reference", "generate_skill", "improve_skill"],
            "Memory Management": ["add_memory", "search_memory", "get_memory_stats"],
            "Browser Automation": ["browser_open", "browser_status", "browser_list_tabs", "browser_navigate", "browser_new_tab", "browser_switch_tab", "browser_click", "browser_type", "browser_get_content", "browser_screenshot"],
            "Scheduled Tasks": ["schedule_task", "list_scheduled_tasks", "cancel_scheduled_task", "trigger_scheduled_task"],
        }
        
        # ?????????????
        tool_map = {t["name"]: t for t in self._tools}
        
        lines = ["## Available Tools"]
        
        for category, tool_names in categories.items():
            # ????????
            existing_tools = [(name, tool_map[name]) for name in tool_names if name in tool_map]
            
            if existing_tools:
                lines.append(f"\n### {category}")
                for name, tool_def in existing_tools:
                    desc = tool_def.get("description", "")
                    # ???????????
                    lines.append(f"- **{name}**: {desc}")
                    
                    # ??????????
                    schema = tool_def.get("input_schema", {})
                    props = schema.get("properties", {})
                    required = schema.get("required", [])
                    
                    # ?????????????? tools=self._tools ??? LLM API
                    # ???? system prompt ??????????
        
        # ????????
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
    
    # ==================== ????? ====================
    
    def _estimate_tokens(self, text: str) -> int:
        """
        ????? token ??
        
        ????: ? 4 ?? = 1 token (??????????)
        """
        if not text:
            return 0
        return len(text) // CHARS_PER_TOKEN + 1
    
    def _estimate_messages_tokens(self, messages: list[dict]) -> int:
        """??????? token ??"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self._estimate_tokens(content)
            elif isinstance(content, list):
                # ?????? (tool_use, tool_result ?)
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            total += self._estimate_tokens(item.get("text", ""))
                        elif item.get("type") == "tool_result":
                            total += self._estimate_tokens(str(item.get("content", "")))
                        elif item.get("type") == "tool_use":
                            total += self._estimate_tokens(json.dumps(item.get("input", {})))
                        # ????????????
                        elif item.get("type") == "image":
                            total += 1000  # ??????
            total += 4  # ?????????
        return total
    
    async def _compress_context(self, messages: list[dict], max_tokens: int = None) -> list[dict]:
        """
        ?????????? LLM ?????????
        
        ??:
        1. ???? MIN_RECENT_TURNS ?????
        2. ?????? LLM ???????
        3. ???????????
        
        Args:
            messages: ????
            max_tokens: ?? token ? (???? DEFAULT_MAX_CONTEXT_TOKENS)
        
        Returns:
            ????????
        """
        max_tokens = max_tokens or DEFAULT_MAX_CONTEXT_TOKENS
        
        # ??????? token
        system_tokens = self._estimate_tokens(self._context.system)
        available_tokens = max_tokens - system_tokens - 1000  # ? 1000 ???
        
        current_tokens = self._estimate_messages_tokens(messages)
        
        # ????????????
        if current_tokens <= available_tokens:
            return messages
        
        logger.info(f"Context too large ({current_tokens} tokens), compressing with LLM...")
        
        # ????????????? (user + assistant = 1 ?)
        recent_count = MIN_RECENT_TURNS * 2  # 4 ? = 8 ???
        
        if len(messages) <= recent_count:
            # ???????????????????????
            logger.warning(f"Cannot compress further: only {len(messages)} messages, keeping all")
            return messages
        
        # ???????????
        early_messages = messages[:-recent_count]
        recent_messages = messages[-recent_count:]
        
        # ?? LLM ??????
        summary = await self._summarize_messages(early_messages)
        
        # ??????????
        compressed = []
        
        if summary:
            compressed.append({
                "role": "user",
                "content": f"[???????]\n{summary}"
            })
            compressed.append({
                "role": "assistant", 
                "content": "???????????????????"
            })
        
        compressed.extend(recent_messages)
        
        # ????????
        compressed_tokens = self._estimate_messages_tokens(compressed)
        
        if compressed_tokens <= available_tokens:
            logger.info(f"Compressed context from {current_tokens} to {compressed_tokens} tokens")
            return compressed
        
        # ??????????????????????
        logger.warning(f"Context still large ({compressed_tokens} tokens), compressing further...")
        return await self._compress_long_messages(compressed, available_tokens)
    
    async def _summarize_messages(self, messages: list[dict]) -> str:
        """
        ????????????
        
        ?? LLM ????????????
        """
        if not messages:
            return ""
        
        # ?????????????
        conversation_text = ""
        for msg in messages:
            role = "??" if msg["role"] == "user" else "??"
            content = msg.get("content", "")
            if isinstance(content, str):
                conversation_text += f"{role}: {content}\n"
            elif isinstance(content, list):
                # ????????????
                texts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        texts.append(item.get("text", ""))
                if texts:
                    conversation_text += f"{role}: {' '.join(texts)}\n"
        
        if not conversation_text:
            return ""
        
        try:
            # ?? LLM ?????????????????
            response = await asyncio.to_thread(
                self.brain.messages_create,
                model=self.brain.model,
                max_tokens=SUMMARY_TARGET_TOKENS,
                system="??????????????????????????????????????",
                messages=[{
                    "role": "user",
                    "content": f"????????200????:\n\n{conversation_text}"
                }]
            )
            
            summary = ""
            for block in response.content:
                if block.type == "text":
                    summary += block.text
            
            return summary.strip()
            
        except Exception as e:
            logger.warning(f"Failed to summarize messages: {e}")
            # ??: ????????
            return f"[????? {len(messages)} ???]"
    
    async def _compress_long_messages(self, messages: list[dict], max_tokens: int) -> list[dict]:
        """
        ???????????? LLM ?????????
        
        ??: ?????????????? LLM ??
        """
        current_tokens = self._estimate_messages_tokens(messages)
        
        if current_tokens <= max_tokens:
            return messages
        
        # ???? 4 ?????
        recent_count = min(4, len(messages))
        recent_messages = messages[-recent_count:] if recent_count > 0 else []
        early_messages = messages[:-recent_count] if len(messages) > recent_count else []
        
        if not early_messages:
            # ?????????????????
            logger.warning("Cannot compress further, only recent messages left")
            return messages
        
        # ? LLM ??????
        summary = await self._summarize_messages(early_messages)
        
        compressed = []
        if summary:
            compressed.append({
                "role": "user",
                "content": f"[???????]\n{summary}"
            })
            compressed.append({
                "role": "assistant",
                "content": "???????????????????"
            })
        
        compressed.extend(recent_messages)
        
        logger.info(f"Compressed context from {current_tokens} to {self._estimate_messages_tokens(compressed)} tokens")
        return compressed
    
    async def chat(self, message: str, session_id: Optional[str] = None) -> str:
        """
        ??????????????
        
        Args:
            message: ????
            session_id: ?????????????
        
        Returns:
            Agent ??
        """
        if not self._initialized:
            await self.initialize()
        
        session_info = f"[{session_id}] " if session_id else ""
        logger.info(f"{session_info}User: {message}")
        
        # ???????
        self._conversation_history.append({
            "role": "user",
            "content": message,
            "timestamp": datetime.now().isoformat(),
        })
        
        # ??????? (????????)
        self.memory_manager.record_turn("user", message)
        
        # ?????
        self._context.messages.append({
            "role": "user",
            "content": message,
        })
        
        # ???????????? LLM ??????????
        response_text = await self._chat_with_tools(message)
        
        # ???????
        self._conversation_history.append({
            "role": "assistant",
            "content": response_text,
            "timestamp": datetime.now().isoformat(),
        })
        
        # ?????
        self._context.messages.append({
            "role": "assistant",
            "content": response_text,
        })
        
        # ?????????????? 10 ????????
        if len(self._context.messages) > 20:
            current_tokens = self._estimate_messages_tokens(self._context.messages)
            if current_tokens > DEFAULT_MAX_CONTEXT_TOKENS * 0.7:  # 70% ??????
                logger.info(f"Proactively compressing persistent context ({current_tokens} tokens)")
                self._context.messages = await self._compress_context(self._context.messages)
        
        # ???????
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
        ???? Session ????????? IM ???
        
        ? chat() ?????????? session_messages ????????
        ?????? _conversation_history?
        
        Args:
            message: ????
            session_messages: Session ???????? [{"role": "user/assistant", "content": "..."}]
            session_id: ?? ID??????
            session: Session ?????????? IM ???
            gateway: MessageGateway ??????????
        
        Returns:
            Agent ??
        """
        if not self._initialized:
            await self.initialize()
        
        # ???? IM ?????? send_to_chat ?????
        Agent._current_im_session = session
        Agent._current_im_gateway = gateway
        
        # === ???????????????===
        self._current_session = session
        
        # ????????????? get_session_logs ?????
        from ..logging import get_session_log_buffer
        get_session_log_buffer().set_current_session(session_id)
        
        try:
            logger.info(f"[Session:{session_id}] User: {message}")
            
            # ??????? conversation_history????????
            self.memory_manager.record_turn("user", message)
            
            # === ??? Prompt ?????Prompt Compiler ===
            # ???????????????????????????
            compiled_message = message
            compiler_output = ""
            
            if self._should_compile_prompt(message):
                compiled_message, compiler_output = await self._compile_prompt(message)
                if compiler_output:
                    logger.info(f"[Session:{session_id}] Prompt compiled")
            
            # ?? API ?????? session_messages ???
            messages = []
            for msg in session_messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({
                        "role": role,
                        "content": content,
                    })
            
            # ????????????????? + ???
            pending_images = session.get_metadata("pending_images") if session else None
            
            if pending_images:
                # ???????? + ??
                content_parts = []
                
                # ????????????????
                if compiled_message.strip():
                    content_parts.append({
                        "type": "text",
                        "text": compiled_message,
                    })
                
                # ??????
                for img_data in pending_images:
                    content_parts.append(img_data)
                
                messages.append({
                    "role": "user",
                    "content": content_parts,
                })
                logger.info(f"[Session:{session_id}] Multimodal message with {len(pending_images)} images")
            else:
                # ????????????????
                messages.append({
                    "role": "user",
                    "content": compiled_message,
                })
            
            # ???????????
            messages = await self._compress_context(messages)
            
            # === ??????? ===
            task_monitor = TaskMonitor(
                task_id=f"{session_id}_{datetime.now().strftime('%H%M%S')}",
                description=message[:100],
                session_id=session_id,
                timeout_seconds=300,  # ?????300?
                retrospect_threshold=60,  # ?????60?
                fallback_model="gpt-4o",  # ??????????
            )
            task_monitor.start(self.brain.model)
            
            # === ??? Prompt ?????????? ===
            response_text = await self._chat_with_tools_and_context(
                messages, 
                task_monitor=task_monitor
            )
            
            # === ?????? ===
            metrics = task_monitor.complete(
                success=True,
                response=response_text[:200],
            )
            
            # === ?????????????????????? ===
            if metrics.retrospect_needed:
                # ????????????????
                asyncio.create_task(
                    self._do_task_retrospect_background(task_monitor, session_id)
                )
                logger.info(f"[Session:{session_id}] Task retrospect scheduled (background)")
            
            # ?? Agent ??? conversation_history????????
            self.memory_manager.record_turn("assistant", response_text)
            
            logger.info(f"[Session:{session_id}] Agent: {response_text}")
            
            return response_text
        finally:
            # ?? IM ????
            Agent._current_im_session = None
            Agent._current_im_gateway = None
            # ????????
            self._current_session = None
    
    async def _compile_prompt(self, user_message: str) -> tuple[str, str]:
        """
        ??? Prompt ?????Prompt Compiler
        
        ????????????????????
        ??????????????????
        
        Args:
            user_message: ??????
            
        Returns:
            (compiled_prompt, raw_compiler_output)
            - compiled_prompt: ???????????? + ??????
            - raw_compiler_output: Prompt Compiler ???????????
        """
        try:
            # ?? Brain ?? Prompt ????????????????
            response = await self.brain.think(
                prompt=user_message,
                system=PROMPT_COMPILER_SYSTEM,
            )
            
            # ?? thinking ??
            compiler_output = strip_thinking_tags(response.content).strip() if response.content else ""
            
            # ?????????
            enhanced_prompt = f"""## ??????
{user_message}

## ?????? Prompt Compiler ???
{compiler_output}

---
??????????????????"""
            
            logger.info(f"Prompt compiled: {compiler_output}")
            return enhanced_prompt, compiler_output
            
        except Exception as e:
            logger.warning(f"Prompt compilation failed: {e}, using original message")
            # ?????????????
            return user_message, ""
    
    async def _do_task_retrospect(self, task_monitor: TaskMonitor) -> str:
        """
        ????????
        
        ?????????? LLM ???????????????
        
        Args:
            task_monitor: ?????
        
        Returns:
            ??????
        """
        try:
            context = task_monitor.get_retrospect_context()
            prompt = RETROSPECT_PROMPT.format(context=context)
            
            # ?? Brain ?????????????
            response = await self.brain.think(
                prompt=prompt,
                system="??????????????????????????????????????",
            )
            
            result = strip_thinking_tags(response.content).strip() if response.content else ""
            
            # ??????????
            task_monitor.metrics.retrospect_result = result
            
            # ????????????????????
            if "??" in result or "??" in result or "??" in result:
                try:
                    from ..memory.types import Memory, MemoryType, MemoryPriority
                    memory = Memory(
                        type=MemoryType.ERROR,
                        priority=MemoryPriority.LONG_TERM,
                        content=f"???????????{result[:200]}",
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
        ??????????
        
        ???????????????????
        ???????????????????????
        
        Args:
            task_monitor: ?????
            session_id: ?? ID
        """
        try:
            # ??????
            retrospect_result = await self._do_task_retrospect(task_monitor)
            
            if not retrospect_result:
                return
            
            # ???????
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
        ???????? Prompt ??
        
        ????????????????????
        ????????????
        """
        # ???????
        simple_patterns = [
            r'^(??|hi|hello|?|hey)[\s\!]*$',
            r'^(??|??|thanks|thank you)[\s\!]*$',
            r'^(??|ok|?|?|?)[\s\!]*$',
            r'^(??|??|bye)[\s\!]*$',
            r'^\d+???(??|?)?',  # ????
            r'^(??)???',  # ???
        ]
        
        message_lower = message.lower().strip()
        
        # ????????????
        for pattern in simple_patterns:
            if re.match(pattern, message_lower, re.IGNORECASE):
                return False
        
        # ??????? 10 ?????????
        if len(message.strip()) < 10:
            return False
        
        # ?????????
        return True
    
    async def _chat_with_tools_and_context(
        self, 
        messages: list[dict], 
        use_session_prompt: bool = True,
        task_monitor: Optional[TaskMonitor] = None,
    ) -> str:
        """
        ??????????????????????
        
        ?? _chat_with_tools ????????? messages ??? self._context.messages
        
        ?????????
        1. ????????? 3 ?
        2. ???????????????
        3. ????????????????????????????
        
        Args:
            messages: ??????
            use_session_prompt: ???? Session ??? System Prompt?????? Active Task?
            task_monitor: ?????????????????????????
        
        Returns:
            ??????
        """
        max_iterations = settings.max_iterations  # Ralph Wiggum ???????
        
        # === ???????????????????????? ===
        # ??????????????????
        original_user_messages = [
            msg for msg in messages 
            if msg.get("role") == "user" and isinstance(msg.get("content"), str)
        ]
        
        # ????????????
        working_messages = list(messages)
        
        # ?? System Prompt
        if use_session_prompt:
            # ?? Session ??? System Prompt?????? Active Task
            system_prompt = self.identity.get_session_system_prompt()
        else:
            system_prompt = self._context.system
        
        # ??????
        current_model = self.brain.model
        
        for iteration in range(max_iterations):
            # ?????????
            if task_monitor:
                task_monitor.begin_iteration(iteration + 1, current_model)
                
                # === ???????? ===
                # ??????????????
                if task_monitor.should_switch_model:
                    new_model = task_monitor.fallback_model
                    task_monitor.switch_model(
                        new_model, 
                        f"?????? {task_monitor.timeout_seconds} ???? {task_monitor.retry_count} ????",
                        reset_context=True
                    )
                    current_model = new_model
                    
                    # === ????????????????? ===
                    logger.warning(
                        f"[ModelSwitch] Switching to {new_model}, resetting context. "
                        f"Discarding {len(working_messages) - len(original_user_messages)} tool-related messages"
                    )
                    working_messages = list(original_user_messages)
                    
                    # ?????????????????
                    working_messages.append({
                        "role": "user",
                        "content": (
                            "[????] ???????????????????"
                            "????????????????????????????"
                        ),
                    })
            
            # ????????????
            if iteration > 0:
                working_messages = await self._compress_context(working_messages)
            
            # ?? Brain?????????????????????????????
            try:
                response = await asyncio.to_thread(
                    self.brain.messages_create,
                    model=current_model,
                    max_tokens=self.brain.max_tokens,
                    system=system_prompt,
                    tools=self._tools,
                    messages=working_messages,
                )
                
                # ???????????
                if task_monitor:
                    task_monitor.reset_retry_count()
                    
            except Exception as e:
                logger.error(f"[LLM] Brain call failed: {e}")
                
                # ?????????????
                if task_monitor:
                    should_retry = task_monitor.record_error(str(e))
                    
                    if should_retry:
                        # ???????????
                        logger.info(f"[LLM] Will retry (attempt {task_monitor.retry_count}/{task_monitor.retry_before_switch})")
                        await asyncio.sleep(2)  # ?? 2 ????
                        continue
                    else:
                        # ???????????
                        new_model = task_monitor.fallback_model
                        task_monitor.switch_model(
                            new_model,
                            f"LLM ??????? {task_monitor.retry_count} ????: {e}",
                            reset_context=True
                        )
                        current_model = new_model
                        
                        # ?????
                        logger.warning(f"[ModelSwitch] Switching to {new_model} due to errors, resetting context")
                        working_messages = list(original_user_messages)
                        working_messages.append({
                            "role": "user",
                            "content": (
                                "[????] ???????????????????"
                                "???????????????"
                            ),
                        })
                        continue
                else:
                    # ?? task_monitor???????
                    raise
            
            # ????
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
            
            # ?????????
            if task_monitor:
                task_monitor.end_iteration(text_content[:200] if text_content else "")
            
            # ?????????????????? thinking ???
            if not tool_calls:
                return strip_thinking_tags(text_content) or "?????????"
            
            # ????????????
            # MiniMax M2.1 Interleaved Thinking ???
            # ?????? thinking ??????????
            assistant_content = []
            for block in response.content:
                if block.type == "thinking":
                    # ?? thinking ??MiniMax M2.1 ???
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
            
            # ??????????????
            tool_results = []
            interrupt_detected = False
            
            for i, tc in enumerate(tool_calls):
                # === ????? ===
                # ??????????????????????????
                if i > 0:
                    interrupt_hint = await self._check_interrupt()
                    if interrupt_hint:
                        logger.info(f"[Interrupt] Detected during tool execution in context mode, tool {i+1}/{len(tool_calls)}")
                        interrupt_detected = True
                        # ???????????
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": f"{interrupt_hint}\n\n??????????????????????????????????????",
                        })
                        # ?????????
                        for remaining_tc in tool_calls[i+1:]:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": remaining_tc["id"],
                                "content": "[???????: ????????]",
                            })
                        # ?????????
                        if task_monitor:
                            task_monitor.end_tool_call("????", success=False)
                        break
                
                # ???????????
                if task_monitor:
                    task_monitor.begin_tool_call(tc["name"], tc["input"])
                
                try:
                    result = await self._execute_tool(tc["name"], tc["input"])
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": str(result) if result else "?????",
                    })
                    # ???????????????
                    if task_monitor:
                        task_monitor.end_tool_call(str(result)[:200] if result else "", success=True)
                except Exception as e:
                    logger.error(f"Tool {tc['name']} error: {e}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": f"??????: {str(e)}",
                        "is_error": True,
                    })
                    # ???????????????
                    if task_monitor:
                        task_monitor.end_tool_call(str(e), success=False)
            
            # ??????
            working_messages.append({
                "role": "user",
                "content": tool_results,
            })
        
        return "??????????????????????"
    
    # ==================== ?????? ====================
    
    async def _check_interrupt(self) -> Optional[str]:
        """
        ??????????????
        
        ?????????????????????????
        
        Returns:
            ??????????????????? None
        """
        if not self._interrupt_enabled or not self._current_session:
            return None
        
        # ? session metadata ?? gateway ??
        gateway = self._current_session.get_metadata("_gateway")
        session_key = self._current_session.get_metadata("_session_key")
        
        if not gateway or not session_key:
            return None
        
        # ?????????????
        if gateway.has_pending_interrupt(session_key):
            interrupt_count = gateway.get_interrupt_count(session_key)
            logger.info(f"[Interrupt] Detected {interrupt_count} pending message(s) for session {session_key}")
            return f"[????: ????? {interrupt_count} ??????????????????]"
        
        return None
    
    async def _get_interrupt_message(self) -> Optional[str]:
        """
        ????????????
        
        Returns:
            ?????????????? None
        """
        if not self._current_session:
            return None
        
        gateway = self._current_session.get_metadata("_gateway")
        session_key = self._current_session.get_metadata("_session_key")
        
        if not gateway or not session_key:
            return None
        
        # ??????
        interrupt_msg = await gateway.check_interrupt(session_key)
        if interrupt_msg:
            return interrupt_msg.plain_text
        
        return None
    
    def set_interrupt_enabled(self, enabled: bool) -> None:
        """
        ??????????
        
        Args:
            enabled: ????
        """
        self._interrupt_enabled = enabled
        logger.info(f"Interrupt check {'enabled' if enabled else 'disabled'}")
    
    async def _chat_with_tools(self, message: str) -> str:
        """
        ???????????
        
        ? LLM ??????????????????
        
        Args:
            message: ????
        
        Returns:
            ??????
        """
        # ????????????????????
        # ???????????????????????
        messages = list(self._context.messages)
        
        # ????????????????
        messages = await self._compress_context(messages)
        
        max_iterations = settings.max_iterations  # Ralph Wiggum ???????
        
        # ??????
        recent_tool_calls: list[str] = []
        max_repeated_calls = 3
        
        for iteration in range(max_iterations):
            # ??????????????????????????
            if iteration > 0:
                messages = await self._compress_context(messages)
            
            # ?? Brain????????????????????
            response = await asyncio.to_thread(
                self.brain.messages_create,
                model=self.brain.model,
                max_tokens=self.brain.max_tokens,
                system=self._context.system,
                tools=self._tools,
                messages=messages,
            )
            
            # ????
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
            
            # ???????????????
            if not tool_calls:
                return strip_thinking_tags(text_content)
            
            # ????
            call_signature = "|".join([f"{tc['name']}:{sorted(tc['input'].items())}" for tc in tool_calls])
            recent_tool_calls.append(call_signature)
            if len(recent_tool_calls) > max_repeated_calls:
                recent_tool_calls = recent_tool_calls[-max_repeated_calls:]
            
            if len(recent_tool_calls) >= max_repeated_calls and len(set(recent_tool_calls)) == 1:
                logger.warning(f"[Loop Detection] Same tool call repeated {max_repeated_calls} times, ending chat")
                return "??????????????"
            
            # ??????????
            logger.info(f"Chat iteration {iteration + 1}, {len(tool_calls)} tool calls")
            
            # ?? assistant ??
            # MiniMax M2.1 Interleaved Thinking ???
            # ?????? thinking ??????????
            assistant_content = []
            for block in response.content:
                if block.type == "thinking":
                    # ?? thinking ??MiniMax M2.1 ???
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
            
            # ?????????????????
            tool_results = []
            interrupt_detected = False
            
            for i, tool_call in enumerate(tool_calls):
                # === ????? ===
                # ?????????????????
                if i > 0:  # ???????????????
                    interrupt_hint = await self._check_interrupt()
                    if interrupt_hint:
                        logger.info(f"[Interrupt] Detected during tool execution, tool {i+1}/{len(tool_calls)}")
                        interrupt_detected = True
                        # ???????????
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_call["id"],
                            "content": f"{interrupt_hint}\n\n??????????????????????????????????????",
                        })
                        # ?????????
                        for remaining_call in tool_calls[i+1:]:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": remaining_call["id"],
                                "content": "[???????: ????????]",
                            })
                        break
                
                # ??????
                result = await self._execute_tool(tool_call["name"], tool_call["input"])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call["id"],
                    "content": result,
                })
                logger.info(f"Tool {tool_call['name']} result: {result[:200]}..." if len(result) > 200 else f"Tool {tool_call['name']} result: {result}")
            
            messages.append({"role": "user", "content": tool_results})
            
            # ??????????????? LLM ???????
            
            # ??????
            if response.stop_reason == "end_turn":
                break
        
        # ?????????????? thinking ???
        return strip_thinking_tags(text_content) or "????"
    
    async def execute_task_from_message(self, message: str) -> TaskResult:
        """??????????"""
        task = Task(
            id=str(uuid.uuid4())[:8],
            description=message,
            session_id=getattr(self, '_current_session_id', None),  # ??????
            priority=1,
        )
        return await self.execute_task(task)
    
    async def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """
        ??????
        
        Args:
            tool_name: ????
            tool_input: ??????
        
        Returns:
            ??????
        """
        logger.info(f"Executing tool: {tool_name} with {tool_input}")
        
        try:
            # === ???????? ===
            if tool_name == "run_shell":
                command = tool_input["command"]
                # ?? LLM ?????????? 60 ?
                timeout = tool_input.get("timeout", 60)
                # ??????? 10 ???? 600 ?
                timeout = max(10, min(timeout, 600))
                
                result = await self.shell_tool.run(
                    command,
                    cwd=tool_input.get("cwd"),
                    timeout=timeout,
                )
                
                # ??????????????? AI ???
                from ..logging import get_session_log_buffer
                log_buffer = get_session_log_buffer()
                
                command_preview = tool_input["command"][:100]
                if len(tool_input["command"]) > 100:
                    command_preview += "..."
                
                # ???????
                output_preview = result.stdout[:500] if result.stdout else ""
                if len(result.stdout or "") > 500:
                    output_preview += f"\n... (? {len(result.stdout)} ??)"
                
                if result.success:
                    log_buffer.add_log(
                        level="INFO",
                        module="shell",
                        message=f"$ {command_preview}\n[exit: 0]\n{output_preview}",
                    )
                    return f"?????? (exit code: 0):\n{result.stdout}"
                else:
                    # ???????
                    error_output = result.stderr[:500] if result.stderr else ""
                    log_buffer.add_log(
                        level="ERROR",
                        module="shell",
                        message=f"$ {command_preview}\n[exit: {result.returncode}]\nstdout: {output_preview}\nstderr: {error_output}",
                    )
                    
                    # ????????AI????
                    output_parts = [f"?????? (exit code: {result.returncode})"]
                    if result.stdout:
                        output_parts.append(f"[stdout]:\n{result.stdout}")
                    if result.stderr:
                        output_parts.append(f"[stderr]:\n{result.stderr}")
                    if not result.stdout and not result.stderr:
                        output_parts.append("(????????????????)")
                    # ?? AI ???????????
                    output_parts.append("\n??: ???????????? get_session_logs ???????????????")
                    return "\n".join(output_parts)
            
            elif tool_name == "write_file":
                await self.file_tool.write(
                    tool_input["path"],
                    tool_input["content"]
                )
                return f"?????: {tool_input['path']}"
            
            elif tool_name == "read_file":
                content = await self.file_tool.read(tool_input["path"])
                return f"????:\n{content}"
            
            elif tool_name == "list_directory":
                files = await self.file_tool.list_dir(tool_input["path"])
                return f"????:\n" + "\n".join(files)
            
            # === Skills ?? (SKILL.md ??) ===
            elif tool_name == "list_skills":
                skills = self.skill_registry.list_all()
                if not skills:
                    return "??????????\n\n??: ????? skills/ ????????????? SKILL.md ????"
                
                output = f"??? {len(skills)} ??? (?? Agent Skills ??):\n\n"
                for skill in skills:
                    auto = "??" if not skill.disable_model_invocation else "??"
                    output += f"**{skill.name}** [{auto}]\n"
                    output += f"  {skill.description}\n\n"
                return output
            
            elif tool_name == "get_skill_info":
                skill_name = tool_input["skill_name"]
                skill = self.skill_registry.get(skill_name)
                
                if not skill:
                    return f"? ?????: {skill_name}"
                
                # ????? body (Level 2)
                body = skill.get_body()
                
                output = f"# ??: {skill.name}\n\n"
                output += f"**??**: {skill.description}\n"
                if skill.license:
                    output += f"**???**: {skill.license}\n"
                if skill.compatibility:
                    output += f"**???**: {skill.compatibility}\n"
                output += f"\n---\n\n"
                output += body or "(?????)"
                
                return output
            
            elif tool_name == "run_skill_script":
                skill_name = tool_input["skill_name"]
                script_name = tool_input["script_name"]
                args = tool_input.get("args", [])
                
                success, output = self.skill_loader.run_script(
                    skill_name, script_name, args
                )
                
                if success:
                    return f"? ??????:\n{output}"
                else:
                    return f"? ??????:\n{output}"
            
            elif tool_name == "get_skill_reference":
                skill_name = tool_input["skill_name"]
                ref_name = tool_input.get("ref_name", "REFERENCE.md")
                
                content = self.skill_loader.get_reference(skill_name, ref_name)
                
                if content:
                    return f"# ????: {ref_name}\n\n{content}"
                else:
                    return f"? ???????: {skill_name}/{ref_name}"
            
            elif tool_name == "install_skill":
                source = tool_input["source"]
                name = tool_input.get("name")
                subdir = tool_input.get("subdir")
                extra_files = tool_input.get("extra_files", [])
                
                result = await self._install_skill(source, name, subdir, extra_files)
                return result
            
            # === ????? ===
            elif tool_name == "generate_skill":
                description = tool_input["description"]
                name = tool_input.get("name")
                
                result = await self.skill_generator.generate(description, name)
                
                if result.success:
                    return f"""? ???????

**??**: {result.skill_name}
**??**: {result.skill_dir}
**??**: {'??' if result.test_passed else '???'}

????????????????:
- `get_skill_info` ??????
- `run_skill_script` ???? (scripts/main.py)"""
                else:
                    return f"? ??????: {result.error or '????'}"
            
            elif tool_name == "improve_skill":
                skill_name = tool_input["skill_name"]
                feedback = tool_input["feedback"]
                
                result = await self.skill_generator.improve(skill_name, feedback)
                
                if result.success:
                    return f"? ?????: {skill_name}\n??: {'??' if result.test_passed else '???'}"
                else:
                    return f"? ??????: {result.error or '????'}"
            
            # === ???? ===
            elif tool_name == "add_memory":
                from ..memory.types import Memory, MemoryType, MemoryPriority
                
                content = tool_input["content"]
                mem_type_str = tool_input["type"]
                importance = tool_input.get("importance", 0.5)
                
                # ????
                type_map = {
                    "fact": MemoryType.FACT,
                    "preference": MemoryType.PREFERENCE,
                    "skill": MemoryType.SKILL,
                    "error": MemoryType.ERROR,
                    "rule": MemoryType.RULE,
                }
                mem_type = type_map.get(mem_type_str, MemoryType.FACT)
                
                # ??????????
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
                    return f"? ???: [{mem_type_str}] {content}\nID: {memory_id}"
                else:
                    return "? ????????????????????????????????"
            
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
                    return f"???? '{query}' ?????"
                
                output = f"?? {len(memories)} ?????:\n\n"
                for m in memories:
                    output += f"- [{m.type.value}] {m.content}\n"
                    output += f"  (???: {m.importance_score:.1f}, ????: {m.access_count})\n\n"
                
                return output
            
            elif tool_name == "get_memory_stats":
                stats = self.memory_manager.get_stats()
                
                output = f"""??????:

- ????: {stats['total']}
- ????: {stats['sessions_today']}
- ?????: {stats['unprocessed_sessions']}

???:
"""
                for type_name, count in stats.get('by_type', {}).items():
                    output += f"  - {type_name}: {count}\n"
                
                output += "\n????:\n"
                for priority, count in stats.get('by_priority', {}).items():
                    output += f"  - {priority}: {count}\n"
                
                return output
            
            # === ???????????? Level 2?===
            elif tool_name == "get_tool_info":
                tool_name_to_query = tool_input["tool_name"]
                return self.tool_catalog.get_tool_info_formatted(tool_name_to_query)
            
            # === MCP ?? ===
            elif tool_name == "call_mcp_tool":
                server = tool_input["server"]
                mcp_tool_name = tool_input["tool_name"]
                arguments = tool_input.get("arguments", {})
                
                # ??????????
                if server not in self.mcp_client.list_connected():
                    # ????
                    connected = await self.mcp_client.connect(server)
                    if not connected:
                        return f"? ????? MCP ???: {server}"
                
                result = await self.mcp_client.call_tool(server, mcp_tool_name, arguments)
                
                if result.success:
                    return f"? MCP ??????:\n{result.data}"
                else:
                    return f"? MCP ??????: {result.error}"
            
            elif tool_name == "list_mcp_servers":
                servers = self.mcp_catalog.list_servers()
                connected = self.mcp_client.list_connected()
                
                if not servers:
                    return "?????? MCP ???\n\n??: MCP ??????? mcps/ ???"
                
                output = f"??? {len(servers)} ? MCP ???:\n\n"
                for server_id in servers:
                    status = "?? ???" if server_id in connected else "? ???"
                    output += f"- **{server_id}** {status}\n"
                
                output += "\n?? `call_mcp_tool(server, tool_name, arguments)` ????"
                return output
            
            elif tool_name == "get_mcp_instructions":
                server = tool_input["server"]
                instructions = self.mcp_catalog.get_server_instructions(server)
                
                if instructions:
                    return f"# MCP ??? {server} ????\n\n{instructions}"
                else:
                    return f"? ?????? {server} ?????????????"
            
            # === ????? (browser-use MCP) ===
            elif tool_name.startswith("browser_") or "browser_" in tool_name:
                if not hasattr(self, 'browser_mcp') or not self.browser_mcp:
                    return "? ??? MCP ?????????? playwright: pip install playwright && playwright install chromium"
                
                # ??????? (?? mcp__browser-use__browser_navigate ??)
                actual_tool_name = tool_name
                if "browser_" in tool_name and not tool_name.startswith("browser_"):
                    # ?? browser_xxx ??
                    import re
                    match = re.search(r'(browser_\w+)', tool_name)
                    if match:
                        actual_tool_name = match.group(1)
                
                result = await self.browser_mcp.call_tool(actual_tool_name, tool_input)
                
                if result.get("success"):
                    return f"? {result.get('result', 'OK')}"
                else:
                    return f"? {result.get('error', '????')}"
            
            # === ?????? ===
            elif tool_name == "schedule_task":
                if not hasattr(self, 'task_scheduler') or not self.task_scheduler:
                    return "? ??????????"
                
                from ..scheduler import ScheduledTask, TriggerType
                from ..scheduler.task import TaskType
                
                trigger_type = TriggerType(tool_input["trigger_type"])
                task_type = TaskType(tool_input.get("task_type", "task"))
                
                # ???? IM ?????????
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
                next_run = task.next_run.strftime('%Y-%m-%d %H:%M:%S') if task.next_run else '???'
                
                # ??????
                type_display = "?? ????" if task_type == TaskType.REMINDER else "?? ????"
                
                # ???????????
                print(f"\n?? ???????:")
                print(f"   ID: {task_id}")
                print(f"   ??: {task.name}")
                print(f"   ??: {type_display}")
                print(f"   ??: {task.trigger_type.value}")
                print(f"   ????: {next_run}")
                if channel_id and chat_id:
                    print(f"   ????: {channel_id}/{chat_id}")
                print()
                
                logger.info(f"Created scheduled task: {task_id} ({task.name}), type={task_type.value}, next run: {next_run}")
                
                return f"? ???{type_display}\n- ID: {task_id}\n- ??: {task.name}\n- ????: {next_run}"
            
            elif tool_name == "list_scheduled_tasks":
                if not hasattr(self, 'task_scheduler') or not self.task_scheduler:
                    return "? ??????????"
                
                enabled_only = tool_input.get("enabled_only", False)
                tasks = self.task_scheduler.list_tasks(enabled_only=enabled_only)
                
                if not tasks:
                    return "????????"
                
                output = f"? {len(tasks)} ?????:\n\n"
                for t in tasks:
                    status = "?" if t.enabled else "?"
                    next_run = t.next_run.strftime('%m-%d %H:%M') if t.next_run else 'N/A'
                    output += f"[{status}] {t.name} ({t.id})\n"
                    output += f"    ??: {t.trigger_type.value}, ??: {next_run}\n"
                
                return output
            
            elif tool_name == "cancel_scheduled_task":
                if not hasattr(self, 'task_scheduler') or not self.task_scheduler:
                    return "? ??????????"
                
                task_id = tool_input["task_id"]
                success = await self.task_scheduler.remove_task(task_id)
                
                if success:
                    return f"? ?? {task_id} ???"
                else:
                    return f"? ?? {task_id} ???"
            
            elif tool_name == "update_scheduled_task":
                if not hasattr(self, 'task_scheduler') or not self.task_scheduler:
                    return "? ??????????"
                task_id = tool_input["task_id"]
                task = self.task_scheduler.get_task(task_id)
                if not task:
                    return f"? ?? {task_id} ???"
                changes = []
                if "notify_on_start" in tool_input:
                    task.metadata["notify_on_start"] = tool_input["notify_on_start"]
                    changes.append("????: " + ("?" if tool_input["notify_on_start"] else "?"))
                if "notify_on_complete" in tool_input:
                    task.metadata["notify_on_complete"] = tool_input["notify_on_complete"]
                    changes.append("????: " + ("?" if tool_input["notify_on_complete"] else "?"))
                if "enabled" in tool_input:
                    if tool_input["enabled"]:
                        task.enable()
                        changes.append("???")
                    else:
                        task.disable()
                        changes.append("???")
                self.task_scheduler._save_tasks()
                if changes:
                    return f"? ?? {task.name} ???: " + ", ".join(changes)
                return "?? ??????????"
            
            elif tool_name == "trigger_scheduled_task":
                if not hasattr(self, 'task_scheduler') or not self.task_scheduler:
                    return "? ??????????"
                
                task_id = tool_input["task_id"]
                execution = await self.task_scheduler.trigger_now(task_id)
                
                if execution:
                    status = "??" if execution.status == "success" else "??"
                    return f"? ??????????: {status}\n??: {execution.result or execution.error or 'N/A'}"
                else:
                    return f"? ?? {task_id} ???"
            
            # === Thinking ???? ===
            elif tool_name == "enable_thinking":
                enabled = tool_input["enabled"]
                reason = tool_input.get("reason", "")
                
                self.brain.set_thinking_mode(enabled)
                
                if enabled:
                    logger.info(f"Thinking mode enabled by LLM: {reason}")
                    return f"? ????????????: {reason}\n???????????????"
                else:
                    logger.info(f"Thinking mode disabled by LLM: {reason}")
                    return f"? ????????????: {reason}\n??????????"
            
            # === ?????? ===
            elif tool_name == "update_user_profile":
                key = tool_input["key"]
                value = tool_input["value"]
                
                available_keys = self.profile_manager.get_available_keys()
                if key not in available_keys:
                    return f"? ??????: {key}\n????: {', '.join(available_keys)}"
                
                success = self.profile_manager.update_profile(key, value)
                if success:
                    return f"? ???????: {key} = {value}"
                else:
                    return f"? ????: {key}"
            
            elif tool_name == "skip_profile_question":
                key = tool_input["key"]
                self.profile_manager.skip_question(key)
                return f"? ?????: {key}"
            
            elif tool_name == "get_user_profile":
                summary = self.profile_manager.get_profile_summary()
                return summary
            
            # === ?????? ===
            elif tool_name == "get_session_logs":
                from ..logging import get_session_log_buffer
                
                count = tool_input.get("count", 20)
                level_filter = tool_input.get("level")
                
                # ??????
                count = min(max(1, count), 200)
                
                buffer = get_session_log_buffer()
                logs_text = buffer.get_logs_formatted(
                    count=count,
                    level_filter=level_filter,
                )
                
                stats = buffer.get_stats()
                session_id = stats.get("current_session", "_global")
                total_logs = stats.get("sessions", {}).get(session_id, 0)
                
                return f"?? ??????? {count} ??? {total_logs} ??:\n\n{logs_text}"
            
            # === IM ???? ===
            elif tool_name == "send_to_chat":
                # ????? IM ???
                if not Agent._current_im_session or not Agent._current_im_gateway:
                    return "? ????? IM ?????????? IM ???"
                
                session = Agent._current_im_session
                gateway = Agent._current_im_gateway
                
                text = tool_input.get("text", "")
                file_path = tool_input.get("file_path", "")
                voice_path = tool_input.get("voice_path", "")
                caption = tool_input.get("caption", "")
                
                try:
                    from pathlib import Path
                    
                    # ?????
                    adapter = gateway.get_adapter(session.channel)
                    if not adapter:
                        return f"? ??????: {session.channel}"
                    
                    # ????
                    if voice_path:
                        voice_path_obj = Path(voice_path)
                        if not voice_path_obj.exists():
                            return f"? ???????: {voice_path}"
                        
                        if hasattr(adapter, 'send_voice'):
                            await adapter.send_voice(
                                chat_id=session.chat_id,
                                voice_path=str(voice_path_obj),
                                caption=caption or text,
                            )
                            self._task_message_sent = True
                            return f"? ?????: {voice_path}"
                        else:
                            # ???????????????
                            await adapter.send_file(
                                chat_id=session.chat_id,
                                file_path=str(voice_path_obj),
                                caption=caption or text,
                            )
                            self._task_message_sent = True
                            return f"? ?????????????: {voice_path}"
                    
                    # ????/??
                    if file_path:
                        file_path_obj = Path(file_path)
                        if not file_path_obj.exists():
                            return f"? ?????: {file_path}"
                        
                        # ????????
                        suffix = file_path_obj.suffix.lower()
                        
                        if suffix in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
                            # ????
                            await adapter.send_photo(
                                chat_id=session.chat_id,
                                photo_path=str(file_path_obj),
                                caption=caption or text,
                            )
                            self._task_message_sent = True
                            return f"? ?????: {file_path}"
                        else:
                            # ????
                            await adapter.send_file(
                                chat_id=session.chat_id,
                                file_path=str(file_path_obj),
                                caption=caption or text,
                            )
                            self._task_message_sent = True
                            return f"? ?????: {file_path}"
                    
                    # ?????
                    elif text:
                        await gateway.send_to_session(session, text)
                        self._task_message_sent = True
                        return f"? ?????"
                    
                    else:
                        return "? ??????????text, file_path ? voice_path?"
                        
                except Exception as e:
                    logger.error(f"send_to_chat error: {e}", exc_info=True)
                    return f"? ????: {str(e)}"
            
            elif tool_name == "get_voice_file":
                # ????? IM ???
                if not Agent._current_im_session:
                    return "? ????? IM ?????"
                
                session = Agent._current_im_session
                
                # ? session metadata ????????
                pending_voices = session.get_metadata("pending_voices")
                if pending_voices and len(pending_voices) > 0:
                    voice_paths = [v.get("local_path", "") for v in pending_voices if v.get("local_path")]
                    if voice_paths:
                        return f"? ???????????:\n" + "\n".join(voice_paths)
                
                # ?????????????
                # ?? session ? messages
                for msg in reversed(session.messages[-10:]):
                    content = msg.get("content", "")
                    if isinstance(content, str) and "[??:" in content:
                        # ???????????
                        # ????????? data/telegram/media/ ??
                        media_dir = Path("data/telegram/media")
                        if media_dir.exists():
                            voice_files = list(media_dir.glob("*.ogg")) + list(media_dir.glob("*.oga")) + list(media_dir.glob("*.opus"))
                            if voice_files:
                                # ?????????
                                latest = max(voice_files, key=lambda f: f.stat().st_mtime)
                                return f"? ???????: {latest}"
                
                return "? ????????????????????????????"
            
            elif tool_name == "get_image_file":
                # ????? IM ???
                if not Agent._current_im_session:
                    return "? ????? IM ?????"
                
                session = Agent._current_im_session
                
                # ? session metadata ????????
                pending_images = session.get_metadata("pending_images")
                if pending_images and len(pending_images) > 0:
                    # pending_images ? multimodal ???? local_path
                    image_paths = []
                    for img in pending_images:
                        if isinstance(img, dict):
                            # ???????????
                            local_path = img.get("local_path", "")
                            if local_path:
                                image_paths.append(local_path)
                    if image_paths:
                        return f"? ???????????:\n" + "\n".join(image_paths)
                
                # ??? media ????
                media_dir = Path("data/telegram/media")
                if media_dir.exists():
                    image_files = list(media_dir.glob("*.jpg")) + list(media_dir.glob("*.png")) + list(media_dir.glob("*.webp"))
                    if image_files:
                        latest = max(image_files, key=lambda f: f.stat().st_mtime)
                        return f"? ???????: {latest}"
                
                return "? ??????????????????????????"
            
            elif tool_name == "get_chat_history":
                # ????? IM ???
                if not Agent._current_im_session:
                    return "? ????? IM ?????"
                
                session = Agent._current_im_session
                limit = tool_input.get("limit", 20)
                include_system = tool_input.get("include_system", True)
                
                # ? session manager ??????
                from ..sessions import session_manager
                
                history = session_manager.get_history(
                    channel=session.channel,
                    chat_id=session.chat_id,
                    user_id=session.user_id,
                    limit=limit
                )
                
                if not history:
                    return "?? ??????"
                
                # ?????
                result_lines = [f"?? ?? {len(history)} ????\n"]
                for i, msg in enumerate(history, 1):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    timestamp = msg.get("timestamp", "")
                    
                    # ?????????????
                    if not include_system and role == "system":
                        continue
                    
                    # ????
                    if role == "user":
                        role_icon = "?? ??"
                    elif role == "assistant":
                        role_icon = "?? ??"
                    elif role == "system":
                        role_icon = "?? ??"
                    else:
                        role_icon = f"?? {role}"
                    
                    # ?????
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
                return f"????: {tool_name}"
                
        except Exception as e:
            logger.error(f"Tool execution error: {e}", exc_info=True)
            return f"??????: {str(e)}"
    
    async def execute_task(self, task: Task) -> TaskResult:
        """
        ???????????
        
        ?????????
        1. ????????? 3 ?
        2. ???????????????
        3. ????????????????????????????
        
        Args:
            task: ????
        
        Returns:
            TaskResult
        """
        import time
        start_time = time.time()
        
        if not self._initialized:
            await self.initialize()
        
        logger.info(f"Executing task: {task.description}")
        
        # === ??????? ===
        task_monitor = TaskMonitor(
            task_id=task.id,
            description=task.description,
            session_id=task.session_id,
            timeout_seconds=300,  # ?????300?
            retrospect_threshold=60,  # ?????60?
            fallback_model="gpt-4o",  # ??????????
            retry_before_switch=3,  # ????? 3 ?
        )
        task_monitor.start(self.brain.model)
        
        # ??????????? (??????)
        # ????????????? _context.system ?
        system_prompt = self._context.system + """

## Task Execution Strategy

????????????:

1. **Check skill catalog above** - ???????????????????????
2. **If skill matches**: Use `get_skill_info(skill_name)` to load full instructions
3. **Run script**: Use `run_skill_script(skill_name, script_name, args)`
4. **If no skill matches**: Use `generate_skill(description)` to create one

????????????"""

        # === ???????????????????????? ===
        original_task_message = {"role": "user", "content": task.description}
        messages = [original_task_message.copy()]
        
        max_tool_iterations = settings.max_iterations  # Ralph Wiggum ???????
        iteration = 0
        final_response = ""
        current_model = self.brain.model
        
        # ??????
        recent_tool_calls: list[str] = []  # ?????????
        max_repeated_calls = 3  # ????????????????
        
        while iteration < max_tool_iterations:
            iteration += 1
            logger.info(f"Task iteration {iteration}")
            
            # ?????????
            task_monitor.begin_iteration(iteration, current_model)
            
            # === ???????? ===
            # ??????????????
            if task_monitor.should_switch_model:
                new_model = task_monitor.fallback_model
                task_monitor.switch_model(
                    new_model, 
                    f"?????? {task_monitor.timeout_seconds} ???? {task_monitor.retry_count} ????",
                    reset_context=True
                )
                current_model = new_model
                
                # === ????????????????? ===
                logger.warning(
                    f"[ModelSwitch] Task {task.id}: Switching to {new_model}, resetting context. "
                    f"Discarding {len(messages) - 1} tool-related messages"
                )
                messages = [original_task_message.copy()]
                
                # ????????
                messages.append({
                    "role": "user",
                    "content": (
                        "[????] ???????????????????"
                        "????????????????????????????"
                    ),
                })
                
                # ??????
                recent_tool_calls.clear()
            
            # ????????????????????????
            if iteration > 1:
                messages = await self._compress_context(messages)
            
            # ?? Brain?????????????
            try:
                response = await asyncio.to_thread(
                    self.brain.messages_create,
                    model=current_model,
                    max_tokens=self.brain.max_tokens,
                    system=system_prompt,
                    tools=self._tools,
                    messages=messages,
                )
                
                # ???????????
                task_monitor.reset_retry_count()
                
            except Exception as e:
                logger.error(f"[LLM] Brain call failed in task {task.id}: {e}")
                
                # ?????????????
                should_retry = task_monitor.record_error(str(e))
                
                if should_retry:
                    # ????
                    logger.info(f"[LLM] Will retry (attempt {task_monitor.retry_count}/{task_monitor.retry_before_switch})")
                    await asyncio.sleep(2)
                    continue
                else:
                    # ???????????
                    new_model = task_monitor.fallback_model
                    task_monitor.switch_model(
                        new_model,
                        f"LLM ??????? {task_monitor.retry_count} ????: {e}",
                        reset_context=True
                    )
                    current_model = new_model
                    
                    # ?????
                    logger.warning(f"[ModelSwitch] Task {task.id}: Switching to {new_model} due to errors, resetting context")
                    messages = [original_task_message.copy()]
                    messages.append({
                        "role": "user",
                        "content": (
                            "[????] ???????????????????"
                            "???????????????"
                        ),
                    })
                    recent_tool_calls.clear()
                    continue
            
            # ????
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
            
            # ?????????
            task_monitor.end_iteration(text_content[:200] if text_content else "")
            
            # ????????????? thinking ????????????
            if text_content:
                cleaned_text = clean_llm_response(text_content)
                # ?????????????????????
                # ??????????????? LLM ?????
                if not tool_calls and cleaned_text:
                    final_response = cleaned_text
            
            # ?????????????
            if not tool_calls:
                break
            
            # ?????????????
            call_signature = "|".join([f"{tc['name']}:{sorted(tc['input'].items())}" for tc in tool_calls])
            recent_tool_calls.append(call_signature)
            
            # ??????????
            if len(recent_tool_calls) > max_repeated_calls:
                recent_tool_calls = recent_tool_calls[-max_repeated_calls:]
            
            # ????????
            if len(recent_tool_calls) >= max_repeated_calls:
                if len(set[str](recent_tool_calls)) == 1:
                    logger.warning(f"[Loop Detection] Same tool call repeated {max_repeated_calls} times, forcing task end")
                    final_response = "????????????????????????????????"
                    break
            
            # ??????
            # MiniMax M2.1 Interleaved Thinking ???
            # ?????? thinking ??????????
            assistant_content = []
            for block in response.content:
                if block.type == "thinking":
                    # ?? thinking ??MiniMax M2.1 ???
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
            
            # ???????????
            tool_results = []
            executed_tools = []  # ??????????????
            for tool_call in tool_calls:
                # ???????????
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
                    
                    # ???????????????
                    task_monitor.end_tool_call(str(result)[:200] if result else "", success=True)
                except Exception as e:
                    logger.error(f"Tool {tool_call['name']} error: {e}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_call["id"],
                        "content": f"??????: {str(e)}",
                        "is_error": True,
                    })
                    # ???????????????
                    task_monitor.end_tool_call(str(e), success=False)
            
            messages.append({"role": "user", "content": tool_results})
            
            # ???????????? stop_reason???????? LLM ?????
        
        # ???????? final_response ?????? LLM ??????
        if not final_response or len(final_response.strip()) < 10:
            logger.info("Task completed but no final response, requesting summary...")
            try:
                # ?? LLM ????????
                messages.append({
                    "role": "user", 
                    "content": "????????????????????????"
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
                final_response = "????????"
        
        # === ?????? ===
        metrics = task_monitor.complete(
            success=True,
            response=final_response[:200],
        )
        
        # === ?????????????????????? ===
        if metrics.retrospect_needed:
            # ????????????????
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
        """???????"""
        if result.success:
            return f"""? ????

{result.data}

---
????: {result.iterations}
??: {result.duration_seconds:.2f}?"""
        else:
            return f"""? ??????

??: {result.error}

---
????: {result.iterations}
??: {result.duration_seconds:.2f}?

??????????..."""
    
    async def self_check(self) -> dict[str, Any]:
        """
        ??
        
        Returns:
            ????
        """
        logger.info("Running self-check...")
        
        results = {
            "timestamp": datetime.now().isoformat(),
            "status": "healthy",
            "checks": {},
        }
        
        # ?? Brain
        try:
            response = await self.brain.think("?????????????'OK'?")
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
        
        # ?? Identity
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
        
        # ????
        results["checks"]["config"] = {
            "status": "ok" if settings.anthropic_api_key else "error",
            "message": "API key configured" if settings.anthropic_api_key else "API key missing",
        }
        
        # ?????? (SKILL.md ??)
        skill_count = self.skill_registry.count
        results["checks"]["skills"] = {
            "status": "ok",
            "message": f"??? {skill_count} ??? (Agent Skills ??)",
            "count": skill_count,
            "skills": [s.name for s in self.skill_registry.list_all()],
        }
        
        # ??????
        skills_path = settings.skills_path
        results["checks"]["skills_dir"] = {
            "status": "ok" if skills_path.exists() else "warning",
            "message": str(skills_path),
        }
        
        # ?? MCP ???
        mcp_servers = self.mcp_client.list_servers()
        mcp_connected = self.mcp_client.list_connected()
        results["checks"]["mcp"] = {
            "status": "ok",
            "message": f"?? {len(mcp_servers)} ????, ??? {len(mcp_connected)} ?",
            "servers": mcp_servers,
            "connected": mcp_connected,
        }
        
        logger.info(f"Self-check complete: {results['status']}")
        
        return results
    
    def _on_iteration(self, iteration: int, task: Task) -> None:
        """Ralph ??????"""
        logger.debug(f"Ralph iteration {iteration} for task {task.id}")
    
    def _on_error(self, error: str, task: Task) -> None:
        """Ralph ??????"""
        logger.warning(f"Ralph error for task {task.id}: {error}")
    
    @property
    def is_initialized(self) -> bool:
        """??????"""
        return self._initialized
    
    @property
    def conversation_history(self) -> list[dict]:
        """????"""
        return self._conversation_history.copy()
    
    # ==================== ?????? ====================
    
    def set_scheduler_gateway(self, gateway: Any) -> None:
        """
        ??????????????
        
        ?????????????? IM ??
        
        Args:
            gateway: MessageGateway ??
        """
        if hasattr(self, '_task_executor') and self._task_executor:
            self._task_executor.gateway = gateway
            logger.info("Scheduler gateway configured")
    
    async def shutdown(self, task_description: str = "", success: bool = True, errors: list = None) -> None:
        """
        ?? Agent ?????
        
        Args:
            task_description: ?????????
            success: ??????
            errors: ???????
        """
        logger.info("Shutting down agent...")
        
        # ??????
        self.memory_manager.end_session(
            task_description=task_description,
            success=success,
            errors=errors or [],
        )
        
        # MEMORY.md ? DailyConsolidator ??????shutdown ????
        
        self._running = False
        logger.info("Agent shutdown complete")
    
    async def consolidate_memories(self) -> dict:
        """
        ???? (??????????)
        
        ??????? (???) ? cron job ??
        
        Returns:
            ??????
        """
        logger.info("Starting memory consolidation...")
        return await self.memory_manager.consolidate_daily()
    
    def get_memory_stats(self) -> dict:
        """??????"""
        return self.memory_manager.get_stats()

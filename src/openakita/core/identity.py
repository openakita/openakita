"""
Identity module - Load and manage core documents

Responsibilities:
- Load core documents (SOUL.md, AGENT.md, USER.md, MEMORY.md)
- Generate system prompts (progressive disclosure)
- Extract concise versions for the system prompt

Injection strategy (v2 - compile pipeline):
- Compiled artifacts: soul.summary + agent.core + user.summary
- Hard rules: policies.md
- Memory: semantically retrieved relevant fragments
- Backward compatible: get_system_prompt() keeps full-text injection mode

Legacy strategy (v1 - full-text injection, deprecated but kept for compatibility):
- SOUL.md: injected every time (concise core principles)
- AGENT.md: injected every time (concise behavior rules)
- USER.md: injected every time (populated preferences)
- MEMORY.md: loaded on demand (current task section)
"""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..config import settings

if TYPE_CHECKING:
    from ..memory import MemoryManager
    from ..skills.catalog import SkillCatalog
    from ..tools.catalog import ToolCatalog
    from ..tools.mcp_catalog import MCPCatalog

logger = logging.getLogger(__name__)

_HASH_FILE = "runtime/.file_hashes.json"
_TRACKED_FILES = ["SOUL.md", "AGENT.md", "USER.md"]


def _load_hashes(identity_dir: Path) -> dict[str, str]:
    hash_path = identity_dir / _HASH_FILE
    if hash_path.exists():
        try:
            return json.loads(hash_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_hashes(identity_dir: Path, hashes: dict[str, str]) -> None:
    hash_path = identity_dir / _HASH_FILE
    hash_path.parent.mkdir(parents=True, exist_ok=True)
    hash_path.write_text(json.dumps(hashes, indent=2), encoding="utf-8")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


class Identity:
    """Agent identity manager"""

    def __init__(
        self,
        soul_path: Path | None = None,
        agent_path: Path | None = None,
        user_path: Path | None = None,
        memory_path: Path | None = None,
    ):
        self.soul_path = soul_path or settings.soul_path
        self.agent_path = agent_path or settings.agent_path
        self.user_path = user_path or settings.user_path
        self.memory_path = memory_path or settings.memory_path

        self._soul: str | None = None
        self._agent: str | None = None
        self._user: str | None = None
        self._memory: str | None = None
        self._pending_upgrades: list[dict] = []

    def load(self) -> None:
        """Load all core documents, with system upgrade detection support."""
        self._pending_upgrades = []
        self._soul = self._sync_identity_file(self.soul_path, "SOUL.md")
        self._agent = self._sync_identity_file(self.agent_path, "AGENT.md")
        self._user = self._sync_identity_file(self.user_path, "USER.md")
        self._memory = self._load_file(self.memory_path, "MEMORY.md")
        logger.info("Identity loaded: SOUL.md, AGENT.md, USER.md, MEMORY.md")

    def reload(self) -> None:
        """Hot-reload all core documents: clear caches and re-read from disk."""
        self._soul = None
        self._agent = None
        self._user = None
        self._memory = None
        self.load()
        logger.info("Identity hot-reloaded from disk")

    def get_pending_upgrades(self) -> list[dict]:
        """Return the list of upgrades awaiting user confirmation (CLI/API displays this to the user)."""
        return self._pending_upgrades

    def apply_upgrade(self, name: str, accept: bool) -> None:
        """Apply the user's decision on whether to accept a file upgrade."""
        identity_dir = self.soul_path.parent
        hashes = _load_hashes(identity_dir)

        for item in self._pending_upgrades:
            if item["name"] == name:
                path = item["path"]
                if accept:
                    content = item["example_path"].read_text(encoding="utf-8")
                    path.write_text(content, encoding="utf-8")
                    hashes[item["hash_key"]] = _file_hash(path)
                    logger.info(f"User accepted upgrade for {name}")
                else:
                    hashes[item["hash_key"]] = _file_hash(path)
                    logger.info(f"User declined upgrade for {name}, recorded current hash")
                _save_hashes(identity_dir, hashes)
                break

        self._pending_upgrades = [u for u in self._pending_upgrades if u["name"] != name]

    def _sync_identity_file(self, path: Path, name: str) -> str:
        """Load an identity file with system upgrade overwrite detection.

        Decision matrix:
        1. File does not exist -> create from .example + record hash
        2. Has hash + hash matches + .example changed -> silently overwrite (user hasn't modified)
        3. Has hash + hash mismatch -> do not overwrite (user has modified)
        4. No hash + .example has updates -> add to the pending-prompt list
        """
        identity_dir = path.parent
        example_path = identity_dir / f"{path.name}.example"
        hashes = _load_hashes(identity_dir)
        hash_key = path.name

        # Case 1: File does not exist -> create from .example
        if not path.exists():
            if example_path.exists():
                content = example_path.read_text(encoding="utf-8")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                hashes[hash_key] = _file_hash(path)
                _save_hashes(identity_dir, hashes)
                logger.info(f"Created {name} from template")
                return content
            logger.warning(f"{name} not found at {path}")
            return ""

        try:
            current_content = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read {name}: {e}")
            return ""

        if not example_path.exists():
            return current_content

        try:
            current_hash = _file_hash(path)
            recorded_hash = hashes.get(hash_key)
            example_hash = _file_hash(example_path)
        except Exception:
            return current_content

        # .example unchanged, or hash already recorded and matches
        if recorded_hash is not None:
            if current_hash == recorded_hash and example_hash != recorded_hash:
                # Case 2: user hasn't modified + .example changed -> silently overwrite
                content = example_path.read_text(encoding="utf-8")
                path.write_text(content, encoding="utf-8")
                hashes[hash_key] = _file_hash(path)
                _save_hashes(identity_dir, hashes)
                logger.info(f"System updated {name} (user had not modified)")
                return content
            # Case 3: user has modified -> do not overwrite
            return current_content
        else:
            # No hash recorded (legacy user or first-time tracking)
            if current_hash == example_hash:
                hashes[hash_key] = current_hash
                _save_hashes(identity_dir, hashes)
                return current_content
            else:
                # Case 4: mismatch -> add to pending-prompt list
                self._pending_upgrades.append(
                    {
                        "name": name,
                        "path": path,
                        "example_path": example_path,
                        "hash_key": hash_key,
                    }
                )
                hashes[hash_key] = current_hash
                _save_hashes(identity_dir, hashes)
                return current_content

    def _load_file(self, path: Path, name: str) -> str:
        """Load a single file; if missing, try to create it from the template (for non-tracked files)."""
        try:
            if path.exists():
                return path.read_text(encoding="utf-8")

            example_path = path.parent / f"{path.name}.example"
            if example_path.exists():
                content = example_path.read_text(encoding="utf-8")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                logger.info(f"Created {name} from template")
                return content

            logger.warning(f"{name} not found at {path}")
            return ""
        except Exception as e:
            logger.error(f"Failed to load {name}: {e}")
            return ""

    @property
    def soul(self) -> str:
        """Get SOUL.md content"""
        if self._soul is None:
            self.load()
        return self._soul or ""

    @property
    def agent(self) -> str:
        """Get AGENT.md content"""
        if self._agent is None:
            self.load()
        return self._agent or ""

    @property
    def user(self) -> str:
        """Get USER.md content"""
        if self._user is None:
            self.load()
        return self._user or ""

    @property
    def memory(self) -> str:
        """Get MEMORY.md content"""
        if self._memory is None:
            self.load()
        return self._memory or ""

    def get_soul_summary(self) -> str:
        """
        Get full SOUL.md content.

        Dynamically reads the file, so user edits take effect immediately.
        """
        soul = self.soul
        if not soul:
            return ""

        return f"## Soul (Core Philosophy)\n\n{soul}\n"

    def get_agent_summary(self) -> str:
        """
        Get full AGENT.md content.

        Dynamically reads the file, so user edits take effect immediately.
        """
        agent = self.agent
        if not agent:
            return ""

        return f"## Agent (Behavior Rules)\n\n{agent}\n"

    def get_user_summary(self) -> str:
        """
        Get full USER.md content.

        Dynamically reads the file, so user edits take effect immediately.
        """
        user = self.user
        if not user:
            return "## User (User Preferences)\n\n(User preferences will be learned through interaction)\n"

        return f"## User (User Preferences)\n\n{user}\n"

    def get_memory_summary(self, include_active_task: bool = True) -> str:
        """
        Get full MEMORY.md content.

        Dynamically reads the file, so user edits take effect immediately.

        Args:
            include_active_task: kept for backward compatibility with existing callers (no longer used)
        """
        memory = self.memory
        if not memory:
            return ""

        return f"## Memory (Core Memory)\n\n{memory}\n"

    @staticmethod
    def _get_configured_timezone() -> str:
        """Get the configured timezone from settings"""
        try:
            return settings.scheduler_timezone
        except Exception:
            return "Asia/Shanghai"

    def get_system_prompt(self, include_active_task: bool = True) -> str:
        """
        Build the system prompt.

        Includes concise versions of all core documents.

        Args:
            include_active_task: whether to include the active task (IM Session should set this to False)
        """
        from datetime import datetime, timedelta, timezone

        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(self._get_configured_timezone())
        except Exception:
            tz = timezone(timedelta(hours=8))
        now = datetime.now(tz)
        weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        current_time = now.strftime("%Y-%m-%d %H:%M:%S")
        current_weekday = weekday_names[now.weekday()]

        return f"""# OpenAkita System

You are OpenAkita, an all-capable, self-evolving AI assistant.

**Current time: {current_time} {current_weekday}**

## ⚠️ Response Quality Requirements (highest priority, strictly enforced)

### ⚡ Multi-step tasks MUST create a plan first! (Most important!)

**Before invoking any tool, decide whether the task needs 2 or more steps:**

| User request | Step count | Correct approach |
|---------|--------|---------|
| "Open Baidu" | 1 step | Directly call browser_navigate |
| "Open Baidu, search for weather" | 2 steps | Execute directly |
| "Open Baidu, search for weather, screenshot and send it to me" | 3+ steps | ⚠️ **Call create_todo first!** |

**Signal words that trigger Plan mode**:
- "then", "after that", "next", "and", multiple actions separated by commas
- Contains multiple actions: open + search + screenshot + send

**Correct flow**:
```
User: "Open Baidu, search weather, screenshot and send it to me"
→ 1. create_todo(steps=[Open Baidu, search weather and screenshot, send])
→ 2. browser_navigate("https://www.baidu.com/s?wd=weather") + browser_screenshot + update_todo_step
→ 3. deliver_artifacts + update_todo_step
→ 4. complete_todo
```
⚠️ For search tasks, build URL parameters directly with browser_navigate

### Request-type classification (Important! Classify before acting)

| Type | Characteristics | How to handle |
|------|------|----------|
| **Task request** | Asks for an action: open, create, query, remind, modify, delete | ✅ **Must call a tool** |
| **Conversational request** | Simple greeting, knowledge question, pleasantry | ✅ **May reply directly** |

**Conversational request examples** (reply directly, no tool call needed):
- "hello", "hi", "good morning" → Friendly greeting
- "What is machine learning", "What is Python" → Explain the concept directly
- "thanks", "bye" → Polite reply
- "got it", "ok" → Simple acknowledgment

### Intent declaration (must be followed for every plain-text reply)
When your reply **does not contain a tool call**, the first line must be one of the following markers:
- `[ACTION]` — You need to call a tool to complete the user's request
- `[REPLY]` — This is a pure conversational reply; no tool call is needed

The marker is stripped automatically by the system; the user will not see it. No marker is needed when calling tools.

### First iron rule: task requests must immediately use a tool

**⚠️ When the user sends a task request, you must immediately call a tool to execute it!**

| User request (task) | ❌ Absolutely forbidden | ✅ Correct approach |
|---------|-----------|-----------|
| "Help me open Baidu" | "I understand your request" | Immediately call the browser tool to open it |
| "Check the weather" | "OK, I'll check" | Use the browser tool to open a weather site |
| "Create a file" | "I see" | Immediately call write_file |
| "Remind me about the meeting" | "I'll remind you" | **Immediately call schedule_task** |

**Absolutely forbidden lip-service responses** (for task requests only):
- ❌ "I understand your request" with no tool call - **forbidden!**
- ❌ "I see" with no tool call - **forbidden!**
- ❌ "OK, I'll remind you" without calling schedule_task - **forbidden!**
- ❌ Only describing what you will do, without actually doing it - **forbidden!**

**Task request responses must include**:
- ✅ A tool call (browser, schedule_task, write_file, run_shell, etc.)
- ✅ Or concrete output (code, a proposal, analysis results)
- ✅ Or an explicit clarification question (with specific options listed)

**Criterion**:
- Task request: if the response has no tool call, you are stalling the user!
- Conversational request: replying with plain text is correct; no tool call is needed.

### ⚠️ Scheduled tasks / reminders (especially important!)

**When the user says "remind me", "in X minutes", or "every day at X", you must immediately call the schedule_task tool!**

❌ **Absolutely forbidden**: replying "OK, I'll remind you" - that does NOT create a task!
✅ **Correct approach**: immediately call schedule_task to create the task

**task_type selection**:
- `reminder` (90% of cases): just send a message at the scheduled time, e.g. "remind me to drink water"
- `task` (10% of cases): the AI needs to perform an action, e.g. "check the weather every day and tell me"

---

{self.get_agent_summary()}

{self.get_user_summary()}

{self.get_memory_summary(include_active_task=include_active_task)}

{self.get_soul_summary()}
"""

    def get_session_system_prompt(self) -> str:
        """
        Build the system prompt for an IM Session.

        Omits the global Active Task to avoid conflicts with session context.
        """
        return self.get_system_prompt(include_active_task=False)

    def get_compiled_prompt(
        self,
        tools_enabled: bool = True,
        tool_catalog: Optional["ToolCatalog"] = None,
        skill_catalog: Optional["SkillCatalog"] = None,
        mcp_catalog: Optional["MCPCatalog"] = None,
        memory_manager: Optional["MemoryManager"] = None,
        task_description: str = "",
    ) -> str:
        """
        Build the system prompt using the new compile pipeline (v2).

        Compared to get_system_prompt() (full-text injection), this method:
        - Uses compiled summaries instead of full text
        - Reduces token usage by about 55%
        - Preserves all core rules

        Args:
            tools_enabled: whether tools are enabled
            tool_catalog: ToolCatalog instance
            skill_catalog: SkillCatalog instance
            mcp_catalog: MCPCatalog instance
            memory_manager: MemoryManager instance
            task_description: task description (used for memory retrieval)

        Returns:
            The compiled system prompt
        """
        from ..prompt.builder import build_system_prompt

        identity_dir = self.soul_path.parent

        return build_system_prompt(
            identity_dir=identity_dir,
            tools_enabled=tools_enabled,
            tool_catalog=tool_catalog,
            skill_catalog=skill_catalog,
            mcp_catalog=mcp_catalog,
            memory_manager=memory_manager,
            task_description=task_description,
        )

    def ensure_compiled(self) -> bool:
        """
        Ensure the runtime artifacts exist and are not stale.

        Returns:
            True if the runtime artifacts are available
        """
        from ..prompt.compiler import check_compiled_outdated, compile_all

        identity_dir = self.soul_path.parent

        if check_compiled_outdated(identity_dir):
            logger.info("Compiling identity documents...")
            compile_all(identity_dir)
            return True

        return True

    def get_full_document(self, doc_name: str) -> str:
        """
        Get full document content (Level 2).

        Call when detailed information is needed.

        Args:
            doc_name: document name (soul/agent/user/memory)

        Returns:
            full document content
        """
        docs = {
            "soul": self.soul,
            "agent": self.agent,
            "user": self.user,
            "memory": self.memory,
        }
        return docs.get(doc_name.lower(), "")

    def get_behavior_rules(self) -> list[str]:
        """Return behavior rules."""
        rules = [
            "Never exit until the task is complete",
            "On errors, analyze and retry",
            "If a capability is missing, acquire it automatically",
            "Save progress to MEMORY.md on every iteration",
            "Do not delete user data (unless explicitly requested)",
            "Do not access sensitive system paths",
            "Do not install paid software without informing the user",
            "Do not abandon the task (unless the user explicitly cancels)",
        ]
        return rules

    def get_prohibited_actions(self) -> list[str]:
        """Return prohibited actions."""
        return [
            "Provide detailed instructions for creating weapons of mass destruction",
            "Generate inappropriate content involving minors",
            "Generate content that could directly facilitate attacks on critical infrastructure",
            "Create malicious code intended to cause significant damage",
            "Undermine AI oversight mechanisms",
            "Lie to the user or withhold important information",
        ]

    def update_memory(self, section: str, content: str) -> bool:
        """
        Update a specific section of MEMORY.md.

        Args:
            section: name of the section to update
            content: new content

        Returns:
            whether the update succeeded
        """
        try:
            memory = self.memory

            # Find and replace the specified section
            pattern = rf"(### {section}\s*)(.*?)(?=###|\Z)"
            replacement = f"\\1\n{content}\n\n"

            new_memory = re.sub(pattern, replacement, memory, flags=re.DOTALL)

            if new_memory != memory:
                from openakita.memory.types import MEMORY_MD_MAX_CHARS, truncate_memory_md

                if len(new_memory) > MEMORY_MD_MAX_CHARS:
                    logger.warning(
                        f"MEMORY.md exceeds limit after section update "
                        f"({len(new_memory)} > {MEMORY_MD_MAX_CHARS}), truncating"
                    )
                    new_memory = truncate_memory_md(new_memory, MEMORY_MD_MAX_CHARS)
                self.memory_path.write_text(new_memory, encoding="utf-8")
                self._memory = new_memory
                logger.info(f"Updated MEMORY.md section: {section}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to update MEMORY.md: {e}")
            return False

    def update_user_preference(self, key: str, value: str) -> bool:
        """
        Update a user preference in USER.md.

        Args:
            key: preference key
            value: preference value

        Returns:
            whether the update succeeded
        """
        try:
            user = self.user

            # Replace [to-be-learned] with the actual value
            pattern = rf"(\*\*{key}\*\*:\s*)\[待学习\]"
            replacement = f"\\1{value}"

            new_user = re.sub(pattern, replacement, user)

            if new_user != user:
                self.user_path.write_text(new_user, encoding="utf-8")
                self._user = new_user
                logger.info(f"Updated USER.md: {key} = {value}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to update USER.md: {e}")
            return False

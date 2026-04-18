"""
Todo state JSON persistence layer

Atomic write + debounce, reusing the project's existing atomic_io utilities:
safe_json_write (.tmp → .bak → replace) / read_json_safe (.bak fallback)

Only persists plans with status == "in_progress"; completed/cancelled ones are cleaned up automatically.
"""

import asyncio
import copy
import logging
from datetime import datetime
from pathlib import Path

from ...utils.atomic_io import read_json_safe, safe_json_write

logger = logging.getLogger(__name__)

__all__ = ["TodoStore"]


class TodoStore:
    """Todo state JSON persistence layer"""

    def __init__(self, store_path: Path | None = None):
        self._path = Path(store_path) if store_path else Path("data/plans/todo_store.json")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._dirty = False
        self._data: dict[str, dict] = {}

    # --- CRUD ---

    def load(self) -> dict[str, dict]:
        """Synchronous load on startup, returns {conversation_id: plan_data}"""
        raw = read_json_safe(self._path)
        if raw is None:
            return {}
        try:
            if isinstance(raw, dict) and "todos" in raw:
                self._data = {
                    k: v
                    for k, v in raw["todos"].items()
                    if isinstance(v, dict) and v.get("status") == "in_progress"
                }
                return dict(self._data)
        except Exception as e:
            logger.warning(f"[TodoStore] Load parse error: {e}")
        return {}

    def upsert(self, conversation_id: str, plan: dict) -> None:
        """Store a deep-copy snapshot rather than a reference, to avoid writing intermediate state during debounce."""
        self._data[conversation_id] = copy.deepcopy(plan)
        self._dirty = True

    def remove(self, conversation_id: str) -> None:
        if conversation_id in self._data:
            del self._data[conversation_id]
            self._dirty = True

    def get(self, conversation_id: str) -> dict | None:
        return self._data.get(conversation_id)

    def get_all_active(self) -> dict[str, dict]:
        return {k: v for k, v in self._data.items() if v.get("status") == "in_progress"}

    # --- Persistence ---

    def save(self) -> bool:
        """Synchronously save to disk (atomic write + .bak backup)"""
        if not self._dirty:
            return True
        payload = {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "todos": {k: v for k, v in self._data.items() if v.get("status") == "in_progress"},
        }
        try:
            safe_json_write(self._path, payload)
            self._dirty = False
            return True
        except Exception as e:
            logger.warning(f"[TodoStore] Save failed: {e}")
            return False

    # --- Message replay recovery (fallback) ---

    def restore_from_messages(self, conversation_id: str, messages: list[dict]) -> dict | None:
        """Modeled after claude-code extractTodosFromTranscript: scan in reverse to find the last create_todo"""
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", [])
            if isinstance(content, str):
                continue
            for block in content if isinstance(content, list) else []:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "create_todo"
                ):
                    tool_input = block.get("input", {})
                    if isinstance(tool_input, dict) and "steps" in tool_input:
                        return self._rebuild_plan_from_create_todo(tool_input)
        return None

    def _rebuild_plan_from_create_todo(self, tool_input: dict) -> dict:
        """Rebuild plan structure from create_todo tool parameters"""
        steps = []
        for i, raw in enumerate(tool_input.get("steps", [])):
            if isinstance(raw, dict):
                steps.append(
                    {
                        "id": raw.get("id", f"step_{i + 1}"),
                        "description": raw.get("description", ""),
                        "status": "pending",
                        "result": "",
                        "started_at": None,
                        "completed_at": None,
                        "depends_on": raw.get("depends_on", []),
                        "skills": raw.get("skills", []),
                    }
                )
        return {
            "id": f"restored_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "task_summary": tool_input.get("task_summary", ""),
            "status": "in_progress",
            "steps": steps,
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "logs": ["(restored from message history)"],
        }

    # --- Debounce loop ---

    async def start_save_loop(self, interval: float = 5.0):
        """Background debounced save loop (driven by asyncio.create_task)"""
        try:
            while True:
                await asyncio.sleep(interval)
                if self._dirty:
                    self.save()
        except asyncio.CancelledError:
            self.save()

    async def flush(self):
        """Persist immediately (called during shutdown)"""
        if self._dirty:
            self.save()

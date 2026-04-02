"""
Todo 状态 JSON 持久化层

原子写 + 防抖，复用项目已有的 atomic_io 工具：
safe_json_write (.tmp → .bak → replace) / read_json_safe (.bak 回退)

仅持久化 status == "in_progress" 的 plan，已完成/取消的自动清理。
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
    """Todo 状态 JSON 持久化层"""

    def __init__(self, store_path: Path | None = None):
        self._path = Path(store_path) if store_path else Path("data/plans/todo_store.json")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._dirty = False
        self._data: dict[str, dict] = {}

    # --- CRUD ---

    def load(self) -> dict[str, dict]:
        """启动时同步加载，返回 {conversation_id: plan_data}"""
        raw = read_json_safe(self._path)
        if raw is None:
            return {}
        try:
            if isinstance(raw, dict) and "todos" in raw:
                self._data = {
                    k: v for k, v in raw["todos"].items()
                    if isinstance(v, dict) and v.get("status") == "in_progress"
                }
                return dict(self._data)
        except Exception as e:
            logger.warning(f"[TodoStore] Load parse error: {e}")
        return {}

    def upsert(self, conversation_id: str, plan: dict) -> None:
        """存 deepcopy 快照而非引用，避免防抖写入中间状态。"""
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

    # --- 持久化 ---

    def save(self) -> bool:
        """同步保存到磁盘（原子写 + .bak 备份）"""
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

    # --- 消息回放恢复（兜底） ---

    def restore_from_messages(self, conversation_id: str, messages: list[dict]) -> dict | None:
        """参考 claude-code extractTodosFromTranscript：倒序找最后一次 create_todo"""
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", [])
            if isinstance(content, str):
                continue
            for block in (content if isinstance(content, list) else []):
                if (isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "create_todo"):
                    tool_input = block.get("input", {})
                    if isinstance(tool_input, dict) and "steps" in tool_input:
                        return self._rebuild_plan_from_create_todo(tool_input)
        return None

    def _rebuild_plan_from_create_todo(self, tool_input: dict) -> dict:
        """从 create_todo 的工具参数重建 plan 结构"""
        steps = []
        for i, raw in enumerate(tool_input.get("steps", [])):
            if isinstance(raw, dict):
                steps.append({
                    "id": raw.get("id", f"step_{i+1}"),
                    "description": raw.get("description", ""),
                    "status": "pending",
                    "result": "", "started_at": None, "completed_at": None,
                    "depends_on": raw.get("depends_on", []),
                    "skills": raw.get("skills", []),
                })
        return {
            "id": f"restored_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "task_summary": tool_input.get("task_summary", ""),
            "status": "in_progress",
            "steps": steps,
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "logs": ["(从消息历史恢复)"],
        }

    # --- 防抖循环 ---

    async def start_save_loop(self, interval: float = 5.0):
        """后台防抖保存循环（由 asyncio.create_task 驱动）"""
        try:
            while True:
                await asyncio.sleep(interval)
                if self._dirty:
                    self.save()
        except asyncio.CancelledError:
            self.save()

    async def flush(self):
        """立即持久化（shutdown 时调用）"""
        if self._dirty:
            self.save()

"""
JSONL 会话持久化

参考 Claude Code 的 sessionStorage.ts 设计:
- 每条消息 append 为一行 JSON
- 加载时支持从 compact 边界开始
- 子 agent 独立 transcript 文件
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRANSCRIPT_DIR = Path("data/transcripts")


def get_transcript_path(session_id: str) -> Path:
    """获取 transcript 文件路径。"""
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    return TRANSCRIPT_DIR / f"{session_id}.jsonl"


def append_entry(session_id: str, entry: dict) -> None:
    """追加一条记录到 transcript。

    原子性: 每次追加完整的一行 JSON + 换行符。
    """
    path = get_transcript_path(session_id)
    entry_with_ts = {
        "_ts": datetime.now().isoformat(),
        **entry,
    }
    line = json.dumps(entry_with_ts, ensure_ascii=False, default=str)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_message(
    session_id: str,
    role: str,
    content: Any,
    *,
    message_id: str = "",
    metadata: dict | None = None,
) -> None:
    """追加一条消息到 transcript。"""
    entry: dict[str, Any] = {
        "type": "message",
        "role": role,
        "content": content if isinstance(content, str) else _serialize_content(content),
    }
    if message_id:
        entry["message_id"] = message_id
    if metadata:
        entry["metadata"] = metadata
    append_entry(session_id, entry)


def append_tool_result(
    session_id: str,
    tool_use_id: str,
    tool_name: str,
    result: str,
    is_error: bool = False,
) -> None:
    """追加一条工具结果到 transcript。"""
    append_entry(session_id, {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "content": result[:5000] if len(result) > 5000 else result,
        "is_error": is_error,
    })


def append_compact_boundary(session_id: str, summary: str = "") -> None:
    """追加压缩边界标记。

    加载时可以从此标记后开始读取，跳过已压缩的前半部分。
    """
    append_entry(session_id, {
        "type": "compact_boundary",
        "summary": summary,
    })


def load_transcript(
    session_id: str,
    *,
    from_compact_boundary: bool = False,
) -> list[dict]:
    """加载 transcript。

    Args:
        session_id: 会话 ID
        from_compact_boundary: 如果为 True，从最后一个 compact_boundary 开始读取

    Returns:
        消息/事件记录列表
    """
    path = get_transcript_path(session_id)
    if not path.exists():
        return []

    entries: list[dict] = []
    last_boundary_idx = -1

    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)
                if entry.get("type") == "compact_boundary":
                    last_boundary_idx = len(entries) - 1
            except json.JSONDecodeError:
                logger.warning("Skipped malformed transcript line %d in %s", i, session_id)

    if from_compact_boundary and last_boundary_idx >= 0:
        return entries[last_boundary_idx + 1:]

    return entries


def transcript_exists(session_id: str) -> bool:
    """检查 transcript 是否存在。"""
    return get_transcript_path(session_id).exists()


def _serialize_content(content: Any) -> Any:
    """序列化消息内容。"""
    if isinstance(content, list):
        result = []
        for block in content:
            if hasattr(block, "to_dict"):
                result.append(block.to_dict())
            elif isinstance(block, dict):
                result.append(block)
            else:
                result.append(str(block))
        return result
    return str(content)

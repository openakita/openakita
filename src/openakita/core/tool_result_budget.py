"""
工具结果预算: 大结果截断与落盘

参考 Claude Code 的工具结果管理:
- 按字符数限制单次工具结果大小
- 超限结果保存到文件，返回引用路径
- 结构化结果 (JSON) 智能截断
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULT_CHARS = 30_000
OVERFLOW_DIR = Path("data/tool-overflow")


def truncate_tool_result(
    result: str,
    *,
    max_chars: int = DEFAULT_MAX_RESULT_CHARS,
    tool_name: str = "",
    session_id: str = "",
) -> str:
    """截断过长的工具结果。

    如果结果超过 max_chars:
    1. 落盘到 data/tool-overflow/
    2. 返回截断版本 + 文件引用

    Args:
        result: 原始结果字符串
        max_chars: 最大字符数
        tool_name: 工具名称（用于日志和文件名）
        session_id: 会话 ID

    Returns:
        可能被截断的结果字符串
    """
    if len(result) <= max_chars:
        return result

    logger.info(
        "Tool result exceeds budget: %s (%d chars > %d limit)",
        tool_name,
        len(result),
        max_chars,
    )

    # Try saving to file
    file_ref = ""
    if session_id:
        file_ref = _save_overflow(result, tool_name, session_id)

    truncated = result[:max_chars]

    # For JSON results, try to preserve structure
    if result.lstrip().startswith(("{", "[")):
        truncated = _smart_json_truncate(result, max_chars)

    suffix = f"\n\n[Truncated: {len(result):,} chars total, showing first {max_chars:,}]"
    if file_ref:
        suffix += f"\n[Full result saved to: {file_ref}]"

    return truncated + suffix


def _smart_json_truncate(result: str, max_chars: int) -> str:
    """JSON 结果智能截断。

    对于 JSON 数组，截断元素数量。
    对于 JSON 对象，截断值的内容。
    """
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, ValueError):
        return result[:max_chars]

    if isinstance(data, list) and len(data) > 10:
        # Keep first N items that fit in budget
        items = []
        current_len = 2  # for []
        for item in data:
            item_str = json.dumps(item, ensure_ascii=False, default=str)
            if current_len + len(item_str) + 2 > max_chars:
                break
            items.append(item)
            current_len += len(item_str) + 2

        return json.dumps(items, indent=2, ensure_ascii=False, default=str)

    return result[:max_chars]


def _save_overflow(result: str, tool_name: str, session_id: str) -> str:
    """将溢出结果保存到文件。"""
    try:
        overflow_dir = OVERFLOW_DIR / session_id
        overflow_dir.mkdir(parents=True, exist_ok=True)

        import time
        filename = f"{tool_name}_{int(time.time())}.txt"
        file_path = overflow_dir / filename
        file_path.write_text(result, encoding="utf-8")

        logger.debug("Overflow result saved to %s", file_path)
        return str(file_path)
    except Exception as e:
        logger.warning("Failed to save overflow result: %s", e)
        return ""

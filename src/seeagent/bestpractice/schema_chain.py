"""SchemaChain — 输出 schema 自动推导。

对第 i 个子任务，其输出 schema = 第 i+1 个子任务的 input_schema。
最后一个子任务的输出 schema = BPConfig.final_output_schema。
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import BestPracticeConfig


class SchemaChain:
    """从下游 input_schema 推导当前子任务的 output_schema。"""

    def derive_output_schema(
        self, bp_config: BestPracticeConfig, subtask_index: int,
    ) -> dict[str, Any] | None:
        """返回子任务 subtask_index 应当输出的 JSON Schema。

        Rules:
        - 最后一个子任务 → final_output_schema (可能为 None)
        - 其他子任务 → 下一个子任务的 input_schema (可能为 {})
        - 空 schema → None (让 SubAgent 自由输出)
        """
        subtasks = bp_config.subtasks
        if not subtasks or subtask_index < 0 or subtask_index >= len(subtasks):
            return None

        if subtask_index >= len(subtasks) - 1:
            return bp_config.final_output_schema or None

        next_schema = subtasks[subtask_index + 1].input_schema
        return next_schema if next_schema else None

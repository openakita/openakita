"""BP 配置加载与验证。"""

from __future__ import annotations

import logging
from typing import Any

import yaml

from .models import (
    BestPracticeConfig,
    RunMode,
    SubtaskConfig,
    TriggerConfig,
    TriggerType,
)

logger = logging.getLogger(__name__)


def load_bp_config(raw: dict[str, Any]) -> BestPracticeConfig:
    """从 YAML dict 构建 BestPracticeConfig。

    兼容: 如果存在 ``best_practice:`` 包装键则自动解包。
    """
    if "best_practice" in raw and "id" not in raw:
        raw = raw["best_practice"]

    triggers = [
        TriggerConfig(
            type=TriggerType(t["type"]),
            pattern=t.get("pattern", ""),
            conditions=t.get("conditions", []),
            cron=t.get("cron", ""),
        )
        for t in raw.get("triggers", [])
    ]

    subtasks = [
        SubtaskConfig(
            id=s["id"],
            name=s["name"],
            agent_profile=s.get("agent_profile", ""),
            input_schema=s.get("input_schema", {}),
            description=s.get("description", ""),
            depends_on=s.get("depends_on", []),
            input_mapping=s.get("input_mapping", {}),
            timeout_seconds=s.get("timeout_seconds"),
            max_retries=s.get("max_retries", 0),
        )
        for s in raw.get("subtasks", [])
    ]

    run_mode_str = raw.get("default_run_mode", "manual")
    run_mode = RunMode(run_mode_str) if run_mode_str in ("manual", "auto") else RunMode.MANUAL

    return BestPracticeConfig(
        id=raw["id"],
        name=raw["name"],
        subtasks=subtasks,
        description=raw.get("description", ""),
        triggers=triggers,
        final_output_schema=raw.get("final_output_schema"),
        default_run_mode=run_mode,
    )


def load_bp_config_from_yaml(yaml_text: str) -> BestPracticeConfig:
    """从 YAML 字符串加载。"""
    raw = yaml.safe_load(yaml_text)
    if not raw:
        raise ValueError("Empty YAML")
    return load_bp_config(raw)


def validate_bp_config(config: BestPracticeConfig) -> list[str]:
    """校验 BP 配置，返回错误列表（空 = 合法）。"""
    errors: list[str] = []

    if not config.id:
        errors.append("id is required")
    if not config.name:
        errors.append("name is required")
    if not config.subtasks:
        errors.append("at least one subtask is required")

    # ID 唯一性
    ids = [s.id for s in config.subtasks]
    if len(ids) != len(set(ids)):
        errors.append("duplicate subtask IDs detected")

    # agent_profile 非空
    for s in config.subtasks:
        if not s.agent_profile:
            errors.append(f"subtask '{s.id}' missing agent_profile")

    # depends_on 引用合法性
    id_set = set(ids)
    for s in config.subtasks:
        for dep in s.depends_on:
            if dep not in id_set:
                errors.append(f"subtask '{s.id}' depends_on unknown '{dep}'")

    # input_mapping 引用检查
    for s in config.subtasks:
        for field_name, upstream_id in s.input_mapping.items():
            if upstream_id not in id_set:
                errors.append(
                    f"subtask '{s.id}' input_mapping['{field_name}'] "
                    f"references unknown upstream '{upstream_id}'"
                )

    # trigger 合法性
    for t in config.triggers:
        if t.type == TriggerType.COMMAND and not t.pattern:
            errors.append("COMMAND trigger requires pattern")
        if t.type == TriggerType.CONTEXT and not t.conditions:
            errors.append("CONTEXT trigger requires conditions")
        if t.type == TriggerType.CRON and not t.cron:
            errors.append("CRON trigger requires cron expression")

    return errors

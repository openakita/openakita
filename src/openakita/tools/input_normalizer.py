"""Schema-driven normalization for tool inputs."""

from __future__ import annotations

import json
import logging
from typing import Any

from .definitions import get_tool_input_schema

logger = logging.getLogger(__name__)


def normalize_tool_input(
    tool_name: str,
    params: Any,
    *,
    schema: dict | None = None,
) -> Any:
    """Normalize a tool input payload using its JSON schema."""
    tool_schema = schema if isinstance(schema, dict) else get_tool_input_schema(tool_name)
    if not tool_schema:
        return params
    return _normalize_value(params, tool_schema, path=tool_name)


def _normalize_value(value: Any, schema: dict | None, *, path: str) -> Any:
    if not isinstance(schema, dict) or not schema:
        return value

    schema_type = _infer_schema_type(schema)
    if schema_type == "object":
        return _normalize_object(value, schema, path=path)
    if schema_type == "array":
        return _normalize_array(value, schema, path=path)
    return value


def _normalize_object(value: Any, schema: dict, *, path: str) -> Any:
    value = _maybe_parse_structured_string(value, expected_type="object", path=path)
    if not isinstance(value, dict):
        return value

    properties = schema.get("properties")
    additional = schema.get("additionalProperties")
    if not isinstance(properties, dict) and not isinstance(additional, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        child_schema = properties.get(key) if isinstance(properties, dict) else None
        if child_schema is None and isinstance(additional, dict):
            child_schema = additional
        normalized[key] = _normalize_value(item, child_schema, path=f"{path}.{key}")
    return normalized


def _normalize_array(value: Any, schema: dict, *, path: str) -> Any:
    value = _maybe_parse_structured_string(value, expected_type="array", path=path)
    if not isinstance(value, list):
        return value

    item_schema = schema.get("items")
    if not isinstance(item_schema, dict):
        return value

    return [
        _normalize_value(item, item_schema, path=f"{path}[{index}]")
        for index, item in enumerate(value)
    ]


def _maybe_parse_structured_string(value: Any, *, expected_type: str, path: str) -> Any:
    if not isinstance(value, str):
        return value

    raw = value.strip()
    if not raw:
        return value

    if expected_type == "object" and not raw.startswith("{"):
        return value
    if expected_type == "array" and not raw.startswith("["):
        return value

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return value

    if expected_type == "object" and isinstance(parsed, dict):
        logger.debug("[ToolInput] Parsed stringified object at %s", path)
        return parsed
    if expected_type == "array" and isinstance(parsed, list):
        logger.debug("[ToolInput] Parsed stringified array at %s", path)
        return parsed
    return value


def _infer_schema_type(schema: dict) -> str | None:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if "object" in schema_type:
            return "object"
        if "array" in schema_type:
            return "array"
        return None
    if isinstance(schema_type, str):
        return schema_type
    if "properties" in schema or isinstance(schema.get("additionalProperties"), dict):
        return "object"
    if "items" in schema:
        return "array"
    return None

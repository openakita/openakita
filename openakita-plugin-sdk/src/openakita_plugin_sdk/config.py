"""Configuration schema helpers for plugin config_schema.json generation."""

from __future__ import annotations

import json
from typing import Any


def config_property(
    name: str,
    prop_type: str = "string",
    description: str = "",
    default: Any = None,
    enum: list[str] | None = None,
    required: bool = False,
) -> dict[str, Any]:
    """Build a single JSON Schema property for plugin configuration.

    Example::

        schema = config_schema(
            "obsidian-kb",
            properties=[
                config_property("vault_path", "string", "Path to Obsidian vault", required=True),
                config_property("index_on_start", "boolean", "Auto-index on load", default=True),
                config_property("max_results", "integer", "Max search results", default=10),
            ],
        )
    """
    prop: dict[str, Any] = {"type": prop_type}
    if description:
        prop["description"] = description
    if default is not None:
        prop["default"] = default
    if enum:
        prop["enum"] = enum
    prop["_required"] = required
    return {name: prop}


def config_schema(
    title: str,
    properties: list[dict[str, Any]],
    description: str = "",
) -> dict[str, Any]:
    """Build a JSON Schema object for ``config_schema.json``.

    Returns a dict suitable for ``json.dump()`` to ``config_schema.json``
    inside the plugin directory.
    """
    merged_props: dict[str, Any] = {}
    required_fields: list[str] = []

    for prop_dict in properties:
        for name, spec in prop_dict.items():
            is_required = spec.pop("_required", False)
            merged_props[name] = spec
            if is_required:
                required_fields.append(name)

    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "title": title,
        "properties": merged_props,
    }
    if description:
        schema["description"] = description
    if required_fields:
        schema["required"] = required_fields

    return schema


def write_config_schema(path: str, schema: dict[str, Any]) -> None:
    """Write a config schema dict to a JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)

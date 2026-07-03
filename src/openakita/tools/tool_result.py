"""Explicit tool result payloads with backend-only metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Generic, TypeAlias, TypeVar, overload

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
ToolMetadata: TypeAlias = dict[str, JsonValue]

T = TypeVar("T")
U = TypeVar("U")


@dataclass(frozen=True, slots=True)
class ToolResultPayload(Generic[T]):
    """LLM-visible content paired with backend-only metadata."""

    content: T
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata or {})))

    def with_content(self, content: U) -> ToolResultPayload[U]:
        return ToolResultPayload(content=content, metadata=dict(self.metadata))


def tool_result_payload(
    content: T,
    *,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ToolResultPayload[T]:
    return ToolResultPayload(content=content, metadata=dict(metadata or {}))


@overload
def visible_tool_content(value: ToolResultPayload[T]) -> T: ...


@overload
def visible_tool_content(value: T) -> T: ...


def visible_tool_content(value):
    """Return the LLM-visible result without backend-only metadata."""
    if isinstance(value, ToolResultPayload):
        return value.content
    return value


@overload
def split_tool_result_payload(value: ToolResultPayload[T]) -> tuple[T, ToolMetadata]: ...


@overload
def split_tool_result_payload(value: T) -> tuple[T, ToolMetadata]: ...


def split_tool_result_payload(value):
    """Return visible content and metadata from an explicit payload value."""
    if isinstance(value, ToolResultPayload):
        return value.content, dict(value.metadata)
    return value, {}


def mutation_effect(
    *,
    action: str,
    target: str,
    status: str = "ok",
    **details: JsonValue,
) -> ToolMetadata:
    """Build a generic mutation effect record."""
    effect = {
        "kind": "tool_effect",
        "action": str(action),
        "target": str(target),
        "status": str(status),
    }
    effect.update({k: v for k, v in details.items() if v is not None})
    return effect


def tool_receipt(
    *,
    action: str,
    target: str,
    status: str = "ok",
    **details: JsonValue,
) -> ToolMetadata:
    """Build a generic tool receipt record."""
    receipt = {
        "kind": "tool_receipt",
        "action": str(action),
        "target": str(target),
        "status": str(status),
    }
    receipt.update({k: v for k, v in details.items() if v is not None})
    return receipt


def iter_tool_result_records(tool_result: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    """Read a list of structured records from ``tool_result.metadata``."""
    metadata = tool_result.get("metadata")
    if not isinstance(metadata, Mapping):
        return []
    records = metadata.get(key)
    if isinstance(records, Mapping):
        records = [records]
    if not isinstance(records, list):
        return []
    return [dict(item) for item in records if isinstance(item, Mapping)]


def iter_tool_result_effects(tool_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return structured effect records from a tool_result dict.

    Both ``effects`` and ``receipts`` are accepted, but only from metadata.
    LLM-visible content is intentionally ignored.
    """
    effects = iter_tool_result_records(tool_result, "effects")
    if effects:
        return effects
    for receipt in iter_tool_result_records(tool_result, "receipts"):
        if receipt.get("kind") == "tool_receipt":
            effects.append(receipt)
    return effects


def successful_tool_effects(tool_results: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tr in tool_results or []:
        if not isinstance(tr, Mapping) or tr.get("is_error"):
            continue
        for effect in iter_tool_result_effects(tr):
            if effect.get("status") == "ok":
                out.append(effect)
    return out


def successful_tool_effect_actions(tool_results: list[dict[str, Any]] | None) -> set[str]:
    return {
        str(effect.get("action") or "")
        for effect in successful_tool_effects(tool_results)
        if effect.get("action")
    }


__all__ = [
    "iter_tool_result_effects",
    "iter_tool_result_records",
    "JsonValue",
    "mutation_effect",
    "split_tool_result_payload",
    "successful_tool_effect_actions",
    "successful_tool_effects",
    "ToolMetadata",
    "tool_receipt",
    "tool_result_payload",
    "ToolResultPayload",
    "visible_tool_content",
]

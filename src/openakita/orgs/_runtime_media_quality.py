"""Deterministic media-quality failures propagated through organization runs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

current_media_quality_failures_var: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "org_current_media_quality_failures",
    default=None,
)


def _failure_store() -> dict[str, dict[str, Any]]:
    failures = current_media_quality_failures_var.get()
    if failures is None:
        failures = {}
        current_media_quality_failures_var.set(failures)
    return failures


def _as_mapping(result: Any) -> Mapping[str, Any] | None:
    if isinstance(result, Mapping):
        return result
    if not isinstance(result, str):
        return None
    try:
        decoded = json.loads(result)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _quality_key(payload: Mapping[str, Any], tool_input: Mapping[str, Any]) -> str:
    failure = payload.get("quality_failure")
    failure_map = failure if isinstance(failure, Mapping) else {}
    segment_id = str(
        failure_map.get("segment_id")
        or payload.get("segment_id")
        or tool_input.get("segment_id")
        or ""
    ).strip()
    if segment_id:
        return f"segment:{segment_id}"
    task_id = str(payload.get("task_id") or tool_input.get("task_id") or "").strip()
    if task_id:
        return f"task:{task_id}"
    return "media:unscoped"


def observe_media_quality_result(
    *,
    tool_name: str,
    tool_input: Mapping[str, Any],
    result: Any,
) -> dict[str, Any] | None:
    """Record or resolve a media validation result in the current node run."""

    payload = _as_mapping(result)
    if payload is None:
        return None
    failures = _failure_store()
    failure_raw = payload.get("quality_failure")
    if isinstance(failure_raw, Mapping):
        failure = dict(failure_raw)
        failure.setdefault("message", str(payload.get("error") or "媒体规格校验失败"))
        failure.setdefault("code", "media_validation_failed")
        failure["tool_name"] = tool_name
        failure["reworkable"] = bool(payload.get("reworkable", True))
        failures[_quality_key(payload, tool_input)] = failure
        return failure

    validation = payload.get("validation")
    if isinstance(validation, Mapping) and validation.get("passed") is True:
        failures.pop(_quality_key(payload, tool_input), None)
    return None


def record_media_quality_failure(failure: Mapping[str, Any]) -> None:
    """Propagate an exhausted child failure into its caller's run context."""

    payload = dict(failure)
    key = str(payload.get("segment_id") or payload.get("task_id") or "unscoped")
    _failure_store()[f"propagated:{key}"] = payload


def current_media_quality_failures() -> list[dict[str, Any]]:
    return [dict(failure) for failure in _failure_store().values()]


def format_media_quality_reason(failures: list[Mapping[str, Any]]) -> str:
    messages = [str(failure.get("message") or "媒体规格校验失败") for failure in failures]
    return "确定性媒体校验未通过：" + "；".join(dict.fromkeys(messages))


__all__ = [
    "current_media_quality_failures",
    "current_media_quality_failures_var",
    "format_media_quality_reason",
    "observe_media_quality_result",
    "record_media_quality_failure",
]

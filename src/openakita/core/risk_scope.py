"""RiskGate scope extraction and matching helpers.

Tool handlers declare policy metadata; this module applies that metadata to
both policy-time scope construction and execution-time authorization checks.
It intentionally avoids concrete tool names.
"""

from __future__ import annotations

import re
from typing import Any

_SIMPLE_TYPES = (str, int, float, bool)
_MARKER_RE = re.compile(r"\b[A-Z0-9_]{8,}\b", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_IDENTIFIER_HINT_RE = re.compile(r"[0-9_./:@-]")


def coerce_scope_keys(raw: Any) -> tuple[str, ...]:
    """Normalize a policy key declaration to a tuple of non-empty strings."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,) if raw else ()
    if isinstance(raw, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in raw if str(item))
    return ()


def extract_tool_scope(params: dict[str, Any] | None, policy: Any) -> dict[str, Any]:
    """Build a compact RiskGate scope from tool params using a ToolPolicy-like object."""
    safe_params = params or {}
    keys = coerce_scope_keys(getattr(policy, "riskgate_scope_params", ()))
    if not keys:
        return {}

    out: dict[str, Any] = {}
    for key in keys:
        if key not in safe_params:
            continue
        value = safe_params.get(key)
        compact = _compact_scope_value(value)
        if compact is not None:
            out[key] = compact
    return out


def tool_policy_is_preview_call(params: dict[str, Any] | None, policy: Any) -> bool:
    """Return whether params select the policy's non-mutating preview path."""
    preview_param = str(getattr(policy, "preview_param", "") or "")
    if not preview_param:
        return False
    safe_params = params or {}
    raw_preview = safe_params.get(preview_param, getattr(policy, "preview_default", None))
    return _coerce_bool(raw_preview, default=bool(getattr(policy, "preview_default", False)))


def tool_policy_requires_riskgate_commit(params: dict[str, Any] | None, policy: Any) -> bool:
    """Return whether this concrete tool call is a RiskGate-protected commit."""
    if policy is None or not bool(getattr(policy, "commit_requires_riskgate", False)):
        return False
    return not tool_policy_is_preview_call(params, policy)


def authorization_covers_tool_call(
    authorization: Any,
    *,
    tool_name: str,
    tool_input: dict[str, Any] | None,
    policy: Any,
) -> bool:
    """Return whether a RiskGate authorization covers a concrete tool commit."""
    intent = getattr(authorization, "authorized_intent", None)
    if not isinstance(intent, dict):
        return False

    operation = str(intent.get("operation") or "").strip()
    required_operation = str(getattr(policy, "riskgate_operation", "") or "").strip()
    if required_operation and operation != required_operation:
        return False

    allowed_tools = _coerce_allowed_tools(intent.get("tool_names") or intent.get("allowed_tools"))
    if allowed_tools and tool_name not in allowed_tools:
        return False

    scope = intent.get("scope") or {}
    if not isinstance(scope, dict):
        return False

    requested = extract_tool_scope(tool_input, policy)
    scope_keys = coerce_scope_keys(getattr(policy, "riskgate_scope_params", ()))
    required_any = (
        coerce_scope_keys(getattr(policy, "riskgate_scope_required_any", ())) or scope_keys
    )
    if required_any and not any(_has_value(requested.get(key)) for key in required_any):
        return False

    exact_keys = set(coerce_scope_keys(getattr(policy, "riskgate_scope_exact_params", ())))
    text_keys = set(coerce_scope_keys(getattr(policy, "riskgate_scope_text_params", ())))
    raw_keys = coerce_scope_keys(getattr(policy, "riskgate_scope_raw_params", ())) or ("raw",)

    matched = False
    for key in scope_keys:
        requested_value = requested.get(key)
        authorized_value = scope.get(key)
        if not _has_value(authorized_value):
            if _has_value(requested_value) and key in text_keys:
                if _raw_scope_matches(scope, raw_keys, requested_value):
                    matched = True
            continue
        if not _has_value(requested_value):
            return False
        if key in text_keys:
            if _text_scope_matches(authorized_value, requested_value):
                matched = True
                continue
            if _raw_scope_matches(scope, raw_keys, requested_value):
                matched = True
                continue
            return False
        if _any_value_equal(authorized_value, requested_value):
            matched = True
            continue
        if key in exact_keys:
            return False

    return matched


def _compact_scope_value(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, str):
        compact = value.strip()
        return compact if compact else None
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        compact = [item for item in value if isinstance(item, _SIMPLE_TYPES)]
        return compact[:20] if compact else None
    if isinstance(value, dict):
        compact_dict = {
            str(key): item
            for key, item in value.items()
            if isinstance(key, str) and isinstance(item, _SIMPLE_TYPES)
        }
        return compact_dict or None
    return None


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return bool(value)


def _coerce_allowed_tools(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        return (raw,) if raw else ()
    if isinstance(raw, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in raw if str(item))
    return ()


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return bool(value)
    return True


def _iter_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_values(item)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _iter_values(item)
        return
    if isinstance(value, _SIMPLE_TYPES):
        yield value


def _normalize_value(value: Any) -> str:
    return " ".join(str(value).strip().casefold().split())


def _is_specific_text_scope(value: str) -> bool:
    """Return whether a text scope is specific enough for containment checks."""
    normalized = _normalize_value(value)
    if not normalized:
        return False
    if _MARKER_RE.search(normalized):
        return True
    if _IDENTIFIER_HINT_RE.search(normalized) and len(normalized) >= 4:
        return True
    if _CJK_RE.search(normalized):
        return len(normalized) >= 8
    # Avoid authorizing broad natural-language nouns like "memory" or "file"
    # just because they appear in the original request text.
    return len(normalized) >= 12


def _any_value_equal(left: Any, right: Any) -> bool:
    right_values = [_normalize_value(item) for item in _iter_values(right)]
    if not right_values:
        return False
    for left_item in _iter_values(left):
        left_text = _normalize_value(left_item)
        if any(left_text == right_text for right_text in right_values):
            return True
    return False


def _text_scope_matches(authorized: Any, requested: Any) -> bool:
    requested_values = [_normalize_value(item) for item in _iter_values(requested)]
    if not requested_values:
        return False
    for auth_item in _iter_values(authorized):
        auth_text = _normalize_value(auth_item)
        if not auth_text:
            continue
        for req_text in requested_values:
            if not req_text:
                continue
            if req_text == auth_text:
                return True
            if _is_specific_text_scope(req_text) and req_text in auth_text:
                return True
            if _is_specific_text_scope(auth_text) and auth_text in req_text:
                return True
    return False


def _raw_scope_matches(scope: dict[str, Any], raw_keys: tuple[str, ...], requested: Any) -> bool:
    requested_values = [_normalize_value(item) for item in _iter_values(requested)]
    if not requested_values:
        return False
    for key in raw_keys:
        raw_value = scope.get(key)
        for raw_item in _iter_values(raw_value):
            raw_text = _normalize_value(raw_item)
            if not raw_text:
                continue
            if any(
                req_text and _is_specific_text_scope(req_text) and req_text in raw_text
                for req_text in requested_values
            ):
                return True
    return False

"""Session/workspace trusted-path policy (Fix-11).

Reduces risk-gate friction for two common, low-risk situations without
loosening the high-risk path itself:

1. **Built-in trusted patterns** — the user explicitly created a scratch
   directory inside the current workspace (e.g. ``qa_test_2026_05_02``,
   ``workspaces/<ws>/scratch/...``, ``/tmp/...``). Edits/deletes inside these
   paths surface every time as MEDIUM-risk confirmations even though the
   user is the one who told us to create them. We treat such single-message
   intents as already authorised.

2. **Session-scoped manual grant** — the ``ask_user`` confirmation popup
   exposes a "本次会话内 workspace 内的同类操作不再询问" checkbox. The
   backend persists the choice in session metadata so subsequent in-session
   file ops in the same workspace skip the gate.

Design constraints (intentionally conservative):

- NEVER demote ``RiskLevel.HIGH`` (sensitive targets like death-switch /
  security policy / shell hard verbs still require confirmation).
- NEVER grant cross-session authority — every grant is scoped to the
  session metadata and to either an operation kind or a path pattern.
- NEVER auto-extend the grant; expiry is opt-in by the caller.
"""

from __future__ import annotations

import re
import time
from typing import Any

# Well-known sub-directories created by the user as scratch / QA areas.
# Matched case-insensitively against the raw user message.
_TRUSTED_PATH_RE = re.compile(
    r"(qa_test[_/-]\d{4}[_/-]\d{2}[_/-]\d{2}|"
    r"workspaces?[/\\][\w\-./]*?(qa_test|scratch|tmp|sandbox|playground)[\w\-./]*|"
    r"[/\\]tmp[/\\][\w\-./]+|"
    r"workspace[/\\][\w\-./]+)",
    re.IGNORECASE,
)

# Paths we DO NOT trust automatically — sensitive system locations.  Listed
# explicitly so a stray match in ``_TRUSTED_PATH_RE`` cannot escalate them.
_PROTECTED_PATH_RE = re.compile(
    r"(identity[/\\]|data[/\\]security|\.ssh|/etc/|/sys/|"
    r"\\windows\\system32|policies\.yaml|secrets[/\\.])",
    re.IGNORECASE,
)

SESSION_KEY = "trusted_path_overrides"


def is_trusted_workspace_path(message: str) -> bool:
    """Return ``True`` when the message references a built-in trusted path
    AND does not also touch a protected location.

    The check is intentionally biased toward false-negatives: when in doubt
    we surface the risk-confirmation as before.
    """
    if not message:
        return False
    if _PROTECTED_PATH_RE.search(message):
        return False
    return bool(_TRUSTED_PATH_RE.search(message))


def get_session_overrides(session: Any) -> dict[str, Any]:
    if session is None:
        return {}
    try:
        data = session.get_metadata(SESSION_KEY)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def grant_session_trust(
    session: Any,
    *,
    operation: str | None = None,
    path_pattern: str | None = None,
    expires_at: float | None = None,
) -> None:
    """Persist the user's "本次会话内同类操作不再询问" choice.

    The caller can scope the grant by ``operation`` (matched against
    :class:`OperationKind`'s value, e.g. ``"delete"``, ``"write"``) and/or
    a regex string ``path_pattern`` (matched against the message body).

    ``expires_at`` is an absolute epoch-second timestamp. ``None`` means
    "valid for the lifetime of the session" (still scoped — when the
    session ends, the metadata goes with it).
    """
    if session is None:
        return
    overrides = get_session_overrides(session)
    rule = {
        "operation": (operation or "").lower() or None,
        "path_pattern": path_pattern,
        "expires_at": expires_at,
        "granted_at": time.time(),
    }
    overrides.setdefault("rules", []).append(rule)
    try:
        session.set_metadata(SESSION_KEY, overrides)
    except Exception:
        pass


def consume_session_trust(
    session: Any,
    *,
    message: str,
    operation: str | None,
) -> bool:
    """Check whether the user's prior in-session grant covers this request.

    Returns ``True`` when at least one *non-expired* matching grant exists.
    Unlike ``risk_authorized_replay`` (single-use replay sentinel) the
    matching trust grant itself is **not** consumed — that is the whole
    point of the "本次会话内不再询问" checkbox. We do, however, garbage-
    collect grants that have already expired or whose ``expires_at`` is
    malformed so the session metadata cannot grow without bound (C8 §2.4
    fix; previously we just ``continue``-d past expired rules and they
    accumulated forever in long-lived IM sessions).

    A sticky grant intentionally does not extend across processes: if the
    session is rebuilt from disk the metadata travels with it; if the
    user starts a new conversation they will be asked again.
    """
    overrides = get_session_overrides(session)
    rules = overrides.get("rules") or []
    if not rules:
        return False

    text_lower = (message or "").lower()
    op = (operation or "").lower()
    now = time.time()

    matched = False
    surviving_rules: list[dict[str, Any]] = []
    pruned = 0

    for rule in rules:
        expires_at = rule.get("expires_at")
        if expires_at is not None:
            try:
                if float(expires_at) < now:
                    pruned += 1
                    continue
            except (TypeError, ValueError):
                pruned += 1
                continue

        surviving_rules.append(rule)

        if matched:
            continue

        rule_op = (rule.get("operation") or "").lower()
        if rule_op and rule_op != op:
            continue

        pattern = rule.get("path_pattern")
        if pattern:
            try:
                if not re.search(pattern, text_lower, re.IGNORECASE):
                    continue
            except re.error:
                continue

        matched = True

    if pruned:
        overrides["rules"] = surviving_rules
        try:
            session.set_metadata(SESSION_KEY, overrides)
        except Exception:
            pass

    return matched


def clear_session_trust(session: Any) -> None:
    """Remove all session-level trust grants (used by tests / reset path)."""
    if session is None:
        return
    try:
        session.set_metadata(SESSION_KEY, None)
    except Exception:
        pass

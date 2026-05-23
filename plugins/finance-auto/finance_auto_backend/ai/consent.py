"""Consent checker hook + dialog channel (v0.2 Part 2 §4).

Public entry point :func:`check_consent` is called by every AI scenario
*before* it sends a payload to the LLM.  The checker:

1. Reads the ``ai_scenarios`` row to honour any user override (disable /
   force a different sensitivity).
2. Looks up an active permanent grant in ``ai_consent``; if found,
   short-circuits to ``ConsentResult(allowed=True, ...)``.
3. Otherwise emits an ``ai_consent_request`` event over the bus (which
   the WebSocket fan-out forwards to the React side as a dialog) and
   waits for the matching ``POST /ai/consent/respond`` to resolve a
   future.  Default timeout is 30 seconds — configurable per call.
4. Persists the user's decision in ``ai_consent`` (allow_once rows
   carry a 60-second TTL via ``revoked_at = granted_at + 60s``;
   allow_permanent leaves ``revoked_at`` NULL).

A :class:`ConsentDenied` exception is raised when the user picks
``deny`` (or the dialog times out without a response).  The caller is
expected to catch it, write a ``denied`` row to ``llm_call_audit``, and
return a friendly error to the UI.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from .desensitizer import (
    SensitivityLevel,
    preview_desensitization,
)
from .event_bus import get_event_bus
from .models import ConsentDecision, ConsentRequestEvent

if TYPE_CHECKING:
    from ..routes import FinanceAutoService

logger = logging.getLogger(__name__)

DEFAULT_DIALOG_TIMEOUT_SECONDS = 30.0
ALLOW_ONCE_TTL_SECONDS = 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ConsentResult:
    """Returned by :func:`check_consent` on success.

    ``id`` is the ``ai_consent.consent_id`` row that authorises the call.
    ``decision`` echoes the stored decision so the audit log can record
    which path the user took.  ``skip_desensitize`` only flips to True
    for local-LLM raw scenarios where the user explicitly asks to send
    the original payload.
    """

    allowed: bool
    consent_id: int | None = None
    decision: ConsentDecision | None = None
    skip_desensitize: bool = False
    reason: str = ""


class ConsentDenied(Exception):
    """Raised by :func:`check_consent` when the user denies / times out."""

    def __init__(self, scenario_id: str, level: SensitivityLevel, reason: str = ""):
        super().__init__(
            f"AI consent denied for scenario={scenario_id} level={level}: {reason}"
        )
        self.scenario_id = scenario_id
        self.level = level
        self.reason = reason


# ---------------------------------------------------------------------------
# Pending dialog registry
# ---------------------------------------------------------------------------


@dataclass
class _PendingDialog:
    dialog_id: str
    scenario_id: str
    sensitivity_level: SensitivityLevel
    user_id: str
    future: asyncio.Future = field(repr=False)
    requested_at: datetime = field(default_factory=_utcnow)


class _DialogRegistry:
    """Global registry of currently-open consent dialogs.

    The WS endpoint emits the request, the REST endpoint resolves it.
    Keeping the registry process-local is fine for v0.2's single-tenant
    desktop deployment.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, _PendingDialog] = {}
        self._lock = asyncio.Lock()

    async def open(
        self,
        *,
        scenario_id: str,
        level: SensitivityLevel,
        user_id: str,
    ) -> _PendingDialog:
        async with self._lock:
            dialog_id = "dlg_" + secrets.token_hex(8)
            loop = asyncio.get_running_loop()
            entry = _PendingDialog(
                dialog_id=dialog_id,
                scenario_id=scenario_id,
                sensitivity_level=level,
                user_id=user_id,
                future=loop.create_future(),
            )
            self._by_id[dialog_id] = entry
            return entry

    async def resolve(self, dialog_id: str, payload: dict) -> bool:
        async with self._lock:
            entry = self._by_id.pop(dialog_id, None)
        if entry is None:
            return False
        if not entry.future.done():
            entry.future.set_result(payload)
        return True

    async def close(self, dialog_id: str) -> None:
        async with self._lock:
            self._by_id.pop(dialog_id, None)


_dialogs: _DialogRegistry | None = None


def get_dialog_registry() -> _DialogRegistry:
    global _dialogs
    if _dialogs is None:
        _dialogs = _DialogRegistry()
    return _dialogs


def reset_dialog_registry_for_tests() -> _DialogRegistry:
    global _dialogs
    _dialogs = _DialogRegistry()
    return _dialogs


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _read_active_permanent_grant(
    service: FinanceAutoService,
    *,
    user_id: str,
    scenario_id: str,
    level: SensitivityLevel,
) -> tuple[int, bool] | None:
    """Return ``(consent_id, skip_desensitize)`` if an active permanent
    grant exists, else ``None``.  Also short-circuits ``allow_once`` rows
    that haven't yet hit their TTL.
    """
    async with service.db.conn.execute(
        "SELECT consent_id, decision, skip_desensitize, granted_at, revoked_at "
        "FROM ai_consent WHERE user_id=? AND scenario_id=? AND sensitivity_level=? "
        "AND revoked_at IS NULL "
        "AND decision IN ('allow_once', 'allow_permanent') "
        "ORDER BY granted_at DESC",
        (user_id, scenario_id, level),
    ) as cur:
        rows = await cur.fetchall()
    for row in rows:
        if row["decision"] == "allow_permanent":
            return int(row["consent_id"]), bool(row["skip_desensitize"])
        # allow_once still good?  We keep allow_once rows around with
        # ``revoked_at`` set explicitly when they expire; if the column
        # is NULL we treat any allow_once <= 60s old as live.
        try:
            granted = datetime.fromisoformat(row["granted_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        if (_utcnow() - granted).total_seconds() <= ALLOW_ONCE_TTL_SECONDS:
            return int(row["consent_id"]), bool(row["skip_desensitize"])
    return None


async def _persist_consent(
    service: FinanceAutoService,
    *,
    user_id: str,
    scenario_id: str,
    level: SensitivityLevel,
    decision: ConsentDecision,
    source_dialog_id: str | None,
    skip_desensitize: bool,
) -> int:
    """Insert an ``ai_consent`` row.  ``allow_once`` rows are written
    with their ``revoked_at`` already populated (granted_at + 60s) so a
    recovered process never re-uses them.  ``deny`` rows are written too
    — they show up in the "AI 授权" page so the user can see they did
    decline, and they help the audit log explain why a call was blocked.
    """
    granted_at = _utcnow()
    revoked_at: datetime | None = None
    if decision == "allow_once":
        revoked_at = granted_at + timedelta(seconds=ALLOW_ONCE_TTL_SECONDS)
    elif decision == "deny":
        revoked_at = granted_at  # deny rows are not active grants.
    await service.db.conn.execute(
        "INSERT INTO ai_consent(user_id, scenario_id, sensitivity_level, "
        "decision, granted_at, revoked_at, source_dialog_id, "
        "skip_desensitize) VALUES (?,?,?,?,?,?,?,?)",
        (
            user_id,
            scenario_id,
            level,
            decision,
            granted_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            (revoked_at.strftime("%Y-%m-%dT%H:%M:%SZ") if revoked_at else None),
            source_dialog_id,
            int(bool(skip_desensitize)),
        ),
    )
    await service.db.conn.commit()
    async with service.db.conn.execute(
        "SELECT consent_id FROM ai_consent ORDER BY consent_id DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    return int(row["consent_id"]) if row else 0


async def _read_scenario_overrides(
    service: FinanceAutoService, scenario_id: str
) -> tuple[bool, SensitivityLevel | None] | None:
    """Return ``(enabled, sensitivity_override)`` if the row exists."""
    async with service.db.conn.execute(
        "SELECT default_enabled, enabled_override, default_sensitivity, "
        "sensitivity_override FROM ai_scenarios WHERE scenario_id=?",
        (scenario_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    enabled = (
        bool(row["enabled_override"])
        if row["enabled_override"] is not None
        else bool(row["default_enabled"])
    )
    return enabled, (row["sensitivity_override"] or None)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def check_consent(
    service: FinanceAutoService,
    *,
    scenario_id: str,
    level: SensitivityLevel,
    payload: dict | list,
    user_id: str = "local",
    timeout: float = DEFAULT_DIALOG_TIMEOUT_SECONDS,
    model_provider: str = "",
    model_name: str = "",
    is_local_endpoint: bool = False,
    auto_decision: ConsentDecision | None = None,
) -> ConsentResult:
    """Authorise an LLM call for ``(scenario_id, level)``.

    ``auto_decision`` is for the acceptance script + tests so they can
    bypass the dialog entirely; it is *not* exposed by any API endpoint.
    """
    overrides = await _read_scenario_overrides(service, scenario_id)
    if overrides is not None:
        enabled, override_level = overrides
        if not enabled:
            raise ConsentDenied(
                scenario_id, level, "scenario disabled by user override"
            )
        if override_level is not None:
            level = override_level  # honour the override.

    grant = await _read_active_permanent_grant(
        service,
        user_id=user_id,
        scenario_id=scenario_id,
        level=level,
    )
    if grant is not None:
        consent_id, skip_desens = grant
        return ConsentResult(
            allowed=True,
            consent_id=consent_id,
            decision="allow_permanent",
            skip_desensitize=skip_desens,
            reason="prior_grant",
        )

    if auto_decision is not None:
        decision = auto_decision
        dialog_id = "auto_" + secrets.token_hex(4)
        if decision == "deny":
            cid = await _persist_consent(
                service,
                user_id=user_id,
                scenario_id=scenario_id,
                level=level,
                decision="deny",
                source_dialog_id=dialog_id,
                skip_desensitize=False,
            )
            await get_event_bus().emit(
                "finance.ai.consent.denied",
                {
                    "consent_id": cid,
                    "scenario_id": scenario_id,
                    "sensitivity_level": level,
                },
            )
            raise ConsentDenied(scenario_id, level, "auto_deny")
        cid = await _persist_consent(
            service,
            user_id=user_id,
            scenario_id=scenario_id,
            level=level,
            decision=decision,
            source_dialog_id=dialog_id,
            skip_desensitize=False,
        )
        await get_event_bus().emit(
            "finance.ai.consent.granted",
            {
                "consent_id": cid,
                "scenario_id": scenario_id,
                "sensitivity_level": level,
                "decision": decision,
            },
        )
        return ConsentResult(
            allowed=True,
            consent_id=cid,
            decision=decision,
            skip_desensitize=False,
            reason="auto",
        )

    registry = get_dialog_registry()
    entry = await registry.open(
        scenario_id=scenario_id, level=level, user_id=user_id
    )
    preview = preview_desensitization(payload, level)
    estimate = max(64, len(preview) // 4)  # rough char→token approximation.

    request_event = ConsentRequestEvent.now(
        dialog_id=entry.dialog_id,
        scenario_id=scenario_id,
        sensitivity_level=level,
        preview_payload=preview,
        model_provider=model_provider,
        model_name=model_name,
        is_local_endpoint=is_local_endpoint,
        estimated_tokens=estimate,
    )
    await get_event_bus().emit(
        "finance.ai.consent.requested", request_event.model_dump()
    )

    try:
        payload_resp: dict = await asyncio.wait_for(entry.future, timeout=timeout)
    except asyncio.TimeoutError as exc:
        await registry.close(entry.dialog_id)
        # Time-outs are recorded as `deny` so the audit log can show
        # the user "you let it lapse"; downstream still raises.
        await _persist_consent(
            service,
            user_id=user_id,
            scenario_id=scenario_id,
            level=level,
            decision="deny",
            source_dialog_id=entry.dialog_id,
            skip_desensitize=False,
        )
        await get_event_bus().emit(
            "finance.ai.consent.denied",
            {
                "scenario_id": scenario_id,
                "sensitivity_level": level,
                "reason": "timeout",
            },
        )
        raise ConsentDenied(scenario_id, level, "dialog timed out") from exc

    decision = payload_resp.get("decision", "deny")
    skip_desens = bool(payload_resp.get("skip_desensitize", False))
    if decision not in ("deny", "allow_once", "allow_permanent"):
        decision = "deny"

    if decision == "deny":
        await _persist_consent(
            service,
            user_id=user_id,
            scenario_id=scenario_id,
            level=level,
            decision="deny",
            source_dialog_id=entry.dialog_id,
            skip_desensitize=False,
        )
        await get_event_bus().emit(
            "finance.ai.consent.denied",
            {
                "scenario_id": scenario_id,
                "sensitivity_level": level,
                "dialog_id": entry.dialog_id,
            },
        )
        raise ConsentDenied(scenario_id, level, "user clicked deny")

    cid = await _persist_consent(
        service,
        user_id=user_id,
        scenario_id=scenario_id,
        level=level,
        decision=decision,
        source_dialog_id=entry.dialog_id,
        skip_desensitize=skip_desens,
    )
    await get_event_bus().emit(
        "finance.ai.consent.granted",
        {
            "consent_id": cid,
            "scenario_id": scenario_id,
            "sensitivity_level": level,
            "decision": decision,
            "dialog_id": entry.dialog_id,
        },
    )
    return ConsentResult(
        allowed=True,
        consent_id=cid,
        decision=decision,
        skip_desensitize=skip_desens,
        reason="user_dialog",
    )


__all__ = [
    "ALLOW_ONCE_TTL_SECONDS",
    "DEFAULT_DIALOG_TIMEOUT_SECONDS",
    "ConsentDenied",
    "ConsentResult",
    "check_consent",
    "get_dialog_registry",
    "reset_dialog_registry_for_tests",
]

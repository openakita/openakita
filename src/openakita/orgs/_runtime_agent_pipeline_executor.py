"""``_runtime_agent_pipeline_executor.py`` -- v2 OrgRuntime activate-and-run executor.

Companion to :mod:`_runtime_agent_pipeline` (split out in
P-RC-10 P10.5a per ADR-0014). Owns :class:`AgentPipelineExecutor`
(plus the ``_QUOTA_AUTH_HINTS`` table, the
``_AgentRunCallable`` Protocol and ``_looks_like_quota_or_auth_error``
string-sniff). The companion shard owns :class:`AgentCache` /
:class:`ProfileResolver` / ``ORG_STATE_PAUSED``; this file imports
them as a one-way dependency, and the companion re-exports
:class:`AgentPipelineExecutor` so the
``from openakita.orgs._runtime_agent_pipeline import
AgentPipelineExecutor`` import path keeps resolving byte-for-byte.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from ._runtime_agent_pipeline import ORG_STATE_PAUSED
from .command_service import OrgLookupProtocol

if TYPE_CHECKING:
    from ._runtime_agent_pipeline import AgentCache, ProfileResolver

_LOGGER = logging.getLogger(__name__)


_QUOTA_AUTH_HINTS: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "quota",
    "billing",
    "insufficient",
    "exhausted",
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "invalid api key",
    "invalid_api_key",
    "permission_denied",
)


class _AgentRunCallable(Protocol):
    """Minimal callable contract the cached agent must satisfy.

    v2 stays decoupled from concrete Agent / Brain types: the
    executor only calls ``await agent.run(content)`` and
    expects a string-coercible response. Concrete agents
    (e.g. ``openakita.core.agent.Agent``) already match.
    """

    async def run(self, content: str) -> Any: ...


def _looks_like_quota_or_auth_error(exc: BaseException) -> bool:
    """Best-effort string-sniff of an LLM exception (v1 parity).

    v1 ``_is_quota_auth_error`` walks the exception chain
    and a couple of attributes; v2 just probes the message
    + ``status_code`` attr. Good enough for the executor''s
    pause-org branch; the parity test fixture covers known
    Anthropic / OpenAI message shapes.
    """

    parts: list[str] = []
    cur: BaseException | None = exc
    while cur is not None:
        parts.append(str(cur))
        parts.append(type(cur).__name__)
        sc = getattr(cur, "status_code", None) or getattr(cur, "status", None)
        if sc is not None:
            parts.append(str(sc))
        cur = cur.__cause__ or cur.__context__
    blob = " ".join(parts).lower()
    return any(h in blob for h in _QUOTA_AUTH_HINTS)


class AgentPipelineExecutor:
    """v2 message-to-agent-run executor (P9.6f2).

    Replaces v1 ``_activate_and_run`` (24 LOC) +
    ``_activate_and_run_inner`` (556 LOC) +
    ``_run_agent_task`` (110 LOC) + ``_emit_llm_usage``
    (23 LOC) + ``_pause_org_for_quota`` (78 LOC) +
    ``_is_quota_auth_error`` (11 LOC). v1 ~800 LOC ->
    v2 ~180 LOC.

    DI:

    * ``cache`` -- :class:`AgentCache` (P9.6f1).
    * ``resolver`` -- :class:`ProfileResolver` (P9.6f1).
    * ``lookup`` -- :class:`OrgLookupProtocol` for org-state
      probing (the v1 ``ORG_STATE_PAUSED`` gate).
    * ``event_bus`` -- :class:`EventBusProtocol` for
      ``agent_run_started`` / ``agent_run_finished`` /
      ``agent_run_failed`` / ``org_paused_quota`` /
      ``llm_usage`` events.
    * ``on_org_paused`` -- optional sync callback the
      runtime composition root wires to flip the org-state
      machine (P9.6d :class:`OrgLifecycleManager.pause_org`)
      when quota / auth errors are detected. Signature:
      ``(org_id, reason) -> None``.
    """

    def __init__(
        self,
        *,
        cache: AgentCache,
        resolver: ProfileResolver,
        lookup: OrgLookupProtocol,
        event_bus: Any,
        on_org_paused: Any = None,
    ) -> None:
        self._cache = cache
        self._resolver = resolver
        self._lookup = lookup
        self._bus = event_bus
        self._on_org_paused = on_org_paused

    async def activate_and_run(
        self,
        *,
        org_id: str,
        node_id: str,
        content: str,
        command_id: str | None = None,
        role: str | None = None,
        persona: str | None = None,
        unattended: bool = False,
    ) -> dict[str, Any]:
        """v1 ``_activate_and_run`` + ``_activate_and_run_inner`` parity.

        Returns a v1-shaped dict:
            {"status": "ok" | "skipped" | "paused" | "error",
             "command_id": str | None,
             "output": str | None,
             "reason": str | None}
        """

        org = self._lookup.get_org(org_id)
        if org is None:
            return self._result("error", command_id, reason="org_not_found")
        # v1 parity: skip if the org is paused (quota / manual).
        state = getattr(org, "state", None) or getattr(org, "status", None)
        if state == ORG_STATE_PAUSED:
            return self._result("skipped", command_id, reason="org_paused")
        spec = self._resolver.resolve(
            org_id=org_id,
            node_id=node_id,
            role=role,
            persona=persona,
            unattended=unattended,
        )
        if spec is None:
            return self._result("error", command_id, reason="profile_unresolved")
        await self._emit(
            "agent_run_started",
            {"org_id": org_id, "node_id": node_id, "command_id": command_id},
        )
        try:
            agent = self._cache.get_or_create(spec)
        except Exception as exc:  # noqa: BLE001 (v1 parity: never crash dispatch)
            _LOGGER.exception("AgentCache.get_or_create raised (org=%s node=%s)", org_id, node_id)
            await self._emit(
                "agent_run_failed",
                {
                    "org_id": org_id,
                    "node_id": node_id,
                    "command_id": command_id,
                    "reason": "agent_build_failed",
                    "error": str(exc),
                },
            )
            return self._result("error", command_id, reason="agent_build_failed")
        try:
            output = await self._invoke_agent(agent, content)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("agent.run raised (org=%s node=%s)", org_id, node_id)
            if _looks_like_quota_or_auth_error(exc):
                await self.pause_org_for_quota(org_id, reason=str(exc))
                return self._result("paused", command_id, reason="quota_auth")
            await self._emit(
                "agent_run_failed",
                {
                    "org_id": org_id,
                    "node_id": node_id,
                    "command_id": command_id,
                    "reason": "agent_run_raised",
                    "error": str(exc),
                },
            )
            return self._result("error", command_id, reason="agent_run_raised")
        await self._emit(
            "agent_run_finished",
            {
                "org_id": org_id,
                "node_id": node_id,
                "command_id": command_id,
                "output_len": len(str(output or "")),
            },
        )
        return self._result("ok", command_id, output=str(output) if output else "")

    async def pause_org_for_quota(self, org_id: str, *, reason: str) -> None:
        """v1 ``_pause_org_for_quota`` parity (78 LOC -> ~15 LOC).

        Emits an event + fires the optional org-paused
        callback (which the runtime wires to
        :meth:`OrgLifecycleManager.pause_org`).
        """

        await self._emit("org_paused_quota", {"org_id": org_id, "reason": reason})
        cb = self._on_org_paused
        if cb is None:
            return
        try:
            cb(org_id, reason)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("on_org_paused callback raised (org=%s)", org_id)

    async def emit_llm_usage(self, usage: Mapping[str, Any]) -> None:
        """v1 ``_emit_llm_usage`` parity -- just publish the event."""

        await self._emit("llm_usage", dict(usage))

    @staticmethod
    def is_quota_auth_error(exc: BaseException) -> bool:
        """Public hook over the private string-sniff (v1 parity name)."""

        return _looks_like_quota_or_auth_error(exc)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    async def _invoke_agent(agent: Any, content: str) -> Any:
        # Accept any agent that exposes ``async run(content) -> Any``.
        run = getattr(agent, "run", None)
        if run is None:
            raise RuntimeError(f"agent {type(agent).__name__} has no .run()")
        return await run(content)

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        try:
            await self._bus.emit(event, payload)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("event_bus.emit raised (event=%s)", event)

    @staticmethod
    def _result(
        status: str,
        command_id: str | None,
        *,
        output: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "command_id": command_id,
            "output": output,
            "reason": reason,
        }


__all__ = [
    "AgentPipelineExecutor",
]

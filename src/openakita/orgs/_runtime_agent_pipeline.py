"""``_runtime_agent_pipeline.py`` -- v2 OrgRuntime agent pipeline (P9.6f).

The second-heaviest sibling: lifts the agent-build / agent-
cache / message-to-agent-run pipeline out of v1
``OrgRuntime`` (~14 methods, ~1 410 LOC dominated by the
single ``_activate_and_run_inner`` method at 556 LOC). v2
splits cleanly into two concerns:

* :class:`AgentCache` -- per ``(org_id, node_id)`` cached
  agent instance store with TTL / explicit eviction (P9.6f1
  this commit; consumed by node-lifecycle P9.6g for evict
  on shutdown).
* :class:`AgentPipelineExecutor` -- the activate-and-run
  loop: prepare profile / build agent / hand off content /
  capture output / emit LLM usage events / detect quota
  errors + pause org (P9.6f2 next commit).

This commit (P9.6f1) ships the cache + builder Protocol +
profile-resolution scaffolding so the executor (P9.6f2)
has a stable seam. Agent construction is fully DI: a
v2-internal :class:`AgentBuilderProtocol` is the only
contract; concrete factories live in ``openakita.agents``
and get wired by the runtime composition root (P9.6i).

ADR-0012 (no-shim): zero ``openakita.orgs`` imports; v2
must be deletion-eligible by P9.8.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import time
from typing import Any, Protocol, runtime_checkable

from .command_service import OrgLookupProtocol

_LOGGER = logging.getLogger(__name__)

# v1 parity: org-state values that gate agent activation.
ORG_STATE_ACTIVE = "active"
ORG_STATE_PAUSED = "paused"


@dataclass
class AgentSpec:
    """Inputs the :class:`AgentBuilderProtocol` needs to build an Agent.

    Fields are deliberately Python-native (no Agent / Brain /
    Identity types) so v2 stays decoupled from
    ``openakita.agents`` at the type level. The builder
    Protocol implementation interprets these fields against
    its concrete Agent surface.
    """

    org_id: str
    node_id: str
    role: str
    persona: str | None = None
    workspace_dir: str | None = None
    system_prompt: str | None = None
    profile: Mapping[str, Any] = field(default_factory=dict)
    tools: tuple[str, ...] = ()
    unattended: bool = False


@runtime_checkable
class AgentBuilderProtocol(Protocol):
    """Builds and caches one agent per ``(org_id, node_id)``.

    Implementations in ``openakita.agents.factory`` /
    ``openakita.core.agent`` wire concrete Agent / Brain
    types; the runtime composes the builder via
    :meth:`OrgRuntime.__init__` (P9.6i).
    """

    def build(self, spec: AgentSpec) -> Any:
        """Synchronously construct an agent instance for ``spec``."""

    def teardown(self, agent: Any) -> None:
        """Release any resources the agent holds (MCP sessions / sockets)."""


class _NullAgentBuilder:
    """Builder of last resort -- raises if anyone actually tries to use it.

    Lets :class:`AgentCache` be instantiated standalone (e.g.
    in unit tests / smokes) without forcing every caller to
    plumb a concrete builder.
    """

    def build(self, spec: AgentSpec) -> Any:
        raise RuntimeError(
            "AgentBuilderProtocol not wired; cannot build agent "
            f"for org={spec.org_id} node={spec.node_id}"
        )

    def teardown(self, agent: Any) -> None:  # noqa: ARG002
        return None


@dataclass
class _CachedAgent:
    """Internal cache entry: agent instance + created_at + last_used_at."""

    spec: AgentSpec
    agent: Any
    created_at: float
    last_used_at: float


class AgentCache:
    """Per ``(org_id, node_id)`` cached agent store with explicit eviction.

    v1 ``OrgRuntime._get_or_create_agent`` + the per-node
    ``_node_agents`` dict + ``evict_node_agent`` collapse to
    this class. DI: just an :class:`AgentBuilderProtocol`
    (defaults to :class:`_NullAgentBuilder`).

    Thread-safety: callers must wrap mutating operations
    under their own lock if they share the cache across
    coroutines. v1 ``OrgRuntime`` serializes via its own
    ``_lock``; the runtime composition root will do the same.
    """

    def __init__(self, *, builder: AgentBuilderProtocol | None = None) -> None:
        self._builder: AgentBuilderProtocol = builder or _NullAgentBuilder()
        self._entries: dict[tuple[str, str], _CachedAgent] = {}

    def get_or_create(self, spec: AgentSpec) -> Any:
        """Return the cached agent for ``(spec.org_id, spec.node_id)`` or build one."""

        key = (spec.org_id, spec.node_id)
        entry = self._entries.get(key)
        if entry is not None:
            entry.last_used_at = time()
            return entry.agent
        agent = self._builder.build(spec)
        now = time()
        self._entries[key] = _CachedAgent(spec=spec, agent=agent, created_at=now, last_used_at=now)
        return agent

    def peek(self, org_id: str, node_id: str) -> Any | None:
        """Return cached agent without rebuilding (``None`` if absent)."""

        entry = self._entries.get((org_id, node_id))
        return entry.agent if entry is not None else None

    def evict(self, org_id: str, node_id: str) -> bool:
        """Drop the cached agent for one node; return True if anything was dropped."""

        entry = self._entries.pop((org_id, node_id), None)
        if entry is None:
            return False
        try:
            self._builder.teardown(entry.agent)
        except Exception:  # noqa: BLE001 (v1 parity: never crash eviction)
            _LOGGER.exception(
                "AgentBuilder.teardown raised (org=%s node=%s); dropping anyway",
                org_id,
                node_id,
            )
        return True

    def evict_org(self, org_id: str) -> int:
        """Drop every cached agent for one org; return count dropped."""

        keys = [k for k in self._entries if k[0] == org_id]
        for org, node in keys:
            self.evict(org, node)
        return len(keys)

    def cached_node_ids(self, org_id: str) -> list[str]:
        """List node ids that currently have a cached agent."""

        return [node for (org, node) in self._entries if org == org_id]

    def __len__(self) -> int:
        return len(self._entries)


class ProfileResolver:
    """Pulls a per-node ``profile`` mapping from the injected lookup.

    v1 ``_build_profile_for_node`` + ``_get_shared_profile`` +
    ``_resolve_org_workspace`` + ``_prepare_unattended_session``
    fold into one resolver here (~120 v1 LOC -> ~50 v2 LOC).
    The resolver is intentionally side-effect-free: it
    returns an :class:`AgentSpec` and the caller decides
    when to instantiate.
    """

    def __init__(self, *, lookup: OrgLookupProtocol) -> None:
        self._lookup = lookup

    def resolve(
        self,
        *,
        org_id: str,
        node_id: str,
        role: str | None = None,
        persona: str | None = None,
        unattended: bool = False,
        workspace_dir: str | None = None,
        extra_profile: Mapping[str, Any] | None = None,
    ) -> AgentSpec | None:
        """Build an :class:`AgentSpec` for ``(org_id, node_id)``.

        Returns ``None`` if the org / node cannot be found
        (v1 parity: callers degrade silently).
        """

        org = self._lookup.get_org(org_id)
        if org is None:
            return None
        # Org-level workspace (default per v1: ``data/orgs/{org_id}/workspace``).
        ws = workspace_dir
        if ws is None:
            try:
                ws = getattr(org, "workspace_dir", None) or getattr(org, "workspace", None)
            except Exception:  # noqa: BLE001
                ws = None
        # Per-node lookup -- v1 looks the node up via the org
        # nodes dict; we accept "missing" gracefully because the
        # node-lifecycle sibling may not have wired yet.
        node_obj = self._extract_node(org, node_id)
        resolved_role = role or self._role_for(node_obj) or "worker"
        resolved_persona = persona or self._persona_for(node_obj)
        profile = dict(extra_profile or {})
        return AgentSpec(
            org_id=org_id,
            node_id=node_id,
            role=resolved_role,
            persona=resolved_persona,
            workspace_dir=ws,
            profile=profile,
            unattended=unattended,
        )

    @staticmethod
    def _extract_node(org: Any, node_id: str) -> Any | None:
        nodes = getattr(org, "nodes", None)
        if isinstance(nodes, Mapping):
            return nodes.get(node_id)
        if nodes is None:
            return None
        # v1 ``Organization.nodes`` is occasionally a list of
        # dataclasses keyed by ``.id``.
        for n in nodes:
            if getattr(n, "id", None) == node_id:
                return n
        return None

    @staticmethod
    def _role_for(node: Any) -> str | None:
        if node is None:
            return None
        return getattr(node, "role", None) or getattr(node, "type", None)

    @staticmethod
    def _persona_for(node: Any) -> str | None:
        if node is None:
            return None
        return getattr(node, "persona", None) or getattr(node, "title", None)


# =====================================================================
# AgentPipelineExecutor -- the activate-and-run loop (P9.6f2)
# =====================================================================


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
    "ORG_STATE_ACTIVE",
    "ORG_STATE_PAUSED",
    "AgentBuilderProtocol",
    "AgentPipelineExecutor",
    "AgentCache",
    "AgentSpec",
    "ProfileResolver",
]

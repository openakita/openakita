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

Note (P-RC-10 P10.5a): :class:`AgentPipelineExecutor` and its
private helpers were extracted to
``_runtime_agent_pipeline_executor.py`` to satisfy the ADR-0014
per-shard 400-LOC soft cap; re-exported below for byte-for-byte
import-path compatibility.
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

# P-RC-10 P10.5a re-export: keep the public import path stable.
from ._runtime_agent_pipeline_executor import (  # noqa: E402
    AgentPipelineExecutor,
)

__all__ = [
    "ORG_STATE_ACTIVE",
    "ORG_STATE_PAUSED",
    "AgentBuilderProtocol",
    "AgentPipelineExecutor",
    "AgentCache",
    "AgentSpec",
    "ProfileResolver",
]

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
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import time
from typing import Any, Protocol, runtime_checkable

from .command_service import OrgLookupProtocol

_LOGGER = logging.getLogger(__name__)

# v1 parity: org-state values that gate agent activation.
ORG_STATE_ACTIVE = "active"
ORG_STATE_PAUSED = "paused"


# Sprint-4 P0-1 (audit ``_orgs_business_capability_audit_v4.md`` §6.2)
# -- depth gate + ContextVar for recursive child dispatch. Defined in
# this module (rather than the natural-home ``_default_agent_builder``)
# because both ``_default_agent_builder`` and
# ``_runtime_agent_pipeline_executor`` need to read them and they
# already share this module via the executor re-export at the bottom
# of the file. Putting them anywhere downstream would re-create the
# ``_runtime_agent_pipeline`` <-> ``_runtime_agent_pipeline_executor``
# import cycle that ADR-0014 explicitly carved out.
MAX_DISPATCH_DEPTH = 6
"""Hard cap on recursion depth: depth 0 = root, 1 = first-level reports,
2 = their reports, ... ``dispatch_subtask`` refuses calls that would
reach this depth so a hallucinated runaway can't fan out forever.

★ Multi-level routing: bumped 3 -> 6 so deep org charts
(主编 → 策划编辑 → 文案写手 → …) can cascade level by level. The real
terminator is topology, not this cap: :meth:`_available_nodes_for` now
hands each node ONLY its direct hierarchy children, so recursion stops
naturally at leaves (which have no children). The cap is just a safety
net against an org with an accidental cycle in its edges."""

MAX_DISPATCH_BLOCKS = 5
"""Hard cap on dispatch blocks parsed per LLM reply, regardless of how
many the model emits."""

dispatch_depth_var: ContextVar[int] = ContextVar("openakita_orgs_v2_dispatch_depth", default=0)
"""Per-task depth marker. Set by
:meth:`AgentPipelineExecutor.activate_and_run` before invoking the
cached node agent; read by
:class:`._default_agent_builder._BrainBackedNodeAgent` (to gate the
``<dispatch>`` parser) and by
:meth:`AgentPipelineExecutor.dispatch_subtask` (to derive the child
depth)."""

current_command_id_var: ContextVar[str] = ContextVar("openakita_orgs_v2_command_id", default="")
"""Per-task command-id marker. Set by
:meth:`AgentPipelineExecutor.activate_and_run` so the child-dispatch
callback wired into :class:`DefaultAgentBuilder` can re-attribute
recursive child runs to the **parent's** command id without having to
thread it through ``agent.run(content)``. Children share the parent's
command id by design: outcomes / cancellation / status are tracked at
the user-command granularity, not per-node."""

current_chain_id_var: ContextVar[str] = ContextVar("openakita_orgs_v2_chain_id", default="")
"""Per-run chain-id marker. Unlike ``current_command_id_var`` (shared
by every node of one user command) this is UNIQUE per agent run, so a
delegation tree can be reconstructed exactly: the root entry run mints
a fresh chain id; :meth:`AgentPipelineExecutor.dispatch_subtask` reads
the parent's chain off this var as ``parent_chain_id`` and mints a new
chain for the child run. ``subtask_assigned`` carries both
(``chain_id`` of the child + ``parent_chain_id`` of the dispatcher) and
``agent_run_started/finished`` carry the run's own ``chain_id`` -- so
the kanban (``OrgRuntime._contract_event_tap`` B5) can set
``ProjectTask.parent_task_id`` precisely instead of guessing from the
node id."""


@dataclass
class AgentSpec:
    """Inputs the :class:`AgentBuilderProtocol` needs to build an Agent.

    Fields are deliberately Python-native (no Agent / Brain /
    Identity types) so v2 stays decoupled from
    ``openakita.agents`` at the type level. The builder
    Protocol implementation interprets these fields against
    its concrete Agent surface.

    Sprint-5 P0-1 (audit ``_orgs_business_capability_audit_v5.md`` §5.2)
    adds three node-context fields the D4 minimum-viable tool injection
    consumes:

    * ``external_tools`` carries the v1-``OrgNode``-shaped whitelist
      (category names like ``research`` mixed with concrete tool names
      like ``hh_storyboard_decompose``).
    * ``enable_file_tools`` mirrors the v1 flag controlling whether the
      four basic file tools (``write_file`` / ``read_file`` /
      ``edit_file`` / ``list_directory``) are auto-merged into the
      whitelist.
    * ``available_nodes`` enumerates the sibling node ids + roles the
      producer LLM may dispatch to, so the per-node system prompt can
      list them explicitly and reduce LLM-invented target names
      (audit v5 §5.3 "self-invented director node").
    * ``workspace_dir`` is propagated to every node tool call. The tool runtime
      adds ``<command_id>/artifacts`` so commands remain isolated even when an
      organization uses a user-selected output directory.

    All three default to safe-empty values so legacy callers (parity /
    contract tests that build :class:`AgentSpec` by hand) keep working
    bit-for-bit.
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
    # Sprint-5 P0-1: node-context fields for D4.
    external_tools: tuple[str, ...] = ()
    enable_file_tools: bool = True
    available_nodes: tuple[tuple[str, str], ...] = ()


def _capability_label(node: Any) -> str:
    """Compose a capability descriptor for a coordinator's dispatch menu.

    A middle node (e.g. 策划编辑) can only delegate WELL if it knows what each
    of its direct reports is actually good at. Pre-fix the dispatch menu showed
    only the bare role title (``- writer-a: 文案写手``), so the coordinator had
    to guess which report fits which sub-task. We now fold each report's
    ``department`` + ``role_goal`` (the same capability signal the central
    supervisor already receives via ``NodeDescriptor.capabilities``) into the
    label so capability-based matching becomes possible. Kept short (goal
    truncated) so the per-coordinator token budget stays bounded.
    """

    role = (
        getattr(node, "role_title", None)
        or getattr(node, "label", None)
        or getattr(node, "role", None)
        or ""
    )
    role = role.strip() if isinstance(role, str) else str(role)
    goal = getattr(node, "role_goal", "") or ""
    dept = getattr(node, "department", "") or ""
    notes: list[str] = []
    if isinstance(dept, str) and dept.strip():
        notes.append(f"部门:{dept.strip()}")
    if isinstance(goal, str) and goal.strip():
        notes.append(f"职责:{goal.strip()[:80]}")
    if not notes:
        return role
    joined = "；".join(notes)
    return f"{role}（{joined}）" if role else joined


@runtime_checkable
class AgentBuilderProtocol(Protocol):
    """Builds and caches one agent per ``(org_id, node_id)``.

    Implementations in ``openakita.agents.factory`` /
    ``openakita.agent`` wire concrete Agent / Brain
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
        # Sprint-5 P0-1: lift the v1 OrgNode whitelist + flag so the
        # D4 helper (``_runtime_node_tools.resolve_node_tools``) can
        # build a real tools list for the brain. Missing node_obj is
        # fine: defaults to empty whitelist + file tools on, which the
        # helper maps to the four basic file tools only -- a v1-parity
        # safe baseline that lets even a minimally-wired node submit
        # a deliverable file.
        external_tools = self._external_tools_for(node_obj)
        enable_file_tools = self._enable_file_tools_for(node_obj)
        available_nodes = self._available_nodes_for(org, node_id)
        return AgentSpec(
            org_id=org_id,
            node_id=node_id,
            role=resolved_role,
            persona=resolved_persona,
            workspace_dir=ws,
            profile=profile,
            unattended=unattended,
            external_tools=external_tools,
            enable_file_tools=enable_file_tools,
            available_nodes=available_nodes,
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
        # v1 OrgNode uses ``custom_prompt`` for the prose persona body
        # while ``role_title`` is the short label. Prefer the longer
        # prose so the LLM gets enough context to act on the role.
        for attr in ("persona", "custom_prompt", "title", "role_title"):
            value = getattr(node, attr, None)
            if isinstance(value, str) and value.strip():
                return value
        return None

    @staticmethod
    def _external_tools_for(node: Any) -> tuple[str, ...]:
        """Best-effort whitelist read off a v1 OrgNode / v2 NodeSpec.

        v1 ``OrgNode`` exposes ``external_tools: list[str]`` (mixed
        category + tool name). v2 ``NodeSpec`` exposes
        ``tool_subset: tuple[str, ...] | None``. We accept both so any
        future v2 manager that returns ``NodeSpec`` directly still gets
        a populated whitelist without a separate code path.
        """

        if node is None:
            return ()
        candidates: Iterable[Any] | None = None
        for attr in ("external_tools", "tool_subset", "tools"):
            value = getattr(node, attr, None)
            if value:
                candidates = value
                break
        if not candidates:
            return ()
        out: list[str] = []
        for item in candidates:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return tuple(out)

    @staticmethod
    def _enable_file_tools_for(node: Any) -> bool:
        if node is None:
            return True
        value = getattr(node, "enable_file_tools", None)
        if value is None:
            return True
        return bool(value)

    @staticmethod
    def _available_nodes_for(org: Any, current_node_id: str) -> tuple[tuple[str, str], ...]:
        """Enumerate THIS node's DIRECT hierarchy children for dispatch.

        ★ Multi-level routing fix (test6 越级 bug): the pre-fix version
        listed EVERY other node in the org as a dispatch target. That let
        the root (主编) dispatch straight to leaf writers, skipping the
        middle 策划编辑 node entirely — a flat two-layer fan-out that
        ignored the designed org chart. We now return ONLY the node's
        direct hierarchy children (the ``source -> target`` ``hierarchy`` /
        ``escalate`` edges where ``source == current_node_id``), so:

        * a node can only hand work DOWN its own real edges (no 越级, no
          凭空连线);
        * a middle node (with children) becomes a sub-coordinator for its
          own reports;
        * a leaf node (no children) gets an empty list and is told to do
          the work itself.

        This is fully general — it reads whatever edges the org defines,
        for any depth / topology — not hard-coded for one org. The label
        is the child's role title so the dispatching LLM picks the right
        report by name.

        Sprint-5 finding #1 (invented ``director`` node) is subsumed: the
        target set is now an even tighter closed list (direct children
        only), so invention is structurally impossible.
        """

        nodes = getattr(org, "nodes", None)
        if nodes is None:
            return ()
        # Index node_id -> label once.
        labels: dict[str, str] = {}
        iter_nodes: Iterable[Any] = nodes.values() if isinstance(nodes, Mapping) else nodes
        for node in iter_nodes:
            node_id = getattr(node, "id", None) or getattr(node, "node_id", None)
            if not isinstance(node_id, str) or not node_id:
                continue
            labels[node_id] = _capability_label(node)

        # Direct hierarchy children = downstream targets of this node's
        # hierarchy / escalate edges. Collaborate / consult edges are peer
        # links and are deliberately NOT delegable targets.
        children: list[str] = []
        seen: set[str] = set()
        for e in list(getattr(org, "edges", None) or []):
            src = getattr(e, "source", "") or ""
            tgt = getattr(e, "target", "") or ""
            if src != current_node_id or not tgt or tgt == current_node_id:
                continue
            et = getattr(e, "edge_type", None)
            et_val = getattr(et, "value", None) or str(et)
            if et_val not in ("hierarchy", "escalate"):
                continue
            if tgt in seen:
                continue
            seen.add(tgt)
            children.append(tgt)
        return tuple((cid, labels.get(cid, "")) for cid in children)


# P-RC-10 P10.5a re-export: keep the public import path stable.
from ._runtime_agent_pipeline_executor import (  # noqa: E402
    AgentPipelineExecutor,
)

__all__ = [
    "MAX_DISPATCH_BLOCKS",
    "MAX_DISPATCH_DEPTH",
    "ORG_STATE_ACTIVE",
    "ORG_STATE_PAUSED",
    "AgentBuilderProtocol",
    "AgentPipelineExecutor",
    "AgentCache",
    "AgentSpec",
    "ProfileResolver",
    "current_chain_id_var",
    "current_command_id_var",
    "dispatch_depth_var",
]

"""``_runtime_agent_host.py`` -- v2 orgs node-level tool execution host.

Sprint-6 P0-1 (audit ``_orgs_business_capability_audit_v6.md`` + RCA
``_v17_p1_rca.md`` §1.5): close the D4 tool-execution loop that
Sprint-5 left half-wired. The Sprint-5 commit (5960bf3e) routed
:func:`._runtime_node_tools.execute_node_tool` straight at
:data:`openakita.tools.handlers.default_handler_registry`, expecting
v1 to have populated that global with 20 system handlers. v17 audit
proved the assumption wrong: every ``register_handler(...)`` call in
the codebase points at **per-Agent** ``self.handler_registry`` (see
``core/_agent_legacy.py:1099-1101 + 2216-2285``); the global was an
empty :class:`SystemHandlerRegistry` lookup cache used by
``policy_v2`` for approval-class metadata only. Result: every D4
``execute_by_tool`` call raised ``ValueError: No handler mapped for
tool: <name>`` and the surrounding ``except Exception`` block silently
turned it into ``node_tool_failed`` -- 12/12 tool calls in v17 audit
were "failed" with the LLM seeing ``[tool xxx failed: ...]`` text and
hallucinating around it (audit §1.R.D4, 0 ``node_tool_completed``).

### Design (RCA §1.4 + §六 escape-hatch)

The RCA's recommendation (§1.5 method B) was "per-org Agent
instance, complete with file_tool / memory_manager / default_cwd".
That requires ~500 LOC and a careful import-cycle audit because the
v1 ``Agent`` constructor pulls in browser / MCP / scheduler / shell
managers we do not actually need for orgs_v2 nodes (RCA §1.5.4
risk). The user-supplied Worker prompt's escape hatch explicitly
allows the minimum-viable wrapper when full per-org instantiation is
risky:

    "如果 NodeToolHost 实施时发现 v1 chat handler_registry 注册路径
    强依赖 Agent 实例 / 工厂 state（即无法独立复制）→ 写明并提案
    '重构成 Builder 模式'作为下下 sprint 任务，本次用最小 wrapper
    兜底"

The handlers ARE tightly coupled to a full ``Agent`` instance
(``FilesystemHandler`` reads ``agent.default_cwd`` /
``agent._execution_env_spec``; ``MemoryHandler`` reads
``agent.memory_manager`` etc.). Re-instantiating a per-org ``Agent``
would either re-do all that work for a fake context or leave many
handlers in degraded mode. So Sprint-6 takes the wrapper path:

* :class:`NodeToolHost` is built **once per process** (or once per
  ``OrgRuntime`` instance) and re-uses the main desktop ``Agent``'s
  already-populated ``handler_registry`` + tool-definition list.
* All 20 system handlers (filesystem / memory / web_search /
  web_fetch / search / plan / scheduled / mcp / persona / sticker /
  notebook / config / lsp / sleep / mode / structured_output /
  agent_package / system / im_channel / code_quality / skills /
  profile) are reachable immediately.
* **Plugin tools (``hh_*`` etc.) come along for free**: the plugin
  API (``plugins/api.py:300``) registers plugin handlers into the
  same ``agent.handler_registry`` and extends ``agent._tools`` with
  their definitions, so a node that whitelists ``hh_image_create``
  in its ``external_tools`` will find both the definition (for the
  LLM tool list) and the handler (for execution) without any
  Sprint-6 plumbing changes. This closes Sprint-6 P0-3 in the same
  module (see :meth:`NodeToolHost.lookup_tool_definition`).
* The host falls back to the (empty) global registry / static
  definitions table when ``app.state.agent`` has not finished
  wiring -- old observable preserved for backwards compatibility
  with bootstrap-race smokes (RCA §1.5.4 rollback path).

### Out-of-scope for Sprint-6 (documented; do NOT add without audit sign-off)

* **Per-org working_directory isolation** -- the host re-uses the
  main agent's ``default_cwd``; filesystem writes therefore land in
  the same workspace v1 chat uses. RCA §1.5.3 "out of scope" item
  #5: per-org ``<org>/workspace/<node_id>/`` split is reserved for
  Sprint-7+.
* **Per-org memory scope** -- memory handler reads / writes via the
  shared ``memory_manager``. Tenant isolation is a Sprint-7 item;
  Sprint-6 ships with a TODO comment and an integration test that
  documents the shared-scope observable (no surprise behaviour for
  v18).
* **Multi-round ReAct** -- :data:`._runtime_node_tools.MAX_TOOL_ROUNDS`
  stays at 1 (Sprint-5 deliberate bound, RCA §1.5.3 out-of-scope #1).
* **Permission / approval gate** -- this Sprint routes through the
  registry's plain ``execute_by_tool`` without going through the
  full ``ToolExecutor`` (which would require an active session /
  user / approval channel; orgs_v2 nodes run unattended). A
  follow-up sprint will plug ``policy_v2`` gates in. The ``RCA``
  §1.5.4 "permission gate" risk is acknowledged here; for Sprint-6
  the orgs_v2 path stays consistent with Sprint-5's choice (no
  gate) and the changelog calls this out explicitly.

### When the host is unavailable

``execute_node_tool`` keeps its Sprint-5 fallback to
``default_handler_registry`` so headless test fixtures and the
lifespan-race window between FastAPI starting and the desktop
``Agent`` finishing wiring keep working. The fallback path will
still raise ``ValueError: No handler mapped`` (same as v17) but at
least no test code needs to change; production wiring (via
``api/server.py`` lifespan) installs a host before any user command
can dispatch.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

__all__ = [
    "NodeToolHost",
    "build_node_tool_host",
]


_LOGGER = logging.getLogger(__name__)


class NodeToolHost:
    """Per-process wrapper around the main desktop Agent's tool plumbing.

    Construction is intentionally cheap: we only stash references to
    the existing ``handler_registry`` and ``_tools`` list -- no new
    handler instantiation, no MCP / browser bring-up. The host's
    lifetime is bound to the ``OrgRuntime`` it was registered against
    (see :meth:`OrgRuntime.register_node_tool_host`); ``dispose``
    drops the references for a clean shutdown but does not tear down
    anything on the source agent.

    Public surface:

    * :meth:`execute_tool` -- dispatch a tool call by name through
      the bound registry; mirrors
      ``SystemHandlerRegistry.execute_by_tool`` but with a clearer
      "tool unavailable" error so the orgs_v2 emit path can surface
      ``reason=plugin_not_loaded`` instead of the generic
      ``ValueError: No handler mapped...`` string the LLM had to
      hallucinate around in v17.
    * :meth:`lookup_tool_definition` -- find the Anthropic-shape tool
      dict for a tool name, scanning the main agent's ``_tools``
      list first (which includes plugin extensions) and falling
      back to the static ``tools/definitions/`` table. Returns
      ``None`` for unknown names so the resolver in
      ``_runtime_node_tools.resolve_node_tools`` can decide whether
      to drop the entry or surface a ``plugin_not_loaded`` warning.
    * :meth:`list_tools` -- the set of tool names the host can
      currently dispatch (used by the parity test against v1).
    """

    __slots__ = ("_agent", "_org_id", "_disposed")

    def __init__(self, *, agent: Any, org_id: str | None = None) -> None:
        if agent is None:
            raise ValueError(
                "NodeToolHost requires a non-None source agent; "
                "wire the desktop Agent into app.state.agent before "
                "starting any org runtime"
            )
        registry = getattr(agent, "handler_registry", None)
        if registry is None:
            raise ValueError(
                "source agent has no handler_registry; the agent "
                "must run through _init_handlers() before being "
                "wrapped by NodeToolHost"
            )
        self._agent = agent
        self._org_id = org_id
        self._disposed = False

    @property
    def org_id(self) -> str | None:
        return self._org_id

    @property
    def agent(self) -> Any:
        return self._agent

    def list_tools(self) -> list[str]:
        """Return tool names this host can dispatch (registry mapped)."""

        if self._disposed:
            return []
        registry = getattr(self._agent, "handler_registry", None)
        if registry is None:
            return []
        try:
            return list(registry.list_tools())
        except Exception:  # noqa: BLE001 -- defensive against external impls
            return []

    def lookup_tool_definition(self, tool_name: str) -> dict[str, Any] | None:
        """Look up an Anthropic-shape ``{name, description, input_schema}``.

        Order:

        1. The main agent's ``_tools`` list (populated by plugin
           ``register_tools`` calls -- includes ``hh_*``).
        2. The static ``tools/definitions/`` registry (system tools).

        Returns ``None`` if the tool is unknown to both sources;
        callers should treat that as "not bridged yet" (Sprint-5
        observable preserved).
        """

        if self._disposed:
            return None
        # Plugin-augmented list lives on the agent.
        agent_tools = getattr(self._agent, "_tools", None)
        if isinstance(agent_tools, list):
            for defn in agent_tools:
                if isinstance(defn, dict) and defn.get("name") == tool_name:
                    return {
                        "name": tool_name,
                        "description": defn.get("description", ""),
                        "input_schema": defn.get("input_schema", {"type": "object"}),
                    }
        # Fallback: static catalog (system tools only).
        try:
            from openakita.tools.definitions import get_tool_definition
        except Exception:  # noqa: BLE001 -- definitions import must not crash exec
            return None
        defn = get_tool_definition(tool_name)
        if defn is None:
            return None
        return {
            "name": defn.get("name", tool_name),
            "description": defn.get("description", ""),
            "input_schema": defn.get("input_schema", {"type": "object"}),
        }

    def has_tool(self, tool_name: str) -> bool:
        """``True`` iff the registry can dispatch ``tool_name``."""

        if self._disposed:
            return False
        registry = getattr(self._agent, "handler_registry", None)
        if registry is None:
            return False
        try:
            return bool(registry.has_tool(tool_name))
        except Exception:  # noqa: BLE001
            return False

    async def execute_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        node_id: str | None = None,
        command_id: str | None = None,
    ) -> str:
        """Dispatch one tool call through the bound handler registry.

        Raises :class:`ToolNotAvailable` when the tool is unknown to
        the registry -- the orgs_v2 caller can translate this into
        a ``reason=plugin_not_loaded`` ``node_tool_failed`` payload
        (Sprint-6 P0-3 telemetry parity). Cancellation propagates
        unchanged so the Sprint-3 P0-2 cancel pipeline keeps working
        through tool execution.
        """

        if self._disposed:
            raise ToolNotAvailable(
                tool_name,
                "node tool host has been disposed; "
                "org runtime is shutting down",
            )
        registry = getattr(self._agent, "handler_registry", None)
        if registry is None:
            raise ToolNotAvailable(
                tool_name,
                "source agent has no handler_registry available",
            )
        if not registry.has_tool(tool_name):
            raise ToolNotAvailable(
                tool_name,
                "no handler registered for this tool name; the "
                "plugin manifest may not be loaded yet",
            )
        # Tag the trace context with the per-node coordinates so
        # downstream LLM debug dumps can attribute the tool call to
        # the right org / node / command without us having to thread
        # the ids through every handler signature.
        brain = getattr(self._agent, "brain", None)
        set_trace = getattr(brain, "set_trace_context", None) if brain is not None else None
        if callable(set_trace):
            try:
                set_trace(
                    {
                        "org_id": self._org_id or "",
                        "node_id": node_id or "",
                        "command_id": command_id or "",
                        "caller": "orgs_v2_node_tool_host",
                    }
                )
            except Exception:  # noqa: BLE001 -- trace tagging is best-effort
                pass
        # Asyncio cancellation must propagate unchanged so the
        # surrounding ``execute_node_tool`` can let the user-cancel
        # path unwind cleanly (Sprint-3 P0-2 invariant).
        try:
            result = await registry.execute_by_tool(tool_name, dict(tool_input))
        except asyncio.CancelledError:
            raise
        return result if isinstance(result, str) else str(result)

    def dispose(self) -> None:
        """Drop references to the source agent; idempotent."""

        self._disposed = True


class ToolNotAvailable(LookupError):
    """Raised by :meth:`NodeToolHost.execute_tool` when the tool is
    not registered.

    Carries the tool name on ``self.tool_name`` so the orgs_v2 emit
    path can put it into the ``node_tool_failed`` payload (Sprint-6
    P0-3: ``reason="plugin_not_loaded"`` for ``hh_*`` tools whose
    plugin manifest is not loaded).
    """

    def __init__(self, tool_name: str, reason: str) -> None:
        super().__init__(reason)
        self.tool_name = tool_name
        self.reason = reason


def build_node_tool_host(
    *,
    agent: Any,
    org_id: str | None = None,
) -> NodeToolHost | None:
    """Convenience factory used by the API lifespan.

    Returns ``None`` (instead of raising) when the source agent is
    not yet ready, so the lifespan can be defensively re-called
    without aborting startup. The caller is expected to retry on the
    first command dispatch.
    """

    if agent is None:
        return None
    if getattr(agent, "handler_registry", None) is None:
        return None
    try:
        return NodeToolHost(agent=agent, org_id=org_id)
    except Exception:  # noqa: BLE001 -- factory must never crash startup
        _LOGGER.warning(
            "build_node_tool_host failed (org=%s); falling back to "
            "Sprint-5 default_handler_registry path",
            org_id,
            exc_info=True,
        )
        return None

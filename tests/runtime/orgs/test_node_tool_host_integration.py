"""Sprint-6 P0-1 / P0-3 regressions: NodeToolHost real-disk integration.

Pins the RCA ``_v17_p1_rca.md`` §3 systematic anti-pattern fix: every
P0 must ship at least one integration test that reads the **real**
``events.jsonl`` payload, not just an in-memory dict. The Sprint-5
suite covered the happy path with handler mocks and the global
``default_handler_registry.execute_by_tool`` monkey-patched -- which
is exactly why the v17 audit caught 0 ``node_tool_completed`` events
in production (the registry was empty in real backends but the tests
never exercised the real registry).

This module replaces ``default_handler_registry.execute_by_tool`` with
nothing: we build a real :class:`NodeToolHost` wrapping a real
``SystemHandlerRegistry``, register a real (test-only) filesystem
handler, and assert that the JSONL file on disk carries the lifecycle
events with the new Sprint-6 ``reason`` / ``cancelled_by`` schema.

Scope:

* ``test_node_tool_host_executes_real_filesystem_handler`` -- closes
  P0-1: registry must dispatch, ``node_tool_completed`` must hit
  events.jsonl, the handler must really write to the workspace.
* ``test_node_tool_host_classifies_plugin_not_loaded`` -- closes
  P0-3: hh_* tool whose plugin manifest is missing surfaces a
  distinct ``reason="plugin_not_loaded"`` instead of the generic
  ``"No handler mapped"`` Sprint-5 string the LLM hallucinated
  around.
* ``test_resolve_node_tools_picks_up_plugin_definitions`` --
  Sprint-6 P0-3: a node whitelisting ``hh_image_create`` must see
  the tool in its LLM tool list when the host's source agent has
  the plugin definition registered.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from openakita.orgs._runtime_agent_host import NodeToolHost, build_node_tool_host
from openakita.orgs._runtime_event_store import OrgEventStore
from openakita.orgs._runtime_node_tools import (
    execute_node_tool,
    resolve_node_tools,
)
from openakita.tools.handlers import SystemHandlerRegistry


class _FakeFilesystemHandler:
    """Real (test-only) handler that writes to a configured workspace.

    Mirrors the v1 ``FilesystemHandler.TOOLS`` surface just enough to
    exercise the registry lookup -- we ship one tool (``write_file``)
    so the host integration can prove real disk I/O happens through
    the orgs_v2 D4 path.
    """

    TOOLS = ["write_file"]

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    def __call__(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name != "write_file":
            raise ValueError(f"unsupported tool: {tool_name}")
        path = self._workspace / str(params.get("path") or "out.txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(params.get("content") or ""), encoding="utf-8")
        return f"wrote {path}"


class _FakeAgent:
    """Stand-in for the desktop ``Agent``: only the handler_registry +
    ``_tools`` surface that :class:`NodeToolHost` reads.

    Construction is deliberately minimal so the test does not pull in
    the full ``openakita.core.agent.Agent`` import graph (browser /
    MCP / desktop / persona managers). Real production wiring binds
    to the desktop ``Agent`` which has all 20 handlers + plugin
    handlers registered through the same path tested here.
    """

    def __init__(self, *, workspace: Path, plugin_tools: list[dict[str, Any]] | None = None) -> None:
        self.handler_registry = SystemHandlerRegistry()
        # Hand the registry one real handler so write_file calls actually
        # land on disk -- this is the bug Sprint-5 missed (the registry
        # had no handlers; v17 audit saw 12/12 failures).
        self.handler_registry.register(
            "filesystem",
            _FakeFilesystemHandler(workspace),
            tool_names=_FakeFilesystemHandler.TOOLS,
        )
        # Mirror the layout ``plugins/api.py:register_tools`` produces:
        # extend ``_tools`` with the plugin's tool definitions so
        # ``NodeToolHost.lookup_tool_definition`` can find them.
        self._tools: list[dict[str, Any]] = list(plugin_tools or [])

    # NodeToolHost only reads ``brain.set_trace_context`` for trace
    # tagging; missing-brain path is exercised by setting brain=None.
    @property
    def brain(self) -> Any:
        return None


def _make_emit(store: OrgEventStore):
    async def _emit(name: str, payload: dict[str, Any]) -> None:
        record = dict(payload)
        record.setdefault("type", name)
        store.append(record)

    return _emit


def _read_events(jsonl: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not jsonl.is_file():
        return events
    for raw in jsonl.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


# ---------------------------------------------------------------------------
# P0-1 -- registry actually dispatches + events.jsonl carries completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_tool_host_executes_real_filesystem_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p06.host.real_filesystem_handler_writes_to_disk

    Production-shape integration: real registry + real handler +
    real JSONL file. The Sprint-5 commit shipped this path believing
    ``default_handler_registry`` was already populated; the v17
    audit proved it wasn't. This test fails LOUDLY (no completion
    event + empty workspace) if we ever regress to the empty-registry
    state.

    Command-scope sandbox (exploratory v22): a node writing a RELATIVE
    path no longer lands in the FileTool workspace / process CWD -- it is
    redirected into the PER-COMMAND workspace
    ``data/orgs/<id>/commands/<command_id>/artifacts/`` before the handler
    runs (see ``_redirect_relative_writes``). We point the resolver at a tmp
    org dir so the redirect is exercised end-to-end without polluting the
    real ``data/`` tree, and assert the file materialises in the per-command
    dir (theme-drift isolation: a fresh command opens an empty workspace).
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    org_dir = tmp_path / "orgs" / "org-int"
    import openakita.orgs._runtime_node_artifacts as _artifacts

    monkeypatch.setattr(
        _artifacts, "_resolve_org_dir", lambda _get, _org: org_dir
    )
    jsonl = tmp_path / "logs" / "events.jsonl"
    store = OrgEventStore(org_id="org-int", jsonl_path=jsonl)
    agent = _FakeAgent(workspace=workspace)
    host = NodeToolHost(agent=agent, org_id="org-int")

    result, is_error = await execute_node_tool(
        tool_name="write_file",
        tool_input={"path": "deliverable.txt", "content": "v18 audit signal"},
        org_id="org-int",
        node_id="producer",
        command_id="cmd-001",
        emit=_make_emit(store),
        tool_host=host,
    )

    assert is_error is False
    assert "deliverable.txt" in result
    # The handler really ran -- file exists with the LLM-supplied content,
    # redirected into the PER-COMMAND artifacts dir (not the org-level dir, not
    # the bare workspace/CWD). This is the command-level isolation boundary.
    written = (
        org_dir / "commands" / "cmd-001" / "artifacts" / "deliverable.txt"
    ).read_text(encoding="utf-8")
    assert written == "v18 audit signal"
    # Belt-and-braces: it must NOT have leaked into the shared org-level dir,
    # which is exactly what would re-pollute a sibling command.
    assert not (org_dir / "artifacts" / "deliverable.txt").exists()
    # The events.jsonl actually has the completion line -- the v17
    # smoking gun signal Sprint-6 must produce ≥3 of.
    events = _read_events(jsonl)
    event_types = [e.get("type") for e in events]
    assert "node_tool_called" in event_types
    assert "node_tool_completed" in event_types
    completed = next(e for e in events if e.get("type") == "node_tool_completed")
    assert completed["tool_name"] == "write_file"
    assert completed["node_id"] == "producer"
    assert completed["command_id"] == "cmd-001"


@pytest.mark.asyncio
async def test_node_tool_host_classifies_plugin_not_loaded(tmp_path: Path) -> None:
    """case id: p06.host.plugin_not_loaded_classified

    Sprint-6 P0-3: when a workbench node whitelists an ``hh_*`` tool
    but the happyhorse-video plugin is not loaded, the registry has
    no handler. The host must distinguish this from a handler crash
    so events.jsonl readers can attribute the failure precisely
    (Sprint-5 conflated both under ``ValueError: No handler mapped
    for tool``).
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    jsonl = tmp_path / "logs" / "events.jsonl"
    store = OrgEventStore(org_id="org-int", jsonl_path=jsonl)
    # No plugin definitions, no plugin handler -- mirrors the "plugin
    # disabled" production state.
    agent = _FakeAgent(workspace=workspace)
    host = NodeToolHost(agent=agent, org_id="org-int")

    result, is_error = await execute_node_tool(
        tool_name="hh_image_create",
        tool_input={"prompt": "valentine cover"},
        org_id="org-int",
        node_id="wb-hh-image",
        command_id="cmd-002",
        emit=_make_emit(store),
        tool_host=host,
    )

    assert is_error is True
    assert "unavailable" in result.lower()
    events = _read_events(jsonl)
    failed = next(e for e in events if e.get("type") == "node_tool_failed")
    assert failed["tool_name"] == "hh_image_create"
    assert failed["reason"] == "plugin_not_loaded"


@pytest.mark.asyncio
async def test_node_tool_host_steers_phantom_dispatch_tool(tmp_path: Path) -> None:
    """case id: test18.host.phantom_dispatch_steered

    test18: coordinator nodes delegate via ``<dispatch target="...">`` XML
    text blocks, NOT a callable tool. A node LLM sometimes hallucinates a
    ``dispatch`` tool anyway; the generic ``plugin_not_loaded`` path made it
    re-call the phantom tool until its budget drained. The host must instead
    return a precise corrective (the XML syntax) and tag telemetry with a
    distinct ``use_dispatch_xml`` reason.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    jsonl = tmp_path / "logs" / "events.jsonl"
    store = OrgEventStore(org_id="org-int", jsonl_path=jsonl)
    agent = _FakeAgent(workspace=workspace)
    host = NodeToolHost(agent=agent, org_id="org-int")

    result, is_error = await execute_node_tool(
        tool_name="dispatch",
        tool_input={"target": "writer-a", "prompt": "write it"},
        org_id="org-int",
        node_id="planner",
        command_id="cmd-003",
        emit=_make_emit(store),
        tool_host=host,
    )

    assert is_error is True
    # Corrective must teach the exact XML syntax so the node self-corrects.
    assert "<dispatch target=" in result
    events = _read_events(jsonl)
    failed = next(e for e in events if e.get("type") == "node_tool_failed")
    assert failed["tool_name"] == "dispatch"
    assert failed["reason"] == "use_dispatch_xml"


# ---------------------------------------------------------------------------
# P0-3 -- plugin tool definitions surface through resolve_node_tools
# ---------------------------------------------------------------------------


def test_resolve_node_tools_picks_up_plugin_definitions(tmp_path: Path) -> None:
    """case id: p06.host.resolve_includes_plugin_tools

    The v17 audit (§1.2.2 + Sprint-5 §3 "out of scope") found
    workbench nodes whitelisting ``hh_image_create`` getting
    ``tools_count=0`` from ``resolve_node_tools`` because the
    static ``tools/definitions/`` catalog has no plugin entries.
    Sprint-6 P0-3 plumbs the host's ``lookup_tool_definition`` into
    the resolver so plugin-registered tools are first-class.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plugin_tools = [
        {
            "name": "hh_image_create",
            "description": "Generate an image from a prompt.",
            "input_schema": {"type": "object", "properties": {"prompt": {"type": "string"}}},
        },
    ]
    agent = _FakeAgent(workspace=workspace, plugin_tools=plugin_tools)
    host = NodeToolHost(agent=agent, org_id="org-int")

    resolved = resolve_node_tools(
        external_tools=("hh_image_create",),
        enable_file_tools=False,
        tool_host=host,
    )
    names = {t["name"] for t in resolved}
    assert "hh_image_create" in names
    spec = next(t for t in resolved if t["name"] == "hh_image_create")
    assert spec["description"]
    assert spec["input_schema"]


def test_build_node_tool_host_returns_none_when_agent_missing(tmp_path: Path) -> None:
    """case id: p06.host.factory_safe_against_bootstrap_race

    Sprint-5 ordering: FastAPI lifespan composes the runtime BEFORE
    ``main.py`` populates ``app.state.agent``. The factory must
    return ``None`` rather than raising so the lifespan keeps
    booting; the late ``update_agent`` hook re-runs the bind once
    the desktop Agent is wired (see ``api/server.py``).
    """

    assert build_node_tool_host(agent=None) is None

    class _NoRegistry:
        pass

    assert build_node_tool_host(agent=_NoRegistry()) is None


def test_node_tool_host_dispose_breaks_further_dispatch(tmp_path: Path) -> None:
    """case id: p06.host.dispose_idempotent_and_safe

    Production wiring rebinds the host on hot reload / agent reload
    via ``OrgRuntime.set_node_tool_host``. ``dispose`` must:
      * drop the source-agent reference so it can be GC'd,
      * make further ``execute_tool`` attempts raise
        ``ToolNotAvailable`` instead of crashing with NPE.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    agent = _FakeAgent(workspace=workspace)
    host = NodeToolHost(agent=agent, org_id="org-int")

    host.dispose()
    host.dispose()  # idempotent

    from openakita.orgs._runtime_agent_host import ToolNotAvailable

    async def _call() -> None:
        await host.execute_tool("write_file", {"path": "p.txt", "content": "x"})

    with pytest.raises(ToolNotAvailable):
        asyncio.run(_call())

"""Sprint-4 P0-2 regression: node-output artefacts + memory summaries.

The v15 audit (``_orgs_business_capability_audit_v4.md`` §5.4 / §6.2 #2)
found every ``data/orgs/<id>/artifacts/`` and ``data/orgs/<id>/memory/``
directory empty across 8 v15-* orgs despite 49 ``agent_run_finished``
events. Node outputs lived only inside the event payload text -- no
file the next node could grep, no on-disk evidence the run produced
anything.

This file pins the persistence helpers + the executor wire-up:

* :func:`persist_node_artifact` writes the full LLM text to a
  per-(command, node) ``.txt`` and returns the resolved path.
* :func:`persist_node_memory` writes a YAML-front-matter Markdown
  file with a bounded summary (head + ellipsis + tail when the body
  exceeds the threshold).
* Both helpers respect the ``OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS``
  env-var kill switch.
* :meth:`AgentPipelineExecutor.activate_and_run` invokes both helpers
  on every successful run and stamps the artefact path into the
  ``agent_run_finished`` event payload.
* Child dispatches (Sprint-4 P0-1) reach the same code path with a
  ``parent_node_id`` so the artefact filename encodes the delegation
  chain.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openakita.orgs._default_agent_builder import DefaultAgentBuilder
from openakita.orgs._runtime_agent_pipeline import (
    AgentCache,
    AgentPipelineExecutor,
    ProfileResolver,
    current_command_id_var,
)
from openakita.orgs._runtime_node_artifacts import (
    MEMORY_SUMMARY_THRESHOLD,
    artifact_persistence_enabled,
    persist_node_artifact,
    persist_node_memory,
    safe_path_segment,
)


# ---------------------------------------------------------------------------
# Helper builders (lightweight stand-ins for OrgManager + brain)
# ---------------------------------------------------------------------------


class _Node:
    def __init__(self, id_: str) -> None:
        self.id = id_
        self.role = "worker"
        self.persona = None


class _Org:
    def __init__(self, node_ids: list[str]) -> None:
        self.status = SimpleNamespace(value="active")
        self.state = "active"
        self.nodes = [_Node(nid) for nid in node_ids]

    def get_node(self, nid: str) -> _Node | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self) -> list[_Node]:
        return list(self.nodes[:1])


class _Lookup:
    def __init__(self, node_ids: list[str], *, org_dir: Path) -> None:
        self._org = _Org(node_ids)
        self._org_dir = org_dir

    def get_org(self, org_id: str) -> _Org | None:
        return self._org

    def get_org_dir(self, org_id: str) -> Path:  # noqa: ARG002
        return self._org_dir


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, dict(payload)))


def _brain_with_replies(replies: list[str]) -> Any:
    call_index = {"n": 0}

    def _resolve(**_kwargs: Any) -> SimpleNamespace:
        idx = call_index["n"]
        call_index["n"] = idx + 1
        text = replies[min(idx, len(replies) - 1)]
        return SimpleNamespace(content=[SimpleNamespace(text=text)])

    return SimpleNamespace(
        messages_create_async=AsyncMock(side_effect=_resolve),
        set_trace_context=lambda _ctx: None,
    )


# ---------------------------------------------------------------------------
# Direct unit tests for the helpers
# ---------------------------------------------------------------------------


def test_safe_path_segment_strips_windows_unsafe_chars() -> None:
    """case id: p0_2.safe_segment.windows_chars

    Filenames must not contain ``< > : " / \\ | ? *`` on Windows.
    The sanitiser drops them silently rather than escaping (an
    escaped name would not round-trip back to the original node id,
    so escaping has no value here).
    """

    assert safe_path_segment("a/b:c*d") == "abcd"
    assert safe_path_segment("  trim me  ") == "trim_me"
    assert safe_path_segment("") == "_"
    assert safe_path_segment(" \t \n ") == "_"


def test_artifact_persistence_enabled_respects_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """case id: p0_2.toggle.env_var_disables_persistence

    The audit recommended a single env-var toggle so storage-bound
    smokes / triage runs can opt out without code changes. The
    helpers consult the toggle on every call (no module-import
    snapshot) so a test fixture monkeypatch takes effect.
    """

    monkeypatch.delenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", raising=False)
    assert artifact_persistence_enabled() is True
    monkeypatch.setenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", "0")
    assert artifact_persistence_enabled() is False
    monkeypatch.setenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", "false")
    assert artifact_persistence_enabled() is False
    monkeypatch.setenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", "1")
    assert artifact_persistence_enabled() is True


def test_persist_node_artifact_writes_file_and_returns_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_2.artifact.basic_write

    The minimum-viable contract: a UTF-8 ``.txt`` lands under
    ``<org_dir>/artifacts/`` containing the LLM output verbatim. The
    return value is the resolved path so the executor can stamp it
    into the ``agent_run_finished`` event payload.
    """

    monkeypatch.delenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", raising=False)
    path_str = persist_node_artifact(
        org_id="o1",
        command_id="cmd_a",
        node_id="producer",
        output="producer LLM reply\n第二段中文内容",
        get_org_dir=lambda _oid: tmp_path,
    )
    assert path_str is not None
    p = Path(path_str)
    assert p.is_file()
    assert p.parent.name == "artifacts"
    assert p.parent.parent == tmp_path
    body = p.read_text(encoding="utf-8")
    assert body == "producer LLM reply\n第二段中文内容"
    # Filename encodes command + node + timestamp.
    assert p.name.startswith("cmd_a_producer_")
    assert p.suffix == ".txt"


def test_persist_node_artifact_encodes_parent_chain_in_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_2.artifact.child_filename_includes_parent

    Sprint-4 P0-1 children are persisted with the parent node id in
    the filename so a single ``ls`` on the artefacts directory shows
    the delegation tree. Filename layout:
    ``<cid>_<parent>_<child>_<ts>.txt``.
    """

    monkeypatch.delenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", raising=False)
    path_str = persist_node_artifact(
        org_id="o1",
        command_id="cmd_b",
        node_id="screenwriter",
        parent_node_id="producer",
        output="child output",
        get_org_dir=lambda _oid: tmp_path,
    )
    assert path_str is not None
    p = Path(path_str)
    assert p.name.startswith("cmd_b_producer_screenwriter_")


def test_persist_node_artifact_skipped_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_2.artifact.kill_switch_blocks_write

    With the env-var set to ``"0"`` no file is written and the
    return value is ``None`` -- the executor must surface ``None`` as
    "persistence skipped" rather than blocking the event flow.
    """

    monkeypatch.setenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", "0")
    result = persist_node_artifact(
        org_id="o1",
        command_id="cmd_x",
        node_id="producer",
        output="hello",
        get_org_dir=lambda _oid: tmp_path,
    )
    assert result is None
    assert not (tmp_path / "artifacts").exists()


def test_persist_node_artifact_skipped_when_output_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_2.artifact.empty_output_no_file

    Blank / whitespace-only outputs don't produce useful artefacts;
    we skip rather than littering the directory with 0-byte files.
    """

    monkeypatch.delenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", raising=False)
    assert (
        persist_node_artifact(
            org_id="o1",
            command_id="c",
            node_id="n",
            output="",
            get_org_dir=lambda _oid: tmp_path,
        )
        is None
    )
    assert (
        persist_node_artifact(
            org_id="o1",
            command_id="c",
            node_id="n",
            output="   \n\t  ",
            get_org_dir=lambda _oid: tmp_path,
        )
        is None
    )


def test_persist_node_memory_short_output_is_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_2.memory.short_output_lossless

    Outputs at or below :data:`MEMORY_SUMMARY_THRESHOLD` are
    persisted verbatim (no truncation marker). This keeps the common
    "single sentence reply" case lossless for the next prompt
    builder to read.
    """

    monkeypatch.delenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", raising=False)
    short = "short reply" * 5
    assert len(short) < MEMORY_SUMMARY_THRESHOLD
    path_str = persist_node_memory(
        org_id="o1",
        command_id="cmd_m",
        node_id="producer",
        role="worker",
        output=short,
        get_org_dir=lambda _oid: tmp_path,
    )
    assert path_str is not None
    p = Path(path_str)
    assert p.is_file()
    assert p.parent.name == "memory"
    body = p.read_text(encoding="utf-8")
    assert body.startswith("---\n")
    assert "command_id: \"cmd_m\"" in body
    assert "node_id: \"producer\"" in body
    assert "role: \"worker\"" in body
    assert "truncated" not in body
    assert short in body


def test_persist_node_memory_long_output_is_summarised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_2.memory.long_output_truncated_with_marker

    Outputs above the threshold get the head + ``[... truncated ...]``
    + tail layout so the per-memory file size has a predictable
    upper bound. The full content is still preserved in the
    artefact file -- ``memory/`` is the "summary" lane.
    """

    monkeypatch.delenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", raising=False)
    head = "A" * 500
    middle = "M" * 800
    tail = "Z" * 50
    long_output = head + middle + tail
    assert len(long_output) > MEMORY_SUMMARY_THRESHOLD
    path_str = persist_node_memory(
        org_id="o1",
        command_id="cmd_l",
        node_id="producer",
        output=long_output,
        get_org_dir=lambda _oid: tmp_path,
    )
    assert path_str is not None
    body = Path(path_str).read_text(encoding="utf-8")
    assert "[... truncated ...]" in body
    assert "A" * 100 in body  # head slice is well-covered
    assert "Z" * 50 in body  # tail slice ends with the original tail
    # The full middle block is dropped -- we should NOT see 800 'M's.
    assert "M" * 200 not in body


def test_persist_node_memory_skipped_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_2.memory.kill_switch_blocks_write"""

    monkeypatch.setenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", "off")
    assert (
        persist_node_memory(
            org_id="o1",
            command_id="c",
            node_id="n",
            output="hello",
            get_org_dir=lambda _oid: tmp_path,
        )
        is None
    )


# ---------------------------------------------------------------------------
# End-to-end executor wire-up
# ---------------------------------------------------------------------------


def _make_executor(
    *, lookup: _Lookup, brain: Any, bus: _RecordingBus
) -> AgentPipelineExecutor:
    profile_resolver = ProfileResolver(lookup=lookup)
    executor_holder: dict[str, AgentPipelineExecutor] = {}

    async def _dispatch_subtask_cb(
        *,
        org_id: str,
        parent_node_id: str,
        child_node_id: str,
        child_content: str,
    ) -> str:
        # Mirror the production wiring in ``api/server.py`` -- the
        # parent's command_id rides the ContextVar so the child gets
        # attributed back to the user-command, not orphaned.
        return await executor_holder["e"].dispatch_subtask(
            org_id=org_id,
            parent_node_id=parent_node_id,
            parent_command_id=current_command_id_var.get("") or None,
            child_node_id=child_node_id,
            child_content=child_content,
        )

    cache = AgentCache(
        builder=DefaultAgentBuilder(
            brain_provider=lambda: brain,
            dispatch_callback=_dispatch_subtask_cb,
        )
    )
    executor = AgentPipelineExecutor(
        cache=cache,
        resolver=profile_resolver,
        lookup=lookup,
        event_bus=bus,
    )
    executor_holder["e"] = executor
    return executor


def test_executor_persists_artifact_and_memory_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_2.executor.persists_on_finished

    The end-to-end pass: ``activate_and_run`` returns ``status=ok``
    AND both ``artifacts/`` and ``memory/`` directories carry a real
    file. The ``agent_run_finished`` event payload includes the
    resolved artefact path so SSE consumers can deep-link without an
    extra round-trip (audit v4 §6.2 explicitly called the missing
    payload field out as a usability gap).
    """

    monkeypatch.delenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", raising=False)
    bus = _RecordingBus()
    lookup = _Lookup(["producer"], org_dir=tmp_path)
    brain = _brain_with_replies(["producer text reply"])

    executor = _make_executor(lookup=lookup, brain=brain, bus=bus)
    result = asyncio.run(
        executor.activate_and_run(
            org_id="o1",
            node_id="producer",
            content="hi",
            command_id="cmd_e",
        )
    )

    assert result["status"] == "ok"
    artifacts = list((tmp_path / "artifacts").iterdir())
    memories = list((tmp_path / "memory").iterdir())
    assert len(artifacts) == 1
    assert len(memories) == 1
    assert artifacts[0].read_text(encoding="utf-8") == "producer text reply"

    finished = next(p for n, p in bus.events if n == "agent_run_finished")
    assert finished.get("artifact_path")
    assert "producer" in finished["artifact_path"]


def test_executor_skips_persistence_when_env_var_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_2.executor.kill_switch_blocks_files

    With the env-var off the executor's ``agent_run_finished`` still
    fires (the event contract is unchanged), but no ``artifact_path``
    is stamped onto the payload and the per-org dirs stay empty.
    """

    monkeypatch.setenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", "0")
    bus = _RecordingBus()
    lookup = _Lookup(["producer"], org_dir=tmp_path)
    brain = _brain_with_replies(["reply"])
    executor = _make_executor(lookup=lookup, brain=brain, bus=bus)
    result = asyncio.run(
        executor.activate_and_run(
            org_id="o1",
            node_id="producer",
            content="hi",
            command_id="cmd_e",
        )
    )
    assert result["status"] == "ok"
    finished = next(p for n, p in bus.events if n == "agent_run_finished")
    assert "artifact_path" not in finished
    assert not (tmp_path / "artifacts").exists()
    assert not (tmp_path / "memory").exists()


def test_executor_persists_child_with_parent_chain_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """case id: p0_2.executor.child_artifact_filename

    When a parent dispatches into a child (Sprint-4 P0-1), the
    child's artefact filename carries both the parent and child node
    ids so the delegation tree is reconstructable from the file
    listing alone. We monkeypatch the delegation-log dir to keep the
    test hermetic.
    """

    from openakita.orgs import _runtime_dispatch as dispatch_mod

    monkeypatch.delenv("OPENAKITA_ORGS_V2_PERSIST_ARTIFACTS", raising=False)
    monkeypatch.setattr(
        dispatch_mod, "_resolve_delegation_log_dir", lambda: tmp_path / "_dl"
    )

    bus = _RecordingBus()
    lookup = _Lookup(["producer", "screenwriter"], org_dir=tmp_path)
    brain = _brain_with_replies(
        [
            "<dispatch target=\"screenwriter\">scene 1</dispatch>",
            "screenwriter reply text",
        ]
    )
    executor = _make_executor(lookup=lookup, brain=brain, bus=bus)
    result = asyncio.run(
        executor.activate_and_run(
            org_id="o1",
            node_id="producer",
            content="kickoff",
            command_id="cmd_child",
        )
    )
    assert result["status"] == "ok"
    artifacts = sorted((tmp_path / "artifacts").iterdir(), key=lambda p: p.name)
    # Two artefact files: producer (entry) + screenwriter (child).
    assert len(artifacts) == 2
    parent_name = next(p.name for p in artifacts if "producer" in p.name and "screenwriter" not in p.name)
    child_name = next(p.name for p in artifacts if "screenwriter" in p.name)
    assert parent_name.startswith("cmd_child_producer_")
    assert child_name.startswith("cmd_child_producer_screenwriter_")

    # Two memory files paired with each artefact.
    memories = list((tmp_path / "memory").iterdir())
    assert len(memories) == 2

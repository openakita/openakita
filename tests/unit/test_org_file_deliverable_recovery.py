"""test11 P1/P2: node-written files count as deliverables + emit file_output_registered.

Root cause (org_34856abd2e8c real events): writer-a wrote a 12 KB plan via
write_file but ended its turn with empty final TEXT (output_len=0) -> parent
review judged "空产出" -> bounced; writer-b same -> escalated/fizzled. And
write_file / deliver_artifacts in the node path never emitted
``file_output_registered`` so the command-center delivery cards were empty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openakita.orgs._runtime_node_tools import (
    _extract_written_paths,
    execute_node_tool,
    pop_node_file_outputs,
    record_node_file_output,
)


# ---------------------------------------------------------------------------
# 1) path extraction
# ---------------------------------------------------------------------------


def test_extract_written_paths_write_file() -> None:
    assert _extract_written_paths("write_file", {"path": "a/b.md", "content": "x"}) == ["a/b.md"]
    assert _extract_written_paths("append_file", {"file_path": "c.md"}) == ["c.md"]


def test_extract_written_paths_deliver_artifacts() -> None:
    args = {"artifacts": [{"type": "file", "path": "d1.md"}, {"type": "file", "path": "d2.pdf"}]}
    assert _extract_written_paths("deliver_artifacts", args) == ["d1.md", "d2.pdf"]


def test_extract_written_paths_ignores_non_file_tools() -> None:
    assert _extract_written_paths("web_search", {"query": "x"}) == []


# ---------------------------------------------------------------------------
# 2) record / pop tracker
# ---------------------------------------------------------------------------


def test_record_and_pop_node_file_outputs(tmp_path: Path) -> None:
    f = tmp_path / "plan.md"
    f.write_text("hello world", encoding="utf-8")
    size = record_node_file_output("org1", "cmd1", "writer-a", str(f), "write_file")
    assert size == len("hello world")
    got = pop_node_file_outputs("org1", "cmd1", "writer-a")
    assert len(got) == 1 and got[0]["path"] == str(f) and got[0]["size_bytes"] == size
    # popped -> cleared
    assert pop_node_file_outputs("org1", "cmd1", "writer-a") == []


def test_record_missing_file_is_noop() -> None:
    assert record_node_file_output("o", "c", "n", "does/not/exist.md", "write_file") is None


def test_pop_respects_since_ts(tmp_path: Path) -> None:
    import time

    f = tmp_path / "old.md"
    f.write_text("data", encoding="utf-8")
    record_node_file_output("org2", "cmd2", "n", str(f), "write_file")
    future = time.time() + 100
    # nothing written at/after `future`
    assert pop_node_file_outputs("org2", "cmd2", "n", since_ts=future) == []
    # but the entry survived (since_ts filter doesn't drop newer-than-cutoff)
    assert len(pop_node_file_outputs("org2", "cmd2", "n", since_ts=0.0)) == 1


# ---------------------------------------------------------------------------
# 3) recoverable-deliverable picker
# ---------------------------------------------------------------------------


def test_pick_recoverable_prefers_text_then_largest() -> None:
    from openakita.orgs._runtime_agent_pipeline_executor import _pick_recoverable_deliverable

    written = [
        {"path": "a.png", "size_bytes": 99999},
        {"path": "small.md", "size_bytes": 100},
        {"path": "big.md", "size_bytes": 5000},
    ]
    best = _pick_recoverable_deliverable(written)
    assert best is not None and best["path"] == "big.md"


def test_pick_recoverable_skips_zero_byte() -> None:
    from openakita.orgs._runtime_agent_pipeline_executor import _pick_recoverable_deliverable

    assert _pick_recoverable_deliverable([{"path": "x.md", "size_bytes": 0}]) is None
    assert _pick_recoverable_deliverable([]) is None


# ---------------------------------------------------------------------------
# 4) integration: write_file via execute_node_tool emits file_output_registered
# ---------------------------------------------------------------------------


class _WriteCapableHandler:
    TOOLS = ["write_file"]

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    def __call__(self, tool_name: str, params: dict[str, Any]) -> str:
        # The write-redirect rewrites a relative path to an ABSOLUTE per-command
        # path; ``workspace / abspath`` collapses to abspath, so the file lands
        # in the per-command sandbox (mirrors the production handler).
        path = self._workspace / str(params.get("path") or "out.txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(params.get("content") or ""), encoding="utf-8")
        return f"wrote {path}"


class _FakeAgent:
    def __init__(self, workspace: Path) -> None:
        from openakita.tools.handlers import SystemHandlerRegistry

        self.handler_registry = SystemHandlerRegistry()
        self.handler_registry.register(
            "filesystem", _WriteCapableHandler(workspace), tool_names=["write_file"]
        )
        self._tools: list[dict[str, Any]] = []

    @property
    def brain(self) -> Any:
        return None


@pytest.mark.asyncio
async def test_execute_node_tool_emits_file_output_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    org_dir = tmp_path / "orgs" / "org-x"
    import openakita.orgs._runtime_node_artifacts as _artifacts
    from openakita.orgs._runtime_agent_host import NodeToolHost

    monkeypatch.setattr(_artifacts, "_resolve_org_dir", lambda _g, _o: org_dir)

    events: list[tuple[str, dict]] = []

    async def emit(name: str, payload: dict) -> None:
        events.append((name, dict(payload)))

    agent = _FakeAgent(workspace=workspace)
    host = NodeToolHost(agent=agent, org_id="org-x")

    _text, is_err = await execute_node_tool(
        tool_name="write_file",
        tool_input={"path": "plan.md", "content": "# Real deliverable\n" + "x" * 500},
        org_id="org-x",
        node_id="writer-a",
        command_id="cmd-1",
        emit=emit,
        tool_host=host,
    )
    assert is_err is False
    reg = [p for (n, p) in events if n == "file_output_registered"]
    assert len(reg) == 1
    assert reg[0]["node_id"] == "writer-a"
    assert reg[0]["tool_name"] == "write_file"
    assert reg[0]["size_bytes"] > 500
    # and the file is recoverable for the executor
    got = pop_node_file_outputs("org-x", "cmd-1", "writer-a")
    assert len(got) == 1
    assert got[0]["path"].endswith("plan.md")


# ---------------------------------------------------------------------------
# 5) reliability: a ToolConfigError (e.g. web_search no working provider /
#    jina 401) degrades GRACEFULLY instead of 炸节点 + spinning.
# ---------------------------------------------------------------------------


class _ConfigErrorHandler:
    TOOLS = ["web_search"]

    def __call__(self, tool_name: str, params: dict[str, Any]) -> str:
        from openakita.tools.tool_hints import ToolConfigError

        raise ToolConfigError(
            scope="web_search",
            error_code="auth_failed",
            title="搜索源 API Key 无效",
            message="当前激活的搜索源拒绝了 API Key（401/403）。",
        )


class _ConfigErrorAgent:
    def __init__(self) -> None:
        from openakita.tools.handlers import SystemHandlerRegistry

        self.handler_registry = SystemHandlerRegistry()
        self.handler_registry.register(
            "web_search", _ConfigErrorHandler(), tool_names=["web_search"]
        )
        self._tools: list[dict[str, Any]] = []

    @property
    def brain(self) -> Any:
        return None


@pytest.mark.asyncio
async def test_execute_node_tool_degrades_on_config_error() -> None:
    from openakita.orgs._runtime_agent_host import NodeToolHost

    events: list[tuple[str, dict]] = []

    async def emit(name: str, payload: dict) -> None:
        events.append((name, dict(payload)))

    host = NodeToolHost(agent=_ConfigErrorAgent(), org_id="org-cfg")

    text, is_err = await execute_node_tool(
        tool_name="web_search",
        tool_input={"query": "市场调研"},
        org_id="org-cfg",
        node_id="data-analyst",
        command_id="cmd-cfg",
        emit=emit,
        tool_host=host,
    )
    # Degraded, NOT a hard error: the node can proceed with what it has.
    assert is_err is False
    assert "不要反复重试" in text
    # Still observable: a config_unavailable failed-event is emitted for the
    # blackboard/anomaly log (distinct from a generic handler_raised crash).
    failed = [p for (n, p) in events if n == "node_tool_failed"]
    assert len(failed) == 1
    assert failed[0]["reason"] == "config_unavailable"
    assert failed[0]["error_code"] == "auth_failed"

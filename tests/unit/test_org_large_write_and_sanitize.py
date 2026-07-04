"""Exploratory v23 reliability fixes:

* ``_sanitize_retrieval_result`` -- strip explicit-adult lines from retrieval
  tool results BEFORE they re-enter the LLM prompt, so a noisy web_search hit
  can no longer trip the cloud model's content-moderation gate
  (``data_inspection_failed``) and fail the whole node. Root-caused from the
  real ``data-analyst 任务失败`` events on org_e6ae7a9b374b/cmd2.

* ``append_file`` node tool -- engine-level enabler for writing LARGE
  documents (上万~数万字) in small chunks so no single tool-call JSON argument
  is large enough to be truncated in transit. write_file(open) + N*append_file
  assembles a complete file on disk, each call's payload bounded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from openakita.orgs._runtime_node_tools import (
    _sanitize_retrieval_result,
    execute_node_tool,
    resolve_node_tools,
)


# ---------------------------------------------------------------------------
# 1) Retrieval-result sanitizer (data-analyst moderation-failure root fix)
# ---------------------------------------------------------------------------


def test_sanitize_drops_explicit_adult_lines_from_web_search() -> None:
    raw = (
        "[搜索源: duckduckgo]\n"
        "**1. 《凡人修仙传》B站官方频道** https://b23.tv/x 播放量破亿，韩立成长线高燃剪辑\n"
        "**2. AI3D动漫凡人修仙传 泳池性爱口交深喉 后入** https://bad.example/x 肉棒内射\n"
        "**3. 凡人修仙传 角色设定考据** https://good.example/y 详解修仙体系\n"
    )
    clean, dropped = _sanitize_retrieval_result("web_search", raw)
    assert dropped == 1
    # on-topic lines survive
    assert "B站官方频道" in clean
    assert "角色设定考据" in clean
    # the explicit line is gone
    assert "性爱" not in clean and "肉棒" not in clean
    assert "已自动过滤" in clean


def test_sanitize_is_noop_for_clean_web_search() -> None:
    raw = "**1. 修仙题材市场分析** https://good.example 数据报告\n**2. B站播放趋势**"
    clean, dropped = _sanitize_retrieval_result("web_search", raw)
    assert dropped == 0
    assert clean == raw  # byte-for-byte unchanged on the common path


def test_sanitize_is_noop_for_non_retrieval_tool() -> None:
    # write_file content must NEVER be filtered (it's the user's deliverable).
    raw = "正文里恰好提到性爱话题的医学科普段落"
    clean, dropped = _sanitize_retrieval_result("write_file", raw)
    assert dropped == 0
    assert clean == raw


def test_sanitize_handles_browser_and_fetch_tools() -> None:
    raw = "正常结果\nxxx porn nsfw blowjob\n更多正常内容"
    for tool in ("web_fetch", "browser", "browser_navigate", "fetch"):
        clean, dropped = _sanitize_retrieval_result(tool, raw)
        assert dropped == 1, tool
        assert "正常结果" in clean and "更多正常内容" in clean


# ---------------------------------------------------------------------------
# 2) append_file is a first-class node file tool
# ---------------------------------------------------------------------------


def test_append_file_in_node_auto_file_tools() -> None:
    resolved = resolve_node_tools(external_tools=(), enable_file_tools=True)
    names = {t["name"] for t in resolved}
    assert "append_file" in names
    assert "write_file" in names
    spec = next(t for t in resolved if t["name"] == "append_file")
    assert spec["input_schema"]["required"] == ["path", "content"]


# ---------------------------------------------------------------------------
# 3) Handler-level: write_file + N*append_file assembles a COMPLETE large file
# ---------------------------------------------------------------------------


@pytest.fixture
def fs_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from openakita.tools.file import FileTool
    from openakita.tools.handlers.filesystem import FilesystemHandler

    class _Workspace:
        paths = [str(tmp_path)]

    class _Profile:
        current = "protect"

    class _Config:
        enabled = True
        workspace = _Workspace()
        profile = _Profile()

    monkeypatch.setattr("openakita.core.policy_v2.get_config_v2", lambda: _Config())
    agent = MagicMock()
    agent.file_tool = FileTool(base_path=str(tmp_path))
    agent.shell_tool = MagicMock()
    agent.allowed_roots = [str(tmp_path)]
    agent.default_cwd = str(tmp_path)
    return FilesystemHandler(agent), tmp_path


async def test_append_file_builds_large_document_without_truncation(fs_handler) -> None:
    handler, tmp_path = fs_handler
    target = str(tmp_path / "凡人修仙传_长篇方案.md")
    # Each chunk is small (well under the truncation-prone size); assembling many
    # of them yields a >30k-char document with zero parse errors.
    chunk = "# 章节\n" + ("韩立修炼凡人逆袭" * 250) + "\n"  # ~2k chars
    n_chunks = 20

    res0 = await handler.handle("write_file", {"path": target, "content": chunk})
    assert "已写入" in res0
    for _ in range(n_chunks - 1):
        res = await handler.handle("append_file", {"path": target, "content": chunk})
        assert "已追加" in res

    final = Path(target).read_text(encoding="utf-8")
    assert len(final) == len(chunk) * n_chunks
    assert len(final) > 30000
    # nothing got overwritten -- the marker appears once per chunk
    assert final.count("# 章节") == n_chunks


async def test_append_file_creates_missing_file(fs_handler) -> None:
    handler, tmp_path = fs_handler
    target = str(tmp_path / "new.md")
    res = await handler.handle("append_file", {"path": target, "content": "hello"})
    assert "已追加" in res
    assert Path(target).read_text(encoding="utf-8") == "hello"


async def test_append_file_rejects_truncated_preview(fs_handler) -> None:
    handler, tmp_path = fs_handler
    target = str(tmp_path / "x.md")
    res = await handler.handle(
        "append_file", {"path": target, "content": "partial [OUTPUT_TRUNCATED] tail"}
    )
    assert res.startswith("❌")
    assert "拒绝" in res


# ---------------------------------------------------------------------------
# 4) Integration: per-command sandbox + multi-call assembly via execute_node_tool
# ---------------------------------------------------------------------------


class _AppendCapableHandler:
    """Real (test-only) handler backed by a tmp dir, supporting write+append."""

    TOOLS = ["write_file", "append_file"]

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    def __call__(self, tool_name: str, params: dict[str, Any]) -> str:
        path = self._workspace / str(params.get("path") or "out.txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(params.get("content") or "")
        if tool_name == "write_file":
            path.write_text(content, encoding="utf-8")
            return f"wrote {path}"
        if tool_name == "append_file":
            with path.open("a", encoding="utf-8") as f:
                f.write(content)
            return f"appended {path}"
        raise ValueError(f"unsupported tool: {tool_name}")


class _FakeAgent:
    def __init__(self, workspace: Path) -> None:
        from openakita.tools.handlers import SystemHandlerRegistry

        self.handler_registry = SystemHandlerRegistry()
        self.handler_registry.register(
            "filesystem",
            _AppendCapableHandler(workspace),
            tool_names=_AppendCapableHandler.TOOLS,
        )
        self._tools: list[dict[str, Any]] = []

    @property
    def brain(self) -> Any:
        return None


async def test_execute_node_tool_appends_into_per_command_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    org_dir = tmp_path / "orgs" / "org-int"
    import openakita.orgs._runtime_node_artifacts as _artifacts
    from openakita.orgs._runtime_agent_host import NodeToolHost

    monkeypatch.setattr(_artifacts, "_resolve_org_dir", lambda _get, _org: org_dir)
    agent = _FakeAgent(workspace=workspace)
    host = NodeToolHost(agent=agent, org_id="org-int")

    common = dict(org_id="org-int", node_id="writer", command_id="cmd-XYZ", tool_host=host)
    r1, e1 = await execute_node_tool(
        tool_name="write_file",
        tool_input={"path": "big.md", "content": "PART1\n"},
        **common,
    )
    assert e1 is False
    for part in ("PART2\n", "PART3\n"):
        r, e = await execute_node_tool(
            tool_name="append_file",
            tool_input={"path": "big.md", "content": part},
            **common,
        )
        assert e is False

    target = org_dir / "commands" / "cmd-XYZ" / "artifacts" / "big.md"
    assert target.read_text(encoding="utf-8") == "PART1\nPART2\nPART3\n"
    # never leaked to the org-level dir (cross-command isolation preserved)
    assert not (org_dir / "artifacts" / "big.md").exists()


# ---------------------------------------------------------------------------
# 4b) Truncated tool-arg fast-fail + per-brain-call timeout (test13 fix a)
# ---------------------------------------------------------------------------


async def test_execute_node_tool_fast_fails_on_truncated_write_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # writer-b病根: the write_file arg JSON was truncated -> __parse_error__.
    # The handler must NOT run with that broken input; instead we return a crisp
    # append_file directive so the model self-corrects within its ReAct budget.
    import openakita.orgs._runtime_node_tools as nt
    from openakita.llm.converters.tools import PARSE_ERROR_KEY
    from openakita.tools.handlers import default_handler_registry

    calls = {"n": 0}

    async def _boom(name: str, inp: dict[str, Any]) -> str:
        calls["n"] += 1
        return "should never run"

    monkeypatch.setattr(default_handler_registry, "execute_by_tool", _boom)
    text, is_error = await nt.execute_node_tool(
        tool_name="write_file",
        tool_input={PARSE_ERROR_KEY: "⚠️ 你的 write_file 调用因内容过长被 API 截断"},
        org_id="o",
        node_id="writer-b",
        command_id="c",
        tool_host=None,
    )
    assert is_error is True
    assert "append_file" in text
    # the broken input never reached the handler (no phantom mis-execution)
    assert calls["n"] == 0


async def test_run_with_tools_fails_fast_on_hung_brain_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A single hung provider call must fail the node fast (RuntimeError) rather
    # than silently consuming the whole node activation budget to the 420s cap.
    import asyncio

    import openakita.orgs._runtime_node_tools as nt

    monkeypatch.setattr(nt, "MAX_LLM_CALL_S", 1)

    class _HangBrain:
        async def messages_create_async(self, **_kw: Any) -> Any:
            await asyncio.sleep(30)  # never returns before the 1s cap

    with pytest.raises(RuntimeError, match="hung provider"):
        await nt.run_with_tools(
            brain=_HangBrain(),
            system_prompt="s",
            user_content="u",
            tools=[{"name": "write_file", "description": "", "input_schema": {}}],
            org_id="o",
            node_id="writer-b",
            command_id="c",
        )


# ---------------------------------------------------------------------------
# 5) Node graceful degradation on content-moderation rejection (data-analyst ①)
# ---------------------------------------------------------------------------


def test_is_content_moderation_error_detects_dashscope_rejection() -> None:
    from openakita.orgs._default_agent_builder import _is_content_moderation_error

    moderation = RuntimeError(
        "All endpoints failed: dashscope-deepseek-r1: 云端模型的内容安全审核未通过 "
        "(HTTP 400, data_inspection_failed)。"
    )
    assert _is_content_moderation_error(moderation) is True
    # a transient network error must NOT be treated as moderation (still hard-fails)
    assert _is_content_moderation_error(TimeoutError("connection reset by peer")) is False


def test_node_degrades_instead_of_failing_on_moderation_rejection() -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from openakita.orgs._default_agent_builder import (
        DefaultAgentBuilder,
        _moderation_degraded_note,
    )
    from openakita.orgs._runtime_agent_pipeline import AgentSpec

    spec = AgentSpec(
        org_id="org_1",
        node_id="data-analyst",
        role="worker",
        persona="data analyst",
        enable_file_tools=False,
        external_tools=(),
    )
    brain = SimpleNamespace(
        messages_create_async=AsyncMock(
            side_effect=RuntimeError(
                "All endpoints failed: dashscope-deepseek-r1: 内容安全审核未通过 "
                "(HTTP 400, data_inspection_failed)。"
            )
        ),
        set_trace_context=lambda ctx: None,
    )
    builder = DefaultAgentBuilder(brain_provider=lambda: brain)
    agent = builder.build(spec)
    out = asyncio.run(agent.run("收集B站/抖音数据"))
    # The node did NOT raise -- it returned a structured degraded deliverable.
    assert out == _moderation_degraded_note(spec)
    assert "自动检索受限说明" in out
    assert "data-analyst" in out


def test_node_still_raises_on_non_moderation_error() -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import pytest as _pytest

    from openakita.orgs._default_agent_builder import DefaultAgentBuilder
    from openakita.orgs._runtime_agent_pipeline import AgentSpec

    spec = AgentSpec(
        org_id="org_1",
        node_id="n1",
        role="worker",
        persona="x",
        enable_file_tools=False,
        external_tools=(),
    )
    brain = SimpleNamespace(
        messages_create_async=AsyncMock(side_effect=RuntimeError("boom: socket closed")),
        set_trace_context=lambda ctx: None,
    )
    agent = DefaultAgentBuilder(brain_provider=lambda: brain).build(spec)
    with _pytest.raises(RuntimeError, match="boom"):
        asyncio.run(agent.run("hi"))

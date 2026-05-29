"""RC-5 S4 unit tests: next_speaker address resolution (gap②) + OrgV2 node
directory injection (gap④).

gap② -- the deliver layer must map a role-style ``next_speaker`` (e.g.
``"copywriter"``) to the concrete ``node_id`` the executor expects (e.g.
``"node_writer"``), while leaving the legacy PassThrough path (no directory)
byte-for-byte unchanged.

gap④ -- ``OrgCommandService._build_node_directory`` must read the real OrgV2
node list and produce ``NodeDescriptor`` records the orchestration brain can
reason over.
"""

from __future__ import annotations

from typing import Any

from openakita.runtime.llm_supervisor_brain import LLMSupervisorBrain, NodeDescriptor
from openakita.runtime.supervisor_factory import (
    _make_executor_deliver,
    _resolve_speaker_to_node_id,
)

_DIRECTORY = [
    NodeDescriptor(node_id="node_root", role="root", capabilities="entry"),
    NodeDescriptor(node_id="node_writer", role="copywriter", capabilities="copy"),
    NodeDescriptor(node_id="node_design", role="art_director", capabilities="visuals"),
    NodeDescriptor(node_id="node_qa", role="qa", capabilities="review"),
]


# ---------------------------------------------------------------------------
# gap② -- resolver correctness (role -> node_id)
# ---------------------------------------------------------------------------


def test_resolve_exact_node_id_wins() -> None:
    assert (
        LLMSupervisorBrain.resolve_next_speaker("node_writer", _DIRECTORY, "node_root")
        == "node_writer"
    )


def test_resolve_role_name_to_node_id() -> None:
    # The model answered with a role label; it must resolve to the node_id.
    assert (
        LLMSupervisorBrain.resolve_next_speaker("copywriter", _DIRECTORY, "node_root")
        == "node_writer"
    )
    assert (
        LLMSupervisorBrain.resolve_next_speaker("art_director", _DIRECTORY, "node_root")
        == "node_design"
    )


def test_resolve_substring_match() -> None:
    # Case-insensitive substring on role.
    assert (
        LLMSupervisorBrain.resolve_next_speaker("QA", _DIRECTORY, "node_root")
        == "node_qa"
    )


def test_resolve_unknown_falls_back_to_root() -> None:
    assert (
        LLMSupervisorBrain.resolve_next_speaker("nobody", _DIRECTORY, "node_root")
        == "node_root"
    )


def test_resolve_supervisor_sentinel_passthrough() -> None:
    # The terminal "supervisor" sentinel is never rewritten.
    assert (
        LLMSupervisorBrain.resolve_next_speaker("supervisor", _DIRECTORY, "node_root")
        == "supervisor"
    )


# ---------------------------------------------------------------------------
# gap② -- deliver-layer resolution + passthrough preservation
# ---------------------------------------------------------------------------


class _RecordingExecutor:
    """Mock executor recording the node_id each ``activate_and_run`` gets."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def activate_and_run(
        self, *, org_id: str, node_id: str, content: str, command_id: str, cancel_event=None
    ) -> dict[str, Any]:
        self.calls.append(node_id)
        return {"status": "ok", "output": f"{node_id} done"}


async def test_deliver_resolves_role_to_node_id_with_directory() -> None:
    executor = _RecordingExecutor()
    deliver = _make_executor_deliver(
        org_id="org1",
        command_id="cmd1",
        executor=executor,
        node_directory=_DIRECTORY,
        root_node_id="node_root",
    )
    # The brain emitted the role "copywriter"; the deliver layer must resolve
    # it to the concrete node_id "node_writer" before calling the executor.
    result = await deliver("copywriter", "draft the copy", None)
    assert executor.calls == ["node_writer"]
    assert result.success is True
    # The DelegationResult speaker reflects the resolved node_id.
    assert result.speaker == "node_writer"


async def test_deliver_passthrough_unchanged_without_directory() -> None:
    """No directory (legacy PassThrough path) -> speaker used verbatim."""
    executor = _RecordingExecutor()
    deliver = _make_executor_deliver(
        org_id="org1",
        command_id="cmd1",
        executor=executor,
    )
    await deliver("node_root", "do it", None)
    # Verbatim: no resolution, exactly the Sprint-9 behaviour.
    assert executor.calls == ["node_root"]


def test_resolve_speaker_helper_no_directory_is_verbatim() -> None:
    assert (
        _resolve_speaker_to_node_id(
            "copywriter", node_directory=None, root_node_id="node_root"
        )
        == "copywriter"
    )


def test_resolve_speaker_helper_with_directory() -> None:
    assert (
        _resolve_speaker_to_node_id(
            "copywriter", node_directory=_DIRECTORY, root_node_id="node_root"
        )
        == "node_writer"
    )


# ---------------------------------------------------------------------------
# gap④ -- OrgV2 node directory injection from the store
# ---------------------------------------------------------------------------


class _Node:
    def __init__(self, id_: str, role_title: str = "", role_goal: str = "", department: str = "") -> None:
        self.id = id_
        self.role_title = role_title
        self.role_goal = role_goal
        self.department = department


class _Org:
    def __init__(self, nodes: list[_Node]) -> None:
        self.nodes = nodes


class _Lookup:
    def __init__(self, org: _Org | None) -> None:
        self._org = org

    def get_org(self, org_id: str) -> _Org | None:
        return self._org


def _make_service_with_org(org: _Org | None):
    from unittest.mock import MagicMock

    from openakita.orgs.command_service import OrgCommandService

    rt = MagicMock()
    return OrgCommandService(rt, lookup=_Lookup(org))


def test_build_node_directory_maps_org_nodes() -> None:
    org = _Org(
        [
            _Node("node_root", role_title="root", role_goal="entry/coordination"),
            _Node("node_writer", role_title="copywriter", role_goal="write copy"),
            _Node("node_ops", role_title="ops", role_goal="", department="operations"),
        ]
    )
    svc = _make_service_with_org(org)
    directory = svc._build_node_directory("org1")
    assert directory is not None
    assert [d.node_id for d in directory] == ["node_root", "node_writer", "node_ops"]
    # role + capabilities mapping.
    by_id = {d.node_id: d for d in directory}
    assert by_id["node_writer"].role == "copywriter"
    assert by_id["node_writer"].capabilities == "write copy"
    # capabilities falls back to department when role_goal is empty.
    assert by_id["node_ops"].capabilities == "operations"


def test_build_node_directory_none_when_no_org() -> None:
    svc = _make_service_with_org(None)
    assert svc._build_node_directory("missing") is None


def test_build_node_directory_skips_blank_ids() -> None:
    org = _Org([_Node("", role_title="ghost"), _Node("node_a", role_title="real")])
    svc = _make_service_with_org(org)
    directory = svc._build_node_directory("org1")
    assert directory is not None
    assert [d.node_id for d in directory] == ["node_a"]

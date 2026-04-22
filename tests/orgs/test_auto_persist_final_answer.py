"""文件交付兜底（auto-persist final answer）关键路径测试。

覆盖：
1. ``runtime._react_trace_has_tool`` 的扫描语义；
2. ``runtime._register_file_output`` 成功登记后计数器 +1；
3. ``runtime._node_files_registered_in_task`` 不同 (org, node) 互不干扰；
4. ``tool_handler.auto_persist_node_final_answer``：
   - body 不足 200 字直接 None；
   - workspace=None 直接 None；
   - 正常路径走 _auto_persist_deliverable + _register_file_output 唯一登记入口，
     返回 ``{filename, file_path, file_size}``；
   - 文件确实落到 ``<workspace>/deliverables/`` 目录里（path-traversal 安全）；
5. ``Organization.auto_persist_final_answer`` 字段默认 True，序列化往返一致，
   旧 JSON（无该字段）反序列化保持默认 True。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.orgs.models import Organization
from openakita.orgs.runtime import OrgRuntime
from openakita.orgs.tool_handler import OrgToolHandler


# ---------------------------------------------------------------------------
# _react_trace_has_tool 静态语义
# ---------------------------------------------------------------------------


class TestReactTraceHasTool:
    def test_empty_trace_returns_false(self):
        assert OrgRuntime._react_trace_has_tool(None, "x") is False
        assert OrgRuntime._react_trace_has_tool([], "x") is False

    def test_empty_tool_name_returns_false(self):
        trace = [{"tool_calls": [{"name": "foo"}]}]
        assert OrgRuntime._react_trace_has_tool(trace, "") is False

    def test_tool_present_returns_true(self):
        trace = [
            {"tool_calls": [{"name": "write_file"}]},
            {"tool_calls": [{"name": "org_submit_deliverable"}]},
        ]
        assert (
            OrgRuntime._react_trace_has_tool(trace, "org_submit_deliverable")
            is True
        )

    def test_tool_absent_returns_false(self):
        trace = [{"tool_calls": [{"name": "write_file"}]}]
        assert (
            OrgRuntime._react_trace_has_tool(trace, "org_submit_deliverable")
            is False
        )

    def test_malformed_entries_safe(self):
        trace = [
            None,  # type: ignore[list-item]
            {},
            {"tool_calls": None},
            {"tool_calls": ["not-a-dict"]},  # type: ignore[list-item]
            {"tool_calls": [{"name": "submit"}]},
        ]
        assert OrgRuntime._react_trace_has_tool(trace, "submit") is True
        assert OrgRuntime._react_trace_has_tool(trace, "missing") is False


# ---------------------------------------------------------------------------
# _register_file_output 登记计数器
# ---------------------------------------------------------------------------


@pytest.fixture()
def runtime_with_blackboard(persisted_org, mock_runtime, org_dir, tmp_path):
    """裸 OrgRuntime 实例 + 真实黑板 / messenger，供 _register_file_output 用。"""
    rt = OrgRuntime.__new__(OrgRuntime)
    # 最小可用字段集合
    rt._manager = mock_runtime._manager
    rt._active_orgs = {persisted_org.id: persisted_org}
    rt._node_files_registered_in_task = {}
    rt._tool_handler = OrgToolHandler(rt)
    # 文件类型标签字典（来自原 __init__ 默认）
    rt._FILE_EXT_LABELS = {
        ".md": "Markdown",
        ".txt": "文本",
        ".png": "图片",
        ".pdf": "PDF",
    }

    # _register_file_output 内部会用 get_blackboard / _broadcast_ws
    rt.get_blackboard = mock_runtime.get_blackboard
    rt._broadcast_ws = mock_runtime._broadcast_ws
    return rt


class TestRegisterFileOutputCounter:
    def test_counter_starts_at_zero_and_increments(
        self, runtime_with_blackboard, persisted_org, tmp_path
    ):
        rt = runtime_with_blackboard
        ws = tmp_path / "ws"
        ws.mkdir()
        f = ws / "report.md"
        f.write_text("# Hello", encoding="utf-8")

        node_id = persisted_org.nodes[0].id
        cache_key = f"{persisted_org.id}:{node_id}"
        assert rt._node_files_registered_in_task.get(cache_key, 0) == 0

        registered = rt._register_file_output(
            persisted_org.id, node_id,
            chain_id=None,
            filename=None,
            file_path=str(f),
            workspace=ws,
        )
        assert registered is not None
        assert registered["filename"] == "report.md"
        assert rt._node_files_registered_in_task[cache_key] == 1

        registered2 = rt._register_file_output(
            persisted_org.id, node_id,
            chain_id=None,
            filename=None,
            file_path=str(f),
            workspace=ws,
        )
        assert registered2 is not None
        assert rt._node_files_registered_in_task[cache_key] == 2

    def test_missing_file_does_not_increment(
        self, runtime_with_blackboard, persisted_org, tmp_path
    ):
        rt = runtime_with_blackboard
        ws = tmp_path / "ws"
        ws.mkdir()
        ghost = ws / "nope.md"  # 不创建

        node_id = persisted_org.nodes[0].id
        cache_key = f"{persisted_org.id}:{node_id}"

        registered = rt._register_file_output(
            persisted_org.id, node_id,
            chain_id=None,
            filename=None,
            file_path=str(ghost),
            workspace=ws,
        )
        assert registered is None
        assert rt._node_files_registered_in_task.get(cache_key, 0) == 0

    def test_different_nodes_independent_counters(
        self, runtime_with_blackboard, persisted_org, tmp_path
    ):
        rt = runtime_with_blackboard
        ws = tmp_path / "ws"
        ws.mkdir()
        f = ws / "x.md"
        f.write_text("y", encoding="utf-8")

        node_a = persisted_org.nodes[0].id
        node_b = persisted_org.nodes[1].id

        rt._register_file_output(
            persisted_org.id, node_a,
            chain_id=None, filename=None, file_path=str(f), workspace=ws,
        )
        assert rt._node_files_registered_in_task[f"{persisted_org.id}:{node_a}"] == 1
        assert (
            rt._node_files_registered_in_task.get(
                f"{persisted_org.id}:{node_b}", 0
            )
            == 0
        )


# ---------------------------------------------------------------------------
# tool_handler.auto_persist_node_final_answer
# ---------------------------------------------------------------------------


class TestAutoPersistNodeFinalAnswer:
    def test_short_body_returns_none(
        self, runtime_with_blackboard, persisted_org, tmp_path
    ):
        rt = runtime_with_blackboard
        ws = tmp_path / "ws"
        ws.mkdir()
        out = rt._tool_handler.auto_persist_node_final_answer(
            org_id=persisted_org.id,
            node_id=persisted_org.nodes[0].id,
            chain_id=None,
            title="t",
            body="too short",
            workspace=ws,
        )
        assert out is None

    def test_workspace_none_returns_none(
        self, runtime_with_blackboard, persisted_org
    ):
        rt = runtime_with_blackboard
        out = rt._tool_handler.auto_persist_node_final_answer(
            org_id=persisted_org.id,
            node_id=persisted_org.nodes[0].id,
            chain_id=None,
            title="t",
            body="x" * 500,
            workspace=None,
        )
        assert out is None

    def test_normal_path_writes_file_and_increments_counter(
        self, runtime_with_blackboard, persisted_org, tmp_path
    ):
        rt = runtime_with_blackboard
        ws = tmp_path / "ws"
        ws.mkdir()
        node_id = persisted_org.nodes[0].id

        long_body = (
            "下面是一份 200+ 字符的长文回复，用于触发 auto_persist_node_final_answer。"
            * 10
        )
        out = rt._tool_handler.auto_persist_node_final_answer(
            org_id=persisted_org.id,
            node_id=node_id,
            chain_id="chain_xyz",
            title="测试交付物",
            body=long_body,
            workspace=ws,
        )
        assert out is not None
        # 必落到 deliverables/ 子目录，且文件真实存在
        p = Path(out["file_path"])
        assert p.exists()
        assert p.parent == (ws / "deliverables").resolve()
        assert p.suffix == ".md"
        # counter 通过唯一登记入口 +1
        cache_key = f"{persisted_org.id}:{node_id}"
        assert rt._node_files_registered_in_task[cache_key] == 1

    def test_path_traversal_in_title_is_neutralized(
        self, runtime_with_blackboard, persisted_org, tmp_path
    ):
        rt = runtime_with_blackboard
        ws = tmp_path / "ws"
        ws.mkdir()
        long_body = "x" * 500
        out = rt._tool_handler.auto_persist_node_final_answer(
            org_id=persisted_org.id,
            node_id=persisted_org.nodes[0].id,
            chain_id="c",
            title="../../etc/passwd",
            body=long_body,
            workspace=ws,
        )
        # 只要 _auto_persist_deliverable 没被绕出 deliverables/，就算稳
        assert out is not None
        p = Path(out["file_path"])
        deliverables_root = (ws / "deliverables").resolve()
        assert str(p).startswith(str(deliverables_root))


# ---------------------------------------------------------------------------
# Organization.auto_persist_final_answer 字段
# ---------------------------------------------------------------------------


class TestAutoPersistOrgSetting:
    def test_default_is_true(self):
        org = Organization(id="o1", name="n")
        assert org.auto_persist_final_answer is True

    def test_to_dict_includes_field(self):
        org = Organization(id="o1", name="n", auto_persist_final_answer=False)
        d = org.to_dict()
        assert d["auto_persist_final_answer"] is False

    def test_round_trip_preserves_field(self):
        org = Organization(id="o1", name="n", auto_persist_final_answer=False)
        restored = Organization.from_dict(org.to_dict())
        assert restored.auto_persist_final_answer is False

    def test_legacy_dict_without_field_defaults_true(self):
        # 旧 JSON 没有该字段时，dataclass 默认 True 应生效
        legacy = {"id": "o2", "name": "n", "nodes": [], "edges": []}
        restored = Organization.from_dict(legacy)
        assert restored.auto_persist_final_answer is True

"""Tests for OrgManager — CRUD, persistence, templates, schedules."""

from __future__ import annotations

import pytest

from openakita.orgs.manager import OrgManager
from openakita.orgs.models import (
    NodeSchedule,
    OrgStatus,
    ScheduleType,
)

from .conftest import make_edge, make_node, make_org


class TestOrgManagerCRUD:
    def test_create_and_get(self, org_manager: OrgManager):
        org = org_manager.create({"name": "测试公司", "description": "一个描述"})
        assert org.name == "测试公司"
        assert org.id.startswith("org_")

        loaded = org_manager.get(org.id)
        assert loaded is not None
        assert loaded.name == "测试公司"

    def test_create_with_nodes(self, org_manager: OrgManager):
        data = make_org(name="带节点").to_dict()
        org = org_manager.create(data)
        assert len(org.nodes) == 3
        assert len(org.edges) == 2

    def test_list_orgs(self, org_manager: OrgManager):
        org_manager.create({"name": "A"})
        org_manager.create({"name": "B"})
        result = org_manager.list_orgs()
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"A", "B"}

    def test_list_orgs_excludes_archived(self, org_manager: OrgManager):
        org = org_manager.create({"name": "X"})
        org_manager.archive(org.id)
        assert len(org_manager.list_orgs(include_archived=False)) == 0
        assert len(org_manager.list_orgs(include_archived=True)) == 1

    def test_update(self, org_manager: OrgManager):
        org = org_manager.create({"name": "旧名"})
        updated = org_manager.update(org.id, {"name": "新名"})
        assert updated.name == "新名"
        assert updated.updated_at != org.created_at

    def test_update_preserves_id(self, org_manager: OrgManager):
        org = org_manager.create({"name": "X"})
        updated = org_manager.update(org.id, {"id": "hacked", "name": "Y"})
        assert updated.id == org.id

    def test_update_nodes(self, org_manager: OrgManager):
        org = org_manager.create(make_org().to_dict())
        new_nodes = [{"id": "new_node", "role_title": "新角色"}]
        updated = org_manager.update(org.id, {"nodes": new_nodes})
        assert len(updated.nodes) == 1
        assert updated.nodes[0].role_title == "新角色"

    def test_delete(self, org_manager: OrgManager):
        org = org_manager.create({"name": "删除测试"})
        assert org_manager.delete(org.id) is True
        assert org_manager.get(org.id) is None

    def test_delete_nonexistent(self, org_manager: OrgManager):
        assert org_manager.delete("no_such_org") is False

    def test_archive(self, org_manager: OrgManager):
        org = org_manager.create({"name": "归档"})
        archived = org_manager.archive(org.id)
        assert archived.status == OrgStatus.ARCHIVED

    def test_duplicate(self, org_manager: OrgManager):
        orig = org_manager.create(make_org(name="原始").to_dict())
        copy = org_manager.duplicate(orig.id, new_name="副本")
        assert copy.id != orig.id
        assert copy.name == "副本"
        assert copy.status == OrgStatus.DORMANT
        assert len(copy.nodes) == len(orig.nodes)
        for n in copy.nodes:
            assert n.id not in {on.id for on in orig.nodes}

    def test_get_nonexistent(self, org_manager: OrgManager):
        assert org_manager.get("fake_id") is None


class TestDirectoryStructure:
    def test_init_dirs_creates_all_subdirs(self, org_manager: OrgManager):
        org = org_manager.create(make_org().to_dict())
        base = org_manager._org_dir(org.id)
        for sub in ["nodes", "policies", "memory", "events", "logs", "reports", "artifacts"]:
            assert (base / sub).is_dir()

    def test_node_dirs_created(self, org_manager: OrgManager):
        org = org_manager.create(make_org().to_dict())
        for node in org.nodes:
            nd = org_manager._node_dir(org.id, node.id)
            assert (nd / "identity").is_dir()
            assert (nd / "mcp_config.json").is_file()
            assert (nd / "schedules.json").is_file()

    def test_department_dirs(self, org_manager: OrgManager):
        org = org_manager.create(make_org().to_dict())
        base = org_manager._org_dir(org.id) / "departments"
        assert (base / "技术部").is_dir()
        assert (base / "管理层").is_dir()


class TestNodeSchedules:
    def test_empty_schedules(self, org_manager: OrgManager):
        org = org_manager.create(make_org().to_dict())
        schedules = org_manager.get_node_schedules(org.id, org.nodes[0].id)
        assert schedules == []

    def test_add_and_get(self, org_manager: OrgManager):
        org = org_manager.create(make_org().to_dict())
        nid = org.nodes[0].id
        s = NodeSchedule(name="巡检", schedule_type=ScheduleType.INTERVAL, interval_s=600, prompt="检查状态")
        org_manager.add_node_schedule(org.id, nid, s)

        result = org_manager.get_node_schedules(org.id, nid)
        assert len(result) == 1
        assert result[0].name == "巡检"

    def test_update_schedule(self, org_manager: OrgManager):
        org = org_manager.create(make_org().to_dict())
        nid = org.nodes[0].id
        s = NodeSchedule(name="旧名", prompt="旧指令")
        org_manager.add_node_schedule(org.id, nid, s)

        updated = org_manager.update_node_schedule(org.id, nid, s.id, {"name": "新名"})
        assert updated is not None
        assert updated.name == "新名"

    def test_delete_schedule(self, org_manager: OrgManager):
        org = org_manager.create(make_org().to_dict())
        nid = org.nodes[0].id
        s = NodeSchedule(name="临时")
        org_manager.add_node_schedule(org.id, nid, s)
        assert org_manager.delete_node_schedule(org.id, nid, s.id) is True
        assert org_manager.delete_node_schedule(org.id, nid, "fake") is False


class TestTemplates:
    def test_save_and_list(self, org_manager: OrgManager):
        org = org_manager.create({"name": "模板源"})
        tid = org_manager.save_as_template(org.id, "my-template")
        assert tid == "my-template"

        tpls = org_manager.list_templates()
        assert any(t["id"] == "my-template" for t in tpls)

    def test_create_from_template(self, org_manager: OrgManager):
        org = org_manager.create(make_org(name="源组织").to_dict())
        org_manager.save_as_template(org.id, "src-tpl")

        created = org_manager.create_from_template("src-tpl", {"name": "从模板创建"})
        assert created.name == "从模板创建"
        assert created.status == OrgStatus.DORMANT
        assert len(created.nodes) == 3

    def test_create_from_template_applies_readable_initial_layout(self, org_manager: OrgManager):
        nodes = [
            make_node("root", "负责人", position={"x": 0, "y": 0}),
            make_node("writer", "写手", position={"x": 0, "y": 0}),
            make_node("designer", "设计", position={"x": 0, "y": 0}),
        ]
        edges = [
            make_edge("root", "writer"),
            make_edge("root", "designer"),
        ]
        org = org_manager.create(make_org(name="乱坐标模板源", nodes=nodes, edges=edges).to_dict())
        org_manager.save_as_template(org.id, "messy-template")

        created = org_manager.create_from_template("messy-template")
        positions = {node.id: node.position for node in created.nodes}

        assert positions["root"] == {"x": 140, "y": 0}
        assert positions["writer"] == {"x": 0, "y": 180}
        assert positions["designer"] == {"x": 280, "y": 180}

    def test_create_from_nonexistent_template(self, org_manager: OrgManager):
        with pytest.raises(FileNotFoundError):
            org_manager.create_from_template("no-such-template")


class TestRuntimeState:
    def test_save_and_load_state(self, org_manager: OrgManager):
        org = org_manager.create({"name": "状态测试"})
        org_manager.save_state(org.id, {"active_nodes": ["n1"], "version": 2})
        state = org_manager.load_state(org.id)
        assert state["version"] == 2
        assert state["active_nodes"] == ["n1"]

    def test_load_state_empty(self, org_manager: OrgManager):
        org = org_manager.create({"name": "无状态"})
        assert org_manager.load_state(org.id) == {}

    def test_cache_invalidation(self, org_manager: OrgManager):
        org = org_manager.create({"name": "缓存"})
        assert org.id in org_manager._cache
        org_manager.invalidate_cache(org.id)
        assert org.id not in org_manager._cache


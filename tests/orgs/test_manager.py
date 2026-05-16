"""Tests for OrgManager — CRUD, persistence, templates, schedules."""

from __future__ import annotations

import pytest

from openakita.orgs.manager import OrgManager, OrgNameConflictError
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


class TestOrgNameUniqueness:
    """组织名字全局唯一约束。聊天/IM 端按名字调用必须依赖这条保证。"""

    def test_create_with_duplicate_name_raises(self, org_manager: OrgManager):
        org_manager.create({"name": "内容工作室"})
        with pytest.raises(OrgNameConflictError) as exc_info:
            org_manager.create({"name": "内容工作室"})
        assert exc_info.value.name == "内容工作室"
        assert exc_info.value.conflict_org_id

    def test_name_uniqueness_is_case_and_whitespace_insensitive(
        self, org_manager: OrgManager
    ):
        org_manager.create({"name": "Content Studio"})
        with pytest.raises(OrgNameConflictError):
            org_manager.create({"name": "  content studio  "})

    def test_create_empty_name_raises(self, org_manager: OrgManager):
        with pytest.raises(ValueError):
            org_manager.create({"name": "   "})

    def test_update_to_existing_name_raises(self, org_manager: OrgManager):
        org_manager.create({"name": "A"})
        b = org_manager.create({"name": "B"})
        with pytest.raises(OrgNameConflictError):
            org_manager.update(b.id, {"name": "A"})

    def test_update_keep_same_name_is_ok(self, org_manager: OrgManager):
        org = org_manager.create({"name": "保持原名"})
        updated = org_manager.update(org.id, {"name": "保持原名", "description": "改描述"})
        assert updated.name == "保持原名"
        assert updated.description == "改描述"

    def test_update_change_case_only_is_ok(self, org_manager: OrgManager):
        org = org_manager.create({"name": "MyOrg"})
        updated = org_manager.update(org.id, {"name": "myorg"})
        assert updated.name == "myorg"

    def test_duplicate_auto_suffix_when_default_name_taken(
        self, org_manager: OrgManager
    ):
        orig = org_manager.create({"name": "源组织"})
        first = org_manager.duplicate(orig.id)
        assert first.name == "源组织 (副本)"
        second = org_manager.duplicate(orig.id)
        assert second.name == "源组织 (副本) 2"

    def test_duplicate_with_explicit_conflict_name_raises(
        self, org_manager: OrgManager
    ):
        org_manager.create({"name": "已存在"})
        orig = org_manager.create({"name": "另一个"})
        with pytest.raises(OrgNameConflictError):
            org_manager.duplicate(orig.id, new_name="已存在")

    def test_create_from_template_auto_suffix_when_no_override(
        self, org_manager: OrgManager
    ):
        """未在 overrides 里指定 name 时，模板自带名撞了应自动加后缀。"""
        seed = org_manager.create({"name": "模板源 A"})
        org_manager.save_as_template(seed.id, "tpl-a")
        # 第二次从模板创建（不传 overrides.name）：自动加后缀
        copy1 = org_manager.create_from_template("tpl-a")
        assert copy1.name == "模板源 A (2)"
        copy2 = org_manager.create_from_template("tpl-a")
        assert copy2.name == "模板源 A (3)"

    def test_create_from_template_explicit_conflict_name_raises(
        self, org_manager: OrgManager
    ):
        """在 overrides 里显式指定的 name，撞名时应直接抛错——
        用户的明确意图不能被悄悄改成另一个名字。"""
        seed = org_manager.create({"name": "模板源 B"})
        org_manager.save_as_template(seed.id, "tpl-b")
        org_manager.create({"name": "占用名"})
        with pytest.raises(OrgNameConflictError):
            org_manager.create_from_template("tpl-b", overrides={"name": "占用名"})


class TestOrgNameResolution:
    """聊天 / IM 用：按"名字或 ID"找到唯一 org_id 的解析函数。"""

    def test_resolve_by_exact_id(self, org_manager: OrgManager):
        org = org_manager.create({"name": "测试"})
        org_id, candidates = org_manager.resolve_id_by_name_or_id(org.id)
        assert org_id == org.id
        assert candidates == []

    def test_resolve_by_unique_name(self, org_manager: OrgManager):
        org = org_manager.create({"name": "内容工作室"})
        org_id, candidates = org_manager.resolve_id_by_name_or_id("内容工作室")
        assert org_id == org.id
        assert candidates == []

    def test_resolve_by_name_is_case_and_whitespace_insensitive(
        self, org_manager: OrgManager
    ):
        org = org_manager.create({"name": "Content Studio"})
        org_id, _ = org_manager.resolve_id_by_name_or_id("  content STUDIO  ")
        assert org_id == org.id

    def test_resolve_unknown_returns_empty(self, org_manager: OrgManager):
        org_id, candidates = org_manager.resolve_id_by_name_or_id("根本不存在")
        assert org_id is None
        assert candidates == []

    def test_resolve_empty_query_returns_empty(self, org_manager: OrgManager):
        org_id, candidates = org_manager.resolve_id_by_name_or_id("")
        assert org_id is None
        assert candidates == []

    def test_find_by_name_excludes_self(self, org_manager: OrgManager):
        """改名时不应把自己算成"重名"——这是 update 不撞自己的关键。"""
        org = org_manager.create({"name": "唯一"})
        found_all = org_manager.find_by_name("唯一")
        assert len(found_all) == 1 and found_all[0]["id"] == org.id
        assert org_manager.find_by_name("唯一", exclude_org_id=org.id) == []


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


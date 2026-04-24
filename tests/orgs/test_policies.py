"""Tests for OrgPolicies — CRUD, search, index, templates."""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.orgs.policies import OrgPolicies, POLICY_TEMPLATES


@pytest.fixture()
def policies(org_dir: Path) -> OrgPolicies:
    return OrgPolicies(org_dir)


class TestPolicyCRUD:
    def test_write_and_read(self, policies: OrgPolicies):
        policies.write_policy("test-rule.md", "# 测试规则\n\n内容正文")
        content = policies.read_policy("test-rule.md")
        assert content is not None
        assert "测试规则" in content

    def test_read_nonexistent(self, policies: OrgPolicies):
        assert policies.read_policy("no-file.md") is None

    def test_delete(self, policies: OrgPolicies):
        policies.write_policy("to-delete.md", "# 删除")
        assert policies.delete_policy("to-delete.md") is True
        assert policies.read_policy("to-delete.md") is None

    def test_delete_nonexistent(self, policies: OrgPolicies):
        assert policies.delete_policy("nope.md") is False

    def test_list_policies(self, policies: OrgPolicies):
        policies.write_policy("a.md", "# A")
        policies.write_policy("b.md", "# B")
        result = policies.list_policies()
        names = {p["filename"] for p in result}
        assert "a.md" in names
        assert "b.md" in names
        assert "README.md" not in names

    def test_department_policy(self, policies: OrgPolicies):
        policies.write_policy("dept-rule.md", "# 部门规则", department="技术部")
        result = policies.list_policies(department="技术部")
        assert any(p["filename"] == "dept-rule.md" for p in result)

        content = policies.read_policy("dept-rule.md", department="技术部")
        assert "部门规则" in content

    def test_invalid_filename_rejected(self, policies: OrgPolicies):
        with pytest.raises(ValueError):
            policies.write_policy("../escape.md", "bad")
        with pytest.raises(ValueError):
            policies.write_policy("sub/dir.md", "bad")


class TestSearch:
    def test_search_finds_content(self, policies: OrgPolicies):
        policies.write_policy("code-review.md", "# 代码审查\n\n所有PR需要审查\n审查清单如下")
        results = policies.search("审查")
        assert len(results) >= 1
        assert results[0]["filename"] == "code-review.md"
        assert results[0]["match_count"] >= 2

    def test_search_case_insensitive(self, policies: OrgPolicies):
        policies.write_policy("test.md", "# Deploy\n\nauto deploy pipeline")
        results = policies.search("DEPLOY")
        assert len(results) >= 1

    def test_search_no_results(self, policies: OrgPolicies):
        policies.write_policy("x.md", "# X")
        assert policies.search("zzz_nonexistent") == []


class TestIndex:
    def test_ensure_index_creates_readme(self, policies: OrgPolicies):
        policies.write_policy("doc.md", "# 文档")
        policies.ensure_index()
        readme = policies._policies_dir / "README.md"
        assert readme.is_file()
        content = readme.read_text(encoding="utf-8")
        assert "doc.md" in content

    def test_index_excludes_readme(self, policies: OrgPolicies):
        policies.write_policy("rule.md", "# 规则")
        policies.ensure_index()
        listed = policies.list_policies()
        assert all(p["filename"] != "README.md" for p in listed)


class TestPolicyTemplates:
    def test_default_templates_exist(self):
        assert "default" in POLICY_TEMPLATES
        assert "software-team" in POLICY_TEMPLATES
        assert "content-ops" in POLICY_TEMPLATES

    def test_install_default(self, policies: OrgPolicies):
        count = policies.install_default_policies("default")
        assert count >= 2
        result = policies.list_policies()
        names = {p["filename"] for p in result}
        assert "communication-guidelines.md" in names

    def test_install_software_team(self, policies: OrgPolicies):
        count = policies.install_default_policies("software-team")
        assert count >= 1
        content = policies.read_policy("code-review.md")
        assert content is not None
        assert "代码审查" in content

    def test_install_idempotent(self, policies: OrgPolicies):
        policies.install_default_policies("default")
        count2 = policies.install_default_policies("default")
        assert count2 == 0


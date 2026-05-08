"""Fix-4 回归测试：SkillCatalog 的 priority_categories 模式与 helper。"""

from __future__ import annotations

from unittest.mock import MagicMock

from openakita.prompt.budget import (
    _TOOL_HINT_TO_SKILL_CATEGORY,
    intent_to_priority_categories,
)
from openakita.skills.catalog import SkillCatalog

# ---------------------------------------------------------------------------
# intent_to_priority_categories
# ---------------------------------------------------------------------------


def test_intent_to_priority_categories_empty_input():
    assert intent_to_priority_categories(None) == ()
    assert intent_to_priority_categories([]) == ()


def test_intent_to_priority_categories_known_hints():
    cats = intent_to_priority_categories(["File System", "Web Search"])
    assert "file-tools" in cats
    assert "web" in cats


def test_intent_to_priority_categories_unknown_hints_pass_through_empty():
    """未知 hint 不抛错，返回空 tuple — 让上游退回到全量展开。"""
    assert intent_to_priority_categories(["UnknownCategory"]) == ()


def test_intent_to_priority_categories_dedup_order():
    cats = intent_to_priority_categories(["Web Search", "Browser"])
    # web 同时出现在两个 hint 里 → 去重，但保留首次出现顺序
    assert cats.count("web") == 1
    assert cats.index("web") < cats.index("browser")


def test_tool_hint_table_has_expected_buckets():
    """防回归：table 至少覆盖以下基础类别。"""
    for required in ("file system", "web search", "shell", "memory"):
        assert required in _TOOL_HINT_TO_SKILL_CATEGORY


# ---------------------------------------------------------------------------
# SkillCatalog.get_grouped_compact_catalog priority_categories 行为
# ---------------------------------------------------------------------------


class _FakeSkill:
    def __init__(self, name: str, category: str, when: str = ""):
        self.name = name
        self.category = category
        self.when_to_use = when
        self.description = when or name


def _make_catalog(skills: list[_FakeSkill]) -> SkillCatalog:
    """Build a SkillCatalog with a stub registry that returns given skills."""
    registry = MagicMock()
    registry.count_catalog_hidden.return_value = 0
    cat = SkillCatalog(registry=registry)
    # Bypass _list_model_visible (depends on registry internals)
    cat._list_model_visible = lambda exposure_filter=None: skills
    return cat


def test_priority_categories_promotes_listed_demotes_others():
    skills = [
        _FakeSkill("read_file", "file-tools", "读文件"),
        _FakeSkill("write_file", "file-tools", "写文件"),
        _FakeSkill("web_search", "web", "搜索网页"),
        _FakeSkill("brewer", "coffee", "煮咖啡"),
    ]
    cat = _make_catalog(skills)

    out = cat.get_grouped_compact_catalog(
        max_tokens=0,
        priority_categories=("file-tools",),
    )

    # file-tools 是 priority — 必须出现详细描述（"读文件"）
    assert "读文件" in out
    # web 不是 priority — 应只出现名字 + (index) 后缀
    assert "(index)" in out
    assert "web_search" in out
    # coffee 也是 (index) 模式
    assert "brewer" in out


def test_priority_categories_none_keeps_legacy_full_expansion():
    skills = [
        _FakeSkill("read_file", "file-tools", "读文件"),
        _FakeSkill("web_search", "web", "搜索网页"),
    ]
    cat = _make_catalog(skills)

    out = cat.get_grouped_compact_catalog(max_tokens=0)

    # 没传 priority_categories — 旧行为：所有分类都详细
    assert "读文件" in out
    assert "搜索网页" in out
    assert "(index)" not in out


def test_priority_categories_cache_keys_are_distinct():
    """priority_categories 必须进 cache_key — 不同 priority 不能复用 cache。"""
    skills = [
        _FakeSkill("read_file", "file-tools", "读文件"),
        _FakeSkill("web_search", "web", "搜索网页"),
    ]
    cat = _make_catalog(skills)

    out_a = cat.get_grouped_compact_catalog(priority_categories=("file-tools",))
    out_b = cat.get_grouped_compact_catalog(priority_categories=("web",))

    # 两次输出应不同 — 不同 priority 选择
    assert out_a != out_b
    # 缓存里有两个独立 entry
    assert len(cat._cached_grouped) == 2


def test_priority_categories_empty_tuple_treated_as_none():
    """priority_categories=() 应等价于 None（不进入 mixed 模式）。"""
    skills = [_FakeSkill("read_file", "file-tools", "读文件")]
    cat = _make_catalog(skills)

    out = cat.get_grouped_compact_catalog(priority_categories=())
    assert "(index)" not in out
    assert "读文件" in out

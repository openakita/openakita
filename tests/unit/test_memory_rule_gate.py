from dataclasses import dataclass, field

from openakita.prompt.builder import _build_pinned_rules_section


@dataclass
class FakeRule:
    content: str
    scope: str = "global"
    source: str = "test"
    tags: list[str] = field(default_factory=list)
    subject: str = ""
    importance_score: float = 0.8
    confidence: float = 0.9
    superseded_by: str | None = None
    expires_at: object | None = None


class FakeStore:
    def __init__(self, rules):
        self.rules = rules

    def query_semantic(self, memory_type: str, limit: int = 20):
        assert memory_type == "rule"
        return self.rules[:limit]


class FakeMemoryManager:
    def __init__(self, rules):
        self.store = FakeStore(rules)


def test_pinned_rules_do_not_inject_unrelated_global_rules():
    memory = FakeMemoryManager([
        FakeRule("蓝豆计划预警线 55 元，熔断线 50 元", tags=["蓝豆计划"]),
    ])

    section = _build_pinned_rules_section(memory, task_description="总结 ClipSense 插件能力")

    assert section == ""


def test_pinned_rules_keep_general_behavior_rules():
    memory = FakeMemoryManager([
        FakeRule("始终使用简体中文回复"),
    ])

    section = _build_pinned_rules_section(memory, task_description="介绍项目")

    assert "始终使用简体中文回复" in section
    assert "general-behavior" in section

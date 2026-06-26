"""Fix-8 回归测试：fact 注入按 (subject, predicate) 去重。"""

from __future__ import annotations

from openakita.memory.retrieval import RetrievalCandidate, RetrievalEngine


def _fact(memory_id: str, subject: str, predicate: str, content: str, score: float = 1.0):
    return RetrievalCandidate(
        memory_id=memory_id,
        content=content,
        memory_type="fact",
        source_type="semantic",
        score=score,
        raw_data={"subject": subject, "predicate": predicate, "id": memory_id},
    )


def _episode(memory_id: str, content: str):
    return RetrievalCandidate(
        memory_id=memory_id,
        content=content,
        memory_type="episode",
        source_type="episode",
        raw_data={},
    )


def test_dedupe_keeps_first_per_subject_predicate():
    """两条 (zhang_san, age) — 第一条保留，第二条丢弃。"""
    candidates = [
        _fact("m1", "zhang_san", "age", "张三今年 35 岁。"),
        _fact("m2", "zhang_san", "age", "张三今年 32 岁。"),  # 历史/冲突
        _fact("m3", "zhang_san", "city", "张三在上海。"),
    ]

    out = RetrievalEngine._dedupe_facts_by_subject_predicate(candidates)
    ids = [c.memory_id for c in out]

    assert ids == ["m1", "m3"]


def test_dedupe_does_not_touch_non_fact_candidates():
    """episode/attachment/recent 等不去重 — 即便 raw_data 一致。"""
    e1 = _episode("e1", "上次在上海开会。")
    e2 = _episode("e2", "另一次在上海开会。")

    out = RetrievalEngine._dedupe_facts_by_subject_predicate([e1, e2])
    assert [c.memory_id for c in out] == ["e1", "e2"]


def test_dedupe_passes_through_facts_without_subject_metadata():
    """缺 raw_data.subject 的 fact 不被去重（保守行为）。"""
    fact_no_subj = RetrievalCandidate(
        memory_id="m1",
        content="x",
        memory_type="fact",
        source_type="semantic",
        raw_data={},  # no subject
    )
    fact_no_subj_2 = RetrievalCandidate(
        memory_id="m2",
        content="y",
        memory_type="fact",
        source_type="semantic",
        raw_data={},
    )
    out = RetrievalEngine._dedupe_facts_by_subject_predicate([fact_no_subj, fact_no_subj_2])
    assert [c.memory_id for c in out] == ["m1", "m2"]


def test_dedupe_case_insensitive_subject_predicate():
    candidates = [
        _fact("m1", "ZhangSan", "AGE", "case A"),
        _fact("m2", "zhangsan", "age", "case B"),
    ]
    out = RetrievalEngine._dedupe_facts_by_subject_predicate(candidates)
    assert [c.memory_id for c in out] == ["m1"]


def test_dedupe_preserves_order_for_distinct_subjects():
    """不同 subject — 全部保留，且顺序不变。"""
    c1 = _fact("m1", "user", "name", "name=Alice")
    c2 = _fact("m2", "user", "city", "city=Shanghai")
    c3 = _fact("m3", "project", "name", "name=OpenAkita")

    out = RetrievalEngine._dedupe_facts_by_subject_predicate([c1, c2, c3])
    assert [c.memory_id for c in out] == ["m1", "m2", "m3"]

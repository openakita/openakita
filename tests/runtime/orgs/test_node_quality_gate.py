"""Structured completion gate tests for organization node outputs."""

from __future__ import annotations

import pytest

from openakita.orgs._runtime_node_artifacts import classify_node_output


@pytest.mark.parametrize("text", ["", "   \n  "])
def test_classify_rejects_objectively_empty_output(text: str) -> None:
    assert classify_node_output(text) == ("incomplete", "empty_output")


@pytest.mark.parametrize(
    "text",
    [
        "交付/毛绒玩具跳舞视频/",
        "让我再搜索一下，结果不合适。",
        "现在我已收集到足够的数据，将整理完整的市场调研报告。",
        "不通过只是本文引用的审阅示例，并非当前裁决。",
        "最终视频已完成并交付。",
    ],
)
def test_prose_keywords_do_not_decide_completion(text: str) -> None:
    assert classify_node_output(text) == ("incomplete", "delivery_manifest_missing")


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("complete", ("ok", "")),
        ("in_progress", ("incomplete", "delivery_state_in_progress")),
        ("blocked", ("incomplete", "delivery_state_blocked")),
        ("failed", ("incomplete", "delivery_state_failed")),
        ("unknown", ("incomplete", "delivery_state_invalid")),
    ],
)
def test_manifest_state_is_authoritative(state: str, expected: tuple[str, str]) -> None:
    assert classify_node_output("任意非空说明", delivery_state=state) == expected

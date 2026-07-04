"""Exploratory v21 (2026-06): a node deliverable must never carry a leaked
``<thinking>…</thinking>`` chain-of-thought block into the persisted artifact,
the rendered PDF, the parent review sample, or the root ``final_message``.

A real multi-layer content-team run (org_e2cf326fbc0b) produced a root主编
deliverable that OPENED with the model's full reasoning::

    <thinking>用户要求我作为主编…我注意到一个问题：…</thinking>
    我将作为主编启动…
    # 家用人形陪伴机器人 内容运营方案
    …

Because a markdown heading follows the block, :func:`classify_node_output`
(correctly) accepts it as a "reasoning + document" deliverable — so the leak
survived into the 713 KB final PDF. :func:`strip_deliverable_thinking` is the
chokepoint that removes the reasoning block while preserving the document.
"""

from __future__ import annotations

import pytest

from openakita.orgs._runtime_node_artifacts import (
    classify_node_output,
    strip_deliverable_thinking,
)


def test_strips_closed_thinking_block_keeps_document() -> None:
    """case id: deliverable.thinking.closed_block_removed"""

    raw = (
        "<thinking>用户要求我作为主编整合各方产出，我注意到下属里没有 writer-a。"
        "我将向三个直接下属并行分派。</thinking>\n"
        "我将作为主编启动方案。\n\n"
        "# 家用人形陪伴机器人 内容运营方案\n\n## 市场速览\n竞品 A/B/C…"
    )
    cleaned = strip_deliverable_thinking(raw)
    assert "<thinking>" not in cleaned
    assert "</thinking>" not in cleaned
    assert "我注意到下属里没有 writer-a" not in cleaned
    assert "# 家用人形陪伴机器人 内容运营方案" in cleaned
    assert "竞品 A/B/C" in cleaned


def test_strips_multiple_blocks_and_think_variant() -> None:
    """case id: deliverable.thinking.multiple_and_think_variant"""

    raw = (
        "<think>先想想</think>正文一\n"
        "<thinking>再想想</thinking>正文二\n# 标题\n内容"
    )
    cleaned = strip_deliverable_thinking(raw)
    assert "先想想" not in cleaned and "再想想" not in cleaned
    assert "正文一" in cleaned and "正文二" in cleaned
    assert "# 标题" in cleaned


def test_unclosed_leading_tag_drops_up_to_heading() -> None:
    """case id: deliverable.thinking.unclosed_leading_to_heading"""

    raw = "<thinking>我先分析需求，这一段没有闭合标签\n继续推理\n# 真正的报告\n这是正文"
    cleaned = strip_deliverable_thinking(raw)
    assert "我先分析需求" not in cleaned
    assert cleaned.startswith("# 真正的报告")
    assert "这是正文" in cleaned


def test_pure_thinking_becomes_empty_then_rejected_by_gate() -> None:
    """case id: deliverable.thinking.pure_reasoning_empty"""

    raw = "<thinking>只有思考，没有任何成文成果，也没有标题</thinking>"
    cleaned = strip_deliverable_thinking(raw)
    assert cleaned == ""
    # downstream completeness gate then rejects the empty deliverable
    status, reason = classify_node_output(cleaned)
    assert status == "incomplete" and reason == "empty_output"


def test_unclosed_leading_tag_no_heading_becomes_empty() -> None:
    """case id: deliverable.thinking.unclosed_no_heading_empty"""

    raw = "<thinking>全是推理，没有闭合，也没有任何 markdown 标题，只是一段思考"
    assert strip_deliverable_thinking(raw) == ""


def test_plain_deliverable_unchanged() -> None:
    """case id: deliverable.thinking.no_tag_preserved"""

    raw = "# 方案\n\n这是一份正常的交付物，完全没有思考标签。\n\n## 小结\n完成。"
    assert strip_deliverable_thinking(raw) == raw.strip()


@pytest.mark.parametrize("bad", [None, "", 123, [], {}])
def test_non_string_or_empty_yields_empty(bad: object) -> None:
    """case id: deliverable.thinking.non_string_safe"""

    assert strip_deliverable_thinking(bad) == ""  # type: ignore[arg-type]

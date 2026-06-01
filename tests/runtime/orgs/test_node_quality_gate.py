"""Quality gate (test7 RCA 2026-06): incomplete node outputs must not be
registered as deliverables.

The single tool round (now lifted) made nodes "deliver" their raw
chain-of-thought. The real test7 (org_892cbfa35d6b) run produced these
artifacts that were wrongly named/registered as deliverables:

* ``visual``       135B  "thinking我看到当前目录下有一个 deliverables 目录…"
* ``data-analyst`` 161B  "thinking搜索结果不太理想，没有找到直接关于…"
* ``writer-b``     76B   "thinking好的，我获取了一些…让我再搜索一下…"
* ``writer-a``     189B  "thinking搜索结果中有很多不合适的同人内容…让我搜索更权威…"

:func:`classify_node_output` must flag all of these as ``incomplete`` while
NEVER rejecting a genuine deliverable (a structured markdown document).
"""

from __future__ import annotations

import pytest

from openakita.orgs._runtime_node_artifacts import classify_node_output


@pytest.mark.parametrize(
    "text",
    [
        "thinking我看到当前目录下有一个 `deliverables` 目录，这应该是存放交付物的地方。",
        "thinking搜索结果不太理想，没有找到直接关于《剑来》粉丝群体和仙侠网文线下活动的具体数据。",
        "thinking好的，我获取了一些关于《剑来》线下活动的信息。让我再搜索一下《剑来》原著的核心设定。",
        "thinking搜索结果中有很多不合适的同人内容，我需要获取更准确的原著信息。让我搜索更权威的来源。",
        "<thinking>用户要求我整合，让我先看看文件。</thinking>",
        "让我再搜索一下相关资料，目前的结果不太理想。",
        "",
        "   \n  ",
    ],
)
def test_classify_rejects_thinking_and_mid_iteration(text: str) -> None:
    """case id: quality.gate.rejects_thinking_only"""

    status, reason = classify_node_output(text)
    assert status == "incomplete", f"should reject incomplete output: {text!r} -> {reason}"


@pytest.mark.parametrize(
    "text",
    [
        # A real structured deliverable (markdown heading + body).
        "# 《剑来》线下交流会关键词优化方案\n\n## 核心关键词\n- 剑来\n- 粉丝见面会\n\n详细内容……",
        # A heading-led doc even if it mentions thinking inside the body.
        "## 活动策划案\n\n经过调研（thinking 略），最终方案如下：\n1. 签到\n2. 开场",
        # A genuine short factual answer (no reasoning lead, no next-step).
        "已完成关键词优化，核心词为：剑来、粉丝见面会、线下交流会。",
        # Long reasoning that nonetheless carries a heading => allowed.
        "thinking 我先分析需求。\n\n# 最终交付：策划案\n\n" + ("正文内容。" * 200),
    ],
)
def test_classify_accepts_real_deliverables(text: str) -> None:
    """case id: quality.gate.accepts_deliverables"""

    status, _reason = classify_node_output(text)
    assert status == "ok", f"should accept genuine deliverable: {text[:40]!r}"


def test_classify_thinking_leak_reason() -> None:
    """case id: quality.gate.reason_codes"""

    assert classify_node_output("thinking 略")[1] == "thinking_leak"
    assert classify_node_output("")[1] == "empty_output"
    assert classify_node_output("让我再搜索一下，结果不合适。")[1] == "mid_reasoning"

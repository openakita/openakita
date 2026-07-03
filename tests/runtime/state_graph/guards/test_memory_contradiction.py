"""Tests for runtime/state_graph/guards/memory_contradiction (F1)."""

from __future__ import annotations

from openakita.runtime.state_graph.guards.memory_contradiction import (
    ContradictionSignal,
    detect_memory_contradiction,
    format_contradiction_alert,
)


def _history() -> list[dict]:
    """The F1 reproduction: assistant recorded the correct division of labour."""
    return [
        {
            "role": "user",
            "content": "阿May 管吧台，小林 管收银和外卖",
            "timestamp": "2026-07-03T18:27:00",
        },
        {
            "role": "assistant",
            "content": "好的，我记住了：阿May 管吧台，小林 管收银外卖。",
            "timestamp": "2026-07-03T18:27:05",
        },
    ]


# --- Positive detection (the F1 failure) ------------------------------------


def test_detects_blind_reversal_challenge() -> None:
    signal = detect_memory_contradiction(
        "是小林管吧台、阿May管收银，你记反了吧？",
        _history(),
    )
    assert signal is not None
    # Grounded on the names / attributes that exist in history.
    assert any(t in {"小林", "吧台", "收银", "阿May", "May"} for t in signal.matched_terms)
    assert signal.evidence  # at least one historical snippet to cite


def test_evidence_prefers_assistant_original_with_timestamp() -> None:
    signal = detect_memory_contradiction(
        "小林管吧台才对吧，你搞反了",
        _history(),
    )
    assert signal is not None
    roles = [e["role"] for e in signal.evidence]
    assert "assistant" in roles
    # HH:MM extracted from the ISO timestamp.
    assert any(e["time"] == "18:27" for e in signal.evidence if e["role"] == "assistant")


def test_format_alert_includes_directive_and_evidence() -> None:
    signal = detect_memory_contradiction(
        "是小林管吧台、阿May管收银，你记反了吧？",
        _history(),
    )
    assert signal is not None
    text = format_contradiction_alert(signal)
    assert "先复述历史原文" in text
    assert "二次确认" in text
    assert "严禁" in text
    # The concrete historical snippet is handed to the model verbatim.
    assert "吧台" in text


def test_format_alert_accepts_dict_form() -> None:
    signal = detect_memory_contradiction("你记反了吧，小林管吧台", _history())
    assert signal is not None
    assert format_contradiction_alert(signal.to_dict()) == format_contradiction_alert(signal)


# --- Negative: genuine corrections must NOT trip the guard (no friction) -----


def test_genuine_new_correction_without_challenge_marker_is_silent() -> None:
    # Turn-6 style: honest correction, no "you got it wrong" accusation.
    history = [
        {"role": "user", "content": "我的店在西湖区", "timestamp": "2026-07-03T18:15:00"},
        {"role": "assistant", "content": "好的，记下了：西湖区。", "timestamp": "2026-07-03T18:15:03"},
    ]
    assert detect_memory_contradiction("地址改成拱墅区", history) is None
    assert detect_memory_contradiction("其实是拱墅区不是西湖区", history) is None


def test_first_person_self_correction_is_silent() -> None:
    # User admits their own mistake — not a challenge to the assistant's memory.
    assert detect_memory_contradiction("抱歉我记错了，是拱墅区", _history()) is None
    assert detect_memory_contradiction("是我搞反了，阿May管收银", _history()) is None


def test_challenge_without_historical_grounding_is_silent() -> None:
    # Reversal framing but the challenged content has no origin in history.
    history = [
        {"role": "assistant", "content": "今天杭州多云。", "timestamp": "2026-07-03T18:00:00"},
    ]
    assert detect_memory_contradiction("是老王管仓库，你记反了吧？", history) is None


def test_empty_message_and_empty_history() -> None:
    assert detect_memory_contradiction("", _history()) is None
    assert detect_memory_contradiction("你记反了吧", []) is None
    assert detect_memory_contradiction("你记反了吧", None) is None


def test_current_message_echo_in_history_not_self_grounded() -> None:
    # If the current message is already appended as the trailing turn, it must
    # not ground against itself.
    msg = "是老赵管账，你记反了吧？"
    history = [{"role": "user", "content": msg, "timestamp": "2026-07-03T18:40:00"}]
    assert detect_memory_contradiction(msg, history) is None


# --- Working-fact grounding -------------------------------------------------


def test_grounds_on_working_fact_value() -> None:
    signal = detect_memory_contradiction(
        "代号应该是ALPHA吧，你记错了",
        [],
        {"test_code": {"value": "BRAVO", "source_turn": 2}},
    )
    # No history and BRAVO not in message → not grounded on the fact value here;
    # ensure it stays silent rather than false-firing.
    assert signal is None

    signal2 = detect_memory_contradiction(
        "代号是BRAVO没错吧，你别记反了",
        [],
        {"test_code": {"value": "BRAVO", "source_turn": 2}},
    )
    assert signal2 is not None
    assert "BRAVO" in signal2.matched_terms


def test_signal_to_dict_roundtrip() -> None:
    sig = ContradictionSignal(matched_terms=["小林"], evidence=[{"role": "assistant", "time": "18:27", "snippet": "x"}])
    d = sig.to_dict()
    assert d["matched_terms"] == ["小林"]
    assert d["evidence"][0]["role"] == "assistant"

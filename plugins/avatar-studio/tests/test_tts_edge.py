"""Tests for avatar_tts_edge — voice catalog + mock synth."""

from __future__ import annotations

from avatar_tts_edge import EDGE_VOICES, EDGE_VOICES_BY_ID


def test_edge_voices_count() -> None:
    assert len(EDGE_VOICES) == 12


def test_edge_voices_have_required_keys() -> None:
    for v in EDGE_VOICES:
        assert "id" in v and "label" in v and "gender" in v
        assert v["id"].startswith("zh-CN-")
        assert v["gender"] in ("male", "female")


def test_edge_voices_by_id_lookup() -> None:
    assert "zh-CN-YunxiNeural" in EDGE_VOICES_BY_ID
    assert "zh-CN-XiaoxiaoNeural" in EDGE_VOICES_BY_ID
    assert EDGE_VOICES_BY_ID["zh-CN-YunxiNeural"]["gender"] == "male"


def test_edge_voices_unique_ids() -> None:
    ids = [v["id"] for v in EDGE_VOICES]
    assert len(ids) == len(set(ids))

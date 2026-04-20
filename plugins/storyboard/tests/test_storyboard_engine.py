"""Tests for storyboard 5-level parser & self-check."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from storyboard_engine import (  # noqa: E402
    Shot, Storyboard,
    parse_storyboard_llm_output, self_check,
    to_seedance_payload,
)


# ── parser ────────────────────────────────────────────────────────────


def test_parser_level1_clean_json() -> None:
    text = """{
      "title": "Test",
      "target_duration_sec": 10,
      "shots": [
        {"index": 1, "duration_sec": 5, "visual": "A"},
        {"index": 2, "duration_sec": 5, "visual": "B"}
      ]
    }"""
    sb = parse_storyboard_llm_output(text)
    assert sb.title == "Test"
    assert len(sb.shots) == 2
    assert sb.shots[0].visual == "A"


def test_parser_level2_fenced_json() -> None:
    text = "Sure!\n```json\n{\"title\":\"X\",\"target_duration_sec\":5,\"shots\":[{\"index\":1,\"duration_sec\":5,\"visual\":\"V\"}]}\n```\nDone!"
    sb = parse_storyboard_llm_output(text)
    assert sb.title == "X"
    assert sb.shots[0].visual == "V"


def test_parser_level3_embedded_json() -> None:
    text = "Here you go: {\"title\":\"Y\",\"shots\":[{\"index\":1,\"duration_sec\":3,\"visual\":\"hello\"}]} bye"
    sb = parse_storyboard_llm_output(text)
    assert sb.title == "Y"


def test_parser_level4_numbered_list() -> None:
    text = """1. 镜头一：主角推门进入
    2. 镜头二：镜头跟随到桌前
    3. 镜头三：特写键盘上的猫
    """
    sb = parse_storyboard_llm_output(text, fallback_duration=30)
    assert len(sb.shots) == 3
    assert sb.shots[0].visual.startswith("镜头一")


def test_parser_level5_total_garbage() -> None:
    text = "Sorry, I can't help with that."
    sb = parse_storyboard_llm_output(text, fallback_title="X", fallback_duration=10)
    assert len(sb.shots) == 1
    assert "fallback" in sb.style_notes.lower()


def test_parser_empty_input() -> None:
    sb = parse_storyboard_llm_output("", fallback_title="Z", fallback_duration=5)
    assert sb.title == "Z"
    assert len(sb.shots) == 1


# ── self-check ────────────────────────────────────────────────────────


def test_self_check_passes_balanced() -> None:
    sb = Storyboard(title="T", target_duration_sec=30, shots=[
        Shot(index=i, duration_sec=5, visual=f"shot {i}") for i in range(1, 7)
    ])
    out = self_check(sb)
    assert out.ok
    assert out.duration_match.startswith("✓")
    assert out.distribution_balance.startswith("✓")
    assert out.minimum_count.startswith("✓")
    assert out.suggestions == []


def test_self_check_flags_clustered_shots() -> None:
    sb = Storyboard(title="T", target_duration_sec=30, shots=[
        Shot(index=1, duration_sec=20, visual="A"),
        Shot(index=2, duration_sec=5, visual="B"),
        Shot(index=3, duration_sec=5, visual="C"),
    ])
    out = self_check(sb)
    assert "⚠" in out.distribution_balance
    assert any("分布" in s for s in out.suggestions)


def test_self_check_flags_too_few_shots() -> None:
    sb = Storyboard(title="T", target_duration_sec=30, shots=[
        Shot(index=1, duration_sec=15, visual="A"),
        Shot(index=2, duration_sec=15, visual="B"),
    ])
    out = self_check(sb)
    assert "⚠" in out.minimum_count


# ── seedance export ───────────────────────────────────────────────────


def _sample_storyboard() -> Storyboard:
    return Storyboard(
        title="测试分镜",
        target_duration_sec=15,
        style_notes="电影感",
        shots=[
            Shot(index=1, duration_sec=5,
                 visual="主角推门进入房间", camera="跟拍",
                 sound="脚步声", notes="emotional"),
            Shot(index=2, duration_sec=4,
                 visual="特写打开笔记本", camera="特写"),
            Shot(index=3, duration_sec=6,
                 visual="窗外日落", camera="固定", sound="轻音乐"),
        ],
    )


def test_seedance_export_basic_shape() -> None:
    payload = to_seedance_payload(_sample_storyboard())
    assert payload["title"] == "测试分镜"
    assert payload["model"].startswith("doubao-seedance")
    assert payload["ratio"] == "16:9"
    assert payload["resolution"] == "720p"
    assert payload["shot_count"] == 3
    assert len(payload["shots"]) == 3
    assert len(payload["cli_examples"]) == 3


def test_seedance_export_shot_prompt_combines_fields() -> None:
    payload = to_seedance_payload(_sample_storyboard())
    first = payload["shots"][0]
    assert first["index"] == 1
    assert first["duration"] == 5
    p = first["prompt"]
    assert "主角推门进入房间" in p
    assert "镜头: 跟拍" in p
    assert "音效: 脚步声" in p
    assert "风格: 电影感" in p


def test_seedance_export_clamps_too_short_duration() -> None:
    sb = Storyboard(title="T", target_duration_sec=2, shots=[
        Shot(index=1, duration_sec=0.5, visual="A"),
    ])
    out = to_seedance_payload(sb)
    assert out["shots"][0]["duration"] == 2


def test_seedance_export_clamps_too_long_duration() -> None:
    sb = Storyboard(title="T", target_duration_sec=60, shots=[
        Shot(index=1, duration_sec=120, visual="A"),
    ])
    out = to_seedance_payload(sb)
    assert out["shots"][0]["duration"] == 15


def test_seedance_export_cli_examples_are_paste_ready() -> None:
    payload = to_seedance_payload(_sample_storyboard())
    cmd = payload["cli_examples"][0]
    assert cmd.startswith("python scripts/seedance.py create")
    assert '--prompt "' in cmd
    assert "--duration 5" in cmd
    assert "--ratio 16:9" in cmd
    assert "--wait" in cmd


def test_seedance_export_escapes_quotes_in_prompt() -> None:
    sb = Storyboard(title="T", target_duration_sec=5, shots=[
        Shot(index=1, duration_sec=5, visual='女主角说"你好"'),
    ])
    out = to_seedance_payload(sb)
    cmd = out["cli_examples"][0]
    # Embedded double quotes must be backslash-escaped so a paste into bash
    # does not terminate the outer quoted string early.
    assert r'\"你好\"' in cmd


def test_seedance_export_custom_model_and_ratio_propagate() -> None:
    payload = to_seedance_payload(
        _sample_storyboard(),
        model="doubao-seedance-1-5-pro-251215",
        ratio="9:16",
        resolution="1080p",
    )
    assert payload["model"] == "doubao-seedance-1-5-pro-251215"
    assert payload["ratio"] == "9:16"
    assert payload["resolution"] == "1080p"
    assert all(
        s["model"] == "doubao-seedance-1-5-pro-251215"
        for s in payload["shots"]
    )
    assert all(s["ratio"] == "9:16" for s in payload["shots"])


def test_seedance_export_handles_empty_shotlist() -> None:
    sb = Storyboard(title="Empty", target_duration_sec=10, shots=[])
    out = to_seedance_payload(sb)
    assert out["shot_count"] == 0
    assert out["shots"] == []
    assert out["cli_examples"] == []


def test_seedance_export_blank_visual_falls_back_to_placeholder() -> None:
    sb = Storyboard(title="T", target_duration_sec=5, shots=[
        Shot(index=1, duration_sec=5, visual="", camera="", sound=""),
    ])
    out = to_seedance_payload(sb)
    assert out["shots"][0]["prompt"] == "一段画面"

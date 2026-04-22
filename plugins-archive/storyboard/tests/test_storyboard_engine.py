"""Tests for storyboard 5-level parser & self-check."""
# --- _shared bootstrap (auto-inserted by archive cleanup) ---
import sys as _sys
import pathlib as _pathlib
_archive_root = _pathlib.Path(__file__).resolve()
for _p in _archive_root.parents:
    if (_p / '_shared' / '__init__.py').is_file():
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
        break
del _sys, _pathlib, _archive_root
# --- end bootstrap ---

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from storyboard_engine import (  # noqa: E402
    Shot, Storyboard,
    parse_storyboard_llm_output, self_check,
    to_seedance_payload, to_tongyi_payload, to_verification,
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


# ── seedance dual-mode (Sprint 7) ─────────────────────────────────────


def test_seedance_export_includes_post_and_curl_examples() -> None:
    """Sprint 7 dual-mode: every shot must produce one CLI line, one POST
    body and one curl example so the storyboard → seedance handoff covers
    both the standalone CLI and the in-process plugin REST API."""
    payload = to_seedance_payload(_sample_storyboard())
    assert len(payload["cli_examples"]) == 3
    assert len(payload["post_examples"]) == 3
    assert len(payload["curl_examples"]) == 3
    assert payload["plugin_model"] == "2.0"


def test_seedance_export_post_body_matches_plugin_create_task_schema() -> None:
    """post_examples[*].body must be POSTable verbatim to the
    plugins/seedance-video CreateTaskBody schema (model is the plugin
    short id, not the Ark id)."""
    payload = to_seedance_payload(_sample_storyboard())
    post = payload["post_examples"][0]
    assert post["endpoint"] == "/api/plugins/seedance-video/tasks"
    assert post["method"] == "POST"
    body = post["body"]
    assert body["mode"] == "t2v"
    assert body["model"] == "2.0"
    assert body["ratio"] == "16:9"
    assert body["resolution"] == "720p"
    assert body["duration"] == 5
    assert body["n"] == 1
    assert body["generate_audio"] is True
    assert "主角推门进入房间" in body["prompt"]


def test_seedance_export_curl_examples_are_paste_ready() -> None:
    payload = to_seedance_payload(_sample_storyboard())
    curl = payload["curl_examples"][0]
    assert curl.startswith("curl -X POST '/api/plugins/seedance-video/tasks'")
    assert "Content-Type: application/json" in curl
    # Body is single-quoted JSON; the plugin endpoint appears in the URL,
    # not the body.
    assert "'{" in curl and "}'" in curl


def test_seedance_export_curl_escapes_single_quote_in_prompt() -> None:
    """Posix-quoted curl: a single quote in the prompt must close the
    outer ' and reopen with '"'"' so the shell never sees an unbalanced
    quote.  Without this, ``it's`` would chop the body in half."""
    sb = Storyboard(title="T", target_duration_sec=5, shots=[
        Shot(index=1, duration_sec=5, visual="it's a test"),
    ])
    out = to_seedance_payload(sb)
    curl = out["curl_examples"][0]
    # The escape sequence proves we did the close-reopen dance.
    assert "'\"'\"'" in curl


def test_seedance_export_plugin_model_is_overridable() -> None:
    """plugin_model is independent from the Ark model id (so users can
    target the lite tier via the plugin while keeping the CLI on pro)."""
    payload = to_seedance_payload(
        _sample_storyboard(),
        model="doubao-seedance-1-5-pro-251215",
        plugin_model="lite",
    )
    assert payload["model"] == "doubao-seedance-1-5-pro-251215"
    assert payload["plugin_model"] == "lite"
    assert all(p["body"]["model"] == "lite" for p in payload["post_examples"])
    # CLI lines still carry the Ark id, not "lite".
    assert all(
        "doubao-seedance-1-5-pro-251215" in c
        for c in payload["cli_examples"]
    )


def test_seedance_export_empty_shotlist_yields_empty_dual_arrays() -> None:
    sb = Storyboard(title="Empty", target_duration_sec=10, shots=[])
    out = to_seedance_payload(sb)
    assert out["post_examples"] == []
    assert out["curl_examples"] == []


# ── tongyi-image export ───────────────────────────────────────────────


def test_tongyi_export_basic_shape() -> None:
    payload = to_tongyi_payload(_sample_storyboard())
    assert payload["title"] == "测试分镜"
    assert payload["model"] == "wan27-pro"
    assert payload["size"] == "1024*1024"
    assert payload["n"] == 1
    assert payload["shot_count"] == 3
    assert len(payload["shots"]) == 3
    assert len(payload["post_examples"]) == 3
    assert len(payload["curl_examples"]) == 3


def test_tongyi_export_shot_prompt_combines_visual_and_camera() -> None:
    payload = to_tongyi_payload(_sample_storyboard())
    first = payload["shots"][0]
    assert first["index"] == 1
    assert first["model"] == "wan27-pro"
    assert first["size"] == "1024*1024"
    assert first["n"] == 1
    assert first["mode"] == "text2img"
    p = first["prompt"]
    assert "主角推门进入房间" in p
    assert "构图: 跟拍" in p
    assert "风格: 电影感" in p
    # Sound is stills-irrelevant and must be dropped from the prompt.
    assert "音效" not in p
    assert "脚步声" not in p


def test_tongyi_export_omits_sound_and_dialogue() -> None:
    sb = Storyboard(title="T", target_duration_sec=5, style_notes="", shots=[
        Shot(index=1, duration_sec=5,
             visual="A man at a desk", camera="近景",
             dialogue="Hello", sound="ambient noise"),
    ])
    out = to_tongyi_payload(sb)
    p = out["shots"][0]["prompt"]
    assert "A man at a desk" in p
    assert "构图: 近景" in p
    assert "Hello" not in p
    assert "ambient noise" not in p


def test_tongyi_export_post_examples_match_create_task_body() -> None:
    payload = to_tongyi_payload(_sample_storyboard())
    pe = payload["post_examples"][0]
    assert pe["path"] == "/api/plugins/tongyi-image/tasks"
    body = pe["body"]
    # Body keys must match plugins/tongyi-image/plugin.py CreateTaskBody so
    # the POST is accepted verbatim — guard against future renames.
    assert set(body.keys()) == {"mode", "prompt", "model", "size", "n"}
    assert body["mode"] == "text2img"
    assert body["model"] == "wan27-pro"
    assert body["size"] == "1024*1024"


def test_tongyi_export_curl_examples_are_paste_ready() -> None:
    payload = to_tongyi_payload(_sample_storyboard())
    cmd = payload["curl_examples"][0]
    assert cmd.startswith("curl -X POST http://localhost:8000/api/plugins/tongyi-image/tasks")
    assert "-H 'content-type: application/json'" in cmd
    # Single-quoted body wrapper survives the JSON content (which uses
    # double-quotes), and the shell command stays on a single line.
    assert "-d '" in cmd and cmd.endswith("'")


def test_tongyi_export_escapes_single_quotes_in_prompt() -> None:
    sb = Storyboard(title="T", target_duration_sec=5, shots=[
        Shot(index=1, duration_sec=5, visual="it's a test"),
    ])
    out = to_tongyi_payload(sb)
    cmd = out["curl_examples"][0]
    # Embedded single-quote must be POSIX-escaped as ``'\''`` so the outer
    # single-quoted body does not terminate early when pasted into bash.
    assert r"'\''" in cmd


def test_tongyi_export_clamps_n_to_valid_range() -> None:
    sb = _sample_storyboard()
    assert to_tongyi_payload(sb, n=0)["n"] == 1
    assert to_tongyi_payload(sb, n=-5)["n"] == 1
    assert to_tongyi_payload(sb, n=10)["n"] == 4
    assert to_tongyi_payload(sb, n=2)["n"] == 2
    # Per-shot n mirrors the top-level value.
    assert to_tongyi_payload(sb, n=3)["shots"][0]["n"] == 3


def test_tongyi_export_custom_model_and_size_propagate() -> None:
    payload = to_tongyi_payload(
        _sample_storyboard(),
        model="qwen-pro",
        size="2048*2048",
        n=2,
    )
    assert payload["model"] == "qwen-pro"
    assert payload["size"] == "2048*2048"
    assert payload["n"] == 2
    assert all(s["model"] == "qwen-pro" for s in payload["shots"])
    assert all(s["size"] == "2048*2048" for s in payload["shots"])
    assert all(s["n"] == 2 for s in payload["shots"])
    assert all(
        pe["body"]["model"] == "qwen-pro" for pe in payload["post_examples"]
    )


def test_tongyi_export_handles_empty_shotlist() -> None:
    sb = Storyboard(title="Empty", target_duration_sec=10, shots=[])
    out = to_tongyi_payload(sb)
    assert out["shot_count"] == 0
    assert out["shots"] == []
    assert out["post_examples"] == []
    assert out["curl_examples"] == []


def test_tongyi_export_blank_visual_falls_back_to_placeholder() -> None:
    sb = Storyboard(title="T", target_duration_sec=5, shots=[
        Shot(index=1, duration_sec=5, visual="", camera="", sound=""),
    ])
    out = to_tongyi_payload(sb)
    assert out["shots"][0]["prompt"] == "一张画面"


def test_tongyi_export_size_uses_dashscope_star_separator() -> None:
    # Guard against a regression where someone "fixes" the size to use
    # ``x`` (PIL/CSS convention) — DashScope rejects 1024x1024 with 422.
    payload = to_tongyi_payload(_sample_storyboard())
    assert "*" in payload["size"]
    assert "x" not in payload["size"].lower().replace("text", "")
    for s in payload["shots"]:
        assert "*" in s["size"]
    for pe in payload["post_examples"]:
        assert "*" in pe["body"]["size"]


# ── D2.10 verification bridge (Sprint 9) ─────────────────────────────


def _balanced_storyboard() -> Storyboard:
    """A storyboard built specifically to satisfy ``self_check.ok`` —
    one shot per third of the timeline, exact duration match, ≥
    ``ceil(target/6)`` shots."""
    return Storyboard(
        title="balanced", target_duration_sec=18,
        shots=[
            Shot(index=1, duration_sec=6, visual="A"),
            Shot(index=2, duration_sec=6, visual="B"),
            Shot(index=3, duration_sec=6, visual="C"),
        ],
    )


def test_to_verification_clean_storyboard_is_green() -> None:
    """A balanced storyboard whose self-check returns ``ok=True`` must
    yield a verified=True envelope with no flagged fields."""
    from _shared import BADGE_GREEN
    sb = _balanced_storyboard()
    check = self_check(sb)
    assert check.ok is True  # sanity: balanced sample passes
    v = to_verification(sb, check)
    assert v.verified is True
    assert v.low_confidence_fields == []
    assert v.badge == BADGE_GREEN
    assert v.verifier_id == "self_check"


def test_to_verification_flags_minimum_count_warning() -> None:
    """Two huge shots over a 30-second target trip the ``minimum_count``
    rule — verifier must flag the shots list so the UI highlights it."""
    from _shared import BADGE_YELLOW, KIND_OTHER
    sb = Storyboard(title="T", target_duration_sec=30, shots=[
        Shot(index=1, duration_sec=15, visual="A"),
        Shot(index=2, duration_sec=15, visual="B"),
    ])
    check = self_check(sb)
    v = to_verification(sb, check)
    assert v.badge == BADGE_YELLOW
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.storyboard.shots" in paths
    shots_field = next(f for f in v.low_confidence_fields
                       if f.path == "$.storyboard.shots")
    assert shots_field.kind == KIND_OTHER
    assert "镜头" in shots_field.reason


def test_to_verification_flags_duration_mismatch() -> None:
    """Self-check warns when actual duration drifts > 10% from target —
    verifier exposes this as a flag on ``target_duration_sec`` so the
    duration cell turns yellow."""
    from _shared import KIND_NUMBER
    sb = Storyboard(title="T", target_duration_sec=30, shots=[
        Shot(index=1, duration_sec=4, visual="A"),
        Shot(index=2, duration_sec=4, visual="B"),
        Shot(index=3, duration_sec=4, visual="C"),
        Shot(index=4, duration_sec=4, visual="D"),
        Shot(index=5, duration_sec=4, visual="E"),
    ])  # actual=20 vs target=30 → 33% drift
    check = self_check(sb)
    v = to_verification(sb, check)
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.storyboard.target_duration_sec" in paths
    dur_field = next(f for f in v.low_confidence_fields
                     if f.path == "$.storyboard.target_duration_sec")
    assert dur_field.kind == KIND_NUMBER
    assert dur_field.value == 30
    # Reason should embed the actual / target the engine produced.
    assert "20" in dur_field.reason and "30" in dur_field.reason


def test_to_verification_dict_is_json_serializable() -> None:
    """The verification dict goes into the task's persisted JSON blob —
    must round-trip through json.dumps without TypeError."""
    import json
    sb = _sample_storyboard()
    v = to_verification(sb, self_check(sb))
    text = json.dumps(v.to_dict(), ensure_ascii=False)
    re_loaded = json.loads(text)
    assert re_loaded["verifier_id"] == "self_check"
    assert "field_count_by_kind" in re_loaded

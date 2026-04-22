"""Unit tests for ``bgm_engine`` — pure functions only, no I/O.

Coverage matrix:

* 5-level fallback parser (each level individually + edge cases)
* coercion helpers (bpm clamp, tempo label derivation, list shaping)
* ``self_check`` quality gate (each issue code + clean case)
* bridge exports (CSV / Suno / search queries / bundle)
* prompt builder (deterministic shape, optional fields)
* stub helper (deterministic, parser-roundtrip safe)

These tests do NOT exercise ``plugin.py`` — that needs the full
PluginAPI host environment.  They DO guard the engine contract that
``plugin.py`` depends on, so any future engine refactor is caught here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from bgm_engine import (  # noqa: E402
    BPM_HARD_MAX,
    BPM_HARD_MIN,
    KNOWN_TEMPO_LABELS,
    BgmBrief,
    SelfCheck,
    build_user_prompt,
    parse_bgm_llm_output,
    self_check,
    stub_brief_text,
    to_csv,
    to_export_payload,
    to_search_queries,
    to_suno_prompt,
    to_verification,
)

# ── parser: level 1 (clean JSON) ─────────────────────────────────────


def test_parse_clean_json() -> None:
    text = json.dumps({
        "title": "海边日落",
        "style": "lofi",
        "tempo_bpm": 80,
        "tempo_label": "midtempo",
        "mood_arc": ["calm", "build"],
        "energy_curve": [0.3, 0.7],
        "keywords": ["lofi", "chill", "downtempo"],
        "avoid": ["heavy distortion"],
        "instrument_hints": ["soft piano"],
        "notes": "hello",
    }, ensure_ascii=False)
    b = parse_bgm_llm_output(text)
    assert b.title == "海边日落"
    assert b.style == "lofi"
    assert b.tempo_bpm == 80
    assert b.tempo_label == "midtempo"
    assert b.mood_arc == ["calm", "build"]
    assert b.energy_curve == [0.3, 0.7]
    assert b.keywords == ["lofi", "chill", "downtempo"]
    assert b.avoid == ["heavy distortion"]
    assert b.instrument_hints == ["soft piano"]
    assert b.notes == "hello"


# ── parser: level 2 (fenced code block) ──────────────────────────────


def test_parse_fenced_json_block() -> None:
    """SDK ``parse_llm_json_object`` is responsible for unwrapping the
    fence — we just need to confirm the engine forwards the unwrapped
    dict into the brief unchanged."""
    text = (
        "Here is the brief:\n\n"
        "```json\n"
        '{"title":"x","style":"epic","tempo_bpm":140,"tempo_label":"fast",'
        '"keywords":["epic","trailer","drums"]}\n'
        "```\n"
        "Hope this helps!"
    )
    b = parse_bgm_llm_output(text)
    assert b.title == "x"
    assert b.style == "epic"
    assert b.tempo_bpm == 140
    assert "epic" in b.keywords


# ── parser: level 3 (loose JSON in prose) ────────────────────────────


def test_parse_first_balanced_object() -> None:
    """LLMs often prepend ``Sure! Here is...`` before the JSON.  The
    SDK's first-balanced-object scanner must still recover it."""
    text = (
        'Sure! Here is the BGM brief: '
        '{"title":"x","style":"jazz","tempo_bpm":100,'
        '"tempo_label":"midtempo","keywords":["jazz","smooth","piano"]} '
        'Let me know if you need adjustments.'
    )
    b = parse_bgm_llm_output(text)
    assert b.style == "jazz"
    assert b.keywords == ["jazz", "smooth", "piano"]


# ── parser: level 4 (key:value bullets) ──────────────────────────────


def test_parse_key_value_bullets() -> None:
    text = (
        "title: 测试标题\n"
        "style: synthwave\n"
        "tempo_bpm: 115\n"
        "tempo_label: upbeat\n"
        "keywords: synthwave, retro, neon, 80s\n"
        "instrument_hints: synth, drum machine\n"
    )
    b = parse_bgm_llm_output(text)
    assert b.title == "测试标题"
    assert b.style == "synthwave"
    assert b.tempo_bpm == 115
    assert b.tempo_label == "upbeat"
    assert b.keywords == ["synthwave", "retro", "neon", "80s"]
    assert "synth" in b.instrument_hints


def test_parse_numbered_list_falls_back_to_keywords() -> None:
    """Old/dumb LLM that just emits a bullet list of song-style keywords —
    the engine should at least preserve them as keywords so the user can
    still feed them into a search."""
    text = "1. lofi\n2. chill\n3. background"
    b = parse_bgm_llm_output(text)
    assert b.keywords == ["lofi", "chill", "background"]


# ── parser: level 5 (plain text stub) ────────────────────────────────


def test_parse_unparseable_returns_stub() -> None:
    b = parse_bgm_llm_output("hello world this is not json or anything useful",
                              fallback_title="my-fallback",
                              fallback_duration=15.0)
    assert b.title == "my-fallback"
    assert b.target_duration_sec == 15.0
    assert b.style == "ambient"
    assert b.tempo_bpm == 80
    assert "stub fallback" in b.notes


def test_parse_empty_string_returns_stub_without_crashing() -> None:
    b = parse_bgm_llm_output("", fallback_title="t", fallback_duration=10.0)
    assert b.title == "t"
    assert b.style == "ambient"


def test_parse_none_input_does_not_crash() -> None:
    """Worker may pass ``None`` if the brain returns nothing — must not
    raise (the worker can't recover from an exception here)."""
    b = parse_bgm_llm_output(None, fallback_title="t", fallback_duration=10.0)  # type: ignore[arg-type]
    assert b.title == "t"


# ── coercion helpers (via parse_bgm_llm_output) ─────────────────────


def test_bpm_clamped_high() -> None:
    """LLM hallucination: 999 bpm is sub-audible as a beat — must clamp."""
    text = json.dumps({"title": "x", "style": "x", "tempo_bpm": 999,
                       "tempo_label": "fast", "keywords": ["a", "b", "c"]})
    b = parse_bgm_llm_output(text)
    assert b.tempo_bpm == BPM_HARD_MAX


def test_bpm_clamped_low() -> None:
    text = json.dumps({"title": "x", "style": "x", "tempo_bpm": 5,
                       "tempo_label": "slow", "keywords": ["a", "b", "c"]})
    b = parse_bgm_llm_output(text)
    assert b.tempo_bpm == BPM_HARD_MIN


def test_bpm_string_coerced() -> None:
    """LLMs sometimes emit '"tempo_bpm": "120"' (string) — must coerce."""
    text = json.dumps({"title": "x", "style": "x", "tempo_bpm": "120",
                       "tempo_label": "upbeat", "keywords": ["a", "b", "c"]})
    b = parse_bgm_llm_output(text)
    assert b.tempo_bpm == 120


def test_bpm_unparseable_defaults_to_80() -> None:
    text = json.dumps({"title": "x", "style": "x", "tempo_bpm": "fast",
                       "tempo_label": "fast", "keywords": ["a", "b", "c"]})
    b = parse_bgm_llm_output(text)
    assert b.tempo_bpm == 80


def test_tempo_label_unknown_derived_from_bpm() -> None:
    """If LLM returns a garbage label, derive one from the bpm so the
    brief is still self-consistent (downstream UI displays the label)."""
    text = json.dumps({"title": "x", "style": "x", "tempo_bpm": 140,
                       "tempo_label": "blazing-hot", "keywords": ["a", "b", "c"]})
    b = parse_bgm_llm_output(text)
    assert b.tempo_label == "fast"  # derived from bpm=140


def test_tempo_label_missing_derived_from_bpm() -> None:
    text = json.dumps({"title": "x", "style": "x", "tempo_bpm": 70,
                       "keywords": ["a", "b", "c"]})
    b = parse_bgm_llm_output(text)
    assert b.tempo_label == "slow"


def test_keywords_string_split_on_comma() -> None:
    """Some LLMs return ``"keywords": "lofi, chill, calm"`` instead of
    a list — must split on Latin / Chinese comma / pause-mark."""
    text = json.dumps({"title": "x", "style": "x", "tempo_bpm": 80,
                       "keywords": "lofi，chill、calm"})
    b = parse_bgm_llm_output(text)
    assert "lofi" in b.keywords
    assert "chill" in b.keywords
    assert "calm" in b.keywords


def test_energy_curve_clamped_to_unit_interval() -> None:
    """``energy_curve`` is meant to be 0..1 — clamp out-of-range values
    rather than rejecting the whole field."""
    text = json.dumps({"title": "x", "style": "x", "tempo_bpm": 80,
                       "energy_curve": [-0.5, 0.5, 1.5, "junk", 2.0],
                       "keywords": ["a", "b", "c"]})
    b = parse_bgm_llm_output(text)
    assert b.energy_curve == [0.0, 0.5, 1.0, 1.0]


# ── self_check ───────────────────────────────────────────────────────


def _ok_brief() -> BgmBrief:
    return BgmBrief(
        title="x", target_duration_sec=30.0, style="lofi",
        tempo_bpm=85, tempo_label="midtempo",
        mood_arc=["calm", "build"],
        energy_curve=[0.3, 0.7],
        keywords=["lofi", "chill", "downtempo"],
        avoid=[], instrument_hints=[], notes="",
    )


def test_self_check_passes_on_clean_brief() -> None:
    out = self_check(_ok_brief())
    assert out.passed is True
    assert out.issues == []


def test_self_check_flags_few_keywords() -> None:
    b = _ok_brief()
    b.keywords = ["lofi"]
    out = self_check(b)
    assert out.passed is False
    codes = {i["code"] for i in out.issues}
    assert "few_keywords" in codes


def test_self_check_flags_bpm_label_mismatch() -> None:
    """tempo_label says 'fast' but bpm=70 — UI should warn the user the
    LLM contradicted itself."""
    b = _ok_brief()
    b.tempo_bpm = 70
    b.tempo_label = "fast"
    out = self_check(b)
    codes = {i["code"] for i in out.issues}
    assert "bpm_label_mismatch" in codes


def test_self_check_flags_arc_curve_length_mismatch() -> None:
    b = _ok_brief()
    b.mood_arc = ["a", "b", "c"]
    b.energy_curve = [0.5]
    out = self_check(b)
    codes = {i["code"] for i in out.issues}
    assert "arc_curve_length" in codes


def test_self_check_flags_missing_style() -> None:
    b = _ok_brief()
    b.style = "   "
    out = self_check(b)
    codes = {i["code"] for i in out.issues}
    assert "missing_style" in codes


def test_self_check_never_blocks() -> None:
    """All issues are warnings/info — there's no blocking severity yet,
    and the contract is that self_check never refuses to return."""
    b = BgmBrief(title="", target_duration_sec=0.0, style="",
                  tempo_bpm=0, tempo_label="invalid",
                  keywords=[], mood_arc=[], energy_curve=[])
    out = self_check(b)
    for issue in out.issues:
        assert issue["severity"] in {"info", "warning"}


# ── exports / bridges ────────────────────────────────────────────────


def test_to_csv_round_trip_columns() -> None:
    csv = to_csv(_ok_brief())
    header, *rows = csv.strip().split("\n")
    assert header.split(",") == [
        "title", "duration_sec", "style", "tempo_bpm", "tempo_label",
        "mood_arc", "energy_curve", "keywords", "avoid", "instruments", "notes",
    ]
    assert len(rows) == 1
    assert "lofi" in rows[0]


def test_to_csv_escapes_commas_in_fields() -> None:
    """A keyword like 'lo-fi, chill' would shift the column count if not
    escaped — must wrap in double-quotes."""
    b = _ok_brief()
    b.title = 'comma, in, title'
    csv = to_csv(b)
    second_line = csv.strip().split("\n")[1]
    assert '"comma, in, title"' in second_line


def test_to_search_queries_shape() -> None:
    q = to_search_queries(_ok_brief())
    assert set(q.keys()) == {"youtube", "spotify", "epidemic_sound", "artlist"}
    assert "lofi" in q["youtube"]
    assert "85 bpm" in q["spotify"]
    assert "85bpm" in q["epidemic_sound"]


def test_to_search_queries_handles_empty_mood_arc() -> None:
    """When mood_arc is [] the YouTube/Artlist queries must still
    produce a usable string (no leading/trailing spaces, no double-spaces)."""
    b = _ok_brief()
    b.mood_arc = []
    q = to_search_queries(b)
    for v in q.values():
        assert v == v.strip()
        assert "  " not in v


def test_to_suno_prompt_shape_and_dedup() -> None:
    b = _ok_brief()
    b.style = "lofi"
    b.keywords = ["lofi", "chill", "downtempo", "lofi"]  # dup intentional
    b.instrument_hints = ["soft piano"]
    out = to_suno_prompt(b)
    assert set(out.keys()) == {"style", "description"}
    # Style must dedup case-insensitively (Suno gets confused by repeats).
    style_tags = [t.strip() for t in out["style"].split(",")]
    assert style_tags.count("lofi") == 1
    assert "soft piano" in style_tags
    # Description should mention bpm + tempo + duration.
    assert "85 bpm" in out["description"]
    assert "midtempo" in out["description"]
    assert "30 seconds" in out["description"]


def test_to_suno_prompt_style_length_clamped() -> None:
    """Suno's UI breaks past ~120 chars — the engine must clamp."""
    b = _ok_brief()
    b.keywords = [f"keyword_{i}_long_padding" for i in range(50)]
    out = to_suno_prompt(b)
    assert len(out["style"]) <= 120


def test_to_suno_prompt_includes_avoid() -> None:
    b = _ok_brief()
    b.avoid = ["screaming vocals", "heavy distortion"]
    out = to_suno_prompt(b)
    assert "Avoid" in out["description"]
    assert "screaming" in out["description"]


def test_to_export_payload_bundles_everything() -> None:
    b = _ok_brief()
    chk = self_check(b)
    bundle = to_export_payload(b, chk)
    assert set(bundle.keys()) == {
        "brief", "self_check", "verification", "search_queries", "suno", "csv",
    }
    assert bundle["brief"]["style"] == "lofi"
    assert bundle["self_check"]["passed"] is True


# ── prompt builder ───────────────────────────────────────────────────


def test_build_user_prompt_includes_required_blocks() -> None:
    p = build_user_prompt(scene="海边日落", mood="平静", duration_sec=30.0)
    assert "海边日落" in p
    assert "平静" in p
    assert "30.0 秒" in p
    # tempo + language NOT included when not provided
    assert "节拍偏好" not in p
    assert "语言偏好" not in p


def test_build_user_prompt_includes_optional_blocks_when_set() -> None:
    p = build_user_prompt(scene="x", mood="y", duration_sec=10,
                           tempo_hint="midtempo", language="zh")
    assert "节拍偏好" in p
    assert "midtempo" in p
    assert "语言偏好" in p
    assert "zh" in p


def test_build_user_prompt_handles_blank_scene() -> None:
    """A blank scene must still produce a renderable prompt — we don't
    want an empty triple-quote that confuses the LLM."""
    p = build_user_prompt(scene="   ", mood="", duration_sec=15.0)
    assert "(未提供)" in p
    assert "15.0 秒" in p


# ── stub_brief_text round-trip ───────────────────────────────────────


def test_stub_text_is_parseable() -> None:
    """The stub MUST satisfy the parser — otherwise plugins without an
    LLM provider would fail every task with a parse error."""
    text = stub_brief_text(scene="海边", mood="calm", duration_sec=30.0)
    b = parse_bgm_llm_output(text)
    # All required-for-export fields must be present and non-trivial.
    assert b.style
    assert b.tempo_bpm > 0
    assert b.tempo_label in KNOWN_TEMPO_LABELS
    assert len(b.keywords) >= 3


def test_stub_text_chooses_slow_tempo_for_calm_mood() -> None:
    text = stub_brief_text(scene="x", mood="calm chill", duration_sec=20.0)
    b = parse_bgm_llm_output(text)
    assert b.tempo_bpm <= 100  # slow / midtempo for chill mood


# ── data invariants ─────────────────────────────────────────────────


def test_known_tempo_labels_cover_full_bpm_range() -> None:
    """Sanity: every bpm between 40 and 200 must hit at least one known
    label.  Otherwise ``_coerce_tempo_label`` falls back to 'midtempo'
    silently and we'd never notice a regression."""
    for bpm in range(40, 201):
        if not any(lo <= bpm <= hi for (lo, hi) in KNOWN_TEMPO_LABELS.values()):
            pytest.fail(f"bpm {bpm} not covered by any KNOWN_TEMPO_LABELS range")


def test_brief_to_dict_is_json_serializable() -> None:
    """Persistence layer json.dumps()'s the brief — must not blow up."""
    text = json.dumps(_ok_brief().to_dict(), ensure_ascii=False)
    re_loaded = json.loads(text)
    assert re_loaded["style"] == "lofi"


def test_self_check_to_dict_is_json_serializable() -> None:
    out = self_check(_ok_brief())
    text = json.dumps(out.to_dict(), ensure_ascii=False)
    re_loaded = json.loads(text)
    assert re_loaded["passed"] is True


def test_selfcheck_dataclass_independent_of_brief() -> None:
    """Reify a SelfCheck without going through ``self_check`` — used by
    ``plugin._load_brief`` when re-hydrating from persistence."""
    s = SelfCheck(passed=False, issues=[{"severity": "warning",
                                          "code": "x", "message": "y"}])
    assert s.passed is False
    assert s.issues[0]["code"] == "x"


# ── D2.10 verification bridge (Sprint 9) ────────────────────────────


def _broken_brief() -> BgmBrief:
    """A brief where bpm and label disagree AND keyword count is short
    — exercises two issue codes at once."""
    return BgmBrief(
        title="t", target_duration_sec=30, style="lofi",
        tempo_bpm=70, tempo_label="fast",  # 70 bpm but says fast
        keywords=["only_one"],  # < 3 → few_keywords
    )


def test_to_verification_clean_brief_is_green() -> None:
    """A self-check-passing brief must produce a verified=True envelope
    with zero flagged fields — the host UI then renders a green badge."""
    from openakita_plugin_sdk.contrib import BADGE_GREEN
    brief = _ok_brief()
    check = self_check(brief)
    v = to_verification(brief, check)
    assert v.verified is True
    assert v.low_confidence_fields == []
    assert v.badge == BADGE_GREEN
    assert v.verifier_id == "self_check"


def test_to_verification_flags_bpm_label_mismatch() -> None:
    """The single most common LLM slip-up: tempo_bpm and tempo_label
    disagree.  Sprint 9 turns this into a yellow-badge LowConfidenceField
    on the bpm so the UI can highlight the cell."""
    from openakita_plugin_sdk.contrib import (
        BADGE_YELLOW,
        KIND_NUMBER,
    )
    brief = _broken_brief()
    check = self_check(brief)
    v = to_verification(brief, check)
    assert v.badge == BADGE_YELLOW
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.brief.tempo_bpm" in paths
    bpm_field = next(f for f in v.low_confidence_fields
                     if f.path == "$.brief.tempo_bpm")
    assert bpm_field.kind == KIND_NUMBER
    assert "fast" in bpm_field.reason  # reason carries the issue message


def test_to_verification_flags_few_keywords() -> None:
    """Keywords < 3 → flag the keywords list so the user knows to add
    more (better search hits on YouTube/Spotify)."""
    brief = _broken_brief()
    check = self_check(brief)
    v = to_verification(brief, check)
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.brief.keywords" in paths


def test_to_verification_ignores_unmapped_issue_codes() -> None:
    """``arc_curve_length`` is the only "info" code we surface; other
    codes are deliberately dropped so the badge does not turn yellow on
    advisory notes the user cannot act on."""
    brief = _ok_brief()
    fake_check = SelfCheck(
        passed=False,
        issues=[
            {"severity": "info", "code": "future_unknown_code",
             "message": "ignored"},
        ],
    )
    v = to_verification(brief, fake_check)
    assert v.low_confidence_fields == []  # unmapped → not flagged
    assert v.verified is False  # but check.passed=False still drags it
    assert "1 self-check issue" in v.notes


def test_to_export_payload_includes_verification_field() -> None:
    """Sprint 9 contract: every export payload must carry a
    ``verification`` key so frontends can render the trust badge
    without an extra round-trip."""
    brief = _broken_brief()
    check = self_check(brief)
    payload = to_export_payload(brief, check)
    assert "verification" in payload
    v_dict = payload["verification"]
    assert v_dict["verifier_id"] == "self_check"
    assert v_dict["badge"] in {"verified", "needs_review", "unverified"}
    assert "field_count_by_kind" in v_dict


def test_to_verification_verifier_id_is_stable() -> None:
    """Pin ``verifier_id="self_check"`` — host UI keys on this string to
    decide which icon / tooltip to show.  A future rule-based enrichment
    must keep this string OR run through ``merge_verifications`` so both
    signals land on the badge."""
    v = to_verification(_ok_brief(), self_check(_ok_brief()))
    assert v.verifier_id == "self_check"

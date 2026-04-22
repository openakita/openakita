"""Unit tests for ``mixer_engine`` — pure logic, no ffmpeg required.

Coverage matrix (mapped to engine surface):

* ``Beat`` / ``Sentence`` invariants and dict round-trip
* ``StubBeatTracker.detect`` determinism + tempo math
* ``MadmomBeatTracker`` raises a friendly RuntimeError when madmom is
  not installed (regression test for the lazy import path)
* ``detect_voice_sentences_from_words`` grouping rules + tiny-segment
  merge + sort-tolerance
* ``compute_ducking_envelope`` shape, overlap merge, edge cases
* ``snap_to_nearest_beat`` tolerance / downbeat preference
* ``plan_mix`` deterministic outputs for both "BGM longer than voice"
  and "BGM needs to loop"
* ``build_ffmpeg_mix_command`` filter graph snapshot (this is the
  whole point — a regression here means the mix changes silently)
* ``to_verification`` flagging rules
* ``MixPlan.to_dict`` / ``MixResult.to_dict`` are JSON-serialisable
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest import mock

import pytest

from mixer_engine import (
    DEFAULT_DUCK_DB,
    LOW_VOICE_RATIO_WARN,
    SNAP_DISTANCE_WARN_SEC,
    Beat,
    MadmomBeatTracker,
    MixPlan,
    MixResult,
    Sentence,
    StubBeatTracker,
    build_ffmpeg_mix_command,
    compute_ducking_envelope,
    detect_voice_sentences_from_words,
    plan_mix,
    snap_to_nearest_beat,
    to_verification,
    voice_active_ratio,
)


# ── primitives ─────────────────────────────────────────────────────────


def test_beat_to_dict_round_trips() -> None:
    b = Beat(index=3, time_sec=1.5, downbeat=True)
    d = b.to_dict()
    assert d == {"index": 3, "time_sec": 1.5, "downbeat": True}


def test_sentence_duration_property() -> None:
    s = Sentence(start_sec=1.0, end_sec=4.5)
    assert s.duration_sec == pytest.approx(3.5)
    assert s.to_dict()["start_sec"] == 1.0


# ── StubBeatTracker ───────────────────────────────────────────────────


def test_stub_beat_tracker_uniform_grid() -> None:
    t = StubBeatTracker(bpm=120.0, offset_sec=0.0, beats_per_measure=4)
    beats, bpm = t.detect(Path("dummy"), duration_sec=2.0)
    assert bpm == 120.0
    # 120 bpm → 0.5 s period → beats at 0, 0.5, 1.0, 1.5
    assert [b.time_sec for b in beats] == [0.0, 0.5, 1.0, 1.5]
    assert beats[0].downbeat is True
    assert beats[1].downbeat is False
    assert beats[4 - 1].downbeat is False  # 1.5s is index 3 → not downbeat


def test_stub_beat_tracker_respects_offset() -> None:
    t = StubBeatTracker(bpm=60.0, offset_sec=0.25)
    beats, _ = t.detect(Path("dummy"), duration_sec=3.5)
    assert [round(b.time_sec, 2) for b in beats] == [0.25, 1.25, 2.25, 3.25]


def test_stub_beat_tracker_zero_duration_returns_empty() -> None:
    t = StubBeatTracker(bpm=120.0)
    beats, bpm = t.detect(Path("dummy"), duration_sec=0.0)
    assert beats == []
    assert bpm == 120.0


def test_stub_beat_tracker_invalid_bpm_raises() -> None:
    t = StubBeatTracker(bpm=0.0)
    with pytest.raises(ValueError):
        t.detect(Path("dummy"), duration_sec=10.0)


def test_stub_beat_tracker_id_is_stable() -> None:
    """The tracker_id is part of cache keys downstream — it must NOT
    change between releases without bumping engine version."""
    assert StubBeatTracker().tracker_id == "stub"


# ── MadmomBeatTracker ─────────────────────────────────────────────────


def test_madmom_beat_tracker_friendly_error_when_missing() -> None:
    """The plugin layer relies on this RuntimeError shape to fall back
    to StubBeatTracker — if it changed to ImportError or to a generic
    "Exception" the fallback would swallow the wrong type."""
    t = MadmomBeatTracker()
    # Force an ImportError inside the lazy import branch.
    with mock.patch.dict("sys.modules", {"madmom": None,
                                          "madmom.features": None,
                                          "madmom.features.beats": None}):
        with pytest.raises(RuntimeError, match="madmom is not installed"):
            t.detect(Path("dummy"), duration_sec=1.0)


def test_madmom_beat_tracker_id() -> None:
    assert MadmomBeatTracker().tracker_id == "madmom"


# ── voice sentence detection ───────────────────────────────────────────


def _w(text: str, start: float, end: float) -> dict:
    return {"text": text, "start": start, "end": end}


def test_detect_voice_sentences_groups_on_long_gap() -> None:
    words = [_w("a", 0.0, 0.5), _w("b", 0.6, 1.0),
             _w("c", 2.5, 3.0), _w("d", 3.1, 3.5)]
    sents = detect_voice_sentences_from_words(words, gap_sec=0.7)
    assert len(sents) == 2
    assert sents[0].start_sec == 0.0 and sents[0].end_sec == 1.0
    assert sents[1].start_sec == 2.5 and sents[1].end_sec == 3.5


def test_detect_voice_sentences_handles_empty_input() -> None:
    assert detect_voice_sentences_from_words([]) == []


def test_detect_voice_sentences_sorts_unsorted_words() -> None:
    """Some ASR backends emit out-of-order words on the chunk
    boundary — the detector must sort before grouping."""
    words = [_w("b", 1.0, 1.5), _w("a", 0.0, 0.5)]
    sents = detect_voice_sentences_from_words(words)
    assert sents[0].start_sec == 0.0


def test_detect_voice_sentences_merges_tiny_fragments() -> None:
    """A 50 ms "嗯" followed by a real sentence should be ONE sentence
    so we don't trigger an extra duck cycle."""
    words = [_w("嗯", 0.0, 0.05),
             _w("我", 1.0, 1.2), _w("说", 1.3, 1.5)]
    sents = detect_voice_sentences_from_words(words, gap_sec=0.3)
    # The 50 ms "嗯" forms its own sentence (it's first), but because
    # it's < MIN_SENTENCE_DURATION_SEC, the merge step joins it to the
    # next.
    assert len(sents) == 1
    assert sents[0].start_sec == 0.0 and sents[0].end_sec == 1.5


# ── ducking envelope ───────────────────────────────────────────────────


def test_compute_ducking_envelope_no_sentences_returns_flat() -> None:
    env = compute_ducking_envelope([], total_duration_sec=10.0)
    assert env == [(0.0, 0.0)]


def test_compute_ducking_envelope_zero_duration_returns_empty() -> None:
    assert compute_ducking_envelope([Sentence(0.0, 1.0)], total_duration_sec=0.0) == []


def test_compute_ducking_envelope_single_sentence_shape() -> None:
    s = Sentence(start_sec=2.0, end_sec=4.0)
    env = compute_ducking_envelope([s], total_duration_sec=6.0,
                                    duck_db=-10.0,
                                    attack_sec=0.5, release_sec=1.0)
    times = [round(t, 4) for t, _ in env]
    gains = [g for _, g in env]
    # Pre-attack at 1.5, full duck from 2.0 to 4.0, release ends at 5.0
    assert 1.5 in times
    assert 2.0 in times and 4.0 in times and 5.0 in times
    assert gains[0] == 0.0  # start at full BGM level
    assert -10.0 in gains   # ducked at the sentence
    assert env[-1][1] == 0.0
    assert env[-1][0] == 6.0


def test_compute_ducking_envelope_merges_overlapping_sentences() -> None:
    sents = [Sentence(0.0, 2.0), Sentence(1.5, 3.0)]
    env = compute_ducking_envelope(sents, total_duration_sec=5.0,
                                    attack_sec=0.0, release_sec=0.0)
    # Two-sentence overlap should produce ONE duck region [0,3].
    duck_start_count = sum(1 for _, g in env if g == DEFAULT_DUCK_DB)
    # Two samples mark the duck (entry at 0, exit at 3).
    assert duck_start_count == 2


def test_compute_ducking_envelope_rejects_positive_duck_db() -> None:
    with pytest.raises(ValueError):
        compute_ducking_envelope([Sentence(0, 1)], total_duration_sec=2.0, duck_db=3.0)


def test_compute_ducking_envelope_rejects_negative_attack() -> None:
    with pytest.raises(ValueError):
        compute_ducking_envelope([Sentence(0, 1)], total_duration_sec=2.0, attack_sec=-0.1)


def test_compute_ducking_envelope_no_redundant_consecutive_samples() -> None:
    """Redundant samples (same time, same gain) blow up ffmpeg's
    expression evaluator — guard at compile time."""
    s = Sentence(0.0, 1.0)
    env = compute_ducking_envelope([s], total_duration_sec=2.0,
                                    attack_sec=0.0, release_sec=0.0)
    # No two consecutive entries share BOTH time and gain.
    for a, b in zip(env, env[1:]):
        assert not (a[0] == b[0] and a[1] == b[1])


# ── voice active ratio ───────────────────────────────────────────────


def test_voice_active_ratio_clamps_to_unit_interval() -> None:
    sents = [Sentence(0.0, 5.0)]
    assert voice_active_ratio(sents, total_duration_sec=10.0) == 0.5
    assert voice_active_ratio([], total_duration_sec=10.0) == 0.0
    # Coverage > total → clamped to 1.0
    sents2 = [Sentence(0, 20)]
    assert voice_active_ratio(sents2, total_duration_sec=10.0) == 1.0


# ── beat snap ─────────────────────────────────────────────────────────


def _grid(bpm: float, count: int) -> list[Beat]:
    period = 60.0 / bpm
    return [Beat(index=i, time_sec=i * period, downbeat=(i % 4 == 0))
            for i in range(count)]


def test_snap_within_tolerance_returns_beat_time() -> None:
    beats = _grid(120.0, 8)  # period 0.5s
    snapped, dist = snap_to_nearest_beat(0.55, beats, tolerance_sec=0.1)
    assert snapped == 0.5
    assert dist == pytest.approx(-0.05)


def test_snap_outside_tolerance_keeps_target() -> None:
    beats = _grid(120.0, 8)
    snapped, dist = snap_to_nearest_beat(0.30, beats, tolerance_sec=0.05)
    assert snapped == 0.30
    assert dist == 0.0


def test_snap_prefer_downbeat_picks_measure_start() -> None:
    beats = _grid(120.0, 8)  # downbeats at 0.0, 2.0
    # Target 1.9 is closer to off-beat 1.5 (dist 0.4) than to downbeat
    # 2.0 (dist 0.1) — so prefer_downbeat picks 2.0 (which is also
    # closer; verify by also testing a tie-breaker case below).
    snapped, _ = snap_to_nearest_beat(1.9, beats,
                                       tolerance_sec=0.5,
                                       prefer_downbeat=True)
    assert snapped == 2.0


def test_snap_prefer_downbeat_skips_offbeat_when_downbeat_in_tolerance() -> None:
    """1.4 s is closer to off-beat 1.5 than downbeat 0.0 / 2.0, but
    with prefer_downbeat=True we should pick 2.0 (the nearest of the
    downbeats inside tolerance)."""
    beats = _grid(120.0, 8)
    snapped, _ = snap_to_nearest_beat(1.4, beats,
                                       tolerance_sec=1.5,
                                       prefer_downbeat=True)
    assert snapped == 2.0


def test_snap_no_beats_returns_target_unchanged() -> None:
    snapped, dist = snap_to_nearest_beat(3.7, [], tolerance_sec=0.5)
    assert snapped == 3.7
    assert dist == 0.0


# ── plan_mix ──────────────────────────────────────────────────────────


def test_plan_mix_bgm_longer_than_voice_no_loop() -> None:
    beats = _grid(120.0, 60)  # 30 s of beats
    plan = plan_mix(
        voice_path="v.wav", bgm_path="b.mp3",
        voice_duration_sec=10.0, bgm_duration_sec=30.0,
        sentences=[Sentence(0.0, 8.0)], beats=beats, bpm=120.0,
    )
    assert plan.bgm_loop_count == 1
    # End trim is snapped to a beat near 10 s (10.0 IS a beat already
    # at 120 bpm, so it stays).
    assert plan.bgm_trim_end_sec == pytest.approx(10.0)
    assert plan.notes == ""


def test_plan_mix_short_bgm_loops_and_records_note() -> None:
    beats = _grid(120.0, 12)  # 6 s of beats
    plan = plan_mix(
        voice_path="v.wav", bgm_path="b.mp3",
        voice_duration_sec=14.0, bgm_duration_sec=6.0,
        sentences=[Sentence(0, 12)], beats=beats, bpm=120.0,
    )
    # ceil(14/6) = 3 loops.
    assert plan.bgm_loop_count == 3
    assert "looped 3 times" in plan.notes


def test_plan_mix_clamps_fades_to_half_duration() -> None:
    """A 1 s mix with default 300/500 ms fades would otherwise leave 0
    ms loud — clamp both fades to 0.5 s each.  D2.8 friendliness."""
    beats = _grid(120.0, 4)
    plan = plan_mix(
        voice_path="v.wav", bgm_path="b.mp3",
        voice_duration_sec=1.0, bgm_duration_sec=10.0,
        sentences=[], beats=beats, bpm=120.0,
        fade_in_sec=0.6, fade_out_sec=0.7,
    )
    assert plan.fade_in_sec <= 0.5
    assert plan.fade_out_sec <= 0.5


def test_plan_mix_invalid_durations_raise() -> None:
    with pytest.raises(ValueError):
        plan_mix(voice_path="v", bgm_path="b",
                 voice_duration_sec=0, bgm_duration_sec=10,
                 sentences=[], beats=[], bpm=120)
    with pytest.raises(ValueError):
        plan_mix(voice_path="v", bgm_path="b",
                 voice_duration_sec=10, bgm_duration_sec=0,
                 sentences=[], beats=[], bpm=120)


def test_plan_mix_is_deterministic() -> None:
    """Same inputs → same plan dict, every run.  This is the single
    most important property of the planner — it powers caching and
    "show me what you'll do" preview."""
    beats = _grid(100.0, 30)
    args = dict(voice_path="v.wav", bgm_path="b.mp3",
                voice_duration_sec=12.3, bgm_duration_sec=20.0,
                sentences=[Sentence(0.5, 5.0), Sentence(7.0, 11.0)],
                beats=beats, bpm=100.0)
    p1 = plan_mix(**args).to_dict()
    p2 = plan_mix(**args).to_dict()
    assert p1 == p2


def test_plan_mix_is_json_serialisable() -> None:
    beats = _grid(120.0, 4)
    plan = plan_mix(
        voice_path="v.wav", bgm_path="b.mp3",
        voice_duration_sec=2.0, bgm_duration_sec=2.0,
        sentences=[Sentence(0.5, 1.5)], beats=beats, bpm=120.0,
    )
    text = json.dumps(plan.to_dict())
    again = json.loads(text)
    assert again["bgm_loop_count"] == 1


# ── ffmpeg command builder ────────────────────────────────────────────


def test_build_ffmpeg_mix_command_contains_required_pieces() -> None:
    beats = _grid(120.0, 30)
    plan = plan_mix(
        voice_path="voice.wav", bgm_path="bgm.mp3",
        voice_duration_sec=10.0, bgm_duration_sec=30.0,
        sentences=[Sentence(2.0, 6.0)], beats=beats, bpm=120.0,
    )
    cmd = build_ffmpeg_mix_command(plan, output_path="out.mp3")
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert "voice.wav" in cmd
    assert "bgm.mp3" in cmd
    assert "out.mp3" in cmd
    fc_idx = cmd.index("-filter_complex")
    fc = cmd[fc_idx + 1]
    # Filter graph contains the three required chains.
    assert "[voice]" in fc and "[bgm]" in fc and "amix" in fc
    assert "aloop=loop=0" in fc  # single play, no loop


def test_build_ffmpeg_mix_command_loop_param_one_less_than_count() -> None:
    """ffmpeg's aloop counts repeats, not plays — so loop_count=3
    plays must turn into ``aloop=loop=2``."""
    beats = _grid(120.0, 30)
    plan = plan_mix(
        voice_path="v", bgm_path="b",
        voice_duration_sec=18.0, bgm_duration_sec=6.0,
        sentences=[], beats=beats, bpm=120.0,
    )
    cmd = build_ffmpeg_mix_command(plan, output_path="o.mp3")
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "aloop=loop=2" in fc
    assert plan.bgm_loop_count == 3


def test_build_ffmpeg_mix_command_uses_eval_frame_for_envelope() -> None:
    """``eval=frame`` is required for the time-driven volume expression
    — without it, ffmpeg evaluates the expression once at start and
    the duck never engages."""
    beats = _grid(120.0, 6)
    plan = plan_mix(
        voice_path="v", bgm_path="b",
        voice_duration_sec=3.0, bgm_duration_sec=3.0,
        sentences=[Sentence(0.5, 2.0)], beats=beats, bpm=120.0,
    )
    cmd = build_ffmpeg_mix_command(plan, output_path="o.mp3")
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "eval=frame" in fc


def test_build_ffmpeg_mix_command_outputs_libmp3lame() -> None:
    beats = _grid(120.0, 4)
    plan = plan_mix(
        voice_path="v", bgm_path="b",
        voice_duration_sec=2.0, bgm_duration_sec=2.0,
        sentences=[], beats=beats, bpm=120.0,
    )
    cmd = build_ffmpeg_mix_command(plan, output_path="o.mp3")
    assert "libmp3lame" in cmd


# ── verification (D2.10) ──────────────────────────────────────────────


def _result(**overrides) -> MixResult:
    """Build a clean MixResult with override-able fields."""
    plan = plan_mix(
        voice_path="v", bgm_path="b",
        voice_duration_sec=10.0, bgm_duration_sec=30.0,
        sentences=[Sentence(1, 5), Sentence(6, 9)],
        beats=_grid(120.0, 60), bpm=120.0,
    )
    base = dict(plan=plan, output_path="o.mp3", duration_sec=10.0,
                ffmpeg_cmd=["ffmpeg"], used_madmom=False,
                voice_active_ratio=0.7, snap_max_distance_sec=0.0,
                looped=False)
    base.update(overrides)
    return MixResult(**base)


def test_to_verification_clean_mix_is_green() -> None:
    v = to_verification(_result())
    assert v.verified is True
    assert v.low_confidence_fields == []
    assert v.verifier_id == "bgm_mixer_self_check"


def test_to_verification_flags_loop() -> None:
    plan = plan_mix(
        voice_path="v", bgm_path="b",
        voice_duration_sec=18.0, bgm_duration_sec=6.0,
        sentences=[Sentence(0, 15)], beats=_grid(120.0, 12), bpm=120.0,
    )
    res = MixResult(plan=plan, output_path="o.mp3", duration_sec=18.0,
                    ffmpeg_cmd=[], used_madmom=False,
                    voice_active_ratio=0.8, snap_max_distance_sec=0.0,
                    looped=True)
    v = to_verification(res)
    assert v.verified is False
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.mix.loop" in paths


def test_to_verification_flags_snap_distance() -> None:
    res = _result(snap_max_distance_sec=SNAP_DISTANCE_WARN_SEC + 0.1)
    v = to_verification(res)
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.mix.snap" in paths


def test_to_verification_flags_low_voice_ratio() -> None:
    res = _result(voice_active_ratio=LOW_VOICE_RATIO_WARN / 2)
    v = to_verification(res)
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.mix.duck" in paths


def test_to_verification_does_not_flag_low_voice_when_no_sentences() -> None:
    """Pure instrumental (no sentences) is a valid "background only"
    mode — don't false-positive the duck warning."""
    plan = plan_mix(
        voice_path="v", bgm_path="b",
        voice_duration_sec=5.0, bgm_duration_sec=5.0,
        sentences=[], beats=_grid(120.0, 10), bpm=120.0,
    )
    res = MixResult(plan=plan, output_path="o.mp3", duration_sec=5.0,
                    ffmpeg_cmd=[], used_madmom=False,
                    voice_active_ratio=0.0, snap_max_distance_sec=0.0,
                    looped=False)
    v = to_verification(res)
    # No "$.mix.duck" because plan.sentences is empty.
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.mix.duck" not in paths


def test_to_verification_dict_is_json_serialisable() -> None:
    v = to_verification(_result(looped=True))
    text = json.dumps(v.to_dict())
    assert "bgm_mixer_self_check" in text


def test_to_verification_notes_includes_tracker_kind() -> None:
    """Operator must be able to tell at a glance whether the mix used
    real madmom or fell back to the stub — that's the difference
    between "publishable" and "preview only"."""
    v_stub = to_verification(_result(used_madmom=False))
    v_madmom = to_verification(_result(used_madmom=True))
    assert "stub" in v_stub.notes
    assert "madmom" in v_madmom.notes


# ── MixResult JSON ────────────────────────────────────────────────────


def test_mix_result_to_dict_round_trips() -> None:
    res = _result(looped=True)
    text = json.dumps(res.to_dict())
    again = json.loads(text)
    assert again["output_path"] == "o.mp3"
    assert again["plan"]["bgm_loop_count"] == 1

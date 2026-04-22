"""Unit tests for ``transcribe_engine`` — pure-function coverage only.

No ffmpeg / no network / no real audio.  Tests against:

* :func:`plan_chunks`           — boundary cases, overlap clamp, tail
* cache key derivation          — stability across runs, sensitivity
                                  to language / args
* per-chunk cache I/O           — round-trip, corruption tolerance
* :func:`merge_words_with_overlap_dedup`
                                — overlap dedup, empty-chunk handling,
                                  confidence-based winner
* renderers (SRT / VTT / TXT)   — timestamp format, CJK no-space rule,
                                  cue grouping
* :class:`StubProvider`         — determinism, args round-trip
* :func:`to_verification`       — D2.10 envelope mapping
* :func:`stub_transcribe_offline` — full offline pipeline shape
* :func:`to_archive_bundle`     — four-format export

Running:
    py -3.11 -m pytest plugins/transcribe-archive/tests -q
"""
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

import json
import re
from pathlib import Path

import pytest

from transcribe_engine import (
    DEFAULT_CHUNK_DURATION_SEC,
    DEFAULT_CHUNK_OVERLAP_SEC,
    LOW_CONFIDENCE_THRESHOLD,
    MAX_FLAGGED_WORDS,
    Chunk,
    StubProvider,
    TranscriptResult,
    Word,
    cache_path_for,
    chunk_cache_key,
    load_cached_words,
    merge_words_with_overlap_dedup,
    plan_chunks,
    store_cached_words,
    stub_transcribe_offline,
    to_archive_bundle,
    to_plain_text,
    to_srt,
    to_verification,
    to_vtt,
)


# ── Word / Chunk dataclass invariants ──────────────────────────────────


def test_word_rejects_end_before_start() -> None:
    """A word with end < start would break every renderer (negative
    duration → broken SRT timestamps).  Reject at construction."""
    with pytest.raises(ValueError):
        Word(text="x", start=1.0, end=0.5)


def test_word_rejects_confidence_outside_unit_interval() -> None:
    """Confidence is documented as 0..1 — out-of-range values silently
    propagated would corrupt the verification badge."""
    with pytest.raises(ValueError):
        Word(text="x", start=0.0, end=0.1, confidence=1.5)
    with pytest.raises(ValueError):
        Word(text="x", start=0.0, end=0.1, confidence=-0.01)


def test_word_allows_equal_start_and_end() -> None:
    """Some providers emit zero-duration "tick" words (e.g. a comma);
    must accept them without raising."""
    w = Word(text=",", start=1.5, end=1.5)
    assert w.end == w.start


def test_chunk_duration_property() -> None:
    c = Chunk(index=0, start_sec=10.0, end_sec=70.0)
    assert c.duration_sec == 60.0


# ── plan_chunks: boundaries & invariants ───────────────────────────────


def test_plan_chunks_zero_duration_returns_empty() -> None:
    """A 0-second file is not a "single zero-duration chunk" — it is
    an empty plan so the engine emits a friendly error instead of
    asking the provider to transcribe nothing."""
    assert plan_chunks(0.0) == []
    assert plan_chunks(-5.0) == []


def test_plan_chunks_short_file_single_chunk() -> None:
    """A 30 s file is shorter than the default 60 s window — must
    produce exactly one chunk that covers the whole file with zero
    overlap (no need for it)."""
    chunks = plan_chunks(30.0)
    assert len(chunks) == 1
    assert chunks[0].start_sec == 0.0
    assert chunks[0].end_sec == 30.0


def test_plan_chunks_exact_window_boundary() -> None:
    """A file whose duration exactly equals the chunk window — must
    still be a single chunk (we never emit a 0 s tail)."""
    chunks = plan_chunks(60.0, chunk_duration_sec=60.0)
    assert len(chunks) == 1
    assert chunks[0].end_sec == 60.0


def test_plan_chunks_multiple_with_overlap() -> None:
    """A 5-minute file at default 60 s / 5 s overlap — chunk #2 must
    start at 55 s (60 - 5), every chunk must end exactly when the
    next one starts + overlap."""
    chunks = plan_chunks(300.0)
    assert len(chunks) >= 5
    for prev, nxt in zip(chunks, chunks[1:]):
        gap = nxt.start_sec - prev.start_sec
        assert pytest.approx(gap, rel=1e-6) == DEFAULT_CHUNK_DURATION_SEC - DEFAULT_CHUNK_OVERLAP_SEC
    assert chunks[-1].end_sec == pytest.approx(300.0)


def test_plan_chunks_clamps_excessive_overlap() -> None:
    """User passes overlap=50 with chunk=60 — must clamp to 30 (half)
    rather than producing a step of 10 (3x the same audio per word)."""
    chunks = plan_chunks(180.0, chunk_duration_sec=60.0, overlap_sec=50.0)
    if len(chunks) >= 2:
        step = chunks[1].start_sec - chunks[0].start_sec
        assert step >= 30.0  # 60 - clamp(50, max=30) = 30


def test_plan_chunks_tail_folded_when_too_short() -> None:
    """A 65 s file at 60/5 default would yield chunk1=[0,60] +
    chunk2=[55,65] (10 s tail).  10 / 60 < 1/3 → tail folds back into
    the previous chunk extending its end to 65."""
    chunks = plan_chunks(65.0, chunk_duration_sec=60.0, overlap_sec=5.0)
    assert len(chunks) == 1
    assert chunks[0].end_sec == 65.0


def test_plan_chunks_rejects_invalid_window() -> None:
    """Defensive: the planner is the API surface for a UI slider — must
    reject obviously-wrong values rather than silently doing something
    weird."""
    with pytest.raises(ValueError):
        plan_chunks(120.0, chunk_duration_sec=5.0)  # < MIN
    with pytest.raises(ValueError):
        plan_chunks(120.0, chunk_duration_sec=10000.0)  # > MAX
    with pytest.raises(ValueError):
        plan_chunks(120.0, overlap_sec=-1.0)


def test_plan_chunks_indexes_are_sequential_from_zero() -> None:
    """Cache keys + UI rows + failure logs all assume contiguous 0,1,2..
    indexes — make that an explicit test contract."""
    chunks = plan_chunks(420.0)
    assert [c.index for c in chunks] == list(range(len(chunks)))


# ── cache key derivation ───────────────────────────────────────────────


def test_chunk_cache_key_is_stable_across_calls() -> None:
    """Same audio bytes + same args → same key.  Without this
    invariant the cache never hits."""
    key1 = chunk_cache_key(
        b"audio_bytes",
        provider_id="stub", provider_args={"x": 1}, language="zh",
    )
    key2 = chunk_cache_key(
        b"audio_bytes",
        provider_id="stub", provider_args={"x": 1}, language="zh",
    )
    assert key1 == key2
    assert len(key1) == 64  # sha256 hex


def test_chunk_cache_key_changes_when_audio_changes() -> None:
    a = chunk_cache_key(b"a", provider_id="stub", provider_args={}, language="zh")
    b = chunk_cache_key(b"b", provider_id="stub", provider_args={}, language="zh")
    assert a != b


def test_chunk_cache_key_changes_when_language_changes() -> None:
    """Language is part of the cache key — switching zh→en MUST
    invalidate every chunk (different ASR model produces different words).
    """
    zh = chunk_cache_key(b"x", provider_id="stub", provider_args={}, language="zh")
    en = chunk_cache_key(b"x", provider_id="stub", provider_args={}, language="en")
    assert zh != en


def test_chunk_cache_key_changes_when_provider_id_changes() -> None:
    """User switches Whisper → Scribe — every cached chunk MUST be
    re-transcribed (different ASR may differ in word boundaries)."""
    whisper = chunk_cache_key(b"x", provider_id="whisper", provider_args={}, language="zh")
    scribe = chunk_cache_key(b"x", provider_id="scribe", provider_args={}, language="zh")
    assert whisper != scribe


def test_chunk_cache_key_changes_when_args_change() -> None:
    """``temperature=0.0`` vs ``0.5`` must produce different cache keys
    so the user can A/B Whisper params without polluting the cache."""
    a = chunk_cache_key(b"x", provider_id="stub", provider_args={"t": 0.0}, language="zh")
    b = chunk_cache_key(b"x", provider_id="stub", provider_args={"t": 0.5}, language="zh")
    assert a != b


def test_chunk_cache_key_args_dict_order_independent() -> None:
    """Cache key must NOT depend on dict key order (Python 3.7+
    insertion-order is stable, but a refactor could rebuild the dict
    — sort_keys in the hashing JSON guarantees stability)."""
    a = chunk_cache_key(b"x", provider_id="stub",
                        provider_args={"a": 1, "b": 2}, language="zh")
    b = chunk_cache_key(b"x", provider_id="stub",
                        provider_args={"b": 2, "a": 1}, language="zh")
    assert a == b


def test_cache_path_for_uses_two_char_prefix_dir() -> None:
    """Sharded directory layout — < 256 sub-dirs in the cache root
    even on a busy host (Windows starts to slow down past ~10k files
    in one directory)."""
    p = cache_path_for(Path("/tmp/cache"), "abcdef" + "0" * 58)
    parts = p.parts
    assert parts[-2] == "ab"
    assert parts[-1] == "abcdef" + "0" * 58 + ".json"


# ── cache I/O round-trip ───────────────────────────────────────────────


def test_cache_round_trip(tmp_path: Path) -> None:
    """Write → read recovers exact words including confidence."""
    words = [
        Word(text="hello", start=0.0, end=0.5, confidence=0.92),
        Word(text="world", start=0.5, end=1.1, confidence=0.78),
    ]
    key = "a" * 64
    store_cached_words(tmp_path, key, words)
    loaded = load_cached_words(tmp_path, key)
    assert loaded == words  # frozen dataclass equality


def test_cache_miss_returns_none(tmp_path: Path) -> None:
    assert load_cached_words(tmp_path, "deadbeef" * 8) is None


def test_cache_corrupted_entry_returns_none(tmp_path: Path) -> None:
    """Half-written JSON from a crashed previous run must NOT poison
    the cache forever — just treat as a miss and re-call the provider."""
    key = "c" * 64
    path = cache_path_for(tmp_path, key)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_cached_words(tmp_path, key) is None


def test_cache_atomic_write_no_partial_files_left(tmp_path: Path) -> None:
    """After a successful store, only the final ``.json`` exists; the
    ``.tmp`` companion has been renamed away."""
    key = "d" * 64
    store_cached_words(tmp_path, key, [Word(text="x", start=0.0, end=0.1)])
    final = cache_path_for(tmp_path, key)
    assert final.exists()
    assert not final.with_suffix(final.suffix + ".tmp").exists()


# ── merge_words_with_overlap_dedup ─────────────────────────────────────


def test_merge_empty_returns_empty() -> None:
    assert merge_words_with_overlap_dedup([], []) == []


def test_merge_single_chunk_passes_through() -> None:
    chunks = [Chunk(index=0, start_sec=0.0, end_sec=10.0)]
    words = [Word(text="a", start=0.5, end=1.0)]
    out = merge_words_with_overlap_dedup(chunks, [words])
    assert out == words


def test_merge_dedup_drops_duplicate_in_overlap() -> None:
    """Word "hello" appears in both chunks within the overlap region —
    the existing (first-chunk) copy must win and the duplicate dropped."""
    chunks = [
        Chunk(index=0, start_sec=0.0, end_sec=60.0),
        Chunk(index=1, start_sec=55.0, end_sec=120.0),
    ]
    words_a = [Word(text="hello", start=58.0, end=58.5, confidence=0.9)]
    words_b = [Word(text="hello", start=58.1, end=58.6, confidence=0.9)]
    out = merge_words_with_overlap_dedup(chunks, [words_a, words_b])
    assert len(out) == 1
    assert out[0].confidence == 0.9


def test_merge_dedup_higher_confidence_wins() -> None:
    """When the second chunk has materially higher confidence (> 0.05
    delta), the new copy replaces the existing one."""
    chunks = [
        Chunk(index=0, start_sec=0.0, end_sec=60.0),
        Chunk(index=1, start_sec=55.0, end_sec=120.0),
    ]
    words_a = [Word(text="hello", start=58.0, end=58.5, confidence=0.6)]
    words_b = [Word(text="hello", start=58.1, end=58.6, confidence=0.95)]
    out = merge_words_with_overlap_dedup(chunks, [words_a, words_b])
    assert len(out) == 1
    assert out[0].confidence == 0.95


def test_merge_skips_empty_chunk() -> None:
    """A failed chunk has empty words — merger must skip it without
    aborting the whole transcript (N1.1 — never silently drop the rest)."""
    chunks = [
        Chunk(index=0, start_sec=0.0, end_sec=60.0),
        Chunk(index=1, start_sec=55.0, end_sec=120.0),
        Chunk(index=2, start_sec=115.0, end_sec=180.0),
    ]
    words_a = [Word(text="a", start=10.0, end=10.5)]
    words_c = [Word(text="c", start=130.0, end=130.5)]
    out = merge_words_with_overlap_dedup(chunks, [words_a, [], words_c])
    texts = [w.text for w in out]
    assert texts == ["a", "c"]


def test_merge_punctuation_treated_as_same_word() -> None:
    """``hello,`` and ``hello`` differ only in punctuation; the dedup
    normaliser must treat them as duplicates."""
    chunks = [
        Chunk(index=0, start_sec=0.0, end_sec=60.0),
        Chunk(index=1, start_sec=55.0, end_sec=120.0),
    ]
    words_a = [Word(text="hello,", start=58.0, end=58.5)]
    words_b = [Word(text="HELLO", start=58.1, end=58.6)]
    out = merge_words_with_overlap_dedup(chunks, [words_a, words_b])
    assert len(out) == 1


def test_merge_validates_lengths_match() -> None:
    """Length mismatch is a programmer error, not a runtime situation
    — fail loudly so a bug in the engine surfaces in tests."""
    with pytest.raises(ValueError):
        merge_words_with_overlap_dedup(
            [Chunk(index=0, start_sec=0, end_sec=10)],
            [],
        )


def test_merge_output_sorted_by_start() -> None:
    """Even if a provider emits words out of order, the merged output
    is monotonically sorted — renderers rely on it."""
    chunks = [
        Chunk(index=0, start_sec=0.0, end_sec=60.0),
        Chunk(index=1, start_sec=55.0, end_sec=120.0),
    ]
    words_a = [Word(text="a", start=5.0, end=5.5),
               Word(text="b", start=2.0, end=2.5)]
    out = merge_words_with_overlap_dedup(chunks, [words_a, []])
    assert [w.start for w in out] == [2.0, 5.0]


# ── renderers ──────────────────────────────────────────────────────────


def _wd(text: str, start: float, end: float, conf: float = 1.0) -> Word:
    return Word(text=text, start=start, end=end, confidence=conf)


def test_to_plain_text_english_uses_spaces() -> None:
    out = to_plain_text([_wd("hello", 0, 0.5), _wd("world", 0.5, 1.0)])
    assert out == "hello world"


def test_to_plain_text_chinese_no_spaces() -> None:
    """CJK rendering: native readers expect "你好世界", not "你 好 世 界"."""
    out = to_plain_text([_wd("你好", 0, 0.5), _wd("世界", 0.5, 1.0)])
    assert out == "你好世界"


def test_to_plain_text_empty() -> None:
    assert to_plain_text([]) == ""


def test_to_srt_basic_shape() -> None:
    out = to_srt([_wd("hello", 0.0, 1.0), _wd("world", 1.1, 2.0)])
    assert "1\n" in out
    # Comma is mandatory in SRT (different from VTT).
    assert re.search(r"\d{2}:\d{2}:\d{2},\d{3}", out)
    assert "hello world" in out


def test_to_vtt_starts_with_header() -> None:
    out = to_vtt([_wd("a", 0.0, 1.0)])
    assert out.startswith("WEBVTT\n")
    # Period separator, not comma.
    assert re.search(r"\d{2}:\d{2}:\d{2}\.\d{3}", out)


def test_to_srt_ts_format_pads_correctly() -> None:
    """A 1-hour-and-2-minute clip must render as 01:02:03,400 not
    1:2:3,400 (SRT players are picky)."""
    long_word = _wd("x", 3723.4, 3724.0)  # 1h02m03.4s
    out = to_srt([long_word])
    assert "01:02:03,400" in out


def test_to_srt_groups_by_silence_gap() -> None:
    """A gap > 0.7 s between words splits them into separate cues —
    otherwise long pauses produce one massive unreadable subtitle."""
    words = [
        _wd("first", 0.0, 0.5),
        _wd("second", 2.0, 2.5),  # 1.5 s gap → new cue
    ]
    out = to_srt(words)
    assert out.count("-->") == 2  # two cues


def test_to_srt_groups_by_max_chars() -> None:
    """A long sentence is broken into multiple cues at the
    ``max_chars_per_cue`` ceiling so subtitles stay readable on
    narrow displays."""
    words = [_wd(f"word{i}", i * 0.4, i * 0.4 + 0.3) for i in range(20)]
    out = to_srt(words, max_chars_per_cue=20)
    assert out.count("-->") >= 2


# ── StubProvider determinism ───────────────────────────────────────────


def test_stub_provider_deterministic(tmp_path: Path) -> None:
    """Same audio bytes → same words.  This is the contract that makes
    the cache testable."""
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"hello-audio-bytes")
    p = StubProvider(words_per_chunk=4)
    a = p.transcribe_chunk(audio, language="zh")
    b = p.transcribe_chunk(audio, language="zh")
    assert a == b
    assert len(a) == 4


def test_stub_provider_args_round_trip() -> None:
    p = StubProvider(words_per_chunk=8, confidence=0.7, language_label="en")
    args = p.args_for_cache_key()
    assert args == {
        "words_per_chunk": 8,
        "confidence": 0.7,
        "language_label": "en",
    }


# ── stub_transcribe_offline (full pipeline, no I/O) ────────────────────


def test_stub_transcribe_offline_runs_end_to_end() -> None:
    """Drives plan_chunks → synth-words → merge_words; result must
    have a non-zero number of words and all timestamps within the
    requested duration."""
    out = stub_transcribe_offline(duration_sec=180.0, words_per_chunk=4)
    assert isinstance(out, TranscriptResult)
    assert out.duration_sec == 180.0
    assert out.chunks_total >= 3
    assert out.chunks_failed == 0
    assert out.words
    assert all(0 <= w.start < w.end <= 180.0 for w in out.words)


def test_stub_transcribe_offline_deterministic() -> None:
    """Same args → identical transcripts.  Plugin's "preview" flow
    relies on this so the user can re-open the page and see the
    same shape they had a minute ago."""
    a = stub_transcribe_offline(duration_sec=120.0, seed_text="alpha")
    b = stub_transcribe_offline(duration_sec=120.0, seed_text="alpha")
    assert [w.to_dict() for w in a.words] == [w.to_dict() for w in b.words]


def test_stub_transcribe_offline_rejects_zero_duration() -> None:
    with pytest.raises(ValueError):
        stub_transcribe_offline(duration_sec=0.0)


# ── verification (D2.10) ───────────────────────────────────────────────


def test_to_verification_clean_transcript_is_green() -> None:
    """All words ≥ threshold + zero failed chunks → verified=True,
    badge green."""
    from _shared import BADGE_GREEN
    result = TranscriptResult(
        words=[_wd("clear", 0, 1, conf=0.95)],
        duration_sec=1.0, language="zh",
        chunks_total=1, chunks_from_cache=0, chunks_failed=0,
        provider_id="stub",
    )
    v = to_verification(result)
    assert v.verified is True
    assert v.badge == BADGE_GREEN
    assert v.verifier_id == "transcribe_archive_self_check"


def test_to_verification_flags_low_confidence_words() -> None:
    """Words below the threshold must appear in low_confidence_fields,
    capped at MAX_FLAGGED_WORDS."""
    from _shared import BADGE_YELLOW, KIND_QUOTE
    low = [_wd(f"w{i}", i, i + 0.5, conf=0.4) for i in range(MAX_FLAGGED_WORDS + 5)]
    result = TranscriptResult(
        words=low, duration_sec=20.0, language="zh",
        chunks_total=1, chunks_from_cache=0, chunks_failed=0,
        provider_id="stub",
    )
    v = to_verification(result)
    assert v.badge == BADGE_YELLOW
    assert len(v.low_confidence_fields) == MAX_FLAGGED_WORDS
    assert all(f.kind == KIND_QUOTE for f in v.low_confidence_fields)
    assert "低置信度" in v.low_confidence_fields[0].reason


def test_to_verification_flags_failed_chunks() -> None:
    """Even with all words above threshold, a failed chunk must surface
    a coverage flag so the UI shows the "N 段失败" banner."""
    from _shared import KIND_OTHER
    result = TranscriptResult(
        words=[_wd("ok", 0, 1, conf=0.95)],
        duration_sec=120.0, language="zh",
        chunks_total=2, chunks_from_cache=0, chunks_failed=1,
        provider_id="stub", failed_chunk_indexes=[1],
    )
    v = to_verification(result)
    assert v.verified is False
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.transcript.coverage" in paths
    cov = next(f for f in v.low_confidence_fields if f.path == "$.transcript.coverage")
    assert cov.kind == KIND_OTHER
    assert "1" in cov.reason


def test_to_verification_notes_includes_cache_hit_rate() -> None:
    """Notes line carries cache-hit info so users can see "I'm not
    paying for words I already paid for last week"."""
    result = TranscriptResult(
        words=[_wd("ok", 0, 1, conf=0.95)],
        duration_sec=60.0, language="zh",
        chunks_total=4, chunks_from_cache=3, chunks_failed=0,
        provider_id="stub",
    )
    v = to_verification(result)
    assert "3/4" in v.notes
    assert "缓存" in v.notes


# ── threshold sanity ───────────────────────────────────────────────────


def test_low_confidence_threshold_is_within_unit_interval() -> None:
    """Constant must be a sensible probability — guard against a
    refactor that accidentally sets it to 60 (out-of-range)."""
    assert 0.0 < LOW_CONFIDENCE_THRESHOLD < 1.0


# ── archive bundle ─────────────────────────────────────────────────────


def test_to_archive_bundle_emits_four_formats() -> None:
    result = stub_transcribe_offline(duration_sec=120.0, words_per_chunk=3)
    bundle = to_archive_bundle(result)
    assert bundle.json
    assert bundle.txt
    assert bundle.srt
    assert bundle.vtt
    # JSON must be valid + round-trip back through TranscriptResult.to_dict().
    decoded = json.loads(bundle.json)
    assert decoded["language"] == "zh"
    assert decoded["chunks_total"] == result.chunks_total
    # SRT/VTT must look like their respective formats.
    assert "-->" in bundle.srt
    assert bundle.vtt.startswith("WEBVTT")


def test_to_archive_bundle_handles_empty_words() -> None:
    """An empty transcript (e.g., 1 s of silence) must still produce a
    valid 4-format bundle — TXT empty, SRT/VTT empty body but valid
    headers, JSON with empty words list."""
    result = TranscriptResult(
        words=[], duration_sec=1.0, language="zh",
        chunks_total=0, chunks_from_cache=0, chunks_failed=0,
        provider_id="stub",
    )
    bundle = to_archive_bundle(result)
    assert bundle.txt == ""
    assert bundle.vtt.startswith("WEBVTT")
    decoded = json.loads(bundle.json)
    assert decoded["words"] == []


# ── result property invariants ─────────────────────────────────────────


def test_transcript_result_cache_hit_rate_zero_when_no_chunks() -> None:
    """Division-by-zero guard — a 0-chunk result must report 0.0 not
    raise."""
    result = TranscriptResult(
        words=[], duration_sec=0.0, language="zh",
        chunks_total=0, chunks_from_cache=0, chunks_failed=0,
        provider_id="stub",
    )
    assert result.cache_hit_rate == 0.0


def test_transcript_result_to_dict_round_trips_through_json() -> None:
    """The ``result`` is persisted as JSON in the task DB — must be
    losslessly serialisable."""
    result = stub_transcribe_offline(duration_sec=70.0, words_per_chunk=2)
    text = json.dumps(result.to_dict(), ensure_ascii=False)
    decoded = json.loads(text)
    assert decoded["chunks_total"] == result.chunks_total
    assert len(decoded["words"]) == len(result.words)

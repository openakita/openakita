"""Phase 2b unit tests — subtitle_renderer + ASR client helpers.

Coverage map (P0-/P1- pitfall IDs from docs/subtitle-craft-plan.md §九):

- P0-12 / P1-7 / P1-8 / P1-9 → ``test_repair_*`` family
- P0-13                        → ``test_no_top_level_playwright_import``
- P0-14                        → ``test_playwright_singleton_*``
- P0-16                        → ``test_ffmpeg_subtitles_arg_*``
- P0-15 + word_normalize       → ``test_normalize_word_*``
- P0-7 + Qwen-MT chunking      → ``test_split_long_chunk_*``
- defensive prose strip        → ``test_strip_prose_preamble_*``
- words → cues                  → ``test_words_to_srt_cues_*``
- SRT/VTT round-trip           → ``test_serialize_*`` / ``test_parse_srt_*``
- ASS style serialization      → ``test_to_force_style_format``
- A-path arg assembly           → ``test_burn_subtitles_ass_args``
- B-path fallback contract     → ``test_burn_subtitles_html_fallback_*``

All tests are pure (no network, no ffmpeg subprocess, no Playwright launch).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from subtitle_asr_client import (
    AsrWord,
    _normalize_sentence,
    _normalize_word,
    _split_long_chunk,
    _strip_prose_preamble,
)
from subtitle_models import SUBTITLE_STYLES_BY_ID
from subtitle_renderer import (
    DEFAULT_MIN_CUE_DURATION_SEC,
    SRTCue,
    _ffmpeg_subtitles_arg,
    _PlaywrightSingleton,
    burn_subtitles_ass,
    burn_subtitles_html,
    cues_to_srt,
    cues_to_vtt,
    parse_srt,
    repair_srt_cues,
    words_to_srt_cues,
)

# ---------------------------------------------------------------------------
# P0-15 + word_normalize
# ---------------------------------------------------------------------------


class TestNormalizeWord:
    def test_canonical_field_names(self) -> None:
        raw = {
            "begin_time": 1230,
            "end_time": 1500,
            "text": "你好",
            "punctuation": "，",
        }
        w = _normalize_word(raw)
        assert w is not None
        assert w.start_ms == 1230
        assert w.end_ms == 1500
        assert w.text == "你好"
        assert w.punctuation == "，"

    def test_rejects_legacy_field_names(self) -> None:
        # "start_ms" / "word" must NOT be accepted (P0-15 — no fallback).
        raw = {"start_ms": 1000, "end_ms": 1200, "word": "hi"}
        assert _normalize_word(raw) is None

    def test_missing_text_returns_none(self) -> None:
        raw = {"begin_time": 0, "end_time": 100}
        assert _normalize_word(raw) is None

    def test_speaker_id_stringified(self) -> None:
        raw = {"begin_time": 0, "end_time": 100, "text": "hi", "speaker_id": 0}
        w = _normalize_word(raw)
        assert w is not None
        assert w.speaker_id == "0"

    def test_sentence_inherits_speaker_to_word(self) -> None:
        raw = {
            "begin_time": 0,
            "end_time": 500,
            "text": "hello world",
            "speaker_id": "SPEAKER_00",
            "words": [
                {"begin_time": 0, "end_time": 200, "text": "hello"},
                {"begin_time": 250, "end_time": 500, "text": "world"},
            ],
        }
        s = _normalize_sentence(raw)
        assert s is not None
        assert s.speaker_id == "SPEAKER_00"
        assert all(w.speaker_id == "SPEAKER_00" for w in s.words)


# ---------------------------------------------------------------------------
# Qwen-MT chunking (P0-7) + prose stripping
# ---------------------------------------------------------------------------


class TestSplitLongChunk:
    def test_short_chunk_passes_through(self) -> None:
        assert _split_long_chunk("hello world") == ["hello world"]

    def test_splits_on_newlines_first(self) -> None:
        text = "\n".join(["a" * 100] * 100)  # ~10100 chars across newlines
        pieces = _split_long_chunk(text)
        assert len(pieces) >= 2
        assert all(len(p) <= 8500 for p in pieces)
        assert "".join(pieces).replace("\n", "") == text.replace("\n", "")

    def test_single_huge_line_splits_on_punctuation(self) -> None:
        # 30 sentences each ~400 chars = 12k chars, no newline.
        text = "".join(("x" * 400 + "。") for _ in range(30))
        pieces = _split_long_chunk(text)
        assert len(pieces) >= 2
        assert all(len(p) <= 8500 for p in pieces)


class TestStripProsePreamble:
    @pytest.mark.parametrize(
        "raw",
        [
            "Sure, here is the translation:\nLine A\nLine B",
            "Here is the translation\nLine A",
            "以下是翻译：\nLine A",
            "下面是翻译\nLine A",
            "Translation:\nLine A",
        ],
    )
    def test_strips_known_preambles(self, raw: str) -> None:
        out = _strip_prose_preamble(raw)
        assert out.startswith("Line A"), f"failed for: {raw!r} → {out!r}"

    def test_clean_text_unchanged(self) -> None:
        assert _strip_prose_preamble("Line A\nLine B") == "Line A\nLine B"

    def test_empty_in_empty_out(self) -> None:
        assert _strip_prose_preamble("") == ""


# ---------------------------------------------------------------------------
# words → cues
# ---------------------------------------------------------------------------


def _w(text: str, s: int, e: int, *, punct: str = "", speaker: str | None = None) -> AsrWord:
    return AsrWord(text=text, start_ms=s, end_ms=e, punctuation=punct, speaker_id=speaker)


class TestWordsToSrtCues:
    def test_basic_pack(self) -> None:
        words = [
            _w("你好", 0, 300),
            _w("世界", 300, 600, punct="。"),
            _w("再见", 1200, 1500, punct="。"),
        ]
        cues = words_to_srt_cues(words)
        # Sentence-final punct forces split → 2 cues.
        assert len(cues) == 2
        assert cues[0].start == 0.0 and cues[0].end == 0.6
        assert cues[1].start == 1.2 and cues[1].end == 1.5

    def test_long_pause_breaks_cue(self) -> None:
        words = [
            _w("a", 0, 100),
            _w("b", 100, 200),
            _w("c", 1500, 1600),  # 1.3s gap > 0.6s default
        ]
        cues = words_to_srt_cues(words)
        assert len(cues) == 2

    def test_speaker_change_breaks_cue(self) -> None:
        words = [
            _w("a", 0, 100, speaker="SPEAKER_00"),
            _w("b", 110, 200, speaker="SPEAKER_01"),
        ]
        cues = words_to_srt_cues(words)
        assert len(cues) == 2
        assert cues[0].speaker_id == "SPEAKER_00"
        assert cues[1].speaker_id == "SPEAKER_01"

    def test_empty_words_empty_cues(self) -> None:
        assert words_to_srt_cues([]) == []


# ---------------------------------------------------------------------------
# Repair (P0-12, P1-7, P1-8, P1-9)
# ---------------------------------------------------------------------------


class TestRepair:
    def test_zero_length_extended(self) -> None:  # P0-12
        cues = [SRTCue(index=1, start=1.0, end=1.0, text="x")]
        out, stats = repair_srt_cues(cues)
        assert stats["fixed_zero_length"] == 1
        assert out[0].duration >= DEFAULT_MIN_CUE_DURATION_SEC

    def test_reverse_time_extended(self) -> None:  # P0-12
        cues = [SRTCue(index=1, start=2.0, end=1.5, text="x")]
        out, stats = repair_srt_cues(cues)
        assert stats["fixed_zero_length"] == 1
        assert out[0].end > out[0].start

    def test_reorder(self) -> None:
        cues = [
            SRTCue(index=1, start=2.0, end=2.5, text="b"),
            SRTCue(index=2, start=0.5, end=1.0, text="a"),
        ]
        out, _ = repair_srt_cues(cues)
        assert [c.text for c in out] == ["a", "b"]
        assert [c.index for c in out] == [1, 2]

    def test_overlap_trimmed(self) -> None:  # P1-8
        cues = [
            SRTCue(index=1, start=0.0, end=2.0, text="a"),
            SRTCue(index=2, start=1.5, end=3.0, text="b"),
        ]
        out, stats = repair_srt_cues(cues)
        assert stats["trimmed_overlap"] == 1
        assert out[0].end <= out[1].start

    def test_short_cue_extended(self) -> None:  # P1-7
        cues = [
            SRTCue(index=1, start=0.0, end=0.1, text="a"),
            SRTCue(index=2, start=2.0, end=3.0, text="b"),
        ]
        out, stats = repair_srt_cues(cues)
        assert stats["extended_short"] >= 1
        assert out[0].duration >= DEFAULT_MIN_CUE_DURATION_SEC - 1e-6

    def test_rewrap_long_line(self) -> None:  # P1-9
        long_text = "你好" * 30  # 60 visible chars > 42 cap
        cues = [SRTCue(index=1, start=0.0, end=2.0, text=long_text)]
        out, stats = repair_srt_cues(cues)
        assert stats["rewrapped"] == 1
        assert "\n" in out[0].text


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


class TestSerialize:
    def test_srt_format(self) -> None:
        cues = [SRTCue(index=1, start=0.0, end=1.5, text="hello")]
        out = cues_to_srt(cues)
        assert "1\r\n00:00:00,000 --> 00:00:01,500\r\nhello" in out
        assert out.endswith("\r\n")

    def test_vtt_format(self) -> None:
        cues = [SRTCue(index=1, start=0.0, end=1.5, text="hello")]
        out = cues_to_vtt(cues)
        assert out.startswith("WEBVTT\n")
        assert "00:00:00.000 --> 00:00:01.500\nhello" in out

    def test_parse_srt_basic(self) -> None:
        srt = (
            "1\r\n00:00:00,000 --> 00:00:01,500\r\nhello\r\n\r\n"
            "2\r\n00:00:01,500 --> 00:00:03,000\r\nworld\r\n"
        )
        cues = parse_srt(srt)
        assert len(cues) == 2
        assert cues[0].text == "hello"
        assert cues[1].start == 1.5

    def test_round_trip(self) -> None:
        original = [
            SRTCue(index=1, start=0.0, end=1.234, text="line one"),
            SRTCue(index=2, start=2.0, end=3.5, text="line\ntwo"),
        ]
        srt = cues_to_srt(original)
        parsed = parse_srt(srt)
        assert len(parsed) == 2
        assert parsed[0].start == 0.0
        assert parsed[0].end == 1.234
        assert parsed[1].text == "line\ntwo"


# ---------------------------------------------------------------------------
# ffmpeg argument assembly (P0-16)
# ---------------------------------------------------------------------------


class TestFfmpegSubtitlesArg:
    def test_windows_path_uses_filename_keyword_and_escapes_colon(self) -> None:
        # P0-16: drive-letter colon must be escaped, wrapped in filename='...'
        out = _ffmpeg_subtitles_arg(r"C:\foo\bar.srt")
        assert out.startswith("filename='")
        assert out.endswith("'")
        assert r"C\:/foo/bar.srt" in out

    def test_posix_path_no_drive_letter(self) -> None:
        out = _ffmpeg_subtitles_arg("/tmp/foo.srt")
        assert out == "filename='/tmp/foo.srt'"

    def test_path_with_spaces_quoted(self) -> None:
        out = _ffmpeg_subtitles_arg(r"D:\my videos\out.srt")
        assert out == "filename='D\\:/my videos/out.srt'"


class TestStyleForceStyle:
    @pytest.mark.parametrize("sid", list(SUBTITLE_STYLES_BY_ID.keys()))
    def test_to_force_style_serializes(self, sid: str) -> None:
        s = SUBTITLE_STYLES_BY_ID[sid]
        rendered = s.to_force_style()
        for key in (
            "FontName=",
            "FontSize=",
            "PrimaryColour=",
            "OutlineColour=",
            "BackColour=",
            "Bold=",
            "Outline=",
            "Shadow=",
            "MarginV=",
            "Alignment=",
        ):
            assert key in rendered, f"{sid} missing {key}: {rendered}"


# ---------------------------------------------------------------------------
# A-path arg assembly — no real subprocess
# ---------------------------------------------------------------------------


class TestBurnSubtitlesAss:
    def test_unknown_style_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown style"):
            asyncio.run(
                burn_subtitles_ass(
                    "in.mp4",
                    "in.srt",
                    "out.mp4",
                    style="does-not-exist",
                    ffmpeg_path="/usr/bin/ffmpeg",
                )
            )

    def test_assembled_args_use_filename_keyword(self, tmp_path: Path) -> None:
        captured: dict[str, list[str]] = {}

        async def fake_run(args: list[str], *, timeout_sec: float, output_path: object) -> str:
            captured["args"] = args
            return str(output_path)

        # ffmpeg path must exist for find_ffmpeg
        fake_ffmpeg = tmp_path / "ffmpeg.exe"
        fake_ffmpeg.write_text("")

        with patch("subtitle_renderer._run_ffmpeg", side_effect=fake_run):
            asyncio.run(
                burn_subtitles_ass(
                    "in.mp4",
                    r"C:\subs\out.srt",
                    "out.mp4",
                    style="default",
                    ffmpeg_path=str(fake_ffmpeg),
                )
            )

        args = captured["args"]
        joined = " ".join(args)
        assert "-vf" in args
        vf_idx = args.index("-vf") + 1
        vf = args[vf_idx]
        assert vf.startswith("subtitles=filename='")
        assert "force_style='" in vf
        assert "C\\:/subs/out.srt" in vf, joined


# ---------------------------------------------------------------------------
# Playwright (P0-13 lazy import + P0-14 singleton + P1-13 fallback)
# ---------------------------------------------------------------------------


class TestPlaywrightContract:
    def test_no_top_level_playwright_import(self) -> None:
        """P0-13: subtitle_renderer must NOT import playwright at module load."""
        renderer_path = Path(__file__).resolve().parent.parent / "subtitle_renderer.py"
        text = renderer_path.read_text(encoding="utf-8")
        # Walk the file: only allow `from playwright` / `import playwright`
        # inside an indented block (i.e. inside a function/method).
        offending: list[tuple[int, str]] = []
        for i, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("from playwright") or stripped.startswith("import playwright"):
                indent = len(line) - len(stripped)
                if indent == 0:
                    offending.append((i, line))
        assert offending == [], f"P0-13 violated: top-level playwright import(s): {offending}"

    def test_playwright_singleton_class_exists(self) -> None:  # P0-14
        # Class-level state, not instance.
        assert hasattr(_PlaywrightSingleton, "get_browser")
        assert hasattr(_PlaywrightSingleton, "close")
        assert _PlaywrightSingleton._browser is None

    def test_burn_html_fallback_when_playwright_unavailable(self, tmp_path: Path) -> None:
        """P1-13: any Playwright failure → degrade to ASS path silently."""
        called: dict[str, bool] = {"ass": False}

        async def fake_get_browser() -> object:
            raise RuntimeError("Playwright not installed")

        async def fake_ass(
            video_path: object,
            srt_path: object,
            output_path: object,
            *,
            style: object,
            ffmpeg_path: object = None,
            extra_args: object = None,
            timeout_sec: float = 1800.0,
        ) -> str:
            called["ass"] = True
            return str(output_path)

        with (
            patch.object(_PlaywrightSingleton, "get_browser", fake_get_browser),
            patch("subtitle_renderer.burn_subtitles_ass", side_effect=fake_ass),
        ):
            result = asyncio.run(
                burn_subtitles_html(
                    "in.mp4",
                    "in.srt",
                    "out.mp4",
                    style="default",
                )
            )

        assert called["ass"] is True
        assert result == "out.mp4"

    def test_burn_html_no_fallback_raises(self) -> None:
        """fallback_on_error=False surfaces the underlying failure."""

        async def fake_get_browser() -> object:
            raise RuntimeError("nope")

        with (
            patch.object(_PlaywrightSingleton, "get_browser", fake_get_browser),
            pytest.raises(RuntimeError, match="nope"),
        ):
            asyncio.run(
                burn_subtitles_html(
                    "in.mp4",
                    "in.srt",
                    "out.mp4",
                    style="default",
                    fallback_on_error=False,
                )
            )

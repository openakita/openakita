"""Unit tests for footage_gate_silence — pure-numpy detection + merge math."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from footage_gate_silence import (
    _merge_and_pad,
    compute_non_silent_intervals,
)


class TestMergeAndPad:
    def test_empty_input(self) -> None:
        assert _merge_and_pad([], min_silence_len=0.15, min_sound_len=0.05, pad=0.0) == []

    def test_drops_too_short_intervals(self) -> None:
        out = _merge_and_pad(
            [(0.0, 0.02), (1.0, 1.50)],
            min_silence_len=0.15,
            min_sound_len=0.05,
            pad=0.0,
        )
        assert out == [(1.0, 1.50)]

    def test_merges_close_intervals(self) -> None:
        out = _merge_and_pad(
            [(0.0, 0.5), (0.55, 1.0)],
            min_silence_len=0.15,
            min_sound_len=0.05,
            pad=0.0,
        )
        assert out == [(0.0, 1.0)]

    def test_keeps_far_apart_intervals(self) -> None:
        out = _merge_and_pad(
            [(0.0, 0.5), (2.0, 2.5)],
            min_silence_len=0.15,
            min_sound_len=0.05,
            pad=0.0,
        )
        assert out == [(0.0, 0.5), (2.0, 2.5)]

    def test_pad_expands_symmetrically(self) -> None:
        out = _merge_and_pad(
            [(1.0, 2.0)],
            min_silence_len=0.15,
            min_sound_len=0.05,
            pad=0.1,
        )
        assert out == [(0.9, 2.1)]

    def test_pad_clamps_to_zero(self) -> None:
        out = _merge_and_pad(
            [(0.05, 1.0)],
            min_silence_len=0.15,
            min_sound_len=0.05,
            pad=0.10,
        )
        assert out[0][0] == 0.0


class TestComputeNonSilentIntervals:
    """Drive the detector with synthetic PCM by patching extract_pcm_mono."""

    @pytest.fixture
    def patch_pcm(self, monkeypatch: pytest.MonkeyPatch):
        def _install(audio: np.ndarray):
            import footage_gate_silence as mod

            monkeypatch.setattr(
                mod,
                "extract_pcm_mono",
                lambda *_a, **_kw: audio,
            )

        return _install

    def test_pure_silence_returns_empty(self, patch_pcm) -> None:
        sr = 16000
        audio = np.zeros(sr * 2, dtype=np.float32)
        patch_pcm(audio)
        intervals = compute_non_silent_intervals(
            Path("dummy.wav"), sr=sr, threshold_db=-45.0, ref="absolute"
        )
        assert intervals == []

    def test_constant_tone_returns_full_span(self, patch_pcm) -> None:
        sr = 16000
        t = np.linspace(0, 2.0, sr * 2, dtype=np.float32)
        audio = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)
        patch_pcm(audio)
        intervals = compute_non_silent_intervals(Path("dummy.wav"), sr=sr, ref="max")
        assert len(intervals) >= 1
        # Whole 2-second tone ≈ a single interval covering most of the span.
        total = sum(e - s for s, e in intervals)
        assert total >= 1.5

    def test_short_clip_fast_path(self, patch_pcm) -> None:
        sr = 16000
        # Less than frame_length (2048) — exercise short-clip branch.
        audio = (np.random.default_rng(42).standard_normal(1000) * 0.3).astype(np.float32)
        patch_pcm(audio)
        intervals = compute_non_silent_intervals(Path("dummy.wav"), sr=sr)
        assert len(intervals) == 1
        assert intervals[0] == (0.0, pytest.approx(1000 / sr, abs=1e-6))

    def test_extraction_failure_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import footage_gate_silence as mod
        from footage_gate_ffmpeg import FFmpegError

        def _raise(*_a, **_kw):
            raise FFmpegError(("ffmpeg",), 1, "boom")

        monkeypatch.setattr(mod, "extract_pcm_mono", _raise)
        assert compute_non_silent_intervals(Path("nope.wav")) == []


class TestNoUpstreamDeps:
    """Hard guard: silence module must not import aubio/madmom/librosa."""

    def test_no_audio_dep_imports(self) -> None:
        text = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("footage_gate_silence.py")
            .read_text(encoding="utf-8")
        )
        # Tokens may appear inside docstrings — strip those before searching.
        # We accept the docstring mention; only flag actual import statements.
        for banned in ("import aubio", "import madmom", "import librosa"):
            assert banned not in text, f"unexpected upstream dep import: {banned}"
        for banned in ("from aubio", "from madmom", "from librosa"):
            assert banned not in text, f"unexpected upstream dep import: {banned}"

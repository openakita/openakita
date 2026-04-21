"""Unit tests for ``dub_engine``."""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita_plugin_sdk.contrib import ReviewIssue, ReviewReport


def _de():
    import dub_engine
    return dub_engine


def _ok_review(source: str = "x.mp4") -> ReviewReport:
    return ReviewReport(
        source=source, kind="video",
        metadata={"width": 1920, "height": 1080, "duration_sec": 60.0,
                   "fps": 30.0, "codec": "h264"},
        issues=(),
    )


def _failed_review(source: str = "x.mp4") -> ReviewReport:
    return ReviewReport(
        source=source, kind="video", metadata={},
        issues=(ReviewIssue(
            code="video.too_short", severity="error",
            message="too short", metric="duration_sec",
            actual=1.0, expected=">=3.0",
        ),),
    )


def _warn_review(source: str = "x.mp4") -> ReviewReport:
    return ReviewReport(
        source=source, kind="video",
        metadata={"width": 1920, "height": 1080, "duration_sec": 60.0,
                   "fps": 12.0, "codec": "h264"},
        issues=(ReviewIssue(
            code="video.fps_too_low", severity="warning",
            message="fps low", metric="fps",
            actual=12, expected=">=15",
        ),),
    )


# ── DubSegment / DubPlan / DubResult ──────────────────────────────────


def test_dub_segment_duration_clamps_negative() -> None:
    de = _de()
    s = de.DubSegment(index=0, start_sec=2.0, end_sec=1.0, text="oops")
    assert s.duration_sec == 0.0


def test_dub_segment_to_dict_includes_translation() -> None:
    de = _de()
    s = de.DubSegment(index=1, start_sec=0.0, end_sec=1.0,
                       text="hi", translated_text="你好", language="zh-CN")
    d = s.to_dict()
    assert d["translated_text"] == "你好"
    assert d["language"] == "zh-CN"


# ── plan_dub validation ───────────────────────────────────────────────


def test_plan_dub_rejects_missing_source(tmp_path: Path) -> None:
    de = _de()
    with pytest.raises(FileNotFoundError):
        de.plan_dub(
            source_video=tmp_path / "missing.mp4",
            target_language="en",
            output_path=tmp_path / "out.mp4",
            review_fn=lambda *_a, **_kw: _ok_review(),
        )


def test_plan_dub_rejects_unknown_target_language(tmp_path: Path) -> None:
    de = _de()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"")
    with pytest.raises(ValueError, match="target_language"):
        de.plan_dub(
            source_video=src, target_language="xx",
            output_path=tmp_path / "out.mp4",
            review_fn=lambda *_a, **_kw: _ok_review(),
        )


def test_plan_dub_rejects_unknown_output_format(tmp_path: Path) -> None:
    de = _de()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"")
    with pytest.raises(ValueError, match="output_format"):
        de.plan_dub(
            source_video=src, target_language="en",
            output_path=tmp_path / "out.avi", output_format="avi",
            review_fn=lambda *_a, **_kw: _ok_review(),
        )


def test_plan_dub_rejects_invalid_duck_db(tmp_path: Path) -> None:
    de = _de()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"")
    with pytest.raises(ValueError, match="duck_db"):
        de.plan_dub(
            source_video=src, target_language="en",
            output_path=tmp_path / "out.mp4", duck_db=10,
            review_fn=lambda *_a, **_kw: _ok_review(),
        )


def test_plan_dub_returns_plan_with_review(tmp_path: Path) -> None:
    de = _de()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"")
    plan = de.plan_dub(
        source_video=src, target_language="en",
        output_path=tmp_path / "out.mp4",
        review_fn=lambda *_a, **_kw: _ok_review(str(src)),
    )
    assert plan.target_language == "en"
    assert plan.review.passed is True
    assert plan.duck_db == de.DEFAULT_DUCK_DB


def test_plan_dub_keeps_review_warnings(tmp_path: Path) -> None:
    de = _de()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"")
    plan = de.plan_dub(
        source_video=src, target_language="en",
        output_path=tmp_path / "out.mp4",
        review_fn=lambda *_a, **_kw: _warn_review(str(src)),
    )
    assert plan.review.passed is True
    assert len(plan.review.warnings) == 1


# ── ffmpeg argv builders ──────────────────────────────────────────────


def test_build_extract_audio_argv_uses_pcm_s16le(tmp_path: Path) -> None:
    de = _de()
    cmd = de.build_extract_audio_argv(tmp_path / "v.mp4",
                                        tmp_path / "a.wav")
    assert "-vn" in cmd
    assert "pcm_s16le" in cmd
    assert "16000" in cmd  # default sample rate
    assert cmd[-1] == str(tmp_path / "a.wav")


def test_build_extract_audio_argv_rejects_bad_sample_rate(tmp_path: Path) -> None:
    de = _de()
    with pytest.raises(ValueError):
        de.build_extract_audio_argv(tmp_path / "v.mp4",
                                      tmp_path / "a.wav", sample_rate=0)


def test_build_extract_audio_argv_rejects_invalid_channels(tmp_path: Path) -> None:
    de = _de()
    with pytest.raises(ValueError):
        de.build_extract_audio_argv(tmp_path / "v.mp4",
                                      tmp_path / "a.wav", channels=5)


def test_build_mux_argv_keeps_original_with_filter_complex(tmp_path: Path) -> None:
    de = _de()
    cmd = de.build_mux_argv(
        tmp_path / "v.mp4", tmp_path / "a.wav", tmp_path / "out.mp4",
        keep_original_audio=True,
    )
    assert "-filter_complex" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "amix" in fc
    assert "volume=" in fc


def test_build_mux_argv_drops_original_without_filter(tmp_path: Path) -> None:
    de = _de()
    cmd = de.build_mux_argv(
        tmp_path / "v.mp4", tmp_path / "a.wav", tmp_path / "out.mp4",
        keep_original_audio=False,
    )
    assert "-filter_complex" not in cmd
    # Maps original video + dub-only audio.
    assert "1:a:0" in cmd


def test_build_mux_argv_rejects_invalid_duck_db(tmp_path: Path) -> None:
    de = _de()
    with pytest.raises(ValueError):
        de.build_mux_argv(
            tmp_path / "v.mp4", tmp_path / "a.wav", tmp_path / "out.mp4",
            duck_db=5,
        )


# ── default_translator ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_translator_copies_text() -> None:
    de = _de()
    out = await de.default_translator(
        [de.DubSegment(index=0, start_sec=0.0, end_sec=1.0, text="hello")],
        "zh-CN",
    )
    assert out[0].translated_text == "hello"
    assert out[0].language == "zh-CN"


# ── run_dub ───────────────────────────────────────────────────────────


@pytest.fixture
def dub_runner(tmp_path: Path):
    """Return (de, plan_factory, fake_run_ffmpeg, transcribe, synthesize)."""
    de = _de()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x" * 1024)

    def make_plan(*, review: ReviewReport | None = None,
                   duck_db: int = de.DEFAULT_DUCK_DB,
                   keep_original_audio: bool = True) -> object:
        return de.plan_dub(
            source_video=src, target_language="en",
            output_path=tmp_path / "outputs" / "out.mp4",
            duck_db=duck_db, keep_original_audio=keep_original_audio,
            review_fn=lambda *_a, **_kw: review or _ok_review(str(src)),
        )

    calls: list[list[str]] = []

    async def fake_ffmpeg(cmd: list[str], *, timeout_sec: float, **_kw):
        calls.append(list(cmd))
        # The mux command's last arg is the output file → create it.
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"FAKEMP4DATA" * 100)
        return type("Result", (), {"returncode": 0, "duration_sec": 0.01})()

    async def fake_transcribe(audio_path: Path, _hint: str):
        return [
            de.DubSegment(index=0, start_sec=0.0, end_sec=2.0, text="hello"),
            de.DubSegment(index=1, start_sec=2.0, end_sec=4.0, text="world"),
        ]

    async def fake_synthesize(segments, _lang, out_path: Path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"WAV" * 100)
        return out_path

    return de, make_plan, calls, fake_ffmpeg, fake_transcribe, fake_synthesize


@pytest.mark.asyncio
async def test_run_dub_happy_path_runs_extract_then_mux(
    tmp_path: Path, dub_runner,
) -> None:
    de, make_plan, calls, fake_ffmpeg, fake_transcribe, fake_synthesize = dub_runner
    plan = make_plan()
    workdir = tmp_path / "work"

    result = await de.run_dub(
        plan,
        transcribe=fake_transcribe,
        synthesize=fake_synthesize,
        workdir=workdir,
        run_ffmpeg=fake_ffmpeg,
    )

    assert result.succeeded is True
    assert len(calls) == 2  # extract + mux
    # First call extracts audio (-vn).
    assert "-vn" in calls[0]
    # Second call writes to plan.output_path.
    assert calls[1][-1] == str(plan.output_path)
    assert result.bytes_output > 0
    assert len(result.segments) == 2


@pytest.mark.asyncio
async def test_run_dub_short_circuits_when_review_fails(
    tmp_path: Path, dub_runner,
) -> None:
    de, make_plan, calls, fake_ffmpeg, fake_transcribe, fake_synthesize = dub_runner
    plan = make_plan(review=_failed_review())
    result = await de.run_dub(
        plan,
        transcribe=fake_transcribe, synthesize=fake_synthesize,
        workdir=tmp_path / "work", run_ffmpeg=fake_ffmpeg,
    )
    assert result.succeeded is False
    assert "video.too_short" in (result.error or "")
    assert len(calls) == 0  # no ffmpeg ever ran


@pytest.mark.asyncio
async def test_run_dub_calls_on_stage_in_order(
    tmp_path: Path, dub_runner,
) -> None:
    de, make_plan, calls, fake_ffmpeg, fake_transcribe, fake_synthesize = dub_runner
    stages: list[str] = []
    plan = make_plan()
    await de.run_dub(
        plan, transcribe=fake_transcribe, synthesize=fake_synthesize,
        workdir=tmp_path / "work", run_ffmpeg=fake_ffmpeg,
        on_stage=stages.append,
    )
    assert stages == ["extract", "transcribe", "translate", "synthesize", "mux"]


@pytest.mark.asyncio
async def test_run_dub_uses_translator_override(
    tmp_path: Path, dub_runner,
) -> None:
    de, make_plan, calls, fake_ffmpeg, fake_transcribe, fake_synthesize = dub_runner

    async def loud_translator(segs, lang):
        return [
            de.DubSegment(
                index=s.index, start_sec=s.start_sec, end_sec=s.end_sec,
                text=s.text, translated_text=s.text.upper(), language=lang,
            )
            for s in segs
        ]

    plan = make_plan()
    result = await de.run_dub(
        plan, transcribe=fake_transcribe, synthesize=fake_synthesize,
        translate=loud_translator,
        workdir=tmp_path / "work", run_ffmpeg=fake_ffmpeg,
    )
    assert result.segments[0].translated_text == "HELLO"


@pytest.mark.asyncio
async def test_run_dub_keep_original_audio_false_drops_filter(
    tmp_path: Path, dub_runner,
) -> None:
    de, make_plan, calls, fake_ffmpeg, fake_transcribe, fake_synthesize = dub_runner
    plan = make_plan(keep_original_audio=False)
    await de.run_dub(
        plan, transcribe=fake_transcribe, synthesize=fake_synthesize,
        workdir=tmp_path / "work", run_ffmpeg=fake_ffmpeg,
    )
    mux_cmd = calls[1]
    assert "-filter_complex" not in mux_cmd


# ── verification (D2.10) ──────────────────────────────────────────────


def _result_with(de, *, succeeded=True, segments=None, bytes_output=1,
                  warnings=False, error=None):
    src = Path("x.mp4")
    review = _warn_review() if warnings else _ok_review()
    plan = de.DubPlan(
        source_video=src, target_language="en", output_format="mp4",
        output_path=Path("out.mp4"),
        duck_db=de.DEFAULT_DUCK_DB, keep_original_audio=True,
        review=review,
    )
    return de.DubResult(
        plan=plan,
        segments=list(segments or []),
        extracted_audio_path=Path("a.wav") if succeeded else None,
        dubbed_audio_path=Path("a.wav") if succeeded else None,
        output_video_path=Path("out.mp4") if succeeded else None,
        elapsed_sec=1.0,
        bytes_output=bytes_output,
        error=error if not succeeded else None,
    )


def test_verification_green_on_clean_result() -> None:
    de = _de()
    segments = [
        de.DubSegment(index=0, start_sec=0.0, end_sec=1.0,
                       text="hi", translated_text="你好"),
    ]
    v = de.to_verification(_result_with(de, segments=segments, bytes_output=1024))
    assert v.verified is True


def test_verification_flags_failure_with_error_field() -> None:
    de = _de()
    v = de.to_verification(_result_with(
        de, succeeded=False, segments=[], error="something exploded",
    ))
    assert v.verified is False
    assert any(f.path == "$.error" for f in v.low_confidence_fields)


def test_verification_flags_zero_bytes_when_succeeded() -> None:
    de = _de()
    segments = [
        de.DubSegment(index=0, start_sec=0.0, end_sec=1.0,
                       text="hi", translated_text="你好"),
    ]
    v = de.to_verification(_result_with(de, segments=segments, bytes_output=0))
    assert v.verified is False
    assert any("bytes_output" in f.path for f in v.low_confidence_fields)


def test_verification_flags_no_segments() -> None:
    de = _de()
    v = de.to_verification(_result_with(de, segments=[], bytes_output=1024))
    assert v.verified is False
    assert any(f.path == "$.segments" for f in v.low_confidence_fields)


def test_verification_flags_empty_translations() -> None:
    de = _de()
    segments = [
        de.DubSegment(index=0, start_sec=0.0, end_sec=1.0,
                       text="hi", translated_text="你好"),
        de.DubSegment(index=1, start_sec=1.0, end_sec=2.0,
                       text="bye", translated_text=""),
    ]
    v = de.to_verification(_result_with(de, segments=segments, bytes_output=1024))
    assert v.verified is False
    assert any("translated_text" in f.path for f in v.low_confidence_fields)


def test_verification_passes_through_review_warnings() -> None:
    de = _de()
    segments = [
        de.DubSegment(index=0, start_sec=0.0, end_sec=1.0,
                       text="hi", translated_text="你好"),
    ]
    v = de.to_verification(_result_with(
        de, segments=segments, bytes_output=1024, warnings=True,
    ))
    assert v.verified is False
    assert any("review.warnings" in f.path for f in v.low_confidence_fields)


# ── small helpers ─────────────────────────────────────────────────────


def test_humanise_segment_summary_empty() -> None:
    de = _de()
    assert "0 segments" in de.humanise_segment_summary([])


def test_humanise_segment_summary_non_empty() -> None:
    de = _de()
    segments = [
        de.DubSegment(index=0, start_sec=0.0, end_sec=2.0,
                       text="hi", translated_text="你好"),
        de.DubSegment(index=1, start_sec=2.0, end_sec=4.0,
                       text="bye", translated_text="再见"),
    ]
    out = de.humanise_segment_summary(segments)
    assert "段" in out and "字" in out


def test_safe_workdir_name_strips_unsafe_chars(tmp_path: Path) -> None:
    de = _de()
    name = de.safe_workdir_name(Path("/tmp/our cool!?video.mp4"))
    assert "!" not in name
    assert "?" not in name
    assert " " not in name

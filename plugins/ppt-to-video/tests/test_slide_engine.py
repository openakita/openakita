"""Unit tests for ``slide_engine`` — pure logic only.

We never spawn ``soffice`` or ``ffmpeg`` and we don't require
``python-pptx`` to be installed: every external integration is
either dependency-injected or mocked.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────


def _engine():
    import slide_engine
    return slide_engine


def _write_fake_pptx(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"PK\x03\x04 fake pptx")  # minimal zip-ish header
    return p


def _seed_pngs(out_dir: Path, n: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(1, n + 1):
        p = out_dir / f"deck-{i:03d}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        paths.append(p)
    return paths


# ── module-level dep helpers ──────────────────────────────────────────


def test_libreoffice_available_returns_bool() -> None:
    me = _engine()
    assert isinstance(me.libreoffice_available(), bool)


def test_resolve_libreoffice_returns_str_or_none() -> None:
    me = _engine()
    val = me.resolve_libreoffice()
    assert val is None or isinstance(val, str)


def test_resolve_libreoffice_falls_back_to_known_paths(monkeypatch) -> None:
    me = _engine()
    monkeypatch.setattr(me.shutil, "which", lambda _name: None)

    # Engine checks a fixed list of soffice paths via Path.is_file().
    # We mark only the first Windows path as existing.
    target = r"C:\Program Files\LibreOffice\program\soffice.exe"

    def _is_file_only_for_target(self) -> bool:
        return str(self) == target

    monkeypatch.setattr(me.Path, "is_file", _is_file_only_for_target)
    assert me.resolve_libreoffice() == target


def test_pptx_available_matches_real_install() -> None:
    me = _engine()
    import importlib
    expected = importlib.util.find_spec("pptx") is not None
    assert me.pptx_available() is expected


def test_ffmpeg_available_uses_shutil_which(monkeypatch) -> None:
    me = _engine()
    monkeypatch.setattr(me.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    assert me.ffmpeg_available() is True
    monkeypatch.setattr(me.shutil, "which", lambda _n: None)
    assert me.ffmpeg_available() is False


# ── soffice command builder ───────────────────────────────────────────


def test_soffice_convert_command_basic_shape(tmp_path: Path) -> None:
    me = _engine()
    cmd = me.soffice_convert_command(
        soffice="/usr/bin/soffice",
        input_path=str(tmp_path / "deck.pptx"),
        out_dir=tmp_path / "out",
    )
    assert cmd[0] == "/usr/bin/soffice"
    assert "--headless" in cmd
    assert "--convert-to" in cmd
    assert "png" in cmd  # default format
    assert "--outdir" in cmd
    assert str(tmp_path / "deck.pptx") in cmd


def test_soffice_convert_command_creates_outdir(tmp_path: Path) -> None:
    me = _engine()
    out = tmp_path / "fresh"
    assert not out.exists()
    me.soffice_convert_command(
        soffice="soffice", input_path=str(tmp_path / "x.pptx"), out_dir=out,
    )
    assert out.is_dir()


def test_soffice_convert_command_honors_image_format(tmp_path: Path) -> None:
    me = _engine()
    cmd = me.soffice_convert_command(
        soffice="soffice", input_path=str(tmp_path / "deck.pptx"),
        out_dir=tmp_path, image_format="jpg",
    )
    assert "jpg" in cmd
    assert "png" not in cmd


# ── PNG discovery ─────────────────────────────────────────────────────


def test_discover_exported_pngs_sorts_by_trailing_index(tmp_path: Path) -> None:
    me = _engine()
    out = tmp_path / "imgs"
    out.mkdir()
    (out / "deck-2.png").write_bytes(b"\x89PNG")
    (out / "deck-10.png").write_bytes(b"\x89PNG")
    (out / "deck-1.png").write_bytes(b"\x89PNG")
    pngs = me.discover_exported_pngs(out)
    assert [p.name for p in pngs] == ["deck-1.png", "deck-2.png", "deck-10.png"]


def test_discover_exported_pngs_handles_missing_dir(tmp_path: Path) -> None:
    me = _engine()
    assert me.discover_exported_pngs(tmp_path / "nope") == []


def test_discover_exported_pngs_unindexed_after_indexed(tmp_path: Path) -> None:
    me = _engine()
    out = tmp_path / "imgs"
    out.mkdir()
    (out / "single.png").write_bytes(b"\x89PNG")
    (out / "deck-2.png").write_bytes(b"\x89PNG")
    pngs = me.discover_exported_pngs(out)
    assert pngs[0].name == "deck-2.png"
    assert pngs[-1].name == "single.png"


# ── extract_slide_notes (mocked) ──────────────────────────────────────


def test_extract_slide_notes_raises_for_missing_pptx_module(
    monkeypatch, tmp_path: Path,
) -> None:
    me = _engine()
    monkeypatch.setattr(me, "pptx_available", lambda: False)
    src = _write_fake_pptx(tmp_path / "x.pptx")
    with pytest.raises(ImportError):
        me.extract_slide_notes(src)


def test_extract_slide_notes_raises_for_missing_file(monkeypatch, tmp_path: Path) -> None:
    me = _engine()
    monkeypatch.setattr(me, "pptx_available", lambda: True)
    with pytest.raises(FileNotFoundError):
        me.extract_slide_notes(tmp_path / "nope.pptx")


def test_extract_slide_notes_uses_python_pptx(monkeypatch, tmp_path: Path) -> None:
    me = _engine()
    monkeypatch.setattr(me, "pptx_available", lambda: True)
    src = _write_fake_pptx(tmp_path / "deck.pptx")

    class _NotesFrame:
        def __init__(self, text: str) -> None:
            self.text = text

    class _NotesSlide:
        def __init__(self, text: str) -> None:
            self.notes_text_frame = _NotesFrame(text)

    class _Slide:
        def __init__(self, text: str | None) -> None:
            self.has_notes_slide = text is not None
            self.notes_slide = _NotesSlide(text) if text is not None else None

    class _Prs:
        slides = [_Slide("slide one notes"), _Slide(""), _Slide(None)]

    fake_pptx = type(sys)("pptx")
    fake_pptx.Presentation = lambda _p: _Prs()
    monkeypatch.setitem(sys.modules, "pptx", fake_pptx)

    out = me.extract_slide_notes(src)
    assert out == ["slide one notes", "", ""]


# ── ffmpeg per-clip command ───────────────────────────────────────────


def test_build_image_clip_command_with_audio(tmp_path: Path) -> None:
    me = _engine()
    cmd = me.build_image_clip_command(
        image_path=tmp_path / "i.png", audio_path=tmp_path / "a.mp3",
        duration_sec=5.0, output_path=tmp_path / "out.mp4",
    )
    assert "-loop" in cmd
    assert "-shortest" in cmd
    assert "-c:v" in cmd
    assert "libx264" in cmd
    assert str(tmp_path / "a.mp3") in cmd


def test_build_image_clip_command_silence_uses_anullsrc(tmp_path: Path) -> None:
    me = _engine()
    cmd = me.build_image_clip_command(
        image_path=tmp_path / "i.png", audio_path=None,
        duration_sec=3.0, output_path=tmp_path / "out.mp4",
    )
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" in cmd
    assert "-shortest" not in cmd
    # duration appears twice: once as -t for the lavfi, once as final -t
    assert sum(1 for x in cmd if x == "3.000") == 2


def test_build_image_clip_command_zero_duration_no_audio_raises(tmp_path: Path) -> None:
    me = _engine()
    with pytest.raises(ValueError):
        me.build_image_clip_command(
            image_path=tmp_path / "i.png", audio_path=None,
            duration_sec=0.0, output_path=tmp_path / "out.mp4",
        )


def test_build_image_clip_command_pads_to_even_dimensions(tmp_path: Path) -> None:
    me = _engine()
    cmd = me.build_image_clip_command(
        image_path=tmp_path / "i.png", audio_path=None,
        duration_sec=2.0, output_path=tmp_path / "out.mp4",
    )
    assert any("pad=ceil(iw/2)*2:ceil(ih/2)*2" in c for c in cmd)


def test_build_image_clip_command_honors_crf_and_preset(tmp_path: Path) -> None:
    me = _engine()
    cmd = me.build_image_clip_command(
        image_path=tmp_path / "i.png", audio_path=tmp_path / "a.mp3",
        duration_sec=5.0, output_path=tmp_path / "out.mp4",
        crf=15, libx264_preset="slow",
    )
    assert "15" in cmd
    assert "slow" in cmd


# ── ffmpeg concat command ─────────────────────────────────────────────


def test_build_concat_command_creates_list_file(tmp_path: Path) -> None:
    me = _engine()
    list_p = tmp_path / "concat.txt"
    cmd = me.build_concat_command(
        clip_paths=[tmp_path / "a.mp4", tmp_path / "b.mp4"],
        list_file=list_p, output_path=tmp_path / "out.mp4",
    )
    assert list_p.is_file()
    body = list_p.read_text(encoding="utf-8")
    assert "a.mp4" in body
    assert "b.mp4" in body
    assert "-f" in cmd and "concat" in cmd
    assert "-c" in cmd and "copy" in cmd


def test_build_concat_command_requires_at_least_one_clip(tmp_path: Path) -> None:
    me = _engine()
    with pytest.raises(ValueError):
        me.build_concat_command(
            clip_paths=[], list_file=tmp_path / "list.txt",
            output_path=tmp_path / "out.mp4",
        )


def test_build_concat_command_writes_posix_paths(tmp_path: Path) -> None:
    me = _engine()
    list_p = tmp_path / "concat.txt"
    me.build_concat_command(
        clip_paths=[tmp_path / "x.mp4"],
        list_file=list_p, output_path=tmp_path / "y.mp4",
    )
    body = list_p.read_text(encoding="utf-8")
    # On Windows ``Path.as_posix`` produces forward slashes — concat
    # demuxer needs forward slashes regardless of OS.
    assert "\\" not in body


# ── plan_video ────────────────────────────────────────────────────────


def test_plan_video_rejects_empty_input(tmp_path: Path) -> None:
    me = _engine()
    with pytest.raises(ValueError):
        me.plan_video(
            input_path="", output_path=str(tmp_path / "o.mp4"),
            work_dir=str(tmp_path / "w"),
            convert_runner=lambda _c: None,
            notes_extractor=lambda _p: [],
            pngs_discoverer=lambda _d: [],
        )


def test_plan_video_rejects_non_mp4_output(tmp_path: Path) -> None:
    me = _engine()
    src = _write_fake_pptx(tmp_path / "deck.pptx")
    with pytest.raises(ValueError):
        me.plan_video(
            input_path=str(src), output_path=str(tmp_path / "out.mov"),
            work_dir=str(tmp_path / "w"),
            convert_runner=lambda _c: None,
            notes_extractor=lambda _p: ["a"],
            pngs_discoverer=lambda d: _seed_pngs(Path(d), 1),
        )


def test_plan_video_rejects_unsupported_extension(tmp_path: Path) -> None:
    me = _engine()
    src = tmp_path / "doc.docx"
    src.write_bytes(b"x")
    with pytest.raises(ValueError):
        me.plan_video(
            input_path=str(src), output_path=str(tmp_path / "out.mp4"),
            work_dir=str(tmp_path / "w"),
            convert_runner=lambda _c: None,
            notes_extractor=lambda _p: [],
            pngs_discoverer=lambda _d: [],
        )


def test_plan_video_rejects_missing_file(tmp_path: Path) -> None:
    me = _engine()
    with pytest.raises(FileNotFoundError):
        me.plan_video(
            input_path=str(tmp_path / "nope.pptx"),
            output_path=str(tmp_path / "out.mp4"),
            work_dir=str(tmp_path / "w"),
            convert_runner=lambda _c: None,
            notes_extractor=lambda _p: [],
            pngs_discoverer=lambda _d: [],
        )


def test_plan_video_rejects_silent_slide_sec_out_of_range(tmp_path: Path) -> None:
    me = _engine()
    src = _write_fake_pptx(tmp_path / "deck.pptx")
    with pytest.raises(ValueError):
        me.plan_video(
            input_path=str(src), output_path=str(tmp_path / "o.mp4"),
            work_dir=str(tmp_path / "w"), silent_slide_sec=0.1,
            convert_runner=lambda _c: None,
            notes_extractor=lambda _p: ["a"],
            pngs_discoverer=lambda d: _seed_pngs(Path(d), 1),
        )


def test_plan_video_rejects_when_no_pngs_produced(tmp_path: Path) -> None:
    me = _engine()
    src = _write_fake_pptx(tmp_path / "deck.pptx")
    with pytest.raises(RuntimeError):
        me.plan_video(
            input_path=str(src), output_path=str(tmp_path / "o.mp4"),
            work_dir=str(tmp_path / "w"),
            convert_runner=lambda _c: None,
            notes_extractor=lambda _p: [],
            pngs_discoverer=lambda _d: [],
        )


def test_plan_video_pads_short_notes_with_empty_strings(tmp_path: Path) -> None:
    me = _engine()
    src = _write_fake_pptx(tmp_path / "deck.pptx")
    plan = me.plan_video(
        input_path=str(src), output_path=str(tmp_path / "o.mp4"),
        work_dir=str(tmp_path / "w"),
        convert_runner=lambda _c: None,
        notes_extractor=lambda _p: ["only one"],
        pngs_discoverer=lambda d: _seed_pngs(Path(d), 3),
    )
    assert plan.slide_count == 3
    assert plan.slides[0].notes == "only one"
    assert plan.slides[1].notes == ""
    assert plan.slides[2].notes == ""
    assert plan.empty_notes_count == 2


def test_plan_video_truncates_extra_notes(tmp_path: Path) -> None:
    me = _engine()
    src = _write_fake_pptx(tmp_path / "deck.pptx")
    plan = me.plan_video(
        input_path=str(src), output_path=str(tmp_path / "o.mp4"),
        work_dir=str(tmp_path / "w"),
        convert_runner=lambda _c: None,
        notes_extractor=lambda _p: ["a", "b", "c", "d"],
        pngs_discoverer=lambda d: _seed_pngs(Path(d), 2),
    )
    assert plan.slide_count == 2
    assert [s.notes for s in plan.slides] == ["a", "b"]


def test_plan_video_to_dict_includes_aggregates(tmp_path: Path) -> None:
    me = _engine()
    src = _write_fake_pptx(tmp_path / "deck.pptx")
    plan = me.plan_video(
        input_path=str(src), output_path=str(tmp_path / "o.mp4"),
        work_dir=str(tmp_path / "w"),
        convert_runner=lambda _c: None,
        notes_extractor=lambda _p: ["hello", "world"],
        pngs_discoverer=lambda d: _seed_pngs(Path(d), 2),
    )
    d = plan.to_dict()
    assert d["slide_count"] == 2
    assert d["notes_total_chars"] == len("hello") + len("world")
    assert d["empty_notes_count"] == 0
    assert len(d["slides"]) == 2


def test_plan_video_default_path_resolution_when_no_runner(
    tmp_path: Path, monkeypatch,
) -> None:
    me = _engine()
    src = _write_fake_pptx(tmp_path / "deck.pptx")
    monkeypatch.setattr(me, "resolve_libreoffice", lambda: None)
    with pytest.raises(FileNotFoundError):
        me.plan_video(
            input_path=str(src), output_path=str(tmp_path / "o.mp4"),
            work_dir=str(tmp_path / "w"),
            notes_extractor=lambda _p: [],
            pngs_discoverer=lambda _d: [],
        )


# ── render_clips ──────────────────────────────────────────────────────


def _build_plan_with_pngs(me, tmp_path: Path, n: int, *, notes=None):
    src = _write_fake_pptx(tmp_path / "deck.pptx")
    return me.plan_video(
        input_path=str(src), output_path=str(tmp_path / "o.mp4"),
        work_dir=str(tmp_path / "w"),
        convert_runner=lambda _c: None,
        notes_extractor=lambda _p: notes or ["x"] * n,
        pngs_discoverer=lambda d: _seed_pngs(Path(d), n),
    )


def test_render_clips_invokes_runner_per_slide(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 3, notes=["a", "", "c"])
    plan.slides[0].audio_path = str(tmp_path / "a.mp3")
    plan.slides[0].audio_duration_sec = 2.5
    calls = []
    me.render_clips(plan, ffmpeg_runner=lambda c: calls.append(c))
    assert len(calls) == 3
    for slide in plan.slides:
        assert slide.clip_path is not None


def test_render_clips_returns_audio_total_and_fallbacks(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 2)
    plan.slides[0].audio_path = str(tmp_path / "a.mp3")
    plan.slides[0].audio_duration_sec = 4.0
    audio_total, fallbacks = me.render_clips(plan, ffmpeg_runner=lambda _c: None)
    # First slide contributes 4.0; second slide falls back to silent_slide_sec (default 2.0)
    assert audio_total == pytest.approx(4.0 + plan.silent_slide_sec)
    assert fallbacks == 1


def test_render_clips_progress_callback_fires(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 4)
    seen: list[tuple[int, int]] = []
    me.render_clips(
        plan, ffmpeg_runner=lambda _c: None,
        on_progress=lambda d, t: seen.append((d, t)),
    )
    assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]


# ── run_pipeline ──────────────────────────────────────────────────────


def test_run_pipeline_full_happy_path(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 3, notes=["one", "two", "three"])

    def _tts(text, voice):
        p = tmp_path / "audio" / f"{abs(hash(text)) % 1_000_000}.mp3"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00" * 32)
        return p, 1.5

    def _runner(_c):
        # Simulate concat creating the final file.
        Path(plan.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(plan.output_path).write_bytes(b"\x00" * 64)

    result = me.run_pipeline(plan, tts_synth=_tts, ffmpeg_runner=_runner)
    assert result.slide_count == 3
    assert result.tts_fallbacks == 0
    assert result.audio_total_sec == pytest.approx(4.5)
    assert result.output_size_bytes == 64
    assert result.tts_provider_used == "injected"


def test_run_pipeline_tts_returning_none_falls_back_to_silence(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 2, notes=["a", "b"])

    def _tts(text, voice):
        return None  # TTS provider unavailable

    def _runner(_c):
        Path(plan.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(plan.output_path).write_bytes(b"x")

    result = me.run_pipeline(plan, tts_synth=_tts, ffmpeg_runner=_runner)
    assert result.tts_fallbacks == 2


def test_run_pipeline_tts_exception_does_not_kill_job(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 2, notes=["a", "b"])

    def _tts(text, voice):
        raise RuntimeError("network down")

    def _runner(_c):
        Path(plan.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(plan.output_path).write_bytes(b"x")

    result = me.run_pipeline(plan, tts_synth=_tts, ffmpeg_runner=_runner)
    assert result.tts_fallbacks == 2


def test_run_pipeline_no_tts_synth_treats_all_as_silent(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 2, notes=["a", "b"])

    def _runner(_c):
        Path(plan.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(plan.output_path).write_bytes(b"x")

    result = me.run_pipeline(plan, tts_synth=None, ffmpeg_runner=_runner)
    assert result.tts_fallbacks == 2


def test_run_pipeline_progress_callback_visits_stages(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 2, notes=["a", "b"])

    def _tts(text, voice):
        p = tmp_path / "audio" / f"{abs(hash(text))}.mp3"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00")
        return p, 1.0

    def _runner(_c):
        Path(plan.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(plan.output_path).write_bytes(b"x")

    stages: list[str] = []
    me.run_pipeline(
        plan, tts_synth=_tts, ffmpeg_runner=_runner,
        on_progress=lambda stage, _d, _t: stages.append(stage),
    )
    assert "tts" in stages
    assert "clips" in stages
    assert "concat" in stages


def test_run_pipeline_records_size_zero_when_concat_does_not_write(
    tmp_path: Path,
) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 1, notes=["only"])

    def _tts(text, voice):
        p = tmp_path / "a.mp3"
        p.write_bytes(b"\x00")
        return p, 1.0

    result = me.run_pipeline(plan, tts_synth=_tts, ffmpeg_runner=lambda _c: None)
    assert result.output_size_bytes == 0


# ── verification ──────────────────────────────────────────────────────


def _result(me, plan, *, slide_count=2, audio_total=4.0,
            output_size=128, tts_fallbacks=0, provider="edge"):
    return me.SlideVideoResult(
        plan=plan, output_path=plan.output_path,
        elapsed_sec=1.0, slide_count=slide_count,
        audio_total_sec=audio_total, output_size_bytes=output_size,
        tts_provider_used=provider, tts_fallbacks=tts_fallbacks,
    )


def test_to_verification_green_when_everything_normal(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 2, notes=["a", "b"])
    v = me.to_verification(_result(me, plan))
    assert v.verified is True
    assert v.low_confidence_fields == []


def test_to_verification_flags_zero_slides(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 1, notes=["only"])
    plan.slides = []
    v = me.to_verification(_result(me, plan, slide_count=0))
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.slide_count" in paths
    assert v.verified is False


def test_to_verification_flags_majority_empty_notes(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 4, notes=["a", "", "", ""])
    v = me.to_verification(_result(me, plan, slide_count=4))
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.plan.empty_notes_count" in paths


def test_to_verification_flags_high_tts_fallbacks(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 4, notes=["a", "b", "c", "d"])
    v = me.to_verification(_result(me, plan, slide_count=4, tts_fallbacks=3))
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.tts_fallbacks" in paths


def test_to_verification_does_not_double_count_empty_notes_as_tts_fallback(
    tmp_path: Path,
) -> None:
    """Empty notes are *expected* to fall back; only count "real" failures."""
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 4, notes=["a", "", "", ""])
    # 3 empty notes → 3 fallbacks but they're all *expected*, not failures
    v = me.to_verification(_result(me, plan, slide_count=4, tts_fallbacks=3))
    paths = [f.path for f in v.low_confidence_fields]
    # empty_notes flag stays, but the high-fallback flag should NOT
    assert "$.plan.empty_notes_count" in paths
    assert "$.tts_fallbacks" not in paths


def test_to_verification_flags_zero_output_size(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 2, notes=["a", "b"])
    v = me.to_verification(_result(me, plan, output_size=0))
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.output_size_bytes" in paths


def test_to_verification_flags_stub_tts_provider(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 2, notes=["a", "b"])
    plan.tts_provider = "stub"
    v = me.to_verification(_result(me, plan))
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.plan.tts_provider" in paths


# ── _default_ffmpeg_runner ────────────────────────────────────────────


def test_default_ffmpeg_runner_calls_subprocess_run(monkeypatch) -> None:
    me = _engine()
    seen = {}

    def _fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs

    monkeypatch.setattr(me.subprocess, "run", _fake_run)
    me._default_ffmpeg_runner(["ffmpeg", "-i", "x.mp4"])
    assert seen["cmd"] == ["ffmpeg", "-i", "x.mp4"]
    assert seen["kwargs"]["check"] is True


# ── SlideMeta / SlideVideoResult dataclasses ──────────────────────────


def test_slide_meta_to_dict_round_trip() -> None:
    me = _engine()
    s = me.SlideMeta(index=1, image_path="a.png", notes="hi")
    d = s.to_dict()
    assert d["index"] == 1
    assert d["image_path"] == "a.png"
    assert d["audio_path"] is None


def test_slide_video_result_to_dict_includes_plan(tmp_path: Path) -> None:
    me = _engine()
    plan = _build_plan_with_pngs(me, tmp_path, 1, notes=["x"])
    r = _result(me, plan, slide_count=1)
    d = r.to_dict()
    assert d["slide_count"] == 1
    assert "plan" in d
    assert d["plan"]["slide_count"] == 1


# ── exported symbols ──────────────────────────────────────────────────


def test_engine_has_documented_exports() -> None:
    me = _engine()
    for name in [
        "SlideMeta", "SlidePlan", "SlideVideoResult",
        "plan_video", "run_pipeline", "render_clips", "to_verification",
        "build_image_clip_command", "build_concat_command",
        "soffice_convert_command", "extract_slide_notes",
        "libreoffice_available", "pptx_available", "ffmpeg_available",
        "resolve_libreoffice",
        "DEFAULT_FPS", "DEFAULT_VOICE", "DEFAULT_SILENT_SLIDE_SEC",
        "SUPPORTED_INPUT_EXTENSIONS",
    ]:
        assert hasattr(me, name), f"missing engine symbol: {name}"

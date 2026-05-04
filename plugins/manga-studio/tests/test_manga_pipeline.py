"""Phase 2.7 — manga_pipeline.py orchestration tests.

We mock every external boundary (wanxiang / ark / tts / ffmpeg /
script_writer / task_manager) and exercise:

- Happy path: 2 panels → all 8 steps run, final.mp4 written.
- Per-panel image failure → soft error recorded, pipeline continues.
- I2V → T2V auto-fallback on ``moderation_face`` rejection.
- Hard auth failure on first panel aborts with PipelineError.
- Progress callback fires for every step boundary.
- Working dir is laid out correctly (panels/, audio/, muxed/, final.mp4).
- Subtitle SRT generation skips silent panels.
- ``_ratio_to_size`` and ``_scaled_progress`` pure helpers.

Each fake client mirrors the real client's interface but lets us
choose what each call returns, so we don't need network or FFmpeg.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from manga_inline.vendor_client import VendorError
from manga_pipeline import (
    STEP_WEIGHTS,
    MangaPipeline,
    MangaPipelineConfig,
    PanelArtifact,
    PipelineError,
    _ratio_to_size,
    _scaled_progress,
)

# ─── Pure helpers ────────────────────────────────────────────────────────


def test_ratio_to_size_known() -> None:
    assert _ratio_to_size("9:16") == "720*1280"
    assert _ratio_to_size("16:9") == "1280*720"
    assert _ratio_to_size("1:1") == "1024*1024"
    assert _ratio_to_size("4:5") == "1024*1280"


def test_ratio_to_size_unknown_falls_back() -> None:
    assert _ratio_to_size("21:9") == "1024*1024"


def test_scaled_progress_within_step_band() -> None:
    """Step ``panel_video`` is band [35, 75]. With 4 panels, panel 0 ends
    at 35 + (75-35)/4 = 45; panel 3 ends at 75."""
    assert _scaled_progress("panel_video", 0, 4) == 45
    assert _scaled_progress("panel_video", 3, 4) == 75


def test_scaled_progress_unknown_step_uses_default_band() -> None:
    """Unknown step name → uses the default ``(0, 100)`` band, so a
    1-of-1 panel maps to 100%."""
    assert _scaled_progress("nonexistent", 0, 1) == 100


def test_step_weights_sum_to_100() -> None:
    """Sanity: every band starts where the previous ended; final band
    closes at 100."""
    last_hi = 0
    for step, (lo, hi) in STEP_WEIGHTS.items():
        assert lo == last_hi, f"gap before {step}: {lo} != {last_hi}"
        last_hi = hi
    assert last_hi == 100


# ─── Fake clients / services ──────────────────────────────────────────────


class _FakeWanxiang:
    def __init__(
        self,
        *,
        submit_returns: list[Any] | None = None,
        poll_returns: dict[str, Any] | None = None,
        submit_raises: list[Exception | None] | None = None,
    ) -> None:
        self.submit_calls: list[dict[str, Any]] = []
        self.poll_calls: list[str] = []
        self._submit_returns = submit_returns or []
        self._submit_raises = submit_raises or []
        self._poll_default = poll_returns or {
            "task_id": "tid",
            "status": "SUCCEEDED",
            "is_done": True,
            "is_ok": True,
            "output_url": "https://oss/img.png",
            "output_kind": "image",
        }

    async def submit_image(self, **kwargs: Any) -> str:
        i = len(self.submit_calls)
        self.submit_calls.append(kwargs)
        if self._submit_raises and i < len(self._submit_raises):
            raised = self._submit_raises[i]
            if raised is not None:
                raise raised
        if self._submit_returns and i < len(self._submit_returns):
            return self._submit_returns[i]
        return f"img_task_{i}"

    async def poll_until_done(self, task_id: str, **_: Any) -> dict[str, Any]:
        self.poll_calls.append(task_id)
        return dict(self._poll_default)


class _FakeArk:
    def __init__(
        self,
        *,
        i2v_raises: list[Exception | None] | None = None,
        t2v_raises: list[Exception | None] | None = None,
        poll_returns: dict[str, Any] | None = None,
    ) -> None:
        self.i2v_calls: list[dict[str, Any]] = []
        self.t2v_calls: list[dict[str, Any]] = []
        self.poll_calls: list[str] = []
        self._i2v_raises = i2v_raises or []
        self._t2v_raises = t2v_raises or []
        self._poll_default = poll_returns or {
            "id": "vid_task",
            "status": "succeeded",
            "content": {"video_url": "https://oss/clip.mp4"},
        }

    async def submit_seedance_i2v(self, **kwargs: Any) -> dict[str, Any]:
        i = len(self.i2v_calls)
        self.i2v_calls.append(kwargs)
        if self._i2v_raises and i < len(self._i2v_raises):
            err = self._i2v_raises[i]
            if err is not None:
                raise err
        return {"id": f"i2v_{i}"}

    async def submit_seedance_t2v(self, **kwargs: Any) -> dict[str, Any]:
        i = len(self.t2v_calls)
        self.t2v_calls.append(kwargs)
        if self._t2v_raises and i < len(self._t2v_raises):
            err = self._t2v_raises[i]
            if err is not None:
                raise err
        return {"id": f"t2v_{i}"}

    async def poll_until_done(self, task_id: str, **_: Any) -> dict[str, Any]:
        self.poll_calls.append(task_id)
        return dict(self._poll_default)

    @staticmethod
    def extract_video_url(response: dict[str, Any]) -> str | None:
        return response.get("content", {}).get("video_url")


class _FakeTTS:
    def __init__(
        self,
        *,
        synth_raises: list[Exception | None] | None = None,
        duration_sec: float = 3.0,
    ) -> None:
        self.synth_calls: list[dict[str, Any]] = []
        self._raises = synth_raises or []
        self._duration = duration_sec

    async def synth(self, **kwargs: Any) -> dict[str, Any]:
        i = len(self.synth_calls)
        self.synth_calls.append(kwargs)
        if self._raises and i < len(self._raises):
            err = self._raises[i]
            if err is not None:
                raise err
        # Write a fake audio file so the muxer can find it.
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"FAKEAUDIO")
        return {
            "bytes": b"FAKEAUDIO",
            "duration_sec": self._duration,
            "engine": "edge",
            "voice_id": kwargs["voice_id"],
            "path": str(out),
        }


class _FakeFFmpeg:
    def __init__(self) -> None:
        self.attach_calls: list[tuple[Path, Path, Path]] = []
        self.concat_calls: list[tuple[list[Path], Path]] = []
        self.burn_calls: list[tuple[Path, list[Any], Path]] = []
        self.bgm_calls: list[tuple[Path, Path, Path]] = []

    async def attach_audio(
        self,
        video_path: Any,
        audio_path: Any,
        output_path: Any,
        *,
        timeout_sec: float | None = None,
    ) -> Path:
        v, a, o = Path(video_path), Path(audio_path), Path(output_path)
        self.attach_calls.append((v, a, o))
        o.parent.mkdir(parents=True, exist_ok=True)
        o.write_bytes(b"MUXED")
        return o

    async def concat(
        self,
        video_paths: list[Any],
        output_path: Any,
        *,
        transition: str = "none",
        fade_duration: float = 0.5,
        timeout_sec: float | None = None,
    ) -> Path:
        paths = [Path(p) for p in video_paths]
        out = Path(output_path)
        self.concat_calls.append((paths, out))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"CONCAT")
        return out

    async def burn_subtitles(
        self,
        video_path: Any,
        subtitles: list[Any],
        output_path: Any,
        **_: Any,
    ) -> Path:
        v, o = Path(video_path), Path(output_path)
        self.burn_calls.append((v, list(subtitles), o))
        o.parent.mkdir(parents=True, exist_ok=True)
        o.write_bytes(b"BURNED")
        return o

    async def mix_bgm(
        self,
        video_path: Any,
        bgm_path: Any,
        output_path: Any,
        **_: Any,
    ) -> Path:
        v, b, o = Path(video_path), Path(bgm_path), Path(output_path)
        self.bgm_calls.append((v, b, o))
        o.parent.mkdir(parents=True, exist_ok=True)
        o.write_bytes(b"BGM")
        return o


class _FakeWriter:
    def __init__(
        self,
        *,
        storyboard: dict[str, Any] | None = None,
        ok: bool = True,
        used_brain: bool = True,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._storyboard = storyboard or {
            "episode_title": "Test Episode",
            "summary": "summary",
            "panels": [
                {
                    "idx": 0,
                    "narration": "panel 0 narration",
                    "dialogue": [{"character": "李雷", "line": "hello"}],
                    "characters_in_scene": ["李雷"],
                    "camera": "中景",
                    "action": "走入",
                    "mood": "calm",
                    "background": "教室",
                    "image_url": "https://oss/p0.png",
                },
                {
                    "idx": 1,
                    "narration": "panel 1 narration",
                    "dialogue": [],
                    "characters_in_scene": ["李雷"],
                    "camera": "特写",
                    "action": "举手",
                    "mood": "focus",
                    "background": "黑板",
                    "image_url": "https://oss/p1.png",
                },
            ],
        }
        self._ok = ok
        self._used_brain = used_brain

    async def write_storyboard(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        from script_writer import BrainResult

        return BrainResult(ok=self._ok, data=self._storyboard, used_brain=self._used_brain)


class _FakeTaskManager:
    def __init__(self, *, characters: list[dict[str, Any]] | None = None) -> None:
        self._characters = {c["id"]: c for c in (characters or [])}
        self.episode_updates: list[tuple[str, dict[str, Any]]] = []
        self.task_updates: list[tuple[str, dict[str, Any]]] = []

    async def get_character(self, char_id: str) -> dict[str, Any] | None:
        return self._characters.get(char_id)

    async def update_episode_safe(self, ep_id: str, /, **updates: Any) -> bool:
        self.episode_updates.append((ep_id, updates))
        return True

    async def update_task_safe(self, task_id: str, /, **updates: Any) -> bool:
        self.task_updates.append((task_id, updates))
        return True


# ─── Fixture: assemble pipeline ───────────────────────────────────────────


@pytest.fixture
def _build_pipeline(tmp_path: Path, monkeypatch):
    """Returns a builder that produces a MangaPipeline with all the
    fake collaborators wired in. Caller can override individual
    collaborators."""

    def make(
        *,
        wanxiang: _FakeWanxiang | None = None,
        ark: _FakeArk | None = None,
        tts: _FakeTTS | None = None,
        ffmpeg: _FakeFFmpeg | None = None,
        writer: _FakeWriter | None = None,
        tm: _FakeTaskManager | None = None,
        comfy: Any | None = None,
    ) -> tuple[MangaPipeline, dict[str, Any]]:
        wx = wanxiang or _FakeWanxiang()
        ar = ark or _FakeArk()
        tt = tts or _FakeTTS()
        ff = ffmpeg or _FakeFFmpeg()
        wr = writer or _FakeWriter()
        tm_ = tm or _FakeTaskManager(
            characters=[
                {
                    "id": "c1",
                    "name": "李雷",
                    "default_voice_id": "zh-CN-YunjianNeural",
                    "ref_images_json": [],
                }
            ]
        )

        # The pipeline uses httpx.AsyncClient.stream() to download
        # vendor URLs. We monkeypatch it to write a stub bytes payload
        # to ``output_path`` so the rest of the pipeline runs.
        async def fake_download_to(self: Any, url: str, output_path: Path) -> None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"FAKE_BINARY_FOR_" + url.encode("utf-8")[-32:])

        monkeypatch.setattr(MangaPipeline, "_download_to", fake_download_to)

        pipe = MangaPipeline(
            wanxiang_client=wx,  # type: ignore[arg-type]
            ark_client=ar,  # type: ignore[arg-type]
            tts_client=tt,  # type: ignore[arg-type]
            ffmpeg=ff,  # type: ignore[arg-type]
            script_writer=wr,  # type: ignore[arg-type]
            task_manager=tm_,  # type: ignore[arg-type]
            working_dir=tmp_path / "manga",
            comfy_client=comfy,
        )
        return pipe, {
            "wx": wx,
            "ar": ar,
            "tt": tt,
            "ff": ff,
            "wr": wr,
            "tm": tm_,
            "comfy": comfy,
            "tmp": tmp_path,
        }

    return make


# ─── Happy path ───────────────────────────────────────────────────────────


async def test_run_episode_happy_path(_build_pipeline) -> None:
    pipe, fakes = _build_pipeline()
    config = MangaPipelineConfig(
        story="李雷上学的故事",
        n_panels=2,
        seconds_per_panel=3,
        bound_character_ids=["c1"],
        burn_subtitles=False,
    )
    events: list[dict[str, Any]] = []

    async def cb(
        *,
        step: str,
        progress: int,
        message: str = "",
        error: str | None = None,
    ) -> None:
        events.append({"step": step, "progress": progress, "message": message, "error": error})

    res = await pipe.run_episode(
        episode_id="ep_test",
        config=config,
        task_id="task_test",
        progress_cb=cb,
    )

    assert res.episode_id == "ep_test"
    assert len(res.panels) == 2
    assert res.final_video_path.name == "final.mp4"
    assert res.final_video_path.exists()
    assert res.errors == []  # no soft failures

    # Each step in 3-6 produced exactly one call per panel.
    assert len(fakes["wx"].submit_calls) == 2
    assert len(fakes["ar"].i2v_calls) == 2
    # Both panels have spoken text (panel 0 has dialogue, panel 1 has
    # narration only — both go to TTS via compose_tts_text).
    assert len(fakes["tt"].synth_calls) == 2
    assert len(fakes["ff"].attach_calls) == 2
    assert len(fakes["ff"].concat_calls) == 1

    # progress callback fired across the lifecycle
    assert any(e["step"] == "setup" for e in events)
    assert any(e["step"] == "post_processing" for e in events)

    # final episode row update has the canonical final path
    last_ep = fakes["tm"].episode_updates[-1]
    assert "final_video_path" in last_ep[1]
    assert last_ep[1]["final_video_path"].endswith("final.mp4")

    # task row has status=succeeded at the end
    last_task = fakes["tm"].task_updates[-1]
    assert last_task[1]["status"] == "succeeded"
    assert last_task[1]["progress"] == 100


# ─── Per-panel image fail (soft error) ───────────────────────────────────


async def test_panel_image_failure_recorded_but_pipeline_continues(_build_pipeline) -> None:
    """Image fails on panel 1 (NOT panel 0 — first-panel auth/quota
    aborts hard). The pipeline records a soft error and the panel falls
    through to T2V for video generation."""
    wx = _FakeWanxiang(submit_raises=[None, RuntimeError("image gen blew up")])
    pipe, fakes = _build_pipeline(wanxiang=wx)
    config = MangaPipelineConfig(
        story="x",
        n_panels=2,
        bound_character_ids=["c1"],
        burn_subtitles=False,
    )
    res = await pipe.run_episode(episode_id="ep_e1", config=config)

    # First panel succeeded image gen → I2V; second has no image → T2V.
    assert len(fakes["ar"].i2v_calls) == 1
    assert len(fakes["ar"].t2v_calls) == 1

    # Soft error was recorded on panel 1.
    assert any(p.error and p.error["step"] == "panel_image" for p in res.panels)

    # Final video still produced.
    assert res.final_video_path.exists()


# ─── First-panel hard auth/quota abort ───────────────────────────────────


async def test_first_panel_auth_failure_aborts_with_pipeline_error(_build_pipeline) -> None:
    """Auth failure on the FIRST panel must abort fast — we don't want
    to spend more quota retrying on a bad key."""
    wx = _FakeWanxiang(submit_raises=[VendorError("bad key", kind="auth", status=401)])
    pipe, fakes = _build_pipeline(wanxiang=wx)
    config = MangaPipelineConfig(
        story="x",
        n_panels=2,
        bound_character_ids=["c1"],
    )
    with pytest.raises(PipelineError) as exc:
        await pipe.run_episode(episode_id="ep_e2", config=config)
    assert exc.value.error_kind == "auth"


# ─── I2V → T2V auto-fallback ──────────────────────────────────────────────


async def test_face_moderation_falls_back_to_t2v(_build_pipeline) -> None:
    """``moderation_face`` rejection on I2V should re-route the same
    panel through T2V (no panel dropped)."""
    ar = _FakeArk(
        i2v_raises=[
            VendorError("face moderated", kind="moderation_face"),
            None,
        ]
    )
    pipe, fakes = _build_pipeline(ark=ar)
    config = MangaPipelineConfig(
        story="x",
        n_panels=2,
        bound_character_ids=["c1"],
        burn_subtitles=False,
    )
    res = await pipe.run_episode(episode_id="ep_e3", config=config)

    # Panel 0 went I2V (failed) → T2V (succeeded). Panel 1 went I2V.
    assert len(fakes["ar"].i2v_calls) == 2
    assert len(fakes["ar"].t2v_calls) == 1

    # No soft error recorded — the recovery path produced the video.
    assert res.errors == []


# ─── Subtitle generation skips silent panels ──────────────────────────────


def test_build_subtitles_skips_silent_panels() -> None:
    panels = [
        PanelArtifact(
            idx=0,
            storyboard_entry={"narration": "first"},
            duration_sec=2.0,
        ),
        PanelArtifact(
            idx=1,
            storyboard_entry={"dialogue": [], "narration": ""},
            duration_sec=1.0,
        ),
        PanelArtifact(
            idx=2,
            storyboard_entry={"dialogue": [{"character": "x", "line": "third"}]},
            duration_sec=2.0,
        ),
    ]
    subs = MangaPipeline._build_subtitles(panels)
    assert len(subs) == 2  # silent panel 1 skipped
    assert subs[0].text == "first"
    assert subs[0].start == 0.0
    assert subs[0].end == 2.0
    # Panel 2's offset = 2.0 + 1.0 (silent panel still consumes time)
    assert subs[1].text == "third"
    assert subs[1].start == 3.0
    assert subs[1].end == 5.0


# ─── Working dir layout ───────────────────────────────────────────────────


async def test_working_dir_layout_created(_build_pipeline, tmp_path: Path) -> None:
    pipe, fakes = _build_pipeline()
    config = MangaPipelineConfig(
        story="x",
        n_panels=1,
        bound_character_ids=["c1"],
        burn_subtitles=False,
    )
    await pipe.run_episode(episode_id="ep_layout", config=config)

    base = fakes["tmp"] / "manga" / "ep_layout"
    assert (base / "panels").is_dir()
    assert (base / "audio").is_dir()
    assert (base / "muxed").is_dir()
    assert (base / "final.mp4").exists()


# ─── Unknown visual style ─────────────────────────────────────────────────


async def test_unknown_visual_style_aborts(_build_pipeline) -> None:
    pipe, _ = _build_pipeline()
    config = MangaPipelineConfig(
        story="x",
        n_panels=1,
        visual_style="not-a-real-style",
        bound_character_ids=["c1"],
    )
    with pytest.raises(PipelineError) as exc:
        await pipe.run_episode(episode_id="ep_e4", config=config)
    assert exc.value.error_kind == "dependency"


# ─── Storyboard persisted early ───────────────────────────────────────────


async def test_storyboard_persisted_immediately(_build_pipeline) -> None:
    """Even if a later step fails, the storyboard / story / title are
    persisted to the episode row at the end of step 2."""
    ff = _FakeFFmpeg()

    async def fail_concat(*args, **kwargs):
        raise RuntimeError("ffmpeg crashed")

    ff.concat = fail_concat  # type: ignore[assignment]
    pipe, fakes = _build_pipeline(ffmpeg=ff)
    config = MangaPipelineConfig(
        story="李雷的故事",
        n_panels=1,
        bound_character_ids=["c1"],
        burn_subtitles=False,
    )
    with pytest.raises(PipelineError):
        await pipe.run_episode(episode_id="ep_partial", config=config)

    # The storyboard write happened BEFORE the concat failure.
    assert any("storyboard_json" in upd for _, upd in fakes["tm"].episode_updates)


# ─── No panels mux successfully ───────────────────────────────────────────


async def test_pipeline_aborts_when_no_panel_succeeds(_build_pipeline) -> None:
    """If every panel's video generation fails AND T2V fallback also
    fails, no panel has a muxed_path → concat input is empty → hard
    abort."""
    ar = _FakeArk(
        i2v_raises=[VendorError("boom", kind="server")] * 2,
        t2v_raises=[VendorError("also boom", kind="server")] * 2,
    )
    pipe, _ = _build_pipeline(ark=ar)
    config = MangaPipelineConfig(
        story="x",
        n_panels=2,
        bound_character_ids=["c1"],
        burn_subtitles=False,
    )
    with pytest.raises(PipelineError) as exc:
        await pipe.run_episode(episode_id="ep_e5", config=config)
    assert "no panels produced a muxed video" in str(exc.value)


# ─── PipelineError surface ────────────────────────────────────────────────


def test_pipeline_error_to_dict_includes_hint() -> None:
    err = PipelineError("boom", error_kind="auth")
    d = err.to_dict()
    assert d["error_kind"] == "auth"
    assert d["error_message"] == "boom"
    assert "鉴权失败" in d["hint_zh"]
    assert "Auth failed" in d["hint_en"]
    assert isinstance(d["hints_zh"], list)
    assert isinstance(d["hints_en"], list)


def test_pipeline_error_unknown_kind_falls_back_to_unknown_hint() -> None:
    err = PipelineError("x", error_kind="totally_made_up")
    d = err.to_dict()
    assert d["hint_zh"] == "未知错误"


# ─── Phase 3.3 — workflow backend dispatch ────────────────────────────────


class _FakeComfy:
    """Minimal stand-in for MangaComfyClient — captures every call so
    tests can verify the pipeline routed through the workflow backend
    (and not the direct vendor) when ``config.backend != "direct"``."""

    def __init__(
        self,
        *,
        image_returns: dict[str, Any] | None = None,
        i2v_returns: dict[str, Any] | None = None,
        t2v_returns: dict[str, Any] | None = None,
        image_raises: Exception | None = None,
        i2v_raises: Exception | None = None,
        t2v_raises: Exception | None = None,
    ) -> None:
        self.image_calls: list[dict[str, Any]] = []
        self.i2v_calls: list[dict[str, Any]] = []
        self.t2v_calls: list[dict[str, Any]] = []
        self._image_returns = image_returns or {
            "image_url": "https://wf/img.png",
            "raw": {},
        }
        self._i2v_returns = i2v_returns or {
            "video_url": "https://wf/clip.mp4",
            "raw": {},
        }
        self._t2v_returns = t2v_returns or {
            "video_url": "https://wf/t2v.mp4",
            "raw": {},
        }
        self._image_raises = image_raises
        self._i2v_raises = i2v_raises
        self._t2v_raises = t2v_raises

    async def generate_image(self, **kwargs: Any) -> dict[str, Any]:
        self.image_calls.append(kwargs)
        if self._image_raises is not None:
            raise self._image_raises
        return dict(self._image_returns)

    async def generate_i2v(self, **kwargs: Any) -> dict[str, Any]:
        self.i2v_calls.append(kwargs)
        if self._i2v_raises is not None:
            raise self._i2v_raises
        return dict(self._i2v_returns)

    async def generate_t2v(self, **kwargs: Any) -> dict[str, Any]:
        self.t2v_calls.append(kwargs)
        if self._t2v_raises is not None:
            raise self._t2v_raises
        return dict(self._t2v_returns)


async def test_run_episode_workflow_backend_routes_through_comfy(_build_pipeline) -> None:
    """When ``config.backend="runninghub"``, the pipeline must hit the
    comfy client for both image and i2v steps and skip the direct
    vendor calls entirely."""
    comfy = _FakeComfy()
    pipe, fakes = _build_pipeline(comfy=comfy)
    config = MangaPipelineConfig(
        story="李雷上学的故事",
        n_panels=2,
        seconds_per_panel=3,
        backend="runninghub",
        bound_character_ids=["c1"],
        burn_subtitles=False,
    )
    res = await pipe.run_episode(episode_id="ep_wf", config=config)
    assert res.errors == []
    assert len(res.panels) == 2

    assert len(comfy.image_calls) == 2
    assert len(comfy.i2v_calls) == 2
    assert len(fakes["wx"].submit_calls) == 0
    assert len(fakes["ar"].i2v_calls) == 0


async def test_run_episode_workflow_passes_ref_image_urls(_build_pipeline) -> None:
    """Character ref-images must be forwarded to the workflow's
    image-gen call so the IP-Adapter node can use them for character
    consistency."""
    comfy = _FakeComfy()
    tm = _FakeTaskManager(
        characters=[
            {
                "id": "c1",
                "name": "李雷",
                "default_voice_id": "zh-CN-YunjianNeural",
                "ref_images_json": [
                    "https://oss/hero_front.png",
                    "https://oss/hero_side.png",
                ],
            }
        ]
    )
    pipe, _ = _build_pipeline(comfy=comfy, tm=tm)
    config = MangaPipelineConfig(
        story="x",
        n_panels=2,
        backend="runninghub",
        bound_character_ids=["c1"],
        burn_subtitles=False,
    )
    await pipe.run_episode(episode_id="ep_ref", config=config)
    assert comfy.image_calls
    refs = comfy.image_calls[0].get("ref_image_urls") or []
    assert "https://oss/hero_front.png" in refs


async def test_run_episode_direct_backend_does_not_touch_comfy(_build_pipeline) -> None:
    """The default ``backend="direct"`` keeps everything on wanxiang +
    ark; the comfy client (when wired) must not be called.

    The fake writer always returns 2 panels regardless of ``n_panels``
    so we expect 2 wanxiang submits / 2 ark i2v calls — what matters is
    the comfy counts stay at zero.
    """
    comfy = _FakeComfy()
    pipe, fakes = _build_pipeline(comfy=comfy)
    config = MangaPipelineConfig(
        story="x",
        n_panels=2,
        bound_character_ids=["c1"],
        burn_subtitles=False,
    )
    await pipe.run_episode(episode_id="ep_direct", config=config)
    assert comfy.image_calls == []
    assert comfy.i2v_calls == []
    assert comfy.t2v_calls == []
    assert len(fakes["wx"].submit_calls) == 2
    assert len(fakes["ar"].i2v_calls) == 2


async def test_run_episode_workflow_backend_without_comfy_aborts_fast(
    _build_pipeline,
) -> None:
    """If the user picks ``backend="runninghub"`` but the comfy client
    wasn't provided (mid-init race / misconfig), the FIRST panel's
    image step raises ``PipelineError(kind=dependency)`` and the
    pipeline aborts immediately rather than chewing through every
    panel + falling back. This is the right behaviour because re-trying
    won't help — the user has to fix their config first.
    """
    pipe, _ = _build_pipeline(comfy=None)
    config = MangaPipelineConfig(
        story="x",
        n_panels=2,
        backend="runninghub",
        bound_character_ids=["c1"],
        burn_subtitles=False,
    )
    with pytest.raises(PipelineError) as exc:
        await pipe.run_episode(episode_id="ep_no_comfy", config=config)
    assert exc.value.error_kind == "dependency"
    assert "comfy client not configured" in str(exc.value)


async def test_run_episode_workflow_image_error_recorded_but_pipeline_continues(
    _build_pipeline,
) -> None:
    """A workflow-level error on one panel surfaces as a soft per-panel
    error with kind=workflow (mapped from ``WorkflowError``); the
    second panel still runs, so the overall episode finishes."""
    from comfy_client import ERROR_KIND_WORKFLOW, WorkflowError

    image_calls = {"n": 0}

    class _PartialFailComfy(_FakeComfy):
        async def generate_image(self, **kwargs: Any) -> dict[str, Any]:
            image_calls["n"] += 1
            if image_calls["n"] == 1:
                raise WorkflowError("node 47 crashed", kind=ERROR_KIND_WORKFLOW)
            return await super().generate_image(**kwargs)

    pipe, _ = _build_pipeline(comfy=_PartialFailComfy())
    config = MangaPipelineConfig(
        story="x",
        n_panels=2,
        backend="runninghub",
        bound_character_ids=["c1"],
        burn_subtitles=False,
    )
    res = await pipe.run_episode(episode_id="ep_partial_wf", config=config)
    assert len(res.panels) == 2
    failed = [p for p in res.panels if p.error is not None]
    assert len(failed) == 1
    assert failed[0].error and failed[0].error["kind"] == ERROR_KIND_WORKFLOW
    assert failed[0].error and "node 47" in failed[0].error["message"]
    # Second panel succeeded → final video produced.
    assert res.final_video_path.exists()


async def test_run_episode_workflow_t2v_fallback_on_face_moderation(
    _build_pipeline,
) -> None:
    """The face-moderation → t2v fallback path must use the workflow
    t2v call when ``backend != "direct"``, not Seedance t2v.

    The fake writer always emits 2 panels; both i2v calls get face-
    moderated, so both fall back to t2v.
    """
    from comfy_client import WorkflowError

    class _FaceModComfy(_FakeComfy):
        async def generate_i2v(self, **kwargs: Any) -> dict[str, Any]:
            self.i2v_calls.append(kwargs)
            raise WorkflowError("face moderation", kind="moderation_face")

    comfy = _FaceModComfy()
    pipe, fakes = _build_pipeline(comfy=comfy)
    config = MangaPipelineConfig(
        story="x",
        n_panels=2,
        backend="runninghub",
        bound_character_ids=["c1"],
        burn_subtitles=False,
    )
    res = await pipe.run_episode(episode_id="ep_face_wf", config=config)
    # Both i2v calls fail with moderation_face; both fall back to t2v.
    assert len(comfy.i2v_calls) == 2
    assert len(comfy.t2v_calls) == 2
    assert len(fakes["ar"].t2v_calls) == 0
    assert res.final_video_path.exists()

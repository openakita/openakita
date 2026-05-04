"""manga-studio pipeline — 8-step linear orchestrator.

The pipeline takes a story + character bindings + style / ratio / pacing
and produces ``{episode_id}/final.mp4`` on disk. Steps run sequentially
(simpler error handling), with Pixelle-style guardrails at each
boundary so a per-panel failure doesn't poison the whole episode.

Step map
--------
1. **setup**          — create the working directory, validate the
                        config, load the bound characters from DB.
2. **script**         — call ``MangaScriptWriter`` to expand the story
                        into a structured ``storyboard.json``. Always
                        succeeds (deterministic fallback when brain is
                        unavailable).
3. **panel_image**    — for each panel, call ``MangaWanxiangClient`` to
                        generate a 1024×1024 manga panel using the
                        bound characters' reference images. Per-panel
                        fail → record in ``errors`` and skip the panel.
4. **panel_video**    — for each panel with an image, call Seedance
                        I2V; fall back to T2V on ``moderation_face``
                        rejections (Pixelle anti-pattern: never silently
                        drop a panel).
5. **panel_tts**      — for each panel, synthesise the dialogue +
                        narration via ``MangaTTSClient``.
6. **panel_mux**      — attach the audio to the silent panel video
                        (``ffmpeg attach_audio``).
7. **concat**         — join the muxed panels into one episode video.
8. **post_processing** — burn subtitles + (optionally) mix BGM.

Pipeline always closes by writing ``final_video_path`` /
``duration_sec`` into the ``episodes`` row so the UI can pick the
result up via ``GET /episodes/{ep_id}``.

Failure semantics
-----------------
- Hard failures (vendor auth / quota / dependency missing / bad
  config) raise :class:`PipelineError` and the orchestrator surfaces
  ``error_kind`` / ``error_message`` / bilingual hints into the
  ``tasks`` row.
- Soft failures (one panel out of N fails image gen, or TTS) are
  appended to ``MangaPipelineResult.errors`` and the pipeline
  continues — the user gets an episode with a fallback panel image
  (a black frame with the panel's narration burned in).

Progress callback
-----------------
``progress_cb(step, progress, message, error)`` is called at every
step boundary (and once per panel inside steps 3-6). The ``progress``
field is 0-100. The orchestrator separately writes the same data into
the ``tasks`` row so an external client can poll ``GET /tasks/{id}``
for live updates.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from manga_inline.vendor_client import VendorError
from manga_models import VISUAL_STYLES_BY_ID, hint_for
from prompt_assembler import (
    compose_i2v_prompt,
    compose_image_prompt,
    compose_t2v_prompt,
    compose_tts_text,
)

if TYPE_CHECKING:  # pragma: no cover - import only used for typing
    from direct_ark_client import MangaArkClient
    from direct_wanxiang_client import MangaWanxiangClient
    from ffmpeg_service import FFmpegService
    from manga_task_manager import MangaTaskManager
    from script_writer import MangaScriptWriter
    from tts_client import MangaTTSClient

logger = logging.getLogger(__name__)


# ─── Public types ─────────────────────────────────────────────────────────


class PipelineError(Exception):
    """Hard failure that aborts the whole episode.

    Carries an ``error_kind`` aligned with ``manga_models.ERROR_HINTS``
    so the UI can render a localised hint without a second lookup.
    """

    def __init__(
        self,
        message: str,
        *,
        error_kind: str = "unknown",
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        hint = hint_for(self.error_kind)
        return {
            "error_kind": self.error_kind,
            "error_message": str(self),
            "hint_zh": hint["title_zh"],
            "hint_en": hint["title_en"],
            "hints_zh": hint["hints_zh"],
            "hints_en": hint["hints_en"],
        }


@dataclass(slots=True)
class MangaPipelineConfig:
    """Episode-level config (one ``run_episode`` call).

    Phase 3.3 added ``backend`` to drive whether image/video generation
    goes through the direct vendor APIs (DashScope wan2.7-image +
    Seedance) or the comfy client (RunningHub / local ComfyUI). Valid
    values: ``direct`` | ``runninghub`` | ``comfyui_local``. The
    pipeline picks the matching client per step; everything else
    (script writing, TTS, FFmpeg) stays unchanged.
    """

    story: str
    n_panels: int
    seconds_per_panel: int = 5
    visual_style: str = "shonen"
    ratio: str = "9:16"
    resolution: str = "480P"
    backend: str = "direct"
    bound_character_ids: list[str] = field(default_factory=list)
    fallback_voice: str = "zh-CN-XiaoxiaoNeural"
    image_model: str = "wan2.7-image"
    video_model: str = "seedance-1.0-lite-i2v"
    burn_subtitles: bool = True
    bgm_path: str | None = None
    title_hint: str = ""
    series_id: str | None = None


@dataclass(slots=True)
class PanelArtifact:
    """One panel's outputs through the pipeline."""

    idx: int
    storyboard_entry: dict[str, Any]
    image_path: Path | None = None
    video_path: Path | None = None
    audio_path: Path | None = None
    muxed_path: Path | None = None
    duration_sec: float = 0.0
    error: dict[str, Any] | None = None


@dataclass(slots=True)
class MangaPipelineResult:
    """Aggregate result handed back to the caller of ``run_episode``."""

    episode_id: str
    storyboard: dict[str, Any]
    panels: list[PanelArtifact]
    final_video_path: Path
    duration_sec: float
    errors: list[dict[str, Any]] = field(default_factory=list)
    used_brain: bool = False


class ProgressCallback(Protocol):
    """Async progress reporter — see ``MangaPipeline.run_episode``."""

    async def __call__(
        self,
        *,
        step: str,
        progress: int,
        message: str = "",
        error: str | None = None,
    ) -> None: ...


# ─── Step weights (sum to 100) ────────────────────────────────────────────
#
# Empirically the heavy steps are panel_video (Seedance I2V is 30-180 s
# per panel) and concat (≤ 5 s). We allocate the progress band per step
# so the UI's progress bar advances at a believable rate.
STEP_WEIGHTS: dict[str, tuple[int, int]] = {
    "setup": (0, 5),
    "script": (5, 15),
    "panel_image": (15, 35),
    "panel_video": (35, 75),
    "panel_tts": (75, 85),
    "panel_mux": (85, 90),
    "concat": (90, 95),
    "post_processing": (95, 100),
}


def _scaled_progress(step: str, panel_idx: int, n_panels: int) -> int:
    """Linearly interpolate within the step's progress band."""
    lo, hi = STEP_WEIGHTS.get(step, (0, 100))
    if n_panels <= 0:
        return lo
    span = hi - lo
    return min(hi, lo + int(span * (panel_idx + 1) / n_panels))


# ─── Pipeline ─────────────────────────────────────────────────────────────


class MangaPipeline:
    """The orchestrator. Stateless — every ``run_episode`` is independent.

    Args:
        wanxiang_client: DashScope wan2.7-image client (panel images).
        ark_client: Volcengine Seedance client (image-to-video).
        tts_client: Edge-TTS / CosyVoice unified facade.
        ffmpeg: FFmpeg service for mux / concat / subtitles / BGM.
        script_writer: LLM-driven storyboard writer.
        task_manager: For loading characters and persisting episode
            results. ``run_episode`` updates the episode row and the
            optional ``task_id`` if provided.
        working_dir: Parent dir under which ``{episode_id}/`` lands.
    """

    def __init__(
        self,
        *,
        wanxiang_client: MangaWanxiangClient,
        ark_client: MangaArkClient,
        tts_client: MangaTTSClient,
        ffmpeg: FFmpegService,
        script_writer: MangaScriptWriter,
        task_manager: MangaTaskManager,
        working_dir: Path,
        comfy_client: Any | None = None,
    ) -> None:
        self._wanxiang = wanxiang_client
        self._ark = ark_client
        self._tts = tts_client
        self._ffmpeg = ffmpeg
        self._writer = script_writer
        self._tm = task_manager
        self._root = Path(working_dir)
        # Phase 3.3 — optional comfy client. Only consulted when
        # ``config.backend`` is one of ``runninghub`` / ``comfyui_local``.
        # Direct-only environments (no comfykit installed) leave this
        # None and ``backend="direct"`` keeps everything working.
        self._comfy = comfy_client

    # ─── Top-level orchestration ─────────────────────────────────

    async def run_episode(
        self,
        *,
        episode_id: str,
        config: MangaPipelineConfig,
        task_id: str | None = None,
        progress_cb: ProgressCallback | None = None,
    ) -> MangaPipelineResult:
        """Execute the full 8-step pipeline.

        Args:
            episode_id: Pre-created row in the ``episodes`` table.
            config: All non-DB inputs (story, style, n_panels, …).
            task_id: Optional row in ``tasks`` to update with progress.
            progress_cb: Optional async callback invoked at each step.

        Returns:
            ``MangaPipelineResult`` with the final video path, panel
            artifacts, and any per-panel soft errors.

        Raises:
            PipelineError on hard failure. The caller is expected to
            mark the task row ``failed`` with the error kind.
        """
        ep_dir = self._ensure_working_dir(episode_id)
        await self._notify(progress_cb, "setup", 0, "starting pipeline")
        await self._set_task(task_id, current_step="setup", status="running", progress=0)

        # ── 1. setup
        characters = await self._load_characters(config.bound_character_ids)
        style = VISUAL_STYLES_BY_ID.get(config.visual_style)
        if style is None:
            raise PipelineError(
                f"unknown visual_style {config.visual_style!r}",
                error_kind="dependency",
            )
        await self._notify(progress_cb, "setup", 5, f"loaded {len(characters)} character(s)")

        # ── 2. script
        await self._set_task(task_id, current_step="script", progress=5)
        await self._notify(progress_cb, "script", 5, "calling brain for storyboard")
        sb_result = await self._writer.write_storyboard(
            story=config.story,
            n_panels=config.n_panels,
            seconds_per_panel=config.seconds_per_panel,
            characters=characters,
            visual_style_label=style.label_zh,
        )
        storyboard = sb_result.data
        used_brain = sb_result.used_brain
        await self._notify(
            progress_cb,
            "script",
            15,
            f"storyboard ready: {len(storyboard.get('panels', []))} panels"
            + ("" if sb_result.ok else " (fallback)"),
        )

        # Persist the storyboard immediately so a crash later still
        # leaves a partially-recoverable episode row.
        try:
            await self._tm.update_episode_safe(
                episode_id,
                title=storyboard.get("episode_title") or config.title_hint or "",
                story=config.story,
                storyboard_json=storyboard,
                bound_characters_json=config.bound_character_ids,
            )
        except Exception as exc:  # noqa: BLE001 - log only, episode persistence is non-fatal
            logger.warning("manga-studio: failed to persist storyboard early: %s", exc)

        # ── 3-6. per-panel
        panels = await self._panel_loop(
            ep_dir=ep_dir,
            episode_id=episode_id,
            storyboard=storyboard,
            characters=characters,
            config=config,
            style_id=style.id,
            task_id=task_id,
            progress_cb=progress_cb,
        )

        # ── 7. concat
        await self._set_task(task_id, current_step="concat", progress=90)
        await self._notify(progress_cb, "concat", 90, "joining panels")
        muxed_paths = [p.muxed_path for p in panels if p.muxed_path is not None]
        if not muxed_paths:
            raise PipelineError(
                "no panels produced a muxed video — episode is unrecoverable",
                error_kind="server",
            )
        concat_path = ep_dir / "concat.mp4"
        try:
            await self._ffmpeg.concat(muxed_paths, concat_path, transition="none")
        except Exception as exc:  # noqa: BLE001
            raise PipelineError(
                f"ffmpeg concat failed: {exc}",
                error_kind="dependency",
                cause=exc,
            ) from exc
        await self._notify(progress_cb, "concat", 95, f"joined {len(muxed_paths)} panels")

        # ── 8. post_processing
        await self._set_task(task_id, current_step="post_processing", progress=95)
        final_path = await self._post_process(
            ep_dir=ep_dir,
            concat_path=concat_path,
            panels=panels,
            config=config,
            progress_cb=progress_cb,
        )

        # ── persist final
        total_duration = sum(p.duration_sec for p in panels)
        await self._tm.update_episode_safe(
            episode_id,
            final_video_path=str(final_path),
            duration_sec=total_duration,
        )
        await self._set_task(
            task_id,
            current_step="done",
            progress=100,
            status="succeeded",
            completed_at=time.time(),
        )
        await self._notify(progress_cb, "post_processing", 100, "episode ready")

        errors = [p.error for p in panels if p.error]
        return MangaPipelineResult(
            episode_id=episode_id,
            storyboard=storyboard,
            panels=panels,
            final_video_path=final_path,
            duration_sec=total_duration,
            errors=errors,
            used_brain=used_brain,
        )

    # ─── Helpers: working dir, characters, notify ───────────────

    def _ensure_working_dir(self, episode_id: str) -> Path:
        ep_dir = self._root / episode_id
        (ep_dir / "panels").mkdir(parents=True, exist_ok=True)
        (ep_dir / "audio").mkdir(parents=True, exist_ok=True)
        (ep_dir / "muxed").mkdir(parents=True, exist_ok=True)
        return ep_dir

    async def _load_characters(self, ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for cid in ids:
            row = await self._tm.get_character(cid)
            if row is None:
                logger.warning("manga-studio: bound character %s not found", cid)
                continue
            out.append(row)
        return out

    async def _notify(
        self,
        cb: ProgressCallback | None,
        step: str,
        progress: int,
        message: str = "",
        error: str | None = None,
    ) -> None:
        if cb is None:
            return
        try:
            await cb(step=step, progress=progress, message=message, error=error)
        except Exception as exc:  # noqa: BLE001 - progress is informational
            logger.warning("manga-studio: progress callback raised: %s", exc)

    async def _set_task(self, task_id: str | None, **updates: Any) -> None:
        if not task_id:
            return
        try:
            await self._tm.update_task_safe(task_id, **updates)
        except Exception as exc:  # noqa: BLE001
            logger.warning("manga-studio: task update failed: %s", exc)

    # ─── Per-panel loop (steps 3-6) ─────────────────────────────

    async def _panel_loop(
        self,
        *,
        ep_dir: Path,
        episode_id: str,
        storyboard: dict[str, Any],
        characters: list[dict[str, Any]],
        config: MangaPipelineConfig,
        style_id: str,
        task_id: str | None,
        progress_cb: ProgressCallback | None,
    ) -> list[PanelArtifact]:
        style = VISUAL_STYLES_BY_ID[style_id]
        sb_panels: list[dict[str, Any]] = list(storyboard.get("panels", []))
        n = len(sb_panels)
        panels: list[PanelArtifact] = []

        # ── Step 3: per-panel image
        await self._set_task(task_id, current_step="panel_image", progress=15)
        for idx, sb_panel in enumerate(sb_panels):
            panel = PanelArtifact(idx=idx, storyboard_entry=sb_panel)
            panels.append(panel)
            try:
                img_prompt = compose_image_prompt(
                    panel=sb_panel,
                    characters=characters,
                    style=style,
                    ratio=config.ratio,
                    panel_index=idx,
                )
                img_path = ep_dir / "panels" / f"panel_{idx:03d}.png"
                await self._gen_panel_image(
                    prompt=img_prompt.prompt,
                    negative_prompt=img_prompt.negative_prompt,
                    ref_urls=img_prompt.reference_image_urls,
                    ratio=config.ratio,
                    output_path=img_path,
                    backend=config.backend,
                )
                panel.image_path = img_path
            except VendorError as exc:
                # Hard vendor failure on the FIRST panel → abort. We
                # don't want to spend more quota retrying every panel
                # if the API key is wrong.
                if idx == 0 and exc.kind in ("auth", "quota"):
                    raise PipelineError(
                        f"image generation aborted on first panel: {exc}",
                        error_kind=exc.kind,
                        cause=exc,
                    ) from exc
                panel.error = {"step": "panel_image", "kind": exc.kind, "message": str(exc)}
                logger.warning("manga-studio panel %d image failed: %s", idx, exc)
            except PipelineError as exc:
                # Workflow-backend failures (and pipeline misconfig) come
                # through as PipelineError. Mirror the VendorError branch:
                # abort on the first panel for hard auth / config /
                # dependency errors, soft-fail on everything else.
                if idx == 0 and exc.error_kind in (
                    "auth",
                    "quota",
                    "config",
                    "dependency",
                ):
                    raise
                panel.error = {
                    "step": "panel_image",
                    "kind": exc.error_kind,
                    "message": str(exc),
                }
                logger.warning("manga-studio panel %d image failed (workflow): %s", idx, exc)
            except Exception as exc:  # noqa: BLE001
                panel.error = {"step": "panel_image", "kind": "unknown", "message": str(exc)}
                logger.warning("manga-studio panel %d image error: %s", idx, exc)
            await self._notify(
                progress_cb,
                "panel_image",
                _scaled_progress("panel_image", idx, n),
                f"panel {idx + 1}/{n} image",
            )

        # ── Step 4: per-panel video (with I2V → T2V fallback)
        await self._set_task(task_id, current_step="panel_video", progress=35)
        for idx, panel in enumerate(panels):
            sb_panel = panel.storyboard_entry
            try:
                if panel.image_path:
                    panel.video_path = await self._gen_panel_video_i2v(
                        ep_dir=ep_dir,
                        idx=idx,
                        panel=sb_panel,
                        style=style,
                        image_path=panel.image_path,
                        config=config,
                    )
                else:
                    # Image step failed → go straight to T2V.
                    panel.video_path = await self._gen_panel_video_t2v(
                        ep_dir=ep_dir,
                        idx=idx,
                        panel=sb_panel,
                        style=style,
                        characters=characters,
                        config=config,
                    )
            except (VendorError, PipelineError) as exc:
                # Both direct vendor (VendorError) and workflow backend
                # (PipelineError mapped from WorkflowError) report face
                # moderation through ``kind="moderation_face"``. We
                # honour that signal regardless of the source so the
                # T2V fallback works for both backends.
                kind = exc.kind if isinstance(exc, VendorError) else exc.error_kind
                if kind == "moderation_face" and panel.image_path:
                    # Pixelle anti-pattern recovery: face moderation
                    # rejection → drop the image and retry T2V.
                    logger.warning("manga-studio panel %d face-moderated; falling back to T2V", idx)
                    try:
                        panel.video_path = await self._gen_panel_video_t2v(
                            ep_dir=ep_dir,
                            idx=idx,
                            panel=sb_panel,
                            style=style,
                            characters=characters,
                            config=config,
                        )
                    except Exception as exc2:  # noqa: BLE001
                        fallback_kind = (
                            exc2.error_kind
                            if isinstance(exc2, PipelineError)
                            else getattr(exc2, "kind", "unknown")
                        )
                        panel.error = {
                            "step": "panel_video",
                            "kind": fallback_kind,
                            "message": str(exc2),
                        }
                else:
                    panel.error = {"step": "panel_video", "kind": kind, "message": str(exc)}
                    logger.warning("manga-studio panel %d video failed: %s", idx, exc)
            except Exception as exc:  # noqa: BLE001
                panel.error = {"step": "panel_video", "kind": "unknown", "message": str(exc)}
                logger.warning("manga-studio panel %d video error: %s", idx, exc)
            await self._notify(
                progress_cb,
                "panel_video",
                _scaled_progress("panel_video", idx, n),
                f"panel {idx + 1}/{n} animated",
            )

        # ── Step 5: per-panel TTS
        await self._set_task(task_id, current_step="panel_tts", progress=75)
        for idx, panel in enumerate(panels):
            text, voice = compose_tts_text(
                panel=panel.storyboard_entry,
                characters=characters,
                fallback_voice=config.fallback_voice,
            )
            if not text:
                continue
            audio_path = ep_dir / "audio" / f"panel_{idx:03d}.mp3"
            try:
                tts_result = await self._tts.synth(
                    text=text, voice_id=voice, output_path=audio_path
                )
                panel.audio_path = audio_path
                # Use TTS-reported duration as a hint; the pipeline's
                # actual panel length is governed by the video clip.
                panel.duration_sec = max(
                    panel.duration_sec, float(tts_result.get("duration_sec", 0.0))
                )
            except Exception as exc:  # noqa: BLE001 - TTS is non-fatal
                # Soft error: pipeline can still produce a silent
                # episode — don't poison the run.
                if panel.error is None:
                    panel.error = {
                        "step": "panel_tts",
                        "kind": getattr(exc, "kind", "unknown"),
                        "message": str(exc),
                    }
                logger.warning("manga-studio panel %d TTS failed: %s", idx, exc)
            await self._notify(
                progress_cb,
                "panel_tts",
                _scaled_progress("panel_tts", idx, n),
                f"panel {idx + 1}/{n} voiced",
            )

        # ── Step 6: per-panel audio mux
        await self._set_task(task_id, current_step="panel_mux", progress=85)
        for idx, panel in enumerate(panels):
            if panel.video_path is None:
                continue
            target = ep_dir / "muxed" / f"panel_{idx:03d}.mp4"
            if panel.audio_path is None:
                # No audio → just copy the silent video into ``muxed``
                # so the concat step finds it.
                shutil.copy2(panel.video_path, target)
                panel.muxed_path = target
            else:
                try:
                    await self._ffmpeg.attach_audio(panel.video_path, panel.audio_path, target)
                    panel.muxed_path = target
                except Exception as exc:  # noqa: BLE001
                    panel.error = {
                        "step": "panel_mux",
                        "kind": "dependency",
                        "message": str(exc),
                    }
                    logger.warning("manga-studio panel %d mux failed: %s", idx, exc)
            # Refine ``duration_sec`` from the muxed file for the
            # final episode-duration aggregate.
            if panel.muxed_path and panel.duration_sec == 0:
                # Fall back to seconds_per_panel as the canonical
                # duration when neither TTS nor probe gave us one.
                panel.duration_sec = float(config.seconds_per_panel)
            await self._notify(
                progress_cb,
                "panel_mux",
                _scaled_progress("panel_mux", idx, n),
                f"panel {idx + 1}/{n} muxed",
            )

        return panels

    # ─── Image generation ────────────────────────────────────────

    async def _gen_panel_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        ref_urls: list[str],
        ratio: str,
        output_path: Path,
        backend: str = "direct",
    ) -> None:
        """Generate one panel image, downloading to ``output_path``.

        Branches on ``backend``:

        - ``direct`` (default): DashScope wan2.7-image submit + poll.
        - ``runninghub`` / ``comfyui_local``: ``MangaComfyClient.generate_image``
          which runs the user's image workflow synchronously.

        In either case the call returns a public URL and we stream it
        into ``output_path``.
        """
        size = _ratio_to_size(ratio)
        if backend in ("runninghub", "comfyui_local"):
            url = await self._gen_panel_image_via_workflow(
                prompt=prompt,
                negative_prompt=negative_prompt,
                ref_urls=ref_urls,
                size=size,
            )
        else:
            url = await self._gen_panel_image_via_direct(
                prompt=prompt,
                negative_prompt=negative_prompt,
                ref_urls=ref_urls,
                size=size,
            )
        await self._download_to(url, output_path)

    async def _gen_panel_image_via_direct(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        ref_urls: list[str],
        size: str,
    ) -> str:
        """Direct DashScope wan2.7-image: submit + poll + extract URL."""
        task_id = await self._wanxiang.submit_image(
            prompt=prompt,
            ref_images_url=ref_urls or None,
            negative_prompt=negative_prompt or "",
            size=size,
        )
        result = await self._wanxiang.poll_until_done(task_id, timeout_sec=180)
        if not result.get("is_ok"):
            raise PipelineError(
                f"wan2.7-image task {task_id} ended in {result.get('status')}",
                error_kind=result.get("error_kind") or "server",
            )
        url = result.get("output_url")
        if not isinstance(url, str) or not url:
            raise PipelineError(
                "wan2.7-image returned no output url",
                error_kind="server",
            )
        return url

    async def _gen_panel_image_via_workflow(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        ref_urls: list[str],
        size: str,
    ) -> str:
        """Workflow backend (RunningHub / local ComfyUI) image gen.

        The comfy client raises ``WorkflowError`` on every failure mode;
        we map that to ``PipelineError`` with the same ``error_kind``
        so the rest of the pipeline (panel-soft-fail handling, task
        row updates) doesn't need to special-case workflow errors.
        """
        if self._comfy is None:
            raise PipelineError(
                "workflow backend requested but comfy client not configured",
                error_kind="dependency",
            )
        # Late-import keeps the module loadable when comfykit is absent
        # — only callers that actually pick a workflow backend get to
        # this branch in the first place.
        from comfy_client import WorkflowError  # type: ignore[import-untyped]

        try:
            result = await self._comfy.generate_image(
                prompt=prompt,
                ref_image_urls=ref_urls or [],
                negative_prompt=negative_prompt or "",
                size=size,
            )
        except WorkflowError as exc:
            raise PipelineError(
                f"workflow image gen failed: {exc.message}",
                error_kind=exc.kind,
            ) from exc
        return str(result.get("image_url") or "")

    async def _download_to(self, url: str, output_path: Path) -> None:
        """Stream-download ``url`` into ``output_path``.

        We piggy-back on ``vendor_client``'s shared client through a
        plain GET. ``BaseVendorClient`` already handles retry / timeout
        / error classification, but its public surface is JSON-only;
        for a binary asset we need a raw HTTP GET, so we use httpx
        directly (Pixelle dependency). The inline helpers all already
        depend on httpx so we don't add a new dep.
        """
        import httpx

        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with (
            httpx.AsyncClient(timeout=60) as client,
            client.stream("GET", url) as resp,
        ):
            resp.raise_for_status()
            with output_path.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)

    # ─── Video generation (I2V / T2V) ───────────────────────────

    async def _gen_panel_video_i2v(
        self,
        *,
        ep_dir: Path,
        idx: int,
        panel: dict[str, Any],
        style: Any,
        image_path: Path,
        config: MangaPipelineConfig,
    ) -> Path:
        """Image-to-video for one panel — direct or workflow backend.

        Both branches need a public image URL (not a local path) because
        the remote service must fetch the source image. The caller is
        expected to upload to OSS before calling I2V (see
        ``panel['image_url']``).
        """
        prompt = compose_i2v_prompt(panel=panel, style=style, duration_sec=config.seconds_per_panel)
        image_url = panel.get("image_url")
        if not image_url:
            raise PipelineError(
                f"panel {idx}: image_url missing — upload to OSS before I2V",
                error_kind="dependency",
            )
        out = ep_dir / "panels" / f"panel_{idx:03d}.mp4"
        if config.backend in ("runninghub", "comfyui_local"):
            video_url = await self._gen_panel_video_i2v_via_workflow(
                image_url=image_url,
                prompt=prompt.prompt,
                duration_sec=config.seconds_per_panel,
                ratio=config.ratio,
            )
        else:
            video_url = await self._gen_panel_video_i2v_via_direct(
                image_url=image_url,
                prompt=prompt.prompt,
                duration_sec=config.seconds_per_panel,
                ratio=config.ratio,
                resolution=config.resolution,
            )
        await self._download_to(video_url, out)
        return out

    async def _gen_panel_video_i2v_via_direct(
        self,
        *,
        image_url: str,
        prompt: str,
        duration_sec: int,
        ratio: str,
        resolution: str,
    ) -> str:
        sub = await self._ark.submit_seedance_i2v(
            image_url=image_url,
            prompt=prompt,
            duration=duration_sec,
            ratio=ratio,
            resolution=resolution,
        )
        task_id = str(sub.get("id") or sub.get("task_id") or "")
        if not task_id:
            raise PipelineError(
                "seedance create_task returned no id",
                error_kind="server",
            )
        result = await self._ark.poll_until_done(task_id, timeout_sec=300)
        if str(result.get("status", "")).lower() not in {"succeeded", "completed"}:
            raise PipelineError(
                f"seedance task {task_id} ended in {result.get('status')}",
                error_kind="server",
            )
        video_url = self._ark.extract_video_url(result)
        if not video_url:
            raise PipelineError(
                "seedance returned no video url",
                error_kind="server",
            )
        return video_url

    async def _gen_panel_video_i2v_via_workflow(
        self,
        *,
        image_url: str,
        prompt: str,
        duration_sec: int,
        ratio: str,
    ) -> str:
        if self._comfy is None:
            raise PipelineError(
                "workflow backend requested but comfy client not configured",
                error_kind="dependency",
            )
        from comfy_client import WorkflowError  # type: ignore[import-untyped]

        try:
            result = await self._comfy.generate_i2v(
                image_url=image_url,
                prompt=prompt,
                duration_sec=duration_sec,
                ratio=ratio,
            )
        except WorkflowError as exc:
            raise PipelineError(
                f"workflow i2v failed: {exc.message}",
                error_kind=exc.kind,
            ) from exc
        return str(result.get("video_url") or "")

    async def _gen_panel_video_t2v(
        self,
        *,
        ep_dir: Path,
        idx: int,
        panel: dict[str, Any],
        style: Any,
        characters: list[dict[str, Any]],
        config: MangaPipelineConfig,
    ) -> Path:
        """Text-to-video fallback (when face moderation rejected I2V).

        Same dual-backend dispatch as I2V; the prompt is composed by
        ``compose_t2v_prompt`` which embeds character descriptors so
        the model has more to work with than the bare scene text.
        """
        prompt = compose_t2v_prompt(
            panel=panel,
            characters=characters,
            style=style,
            duration_sec=config.seconds_per_panel,
        )
        out = ep_dir / "panels" / f"panel_{idx:03d}.mp4"
        if config.backend in ("runninghub", "comfyui_local"):
            video_url = await self._gen_panel_video_t2v_via_workflow(
                prompt=prompt.prompt,
                duration_sec=config.seconds_per_panel,
                ratio=config.ratio,
            )
        else:
            video_url = await self._gen_panel_video_t2v_via_direct(
                prompt=prompt.prompt,
                duration_sec=config.seconds_per_panel,
                ratio=config.ratio,
                resolution=config.resolution,
            )
        await self._download_to(video_url, out)
        return out

    async def _gen_panel_video_t2v_via_direct(
        self,
        *,
        prompt: str,
        duration_sec: int,
        ratio: str,
        resolution: str,
    ) -> str:
        sub = await self._ark.submit_seedance_t2v(
            prompt=prompt,
            duration=duration_sec,
            ratio=ratio,
            resolution=resolution,
        )
        task_id = str(sub.get("id") or sub.get("task_id") or "")
        if not task_id:
            raise PipelineError(
                "seedance create_task returned no id",
                error_kind="server",
            )
        result = await self._ark.poll_until_done(task_id, timeout_sec=300)
        if str(result.get("status", "")).lower() not in {"succeeded", "completed"}:
            raise PipelineError(
                f"seedance task {task_id} ended in {result.get('status')}",
                error_kind="server",
            )
        video_url = self._ark.extract_video_url(result)
        if not video_url:
            raise PipelineError(
                "seedance returned no video url",
                error_kind="server",
            )
        return video_url

    async def _gen_panel_video_t2v_via_workflow(
        self,
        *,
        prompt: str,
        duration_sec: int,
        ratio: str,
    ) -> str:
        if self._comfy is None:
            raise PipelineError(
                "workflow backend requested but comfy client not configured",
                error_kind="dependency",
            )
        from comfy_client import WorkflowError  # type: ignore[import-untyped]

        try:
            result = await self._comfy.generate_t2v(
                prompt=prompt,
                duration_sec=duration_sec,
                ratio=ratio,
            )
        except WorkflowError as exc:
            raise PipelineError(
                f"workflow t2v failed: {exc.message}",
                error_kind=exc.kind,
            ) from exc
        return str(result.get("video_url") or "")

    # ─── Post-processing ────────────────────────────────────────

    async def _post_process(
        self,
        *,
        ep_dir: Path,
        concat_path: Path,
        panels: list[PanelArtifact],
        config: MangaPipelineConfig,
        progress_cb: ProgressCallback | None,
    ) -> Path:
        """Burn subtitles + (optionally) mix BGM, return final.mp4 path."""
        current = concat_path
        # 1. burn subtitles
        if config.burn_subtitles:
            subs = self._build_subtitles(panels)
            if subs:
                burned = ep_dir / "with_subs.mp4"
                try:
                    await self._ffmpeg.burn_subtitles(current, subs, burned)
                    current = burned
                    await self._notify(progress_cb, "post_processing", 97, "subtitles burned")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("manga-studio subtitle burn failed: %s", exc)

        # 2. mix BGM
        if config.bgm_path:
            bgm_path = Path(config.bgm_path)
            if bgm_path.exists():
                final = ep_dir / "final.mp4"
                try:
                    await self._ffmpeg.mix_bgm(current, bgm_path, final)
                    current = final
                    await self._notify(progress_cb, "post_processing", 99, "BGM mixed")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("manga-studio bgm mix failed: %s", exc)
            else:
                logger.warning("manga-studio: bgm_path %s not found", bgm_path)

        # 3. canonical final.mp4 — always exists at this path so the
        # caller / UI can fetch it without conditional logic.
        final_path = ep_dir / "final.mp4"
        if current != final_path:
            shutil.copy2(current, final_path)
        return final_path

    @staticmethod
    def _build_subtitles(panels: list[PanelArtifact]) -> list[Any]:
        """Compose SRT cues from each panel's storyboard text.

        The cue's start = cumulative offset, end = start + panel.duration.
        Empty-text panels are skipped — no flicker for silent shots.
        """
        from ffmpeg_service import SubtitleLine

        subs: list[SubtitleLine] = []
        offset = 0.0
        for p in panels:
            duration = max(0.5, p.duration_sec)
            sb = p.storyboard_entry
            text = ""
            dialogue = sb.get("dialogue") or []
            if isinstance(dialogue, list) and dialogue:
                first = dialogue[0]
                if isinstance(first, dict):
                    text = str(first.get("line") or first.get("text") or "")
            if not text:
                text = str(sb.get("narration") or "")
            text = text.strip()
            if text:
                subs.append(SubtitleLine(start=offset, end=offset + duration, text=text))
            offset += duration
        return subs


# ─── Pure helpers ─────────────────────────────────────────────────────────


def _ratio_to_size(ratio: str) -> str:
    """Map manga aspect ratio to wan2.7-image ``size`` strings."""
    return {
        "9:16": "720*1280",
        "16:9": "1280*720",
        "1:1": "1024*1024",
        "4:5": "1024*1280",
    }.get(ratio, "1024*1024")


__all__ = [
    "MangaPipeline",
    "MangaPipelineConfig",
    "MangaPipelineResult",
    "PanelArtifact",
    "PipelineError",
    "ProgressCallback",
    "STEP_WEIGHTS",
    "_scaled_progress",
    "_ratio_to_size",
]

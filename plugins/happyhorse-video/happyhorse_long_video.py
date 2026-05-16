"""Long video generation — LLM storyboard decomposition, chain
generation (serial / parallel) and ffmpeg concat / crossfade.

Ported from ``plugins/seedance-video/long_video.py`` with the chain
adapted to call :class:`HappyhorseDashScopeClient` instead of the Ark
client. Each segment is generated as a HappyHorse 1.0 i2v / t2v task
(or any registry-default model for the i2v mode) and chained via
``last_frame_url`` returned by ``client.query_task``.

Modes:

- ``serial``       — each segment uses the previous segment's
                     ``last_frame_url`` as its ``first_frame_url`` so
                     visual continuity is preserved by the model.
- ``parallel``     — segments generated independently with bounded
                     ``run_parallel`` concurrency. Used when the user
                     wants speed and accepts the lack of visual
                     continuity (cuts).
- ``cloud_extend`` — placeholder for HappyHorse / Wan video_extend
                     when the model is wan2.7-i2v with task_type =
                     video-continuation; falls back to ``serial`` if
                     the registry doesn't accept the chosen model.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from happyhorse_inline.llm_json_parser import parse_llm_json_object
from happyhorse_inline.parallel_executor import run_parallel

logger = logging.getLogger(__name__)


def ffmpeg_available() -> bool:
    """Check if ffmpeg is on PATH."""
    return shutil.which("ffmpeg") is not None


STORYBOARD_SYSTEM_PROMPT = """你是专业的 AI 视频分镜师，正在为「快乐马工作室（HappyHorse Studio）」拆分镜。

## 约束条件
- 每段视频时长: {duration} 秒（用户设定，3-15 秒）
- 总目标时长: {total_duration} 秒
- 需要拆为约 {segment_count} 段
- 视频比例: {ratio}，风格: {style}
- 基础模型: HappyHorse 1.0 / Wan 2.6/2.7 i2v（首帧驱动）

## 输出格式（严格 JSON）
{{
  "segments": [
    {{
      "index": 1,
      "duration": {duration},
      "prompt": "镜头语言 + 主体动作 + 氛围 + 关键道具的中文描述（建议 50-120 字）",
      "key_frame_description": "这一段开始画面的文字描述（用于生图做首帧）",
      "end_frame_description": "这一段结束画面的文字描述（用于生图做尾帧）",
      "transition_to_next": "cut",
      "camera_notes": "镜头语言说明（推拉摇移、长焦短焦、航拍等）",
      "audio_notes": "声音设计说明（环境音 / 背景音乐 / 旁白）",
      "characters": ["角色 A", "角色 B"]
    }}
  ],
  "style_prefix": "统一的风格描述前缀（贴在每段 prompt 前面）",
  "character_refs": ["需要的角色参考图说明"],
  "scene_refs": ["需要的场景参考图说明"]
}}

transition_to_next 可选值: "cut" (硬切), "crossfade" (交叉淡化), "ai_extend" (AI 延长过渡)

请确保输出是有效 JSON，不要包含多余文本。"""


async def decompose_storyboard(
    brain: Any,
    story: str,
    total_duration: int = 60,
    segment_duration: int = 10,
    ratio: str = "16:9",
    style: str = "电影级画质",
) -> dict:
    """Use LLM to decompose a story into a multi-segment storyboard.

    Returns a dict shaped like the JSON spec above; on parse failure the
    legacy ``{"error": ..., "raw": ...}`` envelope is preserved so the
    UI can surface the raw text for debugging.
    """
    segment_count = max(1, total_duration // max(1, segment_duration))

    system = STORYBOARD_SYSTEM_PROMPT.format(
        duration=segment_duration,
        total_duration=total_duration,
        segment_count=segment_count,
        ratio=ratio,
        style=style,
    )

    user_msg = f"## 用户故事\n{story}\n\n请生成 {segment_count} 段分镜脚本。"

    try:
        if hasattr(brain, "think"):
            result = await brain.think(prompt=user_msg, system=system)
            text = (
                getattr(result, "content", "")
                or (result.get("content", "") if isinstance(result, dict) else str(result))
            )
        elif hasattr(brain, "chat"):
            result = await brain.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ]
            )
            text = result.get("content", "") if isinstance(result, dict) else str(result)
        else:
            return {"error": "No LLM available"}

        parse_errors: list[str] = []
        parsed = parse_llm_json_object(text, errors=parse_errors)
        if not parsed:
            logger.warning(
                "Storyboard JSON parse failed (5-level): %s",
                "; ".join(parse_errors),
            )
            return {"error": "Failed to parse storyboard JSON", "raw": text}
        return parsed

    except Exception as e:  # noqa: BLE001
        logger.error("Storyboard decomposition failed: %s", e)
        return {"error": str(e)}


# ─── Concat / crossfade ───────────────────────────────────────────────


async def concat_videos(
    video_paths: list[str],
    output_path: str,
    transition: str = "none",
    fade_duration: float = 0.5,
) -> bool:
    """Concatenate multiple video files using ffmpeg.

    Args:
        video_paths: List of input video file paths.
        output_path: Output file path.
        transition: ``"none"`` for lossless concat, ``"crossfade"`` for
            xfade filter (forces re-encode).
        fade_duration: Duration of crossfade (only used when transition=
            ``"crossfade"``).

    Returns:
        True on success, False on failure (logs the reason; never raises).
    """
    if not ffmpeg_available():
        logger.error("ffmpeg not found on PATH")
        return False

    if len(video_paths) < 2:
        if video_paths:
            shutil.copy2(video_paths[0], output_path)
            return True
        return False

    if transition == "none":
        return await _concat_lossless(video_paths, output_path)
    if transition == "crossfade":
        return await _concat_crossfade(video_paths, output_path, fade_duration)
    logger.warning("Unknown transition '%s', falling back to lossless", transition)
    return await _concat_lossless(video_paths, output_path)


async def _concat_lossless(video_paths: list[str], output_path: str) -> bool:
    """Lossless concat using ffmpeg concat demuxer (-c copy)."""
    list_file: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for path in video_paths:
                f.write(f"file '{path}'\n")
            list_file = f.name

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_file,
            "-c",
            "copy",
            output_path,
        ]
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, timeout=120
        )

        if result.returncode != 0:
            logger.error(
                "ffmpeg concat failed: %s",
                result.stderr.decode(errors="replace"),
            )
            return False

        return True
    except Exception as e:  # noqa: BLE001
        logger.error("Lossless concat error: %s", e)
        return False
    finally:
        if list_file:
            Path(list_file).unlink(missing_ok=True)


async def _concat_crossfade(
    video_paths: list[str], output_path: str, fade_dur: float
) -> bool:
    """Crossfade concat using ffmpeg xfade filter (re-encodes via libx264)."""
    if len(video_paths) == 2:
        return await _xfade_two(video_paths[0], video_paths[1], output_path, fade_dur)

    temp_dir = Path(tempfile.mkdtemp(prefix="happyhorse_xfade_"))
    try:
        current = video_paths[0]
        for i in range(1, len(video_paths)):
            temp_out = str(temp_dir / f"xfade_{i}.mp4")
            ok = await _xfade_two(current, video_paths[i], temp_out, fade_dur)
            if not ok:
                return False
            current = temp_out
        shutil.copy2(current, output_path)
        return True
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _xfade_two(
    path_a: str, path_b: str, output: str, fade_dur: float
) -> bool:
    """Apply xfade between two videos (re-encodes both)."""
    try:
        probe_cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path_a,
        ]
        probe = await asyncio.to_thread(
            subprocess.run, probe_cmd, capture_output=True, timeout=10
        )
        dur_a = float(probe.stdout.decode().strip()) if probe.returncode == 0 else 5.0
        offset = max(0, dur_a - fade_dur)

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            path_a,
            "-i",
            path_b,
            "-filter_complex",
            f"xfade=transition=fade:duration={fade_dur}:offset={offset}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            output,
        ]
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, timeout=180
        )
        if result.returncode != 0:
            logger.error("xfade failed: %s", result.stderr.decode(errors="replace"))
            return False
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("xfade error: %s", e)
        return False


# ─── ChainGenerator (HappyHorse / Wan adaptation) ─────────────────────


class ChainGenerator:
    """Manages multi-segment video generation with first/last frame chaining.

    Backed by :class:`HappyhorseDashScopeClient` and
    :class:`HappyhorseTaskManager`. Each segment becomes a regular task
    in the DB (mode = ``i2v`` for chained segments, ``t2v`` for the
    head segment / parallel mode), so the existing Tasks tab + SSE
    progress works unchanged for long videos.
    """

    def __init__(
        self,
        client: Any,
        task_manager: Any,
        *,
        chain_group_id: str = "",
    ) -> None:
        self._client = client
        self._tm = task_manager
        self._chain_group_id = chain_group_id

    async def generate_chain(
        self,
        segments: list[dict],
        model_id: str,
        *,
        ratio: str = "16:9",
        resolution: str = "720P",
        mode: str = "serial",
        max_parallel: int = 3,
        first_frame_url: str | None = None,
    ) -> list[dict]:
        """Generate every segment in order. Always returns one entry per
        input — failures are reported as ``{"error": str, "index": ...}``.

        Args:
            segments: List of storyboard rows
                (``{prompt, duration, transition_to_next, ...}``).
            model_id: Per-segment HappyHorse / Wan model id (e.g.
                ``happyhorse-1.0-i2v`` or ``wan2.6-i2v``).
            ratio: Aspect ratio (used by Wan size dispatch).
            resolution: ``720P`` / ``1080P``.
            mode: ``"serial"`` chains via last_frame, ``"parallel"`` runs
                independently, ``"cloud_extend"`` uses video_extend.
            max_parallel: Concurrency cap (parallel mode only).
            first_frame_url: Optional seed frame for the first segment.
        """
        results: list[dict] = []
        total = len(segments)

        async def _submit_one(
            seg: dict, idx: int, prev_task: dict | None
        ) -> dict:
            seg_first_frame = ""
            seg_mode = "t2v"
            if prev_task is not None:
                last = str(prev_task.get("last_frame_url") or "")
                if last:
                    seg_first_frame = last
                    seg_mode = "i2v"
            elif first_frame_url and idx == 0:
                seg_first_frame = first_frame_url
                seg_mode = "i2v"

            prompt = str(seg.get("prompt") or "")
            duration = int(seg.get("duration") or 5)

            # Persist a DB row up front so the UI can show the
            # segment as `pending` immediately.
            task_id = await self._tm.create_task(
                mode=seg_mode,
                model_id=model_id,
                prompt=prompt,
                params={
                    "model": model_id,
                    "duration": duration,
                    "resolution": resolution,
                    "aspect_ratio": ratio,
                    "first_frame_url": seg_first_frame,
                    "segment_index": seg.get("index", idx + 1),
                    "transition_to_next": seg.get("transition_to_next"),
                },
                chain_group_id=self._chain_group_id,
                chain_index=seg.get("index", idx + 1),
                chain_total=total,
            )

            try:
                dashscope_id = await self._client.submit_video_synth(
                    mode=seg_mode,
                    model_id=model_id,
                    prompt=prompt,
                    first_frame_url=seg_first_frame or None,
                    resolution=resolution,
                    aspect=ratio,
                    duration=duration,
                )
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "long_video segment %d submit failed: %s",
                    idx + 1,
                    e,
                )
                await self._tm.update_task_safe(
                    task_id,
                    status="failed",
                    error_kind="server",
                    error_message=str(e),
                )
                return self._make_error(seg, e, task_id)

            await self._tm.update_task_safe(
                task_id,
                status="running",
                dashscope_id=dashscope_id,
                dashscope_endpoint=model_id,
            )
            completed = await self._wait_for_task(dashscope_id)
            await self._tm.update_task_safe(
                task_id,
                status="succeeded" if completed.get("is_ok") else "failed",
                video_url=str(completed.get("output_url") or ""),
                last_frame_url=str(completed.get("last_frame_url") or ""),
                error_kind=completed.get("error_kind") if not completed.get("is_ok") else None,
                error_message=completed.get("error_message") if not completed.get("is_ok") else None,
            )
            row = await self._tm.get_task(task_id) or {}
            return {
                "task_id": task_id,
                "index": seg.get("index", idx + 1),
                "status": row.get("status"),
                "prompt": prompt,
                "video_url": row.get("video_url") or "",
                "last_frame_url": row.get("last_frame_url") or "",
            }

        if mode in {"serial", "cloud_extend"}:
            prev: dict | None = None
            for i, seg in enumerate(segments):
                row = await _submit_one(seg, i, prev)
                results.append(row)
                if row.get("status") != "succeeded":
                    # Hard stop — chained mode can't continue without
                    # the predecessor's last_frame.
                    break
                prev = row
            return results

        if mode == "parallel":
            async def _parallel(seg_idx_pair: tuple[dict, int]) -> dict:
                seg, i = seg_idx_pair
                return await _submit_one(seg, i, None)

            indexed = [(seg, i) for i, seg in enumerate(segments)]
            pr_results = await run_parallel(
                indexed, _parallel, max_concurrency=max(1, max_parallel)
            )
            for pr in pr_results:
                if pr.ok and pr.value is not None:
                    results.append(pr.value)
                else:
                    seg, i = pr.item if isinstance(pr.item, tuple) else (pr.item, 0)
                    err = pr.error or RuntimeError("unknown failure")
                    results.append(self._make_error(seg, err, ""))
            return results

        raise ValueError(f"unknown chain mode {mode!r}")

    @staticmethod
    def _make_error(seg: dict, exc: Exception, task_id: str) -> dict:
        idx = seg.get("index", 0)
        detail = str(exc)
        logger.error("Chain segment %d failed: %s", idx, detail)
        return {
            "task_id": task_id,
            "error": detail,
            "index": idx,
            "status": "failed",
            "prompt": seg.get("prompt", ""),
        }

    async def _wait_for_task(
        self, dashscope_id: str, timeout: int = 600, interval: float = 10.0
    ) -> dict[str, Any]:
        """Poll the DashScope task until done / timeout."""
        loop = asyncio.get_event_loop()
        start = loop.time()
        while loop.time() - start < timeout:
            try:
                res = await self._client.query_task(dashscope_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "long_video poll error for %s: %s — retrying",
                    dashscope_id,
                    e,
                )
                await asyncio.sleep(interval)
                continue
            if res.get("is_done"):
                return res
            await asyncio.sleep(interval)
        return {
            "task_id": dashscope_id,
            "status": "TIMEOUT",
            "is_done": True,
            "is_ok": False,
            "error_kind": "timeout",
            "error_message": (
                f"DashScope task {dashscope_id} did not finish in {timeout}s"
            ),
        }

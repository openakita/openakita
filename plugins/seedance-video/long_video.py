"""Long video generation — LLM storyboard decomposition, chain generation,
and ffmpeg concatenation/crossfade."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from seedance_inline.llm_json_parser import parse_llm_json_object
from seedance_inline.parallel_executor import run_parallel

logger = logging.getLogger(__name__)


def ffmpeg_available() -> bool:
    """Check if ffmpeg is on PATH."""
    return shutil.which("ffmpeg") is not None


STORYBOARD_SYSTEM_PROMPT = """你是专业的 AI 视频分镜师。请将用户的故事拆解为多段 Seedance 2.0 视频分镜脚本。

## 约束条件
- 每段视频时长: {duration} 秒（用户设定，4-15秒）
- 总目标时长: {total_duration} 秒
- 需要拆为约 {segment_count} 段
- 视频比例: {ratio}，风格: {style}

## 输出格式（严格 JSON）
{{
  "segments": [
    {{
      "index": 1,
      "duration": {duration},
      "prompt": "Seedance 格式的时间轴提示词...",
      "key_frame_description": "这一段开始画面的文字描述（用于生图做首帧）",
      "end_frame_description": "这一段结束画面的文字描述（用于生图做尾帧）",
      "transition_to_next": "cut",
      "camera_notes": "镜头语言说明",
      "audio_notes": "声音设计说明"
    }}
  ],
  "style_prefix": "统一的风格描述前缀",
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
    """Use LLM to decompose a story into multi-segment storyboard."""
    segment_count = max(1, total_duration // segment_duration)

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
            text = getattr(result, "content", "") or (result.get("content", "") if isinstance(result, dict) else str(result))
        elif hasattr(brain, "chat"):
            result = await brain.chat(messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ])
            text = result.get("content", "") if isinstance(result, dict) else str(result)
        else:
            return {"error": "No LLM available"}

        # Sprint 7 / C5 — use SDK 5-level fallback parser (handles fenced ```json
        # blocks, leading prose, escaped quotes, etc.) instead of the brittle
        # ``text.find("{") / text.rfind("}")`` heuristic which used to drop
        # entire storyboards when the model wrapped output in a fence.
        # ``parse_llm_json_object`` never raises — it returns the empty
        # fallback dict on total failure, and the ``errors`` out-param
        # explains which level (L1/L2/L3/L4/L5) gave up.  We map an empty
        # result to the legacy ``{"error": ..., "raw": ...}`` envelope so
        # downstream callers (UI, /long-video/decompose) can still
        # surface the raw text for debugging.
        parse_errors: list[str] = []
        parsed = parse_llm_json_object(text, errors=parse_errors)
        if not parsed:
            logger.warning(
                "Storyboard JSON parse failed (5-level): %s",
                "; ".join(parse_errors),
            )
            return {"error": "Failed to parse storyboard JSON", "raw": text}
        return parsed

    except Exception as e:
        logger.error("Storyboard decomposition failed: %s", e)
        return {"error": str(e)}


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
        transition: "none" for lossless concat, "crossfade" for xfade filter.
        fade_duration: Duration of crossfade (only used when transition="crossfade").

    Returns:
        True on success, False on failure.
    """
    if not ffmpeg_available():
        logger.error("ffmpeg not found on PATH")
        return False

    if len(video_paths) < 2:
        if video_paths:
            import shutil as _shutil
            _shutil.copy2(video_paths[0], output_path)
            return True
        return False

    if transition == "none":
        return await _concat_lossless(video_paths, output_path)
    elif transition == "crossfade":
        return await _concat_crossfade(video_paths, output_path, fade_duration)
    else:
        logger.warning("Unknown transition '%s', falling back to lossless", transition)
        return await _concat_lossless(video_paths, output_path)


async def _concat_lossless(video_paths: list[str], output_path: str) -> bool:
    """Lossless concat using ffmpeg concat demuxer (-c copy)."""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for path in video_paths:
                f.write(f"file '{path}'\n")
            list_file = f.name

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_file, "-c", "copy", output_path,
        ]
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, timeout=120
        )

        Path(list_file).unlink(missing_ok=True)

        if result.returncode != 0:
            logger.error("ffmpeg concat failed: %s", result.stderr.decode(errors="replace"))
            return False

        return True
    except Exception as e:
        logger.error("Lossless concat error: %s", e)
        return False


async def _concat_crossfade(
    video_paths: list[str], output_path: str, fade_dur: float,
) -> bool:
    """Crossfade concat using ffmpeg xfade filter (requires re-encoding)."""
    if len(video_paths) == 2:
        return await _xfade_two(video_paths[0], video_paths[1], output_path, fade_dur)

    temp_dir = Path(tempfile.mkdtemp(prefix="seedance_xfade_"))
    try:
        current = video_paths[0]
        for i in range(1, len(video_paths)):
            temp_out = str(temp_dir / f"xfade_{i}.mp4")
            ok = await _xfade_two(current, video_paths[i], temp_out, fade_dur)
            if not ok:
                return False
            current = temp_out

        import shutil as _shutil
        _shutil.copy2(current, output_path)
        return True
    finally:
        import shutil as _shutil
        _shutil.rmtree(temp_dir, ignore_errors=True)


async def _xfade_two(
    path_a: str, path_b: str, output: str, fade_dur: float,
) -> bool:
    """Apply xfade between two videos."""
    try:
        probe_cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path_a,
        ]
        probe = await asyncio.to_thread(
            subprocess.run, probe_cmd, capture_output=True, timeout=10
        )
        dur_a = float(probe.stdout.decode().strip()) if probe.returncode == 0 else 5.0
        offset = max(0, dur_a - fade_dur)

        cmd = [
            "ffmpeg", "-y",
            "-i", path_a, "-i", path_b,
            "-filter_complex",
            f"xfade=transition=fade:duration={fade_dur}:offset={offset}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            output,
        ]
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, timeout=120
        )
        if result.returncode != 0:
            logger.error("xfade failed: %s", result.stderr.decode(errors="replace"))
            return False
        return True
    except Exception as e:
        logger.error("xfade error: %s", e)
        return False


class ChainGenerator:
    """Manages multi-segment video generation with first/last frame chaining."""

    def __init__(self, ark_client: Any, task_manager: Any) -> None:
        self._ark = ark_client
        self._tm = task_manager

    async def generate_chain(
        self,
        segments: list[dict],
        model_id: str,
        ratio: str = "16:9",
        resolution: str = "720p",
        mode: str = "serial",
        max_parallel: int = 3,
        chain_group: str | None = None,
    ) -> list[dict]:
        """Generate all segments with chaining.

        mode: "serial" — each segment uses previous as reference_video (AI extend)
              "parallel" — each uses return_last_frame for next's first_frame

        ``max_parallel`` (parallel mode only) — bounded concurrency for the
        ``run_parallel`` worker that submits the per-segment Ark calls.
        Always returns a result for every input (no silent skip — N1.1).

        ``chain_group`` (Sprint 8 / V2) — opaque ID stamped onto every
        DB-side ``params.chain_group`` so ``GET /long-video/tasks/<id>``
        can recover progress for a fire-and-forget run, and so the UI can
        prevent duplicate submissions across tab switches / page reloads.
        """
        results: list[dict] = []

        # Helper: build the params dict every create_task() call passes
        # so /long-video/tasks/<group_id> can join and aggregate later.
        def _seg_params(seg: dict) -> dict:
            base: dict = {}
            if chain_group:
                base["chain_group"] = chain_group
                base["segment_index"] = seg.get("index", 0)
                base["chain_mode"] = mode
            return base

        if mode == "serial":
            prev_task = None
            for seg in segments:
                idx = seg.get("index", 0)
                content = self._build_content(seg, prev_task)
                has_frame = bool(prev_task and prev_task.get("last_frame_url"))
                try:
                    result = await self._ark.create_task(
                        model=model_id,
                        content=content,
                        ratio=ratio,
                        duration=seg.get("duration", 10),
                        resolution=resolution,
                        return_last_frame=True,
                    )
                except Exception as e:
                    if has_frame and self._is_image_rejection(e):
                        logger.warning(
                            "Chain segment %d: image rejected (likely human face), "
                            "retrying as pure text-to-video",
                            idx,
                        )
                        content = [{"type": "text", "text": seg.get("prompt", "")}]
                        has_frame = False
                        try:
                            result = await self._ark.create_task(
                                model=model_id,
                                content=content,
                                ratio=ratio,
                                duration=seg.get("duration", 10),
                                resolution=resolution,
                                return_last_frame=True,
                            )
                        except Exception as e2:
                            logger.error("Chain segment %d retry also failed: %s", idx, e2)
                            results.append(self._make_error(seg, e2))
                            break
                    else:
                        results.append(self._make_error(seg, e))
                        break

                task = await self._tm.create_task(
                    ark_task_id=result.get("id", ""),
                    status="running",
                    prompt=seg.get("prompt", ""),
                    mode="i2v" if has_frame else "t2v",
                    model=model_id,
                    params=_seg_params(seg),
                )
                results.append(task)

                task = await self._wait_for_task(task["id"])
                prev_task = task

        elif mode == "parallel":
            async def submit(seg: dict) -> dict:
                content = [{"type": "text", "text": seg.get("prompt", "")}]
                result = await self._ark.create_task(
                    model=model_id,
                    content=content,
                    ratio=ratio,
                    duration=seg.get("duration", 10),
                    resolution=resolution,
                    return_last_frame=True,
                )
                return await self._tm.create_task(
                    ark_task_id=result.get("id", ""),
                    status="running",
                    prompt=seg.get("prompt", ""),
                    mode="t2v",
                    model=model_id,
                    params=_seg_params(seg),
                )

            # N1.1 防御：run_parallel 保证每个 input 都有 ParallelResult，
            # 无论 success / failed / cancelled，绝不静默丢弃。
            submit_results = await run_parallel(
                segments, submit, max_concurrency=max(1, max_parallel),
            )
            for pr in submit_results:
                # ``ParallelResult`` exposes ``.ok`` / ``.failed`` / ``.cancelled``
                # (NOT ``.success`` — that attribute does not exist and would
                # raise AttributeError at runtime; Sprint 7 caught this via
                # tests/test_long_video.py).
                if pr.ok and pr.value is not None:
                    results.append(pr.value)
                else:
                    seg = pr.item if isinstance(pr.item, dict) else {}
                    err = pr.error or RuntimeError("unknown failure")
                    results.append(self._make_error(seg, err))

            for entry in list(results):
                if "error" not in entry and entry.get("id"):
                    completed = await self._wait_for_task(entry["id"])
                    idx = results.index(entry)
                    results[idx] = completed

        return results

    def _build_content(self, segment: dict, prev_task: dict | None) -> list[dict]:
        """Build content array for a segment, optionally chaining from previous.

        Uses last_frame → first_frame chaining as required by Seedance API:
        the previous video's last frame becomes the next video's first frame image.
        """
        content: list[dict] = [{"type": "text", "text": segment.get("prompt", "")}]
        if prev_task:
            frame_url = prev_task.get("last_frame_url") or ""
            if frame_url:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": frame_url},
                })
                logger.info(
                    "Chain: using last_frame from segment %s as first_frame",
                    prev_task.get("id", "?"),
                )
            else:
                logger.warning(
                    "Chain: prev segment %s has no last_frame_url, generating as standalone t2v",
                    prev_task.get("id", "?"),
                )
        return content

    @staticmethod
    def _is_image_rejection(exc: Exception) -> bool:
        """Check if the exception is an image content policy rejection."""
        msg = str(exc).lower()
        markers = [
            "sensitivecontent",
            "privacyinformation",
            "real person",
            "inputimage",
            "image_url",
            "nsfw",
        ]
        return any(m in msg for m in markers)

    @staticmethod
    def _make_error(seg: dict, exc: Exception) -> dict:
        idx = seg.get("index", 0)
        detail = str(exc)
        if hasattr(exc, "response"):
            try:
                detail = exc.response.text
            except Exception:
                pass
        logger.error("Chain segment %d failed: %s", idx, detail)
        return {
            "error": detail, "index": idx,
            "status": "failed", "prompt": seg.get("prompt", ""),
        }

    async def _wait_for_task(self, task_id: str, timeout: int = 600) -> dict:
        """Poll until task completes or timeout."""
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            task = await self._tm.get_task(task_id)
            if task and task["status"] in ("succeeded", "failed"):
                return task
            await asyncio.sleep(10)
        task = await self._tm.get_task(task_id)
        return task or {"id": task_id, "status": "timeout"}

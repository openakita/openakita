"""Long video generation — LLM storyboard decomposition, chain generation,
and ffmpeg concatenation/crossfade."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

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
            result = await brain.think(user_message=user_msg, system_prompt=system)
            text = result.get("content", "") if isinstance(result, dict) else str(result)
        elif hasattr(brain, "chat"):
            result = await brain.chat(messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ])
            text = result.get("content", "") if isinstance(result, dict) else str(result)
        else:
            return {"error": "No LLM available"}

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return {"error": "Failed to parse storyboard JSON", "raw": text}

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
    ) -> list[dict]:
        """Generate all segments with chaining.

        mode: "serial" — each segment uses previous as reference_video (AI extend)
              "parallel" — each uses return_last_frame for next's first_frame
        """
        results = []

        if mode == "serial":
            prev_task = None
            for seg in segments:
                content = self._build_content(seg, prev_task)
                try:
                    result = await self._ark.create_task(
                        model=model_id,
                        content=content,
                        ratio=ratio,
                        duration=seg.get("duration", 10),
                        resolution=resolution,
                        return_last_frame=True,
                    )
                    task = await self._tm.create_task(
                        ark_task_id=result.get("id", ""),
                        status="running",
                        prompt=seg.get("prompt", ""),
                        mode="extend" if prev_task else "t2v",
                        model=model_id,
                    )
                    results.append(task)

                    task = await self._wait_for_task(task["id"])
                    prev_task = task
                except Exception as e:
                    logger.error("Chain segment %d failed: %s", seg.get("index", 0), e)
                    results.append({"error": str(e), "index": seg.get("index", 0)})
                    break

        elif mode == "parallel":
            tasks = []
            for seg in segments:
                content = [{"type": "text", "text": seg.get("prompt", "")}]
                try:
                    result = await self._ark.create_task(
                        model=model_id,
                        content=content,
                        ratio=ratio,
                        duration=seg.get("duration", 10),
                        resolution=resolution,
                        return_last_frame=True,
                    )
                    task = await self._tm.create_task(
                        ark_task_id=result.get("id", ""),
                        status="running",
                        prompt=seg.get("prompt", ""),
                        mode="t2v",
                        model=model_id,
                    )
                    tasks.append(task)
                except Exception as e:
                    tasks.append({"error": str(e), "index": seg.get("index", 0)})

            for task in tasks:
                if "error" not in task:
                    completed = await self._wait_for_task(task["id"])
                    results.append(completed)
                else:
                    results.append(task)

        return results

    def _build_content(self, segment: dict, prev_task: dict | None) -> list[dict]:
        """Build content array for a segment, optionally referencing previous."""
        content: list[dict] = [{"type": "text", "text": segment.get("prompt", "")}]
        if prev_task and prev_task.get("video_url"):
            content.insert(0, {
                "type": "video_url",
                "video_url": {"url": prev_task["video_url"]},
            })
        return content

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

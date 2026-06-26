"""8-step breakdown pipeline + 4 mode runners (§7).

The pipeline lives behind a small ``IdeaPipelineContext`` dataclass
(``ctx``) so tests can construct one with fake collectors / fake
DashScope client / fake MDRM adapter and exercise each step in
isolation.

Public API
----------
* ``IdeaPipelineContext`` — bag of plugin-side dependencies + per-task
  scratch state (``input``, ``metadata``, ``frames``,
  ``structure``, ``comments``, ``cost`` …).
* ``run_breakdown_url(ctx)`` — drives the canonical 8 steps end-to-end.
* ``run_radar_pull(ctx)`` — fan out via ``CollectorRegistry`` then
  persist ranked items into ``trend_items``.
* ``run_compare_accounts(ctx)`` — pull each account, aggregate, run
  one cross-account LLM analysis.
* ``run_script_remix(ctx)`` — fetch the source ``trend_item``,
  optionally pull MDRM inspirations, generate ``num_variants`` scripts.

Every step that mutates a task does so via
``ctx.tm.update_task_safe`` (the §10 whitelist). All ``VendorError``s
flow up unchanged so the route layer can render the §15 hint table.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from idea_collectors import CollectorRegistry
from idea_engine_api import filter_items_by_keywords
from idea_dashscope_client import DashScopeClient, faster_whisper_available
from idea_models import (
    PERSONAS_BY_NAME,
    PLUGIN_ID,
    PROMPTS,
    ResolvedSource,
    TrendItem,
    estimate_cost,
    format_script_remix_markdown,
    normalize_script_remix_variants,
    hint_for,
)
from idea_research_inline.mdrm_adapter import HookRecord, MdrmAdapter
from idea_research_inline.parallel_executor import run_with_semaphore
from idea_research_inline.vendor_client import VendorError, VendorTimeoutError

_LOG = logging.getLogger("idea-research.pipeline")

DEFAULT_MAX_FRAMES = 16
MAX_FRAME_LIMIT = 32
RADAR_PULL_TIMEOUT_S = 120.0
RESOLVE_SOURCE_TIMEOUT_S = 90.0
YTDLP_DOWNLOAD_TIMEOUT_YOUTUBE_PROXY_S = 90.0
YTDLP_DOWNLOAD_TIMEOUT_YOUTUBE_DIRECT_S = 25.0
YTDLP_DOWNLOAD_TIMEOUT_BILIBILI_PROXY_S = 180.0
YTDLP_DOWNLOAD_TIMEOUT_BILIBILI_DIRECT_S = 60.0
YTDLP_DOWNLOAD_TIMEOUT_DEFAULT_S = 120.0
COMPARE_ACCOUNT_PULL_TIMEOUT_S = 30.0
COMPARE_ACCOUNT_ANALYZE_TIMEOUT_S = 60.0


# --------------------------------------------------------------------------- #
# Lifecycle helpers                                                            #
# --------------------------------------------------------------------------- #


def _now() -> int:
    return int(time.time())


def _trend_item_from_db_row(row: dict[str, Any]) -> TrendItem:
    raw_payload = row.get("raw_payload_json") or "{}"
    if isinstance(raw_payload, dict):
        raw_payload = json.dumps(raw_payload, ensure_ascii=False)
    return TrendItem(
        id=str(row["id"]),
        platform=row["platform"],
        external_id=str(row.get("external_id") or ""),
        external_url=str(row.get("external_url") or ""),
        title=str(row.get("title") or ""),
        author=str(row.get("author") or ""),
        author_url=row.get("author_url"),
        cover_url=row.get("cover_url"),
        duration_seconds=row.get("duration_seconds"),
        description=row.get("description"),
        like_count=row.get("like_count"),
        comment_count=row.get("comment_count"),
        share_count=row.get("share_count"),
        view_count=row.get("view_count"),
        publish_at=int(row.get("publish_at") or 0),
        fetched_at=int(row.get("fetched_at") or 0),
        engine_used=row.get("engine_used") or "a",
        collector_name=str(row.get("collector_name") or "cache"),
        raw_payload_json=raw_payload if isinstance(raw_payload, str) else "{}",
        score=float(row.get("score") or 0),
        keywords_matched=list(row.get("keywords_matched") or []),
        hook_type_guess=row.get("hook_type_guess"),
        data_quality=row.get("data_quality") or "medium",
        mdrm_hits=list(row.get("mdrm_hits") or []),
    )


def _resolve_ytdlp_runner() -> list[str] | None:
    from idea_research_inline.dep_bootstrap import resolve_ytdlp_runner

    return resolve_ytdlp_runner()


def _yt_dlp_format_candidates(url: str) -> list[str]:
    _ = url
    return [
        "bestvideo*[height<=720]+bestaudio/best[height<=720]/best",
        "bestvideo*+bestaudio/best",
        "best",
    ]


def _is_bilibili_url(url: str) -> bool:
    u = (url or "").lower()
    return "bilibili.com" in u or "b23.tv" in u


def _is_youtube_url(url: str) -> bool:
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


def _ytdlp_failure_kind(stderr: str) -> str:
    text = (stderr or "").lower()
    if any(
        token in text
        for token in (
            "timed out",
            "timeout",
            "transporterror",
            "unable to download",
            "connection",
            "network",
            "getaddrinfo failed",
        )
    ):
        return "network"
    return "format"


def _yt_dlp_proxy_arg() -> list[str]:
    from idea_engine_api import resolve_http_proxy

    proxy = resolve_http_proxy()
    return ["--proxy", proxy] if proxy else []


def _ytdlp_download_timeout_s(url: str) -> float:
    from idea_engine_api import resolve_http_proxy

    has_proxy = bool(resolve_http_proxy())
    if _is_youtube_url(url):
        return (
            YTDLP_DOWNLOAD_TIMEOUT_YOUTUBE_PROXY_S
            if has_proxy
            else YTDLP_DOWNLOAD_TIMEOUT_YOUTUBE_DIRECT_S
        )
    if _is_bilibili_url(url):
        return (
            YTDLP_DOWNLOAD_TIMEOUT_BILIBILI_PROXY_S
            if has_proxy
            else YTDLP_DOWNLOAD_TIMEOUT_BILIBILI_DIRECT_S
        )
    return YTDLP_DOWNLOAD_TIMEOUT_DEFAULT_S


def _yt_dlp_request_args(url: str, *, cookie_header: str | None = None) -> list[str]:
    args: list[str] = []
    if _is_youtube_url(url):
        args.extend(
            [
                "--extractor-args",
                "youtube:player_client=android,web",
                "--socket-timeout",
                "30",
                *_yt_dlp_proxy_arg(),
            ]
        )
    if _is_bilibili_url(url):
        args.extend(
            [
                "--user-agent",
                (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "--referer",
                "https://www.bilibili.com/",
                "--add-header",
                "Origin:https://www.bilibili.com",
                "--add-header",
                "Accept-Language:zh-CN,zh;q=0.9,en;q=0.8",
            ]
        )
    if cookie_header:
        args.extend(["--add-header", f"Cookie:{cookie_header}"])
    return args


def _yt_dlp_subtitle_request_args(url: str) -> list[str]:
    """YouTube subtitle-only fetch — web client avoids empty VTT with android+SABR."""

    if not _is_youtube_url(url):
        return _yt_dlp_request_args(url)
    return [
        "--extractor-args",
        "youtube:player_client=web",
        "--socket-timeout",
        "30",
        *_yt_dlp_proxy_arg(),
    ]


def _urllib_urlopen(req: urllib.request.Request, *, timeout: float) -> Any:
    from idea_engine_api import resolve_http_proxy

    proxy = resolve_http_proxy()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _bounded_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(min_value, min(max_value, n))


def _task_file_route(task_id: str, rel_path: str) -> str:
    clean = rel_path.strip().replace("\\", "/").lstrip("/")
    return f"/api/plugins/{PLUGIN_ID}/tasks/{task_id}/files/{clean}"


# --------------------------------------------------------------------------- #
# IdeaPipelineContext                                                          #
# --------------------------------------------------------------------------- #


class _TaskManagerProtocol(Protocol):
    async def update_task_safe(self, task_id: str, updates: dict[str, Any]) -> dict[str, Any]: ...

    async def upsert_trend_item(self, item: dict[str, Any]) -> None: ...

    async def insert_hook_library(
        self, record: dict[str, Any], *, write_result: dict[str, str] | None = None
    ) -> str: ...

    async def get_task(self, task_id: str) -> dict[str, Any] | None: ...

    async def get_trend_item(self, item_id: str) -> dict[str, Any] | None: ...


@dataclass
class IdeaPipelineContext:
    """Per-task scratch + injected dependencies."""

    task_id: str
    mode: str
    input: dict[str, Any]
    work_dir: Path
    tm: _TaskManagerProtocol
    registry: CollectorRegistry
    dashscope: DashScopeClient
    mdrm: MdrmAdapter
    persona_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    source_info: dict[str, Any] = field(default_factory=dict)
    frames: list[dict[str, Any]] = field(default_factory=list)
    transcript: dict[str, Any] | None = None
    structure: dict[str, Any] = field(default_factory=dict)
    comments_summary: dict[str, Any] | None = None
    persona_takeaways: list[str] = field(default_factory=list)
    breakdown: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, float] = field(default_factory=dict)
    handoff_target: str | None = None

    # Hooks tests can patch to skip subprocess work.
    download_media_fn: Callable[[Path, str], dict[str, Path]] | None = None
    subtitle_fetch_fn: Callable[[Path, str], dict[str, Any] | None] | None = None
    extract_frames_fn: Callable[[Path, Path, str, int], list[Path]] | None = None

    # ---- helpers ----------------------------------------------------------

    async def update(self, **fields: Any) -> None:
        """Convenience wrapper around ``tm.update_task_safe``."""

        await self.tm.update_task_safe(self.task_id, fields)

    def write_json(self, name: str, payload: Any) -> Path:
        path = self.work_dir / name
        self.work_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path


# --------------------------------------------------------------------------- #
# 8-step breakdown pipeline                                                    #
# --------------------------------------------------------------------------- #


async def setup_environment(ctx: IdeaPipelineContext) -> None:
    """Step 1 — create the per-task work directory and metadata stub."""

    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    (ctx.work_dir / "frames").mkdir(parents=True, exist_ok=True)
    ctx.metadata = {
        "task_id": ctx.task_id,
        "mode": ctx.mode,
        "url": ctx.input.get("url"),
        "persona": ctx.persona_name,
        "started_at": _now(),
        "platform": None,
    }
    ctx.write_json("metadata.json", ctx.metadata)
    await ctx.update(
        status="running",
        progress_pct=5,
        current_step="setup_environment",
        started_at=_now(),
    )


async def _commit_resolved_source(
    ctx: IdeaPipelineContext, resolved: ResolvedSource
) -> ResolvedSource:
    item = resolved.item
    ctx.source_info = {
        "platform": item.platform,
        "external_id": item.external_id,
        "external_url": item.external_url,
        "title": item.title,
        "author": item.author,
        "duration_seconds": item.duration_seconds,
        "comment_count": item.comment_count,
        "publish_at": item.publish_at,
        "cover_url": item.cover_url,
        "description": item.description,
    }
    ctx.metadata["platform"] = item.platform
    ctx.metadata["title"] = item.title
    ctx.metadata["author"] = item.author
    ctx.metadata["duration_seconds"] = item.duration_seconds
    ctx.write_json("metadata.json", ctx.metadata)
    ctx.write_json("source_info.json", ctx.source_info)
    await ctx.update(progress_pct=15, current_step="resolve_source")
    return resolved


async def resolve_source(ctx: IdeaPipelineContext) -> ResolvedSource:
    """Step 2 — turn the user URL into a TrendItem skeleton."""

    from idea_engine_api import (
        _platform_from_url,
        fetch_youtube_oembed_item,
        youtube_item_from_url,
    )

    url = str(ctx.input.get("url") or "").strip()
    if not url:
        err = VendorError("breakdown_url 缺少 url 字段")
        err.error_kind = "format"
        raise err
    await ctx.update(progress_pct=8, current_step="resolve_source")
    trend_item_id = str(ctx.input.get("trend_item_id") or "").strip()
    if trend_item_id:
        source_row = await ctx.tm.get_trend_item(trend_item_id)
        if source_row:
            item = _trend_item_from_db_row(source_row)
            return await _commit_resolved_source(ctx, ResolvedSource(item=item))
    platform = _platform_from_url(url) or "other"
    with_comments = bool(ctx.input.get("enable_comments", True))
    resolved: ResolvedSource | None = None
    if platform == "youtube":
        http_client = getattr(ctx.registry, "_client", None)
        oembed_item = await fetch_youtube_oembed_item(url, client=http_client)
        if oembed_item is not None:
            resolved = ResolvedSource(item=oembed_item)
        else:
            ctx.metadata["youtube_network_degraded"] = True
        if resolved is None:
            skeleton = youtube_item_from_url(url)
            if skeleton is not None:
                resolved = ResolvedSource(item=skeleton)
    if resolved is None and platform != "youtube":
        resolved = await ctx.registry.fetch_single_source(
            url,
            with_comments=with_comments,
        )
    elif resolved is None:
        err = VendorError(f"无法解析 YouTube URL: {url}")
        err.error_kind = "format"
        raise err
    if resolved is None:
        err = VendorError(f"无法解析 URL: {url}")
        err.error_kind = "format"
        raise err
    item = resolved.item
    if platform == "youtube" and with_comments:
        resolved = await _enrich_youtube_resolved(ctx, url, resolved)
        item = resolved.item
    return await _commit_resolved_source(ctx, resolved)


async def _enrich_youtube_resolved(
    ctx: IdeaPipelineContext,
    url: str,
    resolved: ResolvedSource,
) -> ResolvedSource:
    """Attach YouTube Data API metadata/comments when an API key is configured."""

    try:
        collector = ctx.registry._engine_a_for("youtube")  # type: ignore[union-attr]
        api_key = getattr(collector, "_api_key", None)
        if not api_key:
            return resolved
        enriched = await collector.fetch_single_source(url, with_comments=True)  # type: ignore[union-attr]
        if enriched is None:
            return resolved
        comments = list(enriched.comments or [])
        return ResolvedSource(
            item=enriched.item,
            comments=comments or list(resolved.comments or []),
        )
    except Exception as exc:
        _LOG.debug("youtube api enrich skipped: %s", exc)
        return resolved


async def _resolve_source_with_timeout(ctx: IdeaPipelineContext) -> ResolvedSource:
    task = asyncio.create_task(resolve_source(ctx), name=f"resolve_source:{ctx.task_id}")
    try:
        done, _pending = await asyncio.wait({task}, timeout=RESOLVE_SOURCE_TIMEOUT_S)
        if done:
            return task.result()
        task.cancel()
        task.add_done_callback(_consume_task_exception)
        err = VendorTimeoutError(
            f"resolve_source timed out after {RESOLVE_SOURCE_TIMEOUT_S:.0f}s"
        )
        err.error_kind = "timeout"
        raise err
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
            task.add_done_callback(_consume_task_exception)
        raise


async def download_media(ctx: IdeaPipelineContext) -> dict[str, Path]:
    """Step 3 — yt-dlp + ffmpeg into ``video.mp4`` + ``audio.wav``."""

    url = str(ctx.source_info.get("external_url") or ctx.input.get("url"))
    await ctx.update(progress_pct=20, current_step="download_media")
    cookie_header = await _cookie_header_for(ctx, str(ctx.source_info.get("platform") or ""))
    if ctx.download_media_fn is not None:
        artefacts = await asyncio.to_thread(ctx.download_media_fn, ctx.work_dir, url)
    else:
        artefacts = await asyncio.to_thread(
            _download_media_default,
            ctx.work_dir,
            url,
            cookie_header=cookie_header,
        )
    await ctx.update(progress_pct=30, current_step="download_media")
    return artefacts


async def _cookie_header_for(ctx: IdeaPipelineContext, platform: str) -> str | None:
    if not platform:
        return None
    get_cookie_header = getattr(ctx.registry, "cookie_header", None)
    if not callable(get_cookie_header):
        return None
    with contextlib.suppress(Exception):
        return await get_cookie_header(platform)
    return None


def _download_media_default(
    work_dir: Path,
    url: str,
    *,
    cookie_header: str | None = None,
) -> dict[str, Path]:
    ytdlp_runner = _resolve_ytdlp_runner()
    if ytdlp_runner is None:
        err = VendorError("yt-dlp 未安装；执行 `pip install yt-dlp`")
        err.error_kind = "dependency"
        raise err
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        err = VendorError("ffmpeg 未安装；请按平台安装 FFmpeg 套件")
        err.error_kind = "dependency"
        raise err
    video_path = work_dir / "video.mp4"
    audio_path = work_dir / "audio.wav"
    work_dir.mkdir(parents=True, exist_ok=True)
    from idea_research_inline.dep_bootstrap import run_ytdlp_subprocess

    ytdlp_timeout = _ytdlp_download_timeout_s(url)
    last_stderr = ""
    for fmt in _yt_dlp_format_candidates(url):
        if video_path.exists():
            video_path.unlink()
        try:
            proc = run_ytdlp_subprocess(
                [
                    *ytdlp_runner,
                    *_yt_dlp_request_args(url, cookie_header=cookie_header),
                    "-f",
                    fmt,
                    "--merge-output-format",
                    "mp4",
                    "-o",
                    str(video_path),
                    "--no-playlist",
                    url,
                ],
                timeout=ytdlp_timeout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            from idea_engine_api import resolve_http_proxy

            err = VendorError(
                f"yt-dlp timed out after {ytdlp_timeout:.0f}s"
                + (
                    "；请检查 Settings 中的 http_proxy 或代理是否可用"
                    if not resolve_http_proxy()
                    else ""
                )
            )
            err.error_kind = "network"
            raise err from exc
        last_stderr = proc.stderr or ""
        if proc.returncode == 0 and video_path.exists():
            break
        if "Requested format is not available" not in last_stderr:
            err = VendorError(f"yt-dlp failed: {last_stderr[-200:] if last_stderr else ''}")
            err.error_kind = _ytdlp_failure_kind(last_stderr)
            raise err
    else:
        err = VendorError(f"yt-dlp failed: {last_stderr[-200:] if last_stderr else ''}")
        err.error_kind = _ytdlp_failure_kind(last_stderr)
        raise err
    proc2 = subprocess.run(  # noqa: S603
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            str(audio_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if proc2.returncode != 0 or not audio_path.exists():
        err = VendorError(f"ffmpeg audio extract failed: {proc2.stderr[-200:]}")
        err.error_kind = "format"
        raise err
    return {"video": video_path, "audio": audio_path}


async def visual_keyframes(
    ctx: IdeaPipelineContext,
    *,
    video: Path,
    strategy: str = "hybrid",
    max_frames: int = DEFAULT_MAX_FRAMES,
    concurrency: int = 4,
) -> list[dict[str, Any]]:
    """Step 5 — extract frames + describe each via Qwen-VL-Max."""

    extract = ctx.extract_frames_fn or _extract_frames_default
    try:
        frame_paths = await asyncio.to_thread(
            extract,
            video,
            ctx.work_dir / "frames",
            strategy,
            max_frames,
        )
    except VendorError:
        raise
    except Exception as exc:  # treat unknown subprocess crashes as dependency
        err = VendorError(f"frame extraction crashed: {exc}")
        err.error_kind = "dependency"
        raise err from exc

    async def describe(p: Path) -> dict[str, Any]:
        rel_path = f"frames/{p.name}"
        try:
            desc = await ctx.dashscope.describe_image(p)
        except VendorError as exc:
            _LOG.warning("VLM describe failed for %s (degrading): %s", p.name, exc)
            return {
                "frame": p.name,
                "image_path": rel_path,
                "image_url": _task_file_route(ctx.task_id, rel_path),
                "timestamp": None,
                "vlm_description": "",
                "desc": "",
                "error_kind": exc.error_kind,
                "message": str(exc),
            }
        return {
            "frame": p.name,
            "image_path": rel_path,
            "image_url": _task_file_route(ctx.task_id, rel_path),
            "timestamp": None,
            "vlm_description": desc.desc,
            "desc": desc.desc,
            "has_text": desc.has_text,
            "text_extracted": desc.text_extracted,
            "brand_visible": desc.brand_visible,
        }

    results = await run_with_semaphore(
        frame_paths,
        describe,
        concurrency=concurrency,
        return_exceptions=False,
    )
    frames: list[dict[str, Any]] = list(results)  # type: ignore[arg-type]
    ctx.frames = frames
    ctx.write_json("frames.json", frames)
    ctx.cost["vlm_frames"] = ctx.cost.get("vlm_frames", 0.0) + 0.02 * len(frames)
    await ctx.update(progress_pct=60, current_step="visual_keyframes")
    return frames


async def visual_keyframes_from_cover(
    ctx: IdeaPipelineContext,
    *,
    cover_url: str,
) -> list[dict[str, Any]]:
    """Fallback VLM pass using the video thumbnail when full download is unavailable."""

    import httpx

    frames_dir = ctx.work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    cover_path = frames_dir / "cover.jpg"
    client = getattr(ctx.registry, "_client", None)
    try:
        if client is not None:
            resp = await client.get(cover_url, timeout=30.0)
        else:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as own:
                resp = await own.get(cover_url)
        resp.raise_for_status()
        cover_path.write_bytes(resp.content)
    except Exception as exc:
        _LOG.warning("thumbnail download failed: %s", exc)
        ctx.frames = []
        ctx.write_json("frames.json", [])
        await ctx.update(progress_pct=60, current_step="visual_keyframes(thumbnail_failed)")
        return []

    rel_path = "frames/cover.jpg"
    try:
        desc = await ctx.dashscope.describe_image(cover_path)
        frame = {
            "frame": cover_path.name,
            "image_path": rel_path,
            "image_url": _task_file_route(ctx.task_id, rel_path),
            "timestamp": 0,
            "vlm_description": desc.desc,
            "desc": desc.desc,
            "has_text": desc.has_text,
            "text_extracted": desc.text_extracted,
            "brand_visible": desc.brand_visible,
            "source": "thumbnail",
        }
    except VendorError as exc:
        _LOG.warning("thumbnail VLM failed: %s", exc)
        frame = {
            "frame": cover_path.name,
            "image_path": rel_path,
            "image_url": _task_file_route(ctx.task_id, rel_path),
            "timestamp": 0,
            "vlm_description": "",
            "desc": "",
            "error_kind": exc.error_kind,
            "message": str(exc),
            "source": "thumbnail",
        }
    ctx.frames = [frame]
    ctx.write_json("frames.json", ctx.frames)
    ctx.cost["vlm_frames"] = ctx.cost.get("vlm_frames", 0.0) + 0.02
    await ctx.update(progress_pct=60, current_step="visual_keyframes(thumbnail)")
    return ctx.frames


async def transcribe_audio(ctx: IdeaPipelineContext, *, audio: Path | None) -> dict[str, Any] | None:
    """Optional ASR step — produce transcript when the backend is available."""

    subtitle_payload = await _try_youtube_subtitle_transcript(
        ctx,
        url=str(ctx.source_info.get("external_url") or ctx.input.get("url") or ""),
    )
    if subtitle_payload is not None:
        ctx.transcript = subtitle_payload
        ctx.write_json("transcript.json", subtitle_payload)
        await ctx.update(progress_pct=45, current_step="subtitle_transcript")
        return subtitle_payload
    if audio is None or not audio.exists():
        if ctx.metadata.get("download_skipped"):
            from idea_engine_api import resolve_http_proxy

            if resolve_http_proxy():
                message = (
                    "视频下载已跳过或失败，且未能拉取 YouTube 字幕（yt-dlp / Invidious 均失败）。"
                    "请确认 http_proxy 可访问 YouTube，或稍后重试。"
                )
            else:
                message = (
                    "视频下载已跳过（未配置 http_proxy），且未能通过 yt-dlp 或 Invidious 拉取 YouTube 字幕。"
                    "请在设置中配置 http_proxy（如 http://127.0.0.1:7890）并确认代理可用后重试。"
                )
            payload = {
                "error_kind": "network",
                "message": message,
            }
        else:
            payload = {
                "error_kind": "unavailable",
                "message": "未检测到可用音频文件，无法进行 ASR 转写。",
            }
        ctx.transcript = payload
        ctx.write_json("transcript.json", payload)
        await ctx.update(progress_pct=45, current_step="transcribe_audio(unavailable)")
        return payload
    if (
        str(ctx.source_info.get("platform") or "").lower() == "youtube"
        and str(ctx.input.get("asr_backend", "auto")) in {"auto", "cloud"}
        and not faster_whisper_available()
    ):
        payload = {
            "error_kind": "unavailable",
            "message": "未检测到 YouTube 字幕，且当前环境未安装本地 ASR；当前云端 ASR 路径无法直接转写本地音频。",
        }
        ctx.transcript = payload
        ctx.write_json("transcript.json", payload)
        await ctx.update(progress_pct=45, current_step="transcribe_audio(unavailable)")
        return payload
    try:
        transcript = await ctx.dashscope.transcribe_audio(
            audio,
            backend=str(ctx.input.get("asr_backend", "auto")),
            language=str(ctx.input.get("asr_language", "zh")),
        )
    except VendorError as exc:
        _LOG.warning("transcribe_audio failed (degrading): %s", exc)
        payload = {"error_kind": exc.error_kind, "message": str(exc)}
        ctx.transcript = payload
        ctx.write_json("transcript.json", payload)
        await ctx.update(progress_pct=45, current_step="transcribe_audio(skipped)")
        return payload

    payload = {
        "backend": transcript.backend,
        "text": transcript.text,
        "segments": [
            {"start": seg.start, "end": seg.end, "text": seg.text}
            for seg in transcript.segments
        ],
        "language": transcript.language,
        "cost_cny": transcript.cost_cny,
    }
    if not str(payload.get("text") or "").strip() and not payload["segments"]:
        payload = {
            "error_kind": "empty",
            "message": "未识别到可转写语音；该视频可能主要为音乐、无对白或音质不适合 ASR。",
            "backend": transcript.backend,
            "language": transcript.language,
            "cost_cny": transcript.cost_cny,
        }
    ctx.transcript = payload
    ctx.write_json("transcript.json", payload)
    if transcript.cost_cny:
        ctx.cost["transcript_asr"] = ctx.cost.get("transcript_asr", 0.0) + transcript.cost_cny
    await ctx.update(progress_pct=45, current_step="transcribe_audio")
    return payload


async def _try_youtube_subtitle_transcript(
    ctx: IdeaPipelineContext,
    *,
    url: str,
) -> dict[str, Any] | None:
    if str(ctx.source_info.get("platform") or "").lower() != "youtube":
        return None
    fetcher = ctx.subtitle_fetch_fn or _fetch_youtube_subtitles_default
    try:
        return await asyncio.to_thread(fetcher, ctx.work_dir, url)
    except Exception as exc:
        _LOG.warning("youtube subtitle fetch failed: %s", exc)
        return None


_YOUTUBE_SUBTITLE_LANG_PREFS: tuple[str, ...] = (
    "zh.*,zh-Hans.*,zh-Hant.*,en.*,en-US.*",
    "all",
)


def _guess_vtt_language(filename: str) -> str | None:
    name = filename.lower()
    if ".zh" in name:
        return "zh"
    if ".en" in name:
        return "en"
    match = re.search(r"\.([a-z]{2}(?:-[a-z0-9]+)*)\.", name)
    if match:
        return match.group(1).split("-")[0]
    return None


def _subtitle_file_priority(path: Path) -> tuple[int, str]:
    name = path.name.lower()
    if ".zh" in name:
        return (0, name)
    if ".en" in name:
        return (1, name)
    if ".auto" in name:
        return (3, name)
    return (2, name)


def _select_youtube_subtitle_file(files: list[Path]) -> Path:
    return sorted(files, key=_subtitle_file_priority)[0]


def _download_youtube_subtitles(
    work_dir: Path,
    url: str,
    *,
    ytdlp_runner: list[str],
    sub_langs: str,
    request_args: list[str] | None = None,
) -> list[Path]:
    from idea_research_inline.dep_bootstrap import run_ytdlp_subprocess

    stem = work_dir / "captions"
    for old in work_dir.glob("captions*.vtt"):
        with contextlib.suppress(OSError):
            old.unlink()
    yt_args = request_args if request_args is not None else _yt_dlp_subtitle_request_args(url)
    proc = run_ytdlp_subprocess(
        [
            *ytdlp_runner,
            *yt_args,
            "--ignore-no-formats-error",
            "--skip-download",
            "--write-auto-sub",
            "--write-sub",
            "--sub-format",
            "vtt",
            "--sub-langs",
            sub_langs,
            "-o",
            str(stem) + ".%(ext)s",
            "--no-playlist",
            url,
        ],
        timeout=_ytdlp_download_timeout_s(url),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return sorted(work_dir.glob("captions*.vtt"))


def _yt_dlp_subtitle_client_attempts(url: str) -> list[list[str]]:
    """Ordered yt-dlp request arg sets for subtitle-only fetch."""

    proxy_tail = ["--socket-timeout", "30", *_yt_dlp_proxy_arg()]
    if not _is_youtube_url(url):
        return [_yt_dlp_subtitle_request_args(url)]
    return [
        ["--extractor-args", "youtube:player_client=web", *proxy_tail],
        ["--extractor-args", "youtube:player_client=android,web", *proxy_tail],
    ]


def _youtube_video_id_from_url(url: str) -> str | None:
    from idea_engine_api import _YOUTUBE_VIDEO_ID_RE

    match = _YOUTUBE_VIDEO_ID_RE.search(url or "")
    return match.group(1) if match else None


def _pick_invidious_caption_lang(captions: list[dict[str, Any]]) -> str | None:
    pairs: list[tuple[str, str]] = []
    for entry in captions:
        if not isinstance(entry, dict):
            continue
        code = str(
            entry.get("languageCode") or entry.get("language_code") or ""
        ).strip()
        if code:
            pairs.append((code.lower(), code))
    if not pairs:
        return None
    for pref in ("zh-hans", "zh-hant", "zh", "en"):
        for lower, raw in pairs:
            if lower == pref or lower.startswith(f"{pref}-"):
                return raw
    return pairs[0][1]


_INVIDIOUS_CAPTION_HTTP_TIMEOUT_S = 12.0


def _fetch_youtube_subtitles_via_invidious(url: str) -> dict[str, Any] | None:
    """Fetch captions through public Invidious mirrors when yt-dlp cannot reach YouTube."""

    from idea_engine_api import _iter_invidious_bases

    vid = _youtube_video_id_from_url(url)
    if not vid:
        return None
    headers = {"User-Agent": "idea-research/1.0 (youtube-captions)"}
    for base in _iter_invidious_bases():
        root = base.rstrip("/")
        list_url = f"{root}/api/v1/captions/{vid}"
        try:
            req = urllib.request.Request(list_url, headers=headers)
            with _urllib_urlopen(req, timeout=_INVIDIOUS_CAPTION_HTTP_TIMEOUT_S) as resp:
                listing = json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            _LOG.debug("invidious caption list failed via %s: %s", root, exc)
            continue
        captions = listing.get("captions") if isinstance(listing, dict) else None
        if not isinstance(captions, list) or not captions:
            continue
        lang = _pick_invidious_caption_lang(
            [c for c in captions if isinstance(c, dict)]
        )
        if not lang:
            continue
        vtt_url = f"{root}/api/v1/captions/{vid}?lang={urllib.parse.quote(lang)}"
        try:
            vtt_req = urllib.request.Request(vtt_url, headers=headers)
            with _urllib_urlopen(vtt_req, timeout=_INVIDIOUS_CAPTION_HTTP_TIMEOUT_S) as vtt_resp:
                vtt_text = vtt_resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _LOG.debug("invidious caption vtt failed via %s: %s", root, exc)
            continue
        payload = _parse_vtt_transcript(vtt_text)
        if not str(payload.get("text") or "").strip():
            continue
        payload["backend"] = "subtitle"
        payload["language"] = _guess_vtt_language(f"captions.{lang}.vtt")
        payload["source"] = "invidious"
        return payload
    return None


def _fetch_youtube_subtitles_default(work_dir: Path, url: str) -> dict[str, Any] | None:
    if not url:
        return None
    ytdlp_runner = _resolve_ytdlp_runner()
    if ytdlp_runner is not None:
        files: list[Path] = []
        for request_args in _yt_dlp_subtitle_client_attempts(url):
            for sub_langs in _YOUTUBE_SUBTITLE_LANG_PREFS:
                files = _download_youtube_subtitles(
                    work_dir,
                    url,
                    ytdlp_runner=ytdlp_runner,
                    sub_langs=sub_langs,
                    request_args=request_args,
                )
                if files:
                    break
            if files:
                break
        if files:
            preferred = _select_youtube_subtitle_file(files)
            payload = _parse_vtt_transcript(
                preferred.read_text(encoding="utf-8", errors="replace")
            )
            if payload["text"]:
                payload["backend"] = "subtitle"
                payload["language"] = _guess_vtt_language(preferred.name)
                payload["source_file"] = preferred.name
                payload["source"] = "ytdlp"
                return payload
    return _fetch_youtube_subtitles_via_invidious(url)


def _parse_vtt_transcript(text: str) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    cue_lines: list[str] = []
    start = 0.0
    end = 0.0
    in_cue = False
    for raw_line in text.splitlines():
        line = raw_line.strip("\ufeff").strip()
        if not line:
            if in_cue and cue_lines:
                cue_text = _clean_vtt_text(" ".join(cue_lines))
                if cue_text:
                    segments.append({"start": start, "end": end, "text": cue_text})
            cue_lines = []
            in_cue = False
            continue
        if "-->" in line:
            start_s, end_s = [part.strip() for part in line.split("-->", 1)]
            start = _parse_vtt_time(start_s)
            end = _parse_vtt_time(end_s.split(" ", 1)[0].strip())
            cue_lines = []
            in_cue = True
            continue
        if line.startswith("WEBVTT") or line.isdigit():
            continue
        if in_cue:
            cue_lines.append(line)
    if in_cue and cue_lines:
        cue_text = _clean_vtt_text(" ".join(cue_lines))
        if cue_text:
            segments.append({"start": start, "end": end, "text": cue_text})
    deduped: list[dict[str, Any]] = []
    prev_text = ""
    for seg in segments:
        if seg["text"] == prev_text:
            continue
        deduped.append(seg)
        prev_text = seg["text"]
    return {"text": " ".join(seg["text"] for seg in deduped).strip(), "segments": deduped}


def _parse_vtt_time(value: str) -> float:
    nums = [float(part) for part in re.split(r"[:.]", value) if part != ""]
    if len(nums) == 4:
        hh, mm, ss, ms = nums
        return hh * 3600 + mm * 60 + ss + ms / 1000.0
    if len(nums) == 3:
        mm, ss, ms = nums
        return mm * 60 + ss + ms / 1000.0
    return 0.0


def _clean_vtt_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_frames_default(
    video: Path, frames_dir: Path, strategy: str, max_frames: int
) -> list[Path]:  # pragma: no cover — needs ffmpeg
    if shutil.which("ffmpeg") is None:
        err = VendorError("ffmpeg 未安装；请安装 FFmpeg")
        err.error_kind = "dependency"
        raise err
    frames_dir.mkdir(parents=True, exist_ok=True)
    cmd: list[str]
    if strategy == "keyframe":
        cmd = [
            "ffmpeg",
            "-skip_frame",
            "nokey",
            "-i",
            str(video),
            "-vf",
            "select=eq(pict_type\\,I)",
            "-vsync",
            "vfr",
            str(frames_dir / "k_%03d.jpg"),
        ]
    elif strategy == "fixed_1.5s":
        cmd = [
            "ffmpeg",
            "-i",
            str(video),
            "-vf",
            "fps=1/1.5",
            str(frames_dir / "t_%04d.jpg"),
        ]
    else:  # hybrid (default)
        cmd = [
            "ffmpeg",
            "-i",
            str(video),
            "-vf",
            "select='eq(pict_type\\,I)+gt(t\\,prev_pts*1.5)'",
            "-vsync",
            "vfr",
            str(frames_dir / "h_%04d.jpg"),
        ]
    proc = subprocess.run(  # noqa: S603
        cmd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if proc.returncode != 0:
        err = VendorError(f"ffmpeg frame extract failed: {proc.stderr[-200:]}")
        err.error_kind = "format"
        raise err
    paths = sorted(frames_dir.glob("*.jpg"))
    if len(paths) > max_frames:
        step = max(1, len(paths) // max_frames)
        paths = paths[::step][:max_frames]
    return paths


async def structure_analyze(ctx: IdeaPipelineContext) -> dict[str, Any]:
    """Step 5 — fuse frames + metadata via Qwen-Max."""

    frames_descriptions = [{"frame": f.get("frame"), "desc": f.get("desc")} for f in ctx.frames]
    user = PROMPTS["STRUCTURE_PROMPT"].format(
        title=ctx.metadata.get("title") or "",
        author=ctx.metadata.get("author") or "",
        duration=ctx.metadata.get("duration_seconds") or 0,
        platform=ctx.metadata.get("platform") or "",
        frames_descriptions_json=json.dumps(frames_descriptions, ensure_ascii=False),
    )
    persona = PERSONAS_BY_NAME.get(ctx.persona_name) if ctx.persona_name else None
    chat = await ctx.dashscope.chat_completion(
        system=(persona.system_prompt if persona else ""),
        user=user,
        model="qwen-max",
        response_json=True,
        expected_keys=["hook", "body", "cta", "keywords"],
    )
    structure = chat.parsed_json or {}
    ctx.structure = structure
    ctx.write_json("structure.json", structure)
    ctx.cost["structure_llm"] = ctx.cost.get("structure_llm", 0.0) + 0.27
    await ctx.update(progress_pct=75, current_step="structure_analyze")
    return structure


async def comment_summary(
    ctx: IdeaPipelineContext, comments: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """Step 7 — analyse top 100 comments. Failure ⇒ skip non-fatal."""

    if not ctx.input.get("enable_comments", True):
        return None
    comments = comments or []
    if not comments:
        if str(ctx.source_info.get("platform") or "").lower() == "youtube":
            count = int(ctx.source_info.get("comment_count") or 0)
            ctx.comments_summary = {
                "message": (
                    "未抓取到 YouTube 评论。请在设置中配置 youtube_api_key 与 http_proxy 后重试。"
                    if count > 0
                    else "该 YouTube 视频当前没有可用评论，或平台未返回评论数据。"
                ),
            }
            ctx.write_json("comments_summary.json", ctx.comments_summary)
            await ctx.update(progress_pct=85, current_step="comment_summary(unavailable)")
            return ctx.comments_summary
        return None
    try:
        chat = await ctx.dashscope.chat_completion(
            system="",
            user=PROMPTS["COMMENT_SUMMARY_PROMPT"].format(
                comments_json=json.dumps(comments[:100], ensure_ascii=False)
            ),
            model="qwen-plus",
            response_json=True,
            expected_keys=["top_emotions"],
        )
    except VendorError as exc:
        _LOG.warning("comment_summary failed (degrading): %s", exc)
        await ctx.update(progress_pct=85, current_step="comment_summary(skipped)")
        ctx.write_json(
            "comments_summary.json",
            {"error_kind": exc.error_kind, "message": str(exc)},
        )
        return None
    ctx.comments_summary = chat.parsed_json or {}
    ctx.write_json("comments_summary.json", ctx.comments_summary)
    ctx.cost["comments_llm"] = ctx.cost.get("comments_llm", 0.0) + 0.003
    await ctx.update(progress_pct=85, current_step="comment_summary")
    return ctx.comments_summary


async def finalize(ctx: IdeaPipelineContext) -> dict[str, Any]:
    """Step 8 — aggregate + persona takeaways + MDRM dual-track write."""

    persona = ctx.persona_name or "通用"
    breakdown = {
        "task_id": ctx.task_id,
        "metadata": ctx.metadata,
        "source_info": ctx.source_info,
        "transcript": ctx.transcript,
        "frames": ctx.frames,
        "structure": ctx.structure,
        "comments_summary": ctx.comments_summary,
        "persona": persona,
        "cost_cny": round(sum(ctx.cost.values()), 4),
        "cost_breakdown": ctx.cost,
    }
    if ctx.structure:
        try:
            takeaways_chat = await ctx.dashscope.chat_completion(
                system="",
                user=PROMPTS["PERSONA_TAKEAWAYS_PROMPT"].format(
                    persona=persona,
                    breakdown_json=json.dumps(breakdown, ensure_ascii=False),
                ),
                model="qwen-plus",
                response_json=True,
                expected_keys=["persona_takeaways"],
            )
            ctx.persona_takeaways = list(
                (takeaways_chat.parsed_json or {}).get("persona_takeaways") or []
            )
            breakdown["persona_takeaways"] = ctx.persona_takeaways
            ctx.cost["persona_llm"] = ctx.cost.get("persona_llm", 0.0) + 0.002
        except VendorError as exc:
            _LOG.warning("persona takeaways degraded: %s", exc)
            breakdown["persona_takeaways_error"] = {
                "error_kind": exc.error_kind,
                "message": str(exc),
            }

    breakdown["cost_cny"] = round(sum(ctx.cost.values()), 4)
    breakdown["cost_breakdown"] = ctx.cost
    ctx.breakdown = breakdown
    ctx.write_json("breakdown.json", breakdown)
    (ctx.work_dir / "report.md").write_text(_render_report_md(breakdown), encoding="utf-8")

    write_result: dict[str, str] = {"vector": "skipped", "memory": "skipped"}
    if ctx.input.get("write_to_mdrm", True) and ctx.structure:
        hook = ctx.structure.get("hook") or {}
        record = HookRecord(
            id=str(uuid.uuid4()),
            hook_type=str(hook.get("type") or ""),
            hook_text=str(hook.get("text") or ""),
            persona=ctx.persona_name,
            platform=str(ctx.metadata.get("platform") or "other"),
            score=float(ctx.structure.get("estimated_quality") or 0.0),
            brand_keywords=list(ctx.input.get("brand_keywords") or []),
            source_task_id=ctx.task_id,
        )
        try:
            write_result = await ctx.mdrm.write_hook(record)
        except Exception as exc:
            _LOG.warning("MDRM write_hook degraded: %s", exc)
            write_result = {"vector": "error", "memory": "error", "reason": str(exc)}
        with contextlib.suppress(Exception):
            await ctx.tm.insert_hook_library(
                {
                    "id": record.id,
                    "hook_type": record.hook_type,
                    "hook_text": record.hook_text,
                    "persona": record.persona,
                    "platform": record.platform,
                    "score": record.score,
                    "brand_keywords": record.brand_keywords,
                    "source_task_id": record.source_task_id,
                },
                write_result=write_result,
            )
    ctx.write_json("mdrm_writes.json", write_result)

    await ctx.update(
        progress_pct=100,
        current_step="finalize",
        status="done",
        finished_at=_now(),
        output_json=json.dumps(breakdown, ensure_ascii=False),
        cost_cny=breakdown["cost_cny"],
        mdrm_writes_json=json.dumps(write_result, ensure_ascii=False),
        handoff_target=ctx.handoff_target,
    )
    return breakdown


def _render_report_md(breakdown: dict[str, Any]) -> str:
    md = breakdown.get("metadata") or {}
    structure = breakdown.get("structure") or {}
    hook = structure.get("hook") or {}
    body = structure.get("body") or []
    cta = structure.get("cta") or {}
    keywords = structure.get("keywords") or []
    takeaways = breakdown.get("persona_takeaways") or []
    lines: list[str] = [
        f"# 拆解报告 — {md.get('title') or md.get('url') or breakdown.get('task_id')}",
        "",
        f"- 平台：{md.get('platform') or '?'}",
        f"- 作者：{md.get('author') or '?'}",
        f"- 时长：{md.get('duration_seconds') or 0}s",
        f"- 总成本：≈ {breakdown.get('cost_cny', 0)} CNY",
        "",
        "## 钩子",
        f"**类型**：{hook.get('type') or '?'}",
        "",
        f"> {hook.get('text') or ''}",
        "",
        "## 主体段落",
    ]
    for seg in body:
        lines.append(f"- {seg.get('topic') or ''}（{seg.get('time_range') or []}）")
        if seg.get("key_quote"):
            lines.append(f"  - 金句：{seg['key_quote']}")
    lines.extend(
        [
            "",
            "## 行动召唤",
            cta.get("text") or "",
            "",
            "## 关键词",
            ", ".join(str(kw.get("word")) for kw in keywords if isinstance(kw, dict)) or "—",
            "",
            "## Persona Takeaways",
        ]
    )
    for t in takeaways:
        lines.append(f"- {t}")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Mode runners                                                                 #
# --------------------------------------------------------------------------- #


async def run_breakdown_url(
    ctx: IdeaPipelineContext,
    *,
    comments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Drive all 8 steps; failures bubble up as ``VendorError``."""

    async def _skip_youtube_download(*, detail: str) -> None:
        ctx.metadata["download_skipped"] = True
        ctx.metadata["download_error"] = detail[-500:]
        ctx.write_json("metadata.json", ctx.metadata)
        await ctx.update(progress_pct=30, current_step="download_media(skipped)")

    try:
        await setup_environment(ctx)
        resolved = await _resolve_source_with_timeout(ctx)
        from idea_engine_api import resolve_http_proxy

        platform = str(ctx.source_info.get("platform") or "").lower()
        video: Path | None = None
        audio: Path | None = None
        has_proxy = bool(resolve_http_proxy())
        if platform == "youtube" and not has_proxy:
            if ctx.metadata.get("youtube_network_degraded"):
                await _skip_youtube_download(
                    detail=(
                        "YouTube oEmbed 不可达且未配置 http_proxy，跳过视频下载；"
                        "将尝试字幕/元数据拆解"
                    ),
                )
            else:
                await _skip_youtube_download(
                    detail=(
                        "未配置 http_proxy（或系统代理），跳过 YouTube 视频下载；"
                        "将尝试字幕/元数据拆解"
                    ),
                )
        else:
            try:
                artefacts = await download_media(ctx)
                video = artefacts["video"]
                audio = artefacts["audio"]
            except VendorError as exc:
                if platform == "youtube" and exc.error_kind == "network":
                    await _skip_youtube_download(detail=str(exc))
                else:
                    raise
        await transcribe_audio(ctx, audio=audio)
        if video is not None and video.exists():
            await visual_keyframes(
                ctx,
                video=video,
                strategy=str(ctx.input.get("frame_strategy", "hybrid")),
                max_frames=_bounded_int(
                    ctx.input.get("max_frames"),
                    default=DEFAULT_MAX_FRAMES,
                    min_value=4,
                    max_value=MAX_FRAME_LIMIT,
                ),
            )
        else:
            cover_url = str(ctx.source_info.get("cover_url") or "").strip()
            if cover_url:
                await visual_keyframes_from_cover(ctx, cover_url=cover_url)
            else:
                ctx.frames = []
                ctx.write_json("frames.json", [])
                await ctx.update(progress_pct=60, current_step="visual_keyframes(skipped)")
        await structure_analyze(ctx)
        await comment_summary(
            ctx,
            comments=list(resolved.comments) if comments is None else comments,
        )
        return await finalize(ctx)
    except VendorError as exc:
        await _record_failure(ctx, exc)
        raise


async def run_radar_pull(ctx: IdeaPipelineContext) -> dict[str, Any]:
    """Mode 1 — fan out to collectors and persist ranked items."""

    await ctx.update(status="running", started_at=_now(), progress_pct=10)
    try:
        result = await _fetch_radar_with_timeout(ctx)
    except VendorError as exc:
        await _record_failure(ctx, exc)
        raise
    items: list[TrendItem] = list(result.get("items") or [])
    errors: list[dict[str, Any]] = list(result.get("errors") or [])
    keywords = list(ctx.input.get("keywords") or [])
    if errors:
        failed_platforms = [
            str(e.get("platform"))
            for e in errors
            if e.get("platform")
            and e.get("error_kind") in ("network", "timeout", "unknown")
        ]
        if failed_platforms and not any(i.platform in failed_platforms for i in items):
            cached_rows = await ctx.tm.list_trend_items(
                platforms=failed_platforms,
                limit=int(ctx.input.get("limit", 20)),
            )
            if cached_rows:
                stale_items = [_trend_item_from_db_row(row) for row in cached_rows]
                stale_items = filter_items_by_keywords(stale_items, keywords)
                if stale_items:
                    items.extend(stale_items)
                    errors.append(
                        {
                            "platform": ",".join(dict.fromkeys(failed_platforms)),
                            "error_kind": "cache",
                            "message": (
                                f"实时拉取失败，已展示 {len(stale_items)} 条历史缓存；"
                                "如需 YouTube 实时数据，请在 Settings 填写 http_proxy"
                            ),
                        }
                    )
    for item in items:
        await ctx.tm.upsert_trend_item(
            {
                "id": item.id,
                "platform": item.platform,
                "external_id": item.external_id,
                "external_url": item.external_url,
                "title": item.title,
                "author": item.author,
                "author_url": item.author_url,
                "cover_url": item.cover_url,
                "duration_seconds": item.duration_seconds,
                "description": item.description,
                "like_count": item.like_count,
                "comment_count": item.comment_count,
                "share_count": item.share_count,
                "view_count": item.view_count,
                "publish_at": item.publish_at,
                "fetched_at": item.fetched_at,
                "engine_used": item.engine_used,
                "collector_name": item.collector_name,
                "raw_payload": json.loads(item.raw_payload_json or "{}"),
                "score": item.score,
                "keywords_matched": item.keywords_matched,
                "hook_type_guess": item.hook_type_guess,
                "data_quality": item.data_quality,
                "mdrm_hits": item.mdrm_hits,
            }
        )
    out = {
        "items": [item.id for item in items],
        "errors": errors,
        "choices": result.get("choices") or [],
        "fetched_at": result.get("fetched_at"),
        "cost_cny": 0.0,
    }
    await ctx.update(
        status="done",
        progress_pct=100,
        finished_at=_now(),
        output_json=json.dumps(out, ensure_ascii=False),
        cost_cny=out["cost_cny"],
    )
    return out


async def run_compare_accounts(ctx: IdeaPipelineContext) -> dict[str, Any]:
    """Mode 3 — pull each account's recent videos + LLM cross-analysis."""

    try:
        urls = list(ctx.input.get("account_urls") or [])
        if not urls:
            err = VendorError("compare_accounts 缺少 account_urls")
            err.error_kind = "format"
            raise err
        max_per = int(ctx.input.get("max_videos_per_account", 20))
        await ctx.update(status="running", started_at=_now(), progress_pct=15)

        async def _pull_user(url: str) -> dict[str, Any]:
            try:
                from idea_engine_api import _platform_from_url

                platform = _platform_from_url(url) or "other"
                resolved = ctx.registry.resolve_collector(platform)
                if (
                    platform in ("douyin", "xhs", "ks", "bilibili", "weibo")
                    and getattr(resolved, "engine", "a") == "b"
                ):
                    collector = ctx.registry._engine_b_for(platform)
                else:
                    collector = ctx.registry._engine_a_for(platform)
                creator: dict[str, Any] | None = None
                videos: list[TrendItem] = []
                fetch_creator = getattr(collector, "fetch_creator", None)
                if callable(fetch_creator):
                    payload = await asyncio.wait_for(
                        fetch_creator(url, max_per),
                        timeout=COMPARE_ACCOUNT_PULL_TIMEOUT_S,
                    )
                    if isinstance(payload, dict):
                        maybe_creator = payload.get("creator")
                        if isinstance(maybe_creator, dict):
                            creator = maybe_creator
                        raw_videos = payload.get("videos") or []
                        videos = [v for v in raw_videos if isinstance(v, TrendItem)]
                elif hasattr(collector, "fetch_user"):
                    videos = await asyncio.wait_for(
                        collector.fetch_user(url, max_per),
                        timeout=COMPARE_ACCOUNT_PULL_TIMEOUT_S,
                    )
                account = {
                    "url": url,
                    "platform": platform,
                    "videos": [
                        {
                            "external_id": v.external_id,
                            "title": v.title,
                            "like_count": v.like_count,
                            "view_count": v.view_count,
                            "publish_at": v.publish_at,
                        }
                        for v in videos
                    ],
                }
                if creator:
                    account["creator"] = {
                        "name": creator.get("name") or "",
                        "profile_url": creator.get("profile_url") or url,
                        "follower_count": creator.get("follower_count"),
                        "bio": creator.get("bio"),
                    }
                return account
            except asyncio.TimeoutError:
                return {
                    "url": url,
                    "error_kind": "timeout",
                    "message": f"账号拉取超时（>{COMPARE_ACCOUNT_PULL_TIMEOUT_S:.0f}s）",
                }
            except VendorError as exc:
                return {
                    "url": url,
                    "error_kind": exc.error_kind,
                    "message": str(exc),
                }

        accounts = await asyncio.gather(*(_pull_user(u) for u in urls))
        successful_accounts = [
            acc for acc in accounts if not acc.get("error_kind") and (acc.get("videos") or acc.get("creator"))
        ]
        if not successful_accounts:
            detail_lines = [
                f"{acc.get('url') or '?'}: {acc.get('message') or acc.get('error_kind') or '无视频'}"
                for acc in accounts
                if acc.get("message") or acc.get("error_kind") or not acc.get("videos")
            ]
            msg = (
                "所有对标账号均未能拉取到可用视频。"
                + (" " + "; ".join(detail_lines) if detail_lines else "")
                + " 请使用各平台账号主页链接（B站 space、YouTube @handle/channel、快手/抖音/小红书/微博主页），勿粘贴单条视频链接。"
            )
            err = VendorError(msg)
            err.error_kind = "format"
            raise err
        chat = await asyncio.wait_for(ctx.dashscope.chat_completion(
            system=(
                "你是新媒体对标分析师。基于多账号近期视频列表，输出严格 JSON："
                '{"common_traits": ["..."], "differentiators": [{"url": "...",'
                ' "edge": "..."}], "gaps": ["..."], "recommendations": ["..."]}'
            ),
            user=json.dumps(accounts, ensure_ascii=False),
            model="qwen-max",
            response_json=True,
            expected_keys=["common_traits"],
        ), timeout=COMPARE_ACCOUNT_ANALYZE_TIMEOUT_S)
        output = {
            "accounts": accounts,
            "analysis": chat.parsed_json or {},
            "cost_cny": estimate_cost(
                "compare_accounts",
                {"account_count": max(1, len(urls))},
            )["cost_cny"],
        }
        ctx.write_json("compare.json", output)
        await ctx.update(
            status="done",
            progress_pct=100,
            finished_at=_now(),
            output_json=json.dumps(output, ensure_ascii=False),
            cost_cny=output["cost_cny"],
        )
        return output
    except VendorError as exc:
        await _record_failure(ctx, exc)
        raise


async def run_script_remix(
    ctx: IdeaPipelineContext, *, source_item: TrendItem | None = None
) -> dict[str, Any]:
    """Mode 4 — generate ``num_variants`` scripts (with optional MDRM hints)."""

    await ctx.update(status="running", started_at=_now(), progress_pct=15)
    source_row: dict[str, Any] | None = None
    if source_item is None and ctx.input.get("trend_item_id"):
        source_row = await ctx.tm.get_trend_item(str(ctx.input["trend_item_id"]))
        if source_row:
            source_item = _trend_item_from_db_row(source_row)
    hook_text = str(ctx.input.get("hook_text") or "").strip()
    body_outline = str(ctx.input.get("body_outline") or "").strip()
    if source_item is not None:
        if not hook_text:
            hook_text = str(source_item.title or "").strip()
        if not body_outline:
            outline_parts: list[str] = []
            if source_item.title:
                outline_parts.append(f"标题：{source_item.title}")
            if source_item.description:
                outline_parts.append(f"简介：{source_item.description}")
            if source_item.hook_type_guess:
                outline_parts.append(f"钩子类型：{source_item.hook_type_guess}")
            body_outline = "\n".join(outline_parts)
    inspirations: list[dict[str, Any]] = []
    if ctx.input.get("use_mdrm_hints", True):
        try:
            hits = await ctx.mdrm.search_similar_hooks(
                hook_text or (source_item.title if source_item else ""),
                limit=3,
            )
            for rec, sim in hits or []:
                inspirations.append(
                    {
                        "hook_id": getattr(rec, "id", ""),
                        "hook_text": getattr(rec, "hook_text", ""),
                        "similarity": sim,
                    }
                )
        except Exception as exc:
            _LOG.warning("MDRM search_similar_hooks degraded: %s", exc)
    persona_name = str(ctx.input.get("my_persona") or ctx.persona_name or "通用")
    persona = PERSONAS_BY_NAME.get(persona_name)
    num_variants = max(1, min(5, int(ctx.input.get("num_variants", 3))))
    remix_common = {
        "my_persona": persona_name,
        "hook": hook_text,
        "body_outline": body_outline,
        "target_platform": str(ctx.input.get("target_platform") or "douyin"),
        "brand_keywords": ", ".join(ctx.input.get("my_brand_keywords") or []),
        "target_duration_seconds": int(ctx.input.get("target_duration_seconds", 60)),
        "mdrm_inspirations_json": json.dumps(inspirations, ensure_ascii=False),
    }
    variants: list[dict[str, Any]] = []
    prior_titles: list[str] = []
    system = persona.system_prompt if persona else ""
    try:
        for idx in range(1, num_variants + 1):
            pct = 15 + int(70 * (idx - 1) / max(num_variants, 1))
            await ctx.update(
                progress_pct=min(pct, 85),
                current_step=f"script_variant_{idx}",
            )
            user = PROMPTS["SCRIPT_REMIX_VARIANT_PROMPT"].format(
                variant_index=idx,
                num_variants=num_variants,
                avoid_titles_json=json.dumps(prior_titles, ensure_ascii=False),
                **remix_common,
            )
            chat = await ctx.dashscope.chat_completion(
                system=system,
                user=user,
                model="qwen-max",
                response_json=True,
                expected_keys=["title", "hook_line", "body_outline"],
                max_tokens=2500,
            )
            raw_one = chat.parsed_json if isinstance(chat.parsed_json, dict) else {}
            normalized = normalize_script_remix_variants([raw_one])
            variant = normalized[0] if normalized else raw_one
            if variant:
                variants.append(variant)
                title = str(variant.get("title") or "").strip()
                if title:
                    prior_titles.append(title)
    except VendorError as exc:
        await _record_failure(ctx, exc)
        raise
    variants = [v for v in variants if isinstance(v, dict)]
    source_meta: dict[str, Any] = {}
    if source_row:
        source_meta = {
            "trend_item_id": source_row.get("id") or ctx.input.get("trend_item_id"),
            "title": source_row.get("title") or "",
            "platform": source_row.get("platform") or "",
            "external_url": source_row.get("external_url") or "",
        }
    elif source_item is not None:
        source_meta = {
            "trend_item_id": ctx.input.get("trend_item_id") or source_item.id,
            "title": source_item.title,
            "platform": source_item.platform,
            "external_url": source_item.external_url,
        }
    output = {
        "variants": variants,
        "source": source_meta,
        "mdrm_inspirations": inspirations,
        "cost_cny": estimate_cost(
            "script_remix",
            {"num_variants": num_variants},
        )["cost_cny"],
    }
    ctx.write_json("script_remix.json", output)
    report_path = ctx.work_dir / "script_remix.md"
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        format_script_remix_markdown(output, task_id=ctx.task_id),
        encoding="utf-8",
    )
    await ctx.update(
        status="done",
        progress_pct=100,
        finished_at=_now(),
        output_json=json.dumps(output, ensure_ascii=False),
        cost_cny=output["cost_cny"],
    )
    return output


# --------------------------------------------------------------------------- #
# Failure recorder                                                             #
# --------------------------------------------------------------------------- #


async def _record_failure(ctx: IdeaPipelineContext, exc: VendorError) -> None:
    hint = hint_for(exc.error_kind or "unknown")
    try:
        await ctx.update(
            status="failed",
            finished_at=_now(),
            error_kind=exc.error_kind or "unknown",
            error_message=str(exc),
            error_hint_zh=hint["zh"],
            error_hint_en=hint["en"],
        )
    except Exception:  # never let the failure recorder mask the real error
        _LOG.exception("could not persist failure for task %s", ctx.task_id)


async def _fetch_radar_with_timeout(ctx: IdeaPipelineContext) -> dict[str, Any]:
    await ctx.update(current_step="fetch_for_radar")
    task = asyncio.create_task(
        ctx.registry.fetch_for_radar(
            list(ctx.input.get("platforms") or ["bilibili"]),
            list(ctx.input.get("keywords") or []),
            time_window=str(ctx.input.get("time_window", "24h")),
            limit=int(ctx.input.get("limit", 20)),
            engine_pref=str(ctx.input.get("engine", "auto")),
            mdrm_weighting=bool(ctx.input.get("mdrm_weighting", True)),
        )
    )
    try:
        done, _pending = await asyncio.wait({task}, timeout=RADAR_PULL_TIMEOUT_S)
        if done:
            return task.result()
        task.cancel()
        task.add_done_callback(_consume_task_exception)
        raise VendorTimeoutError(f"radar_pull backend timed out after {RADAR_PULL_TIMEOUT_S:.0f}s")
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
            task.add_done_callback(_consume_task_exception)
        raise


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    with contextlib.suppress(asyncio.CancelledError, Exception):
        task.result()


__all__ = [
    "IdeaPipelineContext",
    "comment_summary",
    "download_media",
    "finalize",
    "resolve_source",
    "run_breakdown_url",
    "run_compare_accounts",
    "run_radar_pull",
    "run_script_remix",
    "setup_environment",
    "structure_analyze",
    "visual_keyframes",
]

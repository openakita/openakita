"""8-step breakdown pipeline + 4 mode runners (§7).

The pipeline lives behind a small ``IdeaPipelineContext`` dataclass
(``ctx``) so tests can construct one with fake collectors / fake
DashScope client / fake MDRM adapter and exercise each step in
isolation.

Public API
----------
* ``IdeaPipelineContext`` — bag of plugin-side dependencies + per-task
  scratch state (``input``, ``metadata``, ``transcript``, ``frames``,
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
import json
import logging
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from idea_collectors import CollectorRegistry
from idea_dashscope_client import DashScopeClient, TranscriptResult
from idea_models import (
    PERSONAS_BY_NAME,
    PROMPTS,
    TrendItem,
    estimate_cost,
    hint_for,
)
from idea_research_inline.mdrm_adapter import HookRecord, MdrmAdapter
from idea_research_inline.parallel_executor import run_with_semaphore
from idea_research_inline.vendor_client import VendorError

_LOG = logging.getLogger("idea-research.pipeline")


# --------------------------------------------------------------------------- #
# Lifecycle helpers                                                            #
# --------------------------------------------------------------------------- #


def _now() -> int:
    return int(time.time())


# --------------------------------------------------------------------------- #
# IdeaPipelineContext                                                          #
# --------------------------------------------------------------------------- #


class _TaskManagerProtocol(Protocol):
    async def update_task_safe(self, task_id: str, updates: dict[str, Any]) -> dict[str, Any]: ...

    async def upsert_trend_item(self, item: dict[str, Any]) -> None: ...

    async def insert_hook_library(
        self, record: dict[str, Any], *, write_result: dict[str, str] | None = None
    ) -> str: ...


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
    transcript: TranscriptResult | None = None
    frames: list[dict[str, Any]] = field(default_factory=list)
    structure: dict[str, Any] = field(default_factory=dict)
    comments_summary: dict[str, Any] | None = None
    persona_takeaways: list[str] = field(default_factory=list)
    breakdown: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, float] = field(default_factory=dict)
    handoff_target: str | None = None

    # Hooks tests can patch to skip subprocess work.
    download_media_fn: Callable[[Path, str], dict[str, Path]] | None = None
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


async def resolve_source(ctx: IdeaPipelineContext) -> None:
    """Step 2 — turn the user URL into a TrendItem skeleton."""

    url = str(ctx.input.get("url") or "").strip()
    if not url:
        err = VendorError("breakdown_url 缺少 url 字段")
        err.error_kind = "format"
        raise err
    item = await ctx.registry.fetch_single_url(
        url, with_comments=bool(ctx.input.get("enable_comments", True))
    )
    if item is None:
        err = VendorError(f"无法解析 URL: {url}")
        err.error_kind = "format"
        raise err
    ctx.source_info = {
        "platform": item.platform,
        "external_id": item.external_id,
        "external_url": item.external_url,
        "title": item.title,
        "author": item.author,
        "duration_seconds": item.duration_seconds,
        "publish_at": item.publish_at,
    }
    ctx.metadata["platform"] = item.platform
    ctx.metadata["title"] = item.title
    ctx.metadata["author"] = item.author
    ctx.metadata["duration_seconds"] = item.duration_seconds
    ctx.write_json("metadata.json", ctx.metadata)
    ctx.write_json("source_info.json", ctx.source_info)
    await ctx.update(progress_pct=15, current_step="resolve_source")


async def download_media(ctx: IdeaPipelineContext) -> dict[str, Path]:
    """Step 3 — yt-dlp + ffmpeg into ``video.mp4`` + ``audio.wav``."""

    url = str(ctx.source_info.get("external_url") or ctx.input.get("url"))
    if ctx.download_media_fn is not None:
        artefacts = ctx.download_media_fn(ctx.work_dir, url)
    else:
        artefacts = _download_media_default(ctx.work_dir, url)
    await ctx.update(progress_pct=30, current_step="download_media")
    return artefacts


def _download_media_default(work_dir: Path, url: str) -> dict[str, Path]:
    if shutil.which("yt-dlp") is None:
        err = VendorError("yt-dlp 未安装；执行 `pip install yt-dlp`")
        err.error_kind = "dependency"
        raise err
    if shutil.which("ffmpeg") is None:
        err = VendorError("ffmpeg 未安装；请按平台安装 FFmpeg 套件")
        err.error_kind = "dependency"
        raise err
    video_path = work_dir / "video.mp4"
    audio_path = work_dir / "audio.wav"
    work_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(  # noqa: S603 — explicit binary path
        [
            "yt-dlp",
            "-f",
            "best[height<=720]",
            "-o",
            str(video_path),
            "--no-playlist",
            url,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0 or not video_path.exists():
        err = VendorError(f"yt-dlp failed: {proc.stderr[-200:] if proc.stderr else ''}")
        err.error_kind = "network" if "network" in (proc.stderr or "").lower() else "format"
        raise err
    proc2 = subprocess.run(  # noqa: S603
        [
            "ffmpeg",
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
        timeout=300,
    )
    if proc2.returncode != 0 or not audio_path.exists():
        err = VendorError(f"ffmpeg audio extract failed: {proc2.stderr[-200:]}")
        err.error_kind = "format"
        raise err
    return {"video": video_path, "audio": audio_path}


async def asr_transcribe(
    ctx: IdeaPipelineContext, *, audio: Path, backend: str = "auto"
) -> TranscriptResult | None:
    """Step 4 — speech-to-text. Failure → degrade to ``None`` transcript."""

    try:
        result = await ctx.dashscope.transcribe_audio(
            audio,
            backend=backend,  # type: ignore[arg-type]
        )
    except VendorError as exc:
        _LOG.warning("ASR failed (degrading): %s", exc)
        await ctx.update(progress_pct=45, current_step="asr_transcribe(skipped)")
        ctx.transcript = None
        ctx.write_json("transcript.json", {"error_kind": exc.error_kind, "message": str(exc)})
        return None
    ctx.transcript = result
    ctx.write_json(
        "transcript.json",
        {
            "backend": result.backend,
            "language": result.language,
            "text": result.text,
            "segments": [seg.__dict__ for seg in result.segments],
            "cost_cny": result.cost_cny,
        },
    )
    ctx.cost["asr"] = ctx.cost.get("asr", 0.0) + result.cost_cny
    await ctx.update(progress_pct=45, current_step="asr_transcribe")
    return result


async def visual_keyframes(
    ctx: IdeaPipelineContext,
    *,
    video: Path,
    strategy: str = "hybrid",
    max_frames: int = 80,
    concurrency: int = 4,
) -> list[dict[str, Any]]:
    """Step 5 — extract frames + describe each via Qwen-VL-Max."""

    extract = ctx.extract_frames_fn or _extract_frames_default
    try:
        frame_paths = extract(video, ctx.work_dir / "frames", strategy, max_frames)
    except VendorError:
        raise
    except Exception as exc:  # treat unknown subprocess crashes as dependency
        err = VendorError(f"frame extraction crashed: {exc}")
        err.error_kind = "dependency"
        raise err from exc

    async def describe(p: Path) -> dict[str, Any]:
        try:
            desc = await ctx.dashscope.describe_image(p)
        except VendorError as exc:
            _LOG.warning("VLM describe failed for %s (degrading): %s", p.name, exc)
            return {
                "frame": p.name,
                "desc": "",
                "error_kind": exc.error_kind,
                "message": str(exc),
            }
        return {
            "frame": p.name,
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
        cmd, check=False, capture_output=True, text=True, timeout=300
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
    """Step 6 — fuse transcript + frames + metadata via Qwen-Max."""

    transcript_segments = (
        [seg.__dict__ for seg in ctx.transcript.segments]
        if ctx.transcript and ctx.transcript.segments
        else []
    )
    frames_descriptions = [{"frame": f.get("frame"), "desc": f.get("desc")} for f in ctx.frames]
    user = PROMPTS["STRUCTURE_PROMPT"].format(
        title=ctx.metadata.get("title") or "",
        author=ctx.metadata.get("author") or "",
        duration=ctx.metadata.get("duration_seconds") or 0,
        platform=ctx.metadata.get("platform") or "",
        transcript_segments_json=json.dumps(transcript_segments, ensure_ascii=False),
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
        "transcript": (
            {
                "backend": ctx.transcript.backend,
                "text": ctx.transcript.text,
                "language": ctx.transcript.language,
            }
            if ctx.transcript
            else None
        ),
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

    try:
        await setup_environment(ctx)
        await resolve_source(ctx)
        artefacts = await download_media(ctx)
        await asr_transcribe(
            ctx,
            audio=artefacts["audio"],
            backend=str(ctx.input.get("asr_backend", "auto")),
        )
        await visual_keyframes(
            ctx,
            video=artefacts["video"],
            strategy=str(ctx.input.get("frame_strategy", "hybrid")),
        )
        await structure_analyze(ctx)
        await comment_summary(ctx, comments=comments)
        return await finalize(ctx)
    except VendorError as exc:
        await _record_failure(ctx, exc)
        raise


async def run_radar_pull(ctx: IdeaPipelineContext) -> dict[str, Any]:
    """Mode 1 — fan out to collectors and persist ranked items."""

    await ctx.update(status="running", started_at=_now(), progress_pct=10)
    try:
        result = await ctx.registry.fetch_for_radar(
            list(ctx.input.get("platforms") or ["bilibili"]),
            list(ctx.input.get("keywords") or []),
            time_window=str(ctx.input.get("time_window", "24h")),
            limit=int(ctx.input.get("limit", 20)),
            engine_pref=str(ctx.input.get("engine", "auto")),
            mdrm_weighting=bool(ctx.input.get("mdrm_weighting", True)),
        )
    except VendorError as exc:
        await _record_failure(ctx, exc)
        raise
    items: list[TrendItem] = list(result.get("items") or [])
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
        "errors": result.get("errors") or [],
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
                if hasattr(collector, "fetch_user"):
                    videos = await collector.fetch_user(url, max_per)
                else:
                    videos = []
                return {
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
            except VendorError as exc:
                return {
                    "url": url,
                    "error_kind": exc.error_kind,
                    "message": str(exc),
                }

        accounts = await asyncio.gather(*(_pull_user(u) for u in urls))
        chat = await ctx.dashscope.chat_completion(
            system=(
                "你是新媒体对标分析师。基于多账号近期视频列表，输出严格 JSON："
                '{"common_traits": ["..."], "differentiators": [{"url": "...",'
                ' "edge": "..."}], "gaps": ["..."], "recommendations": ["..."]}'
            ),
            user=json.dumps(accounts, ensure_ascii=False),
            model="qwen-max",
            response_json=True,
            expected_keys=["common_traits"],
        )
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
    if source_item is None and ctx.input.get("trend_item_id"):
        # Caller can supply a TrendItem directly; otherwise we build a
        # minimal one from the optional ``hook_text`` / ``body_outline``
        # fields the route layer may have hydrated.
        source_item = None
    hook_text = ctx.input.get("hook_text") or (source_item.title if source_item else "")
    body_outline = ctx.input.get("body_outline") or ""
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
    user = PROMPTS["SCRIPT_REMIX_PROMPT"].format(
        my_persona=persona_name,
        num_variants=int(ctx.input.get("num_variants", 3)),
        hook=hook_text,
        body_outline=body_outline,
        target_platform=str(ctx.input.get("target_platform") or "douyin"),
        brand_keywords=", ".join(ctx.input.get("my_brand_keywords") or []),
        target_duration_seconds=int(ctx.input.get("target_duration_seconds", 60)),
        mdrm_inspirations_json=json.dumps(inspirations, ensure_ascii=False),
    )
    try:
        chat = await ctx.dashscope.chat_completion(
            system=(persona.system_prompt if persona else ""),
            user=user,
            model="qwen-max",
            response_json=True,
            expected_keys=["variants"],
        )
    except VendorError as exc:
        await _record_failure(ctx, exc)
        raise
    variants = (chat.parsed_json or {}).get("variants") or []
    output = {
        "variants": variants,
        "mdrm_inspirations": inspirations,
        "cost_cny": estimate_cost(
            "script_remix",
            {"num_variants": int(ctx.input.get("num_variants", 3))},
        )["cost_cny"],
    }
    ctx.write_json("script_remix.json", output)
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


__all__ = [
    "IdeaPipelineContext",
    "asr_transcribe",
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

"""local-sd-flux — pure-logic core.

Pipeline (one prompt → N images):

1. ``plan_image`` validates the request, picks a preset workflow,
   applies overrides (prompt / negative / size / seed / steps / ...),
   and freezes everything into an :class:`ImagePlan`.
2. ``run_image`` (async) submits the workflow to ComfyUI, polls
   ``/history`` until completion (or ``timeout_sec``), downloads each
   output image via ``/view``, writes them to ``output_dir``, and
   returns an :class:`ImageResult`.
3. ``to_verification`` produces a D2.10 envelope (zero images, all
   thumbnails 0 bytes, prompt empty, custom workflow without
   ``SaveImage``, ...).

Heavy / non-deterministic deps (``httpx`` / a real ComfyUI server) are
behind dependency-injection points so tests never spawn a network call.
``rank_image_providers`` lives here because picking the right ComfyUI
host is part of "planning an image", not "talking HTTP".
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from openakita_plugin_sdk.contrib import (
    KIND_NUMBER,
    KIND_OTHER,
    LowConfidenceField,
    ProviderScore,
    Verification,
    score_providers,
)

from comfy_client import ComfyClient, ComfyOutputImage, ComfyPromptResult
from workflow_presets import (
    PRESET_IDS,
    apply_overrides,
    build_preset_workflow,
    describe_preset,
    list_presets,
    preset_default_overrides,
)

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_OUTPUT_FORMAT",
    "DEFAULT_POLL_INTERVAL_SEC",
    "DEFAULT_RUN_TIMEOUT_SEC",
    "ImagePlan",
    "ImageResult",
    "RankedProvider",
    "extract_vram_signal",
    "plan_image",
    "rank_image_providers",
    "run_image",
    "to_verification",
]


DEFAULT_BATCH_SIZE = 1
DEFAULT_POLL_INTERVAL_SEC = 1.0
DEFAULT_RUN_TIMEOUT_SEC = 300.0
DEFAULT_OUTPUT_FORMAT = "png"


# ── Models ────────────────────────────────────────────────────────────


@dataclass
class ImagePlan:
    """Frozen description of one image-generation job."""

    preset_id: str
    workflow: dict[str, Any]
    overrides: dict[str, Any]
    output_dir: str
    output_format: str
    poll_interval_sec: float
    timeout_sec: float
    is_custom_workflow: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "overrides": dict(self.overrides),
            "output_dir": self.output_dir,
            "output_format": self.output_format,
            "poll_interval_sec": self.poll_interval_sec,
            "timeout_sec": self.timeout_sec,
            "is_custom_workflow": self.is_custom_workflow,
            # workflow itself is omitted — it can be huge; surface it
            # only when the host explicitly logs at debug level.
        }


@dataclass
class ImageResult:
    """What ``run_image`` produced."""

    plan: ImagePlan
    prompt_id: str
    image_paths: list[str]
    elapsed_sec: float
    polls: int
    bytes_total: int
    raw_history: dict[str, Any] = field(default_factory=dict)

    @property
    def image_count(self) -> int:
        return len(self.image_paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "prompt_id": self.prompt_id,
            "image_paths": list(self.image_paths),
            "image_count": self.image_count,
            "elapsed_sec": self.elapsed_sec,
            "polls": self.polls,
            "bytes_total": self.bytes_total,
        }


@dataclass(frozen=True)
class RankedProvider:
    """One ranked ComfyUI candidate."""

    score: ProviderScore
    base_url: str
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "base_url": self.base_url,
            "total": round(self.score.total, 4),
            "dimensions": {k: round(v, 4) for k, v in self.score.dimensions.items()},
            "notes": list(self.score.notes),
        }


# ── plan_image ────────────────────────────────────────────────────────


def plan_image(
    *,
    prompt: str,
    output_dir: str,
    preset_id: str = "sdxl_basic",
    overrides: dict[str, Any] | None = None,
    custom_workflow: dict[str, Any] | None = None,
    output_format: str = DEFAULT_OUTPUT_FORMAT,
    poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
    timeout_sec: float = DEFAULT_RUN_TIMEOUT_SEC,
) -> ImagePlan:
    """Validate inputs and build a frozen :class:`ImagePlan`.

    Args:
        prompt: Required positive prompt — used to fill the
            ``{prompt}`` placeholder in the chosen preset.  Custom
            workflows are responsible for their own placeholders;
            we still require ``prompt`` non-empty so brain tools have
            something searchable in the task list.
        preset_id: One of :data:`workflow_presets.PRESET_IDS`.  Ignored
            when ``custom_workflow`` is provided.
        overrides: User-provided overrides merged on top of the
            preset defaults (see :func:`workflow_presets.preset_default_overrides`).
        custom_workflow: Skip preset lookup and use this graph verbatim.
        timeout_sec: Wall-clock budget for ``run_image`` (poll loop +
            downloads), clamped to ``[10, 3600]``.
    """
    if not prompt or not str(prompt).strip():
        raise ValueError("prompt is required and must not be empty")
    if not output_dir or not str(output_dir).strip():
        raise ValueError("output_dir is required and must not be empty")
    if output_format.lower() not in ("png", "jpg", "jpeg", "webp"):
        raise ValueError(
            f"unsupported output_format {output_format!r}; "
            "supported: png / jpg / jpeg / webp",
        )
    if not (0.1 <= poll_interval_sec <= 30.0):
        raise ValueError(
            f"poll_interval_sec must be in [0.1, 30.0], got {poll_interval_sec!r}",
        )
    if not (10.0 <= timeout_sec <= 3600.0):
        raise ValueError(
            f"timeout_sec must be in [10, 3600], got {timeout_sec!r}",
        )

    is_custom = custom_workflow is not None
    overrides = dict(overrides or {})
    overrides.setdefault("prompt", prompt)

    if is_custom:
        if not isinstance(custom_workflow, dict) or not custom_workflow:
            raise ValueError("custom_workflow must be a non-empty dict")
        workflow = dict(custom_workflow)
    else:
        if preset_id not in PRESET_IDS:
            raise ValueError(
                f"unknown preset_id {preset_id!r}; known: {list(PRESET_IDS)}",
            )
        merged = preset_default_overrides(preset_id)
        merged.update(overrides)
        workflow = build_preset_workflow(preset_id)
        apply_overrides(workflow, merged)
        overrides = merged

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    return ImagePlan(
        preset_id="custom" if is_custom else preset_id,
        workflow=workflow,
        overrides=overrides,
        output_dir=str(output_dir),
        output_format=output_format.lower(),
        poll_interval_sec=float(poll_interval_sec),
        timeout_sec=float(timeout_sec),
        is_custom_workflow=is_custom,
    )


# ── run_image (async) ─────────────────────────────────────────────────


async def run_image(
    plan: ImagePlan,
    *,
    client: ComfyClient,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> ImageResult:
    """Submit ``plan.workflow`` to ComfyUI, poll until done, download images.

    Args:
        plan: Output of :func:`plan_image`.
        client: A :class:`ComfyClient` instance (real or fake).
        sleep: Async sleep coroutine; defaults to :func:`asyncio.sleep`.
            Tests inject a no-op sleep so the poll loop is instant.
        on_progress: ``(stage, done, total) -> None`` reporter.  Stages:
            ``"submit"`` (1/1), ``"poll"`` (n/-1 with -1 meaning unknown),
            ``"download"`` (i/total).
    """
    sleep = sleep or asyncio.sleep
    started = time.monotonic()

    # 1. submit
    prompt_id = await client.submit_prompt(plan.workflow)
    if on_progress:
        on_progress("submit", 1, 1)

    # 2. poll until ``outputs`` appears or budget expires.
    deadline = started + plan.timeout_sec
    polls = 0
    history: dict[str, Any] = {}
    while True:
        history = await client.get_history(prompt_id)
        polls += 1
        if on_progress:
            on_progress("poll", polls, -1)
        if client.is_history_complete(history):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"ComfyUI did not finish prompt {prompt_id} within "
                f"{plan.timeout_sec:.1f}s (poll #{polls})",
            )
        await sleep(plan.poll_interval_sec)

    # 3. parse + download
    parsed: ComfyPromptResult = client.parse_history_outputs(prompt_id, history)
    image_paths: list[str] = []
    bytes_total = 0
    for i, img in enumerate(parsed.images):
        data = await client.download_image_bytes(img)
        out_path = _resolve_image_path(plan, prompt_id, i, img, plan.output_format)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        image_paths.append(str(out_path))
        bytes_total += len(data)
        if on_progress:
            on_progress("download", i + 1, len(parsed.images))

    elapsed = time.monotonic() - started
    return ImageResult(
        plan=plan, prompt_id=prompt_id,
        image_paths=image_paths, elapsed_sec=elapsed,
        polls=polls, bytes_total=bytes_total, raw_history=history,
    )


def _resolve_image_path(
    plan: ImagePlan, prompt_id: str, index: int,
    image: ComfyOutputImage, fmt: str,
) -> Path:
    base = Path(plan.output_dir)
    stem = Path(image.filename).stem or f"img_{index:03d}"
    return base / f"{prompt_id}_{stem}.{fmt}"


# ── provider ranker ───────────────────────────────────────────────────


def extract_vram_signal(stats: dict[str, Any]) -> float:
    """Pull a normalized 0..1 VRAM-availability signal from ``/system_stats``.

    ComfyUI's ``/system_stats`` returns ``{"devices": [{"vram_total":
    int, "vram_free": int, ...}, ...]}`` (ints in bytes).  We pick the
    first GPU device, divide ``vram_free`` by ``vram_total``, and clamp
    to ``[0, 1]``.  Returns ``0.0`` when the payload is missing /
    malformed (callers can treat that as "treat this candidate as
    untrusted").
    """
    devices = (stats or {}).get("devices") or []
    if not devices:
        return 0.0
    dev = devices[0]
    try:
        total = float(dev.get("vram_total", 0))
        free = float(dev.get("vram_free", 0))
    except (TypeError, ValueError):
        return 0.0
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, free / total))


def rank_image_providers(
    candidates: list[dict[str, Any]],
) -> list[RankedProvider]:
    """Rank ComfyUI candidates with the SDK's :func:`score_providers`.

    Each candidate dict shape::

        {
          "id": "local_gpu",          # required
          "label": "本地 GPU",         # human-readable
          "base_url": "http://...",   # passed through to RankedProvider
          # standard provider_score dimensions, all 0..1:
          "quality": 0.95,
          "speed": 0.7,
          "cost": 0.95,
          "reliability": 0.7,
          "control": 0.95,
          "latency": 0.95,
          "compatibility": 0.95,
        }

    The wrapper just glues the SDK output back to ``base_url`` / ``label``
    so the plugin's brain tool can return a ready-to-render answer.
    """
    scored = score_providers(candidates)
    by_id = {c.get("id"): c for c in candidates or []}
    out: list[RankedProvider] = []
    for s in scored:
        meta = by_id.get(s.provider_id, {})
        out.append(RankedProvider(
            score=s,
            base_url=str(meta.get("base_url", "")),
            label=str(meta.get("label", s.provider_id)),
        ))
    return out


# ── verification (D2.10) ──────────────────────────────────────────────


def to_verification(result: ImageResult) -> Verification:
    """D2.10 envelope.

    Yellow flags:
      * 0 images returned (workflow ran but produced nothing — the
        graph probably lacks a SaveImage / its output directory was
        write-locked),
      * total bytes 0 (downloads succeeded but every image is empty),
      * custom workflow without an obvious SaveImage-ish node,
      * elapsed > 80% of timeout (queue / GPU thrashing — next call may
        actually time out).
    """
    fields: list[LowConfidenceField] = []

    if result.image_count == 0:
        fields.append(LowConfidenceField(
            path="$.image_count",
            value=0,
            kind=KIND_NUMBER,
            reason=(
                "ComfyUI returned 0 images — the workflow probably "
                "lacks a SaveImage node or the output dir was unwritable; "
                "check the ComfyUI server logs"
            ),
        ))

    if result.image_count > 0 and result.bytes_total == 0:
        fields.append(LowConfidenceField(
            path="$.bytes_total",
            value=0,
            kind=KIND_NUMBER,
            reason=(
                "all downloaded images are 0 bytes — /view probably "
                "served empty bodies; the disk may be full or the "
                "ComfyUI worker crashed mid-write"
            ),
        ))

    if result.plan.is_custom_workflow:
        wf = result.plan.workflow
        has_save = any(
            (n or {}).get("class_type", "").startswith("SaveImage")
            for n in (wf or {}).values()
        )
        if not has_save:
            fields.append(LowConfidenceField(
                path="$.plan.workflow",
                value="custom",
                kind=KIND_OTHER,
                reason=(
                    "custom workflow has no SaveImage-like node — "
                    "outputs cannot be picked up by /history"
                ),
            ))

    if result.elapsed_sec > 0.8 * result.plan.timeout_sec:
        fields.append(LowConfidenceField(
            path="$.elapsed_sec",
            value=round(result.elapsed_sec, 2),
            kind=KIND_NUMBER,
            reason=(
                f"render took {result.elapsed_sec:.1f}s out of "
                f"{result.plan.timeout_sec:.0f}s budget — queue or GPU "
                "is saturated, the next request may time out"
            ),
        ))

    return Verification(
        verified=not fields,
        verifier_id="local_sd_flux_self_check",
        low_confidence_fields=fields,
    )


# ── re-exports (so plugin.py doesn't need to know about presets module) ──


def list_available_presets() -> list[str]:
    return list_presets()


def describe(preset_id: str) -> dict[str, Any]:
    spec = describe_preset(preset_id)
    return {
        "id": preset_id,
        "family": spec.family,
        "checkpoint_default": spec.checkpoint_default,
        "width_default": spec.width_default,
        "height_default": spec.height_default,
        "steps_default": spec.steps_default,
        "cfg_default": float(spec.get("cfg_default", 7.0)),
        "sampler_default": str(spec.get("sampler_default", "euler")),
        "scheduler_default": str(spec.get("scheduler_default", "normal")),
        "notes": str(spec.get("notes", "")),
    }

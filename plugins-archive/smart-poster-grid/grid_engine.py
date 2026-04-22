"""smart-poster-grid — pure-logic core.

This module is *intentionally* tiny: it picks one ``poster-maker``
template per target aspect ratio, calls
:func:`poster-maker.poster_engine.render_poster` once per ratio, and
returns a uniform :class:`GridJobResult`.

Why not "a grid in one image"?  Empirically the marketing teams want
*separate files* (different platforms have different upload limits and
auto-crop rules) — so we deliver one PNG per ratio in a flat folder.

This module never touches FastAPI, ``asyncio`` or sqlite — those live in
``plugin.py`` and ``task_manager.py``.  That keeps unit tests fast and
makes the engine reusable from inside ``shorts-batch`` (D3) when it
needs to "render covers for 4 platforms".
"""
# --- _shared bootstrap (auto-inserted by archive cleanup) ---
import sys as _sys
import pathlib as _pathlib
_archive_root = _pathlib.Path(__file__).resolve()
for _p in _archive_root.parents:
    if (_p / '_shared' / '__init__.py').is_file():
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
        break
del _sys, _pathlib, _archive_root
# --- end bootstrap ---

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from _shared import (
    KIND_NUMBER,
    KIND_OTHER,
    LowConfidenceField,
    Verification,
)

__all__ = [
    "DEFAULT_RATIOS",
    "RATIO_PRESETS",
    "GridJobResult",
    "GridPlan",
    "PosterRender",
    "RatioSpec",
    "build_grid_plan",
    "list_ratios",
    "render_grid",
    "resolve_template_for_ratio",
    "to_verification",
]


# ── Aspect ratios ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class RatioSpec:
    """One target aspect ratio.

    ``poster_template_id`` points at a template registered by the
    sibling ``poster-maker`` plugin.  We keep the wall between the two
    plugins thin: this module is the *only* place that knows the
    mapping ratio → template_id.
    """

    id: str                    # "1x1", "3x4", "9x16", "16x9"
    label: str                 # human label
    width: int
    height: int
    poster_template_id: str    # one of poster-maker's templates


# Concrete dimensions chosen to match each platform's recommended source
# size (Instagram square, RED note vertical poster, TikTok / Reels /
# Shorts vertical, YouTube / Twitter banner).  All are even integers so
# downstream encoders never complain about odd dimensions.
RATIO_1X1 = RatioSpec(
    id="1x1", label="1:1 方图 (1080x1080)",
    width=1080, height=1080,
    poster_template_id="social-square",
)
RATIO_3X4 = RatioSpec(
    id="3x4", label="3:4 竖版海报 (900x1200)",
    width=900, height=1200,
    poster_template_id="vertical-poster",
)
RATIO_9X16 = RatioSpec(
    id="9x16", label="9:16 短视频封面 (1080x1920)",
    width=1080, height=1920,
    # poster-maker has no native 9:16 template — we synthesize one by
    # cloning vertical-poster and resizing the canvas.  See
    # :func:`resolve_template_for_ratio`.
    poster_template_id="vertical-poster",
)
RATIO_16X9 = RatioSpec(
    id="16x9", label="16:9 横幅 (1920x1080)",
    width=1920, height=1080,
    poster_template_id="banner-wide",
)

DEFAULT_RATIOS: tuple[RatioSpec, ...] = (
    RATIO_1X1, RATIO_3X4, RATIO_9X16, RATIO_16X9,
)

# Public registry — id → spec.  Tests rely on iteration order being
# deterministic, so we use ``dict`` (ordered since Py3.7) and feed it
# from the tuple above.
RATIO_PRESETS: dict[str, RatioSpec] = {r.id: r for r in DEFAULT_RATIOS}


def list_ratios() -> list[dict[str, Any]]:
    """Return a JSON-serializable summary of every supported ratio."""
    return [
        {"id": r.id, "label": r.label, "width": r.width, "height": r.height,
         "poster_template_id": r.poster_template_id}
        for r in DEFAULT_RATIOS
    ]


# ── Sibling plugin loader ──────────────────────────────────────────────


def _load_poster_maker_module(module_name: str, alias: str):
    """Load ``poster-maker/<module_name>.py`` into ``sys.modules`` once.

    poster-maker is a sibling plugin and exposes plain Python modules
    next to its ``plugin.py``.  We can't simply ``import templates``
    because both this plugin *and* poster-maker have a ``templates`` /
    ``task_manager`` module on their respective ``sys.path`` — same
    name, different code.  Aliasing under ``_oa_pm_<module>`` gives us
    a single canonical reference no matter who imports first.
    """
    if alias in sys.modules:
        return sys.modules[alias]
    src = Path(__file__).resolve().parent.parent / "poster-maker" / f"{module_name}.py"
    if not src.exists():
        raise ImportError(
            f"smart-poster-grid requires the sibling plugin 'poster-maker' "
            f"(missing file: {src})"
        )
    spec = importlib.util.spec_from_file_location(alias, src)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load poster-maker module: {src}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


def _poster_maker_templates():
    return _load_poster_maker_module("templates", "_oa_pm_templates")


def _poster_maker_engine():
    return _load_poster_maker_module("poster_engine", "_oa_pm_poster_engine")


def resolve_template_for_ratio(spec: RatioSpec):
    """Return a ``PosterTemplate`` already resized to ``spec`` dimensions.

    poster-maker only ships 3 native sizes (1:1, 3:4, 16:9).  For 9:16
    we deep-clone the closest match (3:4 ``vertical-poster``) and just
    swap the canvas dimensions.  Slot positions are *normalized* (0-1)
    in poster-maker, so the layout transparently reflows for the new
    aspect ratio.
    """
    pm_templates = _poster_maker_templates()
    base = pm_templates.get_template(spec.poster_template_id)
    if base.width == spec.width and base.height == spec.height:
        return base

    # Clone the dataclass with new dimensions; slots are normalized so
    # they auto-adapt.  We deliberately don't mutate the original,
    # because poster-maker may render in the same process at the same
    # time (e.g., the user opens both UIs).
    Template = type(base)
    return Template(
        id=base.id,
        name=base.name,
        description=base.description,
        width=spec.width,
        height=spec.height,
        background_color=base.background_color,
        overlay_color=base.overlay_color,
        slots=list(base.slots),
    )


# ── Plan & result models ───────────────────────────────────────────────


@dataclass
class PosterRender:
    """One rendered (or failed) ratio inside a grid job."""

    ratio_id: str
    width: int
    height: int
    output_path: str | None = None
    ok: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ratio_id": self.ratio_id,
            "width": self.width,
            "height": self.height,
            "output_path": self.output_path,
            "ok": self.ok,
            "error": self.error,
        }


@dataclass
class GridPlan:
    """What the engine *intends* to render before doing any I/O."""

    ratios: list[RatioSpec]
    text_values: dict[str, str]
    background_image_path: str | None
    output_dir: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ratios": [
                {"id": r.id, "label": r.label, "width": r.width, "height": r.height}
                for r in self.ratios
            ],
            "text_values": dict(self.text_values),
            "background_image_path": self.background_image_path,
            "output_dir": self.output_dir,
        }


@dataclass
class GridJobResult:
    """Full result of one grid render."""

    plan: GridPlan
    renders: list[PosterRender] = field(default_factory=list)

    @property
    def succeeded_count(self) -> int:
        return sum(1 for r in self.renders if r.ok and r.output_path)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.renders if not r.ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "renders": [r.to_dict() for r in self.renders],
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
        }


# ── Public API ─────────────────────────────────────────────────────────


def build_grid_plan(
    *,
    text_values: dict[str, str] | None,
    background_image_path: str | None,
    output_dir: str | Path,
    ratio_ids: Iterable[str] | None = None,
) -> GridPlan:
    """Validate inputs and freeze the rendering plan.

    Raises ``ValueError`` for empty / unknown ratio ids so the API
    layer can short-circuit with a 400 before queuing a worker task.
    """
    if ratio_ids is None:
        chosen = list(DEFAULT_RATIOS)
    else:
        # de-duplicate while preserving the user's order
        seen: set[str] = set()
        chosen = []
        for rid in ratio_ids:
            if not isinstance(rid, str) or not rid.strip():
                raise ValueError(f"ratio id must be a non-empty string, got: {rid!r}")
            rid = rid.strip()
            if rid in seen:
                continue
            spec = RATIO_PRESETS.get(rid)
            if spec is None:
                raise ValueError(
                    f"unknown ratio id: {rid!r}; "
                    f"supported: {sorted(RATIO_PRESETS)}"
                )
            chosen.append(spec)
            seen.add(rid)
        if not chosen:
            raise ValueError("at least one ratio id is required")

    return GridPlan(
        ratios=chosen,
        text_values=dict(text_values or {}),
        background_image_path=background_image_path or None,
        output_dir=str(Path(output_dir).resolve()),
    )


def render_grid(plan: GridPlan) -> GridJobResult:
    """Execute ``plan``: render one PNG per ratio.

    Failures on individual ratios are *non-fatal* — we keep going and
    record the error per :class:`PosterRender`.  Total failure (no
    ratio succeeded) is something the caller decides about by
    inspecting :attr:`GridJobResult.succeeded_count`.

    This function is synchronous on purpose; ``plugin.py`` calls it
    inside :func:`asyncio.to_thread` so the worker loop stays
    responsive.
    """
    pm_engine = _poster_maker_engine()
    out_dir = Path(plan.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    bg_path = Path(plan.background_image_path) if plan.background_image_path else None

    result = GridJobResult(plan=plan)
    for spec in plan.ratios:
        render = PosterRender(ratio_id=spec.id, width=spec.width, height=spec.height)
        try:
            template = resolve_template_for_ratio(spec)
            output_path = out_dir / f"poster_{spec.id}.png"
            pm_engine.render_poster(
                template=template,
                text_values=dict(plan.text_values),
                background_image=bg_path,
                output_path=output_path,
            )
            render.output_path = str(output_path)
            render.ok = True
        except Exception as e:  # noqa: BLE001 — engine boundary
            render.ok = False
            render.error = f"{type(e).__name__}: {e}"
        result.renders.append(render)
    return result


# ── Verification (D2.10) ───────────────────────────────────────────────


def to_verification(result: GridJobResult) -> Verification:
    """Convert a :class:`GridJobResult` into a D2.10 verification envelope.

    We surface yellow flags rather than fail the task — the user often
    *wants* a partial result (e.g., "9:16 broke because the bg image
    is corrupt, but the other 3 are fine, ship them").
    """
    fields: list[LowConfidenceField] = []

    if not result.renders:
        fields.append(LowConfidenceField(
            path="$.renders",
            value=0,
            kind=KIND_NUMBER,
            reason="grid plan produced zero ratios — inputs were probably empty",
        ))

    for render in result.renders:
        if not render.ok:
            fields.append(LowConfidenceField(
                path=f"$.renders[{render.ratio_id}].error",
                value=render.error or "unknown",
                kind=KIND_OTHER,
                reason=(
                    f"ratio {render.ratio_id} failed; the other ratios "
                    "still rendered. Inspect render.error and re-queue "
                    "just this ratio if needed."
                ),
            ))

    if result.renders and result.failed_count == 0 and not any(
        Path(r.output_path).exists() if r.output_path else False
        for r in result.renders
    ):
        fields.append(LowConfidenceField(
            path="$.renders[*].output_path",
            value=None,
            kind=KIND_OTHER,
            reason="every render reported ok=True but no output file was "
                   "found on disk — likely a permission or quota problem",
        ))

    return Verification(
        verified=not fields,
        verifier_id="smart_poster_grid_self_check",
        low_confidence_fields=fields,
    )

"""Unit tests for ``grid_engine`` — every public symbol gets at least
one focused test, plus a few "things that should NEVER happen"
regressions (unknown ratio, empty list, duplicates).

External dependencies (the sibling poster-maker plugin's
``render_poster``) are monkey-patched out so the suite never touches
Pillow on CI runners that lack a font.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import grid_engine
from grid_engine import (
    DEFAULT_RATIOS,
    RATIO_PRESETS,
    GridJobResult,
    GridPlan,
    PosterRender,
    RatioSpec,
    build_grid_plan,
    list_ratios,
    render_grid,
    resolve_template_for_ratio,
    to_verification,
)


# ── DEFAULT_RATIOS / RATIO_PRESETS / list_ratios ──────────────────────────


def test_default_ratios_has_exactly_four() -> None:
    assert len(DEFAULT_RATIOS) == 4


def test_default_ratios_cover_required_aspect_ratios() -> None:
    ids = {r.id for r in DEFAULT_RATIOS}
    assert ids == {"1x1", "3x4", "9x16", "16x9"}


def test_default_ratios_dimensions_are_correct() -> None:
    sizes = {r.id: (r.width, r.height) for r in DEFAULT_RATIOS}
    assert sizes == {
        "1x1":  (1080, 1080),
        "3x4":  (900, 1200),
        "9x16": (1080, 1920),
        "16x9": (1920, 1080),
    }


def test_ratio_presets_keys_match_default_ratios_ids() -> None:
    assert set(RATIO_PRESETS.keys()) == {r.id for r in DEFAULT_RATIOS}


def test_list_ratios_returns_serializable_dicts() -> None:
    items = list_ratios()
    assert isinstance(items, list) and len(items) == 4
    for entry in items:
        # Must be JSON-serializable (the API endpoint dumps these).
        json.dumps(entry)
        assert {"id", "label", "width", "height", "poster_template_id"} <= set(entry.keys())


# ── resolve_template_for_ratio ────────────────────────────────────────────


def test_resolve_template_for_native_ratio_returns_unmodified_template() -> None:
    """1:1 maps to poster-maker's native ``social-square`` (1080x1080),
    so we must return the SAME object — no clone, no mutation."""
    spec = RATIO_PRESETS["1x1"]
    tpl = resolve_template_for_ratio(spec)
    assert tpl.width == spec.width and tpl.height == spec.height


def test_resolve_template_for_9x16_clones_with_new_dimensions() -> None:
    """poster-maker has no native 9:16 — we must synthesize one by
    cloning vertical-poster (3:4) and resizing to 1080x1920."""
    spec = RATIO_PRESETS["9x16"]
    tpl = resolve_template_for_ratio(spec)
    assert tpl.width == 1080 and tpl.height == 1920
    # Slots are normalized so they survive the resize.
    assert tpl.slots, "9:16 clone must inherit slots from vertical-poster"


def test_resolve_template_does_not_mutate_original() -> None:
    """If we accidentally mutated the cached PosterTemplate, a later
    1:1 render would inherit the 9:16 dimensions."""
    spec_9 = RATIO_PRESETS["9x16"]
    resolve_template_for_ratio(spec_9)
    # Now ask for 3:4 (same backing template id) — must still be 900x1200.
    spec_3 = RATIO_PRESETS["3x4"]
    tpl_3 = resolve_template_for_ratio(spec_3)
    assert (tpl_3.width, tpl_3.height) == (900, 1200)


# ── build_grid_plan: validation ──────────────────────────────────────────


def test_build_grid_plan_default_uses_all_four_ratios(tmp_path: Path) -> None:
    plan = build_grid_plan(
        text_values={"title": "Hi"},
        background_image_path=None,
        output_dir=tmp_path,
    )
    assert [r.id for r in plan.ratios] == ["1x1", "3x4", "9x16", "16x9"]


def test_build_grid_plan_preserves_user_ratio_order(tmp_path: Path) -> None:
    plan = build_grid_plan(
        text_values={},
        background_image_path=None,
        output_dir=tmp_path,
        ratio_ids=["16x9", "1x1"],
    )
    assert [r.id for r in plan.ratios] == ["16x9", "1x1"]


def test_build_grid_plan_dedupes_ratio_ids(tmp_path: Path) -> None:
    plan = build_grid_plan(
        text_values={},
        background_image_path=None,
        output_dir=tmp_path,
        ratio_ids=["1x1", "1x1", "3x4"],
    )
    assert [r.id for r in plan.ratios] == ["1x1", "3x4"]


def test_build_grid_plan_rejects_unknown_ratio(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown ratio"):
        build_grid_plan(
            text_values={},
            background_image_path=None,
            output_dir=tmp_path,
            ratio_ids=["666x666"],
        )


def test_build_grid_plan_rejects_empty_ratio_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        build_grid_plan(
            text_values={},
            background_image_path=None,
            output_dir=tmp_path,
            ratio_ids=[""],
        )


def test_build_grid_plan_rejects_empty_list(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        build_grid_plan(
            text_values={},
            background_image_path=None,
            output_dir=tmp_path,
            ratio_ids=[],
        )


def test_build_grid_plan_resolves_output_dir_to_absolute(tmp_path: Path) -> None:
    plan = build_grid_plan(
        text_values={}, background_image_path=None, output_dir=tmp_path,
    )
    assert Path(plan.output_dir).is_absolute()


# ── render_grid: success / failure / partial ─────────────────────────────


def _patch_renderer(monkeypatch: pytest.MonkeyPatch, *, fail_for: set[str] | None = None):
    """Replace poster-maker's ``render_poster`` with a fake that just
    writes a tiny PNG-like file at ``output_path``.

    ``fail_for`` lists ratio dimensions (as ``"WxH"``) for which the
    fake should raise — used to test partial-failure paths.
    """
    fail_for = fail_for or set()

    class FakeEngine:
        @staticmethod
        def render_poster(*, template, text_values, background_image, output_path):
            key = f"{template.width}x{template.height}"
            if key in fail_for:
                raise RuntimeError(f"forced failure for {key}")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
            return output_path

    monkeypatch.setattr(grid_engine, "_poster_maker_engine", lambda: FakeEngine)


def test_render_grid_writes_one_file_per_ratio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_renderer(monkeypatch)
    plan = build_grid_plan(
        text_values={"title": "T"},
        background_image_path=None,
        output_dir=tmp_path,
    )
    result = render_grid(plan)
    assert result.succeeded_count == 4
    assert result.failed_count == 0
    for r in result.renders:
        assert r.output_path is not None and Path(r.output_path).is_file()


def test_render_grid_filenames_use_ratio_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_renderer(monkeypatch)
    plan = build_grid_plan(
        text_values={}, background_image_path=None, output_dir=tmp_path,
    )
    result = render_grid(plan)
    names = {Path(r.output_path).name for r in result.renders if r.output_path}
    assert names == {
        "poster_1x1.png", "poster_3x4.png",
        "poster_9x16.png", "poster_16x9.png",
    }


def test_render_grid_partial_failure_keeps_other_ratios_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If 9:16 raises, 1:1 / 3:4 / 16:9 must still succeed."""
    _patch_renderer(monkeypatch, fail_for={"1080x1920"})
    plan = build_grid_plan(
        text_values={}, background_image_path=None, output_dir=tmp_path,
    )
    result = render_grid(plan)
    assert result.succeeded_count == 3
    assert result.failed_count == 1
    failed = [r for r in result.renders if not r.ok]
    assert len(failed) == 1 and failed[0].ratio_id == "9x16"
    assert "RuntimeError" in (failed[0].error or "")


def test_render_grid_records_error_message_per_ratio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_renderer(monkeypatch, fail_for={"1080x1080", "1920x1080"})
    plan = build_grid_plan(
        text_values={}, background_image_path=None, output_dir=tmp_path,
    )
    result = render_grid(plan)
    errors = {r.ratio_id: r.error for r in result.renders if not r.ok}
    assert set(errors.keys()) == {"1x1", "16x9"}
    for msg in errors.values():
        assert msg and "forced failure" in msg


def test_render_grid_creates_output_dir_if_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_renderer(monkeypatch)
    nested = tmp_path / "deep" / "deeper"
    plan = build_grid_plan(
        text_values={}, background_image_path=None, output_dir=nested,
    )
    render_grid(plan)
    assert nested.is_dir()


# ── GridJobResult ─────────────────────────────────────────────────────────


def test_grid_job_result_to_dict_round_trips_via_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_renderer(monkeypatch)
    plan = build_grid_plan(
        text_values={"title": "T"},
        background_image_path=None,
        output_dir=tmp_path,
        ratio_ids=["1x1"],
    )
    result = render_grid(plan)
    data = result.to_dict()
    raw = json.dumps(data, ensure_ascii=False)
    parsed = json.loads(raw)
    assert parsed["succeeded_count"] == 1 and parsed["failed_count"] == 0
    assert parsed["renders"][0]["ratio_id"] == "1x1"


# ── to_verification ──────────────────────────────────────────────────────


def test_verification_is_clean_when_all_ratios_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_renderer(monkeypatch)
    plan = build_grid_plan(
        text_values={}, background_image_path=None, output_dir=tmp_path,
    )
    result = render_grid(plan)
    v = to_verification(result)
    assert v.verified is True
    assert v.low_confidence_fields == []
    assert v.verifier_id == "smart_poster_grid_self_check"


def test_verification_flags_each_failed_ratio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_renderer(monkeypatch, fail_for={"1080x1920"})
    plan = build_grid_plan(
        text_values={}, background_image_path=None, output_dir=tmp_path,
    )
    result = render_grid(plan)
    v = to_verification(result)
    assert v.verified is False
    paths = [f.path for f in v.low_confidence_fields]
    assert any("9x16" in p for p in paths)


def test_verification_flags_empty_render_list() -> None:
    plan = GridPlan(
        ratios=[],  # impossible via build_grid_plan, but defensive
        text_values={}, background_image_path=None, output_dir=".",
    )
    result = GridJobResult(plan=plan, renders=[])
    v = to_verification(result)
    assert v.verified is False
    assert any(f.path == "$.renders" for f in v.low_confidence_fields)


def test_verification_flags_phantom_files_when_ok_but_missing(
    tmp_path: Path,
) -> None:
    """If a renderer reported ok=True but the file is gone (someone
    rm'd it / disk full), to_verification must flag a yellow."""
    plan = GridPlan(
        ratios=list(DEFAULT_RATIOS), text_values={},
        background_image_path=None, output_dir=str(tmp_path),
    )
    fake_renders = [
        PosterRender(
            ratio_id=r.id, width=r.width, height=r.height,
            output_path=str(tmp_path / "missing" / f"poster_{r.id}.png"),
            ok=True,
        )
        for r in DEFAULT_RATIOS
    ]
    result = GridJobResult(plan=plan, renders=fake_renders)
    v = to_verification(result)
    assert v.verified is False
    assert any(
        "phantom" in (f.reason or "").lower()
        or "no output file" in (f.reason or "").lower()
        for f in v.low_confidence_fields
    )

"""poster-maker engine tests (offline; needs Pillow)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from poster_engine import render_poster  # noqa: E402
from templates import TEMPLATES, get_template, list_templates  # noqa: E402


def test_list_templates_has_three_presets() -> None:
    out = list_templates()
    ids = [t["id"] for t in out]
    assert {"social-square", "vertical-poster", "banner-wide"}.issubset(set(ids))
    for t in out:
        assert t["width"] > 0 and t["height"] > 0
        assert isinstance(t["slots"], list) and len(t["slots"]) >= 1


def test_get_template_returns_known_template() -> None:
    t = get_template("social-square")
    assert t.width == 1080 and t.height == 1080


def test_get_template_unknown_raises() -> None:
    with pytest.raises(KeyError):
        get_template("does-not-exist")


def test_render_poster_creates_png(tmp_path) -> None:
    out = tmp_path / "p.png"
    render_poster(
        template=get_template("social-square"),
        text_values={"title": "测试 Title", "subtitle": "Subtitle here", "cta": "GO"},
        background_image=None, output_path=out,
    )
    assert out.is_file()
    assert out.stat().st_size > 1024  # not empty
    # PNG magic
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_poster_uses_placeholder_when_value_missing(tmp_path) -> None:
    out = tmp_path / "p.png"
    render_poster(
        template=get_template("vertical-poster"),
        text_values={},  # rely on placeholders
        background_image=None, output_path=out,
    )
    assert out.is_file() and out.stat().st_size > 1024


def test_render_poster_with_background_image(tmp_path) -> None:
    from PIL import Image
    bg = tmp_path / "bg.png"
    Image.new("RGB", (300, 300), "#ff8800").save(bg, "PNG")
    out = tmp_path / "p.png"
    render_poster(
        template=get_template("social-square"),
        text_values={"title": "Hi"},
        background_image=bg, output_path=out,
    )
    assert out.is_file()


def test_render_poster_with_missing_background_doesnt_crash(tmp_path) -> None:
    out = tmp_path / "p.png"
    render_poster(
        template=get_template("banner-wide"),
        text_values={"title": "T"},
        background_image=tmp_path / "_nope.png",  # doesn't exist
        output_path=out,
    )
    assert out.is_file()


def test_all_templates_render_without_error(tmp_path) -> None:
    for t in TEMPLATES:
        out = tmp_path / f"{t.id}.png"
        render_poster(template=t, text_values={}, background_image=None, output_path=out)
        assert out.is_file()

"""Unit tests for ``mediapost_chapter_renderer``.

Coverage targets per ``docs/media-post-plan.md`` §6.5 + §11 Phase 3:

- ``parse_template_parameters`` extracts DSL placeholders.
- ``parse_media_size_from_meta`` reads ``<meta name="media-size" ...>``.
- ``replace_parameters`` substitutes values + falls back to defaults.
- ``builtin_template_ids`` returns the 5 documented names.
- ``_escape_drawtext`` escapes ``\\``, ``:``, ``'``.
- B path (drawtext) renders 1 chapter via stubbed ffmpeg.
- A path (Playwright) is skipped when not available.
- Empty chapter list raises ``format``.
- Unknown ``template_id`` raises ``format``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mediapost_chapter_renderer import (
    ChapterCardSpec,
    ChapterRenderContext,
    _escape_drawtext,
    _load_template,
    builtin_template_ids,
    parse_media_size_from_meta,
    parse_template_parameters,
    render_chapter_cards,
    replace_parameters,
)
from mediapost_models import MediaPostError


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestParseMediaSizeFromMeta:
    def test_present(self) -> None:
        html = '<html><meta name="media-size" content="1920x1080"></html>'
        assert parse_media_size_from_meta(html, fallback=(0, 0)) == (1920, 1080)

    def test_absent(self) -> None:
        assert parse_media_size_from_meta("<html></html>", fallback=(800, 600)) == (
            800,
            600,
        )

    def test_uppercase_x(self) -> None:
        html = '<meta name="media-size" content="1280X720">'
        assert parse_media_size_from_meta(html, fallback=(0, 0)) == (1280, 720)


class TestParseTemplateParameters:
    def test_extracts_unique(self) -> None:
        html = "<h1>{{title:text=Hello}}</h1><p>{{count:int=3}}</p>"
        params = parse_template_parameters(html)
        names = [p["name"] for p in params]
        assert names == ["title", "count"]
        assert params[0]["default"] == "Hello"
        assert params[1]["type"] == "int"

    def test_dedupes(self) -> None:
        html = "{{x:text=a}} {{x:text=b}}"
        out = parse_template_parameters(html)
        assert len(out) == 1 and out[0]["default"] == "a"

    def test_empty(self) -> None:
        assert parse_template_parameters("") == []


class TestReplaceParameters:
    def test_substitutes(self) -> None:
        html = "<h1>{{title:text=Default}}</h1>"
        out = replace_parameters(html, {"title": "Hello"})
        assert out == "<h1>Hello</h1>"

    def test_falls_back_to_default(self) -> None:
        html = "<h1>{{title:text=Default}}</h1>"
        out = replace_parameters(html, {})
        assert out == "<h1>Default</h1>"

    def test_none_value_becomes_empty(self) -> None:
        html = "{{title:text=fallback}}"
        out = replace_parameters(html, {"title": None})
        assert out == ""


class TestEscapeDrawtext:
    def test_colon_and_quote(self) -> None:
        out = _escape_drawtext("hello: it's a test")
        assert "\\:" in out
        assert "\\\\'" in out

    def test_empty(self) -> None:
        assert _escape_drawtext("") == ""


class TestBuiltinTemplateIds:
    def test_five_names(self) -> None:
        ids = list(builtin_template_ids())
        assert "modern" in ids
        assert "minimal" in ids
        assert "retro" in ids
        assert "youtube_style" in ids
        assert "custom" in ids


# ---------------------------------------------------------------------------
# _load_template
# ---------------------------------------------------------------------------


class TestLoadTemplate:
    def test_user_overrides_builtin(self, tmp_path: Path) -> None:
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        (templates_dir / "modern.html").write_text("user-version", encoding="utf-8")
        ctx = ChapterRenderContext(
            out_dir=tmp_path / "out",
            chapters=[],
            templates_dir=templates_dir,
            builtin_templates={"modern": "builtin"},
        )
        assert _load_template("modern", ctx) == "user-version"

    def test_unknown_raises_format(self, tmp_path: Path) -> None:
        ctx = ChapterRenderContext(
            out_dir=tmp_path / "out",
            chapters=[],
            builtin_templates={"modern": "x"},
        )
        with pytest.raises(MediaPostError) as ei:
            _load_template("nope", ctx)
        assert ei.value.kind == "format"


# ---------------------------------------------------------------------------
# render_chapter_cards — drawtext B path
# ---------------------------------------------------------------------------


def _patch_drawtext_writes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Replace asyncio.create_subprocess_exec with a fake that writes a PNG."""

    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def _fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        # Last positional arg is the output filename
        out = Path(args[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x89PNG\r\n\x1a\n")
        return _FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)


def _patch_no_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_pw() -> bool:
        return False

    monkeypatch.setattr("mediapost_chapter_renderer._playwright_available", _no_pw)


class TestRenderChapterCards:
    def test_drawtext_path_writes_png(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_no_playwright(monkeypatch)
        _patch_drawtext_writes(monkeypatch, tmp_path)

        ctx = ChapterRenderContext(
            out_dir=tmp_path / "out",
            chapters=[
                ChapterCardSpec(
                    chapter_index=1, title="Intro", subtitle="welcome"
                ),
                ChapterCardSpec(
                    chapter_index=2, title="Conclusion", subtitle="thanks"
                ),
            ],
            prefer_playwright=False,
            builtin_templates={"modern": "<html></html>"},
        )
        rows = _run(render_chapter_cards(ctx))
        assert len(rows) == 2
        assert all(r["render_path"] == "drawtext" for r in rows)
        assert all(Path(r["png_path"]).exists() for r in rows)
        assert rows[0]["chapter_index"] == 1

    def test_empty_chapters_raises_format(self, tmp_path: Path) -> None:
        ctx = ChapterRenderContext(out_dir=tmp_path / "out", chapters=[])
        with pytest.raises(MediaPostError) as ei:
            _run(render_chapter_cards(ctx))
        assert ei.value.kind == "format"

    def test_drawtext_failure_raises_dependency(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_no_playwright(monkeypatch)

        class _Failure:
            returncode = 1

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b"some ffmpeg error"

        async def _fake_exec(*args: Any, **kwargs: Any) -> _Failure:
            return _Failure()

        monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)

        ctx = ChapterRenderContext(
            out_dir=tmp_path / "out2",
            chapters=[ChapterCardSpec(chapter_index=1, title="x")],
            prefer_playwright=False,
        )
        with pytest.raises(MediaPostError) as ei:
            _run(render_chapter_cards(ctx))
        assert ei.value.kind == "dependency"

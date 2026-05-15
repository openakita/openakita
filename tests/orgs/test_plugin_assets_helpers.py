"""Unit tests for :mod:`openakita.orgs.plugin_assets`.

These are the pure helpers that were lifted out of
``openakita.orgs.runtime`` during the runtime-split (P3). Keeping them
unit-tested here means we don't need to spin up an ``OrgRuntime`` to
validate path-traversal hardening or filename clipping.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.orgs.plugin_assets import (
    PLUGIN_ASSET_MAX_BYTES,
    copy_to_workspace,
    download_to_workspace,
    ext_for_url,
    safe_asset_filename,
)


class TestSafeAssetFilename:
    def test_strips_path_traversal(self) -> None:
        assert safe_asset_filename("../../etc/passwd") == "passwd"
        assert safe_asset_filename("foo\\bar\\baz.png") == "baz.png"

    def test_replaces_dangerous_chars(self) -> None:
        out = safe_asset_filename('a:b<c>d|e?.png')
        assert out == "a_b_c_d_e_.png"

    def test_default_when_empty(self) -> None:
        assert safe_asset_filename("") == "asset.bin"
        assert safe_asset_filename("   ", default_ext=".png") == "asset.png"

    def test_caps_overlong_names_keeping_extension(self) -> None:
        out = safe_asset_filename("a" * 500 + ".mp4")
        assert len(out) <= 120
        assert out.endswith(".mp4")

    def test_caps_overlong_names_without_extension(self) -> None:
        out = safe_asset_filename("x" * 500)
        assert len(out) == 120
        assert out == "x" * 120


class TestExtForUrl:
    def test_lowercases_extension(self) -> None:
        assert ext_for_url("https://x.test/Foo.MP4") == ".mp4"

    def test_ignores_query_string(self) -> None:
        assert ext_for_url("https://x.test/foo.png?token=abc") == ".png"

    def test_falls_back_when_no_extension(self) -> None:
        assert ext_for_url("https://x.test/no-ext") == ".bin"
        assert ext_for_url("https://x.test/no-ext", fallback=".jpg") == ".jpg"

    def test_handles_garbage_input(self) -> None:
        assert ext_for_url("not a url at all", fallback=".x") == ".x"


class TestCopyToWorkspace:
    def test_copies_when_dest_missing(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("hello", encoding="utf-8")
        dest = tmp_path / "out" / "dest.txt"

        assert copy_to_workspace(src, dest) is True
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == "hello"

    def test_short_circuits_when_dest_exists(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("hello", encoding="utf-8")
        dest = tmp_path / "dest.txt"
        dest.write_text("existing", encoding="utf-8")

        assert copy_to_workspace(src, dest) is True
        # We never overwrite an existing target.
        assert dest.read_text(encoding="utf-8") == "existing"


@pytest.mark.asyncio
class TestDownloadToWorkspace:
    async def test_empty_url_returns_false(self, tmp_path: Path) -> None:
        ok = await download_to_workspace("", tmp_path / "x.bin")
        assert ok is False

    async def test_max_bytes_constant_is_sane(self) -> None:
        # Just guard the constant against accidental zero / negative drift —
        # a downstream regression there would silently kill every download.
        assert PLUGIN_ASSET_MAX_BYTES > 1024 * 1024

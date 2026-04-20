"""Tests for ``contrib.upload_preview``.

Covers the safe-serve route registered by :func:`add_upload_preview_route`
and the ``build_preview_url`` helper.  All tests run against a real
``FastAPI`` app via ``TestClient`` so we exercise the same path as plugins
register at runtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
testclient = pytest.importorskip("fastapi.testclient")

from fastapi import APIRouter, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openakita_plugin_sdk.contrib import (  # noqa: E402
    DEFAULT_IMAGE_EXTENSIONS,
    DEFAULT_PREVIEW_EXTENSIONS,
    add_upload_preview_route,
    build_preview_url,
)


def _make_app(
    base_dir: Path,
    *,
    allowed_extensions=DEFAULT_PREVIEW_EXTENSIONS,
    max_bytes: int | None = 50 * 1024 * 1024,
) -> tuple[TestClient, callable]:
    router = APIRouter()
    make_url = add_upload_preview_route(
        router,
        base_dir=base_dir,
        allowed_extensions=allowed_extensions,
        max_bytes=max_bytes,
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/test-plugin")
    return TestClient(app), make_url


# ── happy path ────────────────────────────────────────────────────────────


def test_serves_existing_image_with_cache_header(tmp_path: Path) -> None:
    f = tmp_path / "hello.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    client, _ = _make_app(tmp_path)
    r = client.get("/api/plugins/test-plugin/uploads/hello.png")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")
    assert "max-age=" in r.headers.get("cache-control", "")


def test_serves_nested_subdirectory(tmp_path: Path) -> None:
    sub = tmp_path / "shoot" / "2026"
    sub.mkdir(parents=True)
    (sub / "a.jpg").write_bytes(b"jpeg-bytes")
    client, _ = _make_app(tmp_path)
    r = client.get("/api/plugins/test-plugin/uploads/shoot/2026/a.jpg")
    assert r.status_code == 200
    assert r.content == b"jpeg-bytes"


# ── path-traversal & escape attempts ──────────────────────────────────────


def test_rejects_parent_dir_escape(tmp_path: Path) -> None:
    secret = tmp_path.parent / "secret.png"
    secret.write_bytes(b"top-secret")
    try:
        client, _ = _make_app(tmp_path)
        r = client.get(
            f"/api/plugins/test-plugin/uploads/../{secret.name}"
        )
        # Server should refuse rather than serve the file.
        assert r.status_code in (403, 404)
        assert b"top-secret" not in r.content
    finally:
        secret.unlink(missing_ok=True)


def test_rejects_null_byte(tmp_path: Path) -> None:
    client, _ = _make_app(tmp_path)
    # Null bytes in URL are typically stripped/rejected by the server stack.
    # The route's own guard is the last line of defence; either layer
    # producing a non-2xx response is acceptable.
    r = client.get("/api/plugins/test-plugin/uploads/foo%00.png")
    assert r.status_code in (400, 404)


# ── extension allow-list ──────────────────────────────────────────────────


def test_extension_filter_blocks_disallowed_with_404(tmp_path: Path) -> None:
    (tmp_path / "config.env").write_bytes(b"SECRET=hunter2")
    client, _ = _make_app(tmp_path)
    r = client.get("/api/plugins/test-plugin/uploads/config.env")
    assert r.status_code == 404
    assert b"hunter2" not in r.content


def test_extension_filter_image_only(tmp_path: Path) -> None:
    (tmp_path / "song.mp3").write_bytes(b"mp3-data")
    client, _ = _make_app(tmp_path, allowed_extensions=DEFAULT_IMAGE_EXTENSIONS)
    r = client.get("/api/plugins/test-plugin/uploads/song.mp3")
    assert r.status_code == 404


def test_allow_all_extensions_when_filter_none(tmp_path: Path) -> None:
    (tmp_path / "weird.xyz").write_bytes(b"abc")
    client, _ = _make_app(tmp_path, allowed_extensions=None)
    r = client.get("/api/plugins/test-plugin/uploads/weird.xyz")
    assert r.status_code == 200


# ── size cap ──────────────────────────────────────────────────────────────


def test_oversized_file_returns_413(tmp_path: Path) -> None:
    big = tmp_path / "big.png"
    big.write_bytes(b"\x00" * 2048)
    client, _ = _make_app(tmp_path, max_bytes=1024)
    r = client.get("/api/plugins/test-plugin/uploads/big.png")
    assert r.status_code == 413


def test_size_cap_disabled_serves_large_file(tmp_path: Path) -> None:
    big = tmp_path / "big.png"
    big.write_bytes(b"\x00" * 4096)
    client, _ = _make_app(tmp_path, max_bytes=None)
    r = client.get("/api/plugins/test-plugin/uploads/big.png")
    assert r.status_code == 200


# ── 404 for missing files ─────────────────────────────────────────────────


def test_missing_file_returns_404(tmp_path: Path) -> None:
    client, _ = _make_app(tmp_path)
    r = client.get("/api/plugins/test-plugin/uploads/nope.png")
    assert r.status_code == 404


def test_directory_request_returns_404(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    client, _ = _make_app(tmp_path)
    r = client.get("/api/plugins/test-plugin/uploads/subdir")
    assert r.status_code == 404


# ── make_url helper returned by registration ──────────────────────────────


def test_make_url_returns_relative_to_route(tmp_path: Path) -> None:
    _, make_url = _make_app(tmp_path)
    assert make_url("a.png") == "/uploads/a.png"
    assert make_url("nested/b.jpg") == "/uploads/nested/b.jpg"
    # Backslashes (from Path on Windows) get normalised.
    assert make_url("nested\\b.jpg") == "/uploads/nested/b.jpg"
    # Leading slash is stripped to avoid double-slash URLs.
    assert make_url("/already/abs.png") == "/uploads/already/abs.png"


# ── module-level URL builder ──────────────────────────────────────────────


def test_build_preview_url_canonical_form() -> None:
    assert (
        build_preview_url("image-edit", "shoot/01.png")
        == "/api/plugins/image-edit/uploads/shoot/01.png"
    )


def test_build_preview_url_normalises_backslashes_and_leading_slash() -> None:
    assert (
        build_preview_url("tongyi-image", "\\sub\\file.jpg")
        == "/api/plugins/tongyi-image/uploads/sub/file.jpg"
    )


def test_build_preview_url_accepts_pathlike() -> None:
    p = Path("nested") / "asset.webp"
    out = build_preview_url("poster-maker", p)
    assert out.endswith("/uploads/nested/asset.webp")

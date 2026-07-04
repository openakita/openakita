"""Tests for runtime/desktop/attachments helpers (P-RC-6 P6.1c).

Pin the behaviour of the four attachment-routing helpers that
landed in P6.1a / P6.1b: byte-faithful filename normalisation,
data-URI decode + persist, local-image base64 inlining, and the
prompt-safe text reference.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

from openakita.runtime.desktop import attachments


def test_safe_attachment_stem_normalises_special_chars() -> None:
    """Non-ASCII / shell-special characters collapse to underscores."""
    assert attachments.safe_attachment_stem("报告.pdf") == "attachment"
    assert attachments.safe_attachment_stem("a b!@#c.txt") == "a_b_c"
    assert attachments.safe_attachment_stem("") == "attachment"
    assert attachments.safe_attachment_stem(None) == "attachment"


def test_safe_attachment_stem_caps_at_80_chars() -> None:
    """The 80-char ceiling matches the legacy contract."""
    long_name = "a" * 200 + ".txt"
    stem = attachments.safe_attachment_stem(long_name)
    assert len(stem) <= 80
    # Underscores from the strip("._") tail are not added back.
    assert stem == "a" * 80


def test_local_upload_re_matches_local_urls_only() -> None:
    """LOCAL_UPLOAD_RE accepts localhost / 127.0.0.1 / no-host paths."""
    for url in (
        "/api/uploads/foo.png",
        "http://127.0.0.1:18900/api/uploads/foo.png",
        "https://localhost/api/uploads/bar.jpg",
        "http://0.0.0.0:8000/api/uploads/baz.gif",
    ):
        assert attachments.LOCAL_UPLOAD_RE.match(url), url
    for url in (
        "https://example.com/api/uploads/foo.png",
        "http://10.0.0.1/api/uploads/foo.png",
        "/api/upload/foo.png",  # wrong path
    ):
        assert attachments.LOCAL_UPLOAD_RE.match(url) is None, url


def test_maybe_inline_local_image_skips_remote_urls() -> None:
    """Non-local URLs short-circuit to None without IO."""
    assert attachments.maybe_inline_local_image("", "image/png") is None
    assert attachments.maybe_inline_local_image(
        "https://example.com/foo.png", "image/png"
    ) is None
    assert (
        attachments.maybe_inline_local_image("data:image/png;base64,AAAA", "image/png")
        is None
    )


def test_maybe_inline_local_image_returns_data_url(tmp_path) -> None:
    """A local upload that exists and fits the cap returns a data: URL."""
    pixel = bytes.fromhex("89504e470d0a1a0a")  # PNG header bytes, enough for is_file
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "tiny.png").write_bytes(pixel)
    with patch(
        "openakita.api.routes.upload.get_upload_dir",
        return_value=upload_dir,
    ):
        out = attachments.maybe_inline_local_image(
            "/api/uploads/tiny.png", "image/png"
        )
    assert out is not None
    assert out.startswith("data:image/png;base64,")
    body = out.split(",", 1)[1]
    assert base64.b64decode(body) == pixel


def test_maybe_inline_local_image_skips_oversized(tmp_path) -> None:
    """Images larger than INLINE_IMAGE_MAX_BYTES return None."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    big = upload_dir / "big.png"
    big.write_bytes(b"x" * (attachments.INLINE_IMAGE_MAX_BYTES + 1))
    with patch(
        "openakita.api.routes.upload.get_upload_dir",
        return_value=upload_dir,
    ):
        out = attachments.maybe_inline_local_image(
            "/api/uploads/big.png", "image/png"
        )
    assert out is None


def test_format_desktop_attachment_reference_document() -> None:
    """Document branch produces a prompt-safe Chinese reference."""
    text = attachments.format_desktop_attachment_reference(
        att_type="document",
        att_name="report.pdf",
        att_mime="application/pdf",
        att_url="/api/uploads/report.pdf",
        att_local_path="/tmp/report.pdf",
        att_size=42,
    )
    assert "文档" in text  # "document" label in Chinese
    assert "report.pdf" in text
    assert "/tmp/report.pdf" in text
    assert "42 bytes" in text


def test_format_desktop_attachment_reference_audio_default() -> None:
    """audio/* mime triggers the audio label even when type='other'."""
    text = attachments.format_desktop_attachment_reference(
        att_type="other",
        att_name="memo.mp3",
        att_mime="audio/mpeg",
        att_url="",
    )
    assert "音频" in text  # audio label in Chinese
    assert "memo.mp3" in text


def test_save_data_uri_attachment_rejects_non_data_uri() -> None:
    """Non-data URIs short-circuit to None without touching the filesystem."""
    out = attachments.save_data_uri_attachment(
        "https://example.com/foo.bin",
        att_name="foo.bin",
        att_mime="application/octet-stream",
    )
    assert out is None


def test_save_data_uri_attachment_writes_file(tmp_path) -> None:
    """A valid data URI lands as a real file with a routing record."""
    raw = b"hello world"
    encoded = base64.b64encode(raw).decode("ascii")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    with (
        patch(
            "openakita.api.routes.upload.get_upload_dir",
            return_value=upload_dir,
        ),
        patch("openakita.api.routes.upload.MAX_UPLOAD_SIZE", 1024),
        patch("openakita.api.routes.upload.BLOCKED_EXTENSIONS", {".exe"}),
    ):
        out = attachments.save_data_uri_attachment(
            f"data:text/plain;base64,{encoded}",
            att_name="note.txt",
            att_mime="text/plain",
        )
    assert out is not None
    assert out["url"].startswith("/api/uploads/")
    assert out["size"] == len(raw)
    assert Path(out["local_path"]).read_bytes() == raw
    assert out["mime_type"] == "text/plain"


def test_data_uri_re_captures_named_groups() -> None:
    """DATA_URI_RE exposes mime / params / data named groups."""
    m = attachments.DATA_URI_RE.match("data:text/csv;charset=utf-8;base64,abcd")
    assert m is not None
    assert m.group("mime") == "text/csv"
    assert ";base64" in m.group("params")
    assert m.group("data") == "abcd"


def test_module_exports_are_stable() -> None:
    """Pin the public __all__ surface so callers can rely on it."""
    expected = {
        "DATA_URI_RE",
        "format_desktop_attachment_reference",
        "INLINE_IMAGE_MAX_BYTES",
        "LOCAL_UPLOAD_RE",
        "maybe_inline_local_image",
        "safe_attachment_stem",
        "save_data_uri_attachment",
    }
    from openakita.runtime.desktop import __all__ as pkg_all
    assert set(pkg_all) == expected

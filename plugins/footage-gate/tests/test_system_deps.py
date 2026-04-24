"""Phase 0 smoke tests for ``footage_gate_inline.system_deps``.

These tests exercise:

1. The base ``SystemDepsManager`` (forked verbatim from subtitle-craft) —
   we re-test the public surface here so a regression in the vendored copy
   is caught at this plugin's CI gate, not silently inherited.
2. The footage-gate-specific ``probe_ffmpeg_capabilities`` extension —
   version parsing for release / distro / git-build forms, and the
   never-raises contract on missing-binary inputs.

The tests do NOT require FFmpeg to be installed: the real-binary probe is
covered by ``tests/test_real_video_smoke.py`` in Phase 6, behind a
``pytest.mark.skipif(not shutil.which('ffmpeg'))`` gate. Here we patch
``subprocess.run`` so the suite runs on the strictest CI sandbox.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest
from footage_gate_inline import system_deps as sd

# ── version parser ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("blob", "expected"),
    [
        ("ffmpeg version 4.4.2 Copyright (c) 2000-2021 the FFmpeg developers", (4, 4)),
        ("ffmpeg version 4.4-1ubuntu0.1+esm1 Copyright", (4, 4)),
        ("ffmpeg version 6.1.1 Copyright", (6, 1)),
        ("ffmpeg version 4.0 Copyright", (4, 0)),
        # git/N-builds intentionally return None so the caller can warn rather
        # than guess (we treat unknown as 'permissive' downstream).
        ("ffmpeg version N-100123-g4f5a6b7 Copyright", None),
        ("ffmpeg version n4.4.2 Copyright", None),
        ("", None),
        ("not an ffmpeg banner", None),
    ],
)
def test_parse_ffmpeg_version(blob: str, expected: tuple[int, int] | None) -> None:
    assert sd._parse_ffmpeg_version(blob) == expected


# ── probe_ffmpeg_capabilities never raises ────────────────────────────────


def test_probe_returns_structured_dict_when_path_empty() -> None:
    result = sd.probe_ffmpeg_capabilities("")
    assert result["ok"] is False
    assert "ffmpeg_not_found" in result["missing"]
    assert result["filters"] == dict.fromkeys(sd.REQUIRED_FFMPEG_FILTERS, False)
    assert result["hdr_tonemap"] is False


def test_probe_handles_subprocess_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_kw: Any) -> Any:
        raise OSError("simulated spawn failure")

    monkeypatch.setattr(sd.subprocess, "run", boom)
    result = sd.probe_ffmpeg_capabilities("/fake/ffmpeg")
    assert result["ok"] is False
    assert "ffmpeg_version_unreadable" in result["missing"]


def test_probe_happy_path_with_all_filters_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate FFmpeg 4.4.2 with all required filters compiled in."""

    def fake_run(argv: list[str], **_kw: Any) -> Any:
        result = mock.MagicMock()
        result.stdout = ""
        result.stderr = ""
        if argv[1] == "-version":
            result.stdout = "ffmpeg version 4.4.2 Copyright (c) 2000-2021"
            return result
        # ``-h filter=NAME`` — 5th argv element is ``filter=<name>``.
        if "-h" in argv:
            name = argv[-1].split("=", 1)[1]
            # Pretend everything is present (including ``tonemap``).
            result.stdout = f"Filter {name}\n  description ..."
            return result
        return result

    monkeypatch.setattr(sd.subprocess, "run", fake_run)
    result = sd.probe_ffmpeg_capabilities("/fake/ffmpeg")
    assert result["ok"] is True, result
    assert result["version"] == (4, 4)
    assert result["version_str"] == "4.4"
    assert result["version_satisfied"] is True
    assert all(result["filters"].values())
    assert result["hdr_tonemap"] is True
    assert result["hdr_tonemap_via"] == "tonemap"
    assert result["missing"] == []


def test_probe_falls_back_to_zscale_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the standalone ``tonemap`` filter is missing but
    ``zscale + tonemap_zscale`` are both present, ``hdr_tonemap`` still
    passes — this is the common case on builds compiled against libzimg."""

    def fake_run(argv: list[str], **_kw: Any) -> Any:
        result = mock.MagicMock()
        result.stdout = ""
        result.stderr = ""
        if argv[1] == "-version":
            result.stdout = "ffmpeg version 4.4.2 Copyright"
            return result
        name = argv[-1].split("=", 1)[1]
        present = name in (*sd.REQUIRED_FFMPEG_FILTERS, "zscale", "tonemap_zscale")
        if present:
            result.stdout = f"Filter {name}"
        else:
            result.stdout = f"Unknown filter '{name}'."
        return result

    monkeypatch.setattr(sd.subprocess, "run", fake_run)
    result = sd.probe_ffmpeg_capabilities("/fake/ffmpeg")
    assert result["ok"] is True
    assert result["hdr_tonemap_via"] == "zscale+tonemap_zscale"


def test_probe_flags_old_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **_kw: Any) -> Any:
        result = mock.MagicMock()
        result.stdout = ""
        result.stderr = ""
        if argv[1] == "-version":
            result.stdout = "ffmpeg version 4.2.7 Copyright"
            return result
        name = argv[-1].split("=", 1)[1]
        result.stdout = f"Filter {name}"
        return result

    monkeypatch.setattr(sd.subprocess, "run", fake_run)
    result = sd.probe_ffmpeg_capabilities("/fake/ffmpeg")
    assert result["ok"] is False
    assert any(m.startswith("ffmpeg_version_too_old") for m in result["missing"])


# ── SystemDepsManager basic surface ───────────────────────────────────────


def test_manager_lists_ffmpeg_dep() -> None:
    mgr = sd.SystemDepsManager()
    components = mgr.list_components()
    assert any(c["id"] == "ffmpeg" for c in components)
    ff = next(c for c in components if c["id"] == "ffmpeg")
    assert "methods" in ff
    assert "uninstall_methods" in ff
    assert isinstance(ff["found"], bool)


def test_manager_rejects_unknown_dep() -> None:
    mgr = sd.SystemDepsManager()
    with pytest.raises(ValueError):
        mgr.detect("nonexistent-binary")

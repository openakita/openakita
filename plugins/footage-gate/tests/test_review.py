"""Unit tests for footage_gate_review — type detect + risk + usable_for guarantee."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import footage_gate_review as mod
import pytest
from footage_gate_review import detect_media_type, review_source_media


class TestDetectMediaType:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("clip.mp4", "video"),
            ("clip.MOV", "video"),
            ("clip.webm", "video"),
            ("song.mp3", "audio"),
            ("song.WAV", "audio"),
            ("photo.jpg", "image"),
            ("photo.PNG", "image"),
            ("doc.pdf", None),
            ("nope", None),
        ],
    )
    def test_classifies_by_extension(self, filename: str, expected: str | None) -> None:
        assert detect_media_type(Path(filename)) == expected


class TestReviewSourceMedia:
    """Patch the per-kind probes so we can exercise the orchestration logic."""

    @pytest.fixture
    def patch_probes(self, monkeypatch: pytest.MonkeyPatch):
        def _install(
            video: dict[str, Any] | None = None,
            audio: dict[str, Any] | None = None,
            image: dict[str, Any] | None = None,
        ) -> None:
            if video is not None:
                monkeypatch.setattr(mod, "_probe_video", lambda *_a, **_kw: video)
            if audio is not None:
                monkeypatch.setattr(mod, "_probe_audio", lambda *_a, **_kw: audio)
            if image is not None:
                monkeypatch.setattr(mod, "_probe_image", lambda *_a, **_kw: image)

        return _install

    def test_skips_unknown_extensions(self, tmp_path: Path) -> None:
        bogus = tmp_path / "data.bin"
        bogus.write_bytes(b"x")
        out = review_source_media([bogus])
        assert out["files"] == []
        assert "No user-supplied media files" in out["summary"]

    def test_skips_missing_files(self, tmp_path: Path) -> None:
        out = review_source_media([tmp_path / "ghost.mp4"])
        assert out["files"] == []

    def test_video_entry_includes_usable_for(self, tmp_path: Path, patch_probes) -> None:
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x")
        patch_probes(
            video={
                "technical_probe": {
                    "duration_seconds": 12.0,
                    "resolution": "1920x1080",
                    "audio_codec": "aac",
                },
                "quality_risks": [],
            }
        )
        out = review_source_media([f])
        assert len(out["files"]) == 1
        entry = out["files"][0]
        assert entry["media_type"] == "video"
        assert entry["usable_for"]
        assert "hero footage" in entry["usable_for"]

    def test_audio_entry_includes_usable_for(self, tmp_path: Path, patch_probes) -> None:
        f = tmp_path / "song.mp3"
        f.write_bytes(b"x")
        patch_probes(
            audio={
                "technical_probe": {
                    "duration_seconds": 60.0,
                    "audio_codec": "mp3",
                },
                "quality_risks": [],
            }
        )
        out = review_source_media([f])
        assert out["files"][0]["usable_for"]

    def test_image_entry_includes_usable_for_vs_issue_44(
        self, tmp_path: Path, patch_probes
    ) -> None:
        """OpenMontage Issue #44 — image entries previously missed usable_for."""
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")
        patch_probes(
            image={
                "technical_probe": {
                    "width": 2048,
                    "height": 2048,
                    "resolution": "2048x2048",
                },
                "quality_risks": [],
            }
        )
        out = review_source_media([f])
        entry = out["files"][0]
        assert entry["usable_for"], "image entries MUST always have usable_for"
        assert "hero still" in entry["usable_for"]

    def test_low_res_image_still_has_usable_for(self, tmp_path: Path, patch_probes) -> None:
        f = tmp_path / "tiny.jpg"
        f.write_bytes(b"x")
        patch_probes(
            image={
                "technical_probe": {
                    "width": 256,
                    "height": 256,
                    "resolution": "256x256",
                },
                "quality_risks": [],
            }
        )
        out = review_source_media([f])
        assert out["files"][0]["usable_for"] == [
            "visual asset",
            "reference image",
        ]

    def test_transcribe_callback_invoked_for_video(self, tmp_path: Path, patch_probes) -> None:
        f = tmp_path / "talk.mp4"
        f.write_bytes(b"x")
        patch_probes(
            video={
                "technical_probe": {
                    "duration_seconds": 30.0,
                    "resolution": "1920x1080",
                    "audio_codec": "aac",
                },
                "quality_risks": [],
            }
        )
        called: list[tuple[Path, str]] = []

        def _transcribe(path: Path, kind: str) -> str:
            called.append((path, kind))
            return "hello world"

        out = review_source_media([f], transcribe=_transcribe)
        assert called == [(f, "video")]
        entry = out["files"][0]
        assert entry["transcript_summary"] == "hello world"
        assert "source dialogue" in entry["usable_for"]

    def test_transcribe_callback_skipped_for_image(self, tmp_path: Path, patch_probes) -> None:
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")
        patch_probes(
            image={
                "technical_probe": {"width": 1024, "height": 1024},
                "quality_risks": [],
            }
        )

        def _transcribe(_p: Path, _k: str) -> str:
            raise AssertionError("transcribe must NOT be called for images")

        review_source_media([f], transcribe=_transcribe)

    def test_transcribe_failure_is_swallowed(self, tmp_path: Path, patch_probes) -> None:
        f = tmp_path / "talk.mp4"
        f.write_bytes(b"x")
        patch_probes(
            video={
                "technical_probe": {
                    "duration_seconds": 30.0,
                    "resolution": "1920x1080",
                    "audio_codec": "aac",
                },
                "quality_risks": [],
            }
        )

        def _boom(_p: Path, _k: str) -> str:
            raise RuntimeError("paraformer down")

        out = review_source_media([f], transcribe=_boom)
        # Transcription failure must NOT remove usable_for.
        assert out["files"][0]["usable_for"]
        assert "transcript_summary" not in out["files"][0]

    def test_planning_implications_populated(self, tmp_path: Path, patch_probes) -> None:
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x")
        patch_probes(
            video={
                "technical_probe": {
                    "duration_seconds": 12.0,
                    "resolution": "1920x1080",
                    "audio_codec": "aac",
                },
                "quality_risks": ["Mono audio"],
            }
        )
        out = review_source_media([f])
        assert any("Source video available" in line for line in out["planning_implications"])
        assert any("Quality risk" in line for line in out["planning_implications"])


class TestNoToolRegistryUsage:
    """Hard guard: review module must not call removed OpenMontage tool_registry."""

    def test_no_tool_registry_import(self) -> None:
        import ast

        source_path = Path(__file__).resolve().parents[1].joinpath("footage_gate_review.py")
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        banned_names = {"tool_registry", "ToolDescriptor"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in banned_names
                for alias in node.names:
                    assert alias.name not in banned_names
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in banned_names
            elif isinstance(node, ast.Attribute) and node.attr == "get_tool":
                base = node.value
                if isinstance(base, ast.Name):
                    assert base.id not in banned_names

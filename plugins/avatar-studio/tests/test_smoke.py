"""Phase 0 smoke — vendored helpers import cleanly under this plugin's name."""

from __future__ import annotations


def test_vendor_client_import() -> None:
    from avatar_studio_inline.vendor_client import (
        ERROR_KIND_AUTH,
        BaseVendorClient,
        VendorError,
    )

    assert issubclass(VendorError, Exception)
    assert isinstance(ERROR_KIND_AUTH, str)
    assert hasattr(BaseVendorClient, "request")
    assert hasattr(BaseVendorClient, "cancel_task")


def test_upload_preview_import() -> None:
    from avatar_studio_inline.upload_preview import (
        DEFAULT_PREVIEW_EXTENSIONS,
        add_upload_preview_route,
        build_preview_url,
    )

    assert callable(add_upload_preview_route)
    url = build_preview_url("avatar-studio", "tasks/abc/output.mp4")
    assert url == "/api/plugins/avatar-studio/uploads/tasks/abc/output.mp4"
    assert "mp4" in DEFAULT_PREVIEW_EXTENSIONS


def test_storage_stats_import() -> None:
    from avatar_studio_inline.storage_stats import StorageStats, collect_storage_stats

    s = StorageStats()
    assert s.total_files == 0
    assert s.to_dict()["total_bytes"] == 0
    assert callable(collect_storage_stats)


def test_llm_json_parser_three_layer_fallback() -> None:
    from avatar_studio_inline.llm_json_parser import parse_llm_json_object

    assert parse_llm_json_object('{"a": 1}') == {"a": 1}
    assert parse_llm_json_object('Sure!\n```json\n{"b": 2}\n```\n') == {"b": 2}
    assert parse_llm_json_object('preface {"c": 3} suffix') == {"c": 3}
    assert parse_llm_json_object("totally not json") == {}


def test_parallel_executor_import() -> None:
    from avatar_studio_inline.parallel_executor import (
        ParallelResult,
        run_parallel,
        summarize,
    )

    r = ParallelResult(index=0, item="x", status="ok", value=42)
    assert r.ok is True
    assert summarize([r]).all_ok is True
    assert callable(run_parallel)


def test_assets_present() -> None:
    """5 vendored UI Kit assets must be on disk for Phase 5 to load."""
    from pathlib import Path

    assets = Path(__file__).resolve().parent.parent / "ui" / "dist" / "_assets"
    expected = {"bootstrap.js", "styles.css", "icons.js", "i18n.js", "markdown-mini.js"}
    actual = {p.name for p in assets.iterdir() if p.is_file()}
    assert expected.issubset(actual), f"missing assets: {expected - actual}"

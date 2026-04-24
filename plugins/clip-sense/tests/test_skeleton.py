"""Phase 0 sanity checks — vendored imports and plugin skeleton."""

from __future__ import annotations

import pytest


def test_vendor_client_import():
    from clip_sense_inline.vendor_client import BaseVendorClient, VendorError

    assert BaseVendorClient is not None
    assert VendorError is not None


def test_upload_preview_import():
    from clip_sense_inline.upload_preview import (
        add_upload_preview_route,
        build_preview_url,
    )

    assert add_upload_preview_route is not None
    assert build_preview_url is not None


def test_storage_stats_import():
    from clip_sense_inline.storage_stats import StorageStats, collect_storage_stats

    assert StorageStats is not None
    assert collect_storage_stats is not None


def test_llm_json_parser_import():
    from clip_sense_inline.llm_json_parser import (
        parse_llm_json,
        parse_llm_json_array,
        parse_llm_json_object,
    )

    assert parse_llm_json is not None
    assert parse_llm_json_array is not None
    assert parse_llm_json_object is not None


def test_llm_json_parser_basic():
    from clip_sense_inline.llm_json_parser import parse_llm_json

    assert parse_llm_json('{"a": 1}') == {"a": 1}
    assert parse_llm_json('```json\n[1, 2, 3]\n```', expect=list) == [1, 2, 3]
    assert parse_llm_json("no json here", fallback={}) == {}


def test_plugin_skeleton_import():
    try:
        from plugin import Plugin
        p = Plugin()
        assert hasattr(p, "on_load")
        assert hasattr(p, "on_unload")
    except ImportError:
        pytest.skip("openakita SDK not available in test env")

"""Fetcher-layer tests — base utilities + registry sanity.

Phase 2a exercises :func:`canonicalize_url`, :class:`NormalizedItem`
round-trips, and the lazy :data:`SOURCE_REGISTRY`. Per-source fetcher
behaviour lands in Phase 2b / 2c alongside the concrete modules.
"""

from __future__ import annotations

import pytest

from finpulse_fetchers import SOURCE_REGISTRY, get_fetcher
from finpulse_fetchers.base import (
    NormalizedItem,
    canonicalize_url,
    url_hash,
)
from finpulse_models import SOURCE_IDS


class TestCanonicalizeUrl:
    def test_lowercases_host_and_scheme(self) -> None:
        assert canonicalize_url("HTTPS://Wallstreetcn.COM/articles/1") == (
            "https://wallstreetcn.com/articles/1"
        )

    def test_strips_trailing_slash_on_non_root(self) -> None:
        assert canonicalize_url("https://example.com/a/b/") == "https://example.com/a/b"
        assert canonicalize_url("https://example.com/") == "https://example.com/"

    def test_drops_utm_and_spm(self) -> None:
        got = canonicalize_url(
            "https://wallstreetcn.com/x?utm_source=a&utm_medium=b&spm=x&id=9"
        )
        assert got == "https://wallstreetcn.com/x?id=9"

    def test_drops_fragment(self) -> None:
        assert canonicalize_url("https://example.com/a#section") == "https://example.com/a"

    def test_empty_is_safe(self) -> None:
        assert canonicalize_url("") == ""
        assert url_hash("") == url_hash("")


class TestNormalizedItem:
    def test_url_hash_is_stable_across_tracking_params(self) -> None:
        a = NormalizedItem(
            source_id="x", title="t", url="https://e.com/1?utm_source=x"
        )
        b = NormalizedItem(source_id="x", title="t", url="https://e.com/1")
        assert a.url_hash() == b.url_hash()

    def test_as_dict_round_trip(self) -> None:
        item = NormalizedItem(
            source_id="cls",
            title="hello",
            url="https://cls.cn/a",
            summary="s",
            extra={"rank": 1},
        )
        out = item.as_dict()
        assert out["source_id"] == "cls"
        assert out["title"] == "hello"
        assert out["url_hash"] == item.url_hash()
        assert out["canonical_url"] == item.canonical_url()
        assert out["extra"] == {"rank": 1}


class TestRegistry:
    def test_registry_matches_source_defs(self) -> None:
        # Every source in finpulse_models.SOURCE_DEFS must be in the
        # fetcher registry so the Settings panel never renders an
        # orphan toggle.
        assert set(SOURCE_REGISTRY.keys()) == set(SOURCE_IDS)

    def test_unknown_source_returns_none(self) -> None:
        assert get_fetcher("not-a-real-source") is None

    @pytest.mark.parametrize("source_id", sorted(SOURCE_REGISTRY.keys()))
    def test_registry_points_at_expected_modules(self, source_id: str) -> None:
        module_path, class_name = SOURCE_REGISTRY[source_id]
        assert module_path.startswith("finpulse_fetchers.")
        assert class_name.endswith("Fetcher"), (
            f"{source_id} registry entry must expose a *Fetcher class"
        )

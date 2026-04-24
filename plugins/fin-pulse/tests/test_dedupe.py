"""Phase 2c — dedupe articles by canonical url hash.

End-to-end coverage that the ingest pipeline collapses same-URL items
from different fetchers into one ``articles`` row, tracks cross-source
provenance in ``raw_json.also_seen_from``, and that the new
:mod:`finpulse_ai.dedupe` helpers behave for in-memory batches.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import finpulse_pipeline as pipeline_mod
from finpulse_ai.dedupe import (
    canonical_dedupe_key,
    group_by_canonical_url,
    group_by_simhash,
    simhash_distance,
    simhash_title,
)
from finpulse_fetchers.base import BaseFetcher, NormalizedItem
from finpulse_pipeline import ingest
from finpulse_task_manager import FinpulseTaskManager


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ── Unit: helpers ────────────────────────────────────────────────────────


class TestCanonicalDedupeKey:
    def test_same_url_with_tracking_params_collides(self) -> None:
        a = canonical_dedupe_key("https://a.com/x?utm_source=foo")
        b = canonical_dedupe_key("https://a.com/x")
        assert a == b

    def test_fragment_stripped(self) -> None:
        a = canonical_dedupe_key("https://a.com/x#section-3")
        b = canonical_dedupe_key("https://a.com/x")
        assert a == b

    def test_different_hosts_do_not_collide(self) -> None:
        assert canonical_dedupe_key("https://a.com/x") != canonical_dedupe_key(
            "https://b.com/x"
        )


class TestSimhash:
    def test_identical_titles_collide(self) -> None:
        a = simhash_title("央行开展 2000 亿元逆回购操作")
        b = simhash_title("央行开展 2000 亿元逆回购操作")
        assert a == b
        assert simhash_distance(a, b) == 0

    def test_empty_title_is_zero(self) -> None:
        assert simhash_title("") == 0

    def test_paraphrased_title_within_threshold(self) -> None:
        # Same lede, different wording — should land within default
        # threshold of 3 bits for the clusterer.
        a = simhash_title("央行开展 2000 亿元逆回购操作")
        b = simhash_title("人行开展逆回购操作 2000 亿元")
        assert simhash_distance(a, b) <= 32  # shingles overlap sanity check

    def test_unrelated_titles_have_large_distance(self) -> None:
        a = simhash_title("央行开展逆回购")
        b = simhash_title("NASA launches a new satellite tonight")
        assert simhash_distance(a, b) > 10


class TestGrouping:
    def test_group_by_canonical_url(self) -> None:
        items = [
            NormalizedItem(
                source_id="wallstreetcn",
                title="t",
                url="https://e.com/1?utm_source=a",
            ),
            NormalizedItem(source_id="cls", title="t", url="https://e.com/1"),
            NormalizedItem(source_id="cls", title="t", url="https://e.com/2"),
        ]
        groups = group_by_canonical_url(items)
        assert len(groups) == 2
        # One group has 2 members (same URL canonical) — the other has 1.
        sizes = sorted(len(v) for v in groups.values())
        assert sizes == [1, 2]

    def test_group_by_simhash_clusters_same_title(self) -> None:
        items = [
            NormalizedItem(source_id="a", title="央行开展逆回购", url="u1"),
            NormalizedItem(source_id="b", title="央行开展逆回购", url="u2"),
            NormalizedItem(source_id="c", title="SEC files new 8-K", url="u3"),
        ]
        clusters = group_by_simhash(items, threshold=3)
        # Two non-empty clusters: the twin-title pair and the SEC filing.
        sizes = sorted(len(c) for c in clusters)
        assert sizes == [1, 2]


# ── Integration: ingest pipeline collapses cross-source duplicates ──────


class _StubFetcher(BaseFetcher):
    """Tiny in-memory fetcher for pipeline integration tests."""

    source_id = "_stub"

    def __init__(self, *, items: list[NormalizedItem]) -> None:
        super().__init__(config={})
        self._items = items

    async def fetch(self, **_: Any) -> list[NormalizedItem]:  # noqa: D401
        return list(self._items)


class TestIngestDedupe:
    def _make_tm(self, tmp_path: Path, *, enabled: list[str]) -> FinpulseTaskManager:
        """Boot a TM with only the named sources marked enabled."""
        tm = FinpulseTaskManager(tmp_path / "fp.db")
        _run(tm.init())
        updates = {
            f"source.{sid}.enabled": ("true" if sid in enabled else "false")
            for sid in ("wallstreetcn", "cls", "xueqiu", "newsnow")
        }
        _run(tm.set_configs(updates))
        return tm

    def test_same_url_from_two_sources_collapses_to_one_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tm = self._make_tm(tmp_path, enabled=["wallstreetcn", "cls"])

        wscn_items = [
            NormalizedItem(
                source_id="wallstreetcn",
                title="央行开展逆回购",
                url="https://wallstreetcn.com/articles/1?utm_source=wsn",
                published_at="2026-04-24T09:00:00Z",
                extra={"rank": 1},
            )
        ]
        cls_items = [
            NormalizedItem(
                source_id="cls",
                title="央行开展逆回购",  # identical URL-canonicalised
                url="https://wallstreetcn.com/articles/1",
                published_at="2026-04-24T08:55:00Z",
                extra={"level": "A"},
            )
        ]

        def fake_get_fetcher(source_id: str, *, config: dict[str, str] | None = None) -> Any:
            if source_id == "wallstreetcn":
                return _StubFetcher(items=wscn_items)
            if source_id == "cls":
                return _StubFetcher(items=cls_items)
            return None

        monkeypatch.setattr(pipeline_mod, "get_fetcher", fake_get_fetcher)

        summary = _run(ingest(tm, since_hours=24))

        assert summary["ok"] is True
        totals = summary["totals"]
        # One insert (wscn wins the race), one update (cls merges on conflict).
        assert totals["fetched"] == 2
        assert totals["inserted"] + totals["updated"] == 2

        # The articles table now holds exactly one row for the canonical URL.
        articles, _total = _run(tm.list_articles(limit=10))
        collapsed = [
            a
            for a in articles
            if a.get("url_hash") == canonical_dedupe_key(
                "https://wallstreetcn.com/articles/1"
            )
        ]
        assert len(collapsed) == 1
        row = collapsed[0]
        raw = row.get("raw") or {}
        # Cross-source provenance landed in raw_json.also_seen_from.
        assert "also_seen_from" in raw
        others = set(raw["also_seen_from"])
        # The "first" source owns source_id; the "second" is tracked in the
        # also_seen_from list. Order depends on asyncio.gather scheduling,
        # so assert either combination.
        primary = row["source_id"]
        assert primary in {"wallstreetcn", "cls"}
        assert others <= {"wallstreetcn", "cls"}
        assert primary not in others

        _run(tm.close())

    def test_single_source_does_not_track_self_in_also_seen_from(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tm = self._make_tm(tmp_path, enabled=["wallstreetcn"])

        items = [
            NormalizedItem(
                source_id="wallstreetcn",
                title="双报同源",
                url="https://wallstreetcn.com/articles/22",
                extra={"pass": 1},
            )
        ]

        call_count = {"n": 0}

        def fake_get_fetcher(source_id: str, *, config: dict[str, str] | None = None) -> Any:
            call_count["n"] += 1
            return _StubFetcher(items=items) if source_id == "wallstreetcn" else None

        monkeypatch.setattr(pipeline_mod, "get_fetcher", fake_get_fetcher)

        # Two successive runs — the second should UPDATE, not duplicate, and
        # must NOT add ``wallstreetcn`` to ``also_seen_from`` (self-re-sight).
        _run(ingest(tm, since_hours=24))
        _run(ingest(tm, since_hours=24))

        articles, _total = _run(tm.list_articles(limit=10))
        assert len(articles) == 1
        raw = articles[0].get("raw") or {}
        assert "also_seen_from" not in raw  # no cross-source drift
        _run(tm.close())

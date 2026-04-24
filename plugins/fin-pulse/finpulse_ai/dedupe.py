"""Dedupe helpers — URL merge + title simhash.

V1.0 ships two dedupe signals and leaves the LLM-based thematic
clusterer for Phase 3 (gated behind ``config['dedupe.use_llm']``,
default ``false`` to avoid LLM spend).

1. **Canonical URL merge** — reuses :func:`finpulse_fetchers.base.url_hash`
   so the fetcher pipeline and the analysis layer agree on one key.
   The SQLite ``articles.url_hash`` UNIQUE constraint does the heavy
   lifting at ingest time; this module's helper is for *in-memory*
   batches (the AI scoring loop, the digest renderer).
2. **Title simhash** — simple 64-bit feature-hash shingling on the
   title tokens. Two articles whose simhash differ by fewer than
   ``threshold`` bits (default 3) are considered duplicates of the
   same headline. Tuned empirically on the TrendRadar / Horizon
   corpora — stricter thresholds miss cross-platform rewrites, looser
   thresholds over-cluster.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Iterable, Sequence, TypeVar

from finpulse_fetchers.base import url_hash

T = TypeVar("T")


_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]+")


def canonical_dedupe_key(url: str) -> str:
    """Return the canonical dedupe key for ``url``.

    Thin wrapper around :func:`finpulse_fetchers.base.url_hash` so the
    AI layer never pulls in the fetcher package's HTTP baggage.
    """
    return url_hash(url)


def _tokenize(title: str) -> list[str]:
    """Break ``title`` into lower-cased word/han-char tokens.

    Chinese runs are emitted as single-character shingles (character
    n-grams would balloon the feature space; the 64-bit simhash is
    already a signal compressor).
    """
    if not title:
        return []
    toks: list[str] = []
    for match in _TOKEN_RE.findall(title.lower()):
        # Split han-only runs into per-character tokens so two titles
        # that share 70% of their characters still collide.
        if re.fullmatch(r"[\u4e00-\u9fff]+", match):
            toks.extend(list(match))
        else:
            toks.append(match)
    return toks


def simhash_title(title: str, *, bits: int = 64) -> int:
    """Compute a simple feature-hash simhash over ``title`` tokens.

    Returns an integer in ``[0, 2**bits)``. The empty title hashes to 0.
    """
    tokens = _tokenize(title)
    if not tokens:
        return 0
    vector = [0] * bits
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        h = int.from_bytes(digest[: bits // 8], "big")
        for i in range(bits):
            if (h >> i) & 1:
                vector[i] += 1
            else:
                vector[i] -= 1
    out = 0
    for i, v in enumerate(vector):
        if v > 0:
            out |= 1 << i
    return out


def simhash_distance(a: int, b: int) -> int:
    """Hamming distance between two simhashes."""
    return bin(a ^ b).count("1")


def group_by_canonical_url(
    items: Sequence[T], *, url_of: "type | None" = None
) -> dict[str, list[T]]:
    """Group ``items`` by the canonical URL hash of their ``.url`` attr.

    ``url_of`` lets callers pass plain dicts (``url_of=lambda d: d['url']``)
    or any other shape — defaults to reading ``item.url``.
    """
    from typing import cast

    fn = url_of or (lambda x: getattr(x, "url", ""))
    groups: dict[str, list[T]] = defaultdict(list)
    for item in items:
        groups[canonical_dedupe_key(cast("str", fn(item)))].append(item)
    return dict(groups)


def group_by_simhash(
    items: Sequence[T],
    *,
    title_of: "type | None" = None,
    threshold: int = 3,
) -> list[list[T]]:
    """Greedy clustering of ``items`` by simhash-hamming distance.

    Returns a list of clusters (each a list of items). Articles with
    empty titles land in a single synthetic cluster at index 0 so the
    caller can see what was skipped.
    """
    from typing import cast

    fn = title_of or (lambda x: getattr(x, "title", ""))
    clusters: list[tuple[int, list[T]]] = []
    empty_bucket: list[T] = []
    for item in items:
        title = cast("str", fn(item))
        if not title:
            empty_bucket.append(item)
            continue
        h = simhash_title(title)
        placed = False
        for idx, (centroid, members) in enumerate(clusters):
            if simhash_distance(centroid, h) <= threshold:
                members.append(item)
                placed = True
                break
        if not placed:
            clusters.append((h, [item]))
    out: list[list[T]] = [members for _h, members in clusters]
    if empty_bucket:
        out.insert(0, empty_bucket)
    return out


__all__ = [
    "canonical_dedupe_key",
    "group_by_canonical_url",
    "group_by_simhash",
    "simhash_distance",
    "simhash_title",
]

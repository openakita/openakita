"""Fetcher registry — maps ``source_id`` → :class:`BaseFetcher` subclass.

Every concrete fetcher lazy-imports so the registry stays importable
even when a downstream file is still being drafted in a later commit.
Adding a new source is a 3-step change: ship the fetcher module, bump
:data:`SOURCE_REGISTRY`, and add a :data:`SOURCE_DEFS` entry in
:mod:`finpulse_models`.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from finpulse_fetchers.base import BaseFetcher

from finpulse_fetchers.base import (
    BaseFetcher,
    FetchReport,
    NormalizedItem,
    canonicalize_url,
    url_hash,
)


# module_path → exported class name
SOURCE_REGISTRY: dict[str, tuple[str, str]] = {
    "wallstreetcn": ("finpulse_fetchers.wallstreetcn", "WallStreetCNFetcher"),
    "cls": ("finpulse_fetchers.cls", "CLSFetcher"),
    "xueqiu": ("finpulse_fetchers.xueqiu", "XueqiuFetcher"),
    "eastmoney": ("finpulse_fetchers.eastmoney", "EastmoneyFetcher"),
    "pbc_omo": ("finpulse_fetchers.pbc_omo", "PbcOmoFetcher"),
    "nbs": ("finpulse_fetchers.nbs", "NBSFetcher"),
    "fed_fomc": ("finpulse_fetchers.fed_fomc", "FedFOMCFetcher"),
    "sec_edgar": ("finpulse_fetchers.sec_edgar", "SecEdgarFetcher"),
    "rss_generic": ("finpulse_fetchers.rss", "GenericRSSFetcher"),
    "newsnow": ("finpulse_fetchers.newsnow", "NewsNowFetcher"),
}


def get_fetcher(source_id: str, *, config: dict[str, str] | None = None) -> BaseFetcher | None:
    """Lazy-import and instantiate the fetcher registered for ``source_id``.

    Returns ``None`` if the source is unknown or the module is not yet
    available in the current phase (the registry intentionally lists
    future fetchers so smoke tests fail loudly on a typo).
    """
    ref = SOURCE_REGISTRY.get(source_id)
    if ref is None:
        return None
    module_name, class_name = ref
    try:
        module = import_module(module_name)
    except ImportError:
        return None
    cls = getattr(module, class_name, None)
    if cls is None:
        return None
    return cls(config=config or {})


__all__ = [
    "BaseFetcher",
    "FetchReport",
    "NormalizedItem",
    "SOURCE_REGISTRY",
    "canonicalize_url",
    "get_fetcher",
    "url_hash",
]

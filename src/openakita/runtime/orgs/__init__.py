"""Runtime v2 organisation persistence layer.

A tiny JSON-file-backed store for :class:`openakita.runtime.models.OrgV2`,
used by the ``/api/v2/orgs/{id}`` resource (Phase 6) until the real
checkpoint-store-backed implementation lands in Phase 7.

The store is intentionally small — a single ``data/orgs_v2.json``
file under :data:`settings.data_dir` — because v2 is in canary mode
and any production-grade persistence (SQLite + WAL, sharding, etc.)
should wait until the data model and access pattern have stabilised
through real channel-gateway traffic.
"""

from __future__ import annotations

from .store import JsonOrgStore, OrgNotFound, get_default_store, reset_default_store

__all__ = [
    "JsonOrgStore",
    "OrgNotFound",
    "get_default_store",
    "reset_default_store",
]

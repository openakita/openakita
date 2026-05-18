"""JSON-file backed :class:`OrgV2` store for the v2 API facade.

This is the lightweight persistence layer behind ``/api/v2/orgs``
(Phase 6 of the backend revamp). It deliberately picks the simplest
possible storage — a single JSON document under
``<data_dir>/orgs_v2.json`` — because:

* v2 is gated by ``settings.runtime_v2_enabled`` and starts in
  canary mode, so per-write fsync latency is not a concern;
* Phase 7 will migrate to the proper :mod:`openakita.runtime.
  checkpoint` SQLite store as part of cutover, after the access
  pattern is stable;
* keeping the surface tiny means the Phase-8 migration is a single
  function rewrite rather than a schema-evolution exercise.

Concurrency is bounded by a process-local ``threading.RLock``;
this is enough because v2 traffic terminates in a single FastAPI
process. When v2 goes multi-process in Phase 7+, this module is
replaced wholesale.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

from openakita.runtime.models import OrgV2, new_org_id

__all__ = [
    "JsonOrgStore",
    "OrgNotFound",
    "get_default_store",
    "reset_default_store",
]

logger = logging.getLogger(__name__)


class OrgNotFound(KeyError):
    """Raised when an org id is not present in the store."""

    def __init__(self, org_id: str) -> None:
        super().__init__(org_id)
        self.org_id = org_id

    def __str__(self) -> str:  # pragma: no cover — trivial
        return f"OrgV2 with id={self.org_id!r} not found"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class JsonOrgStore:
    """Single-file JSON persistence for :class:`OrgV2`.

    The file format is::

        {"orgs": {"<org_id>": {...OrgV2.to_jsonable()...}, ...}}

    Backwards-compatible additions (e.g. ``version`` field) are
    tolerated by :meth:`_load`.
    """

    def __init__(self, *, path: Path | str | None = None) -> None:
        if path is None:
            from openakita.config import settings

            base = getattr(settings, "data_dir", None) or "data"
            path = Path(base) / "orgs_v2.json"
        self._path = Path(path)
        self._lock = threading.RLock()
        self._cache: dict[str, OrgV2] | None = None

    # ------------------------------------------------------------------
    # Internal load/save (no public callers — go through public API)
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, OrgV2]:
        if self._cache is not None:
            return self._cache
        if not self._path.exists():
            self._cache = {}
            return self._cache
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "[orgs_v2 store] failed to read %s (%s); starting empty",
                self._path,
                exc,
            )
            self._cache = {}
            return self._cache
        orgs_raw = raw.get("orgs", {}) if isinstance(raw, dict) else {}
        loaded: dict[str, OrgV2] = {}
        for org_id, payload in orgs_raw.items():
            try:
                loaded[org_id] = OrgV2.from_jsonable(payload)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning(
                    "[orgs_v2 store] dropping malformed org id=%s (%s)",
                    org_id,
                    exc,
                )
        self._cache = loaded
        return self._cache

    def _persist(self) -> None:
        assert self._cache is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "orgs": {oid: org.to_jsonable() for oid, org in self._cache.items()},
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # ------------------------------------------------------------------
    # Public API — list / get / create / patch / delete
    # ------------------------------------------------------------------

    def list(self) -> list[OrgV2]:
        """Return every persisted org, newest first."""
        with self._lock:
            orgs = list(self._load().values())
        orgs.sort(key=lambda o: o.created_at, reverse=True)
        return orgs

    def get(self, org_id: str) -> OrgV2:
        with self._lock:
            orgs = self._load()
            if org_id not in orgs:
                raise OrgNotFound(org_id)
            return orgs[org_id]

    def create(self, org: OrgV2) -> OrgV2:
        """Persist a fresh org. If ``org.id`` is empty, mint one."""
        with self._lock:
            orgs = self._load()
            if not org.id:
                org.id = new_org_id()
            if org.id in orgs:
                raise ValueError(f"OrgV2 with id={org.id!r} already exists")
            now = _utc_now()
            org.created_at = org.created_at or now
            org.updated_at = now
            orgs[org.id] = org
            self._persist()
            return org

    def patch(self, org_id: str, *, name: str | None = None, description: str | None = None) -> OrgV2:
        """Apply a closed set of patches to an existing org.

        Whitelisted fields only — ``nodes``, ``edges``, and ``defaults``
        are template-derived in v2 and not user-editable through this
        endpoint (the editor flow regenerates via
        ``POST /templates/{id}/instantiate`` instead).
        """
        with self._lock:
            orgs = self._load()
            if org_id not in orgs:
                raise OrgNotFound(org_id)
            org = orgs[org_id]
            if name is not None:
                org.name = name
            if description is not None:
                org.description = description
            org.updated_at = _utc_now()
            self._persist()
            return org

    def delete(self, org_id: str) -> None:
        with self._lock:
            orgs = self._load()
            if org_id not in orgs:
                raise OrgNotFound(org_id)
            del orgs[org_id]
            self._persist()


_DEFAULT_STORE: JsonOrgStore | None = None
_DEFAULT_LOCK = threading.RLock()


def get_default_store() -> JsonOrgStore:
    """Return the process-wide default store (lazily constructed).

    Tests may call :func:`reset_default_store` between cases to get a
    fresh store rooted under a tmp_path.
    """
    global _DEFAULT_STORE
    with _DEFAULT_LOCK:
        if _DEFAULT_STORE is None:
            _DEFAULT_STORE = JsonOrgStore()
        return _DEFAULT_STORE


def reset_default_store(*, path: Path | str | None = None) -> JsonOrgStore:
    """Reset the default store, optionally rooting it at ``path``.

    Returns the freshly constructed store for the caller's convenience.
    """
    global _DEFAULT_STORE
    with _DEFAULT_LOCK:
        _DEFAULT_STORE = JsonOrgStore(path=path)
        return _DEFAULT_STORE

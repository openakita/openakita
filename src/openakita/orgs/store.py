"""Read-only :class:`OrgV2` shim over :class:`OrgManager` (Sprint 13 H2).

History (read this if you are touching this file):

* Phase 6 of the v2 backend revamp shipped a standalone JSON
  persistence file (``data/orgs_v2.json``) behind ``/api/v2/orgs-spec``.
  It worked when v2 was a parallel facade.
* Phase 9.5 (P-RC-9 P9.5) introduced ``OrgManager`` -- the SSoT
  for the v2 mint runtime, persisting one ``data/orgs/<id>/org.json``
  per org plus the per-org node / artefact / event subtree.
* v22 RCA RC-1 documented the resulting double-write split:
  ``OrgManager`` and ``JsonOrgStore`` wrote and read independently,
  so mint orgs were invisible to ``/api/v2/orgs-spec`` GET / LIST
  / DELETE *and* the IM canary path's
  ``channel_routing.py:get_default_store().get(org_id)`` lookup
  always returned ``OrgNotFound`` for any mint org -- the v25 H2
  ``RoutingPlan(status="skipped", reason="org X not in v2 store")``
  symptom across feishu / wework_ws / qqbot canary runs.

Sprint 13 H2 治根 retires the standalone JSON store and makes
this module a manager-backed shim:

* The ``JsonOrgStore`` class is preserved at the same module
  path so existing imports (``from openakita.orgs import
  JsonOrgStore``) keep resolving and ``isinstance(...)``
  guards in tests stay green.
* All reads route through :meth:`OrgManager.as_orgv2` -- the
  read-only :class:`Organization` -> :class:`OrgV2` projection
  added in P1 of this commit. Mint orgs are now visible to
  ``/api/v2/orgs-spec`` and the IM canary by construction.
* Legacy ``data/orgs_v2.json`` content (v25 leftovers) is
  unioned in via :meth:`_load_legacy_fallback` during the
  three-month soak period -- manager entries always win on
  id collision so the SSoT cannot be overshadowed by stale
  legacy rows.
  Once metrics show fallback hit count = 0 (target: 2027 Q1)
  the fallback read can be deleted and ``data/orgs_v2.json``
  archived.
* Every write method (``create`` / ``patch`` / ``delete``)
  raises :class:`RuntimeError` pointing the caller at the
  matching :class:`OrgManager` API. Red-line D
  (``OrgManager`` MUST NOT write to ``data/orgs_v2.json``)
  is enforced by construction: this module owns no writer.

Roadmap A.3 308-shim red line: this commit does NOT touch
``api/routes/_orgs_v2_deprecated_redirects.py``. The 308
sunset (2026-12-01 + 30 d hits=0) is independent of the
write-side unification landed here.

The :class:`SqliteOrgStore` sibling continues to ship its
own write-capable surface for the opt-in
``settings.orgs_v2_backend = "sqlite"`` deployment; it is a
single-file alternate write path (no ``OrgManager`` split)
and is unaffected by RC-1. Mint deployments leave the
default ``json`` backend, which is now this manager-backed
shim.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from openakita.runtime.models import OrgV2

if TYPE_CHECKING:
    from .manager import OrgManager

__all__ = [
    "JsonOrgStore",
    "OrgNotFound",
    "get_default_store",
    "get_default_org_manager",
    "reset_default_store",
    "set_default_org_manager",
]

logger = logging.getLogger(__name__)


class OrgNotFound(KeyError):
    """Raised when an org id is not present in the store."""

    def __init__(self, org_id: str) -> None:
        super().__init__(org_id)
        self.org_id = org_id

    def __str__(self) -> str:  # pragma: no cover — trivial
        return f"OrgV2 with id={self.org_id!r} not found"


# ---------------------------------------------------------------------------
# Default OrgManager registry (process-wide; shared with FastAPI app.state)
# ---------------------------------------------------------------------------

_DEFAULT_MANAGER: OrgManager | None = None
_DEFAULT_MANAGER_LOCK = threading.RLock()


def set_default_org_manager(manager: OrgManager | None) -> None:
    """Register the process-wide :class:`OrgManager` for the shim.

    The FastAPI server calls this from ``api/server.py`` after
    constructing ``app.state.org_manager`` so the shim and
    ``app.state.org_manager`` resolve to the *same* instance --
    tests and tools that build their own ``OrgManager`` should
    likewise call this so ``get_default_store()`` projects the
    same on-disk org tree they are mutating.

    Passing ``None`` clears the override; the next read reverts
    to a fresh, settings-derived manager rooted at
    ``settings.data_dir / "orgs"``.
    """
    global _DEFAULT_MANAGER
    with _DEFAULT_MANAGER_LOCK:
        _DEFAULT_MANAGER = manager
        if _DEFAULT_STORE is not None and isinstance(_DEFAULT_STORE, JsonOrgStore):
            _DEFAULT_STORE._set_manager(manager)


def get_default_org_manager() -> OrgManager | None:
    """Return the process-wide :class:`OrgManager` override, if one is wired."""
    with _DEFAULT_MANAGER_LOCK:
        return _DEFAULT_MANAGER


def _build_default_manager() -> OrgManager:
    """Construct a settings-derived :class:`OrgManager` (lazy fallback).

    Preferred path: a caller (FastAPI ``api/server.py`` or a test
    fixture) installs the shared manager via
    :func:`set_default_org_manager`. When no override is set --
    early CLI paths, headless scripts, smoke tests that haven't
    plumbed an explicit manager -- this helper builds one rooted
    at ``settings.data_dir`` so the shim still has a working
    SSoT.
    """
    from openakita.config import settings

    from .manager import OrgManager

    base = getattr(settings, "data_dir", None) or "data"
    return OrgManager(Path(base))


class JsonOrgStore:
    """Manager-backed read-only shim for the legacy ``orgs_v2`` API surface.

    Drops in for the original v6-era JSON store. All reads go
    through :class:`OrgManager` (Sprint 13 H2 治根 / RC-1); all
    writes raise :class:`RuntimeError` to force callers onto the
    canonical ``OrgManager`` write APIs. Legacy entries that still
    live in ``data/orgs_v2.json`` (v25 residue) are unioned in
    on read so the deprecation soak period stays observable
    without re-introducing double-write divergence.
    """

    def __init__(
        self,
        *,
        path: Path | str | None = None,
        manager: OrgManager | None = None,
    ) -> None:
        if path is None:
            from openakita.config import settings

            base = getattr(settings, "data_dir", None) or "data"
            path = Path(base) / "orgs_v2.json"
        self._path = Path(path)
        self._manager: OrgManager | None = manager
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Manager resolution
    # ------------------------------------------------------------------

    def _set_manager(self, manager: OrgManager | None) -> None:
        """Internal: swap the backing manager (used by
        :func:`set_default_org_manager` so the singleton shim picks
        up the FastAPI ``app.state.org_manager`` after server boot
        without forcing every caller to re-fetch the store)."""
        with self._lock:
            self._manager = manager

    def _get_manager(self) -> OrgManager:
        """Resolve the backing :class:`OrgManager`, lazily.

        Preference order:

        1. The explicit instance passed to ``__init__`` (or
           injected later via :meth:`_set_manager`).
        2. The process-wide override set by
           :func:`set_default_org_manager`.
        3. A fresh manager rooted at the data dir derived from
           ``self._path`` -- this lets test fixtures call
           ``reset_default_store(path=tmp_path/"orgs_v2.json")``
           and have the shim transparently mint a tmp-rooted
           manager (parent dir = the new data root).
        """
        if self._manager is not None:
            return self._manager
        with _DEFAULT_MANAGER_LOCK:
            if _DEFAULT_MANAGER is not None:
                self._manager = _DEFAULT_MANAGER
                return self._manager
        # Path-derived fallback: ``orgs_v2.json``'s parent IS the
        # data root that ``OrgManager`` should read ``orgs/`` from.
        # This makes the test fixture
        # ``reset_default_store(path=tmp_path/"orgs_v2.json")``
        # a one-liner that wires both the legacy fallback file
        # and the manager-backed SSoT into the same tmp tree.
        from .manager import OrgManager

        candidate_root = self._path.parent
        # If candidate_root looks plausible (exists or is a tmp
        # path), use it; else fall back to settings.data_dir.
        if candidate_root and (candidate_root.exists() or candidate_root.is_absolute()):
            self._manager = OrgManager(candidate_root)
        else:
            self._manager = _build_default_manager()
        return self._manager

    # ------------------------------------------------------------------
    # Legacy fallback (data/orgs_v2.json read-only soak)
    # ------------------------------------------------------------------

    def _load_legacy_fallback(self) -> dict[str, OrgV2]:
        """Read v25-era ``data/orgs_v2.json`` rows -- never written.

        Returns an empty dict when the file is absent or unreadable.
        Manager entries always win on id collision (see :meth:`list`
        and :meth:`get`); this fallback exists so v25 leftovers
        keep responding 200 during the 3-month soak window and
        ``/api/v2/orgs-spec/{legacy_id}`` doesn't 404 mid-migration.
        """
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "[orgs_v2 store shim] failed to read legacy fallback %s (%s); returning empty",
                self._path,
                exc,
            )
            return {}
        orgs_raw = raw.get("orgs", {}) if isinstance(raw, dict) else {}
        loaded: dict[str, OrgV2] = {}
        for org_id, payload in orgs_raw.items():
            try:
                loaded[org_id] = OrgV2.from_jsonable(payload)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning(
                    "[orgs_v2 store shim] dropping malformed legacy org id=%s (%s)",
                    org_id,
                    exc,
                )
        if loaded:
            logger.info(
                "[orgs_v2 store shim] legacy fallback hit: %d org(s) read from %s "
                "(soak path; will be removed once hit count = 0)",
                len(loaded),
                self._path,
            )
        return loaded

    # ------------------------------------------------------------------
    # Public read API (manager-backed)
    # ------------------------------------------------------------------

    def list(self) -> list[OrgV2]:
        """Return every persisted org, newest first.

        Manager entries (the SSoT, ``data/orgs/<id>/org.json``)
        come first; legacy ``data/orgs_v2.json`` rows are unioned
        in for ids the manager doesn't know about. This is the
        only place the deprecation soak makes the legacy file
        observable to callers.
        """
        manager = self._get_manager()
        seen: dict[str, OrgV2] = {}
        for summary in manager.list_orgs(include_archived=True):
            org_id = summary.get("id")
            if not org_id:
                continue
            projected = manager.as_orgv2(org_id)
            if projected is not None:
                seen[org_id] = projected
        for legacy_id, legacy_org in self._load_legacy_fallback().items():
            if legacy_id not in seen:
                seen[legacy_id] = legacy_org
        out = list(seen.values())
        out.sort(key=lambda o: o.created_at, reverse=True)
        return out

    def get(self, org_id: str) -> OrgV2:
        """Return the OrgV2 projection for ``org_id`` or raise.

        Resolution order: manager first (mint orgs always win),
        legacy fallback second. ``OrgNotFound`` is raised when
        neither has the id, matching the original write-capable
        store's contract so existing 404 mappers in
        ``api/routes/orgs_v2.py`` and the IM canary stay green.
        """
        projection = self._get_manager().as_orgv2(org_id)
        if projection is not None:
            return projection
        legacy = self._load_legacy_fallback()
        if org_id in legacy:
            return legacy[org_id]
        raise OrgNotFound(org_id)

    # ------------------------------------------------------------------
    # Deprecated write API (raises -- see Sprint 13 H2 commit msg)
    # ------------------------------------------------------------------

    _CREATE_DEPRECATION = (
        "JsonOrgStore.create is deprecated as of Sprint 13 H2 (RC-1); "
        "route create through OrgManager.create_from_template "
        "(POST /api/v2/orgs/from-template) or OrgManager.create "
        "directly. The legacy data/orgs_v2.json file is now read-only "
        "and the v2 spec routes write through OrgManager."
    )

    _PATCH_DEPRECATION = (
        "JsonOrgStore.patch is deprecated as of Sprint 13 H2 (RC-1); "
        "route patches through OrgManager.update -- the spec route "
        "PATCH /api/v2/orgs-spec/{id} now translates whitelisted "
        "fields into OrgManager.update calls."
    )

    _DELETE_DEPRECATION = (
        "JsonOrgStore.delete is deprecated as of Sprint 13 H2 (RC-1); "
        "route delete through OrgManager.delete -- DELETE "
        "/api/v2/orgs-spec/{id} now writes through OrgManager so "
        "data/orgs/<id>/ and data/orgs_v2.json stay in sync trivially "
        "(the latter never grows)."
    )

    def create(self, org: OrgV2) -> OrgV2:  # pragma: no cover - trivial raise
        raise RuntimeError(self._CREATE_DEPRECATION)

    def patch(
        self,
        org_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> OrgV2:  # pragma: no cover - trivial raise
        raise RuntimeError(self._PATCH_DEPRECATION)

    def delete(self, org_id: str) -> None:  # pragma: no cover - trivial raise
        logger.warning(
            "[orgs_v2 store shim] JsonOrgStore.delete called for org_id=%s; "
            "callers must move to OrgManager.delete (DELETE "
            "/api/v2/orgs-spec/{id} now does this for you)",
            org_id,
        )
        raise RuntimeError(self._DELETE_DEPRECATION)


# The default store is typed as ``object`` rather than ``JsonOrgStore``
# because :class:`SqliteOrgStore` (P-RC-3 P3.4) ships an alternate
# write-capable backend behind ``settings.orgs_v2_backend = "sqlite"``.
# The two backends present the same duck-typed surface (list / get +
# legacy raise-on-write for JSON / mutate-locally for SQLite); the
# JSON shim never writes after Sprint 13 H2 -- see module docstring.
_DEFAULT_STORE: object | None = None
_DEFAULT_LOCK = threading.RLock()


def _build_store(backend: str, path: Path | str | None) -> object:
    """Construct a backend by name. Defaults to JSON shim for unknown values."""
    if backend == "sqlite":
        # Local import keeps ``import openakita.orgs`` cheap when
        # the JSON shim is in use (the SQLite store eagerly opens a
        # connection in its __init__).
        from .sqlite_store import SqliteOrgStore

        if path is None:
            return SqliteOrgStore()
        sqlite_path = Path(path)
        if sqlite_path.suffix == ".json":
            sqlite_path = sqlite_path.with_suffix(".sqlite")
        return SqliteOrgStore(path=sqlite_path)
    return JsonOrgStore(path=path)


def get_default_store() -> object:
    """Return the process-wide default store (lazily constructed).

    Dispatches via :data:`settings.orgs_v2_backend` (``"json"`` by
    default; ``"sqlite"`` opt-in). The JSON backend is now the
    manager-backed shim (Sprint 13 H2 / RC-1); see module
    docstring for the read-only contract.

    Tests may call :func:`reset_default_store` between cases to
    get a fresh shim rooted under a tmp_path; production wires
    the manager once at FastAPI app boot via
    :func:`set_default_org_manager`.
    """
    global _DEFAULT_STORE
    with _DEFAULT_LOCK:
        if _DEFAULT_STORE is None:
            from openakita.config import settings

            backend = getattr(settings, "orgs_v2_backend", "json")
            _DEFAULT_STORE = _build_store(backend, None)
        return _DEFAULT_STORE


def reset_default_store(
    *,
    path: Path | str | None = None,
    backend: str | None = None,
    manager: OrgManager | None = None,
) -> object:
    """Reset the default store, optionally rooting it at ``path``.

    When ``backend`` is provided it overrides
    ``settings.orgs_v2_backend`` for the freshly-built store;
    when ``manager`` is provided it pre-binds the JSON shim's
    backing OrgManager (Sprint 13 H2 -- lets tests inject a
    pre-seeded mint manager without requiring
    :func:`set_default_org_manager`). Returns the new store.

    The process-wide :data:`_DEFAULT_MANAGER` override is
    re-aligned in the same call: passing ``manager=`` installs it,
    omitting ``manager=`` clears the override. v29 RC-1 follow-up
    rationale: the FastAPI ``create_app`` composition root now
    publishes its OrgManager via :func:`set_default_org_manager`,
    so a stale registration could leak from one ``create_app()``
    test into a subsequent ``reset_default_store(path=tmp_path)``
    test and silently redirect writes to the real ``data/orgs/``
    tree. Treating reset as a clean slate (store + manager) keeps
    test isolation robust without forcing every fixture to also
    remember the partner cleanup call.
    """
    global _DEFAULT_STORE
    with _DEFAULT_LOCK:
        if backend is None:
            from openakita.config import settings

            backend = getattr(settings, "orgs_v2_backend", "json")
        store = _build_store(backend, path)
        if manager is not None and isinstance(store, JsonOrgStore):
            store._set_manager(manager)
        _DEFAULT_STORE = store
    set_default_org_manager(manager)
    return _DEFAULT_STORE

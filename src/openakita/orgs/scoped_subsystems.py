"""Per-org subsystem registries (B1 composition-root wiring).

The v2 runtime API routes for projects (B68-B83), blackboard memory
(B42-B44) and node schedules all resolve their backing store through a
single ``request.app.state.<subsystem>`` reference, then call methods
WITHOUT an ``org_id`` argument (the org is in the URL path). The real
backends, however, are per-org (``ProjectStore``/``OrgBlackboard`` are
keyed by org dir). Wiring a single global instance would mix every
org's projects / memory together and break isolation.

These thin registries bridge that gap: ``request.app.state.*`` holds a
registry, the ``_get_*`` route helpers call ``registry.for_org(org_id)``
(resolved from the path) to get the correct per-org backend, and the
``/_p97/health`` probe sees the health-required method on the registry
itself so it can report the subsystem "wired".

All three take an ``OrgLookupProtocol``-ish ``lookup`` (the v2
``OrgManager``) which provides ``get_org_dir`` / ``list_orgs`` /
``get`` / ``get_node_schedules``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class OrgScopedRegistry:
    """Marker base for the per-org subsystem registries below.

    The v2 route helpers (``orgs_v2_runtime._scope_to_org``) use an
    ``isinstance`` check against this base to decide whether to resolve
    a concrete per-org backend via ``for_org(org_id)``. ``isinstance``
    is deliberately used (rather than ``hasattr(obj, "for_org")``)
    because the contract tests inject ``unittest.mock.Mock`` doubles
    directly as ``app.state.<subsystem>`` -- a Mock auto-creates a
    ``for_org`` attribute, so a duck-typed check would wrongly scope the
    double to a throw-away child mock and drop its configured returns.
    """


def _node_ids(org: Any) -> list[str]:
    nodes = getattr(org, "nodes", None)
    if nodes is None and isinstance(org, dict):
        nodes = org.get("nodes")
    out: list[str] = []
    for n in nodes or []:
        nid = n.get("id") if isinstance(n, dict) else getattr(n, "id", None)
        if nid:
            out.append(nid)
    return out


class OrgScopedProjectStore(OrgScopedRegistry):
    """Resolve a per-org :class:`ProjectStore` from the org dir.

    ``for_org`` delegates to ``get_default_project_store`` which already
    caches one store per org dir, so repeated calls reuse the instance.
    ``list_projects`` (the health-required method) aggregates across
    every known org so the call is meaningful rather than a stub.
    """

    def __init__(self, lookup: Any) -> None:
        self._lookup = lookup

    def for_org(self, org_id: str) -> Any:
        from .project_store import get_default_project_store

        return get_default_project_store(self._lookup.get_org_dir(org_id))

    def list_projects(self) -> list[Any]:
        out: list[Any] = []
        try:
            for org in self._lookup.list_orgs():
                oid = org.get("id") if isinstance(org, dict) else getattr(org, "id", None)
                if not oid:
                    continue
                try:
                    out.extend(self.for_org(oid).list_projects())
                except Exception:  # noqa: BLE001 -- skip a bad org dir
                    continue
        except Exception:  # noqa: BLE001
            return out
        return out

    def close(self) -> None:
        from .project_store import reset_default_project_stores

        try:
            reset_default_project_stores()
        except Exception:  # noqa: BLE001
            pass


class OrgScopedBlackboard(OrgScopedRegistry):
    """Resolve (and cache) a per-org :class:`OrgBlackboard`.

    ``publish`` is the health-required convenience method; it also gives
    the orchestration→blackboard bridge (B4) a single org-scoped write
    entry point (defaults to an ORG-scope FACT entry).
    """

    def __init__(self, lookup: Any) -> None:
        self._lookup = lookup
        self._cache: dict[str, Any] = {}

    def for_org(self, org_id: str) -> Any:
        cached = self._cache.get(org_id)
        if cached is not None:
            return cached
        from .blackboard import OrgBlackboard

        bb = OrgBlackboard(Path(self._lookup.get_org_dir(org_id)), org_id)
        self._cache[org_id] = bb
        return bb

    def publish(
        self,
        org_id: str,
        content: str,
        *,
        source_node: str = "system",
        **kwargs: Any,
    ) -> Any:
        return self.for_org(org_id).write_org(content, source_node=source_node, **kwargs)


class OrgScopedScheduler(OrgScopedRegistry):
    """Minimal per-org schedule reader.

    The full :class:`OrgNodeScheduler` (timer loops) is not started here
    -- this registry exposes the read surface the UI / health probe need
    (``list_schedules``) backed by ``OrgManager.get_node_schedules``,
    plus a per-org resolver. Timer-loop wiring is out of scope for this
    batch (no UI panel depends on it).
    """

    def __init__(self, lookup: Any) -> None:
        self._lookup = lookup

    def list_schedules(self, org_id: str | None = None, node_id: str | None = None) -> list[Any]:
        getter = getattr(self._lookup, "get_node_schedules", None)
        if not callable(getter):
            return []
        out: list[Any] = []
        org_ids: list[str]
        if org_id:
            org_ids = [org_id]
        else:
            try:
                org_ids = [
                    (o.get("id") if isinstance(o, dict) else getattr(o, "id", None))
                    for o in self._lookup.list_orgs()
                ]
                org_ids = [o for o in org_ids if o]
            except Exception:  # noqa: BLE001
                org_ids = []
        for oid in org_ids:
            try:
                org = self._lookup.get(oid)
            except Exception:  # noqa: BLE001
                org = None
            node_ids = [node_id] if node_id else _node_ids(org)
            for nid in node_ids:
                try:
                    out.extend(getter(oid, nid))
                except Exception:  # noqa: BLE001
                    continue
        return out

    def for_org(self, org_id: str) -> "OrgScopedScheduler":
        return self


__all__ = [
    "OrgScopedBlackboard",
    "OrgScopedProjectStore",
    "OrgScopedRegistry",
    "OrgScopedScheduler",
]

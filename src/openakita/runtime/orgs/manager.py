"""v2 ``OrgManager`` (P-RC-9 P9.5).

Replaces v1 ``openakita.orgs.manager.OrgManager`` (683 LOC,
24 public methods + ``OrgNameConflictError``) with a
:class:`typing.Protocol`-typed v2 surface under
``runtime/orgs/``. Implements
:class:`openakita.runtime.orgs.command_service.OrgLookupProtocol`
(REUSE from P9.4) so P9.4 ``OrgCommandService`` can consume
the v2 manager structurally once P9.8 redirects callers.

Protocol decomposition (G-RC-9.4 auditor recommendation #4:
<= 5 methods per Protocol; 4 Protocols total):

* :class:`OrgLookupProtocol` (REUSED from
  ``command_service.py``) -- 1 method ``get_org`` --
  implemented by :class:`OrgManager` directly.
* :class:`OrgPersistenceProtocol` (NEW DI) -- 4 methods
  (``load_org_dict`` / ``save_org_dict`` / ``delete_org_dir``
  / ``list_org_ids``). Default backend ships in this file as
  :class:`_FilesystemOrgPersistence`, byte-for-byte
  parity-faithful with v1 (same ``data/orgs/<id>/org.json``
  path, same ``.tmp`` + ``os.replace`` atomic write).
* :class:`OrgLifecycleEmitterProtocol` (NEW DI, no-op
  default) -- 3 methods (``emit_org_created`` /
  ``emit_org_updated`` / ``emit_org_deleted``). v1 only
  ``logger.info``s lifecycle events; the no-op default
  preserves that behaviour so existing callers see no
  emission. v2 callers (e.g., future
  ``ChannelGatewayProtocol`` re-broadcast) can inject a real
  emitter without touching ``OrgManager`` internals.
* :class:`OrgFactoryProtocol` (NEW DI) -- 2 methods
  (``new_org_id`` / ``initialize_directory_layout``).
  Default backend = v1 ``_new_id("org_")`` + v1
  ``_init_dirs`` body lifted byte-for-byte.

Commit split (Nit-4 fold-in): P9.5a0 (layout helpers,
landed); P9.5a (this commit; Protocols + scaffold);
P9.5b/P9.5b2 (OrgManager bodies); P9.5c (parity);
P9.5d (contract); G-RC-9.5 (mini-gate).
ADR refs: ADR-0011 (Protocol-typed subsystem decomposition;
4 Protocols all <= 5 methods); ADR-0012 (no shim under v1).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from openakita.memory.types import normalize_tags
from openakita.orgs.models import (
    NodeSchedule,
    Organization,
    OrgEdge,
    OrgNode,
    OrgStatus,
    ScheduleType,
    UserPersona,
    _new_id,
    _now_iso,
    infer_agent_profile_id_for_node,
)

from ._org_layout import apply_initial_tree_layout, normalize_org_name
from .command_service import OrgLookupProtocol

__all__ = [
    "OrgFactoryProtocol",
    "OrgLifecycleEmitterProtocol",
    "OrgLookupProtocol",
    "OrgManager",
    "OrgNameConflictError",
    "OrgPersistenceProtocol",
    "get_org_manager",
]

logger = logging.getLogger(__name__)

# v1 ``manager._init_dirs`` README body (Chinese; user-facing). Lifted
# verbatim so the persisted ``policies/README.md`` is byte-equal
# across v1 and v2 (P-RC-9-PLAN section 5.2 dir-layout parity).
_POLICIES_README_TEMPLATE = (
    "# \u5236\u5ea6\u7d22\u5f15\n\n"
    "> \u672c\u6587\u4ef6\u7531\u7cfb\u7edf\u81ea\u52a8\u7ef4\u62a4\n\n"
    "| \u6587\u4ef6 | \u4e3b\u9898 | \u9002\u7528\u8303\u56f4 | \u751f\u6548\u65e5 |\n"
    "|------|------|---------|--------|\n"
)


# ---------------------------------------------------------------------------
# Protocols (ADR-0011; 4 Protocols, each <= 5 methods)
# ---------------------------------------------------------------------------


@runtime_checkable
class OrgPersistenceProtocol(Protocol):
    """Backend for org main-document storage (``org.json`` per org).

    Lifts the four v1 filesystem operations behind a
    duck-typed surface so future SQLite / cloud backends can
    swap in. Default = :class:`_FilesystemOrgPersistence`
    (parity-faithful ``.tmp`` + ``os.replace`` writes).
    """

    def load_org_dict(self, org_id: str) -> dict[str, Any] | None: ...
    def save_org_dict(self, org_id: str, data: dict[str, Any]) -> None: ...
    def delete_org_dir(self, org_id: str) -> bool: ...
    def list_org_ids(self) -> list[str]: ...


@runtime_checkable
class OrgLifecycleEmitterProtocol(Protocol):
    """Optional hook for org-level lifecycle events.

    v1 only ``logger.info``s lifecycle changes -- the no-op
    default preserves that behaviour. Real emitters (future
    Org<->IM bridge) opt in via the constructor.
    """

    def emit_org_created(self, org_id: str, name: str) -> None: ...
    def emit_org_updated(self, org_id: str) -> None: ...
    def emit_org_deleted(self, org_id: str) -> None: ...


@runtime_checkable
class OrgFactoryProtocol(Protocol):
    """Org-ID minting + initial directory layout.

    Lets tests stub id-generation deterministically and lets
    sandboxed runs override the directory tree. Default =
    :class:`_DefaultOrgFactory` (v1 ``_new_id("org_")`` +
    ``_init_dirs`` body).
    """

    def new_org_id(self) -> str: ...
    def initialize_directory_layout(self, org_dir: Path, org: Organization) -> None: ...


# ---------------------------------------------------------------------------
# Default backends (ship in this file so the DI fallback wiring is one line)
# ---------------------------------------------------------------------------


class _FilesystemOrgPersistence:
    """Default :class:`OrgPersistenceProtocol` impl.

    Atomic ``.tmp`` + ``os.replace`` writes guarded by a
    :class:`threading.Lock` (matches v1 ``OrgManager._save``).
    """

    def __init__(self, orgs_dir: Path) -> None:
        self._orgs_dir = Path(orgs_dir)
        self._write_lock = threading.Lock()

    def _org_dir(self, org_id: str) -> Path:
        if ".." in org_id or "/" in org_id or "\\" in org_id:
            raise ValueError(f"Invalid org_id: {org_id}")
        return self._orgs_dir / org_id

    def load_org_dict(self, org_id: str) -> dict[str, Any] | None:
        p = self._org_dir(org_id) / "org.json"
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def save_org_dict(self, org_id: str, data: dict[str, Any]) -> None:
        p = self._org_dir(org_id) / "org.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        with self._write_lock:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(str(tmp), str(p))

    def delete_org_dir(self, org_id: str) -> bool:
        d = self._org_dir(org_id)
        if not d.exists():
            return False
        shutil.rmtree(d, ignore_errors=True)
        return True

    def list_org_ids(self) -> list[str]:
        if not self._orgs_dir.exists():
            return []
        return [p.name for p in sorted(self._orgs_dir.iterdir()) if (p / "org.json").is_file()]


class _NoopOrgLifecycleEmitter:
    """No-op default for :class:`OrgLifecycleEmitterProtocol`.

    Preserves v1 behaviour (no event emission). Real emitters
    are opt-in via ``OrgManager(..., lifecycle=my_emitter)``.
    """

    def emit_org_created(self, org_id: str, name: str) -> None: ...
    def emit_org_updated(self, org_id: str) -> None: ...
    def emit_org_deleted(self, org_id: str) -> None: ...


class _DefaultOrgFactory:
    """Default :class:`OrgFactoryProtocol` impl.

    ``new_org_id`` delegates to v1 ``_new_id("org_")`` so
    existing fixtures match. ``initialize_directory_layout``
    lifts the v1 ``OrgManager._init_dirs`` body verbatim so
    the P-RC-9-PLAN section 5.2 dir-layout parity assertion
    holds.
    """

    _SUBDIRS: tuple[str, ...] = (
        "nodes",
        "policies",
        "departments",
        "memory",
        "memory/departments",
        "memory/nodes",
        "events",
        "logs",
        "logs/tasks",
        "reports",
        "artifacts",
        "artifacts/meetings",
    )

    def new_org_id(self) -> str:
        return _new_id("org_")

    def initialize_directory_layout(self, org_dir: Path, org: Organization) -> None:
        for sub in self._SUBDIRS:
            (org_dir / sub).mkdir(parents=True, exist_ok=True)
        readme = org_dir / "policies" / "README.md"
        if not readme.exists():
            readme.write_text(_POLICIES_README_TEMPLATE, encoding="utf-8")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OrgNameConflictError(ValueError):
    """Raised by ``create`` / ``update`` / ``duplicate`` on a name collision.

    v2 alias for v1 ``openakita.orgs.manager.OrgNameConflictError``;
    the v2 attribute shape (``name`` + ``conflict_org_id``) is
    byte-for-byte identical so REST handlers that map this to
    HTTP 409 keep working unchanged.
    """

    def __init__(self, name: str, conflict_org_id: str) -> None:
        super().__init__(f"Organization name already exists: {name!r}")
        self.name = name
        self.conflict_org_id = conflict_org_id


# ---------------------------------------------------------------------------
# OrgManager (scaffold; bodies land in P9.5b / P9.5b2)
# ---------------------------------------------------------------------------


class OrgManager:
    """Organisation CRUD + persistence + cache (v2; P-RC-9 P9.5).

    Construct with ``data_dir`` (the same data root v1 reads)
    plus three optional DI Protocols. Default fallbacks wire
    in the parity-faithful filesystem backend so a one-arg
    ``OrgManager(data_dir)`` behaves like v1.

    Concurrency: a single :class:`threading.Lock` (matches v1
    ``OrgManager._write_lock``) guards the in-memory cache.
    Public methods are SYNC -- callers in ``api/routes/`` are
    FastAPI sync handlers; an async lock would force every
    caller to ``await`` and break v1 caller parity.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        persistence: OrgPersistenceProtocol | None = None,
        lifecycle: OrgLifecycleEmitterProtocol | None = None,
        factory: OrgFactoryProtocol | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._orgs_dir = self._data_dir / "orgs"
        self._templates_dir = self._data_dir / "org_templates"
        self._orgs_dir.mkdir(parents=True, exist_ok=True)
        self._templates_dir.mkdir(parents=True, exist_ok=True)
        self._persistence: OrgPersistenceProtocol = (
            persistence if persistence is not None else _FilesystemOrgPersistence(self._orgs_dir)
        )
        self._lifecycle: OrgLifecycleEmitterProtocol = (
            lifecycle if lifecycle is not None else _NoopOrgLifecycleEmitter()
        )
        self._factory: OrgFactoryProtocol = factory if factory is not None else _DefaultOrgFactory()
        self._cache: dict[str, Organization] = {}
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Directory helpers (parity-faithful with v1 ``manager._*``)
    # ------------------------------------------------------------------

    def _org_dir(self, org_id: str) -> Path:
        if ".." in org_id or "/" in org_id or "\\" in org_id:
            raise ValueError(f"Invalid org_id: {org_id}")
        return self._orgs_dir / org_id

    def _org_json(self, org_id: str) -> Path:
        return self._org_dir(org_id) / "org.json"

    def _state_json(self, org_id: str) -> Path:
        return self._org_dir(org_id) / "state.json"

    def _node_dir(self, org_id: str, node_id: str) -> Path:
        return self._org_dir(org_id) / "nodes" / node_id

    def _schedules_json(self, org_id: str, node_id: str) -> Path:
        return self._node_dir(org_id, node_id) / "schedules.json"

    def get_org_dir(self, org_id: str) -> Path:
        """Public alias for ``_org_dir`` (used by command_service / api).

        Callers that need the org root path on disk should
        use this method rather than reaching into the private
        helper. The path is validated for traversal-safety.
        """
        return self._org_dir(org_id)

    # ------------------------------------------------------------------
    # OrgLookupProtocol (P9.4 ``command_service.OrgLookupProtocol``)
    # ------------------------------------------------------------------

    def get_org(self, org_id: str) -> Organization | None:
        """Implements :class:`OrgLookupProtocol` from P9.4 command_service.

        Cache-bypass read via the injected persistence
        backend (so a freshly-saved org from another process
        is visible). ``get(org_id)`` -- landing in P9.5b --
        will be the cached-read variant.
        """
        raw = self._persistence.load_org_dict(org_id)
        if raw is None:
            return None
        return Organization.from_dict(raw)

    # ------------------------------------------------------------------
    # CRUD core (P9.5b -- 12 methods)
    # ------------------------------------------------------------------

    def list_orgs(self, include_archived: bool = False) -> list[dict[str, Any]]:
        """Return summary dicts for every org on disk (sorted by id).

        Each dict carries the same shape v1 returns
        (``id`` / ``name`` / ``description`` / ``icon`` /
        ``status`` / ``node_count`` / ``edge_count`` /
        ``tags`` / ``created_at`` / ``updated_at``). Archived
        orgs are skipped by default; pass
        ``include_archived=True`` to include them. Failures
        on individual orgs (corrupt JSON, etc.) are logged at
        WARNING level and skipped rather than aborting the
        whole listing.
        """
        result: list[dict[str, Any]] = []

        for org_id in self._persistence.list_org_ids():
            try:
                org = self._load(org_id)
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.warning("Failed to load org %s: %s", org_id, exc)
                continue
            if not include_archived and org.status == OrgStatus.ARCHIVED:
                continue
            result.append(
                {
                    "id": org.id,
                    "name": org.name,
                    "description": org.description,
                    "icon": org.icon,
                    "status": org.status.value,
                    "node_count": len(org.nodes),
                    "edge_count": len(org.edges),
                    "tags": org.tags,
                    "created_at": org.created_at,
                    "updated_at": org.updated_at,
                }
            )
        return result

    def get(self, org_id: str) -> Organization | None:
        """Cached read; missing orgs return ``None`` (matches v1)."""
        try:
            return self._load(org_id)
        except FileNotFoundError:
            return None

    def find_by_name(
        self,
        name: str,
        *,
        exclude_org_id: str | None = None,
        include_archived: bool = True,
    ) -> list[dict[str, Any]]:
        """Case- and whitespace-insensitive name lookup (matches v1).

        Returns the same summary dict shape as :meth:`list_orgs`.
        Empty / blank ``name`` short-circuits to an empty list.
        ``exclude_org_id`` lets ``update`` callers skip the org
        being renamed.
        """
        norm = normalize_org_name(name)
        if not norm:
            return []
        result: list[dict[str, Any]] = []
        for item in self.list_orgs(include_archived=include_archived):
            if exclude_org_id and item.get("id") == exclude_org_id:
                continue
            if normalize_org_name(item.get("name", "")) == norm:
                result.append(item)
        return result

    def resolve_id_by_name_or_id(self, query: str) -> tuple[str | None, list[dict[str, Any]]]:
        """Resolve ``query`` to an org id, falling back to name match.

        Returns ``(org_id, candidates)`` where:

        * exact id hit -> ``(id, [])``;
        * unique name hit -> ``(id, [])``;
        * multiple name hits -> ``(None, [summary, ...])``;
        * no hit -> ``(None, [])``.

        Used by CLI / IM call paths so a single user-typed
        string can resolve to an org without an extra
        round-trip.
        """
        q = (query or "").strip()
        if not q:
            return None, []
        if self.get(q) is not None:
            return q, []
        matches = self.find_by_name(q)
        if len(matches) == 1:
            return str(matches[0].get("id") or ""), []
        if len(matches) > 1:
            return None, matches
        return None, []

    def _ensure_name_unique(self, name: str, *, exclude_org_id: str | None = None) -> None:
        """Raise :class:`OrgNameConflictError` if ``name`` is already in use.

        Used by ``create`` / ``update`` / ``duplicate`` to
        give the user a single uniform error message no matter
        how the conflict was reached.
        """
        clean = (name or "").strip()
        if not clean:
            raise ValueError("Organization name is required")
        conflicts = self.find_by_name(clean, exclude_org_id=exclude_org_id)
        if conflicts:
            raise OrgNameConflictError(clean, str(conflicts[0].get("id") or ""))

    def create(self, data: dict[str, Any]) -> Organization:
        """Mint a new org and persist it.

        Raises :class:`OrgNameConflictError` if the name
        collides. Emits ``OrgLifecycleEmitterProtocol.emit_org_created``
        (no-op by default).
        """
        self._ensure_name_unique(data.get("name", ""))
        org = Organization.from_dict(data)
        if not org.id:
            org.id = self._factory.new_org_id()
        org.created_at = _now_iso()
        org.updated_at = org.created_at
        self._init_dirs(org)
        self._save(org)
        logger.info("[OrgManager] Created org: %s (%s)", org.id, org.name)
        self._lifecycle.emit_org_created(org.id, org.name)
        return org

    def delete(self, org_id: str) -> bool:
        """Permanently remove ``org_id``'s data; idempotent (returns False if absent)."""
        deleted = self._persistence.delete_org_dir(org_id)
        if deleted:
            self._cache.pop(org_id, None)
            logger.info("[OrgManager] Deleted org: %s", org_id)
            self._lifecycle.emit_org_deleted(org_id)
        return deleted

    def invalidate_cache(self, org_id: str | None = None) -> None:
        """Drop a single org or the entire in-memory cache."""
        with self._write_lock:
            if org_id:
                self._cache.pop(org_id, None)
            else:
                self._cache.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self, org_id: str) -> Organization:
        """Cache-aware load; raises FileNotFoundError on miss (matches v1)."""
        cached = self._cache.get(org_id)
        if cached is not None:
            return cached
        raw = self._persistence.load_org_dict(org_id)
        if raw is None:
            raise FileNotFoundError(f"Organization not found: {org_id}")
        org = Organization.from_dict(raw)
        with self._write_lock:
            self._cache[org_id] = org
        return org

    def _save(self, org: Organization) -> None:
        """Persist + cache. The persistence backend handles atomic write."""
        self._persistence.save_org_dict(org.id, org.to_dict())
        with self._write_lock:
            self._cache[org.id] = org

    def _init_dirs(self, org: Organization) -> None:
        """Materialise the full org directory tree.

        Delegates the org-level subdirs to
        ``OrgFactoryProtocol.initialize_directory_layout``
        and then materialises per-node dirs via
        ``_ensure_node_dirs``. Order matches v1's
        ``_init_dirs`` byte-for-byte (the dir-layout parity
        gate in P-RC-9-PLAN section 5.2 asserts this).
        """
        base = self._org_dir(org.id)
        self._factory.initialize_directory_layout(base, org)
        self._ensure_node_dirs(org)

    def _ensure_node_dirs(self, org: Organization) -> None:
        """Create per-node ``identity/`` + ``mcp_config.json`` + ``schedules.json``."""
        for node in org.nodes:
            nd = self._node_dir(org.id, node.id)
            (nd / "identity").mkdir(parents=True, exist_ok=True)
            mcp_cfg = nd / "mcp_config.json"
            if not mcp_cfg.exists():
                mcp_cfg.write_text(
                    json.dumps({"mode": "inherit"}, indent=2),
                    encoding="utf-8",
                )
            sched = nd / "schedules.json"
            if not sched.exists():
                sched.write_text("[]", encoding="utf-8")
        for dept in org.get_departments():
            (self._org_dir(org.id) / "departments" / dept).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Update / archive / save_direct / duplicate (P9.5b2)
    # ------------------------------------------------------------------

    def update(self, org_id: str, data: dict[str, Any]) -> Organization:
        """Merge ``data`` into the existing org and persist.

        Top-level keys are setattr'd on the cached Organization
        (``id`` and ``created_at`` are skipped). ``status`` ->
        :class:`OrgStatus` coercion. ``user_persona`` ->
        :class:`UserPersona.from_dict` coercion. ``nodes`` /
        ``edges`` patches replace the lists in place, preserving
        node ids and per-node config keys
        (:class:`OrgNode.__dataclass_fields__` minus runtime
        fields). Workbench plugin nodes must remain leaves
        (no children); violation raises :class:`ValueError`.
        Raises :class:`OrgNameConflictError` on rename collision.
        """
        org = self._load(org_id)
        nodes_raw = data.pop("nodes", None)
        edges_raw = data.pop("edges", None)

        if "name" in data:
            new_name = data.get("name")
            if isinstance(new_name, str) and normalize_org_name(new_name) != normalize_org_name(
                org.name
            ):
                self._ensure_name_unique(new_name, exclude_org_id=org_id)

        for key, val in data.items():
            if key in ("id", "created_at"):
                continue
            if hasattr(org, key):
                if key == "status" and isinstance(val, str):
                    val = OrgStatus(val)
                elif key == "user_persona" and isinstance(val, dict):
                    val = UserPersona.from_dict(val)
                setattr(org, key, val)

        if nodes_raw is not None:
            _RUNTIME_KEYS = {"status", "_runtime", "current_task"}
            _CONFIG_FIELDS = set(OrgNode.__dataclass_fields__) - _RUNTIME_KEYS
            existing = {n.id: n for n in org.nodes}
            updated: list[OrgNode] = []
            for nd in nodes_raw:
                node_id = nd.get("id")
                old = existing.get(node_id) if node_id else None
                if old is not None:
                    for key in _CONFIG_FIELDS:
                        if key in nd:
                            setattr(old, key, nd[key])
                    if not old.agent_profile_id:
                        old.agent_profile_id = infer_agent_profile_id_for_node(old.to_dict())
                    updated.append(old)
                else:
                    clean = {k: v for k, v in nd.items() if k not in _RUNTIME_KEYS}
                    updated.append(OrgNode.from_dict(clean))
            org.nodes = updated
        if edges_raw is not None:
            org.edges = [
                OrgEdge.from_dict(e) for e in edges_raw if e.get("source") != e.get("target")
            ]

        # Workbench plugin nodes must stay leaves. v1 raises ValueError
        # with a Chinese message; the API layer maps to HTTP 422.
        _violations: list[str] = []
        for n in org.nodes:
            if not getattr(n, "plugin_origin", None):
                continue
            if org.get_children(n.id):
                title = (n.role_title or n.id).strip()
                _violations.append(f"{title}({n.id})")
        if _violations:
            raise ValueError(
                "\u63d2\u4ef6\u5de5\u4f5c\u53f0\u8282\u70b9\u53ea\u5141\u8bb8\u4f5c\u4e3a\u53f6\u5b50\u8282\u70b9\uff0c\u4e0d\u80fd\u62e5\u6709\u5b50\u8282\u70b9\uff1a"
                + "\u3001".join(_violations)
                + "\u3002\u8bf7\u5148\u5220\u9664\u5b50\u8282\u70b9\u6216\u79fb\u9664\u5de5\u4f5c\u53f0\u6807\u8bc6\u540e\u518d\u4fdd\u5b58\u3002"
            )

        org.updated_at = _now_iso()
        self._ensure_node_dirs(org)
        self._save(org)
        logger.info("[OrgManager] Updated org: %s", org.id)
        self._lifecycle.emit_org_updated(org.id)
        return org

    def save_direct(self, org: Organization) -> bool:
        """Persist ``org`` without the load-merge dance.

        Returns False if the org directory was deleted between
        load and save (a race we accept by no-op'ing). Unlike
        :meth:`update`, this never re-creates a deleted org.
        """
        d = self._org_dir(org.id)
        if not d.exists():
            self._cache.pop(org.id, None)
            return False
        self._save(org)
        return True

    def archive(self, org_id: str) -> Organization:
        """Sugar for ``update(org_id, {"status": "archived"})``."""
        return self.update(org_id, {"status": "archived"})

    def unarchive(self, org_id: str) -> Organization:
        """Sugar for ``update(org_id, {"status": "active"})``."""
        return self.update(org_id, {"status": "active"})

    def duplicate(self, org_id: str, new_name: str | None = None) -> Organization:
        """Deep-copy ``org_id`` into a fresh org, re-minting node + edge ids.

        Auto-suffix ``" (\u526f\u672c)"`` / ``" (\u526f\u672c 2)"`` etc.
        when ``new_name`` is omitted and the default collides.
        """
        src = self._load(org_id)
        data = src.to_dict()
        data["id"] = self._factory.new_org_id()
        if new_name:
            data["name"] = new_name
        else:
            base = f"{src.name} (\u526f\u672c)"
            candidate = base
            n = 2
            while self.find_by_name(candidate):
                candidate = f"{base} {n}"
                n += 1
            data["name"] = candidate
        data["status"] = OrgStatus.DORMANT.value
        data["created_at"] = _now_iso()
        data["updated_at"] = data["created_at"]
        data["total_tasks_completed"] = 0
        data["total_messages_exchanged"] = 0
        data["total_tokens_used"] = 0

        id_map: dict[str, str] = {}
        original_nodes = src.to_dict()["nodes"]
        for old_n, new_n in zip(original_nodes, data["nodes"], strict=False):
            new_n["id"] = _new_id("node_")
            new_n["status"] = "idle"
            new_n["frozen_by"] = None
            new_n["frozen_reason"] = None
            new_n["frozen_at"] = None
            id_map[old_n["id"]] = new_n["id"]
        for edge in data.get("edges", []):
            edge["id"] = _new_id("edge_")
            edge["source"] = id_map.get(edge["source"], edge["source"])
            edge["target"] = id_map.get(edge["target"], edge["target"])
        return self.create(data)

    # ------------------------------------------------------------------
    # Node schedules (per-node ``schedules.json`` files)
    # ------------------------------------------------------------------

    def get_node_schedules(self, org_id: str, node_id: str) -> list[NodeSchedule]:
        """Return the saved schedule list for ``(org_id, node_id)``."""
        p = self._schedules_json(org_id, node_id)
        if not p.is_file():
            return []
        raw = json.loads(p.read_text(encoding="utf-8"))
        return [NodeSchedule.from_dict(s) for s in raw]

    def save_node_schedules(self, org_id: str, node_id: str, schedules: list[NodeSchedule]) -> None:
        """Overwrite the schedule list for ``(org_id, node_id)``."""
        p = self._schedules_json(org_id, node_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps([s.to_dict() for s in schedules], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_node_schedule(self, org_id: str, node_id: str, schedule: NodeSchedule) -> NodeSchedule:
        """Append + persist ``schedule``."""
        schedules = self.get_node_schedules(org_id, node_id)
        schedules.append(schedule)
        self.save_node_schedules(org_id, node_id, schedules)
        return schedule

    def update_node_schedule(
        self,
        org_id: str,
        node_id: str,
        schedule_id: str,
        data: dict[str, Any],
    ) -> NodeSchedule | None:
        """Patch + persist; returns the updated schedule or None on miss."""
        schedules = self.get_node_schedules(org_id, node_id)
        for i, s in enumerate(schedules):
            if s.id == schedule_id:
                for k, v in data.items():
                    if hasattr(s, k) and k != "id":
                        if k == "schedule_type" and isinstance(v, str):
                            v = ScheduleType(v)
                        setattr(s, k, v)
                schedules[i] = s
                self.save_node_schedules(org_id, node_id, schedules)
                return s
        return None

    def delete_node_schedule(self, org_id: str, node_id: str, schedule_id: str) -> bool:
        """Idempotent delete (returns False if no such schedule_id)."""
        schedules = self.get_node_schedules(org_id, node_id)
        before = len(schedules)
        schedules = [s for s in schedules if s.id != schedule_id]
        if len(schedules) == before:
            return False
        self.save_node_schedules(org_id, node_id, schedules)
        return True

    # ------------------------------------------------------------------
    # Templates (``data/org_templates/*.json``)
    # ------------------------------------------------------------------

    def list_templates(self) -> list[dict[str, Any]]:
        """Summary dicts for every template file."""
        result: list[dict[str, Any]] = []
        for p in sorted(self._templates_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                result.append(
                    {
                        "id": p.stem,
                        "name": data.get("name", p.stem),
                        "description": data.get("description", ""),
                        "icon": data.get("icon", "\u2728"),
                        "node_count": len(data.get("nodes", [])),
                        "tags": normalize_tags(data.get("tags")),
                    }
                )
            except Exception as exc:
                logger.warning("Failed to load template %s: %s", p.name, exc)
        return result

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        """Raw template dict or None on miss."""
        p = self._templates_dir / f"{template_id}.json"
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def create_from_template(
        self, template_id: str, overrides: dict[str, Any] | None = None
    ) -> Organization:
        """Create an org from a template.

        Auto-suffixes name if not explicitly overridden and the
        template default is already in use. Applies the BFS
        tree-layout helper so the new canvas opens with a
        readable arrangement.
        """
        tpl = self.get_template(template_id)
        if tpl is None:
            raise FileNotFoundError(f"Template not found: {template_id}")
        tpl.pop("is_template", None)
        tpl["id"] = self._factory.new_org_id()
        tpl["status"] = OrgStatus.DORMANT.value
        name_explicitly_overridden = bool(overrides and isinstance(overrides.get("name"), str))
        if overrides:
            tpl.update(overrides)
        if not name_explicitly_overridden:
            base_name = (tpl.get("name") or "").strip()
            if base_name and self.find_by_name(base_name):
                candidate = base_name
                n = 2
                while self.find_by_name(candidate):
                    candidate = f"{base_name} ({n})"
                    n += 1
                tpl["name"] = candidate
        for node in tpl.get("nodes", []) or []:
            if isinstance(node, dict) and not node.get("agent_profile_id"):
                node["agent_profile_id"] = infer_agent_profile_id_for_node(node)
        apply_initial_tree_layout(tpl)
        return self.create(tpl)

    def save_as_template(self, org_id: str, template_id: str | None = None) -> str:
        """Snapshot ``org_id`` to a template file; returns the template id."""
        org = self._load(org_id)
        data = org.to_dict()
        data["is_template"] = True
        data.pop("id", None)
        data["status"] = OrgStatus.DORMANT.value
        data["total_tasks_completed"] = 0
        data["total_messages_exchanged"] = 0
        data["total_tokens_used"] = 0
        tid = template_id or org.name.lower().replace(" ", "-")
        p = self._templates_dir / f"{tid}.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[OrgManager] Saved template: %s", tid)
        return tid

    # ------------------------------------------------------------------
    # Runtime state (read/write by OrgRuntime)
    # ------------------------------------------------------------------

    def load_state(self, org_id: str) -> dict[str, Any]:
        """Return the persisted state dict (empty if no ``state.json``)."""
        p = self._state_json(org_id)
        if not p.is_file():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def save_state(self, org_id: str, state: dict[str, Any]) -> None:
        """Overwrite ``state.json`` with ``state``."""
        p = self._state_json(org_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Factory helper (P-RC-9-PLAN section 4 P9.5)
# ---------------------------------------------------------------------------


def get_org_manager(data_dir: Path) -> OrgManager:
    """Module-level factory matching the P-RC-9-PLAN section 4 P9.5 surface.

    Returns a fresh :class:`OrgManager` bound to ``data_dir``
    with all default DI backends. Equivalent to
    ``OrgManager(data_dir)`` but spelled out so the caller-
    facing import surface mirrors v1's
    ``openakita.orgs.manager.OrgManager`` factory pattern.
    """
    return OrgManager(data_dir)

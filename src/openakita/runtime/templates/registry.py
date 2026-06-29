"""Template registry — discovery, validation, instantiation.

Per ADR-0008, the :class:`TemplateRegistry` is the single source of
truth for which templates exist, their schema, and how to clone one
into a fresh :class:`runtime.models.OrgV2`.

Built-in templates self-register on import via the :func:`template`
decorator. Application bootstrap (Phase 7) imports the
``runtime.templates.builtin`` package and the registry is populated
as a side effect — no manual registration needed.

The registry is process-local; callers can construct private
registries for tests, or use :data:`GLOBAL_REGISTRY` in production.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable, Iterable
from typing import Any

from ..models import (
    DefaultsSpec as RuntimeDefaultsSpec,
)
from ..models import (
    EdgeKind,
    EdgeV2,
    NodeRuntimeOverrides,
    NodeV2,
    OrgStatus,
    OrgV2,
    WorkbenchBinding,
    new_edge_id,
    new_node_id,
    new_org_id,
)
from .schema import (
    TemplateSpec,
    TemplateValidationError,
    WorkbenchBindingSpec,
)

__all__ = [
    "GLOBAL_REGISTRY",
    "TEMPLATE_FACTORY_MARK",
    "TemplateRegistry",
    "collect_builtin_factories",
    "discover_builtins",
    "template",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Decorator + module-level pending list
# ---------------------------------------------------------------------------

TemplateFactory = Callable[[], TemplateSpec]
"""``factory() -> TemplateSpec``.

Built-in templates are functions returning a fresh, fully-populated
:class:`TemplateSpec`. Returning a fresh value (rather than a module-
level constant) means the dataclass instance is created lazily, only
when the template is actually instantiated.
"""


TEMPLATE_FACTORY_MARK = "__openakita_template_factory__"
"""Attribute name set on a function by :func:`template`.

We need a *survivable* marker because some callers (FastAPI app
bootstrap, test fixtures that mock out ``_PENDING``) want to
collect every built-in factory by walking the ``runtime.templates
.builtin`` package, *without* depending on the lazy queue having
been populated in this exact process state.
"""


_PENDING: list[TemplateFactory] = []


def template(factory: TemplateFactory) -> TemplateFactory:
    """Decorator that registers a built-in template factory.

    The factory is queued in :data:`_PENDING` (drained by
    :meth:`TemplateRegistry.bootstrap`) and *also* marked with
    :data:`TEMPLATE_FACTORY_MARK` so :func:`collect_builtin_factories`
    can find it later by walking the package. Using both mechanisms
    means callers can pick the cheaper one for their situation:

    - Application bootstrap path:
      ``discover_builtins() + GLOBAL_REGISTRY.bootstrap()`` —
      straightforward, drains the queue once.
    - Test / hot-reload path:
      ``GLOBAL_REGISTRY.bootstrap(collect_builtin_factories(...))`` —
      survives a previously-drained queue and a previously-imported
      module list, because the marker attribute lives on the function
      object, not in the queue.
    """
    _PENDING.append(factory)
    setattr(factory, TEMPLATE_FACTORY_MARK, True)
    return factory


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TemplateRegistry:
    """Process-local catalog of :class:`TemplateSpec` records."""

    def __init__(self) -> None:
        self._templates: dict[str, TemplateSpec] = {}

    # ------------------------------------------------------------------
    # CRUD-ish surface
    # ------------------------------------------------------------------

    def register(self, spec: TemplateSpec) -> None:
        spec.validate()
        if spec.id in self._templates:
            existing = self._templates[spec.id]
            if existing is spec:
                return
            raise TemplateValidationError(
                f"template id {spec.id!r} is already registered with version "
                f"{existing.version}; refusing to overwrite with version "
                f"{spec.version}"
            )
        self._templates[spec.id] = spec

    def get(self, template_id: str) -> TemplateSpec:
        try:
            return self._templates[template_id]
        except KeyError as exc:
            raise KeyError(
                f"unknown template id {template_id!r}; "
                f"known: {sorted(self._templates)}"
            ) from exc

    def list(self) -> list[TemplateSpec]:
        return sorted(self._templates.values(), key=lambda t: t.id)

    def __contains__(self, template_id: str) -> bool:
        return template_id in self._templates

    def __len__(self) -> int:
        return len(self._templates)

    def clear(self) -> None:
        """Drop every registration. Useful for tests; callers must
        re-register or call :meth:`bootstrap` afterwards."""
        self._templates.clear()

    # ------------------------------------------------------------------
    # Bulk bootstrap
    # ------------------------------------------------------------------

    def bootstrap(self, factories: Iterable[TemplateFactory] | None = None) -> int:
        """Drain pending decorators and register their products.

        Returns the number of templates registered. Iterating the
        global pending list is destructive: factories already executed
        are removed so a re-bootstrap is idempotent.
        """
        registered = 0
        if factories is None:
            factories = list(_PENDING)
            _PENDING.clear()
        for factory in factories:
            spec = factory()
            self.register(spec)
            registered += 1
        return registered

    # ------------------------------------------------------------------
    # Instantiation — TemplateSpec -> OrgV2
    # ------------------------------------------------------------------

    def instantiate(
        self,
        template_id: str,
        *,
        name: str,
        description: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> OrgV2:
        """Clone the template into a fresh :class:`OrgV2`.

        ``overrides`` is a small whitelist of safely-applicable knobs;
        anything else must be edited on the resulting org afterwards.
        Supported keys today:

        - ``defaults`` (dict): merge into the template defaults
          (max_turns, max_stalls, suspect_secs, stream_channels).
        - ``node_persona_prompts`` (dict[str, str]): per-node-id
          overrides for ``persona_prompt``.
        - ``node_runtime_overrides`` (dict[str, dict]): per-node-id
          overrides applied to NodeRuntimeOverrides.

        Unknown keys raise TemplateValidationError so a typo is loud.
        """
        spec = self.get(template_id)
        spec.validate()
        overrides = dict(overrides or {})
        unknown = overrides.keys() - {
            "defaults",
            "node_persona_prompts",
            "node_runtime_overrides",
        }
        if unknown:
            raise TemplateValidationError(
                f"unknown override keys: {sorted(unknown)}; allowed: "
                f"defaults, node_persona_prompts, node_runtime_overrides"
            )
        node_personas = dict(overrides.get("node_persona_prompts") or {})
        node_runtimes = dict(overrides.get("node_runtime_overrides") or {})
        defaults_overrides = dict(overrides.get("defaults") or {})
        for nid in node_personas:
            if not isinstance(nid, str):
                raise TemplateValidationError(
                    "node_persona_prompts keys must be strings"
                )
            try:
                spec.get_node(nid)
            except KeyError as exc:
                raise TemplateValidationError(
                    f"node_persona_prompts references unknown node id {nid!r}"
                ) from exc
        for nid in node_runtimes:
            try:
                spec.get_node(nid)
            except KeyError as exc:
                raise TemplateValidationError(
                    f"node_runtime_overrides references unknown node id {nid!r}"
                ) from exc

        org_id = new_org_id()
        # Mint fresh NodeIds and remember the mapping role-handle -> NodeId.
        id_map: dict[str, str] = {n.id: new_node_id() for n in spec.nodes}

        # Derive parent_id from HIERARCHY edges *before* constructing
        # NodeV2s so OrgV2.children_of() and OrgV2.root_nodes() return
        # correct trees on the produced org. The legacy v1 schema
        # carried parent_id as the canonical hierarchy field; v2 made
        # edges canonical but kept ``NodeV2.parent_id`` as a cache for
        # fast tree traversals (used by the IM dispatcher and the
        # supervisor's escalation path). Skipping this step would
        # produce orgs where every node thinks it is a root, silently
        # breaking those consumers.
        #
        # COLLABORATE / CONSULT edges are *not* parent relationships;
        # they connect peers and advisors and must not contribute to
        # parent_id. Only EdgeKind.HIERARCHY counts.
        #
        # A node may not have two distinct hierarchy parents; multi-
        # parent organisations are template-modelled as one HIERARCHY
        # edge plus N COLLABORATE edges. We surface a violation
        # loudly here rather than picking one parent at random.
        spec_parent_of: dict[str, str] = {}
        for spec_edge in spec.edges:
            if spec_edge.kind != EdgeKind.HIERARCHY:
                continue
            existing = spec_parent_of.get(spec_edge.dst)
            if existing is not None and existing != spec_edge.src:
                raise TemplateValidationError(
                    f"node {spec_edge.dst!r} has multiple HIERARCHY parents "
                    f"({existing!r}, {spec_edge.src!r}); a node may have at "
                    "most one parent. Use COLLABORATE for cross-team links."
                )
            spec_parent_of[spec_edge.dst] = spec_edge.src

        nodes: list[NodeV2] = []
        for spec_node in spec.nodes:
            persona = node_personas.get(spec_node.id, spec_node.persona_prompt)
            runtime_payload = spec_node.runtime.to_jsonable()
            runtime_payload.update(node_runtimes.get(spec_node.id, {}))
            try:
                runtime = NodeRuntimeOverrides.from_jsonable(runtime_payload)
            except Exception as exc:  # noqa: BLE001
                raise TemplateValidationError(
                    f"runtime override merge for node {spec_node.id!r} failed: {exc}"
                ) from exc
            wb = (
                _binding_to_runtime(spec_node.workbench)
                if spec_node.workbench is not None
                else None
            )
            parent_handle = spec_parent_of.get(spec_node.id)
            nodes.append(
                NodeV2(
                    id=id_map[spec_node.id],
                    org_id=org_id,
                    type=spec_node.type,
                    role=spec_node.role,
                    label=spec_node.label,
                    persona_prompt=persona,
                    tool_subset=spec_node.tool_subset,
                    workbench=wb,
                    runtime_overrides=runtime,
                    parent_id=(
                        id_map[parent_handle] if parent_handle is not None else None
                    ),
                    department=spec_node.department,
                )
            )

        edges: list[EdgeV2] = [
            EdgeV2(
                id=new_edge_id(),
                org_id=org_id,
                src=id_map[edge.src],
                dst=id_map[edge.dst],
                kind=edge.kind,
            )
            for edge in spec.edges
        ]

        defaults = _merge_defaults(spec.defaults, defaults_overrides)

        org = OrgV2(
            id=org_id,
            name=name,
            template_id=spec.id,
            description=description if description is not None else spec.description,
            nodes=nodes,
            edges=edges,
            defaults=defaults,
            status=OrgStatus.CREATED,
        )
        return org


# ---------------------------------------------------------------------------
# Discovery — auto-import every module under runtime.templates.builtin
# ---------------------------------------------------------------------------


def discover_builtins(package: str = "openakita.runtime.templates.builtin") -> int:
    """Import every submodule of ``package`` so its ``@template``
    decorators register themselves.

    Returns the number of modules imported (or already cached, since
    re-importing a cached module is a no-op).
    """
    try:
        pkg = importlib.import_module(package)
    except ModuleNotFoundError:
        logger.debug("template builtin package %s not present yet", package)
        return 0
    if not hasattr(pkg, "__path__"):
        return 0
    imported = 0
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        importlib.import_module(f"{package}.{info.name}")
        imported += 1
    return imported


def collect_builtin_factories(
    package: str = "openakita.runtime.templates.builtin",
) -> list[TemplateFactory]:
    """Walk ``package`` and return every ``@template``-decorated factory.

    Unlike :func:`discover_builtins` + :meth:`TemplateRegistry.bootstrap`,
    this helper does not consume the queue — it inspects the imported
    modules and returns every callable that carries the
    :data:`TEMPLATE_FACTORY_MARK` attribute. This is the path the API
    facade uses, so a previously-drained ``_PENDING`` queue (e.g.
    after running unit tests that monkeypatched it) cannot starve the
    application of templates.
    """
    try:
        pkg = importlib.import_module(package)
    except ModuleNotFoundError:
        return []
    if not hasattr(pkg, "__path__"):
        return []
    found: list[TemplateFactory] = []
    seen: set[int] = set()
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{package}.{info.name}")
        for value in vars(mod).values():
            if not callable(value):
                continue
            if not getattr(value, TEMPLATE_FACTORY_MARK, False):
                continue
            if id(value) in seen:
                continue
            seen.add(id(value))
            found.append(value)
    return found


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _binding_to_runtime(spec: WorkbenchBindingSpec) -> WorkbenchBinding:
    return WorkbenchBinding(
        plugin_id=spec.plugin_id,
        mode=spec.mode,
        capabilities=spec.capabilities,
    )


def _merge_defaults(
    spec_defaults: object, overrides: dict[str, Any]
) -> RuntimeDefaultsSpec:
    """Merge the template's :class:`DefaultsSpec` with override knobs."""
    base = {
        "max_turns": spec_defaults.max_turns,  # type: ignore[attr-defined]
        "max_stalls": spec_defaults.max_stalls,  # type: ignore[attr-defined]
        "suspect_secs": spec_defaults.suspect_secs,  # type: ignore[attr-defined]
        "stream_channels": spec_defaults.stream_channels,  # type: ignore[attr-defined]
    }
    allowed = set(base.keys())
    unknown = overrides.keys() - allowed
    if unknown:
        raise TemplateValidationError(
            f"unknown defaults override keys: {sorted(unknown)}; "
            f"allowed: {sorted(allowed)}"
        )
    base.update(overrides)
    if "stream_channels" in base and not isinstance(base["stream_channels"], tuple):
        base["stream_channels"] = tuple(base["stream_channels"])
    return RuntimeDefaultsSpec(**base)


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------


GLOBAL_REGISTRY = TemplateRegistry()
"""Process-wide singleton.

Application bootstrap should call::

    discover_builtins()
    GLOBAL_REGISTRY.bootstrap()

once at startup. Tests should construct their own
:class:`TemplateRegistry` instances to avoid cross-test contamination.
"""

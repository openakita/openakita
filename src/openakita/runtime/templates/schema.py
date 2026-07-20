"""Template schema — typed blueprint for an :class:`OrgV2`.

Per ADR-0008, a ``TemplateSpec`` is a fully-typed, JSON-serialisable
description of a multi-agent organization that can be cloned into a
live :class:`runtime.models.OrgV2` with fresh ULIDs by the registry's
``instantiate`` method.

The shape mirrors the runtime models (``NodeV2`` / ``EdgeV2`` /
``DefaultsSpec``) but with two crucial differences:

1. No IDs. ``NodeSpec.id`` is a stable *role-handle* used inside the
   template (and across edges); the runtime generates a real ``NodeId``
   when instantiating. This means a template is a static document —
   committing one to disk does not pollute the system with stale
   ULIDs.

2. No timestamps, no live state. A template never carries
   ``created_at`` / ``last_seen`` / ``status`` fields; those are
   stamped at instantiation.

Validation runs at three points:

* :meth:`TemplateSpec.validate` — on construction.
* :class:`TemplateRegistry.register` — when a template is registered.
* The CI test fixture ``tests/runtime/templates/test_registry.py`` —
  every built-in template is parsed and validated at test time, so a
  broken template can never reach ``main``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..models import EdgeKind, NodeType

__all__ = [
    "DefaultsSpec",
    "EdgeSpec",
    "GuardrailSpec",
    "NodeRuntimeOverridesSpec",
    "NodeSpec",
    "TemplateSpec",
    "TemplateValidationError",
    "WorkbenchBindingSpec",
]


_ROLE_HANDLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class TemplateValidationError(ValueError):
    """Raised when a TemplateSpec or component fails validation."""


# ---------------------------------------------------------------------------
# Leaf dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardrailSpec:
    """Declarative guardrail attached to a node.

    The ``type`` field is matched against the
    :mod:`runtime.guardrail.builtin` registry first, then against any
    plugin-supplied guardrails. ``options`` is the per-type config
    blob (e.g. ``{"field": "shots", "n": 8}`` for ``min_items``).
    """

    type: str
    options: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not isinstance(self.type, str) or not self.type.strip():
            raise TemplateValidationError("GuardrailSpec.type must be a non-empty string")
        if not isinstance(self.options, dict):
            raise TemplateValidationError(
                f"GuardrailSpec.options must be a dict, got {type(self.options).__name__}"
            )

    def to_jsonable(self) -> dict[str, Any]:
        return {"type": self.type, "options": dict(self.options)}


@dataclass(frozen=True)
class WorkbenchBindingSpec:
    """Connector between a NodeSpec and a plugin's WORKBENCH manifest.

    Required for nodes of type ``"workbench"``. ``capabilities`` is an
    optional subset selector; ``None`` means "every capability the
    manifest declares for this mode".
    """

    plugin_id: str
    mode: str
    capabilities: tuple[str, ...] | None = None

    def validate(self) -> None:
        if not isinstance(self.plugin_id, str) or not self.plugin_id.strip():
            raise TemplateValidationError(
                "WorkbenchBindingSpec.plugin_id must be a non-empty string"
            )
        if not isinstance(self.mode, str) or not self.mode.strip():
            raise TemplateValidationError("WorkbenchBindingSpec.mode must be a non-empty string")
        if self.capabilities is not None:
            if not all(isinstance(c, str) and c for c in self.capabilities):
                raise TemplateValidationError(
                    "WorkbenchBindingSpec.capabilities must be a tuple of non-empty strings or None"
                )

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "mode": self.mode,
            "capabilities": (list(self.capabilities) if self.capabilities is not None else None),
        }


@dataclass(frozen=True)
class NodeRuntimeOverridesSpec:
    """Per-node runtime overrides at template-design time.

    Mirrors :class:`runtime.models.NodeRuntimeOverrides` so a template
    can pre-set the same closed knob set the supervisor honours.
    """

    max_iterations: int | None = None
    max_turns: int | None = None
    max_stalls: int | None = None
    suspect_secs: int | None = None
    persona_overlay: str | None = None

    def validate(self) -> None:
        for name, value in self.iter_set():
            if name == "persona_overlay":
                if not isinstance(value, str):
                    raise TemplateValidationError("persona_overlay must be a string")
                continue
            if not isinstance(value, int) or value < 0:
                raise TemplateValidationError(
                    f"NodeRuntimeOverridesSpec.{name} must be a non-negative int"
                )

    def iter_set(self) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for name in (
            "max_iterations",
            "max_turns",
            "max_stalls",
            "suspect_secs",
            "persona_overlay",
        ):
            value = getattr(self, name)
            if value is not None:
                out.append((name, value))
        return out

    def to_jsonable(self) -> dict[str, Any]:
        return dict(self.iter_set())


@dataclass(frozen=True)
class NodeSpec:
    """Blueprint for a single node in the template graph."""

    id: str  # role-handle, unique inside this template
    type: NodeType
    role: str  # human-readable function ("art_director")
    label: str  # display name in the UI
    persona_prompt: str | None = None
    tool_subset: tuple[str, ...] | None = None
    workbench: WorkbenchBindingSpec | None = None
    runtime: NodeRuntimeOverridesSpec = field(default_factory=NodeRuntimeOverridesSpec)
    guardrails: tuple[GuardrailSpec, ...] = ()
    # Org-chart department this node belongs to (元数据). The v1 dict
    # templates carried this; the v2 schema migration dropped it, which
    # left v2-instantiated orgs with an empty ``department`` (so the
    # blackboard's department tier could not group them). It is pure
    # metadata — dispatch/review key on edges, not on department — so it
    # defaults to "" and is optional; built-in templates fill it, and
    # user-authored templates may leave it blank rather than inventing one.
    department: str = ""

    def validate(self) -> None:
        if not isinstance(self.id, str) or not _ROLE_HANDLE_RE.match(self.id):
            raise TemplateValidationError(
                f"NodeSpec.id must match /^[a-z][a-z0-9_]*$/ (got {self.id!r})"
            )
        if not isinstance(self.type, NodeType):
            raise TemplateValidationError(f"NodeSpec.type must be a NodeType, got {self.type!r}")
        if not isinstance(self.role, str) or not self.role.strip():
            raise TemplateValidationError("NodeSpec.role must be a non-empty string")
        if not isinstance(self.label, str) or not self.label.strip():
            raise TemplateValidationError("NodeSpec.label must be a non-empty string")
        if self.persona_prompt is not None and not isinstance(self.persona_prompt, str):
            raise TemplateValidationError("NodeSpec.persona_prompt must be a string or None")
        if not isinstance(self.department, str):
            raise TemplateValidationError("NodeSpec.department must be a string")
        if self.tool_subset is not None:
            if not all(isinstance(t, str) and t for t in self.tool_subset):
                raise TemplateValidationError(
                    "NodeSpec.tool_subset must be a tuple of non-empty strings or None"
                )
        if self.type is NodeType.WORKBENCH:
            if self.workbench is None:
                raise TemplateValidationError(
                    f"node {self.id!r}: type=workbench requires a workbench binding"
                )
        elif self.workbench is not None:
            raise TemplateValidationError(
                f"node {self.id!r}: workbench binding only allowed for type=workbench "
                f"(got type={self.type.value})"
            )
        if self.workbench is not None:
            self.workbench.validate()
        self.runtime.validate()
        for g in self.guardrails:
            g.validate()

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "role": self.role,
            "label": self.label,
            "persona_prompt": self.persona_prompt,
            "tool_subset": (list(self.tool_subset) if self.tool_subset is not None else None),
            "workbench": (self.workbench.to_jsonable() if self.workbench is not None else None),
            "runtime": self.runtime.to_jsonable(),
            "guardrails": [g.to_jsonable() for g in self.guardrails],
            "department": self.department,
        }


@dataclass(frozen=True)
class EdgeSpec:
    """Directed edge between two NodeSpec ids."""

    src: str
    dst: str
    kind: EdgeKind = EdgeKind.HIERARCHY
    binding: dict[str, Any] = field(default_factory=dict)

    def validate(self, *, valid_node_ids: frozenset[str]) -> None:
        if self.src == self.dst:
            raise TemplateValidationError(
                f"edge {self.src!r}->{self.dst!r}: src and dst must differ"
            )
        if self.src not in valid_node_ids:
            raise TemplateValidationError(f"edge src {self.src!r} does not match any NodeSpec.id")
        if self.dst not in valid_node_ids:
            raise TemplateValidationError(f"edge dst {self.dst!r} does not match any NodeSpec.id")
        if not isinstance(self.kind, EdgeKind):
            raise TemplateValidationError(f"EdgeSpec.kind must be an EdgeKind, got {self.kind!r}")
        if self.kind == EdgeKind.ARTIFACT:
            binding = self.binding
            if not isinstance(binding, dict):
                raise TemplateValidationError("artifact edge binding must be an object")
            if (
                not isinstance(binding.get("target_param"), str)
                or not binding["target_param"].strip()
            ):
                raise TemplateValidationError("artifact edge binding requires target_param")
            if binding.get("value_field") not in {"asset_ids", "task_ids", "segments"}:
                raise TemplateValidationError(
                    "artifact edge binding value_field must be asset_ids, task_ids, or segments"
                )
            tools = binding.get("target_tools")
            if (
                not isinstance(tools, list)
                or not tools
                or not all(isinstance(tool, str) and tool.strip() for tool in tools)
            ):
                raise TemplateValidationError(
                    "artifact edge binding target_tools must be a non-empty string list"
                )
            if binding.get("cardinality", "many") not in {"one", "many"}:
                raise TemplateValidationError(
                    "artifact edge binding cardinality must be one or many"
                )
            if "required" in binding and not isinstance(binding["required"], bool):
                raise TemplateValidationError("artifact edge binding required must be a boolean")
            if binding.get("activation", "manual") not in {"manual", "when_ready"}:
                raise TemplateValidationError(
                    "artifact edge binding activation must be manual or when_ready"
                )
            if binding.get("dispatch_mode", "per_join_key") not in {
                "per_join_key",
                "join_all",
            }:
                raise TemplateValidationError(
                    "artifact edge binding dispatch_mode must be per_join_key or join_all"
                )
            for key in ("min_count", "max_attempts"):
                value = binding.get(key)
                if value is not None and (
                    not isinstance(value, int) or isinstance(value, bool) or value < 1
                ):
                    raise TemplateValidationError(
                        f"artifact edge binding {key} must be a positive integer"
                    )
            if int(binding.get("max_attempts", 1) or 1) > 5:
                raise TemplateValidationError(
                    "artifact edge binding max_attempts must not exceed 5"
                )
            join_scope = binding.get("join_scope")
            if join_scope is not None:
                if not isinstance(join_scope, dict):
                    raise TemplateValidationError(
                        "artifact edge binding join_scope must be an object"
                    )
                if (
                    not isinstance(join_scope.get("source"), str)
                    or not join_scope["source"].strip()
                ):
                    raise TemplateValidationError(
                        "artifact edge binding join_scope requires source"
                    )
                if join_scope["source"] not in valid_node_ids:
                    raise TemplateValidationError(
                        "artifact edge binding join_scope source must reference a node"
                    )

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "src": self.src,
            "dst": self.dst,
            "kind": self.kind.value,
            "binding": dict(self.binding),
        }


@dataclass(frozen=True)
class DefaultsSpec:
    """Org-wide defaults for a template instantiation."""

    max_turns: int = 30
    max_stalls: int = 3
    suspect_secs: int = 90
    stream_channels: tuple[str, ...] = (
        "values",
        "updates",
        "tasks",
        "checkpoints",
        "messages",
        "progress_ledger",
        "lifecycle",
    )

    def validate(self) -> None:
        for name in ("max_turns", "max_stalls", "suspect_secs"):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise TemplateValidationError(f"DefaultsSpec.{name} must be a positive int")
        if not all(isinstance(c, str) and c for c in self.stream_channels):
            raise TemplateValidationError(
                "DefaultsSpec.stream_channels must be a tuple of non-empty strings"
            )

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "max_turns": self.max_turns,
            "max_stalls": self.max_stalls,
            "suspect_secs": self.suspect_secs,
            "stream_channels": list(self.stream_channels),
        }


@dataclass(frozen=True)
class TemplateSpec:
    """Top-level blueprint for an organization."""

    id: str
    name: str
    category: str
    description: str
    version: int
    nodes: tuple[NodeSpec, ...]
    edges: tuple[EdgeSpec, ...] = ()
    defaults: DefaultsSpec = field(default_factory=DefaultsSpec)

    def validate(self) -> None:
        if not isinstance(self.id, str) or not _ROLE_HANDLE_RE.match(self.id):
            raise TemplateValidationError(
                f"TemplateSpec.id must match /^[a-z][a-z0-9_]*$/ (got {self.id!r})"
            )
        if not isinstance(self.name, str) or not self.name.strip():
            raise TemplateValidationError("TemplateSpec.name must be a non-empty string")
        if not isinstance(self.category, str) or not self.category.strip():
            raise TemplateValidationError("TemplateSpec.category must be a non-empty string")
        if not isinstance(self.description, str):
            raise TemplateValidationError("TemplateSpec.description must be a string")
        if not isinstance(self.version, int) or self.version < 1:
            raise TemplateValidationError("TemplateSpec.version must be a positive int")
        if not self.nodes:
            raise TemplateValidationError("TemplateSpec.nodes must be non-empty")
        seen: set[str] = set()
        for node in self.nodes:
            node.validate()
            if node.id in seen:
                raise TemplateValidationError(f"TemplateSpec.nodes has duplicate id {node.id!r}")
            seen.add(node.id)
        valid_ids = frozenset(seen)
        for edge in self.edges:
            edge.validate(valid_node_ids=valid_ids)
        self._check_no_hierarchy_cycles()
        self.defaults.validate()

    def _check_no_hierarchy_cycles(self) -> None:
        """Ensure type=hierarchy edges form a DAG.

        A hierarchy edge expresses delegation authority (boss -> report);
        a cycle would mean two nodes are each other's manager, which is
        a template authoring bug.
        """
        graph: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for edge in self.edges:
            if edge.kind is EdgeKind.HIERARCHY:
                graph[edge.src].append(edge.dst)
        WHITE, GRAY, BLACK = 0, 1, 2
        colour: dict[str, int] = {n.id: WHITE for n in self.nodes}

        def dfs(u: str, path: list[str]) -> None:
            colour[u] = GRAY
            for v in graph[u]:
                if colour[v] == GRAY:
                    cycle = path[path.index(v) :] + [v]
                    raise TemplateValidationError(
                        f"hierarchy cycle in template {self.id!r}: {' -> '.join(cycle)}"
                    )
                if colour[v] == WHITE:
                    dfs(v, [*path, v])
            colour[u] = BLACK

        for node in self.nodes:
            if colour[node.id] == WHITE:
                dfs(node.id, [node.id])

    def get_node(self, node_id: str) -> NodeSpec:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "version": self.version,
            "nodes": [n.to_jsonable() for n in self.nodes],
            "edges": [e.to_jsonable() for e in self.edges],
            "defaults": self.defaults.to_jsonable(),
        }

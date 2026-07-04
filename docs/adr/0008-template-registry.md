# ADR-0008 — Template Registry and Schema

- **Status**: Accepted
- **Date**: 2026-05-18
- **Accepted**: 2026-05-19 (after P-RC-0..7 implementation review at G-RC-8)
- **Phase**: 0 (Spec Freeze)

## Context

`src/openakita/orgs/templates.py` (1 234 lines) defines all the
"organization templates" the user can pick from when creating a new org —
AIGC video studio, customer service, research team, etc. Templates are
declared as Python dictionaries that mix node specs, edge specs,
free-text prompts, runtime overrides, and ad-hoc behavioural hints. The
file has grown organically; new templates accrete copy-pasted blocks.

The user explicitly asked (constraint C1) that the v2 architecture
**preserve organization templates as first-class citizens** so that
existing flagship templates (AIGC studio in particular) remain
out-of-the-box. We need:

1. a registry that lets users discover and instantiate built-in
   templates with one click;
2. a typed schema so a template can be validated at load time, reasoned
   about by tooling, and round-tripped through JSON;
3. a per-template file layout so adding or modifying one template
   touches one file, not a 1 234-line monolith;
4. a clean compatibility story: legacy template names continue to work
   for users; only the on-disk format changes.

## Decision

### Registry

```python
# src/openakita/runtime/templates/registry.py
class TemplateRegistry:
    def register(self, template: TemplateSpec) -> None: ...
    def get(self, template_id: str) -> TemplateSpec: ...
    def list(self) -> list[TemplateSpec]: ...
    def instantiate(self, template_id: str, *, name: str,
                    overrides: dict | None = None) -> OrgV2: ...

GLOBAL_REGISTRY = TemplateRegistry()
```

Built-in templates self-register on import via a decorator:

```python
@template
def aigc_video_studio() -> TemplateSpec:
    return TemplateSpec(
        id="aigc_video_studio",
        name="AIGC Video Studio",
        category="content_production",
        description="Multi-role studio that produces short videos end to end.",
        version=1,
        nodes=[ ... ],
        edges=[ ... ],
        defaults=DefaultsSpec(
            max_turns=40,
            max_stalls=4,
            channels=["values", "updates", "tasks", "checkpoints",
                      "messages", "progress_ledger", "lifecycle"],
        ),
    )
```

The decorator places the function in a global list. Application
bootstrap (Phase 7) calls each, validates the result against the
schema, and registers it. Lazy execution avoids import-time side
effects.

### Schema (`runtime/templates/schema.py`)

Templates are dataclasses (Python source-of-truth) with a JSON-Schema
mirror in `runtime/templates/schema.json` for tooling.

```python
@dataclass(frozen=True)
class NodeSpec:
    id: str
    type: Literal["llm", "workbench", "tool", "condition", "human_review"]
    role: str                                   # e.g. "art_director"
    label: str                                  # human-readable
    persona_prompt: str | None = None           # override identity
    tool_subset: list[str] | None = None        # restrict to subset
    workbench: WorkbenchBinding | None = None   # required for type="workbench"
    runtime: NodeRuntimeOverrides = field(default_factory=NodeRuntimeOverrides)
    guardrails: list[GuardrailSpec] = field(default_factory=list)

@dataclass(frozen=True)
class WorkbenchBinding:
    plugin_id: str           # e.g. "happyhorse-video"
    mode: str                # one of the modes declared in WORKBENCH manifest
    capabilities: list[str] | None = None  # subset; None => all from manifest

@dataclass(frozen=True)
class EdgeSpec:
    src: str
    dst: str
    kind: Literal["hierarchy", "collaborate", "escalate", "consult"]

@dataclass(frozen=True)
class TemplateSpec:
    id: str
    name: str
    category: str
    description: str
    version: int
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]
    defaults: DefaultsSpec

@dataclass(frozen=True)
class DefaultsSpec:
    max_turns: int = 30
    max_stalls: int = 3
    channels: list[str] = field(default_factory=list)
```

### Per-template files

Each built-in template gets its own file under
`runtime/templates/builtin/`, named after `id`:

```
runtime/templates/builtin/
  aigc_video_studio.py
  customer_service.py
  research_team.py
  data_analyst.py
  ...
```

Phase 5 will port every legacy template into a separate file. The legacy
1 234-line `orgs/templates.py` is removed in Phase 8.

### Instantiation

`registry.instantiate(template_id, name=, overrides=)`:

1. Validates the template against the schema (defensive — protects
   against tampered files).
2. Generates fresh `OrgV2` / `NodeV2` / `EdgeV2` records with new ULIDs.
3. Applies user `overrides` (whitelist of fields, e.g. node prompts,
   max_turns; arbitrary structural changes require user-edits the
   resulting org afterwards).
4. Persists the org via `runtime.facade.create_org(org)`.
5. Bootstraps the workbench iframe registration for any
   `type="workbench"` node so the front-end immediately knows how to
   render its UI.

### Bootstrap on first launch (constraint C1)

On a fresh `data/orgs/v2/` (the user-selected "fresh" data policy from
[ADR-0010](0010-data-migration.md)), the runtime auto-creates one org
per `category="showcase"` template, marked with
`runtime_overrides.auto_bootstrapped=True`. This gives the user a
working AIGC video studio out of the box, just like today, immediately
after cutover.

The user can clone any template with the `POST /api/v2/orgs/from-template`
endpoint (Phase 6) which calls `registry.instantiate`.

### Versioning

`TemplateSpec.version` is bumped whenever a template's *graph
structure* changes. User-instantiated orgs do not auto-upgrade; the
registry exposes `list_outdated_orgs()` so the UI can offer migration.
Migration scripts live next to the template definition:

```python
# runtime/templates/builtin/aigc_video_studio.py
def migrate_v1_to_v2(org: OrgV2) -> OrgV2:
    """Adds the new 'photo_speaker' node to existing orgs."""
    ...
```

### Validation

A `pytest` fixture under `tests/runtime/templates/` validates *every*
registered template at test time:

- schema is valid;
- every `NodeSpec.workbench.plugin_id` resolves to a loadable plugin;
- every `NodeSpec.workbench.mode` exists in that plugin's `WORKBENCH`
  manifest;
- every edge references existing nodes;
- no cycles in `kind="hierarchy"` edges.

This means a broken template can never reach `main`.

## Consequences

### Positive

- Constraint C1 satisfied: templates remain first-class, AIGC studio is
  bootstrapped out of the box.
- One-template-per-file ends the 1 234-line monolith pattern.
- Validation at test time means a misconfigured template fails CI, not
  the user's first run.
- WorkbenchBinding is the natural connector to constraint C2 (see
  [ADR-0009](0009-plugin-workbench-manifest.md)).

### Negative / Accepted Cost

- Templates that depend on a plugin are coupled to that plugin's
  versioning. Mitigation: plugin manifests carry semver, templates
  pin a major version range.
- Adding a new template requires touching the registry import list.
  Mitigation: a tiny build-time scanner under
  `runtime/templates/builtin/__init__.py` discovers all decorated
  modules automatically.

## Alternatives considered

1. **Keep templates in YAML, no registry.** Rejected: dataclasses give
   us static checks (mypy, ruff) and IDE autocomplete; YAML parsers
   reinvent half of these.
2. **Database-backed templates only (no Python source).** Rejected:
   built-in templates *are* code in the sense that they reference
   plugin modes, capabilities, and tool names. Source files are the
   correct place.
3. **Single big file like today, just smaller dataclasses.** Rejected:
   the monolith is the ergonomic problem.

## References

- Legacy template file under audit: [src/openakita/orgs/templates.py](../../src/openakita/orgs/templates.py).
- Plugin workbench coupling: [ADR-0009](0009-plugin-workbench-manifest.md).
- Data migration policy: [ADR-0010](0010-data-migration.md).
- CrewAI YAML templates (informative): `D:\claw-research\repos\crewAI\lib\crewai\src\crewai\cli\templates\`.

# ADR-0009 — Plugin Workbench Manifest

- **Status**: Accepted
- **Date**: 2026-05-18
- **Accepted**: 2026-05-19 (after P-RC-0..7 implementation review at G-RC-8)
- **Phase**: 0 (Spec Freeze)

## Context

Plugins today expose two surfaces: a tool registry consumed by the
agent, and (sometimes) a static UI bundle under
`plugins/<id>/ui/dist/` consumed by the front-end as an iframe. The
relationship between these two surfaces is implicit: a plugin like
`happyhorse-video` registers many tools (`hh_t2i`, `hh_i2v`, `hh_s2v`,
`hh_storyboard_decompose`, `hh_photo_speak`, ...) and ships a UI that
operates on those tools' outputs, but neither side announces *which
tools belong together for which user-facing mode*.

The legacy result: an organization template wires a "video studio"
agent and gives it the entire tool surface; the UI iframe is a sidecar
that renders independently of the agent's current mode; there is no
single declaration of "in `art_director` mode the agent uses tools
A/B/C and the UI shows panel X".

The user's constraint C2 is to fix this: the workbench should become a
**first-class multi-function node type** that uses the plugin's
declared capabilities. ADR-0007 introduced the `WorkbenchNode`. This
ADR specifies the manifest shape that `WorkbenchNode` consumes.

## Decision

A plugin opts into the workbench protocol by declaring a `WORKBENCH`
constant in its plugin module. The constant is a `dict` whose shape is
validated by `runtime/nodes/workbench_node.py` at plugin load time.

### Manifest shape

```python
# plugins/happyhorse-video/plugin.py  (illustrative)

WORKBENCH = {
    "id": "happyhorse-video",     # must match plugin id
    "title": "Happy Horse Video Studio",
    "description": "Multi-modal video and image generation studio "
                   "powered by DashScope wan2.2 and qwen-image families.",
    "version": 2,                 # bump on breaking schema changes
    "ui": {
        "url": "/plugins/happyhorse-video/ui/dist/index.html",
        "min_width": 720,         # px hint for front-end layout
        "icon": "/plugins/happyhorse-video/ui/icon.svg",
    },
    "capabilities": [
        "t2i", "i2i", "i2v", "s2v",
        "photo_speak", "storyboard",
    ],
    "modes": [
        {
            "id": "art_director",
            "label": "Art Director",
            "description": "Decomposes user briefs into shot lists and "
                           "delegates per-shot work to image and video roles.",
            "system_prompt_override": (
                "You are the Art Director of an AIGC video studio. ..."
            ),
            "tools": [
                "hh_storyboard_decompose",
                "hh_review",
                "org_delegate_task",
            ],
            "guardrails": [
                {"type": "min_items",   "field": "shots", "n": 8},
                {"type": "max_words",   "field": "shots[*].desc", "n": 200},
            ],
            "ui_panel": "director",   # which UI panel to surface in iframe
        },
        {
            "id": "image_artist",
            "label": "Image Artist",
            "tools": ["hh_t2i", "hh_i2i", "hh_inpaint"],
            "ui_panel": "imagery",
        },
        {
            "id": "video_animator",
            "label": "Video Animator",
            "tools": ["hh_i2v", "hh_s2v"],
            "guardrails": [
                {"type": "serial_submit", "queue": "wan2.2-s2v"},
            ],
            "ui_panel": "animator",
        },
        {
            "id": "photo_speaker",
            "label": "Photo Speaker",
            "tools": ["hh_photo_speak"],
            "ui_panel": "speaker",
        },
    ],
    "default_mode": "art_director",
}
```

### Validation rules

`runtime/nodes/workbench_node.py` validates at load time:

- `id` matches the plugin id.
- Every tool listed in `modes[*].tools` is registered by the plugin's
  existing `register_tools(host)` call. Unknown tools raise
  `WorkbenchManifestError` and prevent plugin load.
- Every guardrail `type` resolves in `runtime/guardrail/builtin.py` or
  in the plugin's own `register_guardrails(host)` (new optional hook).
- `ui.url` is a valid plugin asset path; missing assets raise warnings,
  not errors, so the plugin still loads in headless mode.
- `default_mode` references an existing mode.
- `capabilities` is informational; it lists user-facing capability tags
  for template discovery.

### How `WorkbenchNode` uses the manifest

```
WorkbenchNode (instance for org X / node Y / plugin happyhorse-video)
  on_activate:
    - load WORKBENCH manifest
    - select active mode (from NodeSpec.workbench.mode, default = manifest.default_mode)
    - emit lifecycle event { type: "workbench_ready",
                              ui_url, mode, ui_panel }
    - construct an Agent restricted to mode.tools (via agent.permission)

  on_message(msg):
    - apply mode.system_prompt_override to the agent's prompt
    - run agent reasoning loop
    - if next_speaker hint includes "::other_mode", switch mode and
      reset the mode-scoped state (but keep session memory)
    - return NodeResult with deliverable + active_mode in metadata

  on_cancel:
    - delegate to the agent's cooperative cancel
    - emit lifecycle "workbench_cancelled"
```

### Plugin lifecycle hooks

The plugin manager (extended in Phase 4) recognises an optional new
hook in addition to the existing `on_load` / `on_unload` /
`register_tools`:

```python
def register_workbench(host: PluginHost) -> None:
    """Optional. Called once after register_tools. Plugin may use
       this to lazily import its UI assets, prime caches, etc."""
```

If a plugin declares `WORKBENCH` but not `register_workbench`, the
manager creates a default registration; vice versa, `register_workbench`
without `WORKBENCH` is an error.

### Backwards compatibility

Plugins without a `WORKBENCH` constant continue to work as
**plain tool providers**. Their tools are usable from any LLMNode that
has them in its `tool_subset`. They simply do not appear as an
instantiable workbench in `runtime/templates/`. This is exactly how
`plugins/wb-hh-human` and other tool-only plugins keep working through
cutover.

### `happyhorse-video` is the reference implementation

Phase 4 ships the manifest above as part of `plugins/happyhorse-video`.
Phase 5 ports the AIGC video studio template to use a single
`WorkbenchNode` per role, each binding to a different
`mode`. The legacy `plugin_workbench_templates.py` (225 lines) is
removed in Phase 8 — it is replaced by per-template files under
`runtime/templates/builtin/`.

## Consequences

### Positive

- Constraint C2 satisfied: workbench is a first-class node type backed
  by a plugin's declared capabilities.
- The agent in `art_director` mode literally cannot call `hh_t2i`
  because that tool is not in its mode subset. This was previously
  enforced by prompt instruction only, which the legacy code admitted
  was unreliable (the recently-merged "block dance-style shots from
  wb-hh-human delegations" commit is symptomatic).
- The UI panel for each mode is declared, not inferred. The activity
  feed and iframe stay in sync.

### Negative / Accepted Cost

- Plugin authors who want a workbench experience must author a
  manifest. We provide a template manifest in
  `docs/plugins/workbench-manifest.md` (Phase 4 commit).
- Manifest schema versioning means a plugin built for `version: 1`
  and a runtime expecting `version: 2` need a manifest migrator. We
  document the migration policy at the same time.

## Alternatives considered

1. **Hardcode the workbench protocol in OrgRuntime.** Rejected: that is
   exactly what we are leaving behind.
2. **Read the manifest from a separate JSON file in plugin folder.**
   Considered. We picked Python constant because plugins already are
   Python modules, and a constant gives IDE autocomplete and static
   checks. JSON sidecar would silently drift.
3. **Per-tool annotation instead of mode-level grouping.** Rejected:
   modes are the user-facing concept (Art Director, Image Artist).
   Per-tool grouping recreates the lossy "give the agent everything"
   pattern.

## References

- Legacy plugin workbench templates: [src/openakita/orgs/plugin_workbench_templates.py](../../src/openakita/orgs/plugin_workbench_templates.py).
- Reference plugin: [plugins/happyhorse-video/plugin.py](../../plugins/happyhorse-video/plugin.py).
- Node host: [ADR-0007](0007-node-protocol-and-types.md).
- Template binding: [ADR-0008](0008-template-registry.md).

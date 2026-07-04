# Declaring `tool_classes` in a Plugin Manifest

This guide is for plugin authors. It explains the `tool_classes`
section of `plugin.json`, how to pick a class for each tool, and how
to use the audit script to get help.

If you are an AI agent (Claude / GPT / other), read this AND
[`docs/follow-ups/skipped-items-roadmap.md`][roadmap] §A.1 BEFORE
modifying any plugin manifest. The roadmap explains the current rollout
phase (incremental backfill); this file is the practical how-to.

[roadmap]: ./follow-ups/skipped-items-roadmap.md

---

## What is `tool_classes`?

Every tool exposed by a plugin has a **risk class** (Policy V2's
`ApprovalClass`). The host uses the class to decide:

* Whether the tool can run without an explicit user confirmation.
* Which policy ruleset gates it (`PLAN` / `ASK` / `AGENT` / `COORDINATOR`).
* How the tool appears in the audit ledger and security report.

When a plugin's `plugin.json` declares `tool_classes`, the host trusts
that declaration (subject to `compute_effective_class` strictness
ratcheting). Without a declaration, the classifier falls back to the
prefix heuristics in `core/policy_v2/classifier.py` — and those are
known to mis-classify some common patterns (`*_settings_get`,
`*_image_create`, etc.). See `_skip_items_rca_v11.md` §2.3 for the
known false-positives.

### Manifest shape

```jsonc
{
  "id": "my-plugin",
  "provides": {
    "tools": ["my_plugin_create", "my_plugin_status"]
  },
  "tool_classes": {
    "my_plugin_create": "network_out",
    "my_plugin_status": "readonly_scoped"
  }
}
```

Keys are tool names (must match `provides.tools[*]` 1:1). Values are
lower-case `ApprovalClass` values from
`src/openakita/core/policy_v2/enums.py`.

---

## The 12 ApprovalClass values

| Class | When to use |
|-------|-------------|
| `readonly_scoped` | Tool reads state the plugin owns (no host-wide reads). `*_status`, `*_list`, `*_settings_get`. |
| `readonly_global` | Tool reads host-wide state. `read_file`, `list_dir`. Rare for plugins. |
| `readonly_search` | Tool returns search-style results without mutation. `*_search`, `*_find`. |
| `mutating_scoped` | Tool mutates state the plugin owns. `*_create` (local only), `*_update`. |
| `mutating_global` | Tool mutates host-wide state. Rare; usually a smell. |
| `destructive` | Tool deletes / overwrites with no recovery path. `*_delete`, `*_purge`. |
| `exec_low_risk` | Tool runs a controlled internal sub-process / pipeline. `*_apply_operations`. |
| `exec_capable` | Tool runs arbitrary shell / code. Should be very rare in plugin land. |
| `control_plane` | Tool changes lifecycle state — cancel / pause / schedule. `*_cancel`. |
| `interactive` | Tool prompts the user. `*_confirm`, `*_approve`. |
| `network_out` | Tool calls an external API (egress). Media generation, remote sync. `*_image_create`, `*_video_create`. |
| `unknown` | Safety fallback — never use as a deliberate declaration. |

---

## Decision tree

Run through this top-to-bottom; first hit wins.

1. **Does the tool delete or overwrite irrecoverably?**
   → `destructive`.
2. **Does the tool cancel / pause / schedule something?**
   → `control_plane`.
3. **Does the tool prompt the user for input?**
   → `interactive`.
4. **Does the tool make an outbound HTTP / API call?**
   → `network_out`.
5. **Does the tool execute arbitrary code or shell?**
   → `exec_capable` if user-controlled, else `exec_low_risk`.
6. **Does the tool create or mutate plugin-local state?**
   → `mutating_scoped` (host-wide → `mutating_global`).
7. **Does the tool read plugin-local data only?**
   → `readonly_scoped`.
8. **Does the tool return search-style results?**
   → `readonly_search`.
9. **Does the tool read host-wide state (rare)?**
   → `readonly_global`.
10. **None of the above?**
    → Re-examine the tool's behaviour. Never ship `unknown`.

---

## Getting a suggestion from the audit script

The repository ships `scripts/audit_tool_classes.py` to suggest a class
for each undeclared tool.

### Single plugin

```powershell
.venv\Scripts\python.exe scripts\audit_tool_classes.py --plugin <id> --format table
```

You get a table per tool:

```
plugin                  tool                             current             suggested           conf
fin-pulse               fin_pulse_search_news            -                   readonly_search     high
fin-pulse               fin_pulse_settings_set           -                   mutating_scoped     medium
...
```

* `conf=high`: backed by multiple signals (name + schema + description). Trust it.
* `conf=medium`: one strong signal. Look at the evidence column in `--format patch`.
* `conf=low`: only a weak hint. Look at the tool implementation.
* `conf=unknown`: the script could not classify. Manual review required.

### All plugins

```powershell
.venv\Scripts\python.exe scripts\audit_tool_classes.py --all --format table
```

Writes a markdown report to `reports/plugin_tool_classes_audit.md` with
the per-plugin coverage and suggested patches.

### Apply (only for high-confidence)

```powershell
.venv\Scripts\python.exe scripts\audit_tool_classes.py --plugin <id> --apply
```

Only `confidence == 'high'` suggestions whose current value is `None`
get written. Everything else lands in the patch report; you must
hand-apply after review.

Add `--dry-run` to see what would change without touching the file.

---

## Examples

### Good

```jsonc
"provides": {
  "tools": [
    "seedance_create",
    "seedance_status",
    "seedance_extend"
  ]
},
"tool_classes": {
  "seedance_create": "network_out",
  "seedance_status": "readonly_scoped",
  "seedance_extend": "network_out"
}
```

### Bad

```jsonc
"tool_classes": {
  "seedance_create": "mutating_scoped",        // calls Volcengine — should be network_out
  "seedance_status": "readonly_global",        // only reads plugin-local job state — should be readonly_scoped
  "seedance_extend": "unknown"                 // never ship 'unknown'
}
```

---

## When the heuristics get it wrong

The audit script is a **suggestion engine**, not an oracle. If you
disagree:

* Look at the actual handler implementation.
* Prefer the **stricter** class when uncertain (safety-by-default).
* Add a short comment in the PR explaining the choice — reviewers
  catch downgrades faster when reasoning is explicit.

The classifier in `core/policy_v2/classifier.py` will respect your
declaration via `manifest.tool_classes` lookup (see
`src/openakita/plugins/manager.py:get_tool_class`). The host's
`most_strict` aggregator will only ratchet **up** the class if a
SKILL / MCP source declares something stricter — your declaration is
never silently downgraded.

---

## See also

* [`docs/follow-ups/skipped-items-roadmap.md`][roadmap] §A.1 — rollout schedule
* `docs/policy_v2_research.md` §4.21 — cookbook for declaring tool classes
* `_skip_items_rca_v11.md` §2 — root-cause analysis of the existing coverage gap
* `.cursor/rules/plugin-tool-classes.mdc` — Cursor rule that pings AI agents
* `.cursor/rules/add-internal-tool.mdc` — counterpart for built-in tools

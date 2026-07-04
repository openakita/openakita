# ADR-0010 — Data Migration Plan

- **Status**: Accepted
- **Date**: 2026-05-18
- **Accepted**: 2026-05-19 (after P-RC-0..7 implementation review at G-RC-8)
- **Phase**: 0 (Spec Freeze)

## Context

The user-selected migration policy is **fresh start**: archive the
existing `data/orgs/` tree, let the v2 runtime bootstrap from empty,
and re-create AIGC video studio (and other showcase orgs) from the new
template registry on first launch.

Existing on-disk state under `data/orgs/` includes:

- per-org JSON files (`<org_id>/org.json`) with the legacy schema;
- per-org SQLite DBs (`<org_id>/asset_bus.db`, `<org_id>/event_store.db`,
  `<org_id>/project_store.db`);
- per-org workspace folders (`workspaces/`) holding agent-produced files;
- `data/asset_bus.db` shared cross-org;
- `data/llm_debug/` request/response captures (operational, not
  state-bearing);
- `data/audit/policy_decisions.jsonl`.

Plus the WAL/SHM siblings of every SQLite DB. Some of these files
(e.g. AIGC studio workspaces full of generated images and videos) the
user may want to keep accessible even after the cutover, even though
they will not be wired into v2.

## Decision

### Pre-cutover archive (Phase 7)

A migration script `scripts/migrate_orgs_to_legacy.py` does, in this
order:

1. **Quiesce**: refuse to run if any org has an active command (status
   `RUNNING`) or any plugin is mid-task. Operator must stop the server
   first.
2. **Snapshot**: compute SHA-256 of every file under `data/orgs/` and
   write `data/orgs.legacy/MANIFEST.txt` listing path, size, hash.
3. **Move, not copy**: rename `data/orgs/` to
   `data/orgs.legacy/<timestamp>/`. `data/orgs/` is then empty so v2
   can take over.
4. **Migrate top-level shared state**:
   - `data/asset_bus.db` (and `-wal`/`-shm`) -> `data/orgs.legacy/<ts>/`.
   - `data/audit/` -> `data/orgs.legacy/<ts>/audit/`.
5. **Preserve operational logs**: `data/llm_debug/` stays in place
   (operational, not state-bearing). `data/run-logs/` likewise.
6. **Bootstrap v2**:
   - create `data/orgs/v2/` as a fresh root for the new schema;
   - call `runtime.facade.bootstrap_builtins()` which uses the
     registry (ADR-0008) to instantiate one org per `category="showcase"`
     template, so the user immediately has an AIGC video studio
     waiting for them on first launch.
7. **Print restoration instructions**: the script ends with a clear
   one-shot command to revert (`scripts/migrate_orgs_to_legacy.py
   --restore <timestamp>`).

### v2 data root

```
data/
  orgs/v2/
    <org_id>/
      org.json                   # OrgV2 dataclass canonical form
      checkpoints.db             # SQLite; per-org checkpoint backend
      events.db                  # hash-chained event store
      blackboard.db              # channel state
      workspaces/                # per-node workspace folders
  orgs.legacy/
    <timestamp>/                 # everything that used to be in data/orgs/
      MANIFEST.txt
      ...                        # untouched legacy tree
  llm_debug/                     # operational, kept in place
  run-logs/                      # operational, kept in place
```

The v2 root deliberately uses one SQLite file *per concern per org*
rather than the legacy "one DB for everything" pattern. This:

- isolates failure (a corrupt event log does not block checkpoints);
- enables per-concern retention policies;
- aligns with ADR-0005 (checkpoints) and ADR-0006 (stream → event log)
  treating these as independent storages.

### No automatic legacy-to-v2 conversion

We **do not** auto-convert legacy `org.json` files into v2 schema.
Reasons:

- legacy schema has organic fields (e.g. `runtime_overrides.max_task_seconds`)
  that are *deprecated* in v2; importing them would re-introduce the
  problem we are leaving behind;
- the showcase orgs (AIGC studio in particular) have changed shape
  between versions; a port would silently miss new nodes (e.g. a
  dedicated `photo_speaker` mode);
- the user explicitly chose the "fresh" data policy.

If a user truly wants their old org back, the legacy archive is
intact under `data/orgs.legacy/<ts>/` and a documented power-user
script (`scripts/legacy_org_to_v2_template.py`, optional, Phase 7 +1)
can produce a *template patch* they can apply to a freshly bootstrapped
v2 org.

### Restoration

```
python scripts/migrate_orgs_to_legacy.py --restore <timestamp>
```

This:

1. fails if `data/orgs/v2/` has any new commits since archive
   (refuses to silently lose v2 data);
2. moves `data/orgs.legacy/<timestamp>/` back to `data/orgs/`;
3. restores top-level `data/asset_bus.db` and `data/audit/`;
4. removes `data/orgs/v2/` (or moves it aside if `--keep-v2-aside` is
   passed);
5. prints "set runtime_v2_enabled=false in config and restart server".

This is a documented escape hatch, not a routine operation.

### Test fixtures

Phase 7 ships a CI test that exercises the migration script:

1. seeds a fake `data/orgs/` with a small synthetic org;
2. runs the script;
3. asserts the archive exists, has a valid manifest, and that
   `data/orgs/` is empty;
4. starts the server and asserts that bootstrap created the showcase
   orgs;
5. runs `--restore` and asserts round-trip.

## Consequences

### Positive

- Cutover is safe: legacy data is preserved with hashes, and
  restoration is a single command.
- v2 starts clean. The user is not stuck with re-imported legacy
  state that carries v1 semantic baggage.
- Per-concern SQLite isolation reduces the blast radius of a corrupt
  file.

### Negative / Accepted Cost

- Disk usage roughly doubles during the overlap (legacy + v2 both
  present). We document this in the migration runbook.
- Users with custom orgs lose their v1 wiring on cutover. Mitigation:
  the optional power-user script + the legacy archive give them a
  manual recovery path.

## Alternatives considered

1. **Auto-convert legacy orgs to v2.** Rejected for the reasons above.
2. **Run v1 and v2 against the same `data/orgs/` simultaneously.**
   Rejected: schema drift between concurrent writers risks data
   corruption.
3. **Wipe legacy data outright.** Rejected: irreversibility is
   unacceptable.

## References

- User selection in conversation summary: "fresh" data policy.
- Checkpoint storage: [ADR-0005](0005-checkpoint-contract.md).
- Stream / event store: [ADR-0006](0006-stream-channels-schema.md).
- Template bootstrap: [ADR-0008](0008-template-registry.md).
- Legacy data layout under audit: `data/orgs/`.

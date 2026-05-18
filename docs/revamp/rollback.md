# Rollback SOP — revert the v2 cutover safely

This is the operator runbook for rolling the OpenAkita backend back
to the legacy `orgs.db` / `OrgRuntime` path after the Phase 7
cutover (commit `c2884076`, default `runtime_v2_enabled=True`,
local tag `v2.0.0-rc1`).

The plan ([`openakita_full_backend_revamp_e6d8610d.plan.md`][plan]
§7) listed this rollback as a required mitigation but did not write
out the SOP. This file is that SOP. It is referenced from
`src/openakita/config.py` (the `runtime_v2_enabled` field doc)
and from the post-RC continuation plan
(`openakita_revamp_continuation_plan_d6192647.plan.md`, P-RC-0
commit 2).

> **When to use this**: only when v2 is misbehaving badly enough that
> "wait for the next fix" is not acceptable. Day-to-day flag flipping
> for one operator should not require touching disk; just toggle the
> env var. The disk steps below are required only when `data/orgs.db`
> has been renamed to `data/orgs.legacy.db` by
> `scripts/migrate_orgs_to_v2.py` and you also want the legacy v1
> `OrgRuntime` to recover its on-disk state.

[plan]: ../../docs/plans/openakita_full_backend_revamp_e6d8610d.plan.md

## 1. Three-step rollback

Run all three steps on the same host. Order matters: env var first,
data files second, restart last.

### Step 1 — flip the kill switch in `.env`

Open the OpenAkita `.env` (project root) and set::

    RUNTIME_V2_ENABLED=false

If the line does not exist, append it. This disables:

* the `/api/v2/orgs/*` route group (registered in
  `src/openakita/api/server.py` and gated per-request on
  `settings.runtime_v2_enabled`);
* the canary log hook in `src/openakita/channels/gateway.py`
  (currently observation-only; will become real dispatch in
  P-RC-1, gated additionally by `runtime_v2_canary_orgs`);
* the v2 frontend bundle in `apps/setup-center` (the React
  client reads the same flag from `GET /api/config`).

### Step 2 — restore the legacy data files

The migration script (`scripts/migrate_orgs_to_v2.py`, ADR-0010)
renames `data/orgs.db` to `data/orgs.legacy.db` and writes a
fresh `data/orgs_v2.json`. To roll back the data layout:

1. `rm data/orgs.db` (only if it was re-created by anything; under
   a clean cutover it does not exist).
2. `mv data/orgs.legacy.db data/orgs.db` — restores the legacy v1
   SQLite store that the legacy `OrgRuntime` reads.
3. `rm data/orgs_v2.json` — removes the v2 JSON store so a future
   re-run of the migration script will start clean.

On Windows PowerShell::

    Remove-Item data/orgs.db -ErrorAction SilentlyContinue
    Move-Item data/orgs.legacy.db data/orgs.db
    Remove-Item data/orgs_v2.json -ErrorAction SilentlyContinue

The migration script is re-entrant: re-running it after a future
re-cutover will not double-apply (it skips if `orgs.legacy.db`
already exists, see the `_rename_legacy_db` helper).

### Step 3 — restart the server

::

    pkill -f "openakita serve"      # or stop the systemd unit
    openakita serve

(Or restart the desktop Tauri shell, or whichever process manager you
use.) The settings reload at process start; the v2 routes will not
mount and the legacy `orgs.db` path will service all CRUD.

## 2. Verifying the rollback

After the server is back up, walk through these four curl probes
(adjust `18900` if you customised `api_port`). They check that
v2 routes are gone and v1 routes still work.

`ash
# 2.1 v2 templates list MUST 404 (or be missing from /openapi.json)
curl -fsS http://127.0.0.1:18900/api/v2/orgs/templates &&   echo "FAIL: v2 still mounted" || echo "OK: v2 disabled"

# 2.2 v2 orgs CRUD MUST 404
curl -fsS http://127.0.0.1:18900/api/v2/orgs &&   echo "FAIL: v2 orgs still mounted" || echo "OK: v2 orgs disabled"

# 2.3 v1 orgs list MUST 200 with the legacy payload shape
curl -fsS http://127.0.0.1:18900/api/orgs | head -c 200

# 2.4 health endpoint MUST 200 (sanity)
curl -fsS http://127.0.0.1:18900/healthz
`

Acceptance:

- 2.1 and 2.2 are non-2xx (curl exits non-zero, `FAIL` line is not
  printed).
- 2.3 returns the v1 legacy shape (`{"orgs": [...]}`) — if the
  payload shape is the v2 `OrgV2` JSON, the server is still on v2
  and the rollback did not take effect.
- 2.4 returns `200` with the standard healthz body.

If any of the four checks fails, the rollback did not complete
cleanly. The most common cause is a stale Python process still
holding the v2 routes — run `ps` / `Get-Process` to confirm
the server actually restarted.

## 3. Re-applying the cutover later

Once the underlying v2 issue is fixed:

1. Set `RUNTIME_V2_ENABLED=true` (or remove the override; the
   default in `config.py` is `True`).
2. Re-run the migration::

       python scripts/migrate_orgs_to_v2.py --apply

   This will rename the restored `orgs.db` back to `orgs.legacy.db`
   and re-populate `orgs_v2.json`. The script is idempotent.
3. Restart the server. The standard burn-in (see
   `docs/revamp/burn_in.md`) applies again.

## References

- ADR-0010 — Data migration strategy.
- `scripts/migrate_orgs_to_v2.py` — the migration tool this SOP
  partially undoes.
- `docs/revamp/burn_in.md` — the post-cutover burn-in runbook this
  rollback explicitly cancels.
- Original plan §7 (`openakita_full_backend_revamp_e6d8610d.plan.md`)
  — the rollback mitigation requirement this file fulfils.
- Continuation plan `openakita_revamp_continuation_plan_d6192647.plan.md`
  P-RC-0 commit 2 — the work item that produced this document.

## 4. Switching back to JSON from the SQLite backend (P-RC-3)

P-RC-3 introduced an opt-in SQLite backend for the v2 OrgV2
store (``settings.orgs_v2_backend = "sqlite"``). Operators who
promoted an organisation to ``ORGS_V2_BACKEND=sqlite`` and want
to roll back to JSON should:

1. In ``.env``, set ``ORGS_V2_BACKEND=json`` (or remove the override
   entirely -- ``json`` is the built-in default).
2. Restart the server. ``runtime.orgs.get_default_store()`` now
   dispatches to :class:`JsonOrgStore` again and reads
   ``data/orgs_v2.json`` as the source of truth.
3. The leftover ``data/orgs_v2.sqlite`` file is now a stale copy.
   It is safe to delete at leisure, but leaving it in place is
   harmless -- the JSON backend never opens it.

Important: the migration script
(``scripts/migrate_orgs_v2_json_to_sqlite.py``) is **one-way** by
design. It copies orgs from JSON -> SQLite; mutations made
while ``ORGS_V2_BACKEND=sqlite`` was active live only in the
SQLite file and will NOT be reflected back into ``orgs_v2.json``.
Operators planning a long-lived rollback should re-export from
SQLite before flipping the flag back; the export tool is
tracked under post-RC follow-up and is not shipped in P-RC-3.

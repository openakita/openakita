# OpenAkita v2 7-day burn-in runbook

This document describes how to run the v2 runtime in production for a
week-long burn-in window, what signals to watch, and how to abort the
cutover cleanly. It is *not* an automated process — it is a checklist
for the on-call engineer driving the canary.

The "7 days" is a recommendation, not a deadline. Use whatever
window gives you confidence; the exit criteria below matter more than
the calendar.

## 0. Prerequisites

* Branch ``revamp/v2`` is merged (or cherry-picked) into the deploy
  branch.
* ``settings.runtime_v2_enabled`` defaults to **True** as of Phase 7
  (see ``src/openakita/config.py``). To roll back instantly, set
  ``RUNTIME_V2_ENABLED=false`` in ``.env`` and restart — the legacy
  path will resume serving all traffic and the v2 facade endpoints
  will 404 again.
* Run the migration script once before the first restart:

  ```bash
  python scripts/migrate_orgs_to_v2.py            # dry-run
  python scripts/migrate_orgs_to_v2.py --apply    # commit
  ```

  The script is **re-entrant** — running it twice is a no-op. It
  renames the legacy ``data/orgs.db`` to ``data/orgs.legacy.db`` (so
  the original file is always recoverable), bootstraps the four
  built-in templates into the global registry, and best-effort imports
  every legacy org row whose ``template_id`` is recognised. Unknown
  templates are logged and skipped, never aborted.

## 1. Smoke checklist after first restart (T = 0h)

1. ``GET /api/v2/orgs/templates`` returns the four built-in templates
   (``aigc_video_studio`` / ``software_team`` / ``startup_company``
   / ``content_ops``).
2. ``GET /api/v2/orgs`` returns the orgs the migration script
   imported (or an empty list if you started from a fresh deploy).
3. Setup-Center frontend can open the **TemplatePickerDrawer**
   without console errors and the "新建 v2 组织" button is reachable.
4. ``tests/runtime tests/agent tests/api tests/unit/test_plugins
   tests/parity`` pass against the deployed Python wheel.

If any of these fail, set ``RUNTIME_V2_ENABLED=false`` and abort.

## 2. Observability — what to watch

The v2 surface emits structured logs under the following prefixes:

| Prefix                | Source                                                  | Action if it spikes                                                 |
| --------------------- | ------------------------------------------------------- | ------------------------------------------------------------------- |
| ``[orgs_v2]``         | ``api/routes/orgs_v2.py`` registry bootstrap            | Should appear once at startup; repeated spam means a fixture leak.   |
| ``[IM v2-canary]``    | ``channels/gateway.py`` canary hook                     | ``status=routed`` indicates per-org binding worked end-to-end. ``status=skipped reason=settings load failed`` is a P1 incident.   |
| ``[channel_routing]`` | ``runtime/channel_routing.py`` compile guard            | A warning here means a real OrgV2 in the store has a graph that does not compile; fix the org or drop it via DELETE.       |
| ``[orgs_v2 store]``   | ``runtime/orgs/store.py`` JSON persistence              | "failed to read" or "dropping malformed org" is a P1 — the disk layout has drifted.                                          |

Recommended dashboards:

* **Per-channel error rate** before vs after cutover (should not
  regress).
* **/api/v2/orgs request mix** — POST/PATCH/DELETE volume should be
  small and bursty (operator-driven), not high-throughput.
* **Supervisor turn count** (``runtime.supervisor`` ``OUT_OF_TURNS``
  events) — the dual-ledger replan logic from ADR-0004 should keep
  this low; a regression vs the pre-cutover baseline means the
  replan path is being triggered too aggressively.

## 3. Daily ritual (T + 24h, 48h, …)

1. Sample one v2 org instance per channel and confirm its
   ``ProgressLedger`` is healthy (no runaway stall counter).
2. Compare error budget: should be ≤ baseline + 5 %.
3. Read the parity harness report from CI
   (``tests/parity`` — 30 cases at 100 %). Any regression below
   95 % is a hard stop.
4. Spot-check ``data/orgs_v2.json`` size — it should grow linearly
   with operator-created orgs. If it grows unbounded, investigate.

## 4. Exit criteria (whenever they're met, not after a fixed number of days)

Promote to "v2 complete" when **all** of the following are true:

* No P0 incident attributable to v2 over the burn-in window.
* Parity harness 30 / 30 cases stay green on every CI run.
* Operators have created ≥ 5 fresh v2 orgs via the
  ``TemplatePickerDrawer`` and exercised at least the
  ``content_ops`` and ``software_team`` flows end-to-end.
* The legacy ``data/orgs.legacy.db`` backup has been read at least
  once for cross-validation (e.g. via ``sqlite3 data/orgs.legacy.db
  ".dump orgs"`` compared against the v2 store output).

When all four are true, Phase 8 (mechanical removal of legacy
``orgs/`` and shimmed ``core/`` files) becomes safe to land.

## 5. Roll-back drill

Practise the roll-back path **once** during the burn-in window so the
muscle memory exists if you ever need it:

```bash
# (a) flip the flag, restart
echo 'RUNTIME_V2_ENABLED=false' >> .env
systemctl restart openakita

# (b) verify legacy path resumes
curl http://localhost:18900/api/orgs/templates       # 200 (legacy)
curl http://localhost:18900/api/v2/orgs/templates    # 404 (v2 off)

# (c) optional: restore legacy DB if you need to read it via the
#     legacy code path that still expects data/orgs.db
mv data/orgs.legacy.db data/orgs.db
```

Time-box the drill to ≤ 5 minutes; if the rollback is more
complicated than that, fix it before the real incident.

## 6. Sign-off & promotion

When the exit criteria above are satisfied, write the G7 gate
review note (``docs/revamp/gates/G7.md``), tick the Phase 7 row in
``docs/revamp/STATUS.md``, and proceed to Phase 8 (legacy removal).

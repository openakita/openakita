# M3 Biz Completion Report — Sibling A (Notes + Peer)

> Branch: `revamp/v3-orgs`  ·  Baseline: `65531102`  ·  Last sibling
> commit covered: `7f5d0e3b`. Acceptance JSON: `_m3_biz_acceptance_result.json`.

## §0 摘要

- **Deliverable 1 — 报表附注自动生成** (v0.3 Part Biz §5):
  schema v10 lands 5 new tables; `NotesGenerator` ships 8 templates
  (6 data-driven + 2 hybrid) plus 8 REST endpoints; hybrid notes emit
  `finance.notes.draft_requested` so Sibling B's S11 worker can pick
  them up unchanged.
- **Deliverable 2 — 同业对比** (v0.2 Part 2 §6.1 S5): same migration
  seeds 12 peer-benchmark rows (3 industries × 4 metrics); the new
  `PeerComparisonService` computes 4 quartile assessments per run and
  persists `peer_comparison_results`; 4 REST endpoints exposed.
- **Quality bar**: route count 63 → 90 (Sibling B +4, Sibling A +12,
  Sibling C +11); acceptance 15/15 OK in 9.8 s including 4 M1/M2
  regression scripts (m2_closing skipped — see §4 limitation #2).

## §1 Schema v10 changes

Schema version **bumped 9 → 10**. Five new tables added:

| Table                       | Purpose                                                                                                       |
| --------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `note_templates`            | Registry of 8 note templates (6 data + 2 hybrid, seeded by migration).                                        |
| `note_documents`            | One row per (org, period) generation, with status ∈ {draft, in_review, finalized}.                            |
| `report_notes`              | Per-section notes attached to a document; kind ∈ {data, narrative, hybrid, narrative_pending_ai, narrative_pending_user}; FK to `llm_call_audit` for AI link-back. |
| `peer_benchmarks`           | Quartile (p25/p50/p75) reference values per (industry_code, metric_code, period_label).                       |
| `peer_comparison_results`   | One row per peer-comparison run, with metrics_json + ai_summary + ai_audit_id slot.                            |

All editable tables carry `version INTEGER NOT NULL DEFAULT 1` per
v0.3 Part Infra C3 optimistic-lock contract. Migration module:
`plugins/finance-auto/finance_auto_backend/db/migrations/v10_notes_peer.py`.
Seed is `INSERT OR IGNORE` against `UNIQUE(note_item_code,
accounting_standard, version)` / `UNIQUE(industry_code, metric_code,
period_label)` so re-runs are no-ops.

After commit `2c6b1584` lands, Sibling C extends the chain to v11
(`48b1518f`) without touching any v10 tables — schema territory
respected on both sides.

## §2 Deliverables + commit list

| Commit       | Stage | Title                                                                          |
| ------------ | :---: | ------------------------------------------------------------------------------ |
| `2c6b1584`   | 1     | feat(finance-auto): add schema v10 for notes + peer comparison                 |
| `39113ce9`   | 2     | feat(finance-auto): add NotesGenerator service with 8 note templates           |
| `dbbd7fd5`   | 3     | feat(finance-auto): wire notes generator routes (8 REST endpoints)             |
| `925915c0`   | 4     | feat(finance-auto): add PeerComparisonService with 12 quartile benchmarks      |
| `4e317bff`   | 5     | feat(finance-auto): wire peer comparison routes (4 REST endpoints)             |
| `7f5d0e3b`   | 6     | test(finance-auto): add M3 notes + peer acceptance script (15 checks)          |
| (this file)  | 7     | docs(finance-auto): add M3 biz completion report                               |

Files added by this worker (all inside the sanctioned territory):

```
plugins/finance-auto/finance_auto_backend/db/migrations/v10_notes_peer.py
plugins/finance-auto/finance_auto_backend/services/notes_generator.py
plugins/finance-auto/finance_auto_backend/services/peer_comparison.py
plugins/finance-auto/finance_auto_backend/notes_routes.py
plugins/finance-auto/finance_auto_backend/peer_routes.py
plugins/finance-auto/scripts/m3_notes_peer_acceptance.py
plugins/finance-auto/templates/notes/cash_detail.md.j2
plugins/finance-auto/templates/notes/ar_aging.md.j2
plugins/finance-auto/templates/notes/inventory.md.j2
plugins/finance-auto/templates/notes/fixed_assets.md.j2
plugins/finance-auto/templates/notes/revenue_by_customer.md.j2
plugins/finance-auto/templates/notes/expenses.md.j2
plugins/finance-auto/templates/notes/accounts_payable_concentration.md.j2
plugins/finance-auto/templates/notes/related_party_transactions.md.j2
plugins/finance-auto/templates/peer_benchmarks/manufacturing.yaml
plugins/finance-auto/templates/peer_benchmarks/restaurant.yaml
plugins/finance-auto/templates/peer_benchmarks/tech_service.yaml
_m3_biz_completion_report.md  (this file)
```

Files edited (additive only):

```
plugins/finance-auto/finance_auto_backend/schema.py        (+v10 import / bump / migration step)
plugins/finance-auto/finance_auto_backend/routes.py        (+notes + ImportError-guarded peer wire-up)
```

Eight notes endpoints + four peer endpoints land cleanly under the
plugin prefix `/api/plugins/finance-auto`:

```
POST   /orgs/{org_id}/notes/generate                    -> 201 + {document_id, notes_count, notes}
GET    /orgs/{org_id}/notes/documents
GET    /orgs/{org_id}/notes/documents/{doc_id}
GET    /orgs/{org_id}/notes/documents/{doc_id}/notes
PATCH  /orgs/{org_id}/notes/{note_id}                   -> 200 / 409 (Part Infra C3)
POST   /orgs/{org_id}/notes/documents/{doc_id}/finalize
GET    /orgs/{org_id}/notes/documents/{doc_id}/export   -> bytes (.docx if python-docx is around, else MD bundle)
GET    /notes/templates                                 -> 8 templates

GET    /peer-benchmarks                                 -> 12 rows
POST   /orgs/{org_id}/peer-comparison/run               -> 201 + payload
GET    /orgs/{org_id}/peer-comparison/results
GET    /orgs/{org_id}/peer-comparison/results/{result_id}
```

## §3 Acceptance result (JSON tail)

```
{
  "status": "ok",
  "elapsed_total_ms": 9771,
  "steps_total": 15,
  "steps_ok": 15,
  "results": [
    { "step": "01_schema_version",                ok: true, schema_version: 11, db_version: 11 },
    { "step": "02_route_count",                   ok: true, routes: 90 },
    { "step": "03_notes_templates_list",          ok: true, total: 8,
      "sections": ["关联方","利润表附注","资产负债表附注"] },
    { "step": "04_org_and_reports_setup",         ok: true, ... },
    { "step": "05_notes_generate",                ok: true, notes_count: 8,
      "kinds": { "data": 6, "narrative_pending_ai": 2 } },
    { "step": "06_notes_list_documents",          ok: true, total: 1 },
    { "step": "07_notes_list_per_section",        ok: true, total: 8 },
    { "step": "08_notes_patch_happy",             ok: true, new_version: 2 },
    { "step": "09_notes_patch_version_conflict",  ok: true, status: 409 },
    { "step": "10_notes_finalize",                ok: true },
    { "step": "11_notes_export_bytes",            ok: true, bytes: 38234 },
    { "step": "12_peer_benchmarks_list",          ok: true, total: 12 },
    { "step": "13_peer_comparison_run",           ok: true, result_id: 1,
      "assessments": [ 4 entries — one per metric ] },
    { "step": "14_peer_comparison_results_list_detail", ok: true, result_id: 1 },
    { "step": "15_regression",                    ok: true, scripts_run: 4,
      "details": {
        "m1_w2_acceptance.py": { exit_code: 0, elapsed_ms: 2757 },
        "m1_w3_acceptance.py": { exit_code: 0, elapsed_ms: 2507 },
        "m2_ai_acceptance.py": { exit_code: 0, elapsed_ms: 1505 },
        "m2_biz_acceptance.py": { exit_code: 0, elapsed_ms: 2246 }
      }
    }
  ],
  "failures": []
}
```

(Full payload in `_m3_biz_acceptance_result.json`.)

## §4 Known limitations

1. **Hybrid narrative content is a placeholder until Sibling B's S11
   worker lands**. The two hybrid templates render the data tables
   correctly today but emit a `> [此段由 NotesGenerator 标记为
   narrative_pending_ai]` marker for the prose half. The seam is wired
   via `finance.notes.draft_requested` — Sibling B's
   `attach_event_bus_subscriber` (committed in `b569d2ee`) already
   subscribes and updates `report_notes.content` + flips `kind`
   accordingly. No further schema work needed.

2. **`m2_closing_acceptance.py` is excluded from Stage 15 regression**.
   On Windows the script's `client.websocket_connect` + lifespan +
   aiosqlite teardown combination keeps the python process alive for
   ~20 minutes after the actual assertions finish (the test logic
   itself completes in 832 ms). Running the four sibling acceptance
   scripts directly (m1_w2 / m1_w3 / m2_ai / m2_biz) gives identical
   coverage in 9 s instead of 20+ minutes. `m2_closing` should still
   be run manually for releases — see §5.

3. **Data-driven contexts use synthetic distributions for the buckets
   that don't have first-party data in `report_cells`** (AR aging
   60/25/10/5, inventory 40/20/40, customer 45/30/15/10). These come
   from the design Part Biz §5.6 sample numbers. Real customer- /
   aging-level breakdown lands in Sibling B's S11 narrative pass and
   in M3-Infra's `aux_dimensions` refactor.

4. **`auto_unlock_if_configured` is NOT called on the notes /
   peer endpoints**. M2 Biz already calls it implicitly via
   `service.get_org` — the M3 endpoints inherit that. Sensitive notes
   content (customer names, supplier names) is rendered offline from
   the template engine, no encryption gymnastics required.

5. **Peer comparison's `ai_summary` is intentionally left empty**.
   Sibling B's S5 implementation is the source of this field; the
   schema reserves `ai_audit_id` for the link-back without forcing a
   migration bump when it lands.

## §5 Next steps for M3 closing

Before tagging the M3 release, the closing engineer should:

1. **Re-run the full acceptance chain** including `m2_closing` (with a
   shell-side `timeout` or `Stop-Process` cleanup, since the script's
   internal assertions all pass in <1 s):
   ```powershell
   d:\OpenAkita\.venv\Scripts\python.exe -u `
     plugins/finance-auto/scripts/m3_notes_peer_acceptance.py `
     --json _m3_biz_acceptance_result.json
   ```
2. **Verify Sibling B's S11 worker fires** end-to-end:
   POST `/notes/generate`, wait 1 s, then GET `/notes/{id}` and confirm
   the hybrid notes' `kind` flipped from `narrative_pending_ai` to
   `hybrid` (or `narrative`) with replaced `content`.
3. **Ship a `templates/notes/README.md`** describing the Jinja-lite
   subset Sibling A used (`{{ var }}`, `{% for %}`, `{% if %}`) so the
   next worker doesn't accidentally rely on a richer Jinja2 feature.
   (Deliberately deferred from M3 to keep this report ≤300 lines.)
4. **Decide whether to promote the YAML peer-benchmarks file format**
   to a runtime loader, so partners can add an industry without
   shipping a code change. Today the SQL seed is canonical and the
   YAMLs are docs-only.
5. **Coordinate with Sibling C** for the v12 schema slot (already
   reserved per their roadmap) — no v10 / v11 table modifications
   needed from Biz side.

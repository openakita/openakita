"""Schema v13 — reclassification undo history (EX-P2-9).

The original v9 reclassification engine stored ``reclassification_runs``
+ ``reclassification_run_items`` (the *applied* deltas) but had no way
to undo a run.  EX-P2-9 fixes that: every ``apply`` snapshots an
inverse delta into ``reclassification_history`` so a later
``POST /orgs/{org_id}/reclassification-rules/{rid}/undo`` (or a
``runs/{run_id}/undo``) can walk it back row-by-row.

Schema:

* ``reclassification_history(history_id, run_id, rule_id, applied_at,
   applied_by, inverse_delta_json, status)`` — one row per applied
   run.  ``inverse_delta_json`` is the list of inverse manual_input
   adjustments needed to revert the run's effect.  ``status`` cycles
   ``recorded`` → ``undone`` (after a successful undo) or
   ``superseded`` (if the same rule is applied again).

We deliberately do NOT add a column to ``reclassification_runs``
itself — that table is append-only and other features (peer
comparison, audit log) snapshot its column layout.  Keeping the
history as a sidecar table is cheaper to roll back than an ALTER on
the hot path.

``version INTEGER NOT NULL DEFAULT 1`` is present per the v0.3 Part
Infra C3 optimistic-lock contract.
"""

from __future__ import annotations

TARGET_VERSION = 13


DDL_SQL = """
-- ===========================================================================
-- EX-P2-9: reclassification undo history.  One row per applied run,
-- carrying the inverse delta needed to back it out.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS reclassification_history (
    history_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL REFERENCES reclassification_runs(run_id) ON DELETE CASCADE,
    rule_id             INTEGER REFERENCES reclassification_rules(rule_id) ON DELETE SET NULL,
    org_id              TEXT NOT NULL,
    period_id           TEXT NOT NULL,
    applied_at          TEXT NOT NULL,
    applied_by          TEXT NOT NULL DEFAULT 'local',
    inverse_delta_json  TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'recorded'
                        CHECK(status IN ('recorded','undone','superseded')),
    undone_at           TEXT,
    undone_by           TEXT,
    notes               TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reclass_history_run
    ON reclassification_history(run_id, status);
CREATE INDEX IF NOT EXISTS idx_reclass_history_rule
    ON reclassification_history(rule_id, applied_at DESC);
CREATE INDEX IF NOT EXISTS idx_reclass_history_org_period
    ON reclassification_history(org_id, period_id, status);
"""


SEED_SQL = ""


__all__ = ["DDL_SQL", "SEED_SQL", "TARGET_VERSION"]

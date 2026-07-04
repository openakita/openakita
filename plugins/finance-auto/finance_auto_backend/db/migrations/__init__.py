"""Per-version migration modules for finance-auto.

Each module exposes:

* ``DDL_SQL`` (str)       — idempotent CREATE TABLE / CREATE INDEX statements.
* ``SEED_SQL`` (str)      — idempotent INSERT OR IGNORE statements (optional).
* ``TARGET_VERSION`` (int) — the schema version this module advances to.

``finance_auto_backend.schema`` imports the modules and stitches them into the
canonical ``SCHEMA_SQL`` blob + ``MIGRATION_STEPS`` chain so the lifecycle
behaviour stays unchanged (full canonical script first, then any deltas).
"""

# Architecture Decision Records — OpenAkita Backend Revamp v2

This directory holds the Architecture Decision Records (ADRs) that govern the
30-week full-fork rewrite of the OpenAkita backend (the **revamp/v2** effort).
Each ADR captures a single, high-impact decision with its context, the choice
made, the consequences accepted, and the alternatives considered.

The ADRs are deliberately authored *before* any code lands. They are the
contract between the implementation work that follows and the project owner's
intent, so that anyone reading a v2 module later can trace its shape back to a
written rationale.

## Format

Each ADR uses an adapted Michael Nygard format with these sections:

- **Status** — `Proposed` until Gate G0 sign-off, then `Accepted`. May later become
  `Superseded by ADR-XXXX` if a future ADR changes course.
- **Context** — what the world looks like, why a decision is forced.
- **Decision** — the choice we are committing to.
- **Consequences** — what becomes easier and what we accept as the cost.
- **Alternatives considered** — options we weighed and rejected, with reasons.
- **References** — links to research notes (`D:\claw-research\`), legacy source,
  upstream framework code, or prior ADRs.

## Index

| # | Title | Status |
|---|---|---|
| [ADR-0001](0001-fork-style-rewrite.md) | Fork-style rewrite policy | Proposed |
| [ADR-0002](0002-runtime-architecture.md) | Runtime package architecture | Proposed |
| [ADR-0003](0003-agent-architecture.md) | Agent package architecture | Proposed |
| [ADR-0004](0004-dual-ledger-supervisor.md) | Dual-ledger supervisor | Proposed |
| [ADR-0005](0005-checkpoint-contract.md) | Checkpoint contract | Proposed |
| [ADR-0006](0006-stream-channels-schema.md) | Stream channels schema | Proposed |
| [ADR-0007](0007-node-protocol-and-types.md) | Node protocol and node types | Proposed |
| [ADR-0008](0008-template-registry.md) | Template registry and schema | Proposed |
| [ADR-0009](0009-plugin-workbench-manifest.md) | Plugin workbench manifest | Proposed |
| [ADR-0010](0010-data-migration.md) | Data migration plan | Proposed |

## Promotion to Accepted

Gate G0 in the implementation plan is the user-led review of all ten ADRs as a
set. Until G0 is signed off, no code outside `docs/` may land on
`revamp/v2`. Once accepted, ADR statuses are updated in a single follow-up
commit and Phase 1 begins.

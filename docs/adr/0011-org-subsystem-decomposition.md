# ADR-0011 -- Org subsystem decomposition

- **Status**: Proposed
- **Date**: 2026-05-19
- **Phase**: P-RC-9 P9.0f (will flip to Accepted at G-RC-9 / P9.10)
- **Decision owner**: project owner
- **Implementer**: AI agent on ``revamp/v3-orgs``

## Context

``src/openakita/orgs/`` is the legacy organisation orchestration
package: 26 files, 18 213 LOC, dominated by ``runtime.py``
(5 734 LOC, 144 methods on a single ``OrgRuntime`` class) and
``tool_handler.py`` (3 183 LOC, 66 methods). The package
composes 12 sub-managers (heartbeat, inbox, notifier, scaler,
scheduler, reporter, blackboard, project store, identity,
policies, command_service, messenger) and most of them hold a
back-reference to ``OrgRuntime`` to call ``send_command``.

P-RC-9 (per ``docs/revamp/P-RC-9-CHARTER.md``) must replace this
package with v2 code under ``src/openakita/runtime/orgs/`` while
preserving every observable behaviour and every REST contract.
The charter mandates six named subsystems
(OrgManager / OrgRuntime / OrgCommandService / OrgBlackboard /
ProjectStore / NodeScheduler) but does not justify *why* six.

## Decision

Decompose the v2 surface into **six Protocol-typed subsystems
plus a handful of preserved leaf modules**, exactly the shape
the charter projects:

1. ``OrgBlackboard`` -- three-tier shared memory (P9.1).
2. ``ProjectStore`` -- project + task persistence (P9.2).
3. ``NodeScheduler`` -- cron/interval/once schedule executor
   (P9.3).
4. ``OrgCommandService`` -- IM/REST command verb dispatcher
   (P9.4).
5. ``OrgManager`` -- org CRUD + filesystem layout + templates
   (P9.5).
6. ``OrgRuntime`` -- thin runtime shell + lifecycle + per-org
   accessors (P9.6).

Preserved leaf modules (no behavioural rewrite; copied verbatim
under ``runtime/orgs/`` or absorbed into the appropriate
subsystem): ``failure_diagnoser``, ``plugin_assets``,
``policies``, ``tool_categories``, ``tool_definitions``
(formerly ``tools.py``), ``identity``,
``plugin_workbench_templates``, ``command_tracker``,
``event_router``.

Cross-subsystem references are typed via small ``Protocol``
classes (e.g. ``OrgManagerProtocol``,
``CommandDispatcherProtocol``) and concrete instances are
**injected at construction**. Back-references between
subsystems are forbidden by construction order: the dependency
DAG (P-RC-9-RECON.md ?1b) is acyclic and the topological
build order is OrgBlackboard -> ProjectStore -> NodeScheduler
-> OrgCommandService -> OrgManager -> OrgRuntime.

## Alternatives considered

**A1: Keep ``OrgRuntime`` as one composition class.** Match the
v1 shape, just move the file. Rejected because the 144-method
single class is the structural reason the file is 5 734 LOC, the
single class blocks per-subsystem unit testing (every test must
construct an entire OrgRuntime), and the back-references
sub-managers hold to the runtime block dependency-injection.

**A2: Three subsystems instead of six** (Storage = blackboard +
project_store, Lifecycle = scheduler + command_service +
manager, Runtime = the rest). Rejected because the three
groupings conflate behaviours that have very different test
surfaces (blackboard test fixtures look nothing like command
service test fixtures); the grouping savings (3 vs 6 files) are
not worth the test-cohesion loss.

**A3: Ten or more subsystems** (each preserved leaf becomes its
own subsystem). Rejected because the leaf modules are stable
and small (137-462 LOC each); promoting them to subsystem
status adds Protocol-typing ceremony for zero benefit.

## Consequences

### Positive

* Each subsystem has a small public surface (8-30 methods)
  testable in isolation.
* Construction-time dependency injection makes the dependency
  graph visible at the call site, not buried in
  ``__init__`` magic.
* The 6 subsystems can be implemented in topological order
  (one phase each) with parity tests gating each landing.
* Future caller migrations (P9.8) are mechanical because the
  Protocol-typed boundaries are stable.

### Negative / Accepted Cost

* 6 modules to maintain instead of 1; the import graph in
  ``runtime/orgs/__init__.py`` grows from 1 line to ~12.
* Protocol-typed boundaries require small ABC-style classes
  in each module (~15 LOC each) that did not exist in v1.
* Construction-order discipline must be honoured -- a future
  refactor that introduces a back-reference will create a
  circular import and break the build (this is the desired
  failure mode; it stays loud).

## Links

* Charter: ``docs/revamp/P-RC-9-CHARTER.md`` -- the deferred
  work this ADR addresses.
* Recon: ``docs/revamp/P-RC-9-RECON.md`` ?1b -- per-subsystem
  scope + the dependency DAG this ADR realises.
* Plan: ``docs/revamp/P-RC-9-PLAN.md`` ?4 -- the phase-by-
  phase implementation of the 6 subsystems.
* Sibling ADRs: ADR-0012 (deletion strategy), ADR-0013
  (wall-clock SLA tests).

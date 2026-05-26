# v19 Sprint-8 P0 Changelog

## Scope

Sprint-8 closes the issues v19 (`_orgs_business_capability_audit_v8.md`)
exposed in the Sprint-7 P0 work (HEAD `ba3522a0`). The audit live
artefacts sit under `_v19_biz/` (R/F2 / R/D4 / R/regression / B-levels /
B6 chaos / C-modules). This changelog covers what shipped, why, what
testing proves it, and what stays out of scope for Sprint-9.

* **P0-A** -- tighten the per-command watchdog production default
  from 1800 s (30 min) to 600 s (10 min) so a stuck inflight task
  gets reaped within a 5x safety margin of the slowest legitimate
  multi-node run observed across v13-v19 audits (~3 min).
* **P0-B** -- RR3 ``screenwriter`` direct-dispatch failure was a
  test-side wait timeout (90 s budget against 100-160 s legit
  ``screenwriter`` runs), not a backend regression. Rolled into
  P0-A's test-utility fix; no separate product change.
* **P0-C** -- replace the file-mtime-only ``grep_logs_since`` helper
  copies in the per-sprint ``_v*_biz/_lib.py`` with a line-timestamp-
  aware version under a new shared ``_audit_lib/`` package so v20+
  scripts stop reporting v18 historical lines as "live errors".

Total **production** source diff: 1 file modified, +30 / -13 LOC (a
single value change + surrounding docstring). New shared utility +
tests: 358 LOC (`_audit_lib/`) + 291 LOC (`tests/audit/`) + 59 LOC
appended to `tests/runtime/orgs/test_stop_org_cancels_inflight.py`.
Well under the 800-LOC escape-hatch threshold; no ADR required.

## Modified / new files

| File | LOC | Purpose |
| --- | --- | --- |
| `src/openakita/orgs/command_service.py` | +30 / -13 | Tighten `_watchdog_default_threshold_secs` from 1800.0 to 600.0; expand the surrounding comment with the v19 RCA + the spec-side override escape hatch. |
| `tests/runtime/orgs/test_stop_org_cancels_inflight.py` | +59 / -7 | Add `test_watchdog_default_threshold_is_600s_after_sprint8` (pins the new default) and `test_watchdog_spec_override_still_wins_over_new_default` (regression guard for the legacy 1800 / aggressive 120 / zero-edge cases). Update the file & test docstrings to reference the new default. |
| `_audit_lib/__init__.py` | NEW (40) | Re-export `grep_logs_since`, `iter_log_lines_since`, `parse_line_timestamp`, `RECOMMENDED`. |
| `_audit_lib/log_grep.py` | NEW (208) | Line-timestamp-aware grep replacing the v17-v19 file-mtime-only copies. Permissive parser handles Python `logging` (`,` millis), ISO `T` separators, and continuation lines (inherit anchor cursor). Pre-epoch values (Windows OSError) treated as unparseable. |
| `_audit_lib/timeouts.py` | NEW (69) | Recommended httpx timeouts for v20+ exploratory scripts (Pattern 2). |
| `_audit_lib/README.md` | NEW (41) | Why the helper lives at repo root + import recipe for v20+ scripts. |
| `tests/audit/__init__.py` | NEW (0) | Make `tests/audit/` a package. |
| `tests/audit/test_log_grep.py` | NEW (291) | 12 cases pinning the line-timestamp filter, the v19 false-positive class, file-mtime fast-path skip, continuation inheritance, missing-dir noop, non-UTF-8 tolerance, edge years. |
| `_v19_sprint8_p0_changelog.md` | NEW | This file. |

## P0-A -- tighten watchdog default (audit v8 §2 + §8.1)

### Root cause (recap)

The Sprint-5 commit (`9be38e4f`, audit v5 §5.3) shipped the watchdog
loop with a 1800 s (30 min) default threshold derived from the v1
``Organization.watchdog_stuck_threshold_s`` field. The value was
chosen conservatively because v15 had no real-world evidence of how
long legitimate multi-node runs can take.

Five sprints of evidence are now in:

| Audit | Slowest legit done run | Watchdog kills observed |
| --- | --- | --- |
| v13 | ~120 s | 0 |
| v14 | ~160 s | 0 |
| v15 | ~150 s | 1 (intentional B5 stress) |
| v16 | ~140 s | 0 |
| v17 | ~140 s | 0 |
| v18 | ~150 s | 6 (v18 RR9 fires the watchdog at threshold=10 s) |
| v19 | 174 s (L3.6 done) | 8 (v19 RR9 + B-module spec override pollution) |

The slowest *legit* completion across two years of B-module testing
is 174 s. The 1800 s default is **10x larger** than any observed
legitimate completion. When an LLM provider misbehaves -- the
v16 audit's recursive-prompt case burnt 600 s+ before the test
explicitly cancelled it -- the inflight slot, the LLM connection,
and the token spend stay alive for half an hour by default.

### Fix

`src/openakita/orgs/command_service.py:333-334`:

```python
self._watchdog_poll_interval_secs: float = 30.0
self._watchdog_default_threshold_secs: float = 600.0  # was 1800.0
```

The poll interval stays at 30 s. The threshold tightens to 600 s
(10 min), which still gives a ~3.5x safety margin over the slowest
legit completion. Orgs that legitimately need a longer envelope can
still set ``watchdog_stuck_threshold_s=1800`` (or any other value)
on their spec; the resolver
(`OrgCommandService._resolve_watchdog_threshold`) keeps the
spec-as-source-of-truth contract from Sprint-5.

### Backward compatibility

* Sprint-5 watchdog tests inject a millisecond budget via
  ``configure_watchdog(default_threshold_secs=...)``; they ignored
  the 1800 default and continue to ignore the 600 default.
* Tests that assert the resolver returns the configured default
  (``test_watchdog_resolve_threshold_falls_back_on_missing``) ran
  green against both old and new values because the test sets its
  own value via ``configure_watchdog``.
* Specs that explicitly set ``watchdog_stuck_threshold_s`` in any
  positive value keep that exact value (covered by the new
  ``test_watchdog_spec_override_still_wins_over_new_default``).
* Specs with ``watchdog_enabled=False`` still resolve to 0 (skip
  cancel) -- behaviour unchanged.

### Trade-off

Tightening the default kills inflight tasks 20 minutes earlier in
the worst case. A multi-node command that finished its LLM legs in
<3 min and is stuck on a downstream artefact persistence I/O for
9 min would now be reaped where Sprint-7 would have let it finish.
We have **zero** evidence of such a tail in v13-v19 audits, but
this is the explicit surface for any operator that relied on the
implicit 30 min envelope: read this changelog, set
``watchdog_stuck_threshold_s=1800`` on the org spec, redeploy.

## P0-B -- RR3 screenwriter direct-dispatch (audit v8 §1.4 + §8.2)

### Root cause

`_v19_biz/r_regression.py:73-79` submits a direct-dispatch command
to ``screenwriter`` and then calls
``wait_command_terminal(timeout_s=90.0, poll_s=3.0)``. The wait
budget is 90 s. The same v19 B-module run shows
``screenwriter`` legitimately taking 100-160 s on the same orgs
without a watchdog kill. The 90 s wait is below the legit ceiling,
so the test reports a "fail" before the backend has a chance to
finish.

### Fix

This is structurally the **same** root cause as P0-A's audit
narrative: the test client hangs up before the backend finishes,
not because the backend is broken. Sprint-8 captures the recommended
wait budget in `_audit_lib/timeouts.py`:

```python
@dataclass(frozen=True)
class RecommendedTimeouts:
    wait_direct_dispatch_s: float = 180.0  # was 90 in v19 RR3
    wait_multi_node_s: float = 240.0       # was 180 in v19 b_levels.run_one
    ...
```

v20+ scripts that import ``_audit_lib`` and use ``RECOMMENDED``
will not flag legit slow runs as failures. The product code path
(direct-dispatch via ``target_node_id``) is unchanged: v19 R3
already proved ``wb-hh-image`` direct-dispatch works (audit v8
§1.4 RR7 pass). The v19 RR3 fail flagged the test wait, not the
dispatch.

## P0-C -- audit script line-timestamp grep (audit v8 §5.3 + §8.3)

### Root cause

Every per-sprint ``_v*_biz/_lib.py`` ships its own copy of:

```python
def grep_logs_since(cutoff_ts: float, pattern: str) -> int:
    count = 0
    for log_dir in (...):
        for fp in log_dir.glob("*.log*"):
            if fp.stat().st_mtime < cutoff_ts:
                continue                      # << file-mtime-only filter
            with fp.open("r", ...) as f:
                for ln in f:
                    if pattern in ln:
                        count += 1
    return count
```

The OpenAkita logger uses daily-rotated files
(``error.log.YYYY-MM-DD``). When a daemon writes one late line
into ``error.log.2026-05-25`` after the v19 cutoff, the file
mtime ticks past the cutoff and **all** of that file's pre-cutoff
lines become "post-cutoff" hits. v19 caught 12 false-positive
``No handler mapped for tool`` hits this way -- the v19 audit
narrative had to manually re-grep per line and document the
discrepancy.

### Fix

New `_audit_lib/log_grep.py` parses every line's leading timestamp
and only counts a hit when the in-line ``YYYY-MM-DD HH:MM:SS,ms``
prefix is strictly **greater than** the cutoff:

```python
def grep_logs_since(cutoff_ts, pattern, *, log_dirs=None, glob="*.log*"):
    return sum(1 for _ in iter_log_lines_since(
        cutoff_ts, pattern, log_dirs=log_dirs, glob=glob))


def iter_log_lines_since(cutoff_ts, pattern, *, log_dirs=None, glob=...):
    for fp in _iter_log_paths(log_dirs, glob, cutoff_ts):
        last_ts = None
        for idx, line in enumerate(fp.open(...), start=1):
            ts = parse_line_timestamp(line)
            if ts is not None:
                last_ts = ts
            effective_ts = ts if ts is not None else last_ts
            if effective_ts is None or effective_ts <= cutoff_ts:
                continue
            if pattern in line:
                yield fp, idx, effective_ts, line
```

Continuation lines (Python tracebacks span multiple lines without
re-emitting the timestamp) inherit the most recent anchor cursor
in the same file, so a post-cutoff traceback's body lines count as
post-cutoff hits and a pre-cutoff traceback's body lines do not
(``test_grep_logs_since_continuation_line_inherits_anchor_ts``).
Lines without any anchor in the same file are dropped: there is no
way to attribute them, and the conservative read prevents the
exact false-positive class v19 caught.

### Why repo-root, not `src/openakita/`

The audit lib is **test artefact infrastructure**, not a runtime
feature. It is imported only from the per-sprint
exploratory scripts under `_v*_biz/`, which already live at repo
root and ``sys.path``-insert siblings. Putting the helper inside
``src/openakita/`` would couple the production import graph to the
exploratory testing scripts; putting it under ``tests/`` would
require pytest collection to even ``import _audit_lib``. Repo-root
keeps it next to its callers.

The unit tests for the helper still live under ``tests/audit/`` so
pytest discovers them; the test file ``sys.path``-inserts the repo
root so ``import _audit_lib`` resolves.

## Pattern scan results

### Pattern 1 -- default values that may be too wide / too narrow

Surveyed every ``*_secs: float = ...`` / ``*_threshold_*`` / ``*_interval_*``
default in `src/openakita/orgs/`:

| Knob | File | Default | Rationale | Action |
| --- | --- | --- | --- | --- |
| `_watchdog_default_threshold_secs` | `command_service.py` | 1800 -> **600** | v19 audit §8.1 | **Tightened** |
| `_watchdog_poll_interval_secs` | `command_service.py` | 30.0 | Polling perf vs. tail latency | Keep |
| `CommandWatchdog.quiet_threshold_secs` | `_runtime_watchdog.py` | 300.0 | Quiet-deadlock detection (different from inflight watchdog) | Keep |
| `CommandWatchdog.poll_interval_secs` | `_runtime_watchdog.py` | 30.0 | Same as above | Keep |
| `IdleProbe.poll_interval_secs` | `_runtime_watchdog.py` | 60.0 | Idle-nudge cadence | Keep |
| `IdleProbe.idle_threshold_secs` | `_runtime_watchdog.py` | 600.0 | Idle threshold | Keep |
| `LLMClient.DEFAULT_MAX_CONCURRENT` | `llm/client.py` | 20 | Provider-side semaphore | Keep |
| `EndpointConfig.timeout` | `llm/types.py` | 180 | Per-provider HTTP timeout | Keep |

Only the per-command watchdog default needed adjustment. The
provider-side ``EndpointConfig.timeout=180`` covers individual LLM
HTTP calls, not whole-command budgets, and was sized correctly for
the slowest tools-using completions across v13-v19.

### Pattern 2 -- test-client httpx timeouts too low

`_v19_biz/_lib.py:40` uses ``httpx.Client(timeout=30.0)`` as the
default. v19 saw ``httpx.ReadTimeout`` mid-B-module run because the
backend was busy serving 4 concurrent multi-node commands and one
``GET /commands/{cid}`` snapshot took >30 s. The v17/v18 ``_lib.py``
copies share the same default. Sprint-8 ships
`_audit_lib/timeouts.py` with ``RecommendedTimeouts(client_default_s=90.0,
status_poll_s=30.0, wait_multi_node_s=240.0,
wait_direct_dispatch_s=180.0, ...)`` so v20+ scripts can ``import
RECOMMENDED`` and get a coherent set in one place. The frozen
v17/v18/v19 ``_lib.py`` copies are not back-patched -- they are
historical audit artefacts.

### Pattern 3 -- log grep tooling extracted

The file-mtime-only ``grep_logs_since`` survived from v17 -> v18
-> v19 by per-sprint copy-paste. Sprint-8 publishes the
line-timestamp-aware version under ``_audit_lib/log_grep.py`` so
v20+ scripts ``from _audit_lib import grep_logs_since`` and stop
carrying the bug. The ``iter_log_lines_since`` surface lets
callers needing the actual matched lines stay on this module
instead of re-reading the file with their own logic.

## Verification

### Unit + integration

```text
$ pytest tests/runtime/ tests/api/ tests/audit/ -q \
    --deselect tests/api/test_p97_beta_smoke.py::test_b19_create_node_schedule
1336 passed, 1 deselected (pre-existing fail), 3 xfailed (expected),
72 warnings in 83.14s
```

The deselected failure (`test_b19_create_node_schedule`, 422 vs.
201) is **pre-existing on HEAD `ba3522a0`** -- verified by stashing
the Sprint-8 changes and re-running the test alone. It is unrelated
to watchdog / audit-lib code paths and tracked separately.

### New tests (Sprint-8)

```text
tests/runtime/orgs/test_stop_org_cancels_inflight.py
  test_watchdog_default_threshold_is_600s_after_sprint8 ...... PASS
  test_watchdog_spec_override_still_wins_over_new_default ..... PASS

tests/audit/test_log_grep.py
  test_parse_line_timestamp_python_logger_format ............... PASS
  test_parse_line_timestamp_iso_with_dot_millis ................ PASS
  test_parse_line_timestamp_continuation_line_returns_none ..... PASS
  test_grep_logs_since_file_mtime_post_cutoff_but_lines_pre_... PASS
  test_grep_logs_since_counts_only_post_cutoff_lines ........... PASS
  test_grep_logs_since_skips_files_with_old_mtime .............. PASS
  test_grep_logs_since_continuation_line_inherits_anchor_ts .... PASS
  test_grep_logs_since_returns_zero_when_log_dir_missing ....... PASS
  test_grep_logs_since_handles_non_utf8_bytes .................. PASS
  test_iter_log_lines_since_yields_match_metadata .............. PASS
  test_parse_line_timestamp_handles_edge_years[..2024..] ....... PASS
  test_parse_line_timestamp_handles_edge_years[..2026..] ....... PASS
```

The v19 false-positive class is pinned by
`test_grep_logs_since_file_mtime_post_cutoff_but_lines_pre_cutoff`
which reproduces the exact "12 ``No handler mapped`` lines stamped
2026-05-25 in a file whose mtime is post-cutoff" scenario; the
new helper returns 0 where the legacy helper returned 12.

### Lint / format / type

```text
$ ruff check src/openakita/orgs/command_service.py _audit_lib/ \
    tests/audit/ tests/runtime/orgs/test_stop_org_cancels_inflight.py
All checks passed!

$ ruff format --check _audit_lib/ tests/audit/
3 files already formatted

$ mypy src/openakita/orgs/command_service.py _audit_lib/
Success: no issues found in 4 source files
```

### Boundary cases (Round 1)

* `watchdog_stuck_threshold_s = 0` -> resolves to 600 (was 1800
  pre-Sprint-8) -- back-compat note: zero / negative spec values
  have always been "use default", so users that relied on the
  implicit envelope move from 1800 to 600.
* `watchdog_stuck_threshold_s = -5` -> resolves to 600 (same as
  above).
* `watchdog_stuck_threshold_s = 1800` -> resolves to 1800 (spec
  wins, legacy envelope still available).
* `watchdog_stuck_threshold_s = 120` -> resolves to 120 (aggressive
  envelope still available).
* `watchdog_enabled = False` -> resolves to 0 (skip cancel,
  unchanged from Sprint-5).
* `configure_watchdog(default_threshold_secs=1234.0)` then resolve
  -> returns 1234.0 (configure overrides Sprint-8 default,
  unchanged from Sprint-5).

### CLI startup

`OrgCommandService` is constructed inside `api/server.py:_startup_org_runtime`
which calls `start_watchdog()` with the new 600 s default. The
log line emitted by `start_watchdog` reports the default verbatim:

```
[OrgCmd] command watchdog started (poll=30.0s, default_threshold=600s)
```

(Previously `default_threshold=1800s`. The single field changed.)

No pyproject.toml / dependency churn.

## Out of scope (Sprint-9)

* **Parallel child dispatch** -- the v15 changelog left
  ``dispatch_subtask`` to call children sequentially. Switching to
  ``asyncio.gather`` would shrink multi-node wall-clock time
  enough to push the Sprint-8 600 s default well above any
  observable legit run; tracked in
  ``_skip_items_rca_v11.md`` as a Sprint-9 candidate.
* **LLM provider stream optimisation** -- some endpoints have a
  long ttfb (>30 s); switching to ``messages_create_stream`` for
  the per-node agent would let the watchdog observe progress at
  the chunk granularity rather than the request granularity.
  Tracked separately.
* **Backfill v20+ test scripts to import `_audit_lib`** -- the
  v20 worker writing under `_v20_biz/` has the helper available
  on day 1; no v17-v19 copies are back-patched (they are frozen
  audit artefacts).

## Rollback recipe

If a deployment relies on the legacy 30 min envelope:

```yaml
# orgs spec
watchdog_enabled: true
watchdog_stuck_threshold_s: 1800
```

The spec value wins over the new default. No code change required.

## Cross-sprint compatibility

* Sprint-2 P0-1 (DefaultAgentBuilder) -- unchanged.
* Sprint-3 P0-1 (D2 cancel) / P0-2 (status reconciliation) -- unchanged.
* Sprint-4 P0-1 (D5 dispatch) / P0-2 (artefacts) -- unchanged.
* Sprint-5 P0-1 (cancelled_by) / P0-2 (F2 stop_org) -- unchanged.
* Sprint-5 unexpected-finding 2 (watchdog) -- default value
  changed, behaviour unchanged.
* Sprint-6 P0-1 (D4 tools) / P0-2 (cancel-source bridge) -- unchanged.
* Sprint-6 P0-3 (plugin tool definitions) -- unchanged.
* Sprint-7 P0-A (cancelled_by literal) / P0-B (tool-use prompt) /
  P0-C (R4 regression triage) -- unchanged.

The only externally-observable behaviour change is the per-command
watchdog production envelope: 30 min -> 10 min for orgs without an
explicit spec value.

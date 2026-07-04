# P-RC-9 orgs/ parity harness

This package proves that the v2 ``src/openakita/runtime/orgs/``
subsystems behave identically to the v1 ``src/openakita/orgs/``
package for the same inputs, before the v1 package is deleted
in P9.9.

## Per-subsystem contract

For each subsystem in ``docs/revamp/P-RC-9-PLAN.md`` section 4:

| subsystem | landing phase | this file | fixture count |
|---|---|---|---:|
| OrgBlackboard | P9.1 | ``test_blackboard_parity.py`` | 8 |
| ProjectStore | P9.2 | ``test_project_store_parity.py`` | 6 |
| NodeScheduler | P9.3 | ``test_node_scheduler_parity.py`` | 4 |
| OrgCommandService | P9.4 | ``test_command_service_parity.py`` | 10 |
| OrgManager | P9.5 | ``test_manager_parity.py`` | 12 |
| OrgRuntime | P9.6 | ``test_runtime_parity.py`` | 20 |

Each test file follows the ``tests/parity/harness.py`` +
``tests/parity/runners.py`` pattern from P-RC-0..P-RC-7:

1. A ``ParityCase`` is a tiny declarative recipe (id + kind +
   inputs + ignore set).
2. A runner pair (``_<subsystem>_v1``, ``_<subsystem>_v2``)
   takes a ``ParityCase`` and emits a ``ParityResult``.
3. ``assert_parity(v1, v2, case=case)`` compares the two
   results modulo the ignore set, and additionally asserts
   ``sys.modules[v1_module].__file__ != sys.modules[v2_module].__file__``
   so the comparison can never be a tautology.

## P9.0i state -- xfail placeholders only

Every test file in this package is currently a single
``@pytest.mark.xfail`` placeholder with the reason "not yet
implemented; P9.<N> will activate". Gate criterion:

* P9.0 baseline: xfail count rises by **6** (one per subsystem).
  Pytest exits 0; xfail tests do not count against pass count.
* P9.1+: each subsystem landing flips its placeholder to
  N real fixtures (N from the table above) and the xfail count
  for that file drops to 0 (or stays low if the fixtures were
  recorded but not yet asserted).
* P9.6 close: total xfail count under ``tests/parity/orgs/``
  is **0** (every subsystem activated).

The plan section 5 wall-clock budget tests (closes ADR-0013)
live in ``tests/runtime/`` not here, because they pin the v2
contract directly rather than asserting v1==v2.

## How to add a fixture (template for P9.1..P9.6)

```python
from tests.parity.harness import ParityCase, ParityResult, assert_parity

def _blackboard_v1(case: ParityCase) -> ParityResult:
    from openakita.orgs.blackboard import OrgBlackboard
    # ... call v1 path ...
    return ParityResult(...)

def _blackboard_v2(case: ParityCase) -> ParityResult:
    from openakita.orgs.blackboard import OrgBlackboard
    # ... call v2 path ...
    return ParityResult(...)

CASES = [
    ParityCase(id="bb_read_after_write", kind="blackboard",
               inputs={"scope": "org", "key": "x", "value": "y"}),
    # ... 7 more ...
]

@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_blackboard_parity(case):
    v1 = _blackboard_v1(case)
    v2 = _blackboard_v2(case)
    assert_parity(v1, v2, case=case)
```

The P9.0i skeleton ships the empty per-subsystem files with one
xfail placeholder each so the gate can verify the structure is
in place. Activation happens in the subsystem's own phase.

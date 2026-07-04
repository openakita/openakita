"""Parity harness for the P-RC-9 orgs/ -> runtime/orgs/ migration.

Each subsystem (OrgBlackboard / ProjectStore / NodeScheduler /
OrgCommandService / OrgManager / OrgRuntime) has its own
``test_<subsystem>_parity.py`` module that activates as the
subsystem lands in P9.1..P9.6. Until then, every test file in
this package contains placeholder xfail cases so the gate
criterion "xfail count rises, never falls" is enforceable.

See ``docs/revamp/P-RC-9-PLAN.md`` section 5 for the design and
``tests/parity/orgs/README.md`` for the per-subsystem contract.
"""

"""Per-cluster contract tests for the v2 orgs runtime router (P-RC-9 P9.7gamma-1).

This package mirrors the cluster split of
``src/openakita/api/routes/orgs_v2_runtime_*.py`` (B1-B83 from
``docs/revamp/P-RC-9-P9.7-ENDPOINT-INVENTORY.md``) -- each test
file pairs with one route sibling. Cluster files target ~2
contract cases per endpoint per charter section 6 (~166 cases
total) covering the happy / 404 / 422 / 409 / 503 status
matrix where applicable.

Shared fixtures (``mint_app`` / ``mint_client``) live in the
sibling ``conftest.py`` so the per-cluster files stay focused
on assertions per the charter section 3 P9.7gamma-1 brief.
"""

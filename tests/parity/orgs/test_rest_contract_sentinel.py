"""REST contract sentinel for the P9.7 v2 orgs surface (P-RC-9 P9.7gamma-2).

Pairs with the 6 P-RC-9 parity sentinels (one per ADR-0011 subsystem)
and joins them as the 7th active P-RC-9 sentinel: a REST contract
invariant the gate enforces with **active** (non-xfail) assertions.

Three invariants:

1. **Route count parity** -- the v2 OpenAPI surface lands exactly
   88 method-routes under ``/api/v2/orgs/*`` (85 B-marked endpoints +
   1 ``GET /_p97/health`` wiring stub + the SSE alias + the operational
   start-readiness endpoint) plus 9 routes under
   ``/api/v2/orgs-spec/*`` (Group A relocated by P9.7a-2a per
   D-1 R3 LOCKED). On top of those 93 in-schema entries, the
   ``_orgs_v2_deprecated_redirects`` router contributes 9 308 shims
   that live in ``app.routes`` but are excluded from the OpenAPI
   schema (``include_in_schema=False``).
2. **Coverage matrix** -- every minted B-marker (B1-B84) has at
   least one test function named ``test_b<N>_*`` in either the
   contract suite (``tests/api/contracts/``) or the beta smoke
   suite (``tests/api/test_p97_beta_smoke.py``). Future regressions
   that land a v2 endpoint without a contract test must update
   the inventory _and_ this sentinel.
3. **OpenAPI snapshot** -- the canonical pruned schema (paths +
   methods only) matches the frozen
   ``tests/parity/orgs/_openapi_snapshot.json``. Charter section
   7 alternative chosen (snapshot diff vs schemathesis fuzz) per
   the gamma-1 brief: simpler, no new dependency.

The sentinel does NOT activate via ``@pytest.mark.xfail`` -- in
the P9.x convention "sentinel" means **active assertion**; xfail
markers are removed when the invariant is met (which is now).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from openakita.api.routes import (
    _orgs_v2_deprecated_redirects,
    orgs_v2,
    orgs_v2_runtime,
    orgs_v2_stream,
)

# Charter inventory anchors -- count any drift here as a sentinel break
# even if the OpenAPI schema disagrees.
_MINT_ENDPOINTS = 85  # B1-B85 (B84 = PATCH partial-update [smoke-F5]; B85 = mint-runtime SSE stream [smoke-5-sse])
# Non-B-marker mint routes that still land in the OpenAPI schema. The
# Sprint-9 SSE alias ``GET /api/v2/orgs/{id}/events/stream`` (mounted by
# ``orgs_v2_runtime_dispatch.py`` per commit ``04b00c4f`` to stop the
# ``/events/stream`` 404s the v17-v20 probes hit) re-mounts the B85 stream
# body under a second URL. It carries no new B-marker (it is an alias, not a
# new capability), so it is counted here rather than inflating
# ``_MINT_ENDPOINTS`` (which the coverage-matrix test maps 1:1 to
# ``test_b<N>_`` functions).
_MINT_ALIASES = 2  # SSE stream alias + start-readiness operational endpoint
_HEALTH_STUBS = 1  # GET /_p97/health
_SPEC_ENDPOINTS = 9  # Group A relocated (8 CRUD + 1 SSE)
_SHIM_ROUTES = 9  # 308 legacy redirects
_SNAPSHOT_PATH = Path(__file__).parent / "_openapi_snapshot.json"
_CONTRACT_GLOB = "test_orgs_v2_contracts_*.py"


def _build_app() -> FastAPI:
    """Mirror the production mounting order from ``server.py``."""
    app = FastAPI()
    # spec router first -- prefix /api/v2/orgs-spec
    app.include_router(orgs_v2.router)
    app.include_router(orgs_v2_stream.router)
    # mint router second -- prefix /api/v2/orgs
    app.include_router(orgs_v2_runtime.router)
    # redirect shim last so collisions resolve mint-first
    app.include_router(_orgs_v2_deprecated_redirects.router)
    return app


def _iter_api_routes(app: FastAPI) -> Iterator[Any]:
    """Yield effective API routes across eager and lazy FastAPI routers."""
    for route in app.routes:
        effective_route_contexts = getattr(route, "effective_route_contexts", None)
        candidates = effective_route_contexts() if callable(effective_route_contexts) else (route,)
        for candidate in candidates:
            original_route = getattr(candidate, "original_route", candidate)
            if isinstance(original_route, APIRoute):
                yield candidate


def _route_counts(app: FastAPI) -> Counter[str]:
    counts: Counter[str] = Counter()
    for r in _iter_api_routes(app):
        # Methods on a route is a set; count each (path, method).
        n = len(r.methods or {"GET"})
        if r.path.startswith("/api/v2/orgs-spec"):
            counts["spec"] += n
        elif r.path.startswith("/api/v2/orgs"):
            if r.include_in_schema:
                counts["mint"] += n
            else:
                counts["shim"] += n
    return counts


def _canonical_paths(app: FastAPI) -> dict[str, list[str]]:
    oa = app.openapi()
    canon: dict[str, list[str]] = {}
    for path, ops in oa["paths"].items():
        if not (path.startswith("/api/v2/orgs") or path.startswith("/api/v2/orgs-spec")):
            continue
        methods = sorted(
            m.upper() for m in ops if m.lower() in ("get", "post", "put", "delete", "patch")
        )
        if methods:
            canon[path] = methods
    return dict(sorted(canon.items()))


# ---------------------------------------------------------------------------
# Test 1 -- route count parity.
# ---------------------------------------------------------------------------


def test_route_counts_match_inventory() -> None:
    """The v2 surface holds exactly the inventory counts -- no drift."""
    counts = _route_counts(_build_app())
    expected_mint = _MINT_ENDPOINTS + _HEALTH_STUBS + _MINT_ALIASES
    assert counts["mint"] == expected_mint, (
        f"Expected {expected_mint} mint method-routes "
        f"({_MINT_ENDPOINTS} mint + {_HEALTH_STUBS} health + "
        f"{_MINT_ALIASES} auxiliary routes), "
        f"got {counts['mint']}; spec={counts['spec']}, shim={counts['shim']}"
    )
    assert counts["spec"] == _SPEC_ENDPOINTS, (
        f"Expected {_SPEC_ENDPOINTS} spec method-routes (Group A relocated), got {counts['spec']}"
    )
    assert counts["shim"] == _SHIM_ROUTES, (
        f"Expected {_SHIM_ROUTES} 308 redirect shims under /api/v2/orgs, got {counts['shim']}"
    )


# ---------------------------------------------------------------------------
# Test 2 -- coverage matrix (every B-marker has at least one test).
# ---------------------------------------------------------------------------


def _scan_b_markers() -> set[int]:
    """Collect ``test_b<N>_`` markers from the contract + smoke suites."""
    repo = Path(__file__).resolve().parents[3]
    targets = list((repo / "tests" / "api" / "contracts").glob(_CONTRACT_GLOB))
    targets.append(repo / "tests" / "api" / "test_p97_beta_smoke.py")
    pattern = re.compile(r"def\s+test_b(\d+)_", re.MULTILINE)
    found: set[int] = set()
    for path in targets:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            found.add(int(match.group(1)))
    return found


def test_every_minted_endpoint_has_a_contract_test() -> None:
    """Each B1-B84 has >= 1 test function across contracts/ + beta_smoke."""
    found = _scan_b_markers()
    expected = set(range(1, _MINT_ENDPOINTS + 1))
    missing = expected - found
    assert not missing, (
        f"Endpoints without any test: {sorted(missing)}. "
        "Update tests/api/contracts/test_orgs_v2_contracts_*.py "
        "(or tests/api/test_p97_beta_smoke.py) to cover them."
    )


# ---------------------------------------------------------------------------
# Test 3 -- OpenAPI snapshot diff.
# ---------------------------------------------------------------------------


def test_openapi_snapshot_matches() -> None:
    """Canonical pruned schema (paths + methods) must match the snapshot.

    On a deliberate surface change (new endpoint / new method on an
    existing path) the operator regenerates the snapshot via the
    ``WRITE_SNAPSHOT=1`` env var:

        WRITE_SNAPSHOT=1 pytest tests/parity/orgs/test_rest_contract_sentinel.py

    Otherwise the test asserts byte-for-byte parity against the
    frozen snapshot file landed in P9.7gamma-2.
    """
    import os

    canon = _canonical_paths(_build_app())
    if os.environ.get("WRITE_SNAPSHOT") == "1":
        _SNAPSHOT_PATH.write_text(
            json.dumps(canon, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return
    assert _SNAPSHOT_PATH.is_file(), (
        f"OpenAPI snapshot missing at {_SNAPSHOT_PATH}; regenerate with WRITE_SNAPSHOT=1."
    )
    expected = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert canon == expected, (
        f"OpenAPI surface drift detected.\n"
        f"Missing in current: {sorted(set(expected) - set(canon))}\n"
        f"Extra in current: {sorted(set(canon) - set(expected))}\n"
        "Regenerate the snapshot with WRITE_SNAPSHOT=1 if intentional."
    )

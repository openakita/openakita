"""Recommended httpx / wait timeouts for v20+ exploratory test scripts.

Sprint-8 Pattern 2 (v19 audit ``_orgs_business_capability_audit_v8.md``
§2 + §1.4): the v17/v18/v19 ``_v*_biz/_lib.py`` copies hard-coded
``timeout=30.0`` for the shared ``httpx.Client`` factory. v19 saw two
separate failure modes that traced back to those tight defaults:

* B-module L4.5 raised ``httpx.ReadTimeout`` mid-run when the backend
  was busy serving 4 concurrent multi-node commands and a per-status
  poll exceeded 30 s. The 19/25 partial run was mis-attributed to a
  product fault when in fact the test client gave up first.
* RR3 ``screenwriter`` direct-dispatch wait used a 90 s
  :func:`wait_command_terminal` budget against a node that
  legitimately takes 100-160 s in the rest of v19. The case was
  scored as a failure when the underlying issue was the test wait.

These constants are intentionally **conservative for tests, not for
production**: they trade a few extra seconds on the slow path for
zero false positives in the audit narrative.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RECOMMENDED", "RecommendedTimeouts"]


@dataclass(frozen=True)
class RecommendedTimeouts:
    """Per-call HTTP timeout knobs for v20+ exploratory test scripts.

    All values are seconds. Names mirror the legacy ``_lib.py`` API
    surface so a port is one find-and-replace.
    """

    # Shared httpx.Client factory default (was 30.0 in v17/v18/v19).
    # Bumped to cover backend-busy moments where /commands/{cid} can
    # itself block a few seconds before returning the snapshot.
    client_default_s: float = 90.0

    # ``submit_command`` POST. Most submits complete in <2 s but a
    # slow lifecycle-lock contention has been seen at ~10 s in v18;
    # 30 s gives headroom without masking real bugs.
    submit_s: float = 30.0

    # Single ``GET /commands/{cid}`` poll inside ``wait_command_terminal``.
    status_poll_s: float = 30.0

    # End-to-end wait for a single-node command (L1-L2 prompts).
    wait_single_node_s: float = 120.0

    # End-to-end wait for a multi-node command (L3-L4 prompts where
    # producer dispatches to one or more children). v19 saw legit
    # done-runs at 162-174 s; 240 s gives a 1.4x safety margin.
    wait_multi_node_s: float = 240.0

    # Direct-dispatch via ``target_node_id``. RR3 used 90 s in v19
    # and reported a false-positive failure. 180 s tracks the
    # observed ceiling on legit ``screenwriter`` runs.
    wait_direct_dispatch_s: float = 180.0

    # SSE / long-poll style endpoints; the test rarely needs more
    # than 60 s of stream headroom because the orgs_v2 SSE fan-out
    # surfaces phase changes within seconds when one is happening.
    sse_s: float = 60.0


RECOMMENDED = RecommendedTimeouts()

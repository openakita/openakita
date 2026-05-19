"""LOC invariant audit for the OpenAkita revamp.

Why this script exists
----------------------
The post-RC continuation plan
(`openakita_revamp_continuation_plan_d6192647.plan.md` §0.1)
makes a single hard rule the entire phase enforcement hangs on:
the legacy giants under `src/openakita/core/` and
`src/openakita/orgs/` may **never** grow line-wise, and the v2
facades / real implementations under `src/openakita/agent/`
must shrink monotonically once they leave facade-land in P-RC-4
through P-RC-6.

This module is the executable form of that rule. It is invoked
two ways:

* directly, `python scripts/revamp_loc_audit.py` — prints a
  table and exits 0 if every file is within budget, 1 otherwise;
* from a pytest in :mod:	ests.revamp.test_loc_invariants so
  the same rule lives in the regular test run and protects every
  commit on `revamp/v2` automatically.

The baseline numbers live in
`docs/revamp/LOC_BASELINE.json` and are seeded by running this
script with `--init` (only safe at phase boundaries; usually a
human-reviewed step).

Phase-aware rules
-----------------
* For each file under `core/` and `orgs/` listed in the
  baseline, current LOC must be `<= baseline` (no growth ever).
* For each file under `agent/`, current LOC must be
  `<= baseline + AGENT_GROWTH_BUDGET` for the active phase. In
  P-RC-0 the agent files are still thin facades; the cap is set
  generously so the upcoming real rewrites can land in
  `agent/brain.py` etc. without the audit wrongly screaming.
  Each post-RC phase will lower the cap by editing the baseline
  in the same commit that lands the rewrite.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# Project root resolved from this script's location so the audit
# is callable from any working directory (CI runner, dev shell,
# pytest worker, etc.).
ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "docs" / "revamp" / "LOC_BASELINE.json"

# Files we track. Order matters only for the printed table; the
# rules below are keyed by path prefix.
TRACKED_FILES: list[str] = [
    "src/openakita/core/agent.py",
    "src/openakita/core/reasoning_engine.py",
    "src/openakita/core/brain.py",
    "src/openakita/core/tool_executor.py",
    "src/openakita/core/context_manager.py",
    # Legacy renames preserved during the phased rewrite -- listed here
    # for visibility only (N11 P-RC-6 audit fix). The audit prints their
    # current LOC in the table but never enforces a cap on them; they
    # are deleted wholesale in P-RC-7 / P-RC-8.
    "src/openakita/core/_brain_legacy.py",
    "src/openakita/core/_tool_executor_legacy.py",
    "src/openakita/core/_context_manager_legacy.py",
    "src/openakita/core/_reasoning_engine_legacy.py",
    "src/openakita/core/_agent_legacy.py",
    "src/openakita/orgs/runtime.py",
    "src/openakita/orgs/tool_handler.py",
    "src/openakita/orgs/templates.py",
    "src/openakita/orgs/messenger.py",
    "src/openakita/agent/core.py",
    "src/openakita/agent/reasoning.py",
    "src/openakita/agent/brain.py",
    "src/openakita/agent/tools.py",
    "src/openakita/agent/context.py",
]

# In P-RC-0 we are not yet doing the real Phase-2 rewrite, so
# allow up to +50 lines on the agent facade files for any
# incidental growth (e.g. adding sentinel comments). Subsequent
# phases will rebase the baseline downward in the same commit
# that lands the corresponding real rewrite.
AGENT_GROWTH_BUDGET = 50

# Files that are tracked for visibility only -- the audit prints their
# current LOC in the rendered table but never compares against the
# baseline (an informational "infinite cap" so they never fail the gate).
# Per N11 (G-RC-5 P-RC-5 audit fix) the four legacy renames preserved
# during P-RC-4 and P-RC-5 are surfaced here so an external reader can
# see how much of the legacy giants is still in-tree at a glance.
INFO_ONLY_FILES: set[str] = {
    "src/openakita/core/_brain_legacy.py",
    "src/openakita/core/_tool_executor_legacy.py",
    "src/openakita/core/_context_manager_legacy.py",
    "src/openakita/core/_reasoning_engine_legacy.py",
    "src/openakita/core/_agent_legacy.py",
}

# Sentinel for the informational-only budget: large enough that any
# realistic LOC count stays well below it, but kept finite so the
# rendered table stays human-readable.
_INFO_ONLY_BUDGET = 10_000_000


@dataclass(frozen=True)
class FileBudget:
    path: str
    current: int
    baseline: int
    budget: int  # max allowed = baseline + budget

    @property
    def cap(self) -> int:
        return self.baseline + self.budget

    @property
    def ok(self) -> bool:
        return self.current <= self.cap

    @property
    def slack(self) -> int:
        return self.cap - self.current


def _is_agent(path: str) -> bool:
    return path.startswith("src/openakita/agent/")


def _budget_for(path: str) -> int:
    if path in INFO_ONLY_FILES:
        return _INFO_ONLY_BUDGET
    return AGENT_GROWTH_BUDGET if _is_agent(path) else 0


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def measure(root: Path = ROOT) -> dict[str, int]:
    """Return `{relative_path: current_loc}` for every tracked file."""
    return {p: _count_lines(root / p) for p in TRACKED_FILES}


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, int]:
    if not path.exists():
        raise FileNotFoundError(
            f"LOC baseline not found at {path}; run with --init to seed it."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    files = raw.get("files")
    if not isinstance(files, dict):
        raise ValueError(f"Malformed baseline at {path}: missing 'files' object.")
    return {str(k): int(v) for k, v in files.items()}


def write_baseline(measurements: dict[str, int], path: Path = BASELINE_PATH) -> None:
    payload = {
        "_comment": (
            "Per-file LOC baseline used by scripts/revamp_loc_audit.py. "
            "Update only at phase boundaries (P-RC-X commits) and only "
            "to lower the values for agent/* files as their real "
            "rewrites land. core/* and orgs/* baselines must never "
            "grow — they are the giants we are shrinking."
        ),
        "agent_growth_budget": AGENT_GROWTH_BUDGET,
        "files": measurements,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def evaluate(measurements: dict[str, int], baseline: dict[str, int]) -> list[FileBudget]:
    out: list[FileBudget] = []
    for p in TRACKED_FILES:
        out.append(
            FileBudget(
                path=p,
                current=int(measurements.get(p, 0)),
                baseline=int(baseline.get(p, 0)),
                budget=_budget_for(p),
            )
        )
    return out


def render_table(rows: list[FileBudget]) -> str:
    width = max(len(r.path) for r in rows) + 2
    header = f"{'file'.ljust(width)} {'current':>8} {'baseline':>9} {'cap':>6} {'slack':>6}  status"
    lines = [header, "-" * len(header)]
    for r in rows:
        status = "ok" if r.ok else "OVER"
        lines.append(
            f"{r.path.ljust(width)} {r.current:>8} {r.baseline:>9} {r.cap:>6} {r.slack:>6}  {status}"
        )
    return "\n".join(lines)


def audit(verbose: bool = False) -> int:
    """Return 0 when all files are within budget, 1 otherwise."""
    measurements = measure()
    baseline = load_baseline()
    rows = evaluate(measurements, baseline)
    over = [r for r in rows if not r.ok]
    if verbose or over:
        print(render_table(rows))
    if over:
        print("\nLOC audit FAILED for:")
        for r in over:
            print(f"  - {r.path}: {r.current} > cap {r.cap} (baseline {r.baseline} + budget {r.budget})")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LOC invariant audit for the OpenAkita revamp.")
    parser.add_argument(
        "--init",
        action="store_true",
        help="Seed/refresh docs/revamp/LOC_BASELINE.json from the current tree (phase-boundary use only).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Always print the table, even when every file is within budget.",
    )
    args = parser.parse_args(argv)

    if args.init:
        measurements = measure()
        write_baseline(measurements)
        print(f"wrote baseline: {BASELINE_PATH.relative_to(ROOT)}")
        for p, n in measurements.items():
            print(f"  {p}: {n}")
        return 0

    return audit(verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())

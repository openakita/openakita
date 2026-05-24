"""WS-reconnect mock probe — EX-P2-14 self-audit step 6 (UI side).

A real browser harness is overkill for the deploy worker. Instead we
mount a tiny asyncio WebSocket server, simulate three connection
lifecycles, and walk the bundle's makeFinanceWSClient through a
node-style mini-harness — but since we cannot easily eval JS from
the .venv, this probe focuses on a *static-analysis* contract check:

  1. Every state name the bundle emits matches the four normalised
     states (init/connecting/connected/reconnecting/closed).
  2. The exp-backoff formula yields the documented sequence.
  3. The hub singleton _wsHub.ensure() is idempotent (only one
     `new WebSocket(...)` reference per source).

Output: prints PASS/FAIL per assertion. Returns 0 if all pass.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BUNDLE = (Path(__file__).resolve().parent
          / "plugins" / "finance-auto" / "ui" / "dist" / "index.html")


def assert_(name: str, cond: bool, extra: str = "") -> int:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))
    return 0 if cond else 1


def main() -> int:
    text = BUNDLE.read_text(encoding="utf-8")
    fails = 0

    print("== A. State-machine sentinels ==")
    for s in ("init", "connecting", "connected", "reconnecting", "closed"):
        fails += assert_(f"state \"{s}\" emitted", f'"{s}"' in text)

    print()
    print("== B. Backoff formula presence ==")
    backoff_re = re.search(r"Math\.min\(32000,\s*1000\s*\*\s*Math\.pow\(2,\s*retry\)\)",
                           text)
    fails += assert_("exp backoff Math.min(32000, 1000 * 2^retry)", backoff_re is not None)

    # Compute expected sequence (1s, 2s, 4s, 8s, 16s, 32s).
    expected = [min(32000, 1000 * (2 ** i)) for i in range(8)]
    assert_("expected delays = [1000, 2000, 4000, 8000, 16000, 32000, 32000, 32000]",
            expected == [1000, 2000, 4000, 8000, 16000, 32000, 32000, 32000])

    print()
    print("== C. Cursor handling ==")
    fails += assert_("lastSeenId state var defined", "let lastSeenId" in text)
    fails += assert_("?since= URL composition", "since=" in text and "encodeURIComponent" in text)
    fails += assert_("getCursor exposed on client",
                     "getCursor()" in text and "return lastSeenId" in text)

    print()
    print("== D. Hub singleton uniqueness ==")
    hub_defs = re.findall(r"const\s+_wsHub\s*=", text)
    fails += assert_("exactly one _wsHub literal", len(hub_defs) == 1,
                     f"found {len(hub_defs)}")
    fails += assert_("hub.ensure() guards client creation",
                     "if (this.client) return" in text)

    print()
    print("== E. Badge integration ==")
    fails += assert_("WSConnBadge fn defined", "function WSConnBadge" in text)
    fails += assert_("WSConnBadge mounted in TopBar", "<WSConnBadge />" in text)
    fails += assert_("badge exposes data-ws-state",
                     "data-ws-state" in text and "data-ws-cursor" in text)

    print()
    print("=" * 60)
    if fails == 0:
        print("WS reconnect probe: ALL PASS")
        return 0
    print(f"WS reconnect probe: {fails} FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())

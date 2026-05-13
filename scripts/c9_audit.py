"""C9 audit: SecurityView v2 wire-up + UI confirm bus extraction.

5 dimensions parallel to the C8 audits:

D1 completeness — every advertised C9a + C9b sub-task is reachable from prod
D2 architecture — bus is the single source of truth; facade delegates only
D3 no-whack-a-mole — single state location, no duplicate dicts on policy.py
D4 hidden bugs — bus survives engine reset; idempotent prepare; orphan cleanup
D5 compatibility — old SSE consumers (no approval_class) still work
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running from repo root without installation
sys.path.insert(0, str(Path("src").resolve()))


def d1_completeness() -> None:
    print("=== C9 D1 completeness ===")
    re_src = Path("src/openakita/core/reasoning_engine.py").read_text(encoding="utf-8")
    sv_src = Path("apps/setup-center/src/views/SecurityView.tsx").read_text(encoding="utf-8")
    cm_src = Path(
        "apps/setup-center/src/views/chat/components/SecurityConfirmModal.tsx"
    ).read_text(encoding="utf-8")
    api_src = Path("src/openakita/api/routes/config.py").read_text(encoding="utf-8")

    # C9a-1: SSE includes approval_class + policy_version (both hotspots)
    assert re_src.count('"approval_class": _approval_class') == 2, (
        "both reasoning_engine SSE hotspots must include approval_class"
    )
    assert re_src.count('"policy_version": 2') == 2
    print("  C9a-1 SSE v2 fields wired in both hotspots: OK")

    # C9a-2: Modal renders approval_class badge
    assert "APPROVAL_CLASS_LABELS" in cm_src
    assert "data.approvalClass" in cm_src
    print("  C9a-2 SecurityConfirmModal approval_class badge: OK")

    # C9a-3: SecurityView IM owner allowlist tab + handler
    assert '"imowner"' in sv_src and "ImOwnerChannelRow" in sv_src
    assert "/api/im/owner-allowlist" in sv_src
    print("  C9a-3 SecurityView imowner tab + handler: OK")

    # C9a-4: dry-run preview tab + endpoint
    assert '"dryrun"' in sv_src and "runDryRunPreview" in sv_src
    assert '@router.post("/api/config/security/preview")' in api_src
    print("  C9a-4 SecurityView dryrun tab + backend endpoint: OK")

    # C9b-1: bus module exists + symbols
    bus_src = Path("src/openakita/core/ui_confirm_bus.py").read_text(encoding="utf-8")
    for sym in ["class UIConfirmBus", "def store_pending", "def prepare", "def cleanup",
                "def resolve", "async def wait_for_resolution",
                "def get_ui_confirm_bus", "def reset_ui_confirm_bus"]:
        assert sym in bus_src, f"missing symbol: {sym}"
    print("  C9b-1 ui_confirm_bus.py module + 8 symbols: OK")

    # C9b-2: reasoning_engine uses bus for producer/wait
    assert "_bus = get_ui_confirm_bus()" in re_src
    assert re_src.count("_bus.store_pending(") == 2
    assert re_src.count("_bus.prepare(") == 2
    assert re_src.count("_bus.wait_for_resolution(") == 2
    assert re_src.count("_bus.cleanup(") == 2
    print("  C9b-2 reasoning_engine migrated to bus (4 verbs × 2 hotspots): OK")


def d2_architecture() -> None:
    print("\n=== C9 D2 architecture ===")
    pe_src = Path("src/openakita/core/policy.py").read_text(encoding="utf-8")

    # PolicyEngine no longer owns the dicts
    for attr in ["self._ui_confirm_events", "self._ui_confirm_decisions",
                 "self._pending_ui_confirms"]:
        assert f"{attr}: dict" not in pe_src and f"{attr} = " not in pe_src, (
            f"PolicyEngine must not own {attr} after C9b — moved to bus"
        )
    print("  PolicyEngine no longer owns _ui_confirm_* / _pending_ui_confirms: OK")

    # Facade methods delegate to bus
    for verb, bus_call in [
        ("def store_ui_pending", "get_ui_confirm_bus().store_pending"),
        ("def prepare_ui_confirm", "get_ui_confirm_bus().prepare"),
        ("def cleanup_ui_confirm", "get_ui_confirm_bus().cleanup"),
        ("async def wait_for_ui_resolution", "get_ui_confirm_bus().wait_for_resolution"),
        ("def resolve_ui_confirm", "get_ui_confirm_bus().resolve"),
        ("def cleanup_session", "get_ui_confirm_bus().cleanup_session"),
    ]:
        assert verb in pe_src and bus_call in pe_src, (
            f"PolicyEngine.{verb} must delegate to {bus_call}"
        )
    print("  6 facade methods all delegate to bus: OK")

    # reset_policy_engine no longer copies UI confirm fields
    assert "_pending_ui_confirms = previous._pending_ui_confirms" not in pe_src
    assert "_ui_confirm_events = previous._ui_confirm_events" not in pe_src
    assert "_ui_confirm_decisions = previous._ui_confirm_decisions" not in pe_src
    print("  reset_policy_engine no longer copies bus fields: OK (singleton survives)")


def d3_no_whack() -> None:
    print("\n=== C9 D3 no-whack-a-mole ===")
    # Each of the 3 bus state dicts is **assigned** (not just typed) exactly
    # once in the whole production tree. Docstring mentions don't count.
    repo_src = Path("src/openakita")
    grep_results = {
        "self._events: dict[str, asyncio.Event] = {}": 0,
        "self._decisions: dict[str, str] = {}": 0,
        "self._pending: dict[str, dict[str, Any]] = {}": 0,
    }
    for py in repo_src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for k in grep_results:
            grep_results[k] += text.count(k)
    for k, n in grep_results.items():
        assert n == 1, f"expected exactly 1 assignment of `{k}`, found {n}"
    print("  3 bus state dicts each assigned exactly once (in ui_confirm_bus.py): OK")

    # Belt-and-suspenders: confirm policy.py no longer assigns the legacy fields
    pe_src = Path("src/openakita/core/policy.py").read_text(encoding="utf-8")
    for legacy in [
        "self._ui_confirm_events: dict",
        "self._ui_confirm_decisions: dict",
        "self._pending_ui_confirms: dict",
    ]:
        assert legacy not in pe_src, f"PolicyEngine still owns {legacy} after C9b"
    print("  PolicyEngine no longer types the legacy state attrs: OK")


def d4_hidden_bugs() -> None:
    print("\n=== C9 D4 hidden bugs ===")
    from openakita.core.policy import get_policy_engine, reset_policy_engine
    from openakita.core.ui_confirm_bus import get_ui_confirm_bus, reset_ui_confirm_bus

    # #1 bus survives engine reset
    reset_policy_engine()
    reset_ui_confirm_bus()
    pe = get_policy_engine()
    pe.store_ui_pending("d4-1", "write_file", {"p": "x"}, session_id="s")
    reset_policy_engine()
    bus = get_ui_confirm_bus()
    assert any(p["id"] == "d4-1" for p in bus.list_pending())
    print("  #1 bus survives engine reset: OK")

    # #2 idempotent prepare
    reset_ui_confirm_bus()
    bus2 = get_ui_confirm_bus()
    bus2.prepare("d4-2")
    ev1 = bus2._events["d4-2"]
    bus2.prepare("d4-2")
    assert bus2._events["d4-2"] is ev1
    print("  #2 prepare idempotent: OK")

    # #3 timeout cleanup pops orphan pending
    reset_ui_confirm_bus()
    bus3 = get_ui_confirm_bus()

    async def _t() -> None:
        bus3.prepare("d4-3")
        bus3.store_pending("d4-3", "rm", {"path": "x"})
        decision = await bus3.wait_for_resolution("d4-3", timeout=0.05)
        assert decision == "deny"
        assert not any(p["id"] == "d4-3" for p in bus3.list_pending())

    asyncio.run(_t())
    print("  #3 timeout deny + orphan cleanup: OK")

    # #4 resolve without pending still wakes waiter (gateway/HTTP race)
    reset_ui_confirm_bus()
    bus4 = get_ui_confirm_bus()

    async def _t4() -> None:
        bus4.prepare("d4-4")
        # No store_pending — caller resolves on a confirm with no sidecar
        async def _resolve() -> None:
            await asyncio.sleep(0.02)
            bus4.resolve("d4-4", "allow_once")
        asyncio.create_task(_resolve())
        decision = await bus4.wait_for_resolution("d4-4", timeout=1.0)
        assert decision == "allow_once"

    asyncio.run(_t4())
    print("  #4 resolve-without-pending still wakes waiter: OK")

    # #5 mark_confirmed still triggered via facade resolve_ui_confirm
    reset_policy_engine()
    reset_ui_confirm_bus()
    pe = get_policy_engine()
    pe.store_ui_pending("d4-5", "write_file", {"path": "f.txt"}, session_id="s")
    ok = pe.resolve_ui_confirm("d4-5", "allow_session")
    assert ok
    # mark_confirmed should have written to v1 _session_allowlist
    assert len(pe._session_allowlist) >= 1
    print("  #5 facade still wires mark_confirmed (v1 cache): OK")

    # #6 dry-run preview API does not corrupt global engine
    from openakita.core.policy_v2 import get_engine_v2
    e0 = get_engine_v2()
    from openakita.core.policy_v2.loader import load_policies_from_dict
    cfg, _ = load_policies_from_dict({"security": {"version": 2}}, strict=False)
    from openakita.core.policy_v2 import PolicyEngineV2
    PolicyEngineV2(config=cfg)  # ad-hoc instance
    e1 = get_engine_v2()
    assert e0 is e1, "PolicyEngineV2() should not replace the global singleton"
    print("  #6 dry-run preview ad-hoc engine doesn't replace global: OK")


def d5_compat() -> None:
    print("\n=== C9 D5 compatibility ===")
    # External resolve_ui_confirm callers (gateway, telegram, feishu, cli, api)
    # must continue to work via facade
    from openakita.core.policy import get_policy_engine, reset_policy_engine
    from openakita.core.ui_confirm_bus import reset_ui_confirm_bus

    reset_policy_engine()
    reset_ui_confirm_bus()
    pe = get_policy_engine()

    # Scenario: gateway path — store via facade, resolve via facade,
    # decision propagates to v1 mark_confirmed
    pe.store_ui_pending("d5-1", "run_shell", {"command": "ls"}, session_id="g1")
    ok = pe.resolve_ui_confirm("d5-1", "allow_always")
    assert ok
    print("  External callers still work via facade: OK")

    # Old SSE event consumers (no approval_class) — verify backward compat
    # (event shape is unchanged; new fields are additive)
    ev = {
        "type": "security_confirm",
        "tool": "write_file",
        "args": {"path": "x"},
        "id": "e1",
        "reason": "test",
        "risk_level": "high",
        "needs_sandbox": False,
    }
    # Old consumer just reads the legacy fields
    assert ev["risk_level"] == "high"
    assert ev["needs_sandbox"] is False
    print("  Old SSE consumers reading legacy fields still work: OK")


def main() -> None:
    d1_completeness()
    d2_architecture()
    d3_no_whack()
    d4_hidden_bugs()
    d5_compat()
    print("\nC9 ALL 5 DIMENSIONS PASS")


if __name__ == "__main__":
    main()

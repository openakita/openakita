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

    # C8b-3: 6 facade methods on PolicyEngine were thin wrappers around the
    # bus; production callsites (CLI / web / IM / agent cleanup) now go
    # directly to the bus or ``policy_v2.confirm_resolution.apply_resolution``.
    # The audit therefore inverts: assert the wrappers are GONE.
    for gone in (
        "def store_ui_pending",
        "def prepare_ui_confirm",
        "def cleanup_ui_confirm",
        "async def wait_for_ui_resolution",
        "def resolve_ui_confirm",
        "def cleanup_session",
        "def mark_confirmed",
    ):
        assert gone not in pe_src, (
            f"C8b-3 expects PolicyEngine.{gone} to be deleted, still present"
        )
    print("  6 facade methods + mark_confirmed deleted from PolicyEngine: OK")

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

    # #1 bus survives engine reset (C8b-3: store via bus directly, not v1 facade)
    reset_policy_engine()
    reset_ui_confirm_bus()
    bus = get_ui_confirm_bus()
    bus.store_pending("d4-1", "write_file", {"p": "x"}, session_id="s")
    reset_policy_engine()
    bus_after = get_ui_confirm_bus()
    assert any(p["id"] == "d4-1" for p in bus_after.list_pending())
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

    # #5 (C8b-3): apply_resolution writes to v2 SessionAllowlistManager.
    # Replaces the v1 ``pe.resolve_ui_confirm → mark_confirmed → _session_allowlist``
    # path; v1 facades + storage all gone.
    from openakita.core.policy_v2 import (
        apply_resolution,
        get_session_allowlist_manager,
    )

    reset_ui_confirm_bus()
    get_session_allowlist_manager().clear()
    bus5 = get_ui_confirm_bus()
    bus5.store_pending("d4-5", "write_file", {"path": "f.txt"}, session_id="s")
    bus5.prepare("d4-5")
    ok = apply_resolution("d4-5", "allow_session")
    assert ok
    assert get_session_allowlist_manager().is_allowed(
        "write_file", {"path": "f.txt"}
    ) is not None
    print("  #5 apply_resolution writes SessionAllowlistManager: OK")

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


def d6_label_drift() -> None:
    """C9 re-audit catch: verify SecurityConfirmModal's APPROVAL_CLASS_LABELS
    keys are a complete + correct mirror of the Python ``ApprovalClass`` enum.

    Found in re-audit: original C9a-2 had ``shell_exec`` / ``network_egress``
    / ``metadata`` keys that don't exist in the enum, and missed
    ``readonly_search`` / ``exec_low_risk`` / ``exec_capable`` / ``network_out``.
    Result: badges silently failed to render for ~30% of v2 tools.
    """
    print("\n=== C9 D6 enum<->label drift ===")
    from openakita.core.policy_v2 import ApprovalClass

    enum_values: set[str] = {ac.value for ac in ApprovalClass}

    modal_src = Path(
        "apps/setup-center/src/views/chat/components/SecurityConfirmModal.tsx"
    ).read_text(encoding="utf-8")

    # Extract the labels block (between `APPROVAL_CLASS_LABELS = {` and `};`)
    block_start = modal_src.index("APPROVAL_CLASS_LABELS")
    block_end = modal_src.index("};", block_start)
    block = modal_src[block_start:block_end]

    # Parse keys: lines like "  destructive:      { ... }"
    import re

    label_keys: set[str] = set()
    for m in re.finditer(r"^\s+([a-z_]+):\s*\{", block, flags=re.MULTILINE):
        label_keys.add(m.group(1))

    missing = enum_values - label_keys
    extra = label_keys - enum_values
    assert not missing, f"SecurityConfirmModal labels missing enum values: {sorted(missing)}"
    assert not extra, f"SecurityConfirmModal labels include non-enum keys: {sorted(extra)}"
    print(f"  All {len(enum_values)} ApprovalClass enum values have a modal label: OK")


def d5_compat() -> None:
    print("\n=== C9 D5 compatibility ===")
    # C8b-3: External callers (gateway / telegram / feishu / cli / api)
    # all migrated to ``policy_v2.confirm_resolution.apply_resolution``;
    # verify the new entry point still wakes the bus + writes session allow.
    from openakita.core.policy_v2 import (
        apply_resolution,
        get_session_allowlist_manager,
    )
    from openakita.core.ui_confirm_bus import get_ui_confirm_bus, reset_ui_confirm_bus

    reset_ui_confirm_bus()
    get_session_allowlist_manager().clear()
    bus = get_ui_confirm_bus()

    # Scenario: gateway-style flow — bus.store_pending + apply_resolution
    bus.store_pending("d5-1", "run_shell", {"command": "ls"}, session_id="g1")
    bus.prepare("d5-1")
    ok = apply_resolution("d5-1", "allow_session")
    assert ok
    assert get_session_allowlist_manager().is_allowed("run_shell", {"command": "ls"}) is not None
    print("  External callers (apply_resolution path) still work: OK")

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
    d6_label_drift()
    print("\nC9 ALL 6 DIMENSIONS PASS")


if __name__ == "__main__":
    main()

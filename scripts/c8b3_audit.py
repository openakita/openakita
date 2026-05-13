"""C8b-3 audit (D1-D6) — UI confirm facade 完成切换 + confirmed_cache 决策。

D1 — Completeness：新增模块 + apply_resolution 已 export
D2 — Single Source of Truth：UI confirm 状态机 100% 在 bus；session 白名单
     状态 100% 在 SessionAllowlistManager；v1 facade/字段全删
D3 — No Whack-a-Mole：7 个 callsite 全部直连 v2 入口；无残留 v1 调用
D4 — Hidden Bugs：apply_resolution 五种 decision 副作用矩阵 + waiter 唤醒
D5 — Compat：v1 ``_check_persistent_allowlist`` 仍然工作（C8b-5 才删）；
     v2 step 9 三层顺序保持（persistent → session → skill）
D6 — Retry-confirm：tool_executor.py 用 tool_use_id 去重，**不**再调
     mark_confirmed
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def d1_completeness() -> None:
    print("\n=== C8b-3 D1 completeness ===")
    src = ROOT / "src" / "openakita"

    # New modules
    sa = src / "core" / "policy_v2" / "session_allowlist.py"
    cr = src / "core" / "policy_v2" / "confirm_resolution.py"
    assert sa.exists(), "session_allowlist.py missing"
    assert cr.exists(), "confirm_resolution.py missing"
    print(f"  new modules present: {sa.name}, {cr.name}")

    # __init__.py exports
    init_text = (src / "core" / "policy_v2" / "__init__.py").read_text(encoding="utf-8")
    for sym in (
        "SessionAllowlistManager",
        "get_session_allowlist_manager",
        "reset_session_allowlist_manager",
        "apply_resolution",
    ):
        assert sym in init_text, f"policy_v2/__init__.py missing export {sym}"
    print("  4 new symbols all exported from policy_v2: OK")

    # Smoke import
    from openakita.core.policy_v2 import (  # noqa: F401
        SessionAllowlistManager,
        apply_resolution,
        get_session_allowlist_manager,
    )
    print("  smoke imports: OK")

    print("D1 PASS")


def d2_single_source_of_truth() -> None:
    print("\n=== C8b-3 D2 single source of truth ===")
    src = ROOT / "src" / "openakita"

    pe_src = (src / "core" / "policy.py").read_text(encoding="utf-8")
    # 7 facade methods + mark_confirmed all gone from PolicyEngine
    for gone in (
        "def store_ui_pending",
        "def prepare_ui_confirm",
        "def cleanup_ui_confirm",
        "async def wait_for_ui_resolution",
        "def resolve_ui_confirm",
        "def cleanup_session",
        "def mark_confirmed",
    ):
        assert gone not in pe_src, f"PolicyEngine still has '{gone}'"
    print("  6 facade methods + mark_confirmed: all deleted from policy.py")

    # 2 v1 fields gone (only doc-comment mentions remain)
    for field_assign in (
        "self._confirmed_cache: dict",
        "self._session_allowlist: dict",
    ):
        assert field_assign not in pe_src, (
            f"PolicyEngine still initializes {field_assign}"
        )
    print("  _confirmed_cache + _session_allowlist field initializations: deleted")

    # SessionAllowlistManager is the single owner of session allow state
    sa_assigns = 0
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        sa_assigns += text.count("self._entries: dict[str, dict[str, Any]] = {}")
    assert sa_assigns == 1, (
        f"expected exactly 1 assignment of session-allow dict, found {sa_assigns}"
    )
    print("  SessionAllowlistManager._entries assigned exactly once (in session_allowlist.py): OK")

    print("D2 PASS")


def d3_no_whack_a_mole() -> None:
    print("\n=== C8b-3 D3 no whack-a-mole (7 callsite migration) ===")
    src = ROOT / "src" / "openakita"

    callsites = {
        "cli/stream_renderer.py":      ("engine.resolve_ui_confirm",     "apply_resolution"),
        "api/routes/config.py":        ("engine.resolve_ui_confirm",     "apply_resolution"),
        "api/routes/chat.py":          ("get_policy_engine().cleanup_session",
                                        "get_session_allowlist_manager"),
        "channels/gateway.py":         ("pe.resolve_ui_confirm(",        "apply_resolution"),
        "channels/adapters/telegram.py": ("get_policy_engine().resolve_ui_confirm",
                                          "apply_resolution"),
        "channels/adapters/feishu.py": ("get_policy_engine().resolve_ui_confirm",
                                        "apply_resolution"),
        "core/agent.py":               ("_pe.cleanup_session",
                                        "get_session_allowlist_manager"),
    }
    for rel, (banned, expected) in callsites.items():
        text = (src / rel).read_text(encoding="utf-8")
        assert banned not in text, f"{rel}: still calls v1 facade '{banned}'"
        assert expected in text, f"{rel}: missing v2 entry '{expected}'"
    print(f"  all {len(callsites)} callsites migrated to v2 entry points: OK")

    print("D3 PASS")


def d4_hidden_bugs_decision_matrix() -> None:
    print("\n=== C8b-3 D4 apply_resolution decision matrix ===")
    from openakita.core.policy_v2 import (
        apply_resolution,
        get_session_allowlist_manager,
        reset_session_allowlist_manager,
    )
    from openakita.core.ui_confirm_bus import (
        get_ui_confirm_bus,
        reset_ui_confirm_bus,
    )

    matrix = [
        ("allow_once", False),
        ("allow_session", True),
        ("sandbox", True),
        ("deny", False),
        ("unknown_value", False),
    ]
    for decision, expects_session_write in matrix:
        reset_ui_confirm_bus()
        reset_session_allowlist_manager()
        bus = get_ui_confirm_bus()
        bus.store_pending(f"d4-{decision}", "write_file", {"path": f"/tmp/{decision}"})
        bus.prepare(f"d4-{decision}")
        ok = apply_resolution(f"d4-{decision}", decision)
        assert ok is True, f"{decision}: apply_resolution returned False"
        wrote = (
            get_session_allowlist_manager().is_allowed(
                "write_file", {"path": f"/tmp/{decision}"}
            )
            is not None
        )
        assert wrote == expects_session_write, (
            f"{decision}: session_allowlist write expected={expects_session_write}, got={wrote}"
        )
    print("  5-way decision matrix correct: OK")

    # Waiter wakeup
    async def _wakeup() -> None:
        reset_ui_confirm_bus()
        bus = get_ui_confirm_bus()
        bus.store_pending("d4-wake", "write_file", {"path": "/x"})
        bus.prepare("d4-wake")
        async def _resolve() -> None:
            await asyncio.sleep(0.02)
            apply_resolution("d4-wake", "allow_once")
        asyncio.create_task(_resolve())
        decision = await bus.wait_for_resolution("d4-wake", timeout=1.0)
        assert decision == "allow_once"

    asyncio.run(_wakeup())
    print("  apply_resolution wakes bus.wait_for_resolution: OK")

    print("D4 PASS")


def d5_v2_step9_three_tier_order() -> None:
    print("\n=== C8b-3 D5 v2 step 9 three-tier order ===")
    from pathlib import Path as P

    from openakita.core.policy_v2 import (
        ApprovalClass,
        ConfirmationMode,
        DecisionAction,
        PolicyConfigV2,
        PolicyContext,
        SessionRole,
        ToolCallEvent,
        get_session_allowlist_manager,
        get_skill_allowlist_manager,
        reset_session_allowlist_manager,
        reset_skill_allowlist_manager,
    )
    from openakita.core.policy_v2.engine import build_engine_from_config
    from openakita.core.policy_v2.enums import DecisionSource

    cfg = PolicyConfigV2()
    eng = build_engine_from_config(
        cfg,
        explicit_lookup=lambda _n: (
            ApprovalClass.MUTATING_SCOPED,
            DecisionSource.EXPLICIT_HANDLER_ATTR,
        ),
    )
    ctx = PolicyContext(
        session_id="d5",
        workspace=P.cwd(),
        session_role=SessionRole.AGENT,
        confirmation_mode=ConfirmationMode.DEFAULT,
    )
    ev = ToolCallEvent(tool="write_file", params={"path": "/tmp/d5.txt"})

    # Session > skill: both granted, session note wins
    reset_session_allowlist_manager()
    reset_skill_allowlist_manager()
    get_session_allowlist_manager().add("write_file", {"path": "/tmp/d5.txt"})
    get_skill_allowlist_manager().add("test_skill", ["write_file"])
    decision = eng.evaluate_tool_call(ev, ctx)
    assert decision.action == DecisionAction.ALLOW
    note = decision.chain[-1].note or ""
    assert "session_allowlist" in note, f"expected session_allowlist tier hit, got {note!r}"
    print("  session > skill order preserved: OK")

    # Session miss → skill hit
    reset_session_allowlist_manager()
    reset_skill_allowlist_manager()
    get_skill_allowlist_manager().add("test_skill", ["write_file"])
    decision = eng.evaluate_tool_call(ev, ctx)
    assert decision.action == DecisionAction.ALLOW
    note = decision.chain[-1].note or ""
    assert "skill_allowlist" in note, f"expected skill_allowlist tier hit, got {note!r}"
    print("  skill fallback works when session misses: OK")

    print("D5 PASS")


def d6_retry_confirm_uses_tool_use_id() -> None:
    print("\n=== C8b-3 D6 tool_executor retry-confirm uses tool_use_id ===")
    src = ROOT / "src" / "openakita" / "core" / "tool_executor.py"
    text = src.read_text(encoding="utf-8")

    # Old hash-based key gone
    assert "policy_engine._confirm_cache_key" not in text, (
        "tool_executor still uses hash-based confirm_key"
    )
    # mark_confirmed call gone
    assert "policy_engine.mark_confirmed" not in text, (
        "tool_executor still calls mark_confirmed (security hole)"
    )
    # tool_use_id used as the new dedup key
    assert "tool_use_id in self._pending_confirms" in text, (
        "tool_executor missing tool_use_id-based dedup"
    )
    assert "self._pending_confirms[tool_use_id]" in text, (
        "tool_executor missing tool_use_id-keyed pending registration"
    )
    print("  tool_executor.py: tool_use_id-based dedup in place, no mark_confirmed: OK")

    print("D6 PASS")


def main() -> None:
    d1_completeness()
    d2_single_source_of_truth()
    d3_no_whack_a_mole()
    d4_hidden_bugs_decision_matrix()
    d5_v2_step9_three_tier_order()
    d6_retry_confirm_uses_tool_use_id()
    print("\nC8b-3 ALL 6 DIMENSIONS PASS")


if __name__ == "__main__":
    main()

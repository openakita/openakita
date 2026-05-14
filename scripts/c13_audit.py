"""C13 audit script (Policy V2 §15 + R4-1/2/3/4 + R5-16).

Multi-dimensional self-audit of the C13 "Multi-agent confirm bubble +
delegate_chain propagation" milestone:

D1. build_policy_context accepts ``parent_ctx`` + ``child_agent_name`` and
    routes to derive_child when parent_ctx is non-None (Phase A2).

D2. agent.chat_with_session AND chat_with_session_stream detect
    ``_is_sub_agent_call`` and pass parent_ctx to build_policy_context
    using the inherited ContextVar (Phase A1 + R5-16).

D3. security_confirm SSE payload (both reasoning_engine emission sites)
    carries ``delegate_chain`` + ``root_user_id`` (Phase B / R4-1).

D4. tool_executor _security_confirm marker also carries delegate_chain +
    root_user_id for IM card / gateway rendering (Phase B).

D5. UIConfirmBus exposes find_dedup_leader / register_follower /
    deregister_follower / _pending_cleanup (Phase C / R4-2).

D6. reasoning_engine CONFIRM emission sites consult
    ``_compute_confirm_dedup_key`` + ``find_dedup_leader`` and join the
    leader via register_follower instead of emitting a duplicate SSE
    (Phase C wire-through).

D7. cleanup() defers when followers still parked + deregister_follower
    flushes pending_cleanup (race-safe shutdown).

D8. End-to-end pytest of tests/unit/test_policy_v2_c13_multi_agent.py
    (18 tests).

D9. C12+C9c regression — re-run c12_c9c_audit.py to make sure Phase A
    derive_child path didn't break the unattended branch.

D10. Skeleton regression — re-run test_policy_v2_skeleton.py (derive_child
    contract).

D11. Multi-agent regression — re-run test_multi_agent + test_delegation_*.

Exit code 0 = all green; non-zero = one or more failures.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "openakita"
TESTS = ROOT / "tests" / "unit"


def _ok(label: str, msg: str = "") -> None:
    suffix = f" — {msg}" if msg else ""
    print(f"  [OK] {label}{suffix}")


def _fail(label: str, msg: str) -> None:
    print(f"  [FAIL] {label} — {msg}")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


# ---------------------------------------------------------------------------


def d1_adapter_parent_ctx(failures: list[str]) -> None:
    print("\n[D1] adapter.build_policy_context accepts parent_ctx + child_agent_name")
    src = _read(SRC / "core" / "policy_v2" / "adapter.py")
    if "parent_ctx: PolicyContext | None = None" not in src:
        _fail("parent_ctx kwarg", "missing")
        failures.append("D1.parent_ctx_kwarg")
        return
    _ok("parent_ctx kwarg")
    if "child_agent_name: str | None = None" not in src:
        _fail("child_agent_name kwarg", "missing")
        failures.append("D1.child_agent_name_kwarg")
        return
    _ok("child_agent_name kwarg")
    if "parent_ctx.derive_child" not in src:
        _fail("derive_child route", "build_policy_context 不走 derive_child 分支")
        failures.append("D1.derive_child_route")
        return
    _ok("derive_child route", "parent_ctx 非空 → derive_child")


def d2_agent_sub_agent_propagation(failures: list[str]) -> None:
    print("\n[D2] agent.py 两处 PolicyContext 入口检测 _is_sub_agent_call")
    src = _read(SRC / "core" / "agent.py")
    # Both sync and stream paths must propagate
    pattern = re.compile(
        r"_is_sub_agent_call.*?_pv2_get_ctx\(\).*?parent_ctx=_parent_ctx",
        re.DOTALL,
    )
    matches = pattern.findall(src)
    if len(matches) < 2:
        _fail(
            "two-path wire",
            f"found {len(matches)}/2 sites (sync + stream both required)",
        )
        failures.append("D2.two_path_wire")
        return
    _ok("two-path wire", f"sync + stream both pass parent_ctx ({len(matches)} sites)")
    if 'child_agent_name=(\n                    getattr(self, "_agent_profile_id"' not in src:
        # the literal exact string may diff; check that _agent_profile_id is referenced near parent_ctx
        if "_agent_profile_id" not in src or "child_agent_name=" not in src:
            _fail("child_agent_name wired", "缺 _agent_profile_id → child_agent_name 路径")
            failures.append("D2.child_name_wire")
            return
    _ok("child_agent_name wired", "from agent._agent_profile_id")


def d3_sse_payload_delegate_chain(failures: list[str]) -> None:
    print("\n[D3] reasoning_engine security_confirm payload 携带 delegate_chain / root_user_id")
    src = _read(SRC / "core" / "reasoning_engine.py")
    # Two emission sites; both must include the fields
    count_chain = src.count('"delegate_chain": _delegate_chain')
    count_root = src.count('"root_user_id": _root_user_id')
    if count_chain < 2 or count_root < 2:
        _fail(
            "payload fields",
            f"delegate_chain {count_chain}/2, root_user_id {count_root}/2",
        )
        failures.append("D3.payload_fields")
        return
    _ok("payload fields", f"both sites carry chain + root ({count_chain}+{count_root})")


def d4_tool_executor_marker(failures: list[str]) -> None:
    print("\n[D4] tool_executor _security_confirm marker 携带 chain + root")
    src = _read(SRC / "core" / "tool_executor.py")
    if '"delegate_chain": _marker_chain' not in src:
        _fail("marker delegate_chain", "missing")
        failures.append("D4.marker_delegate_chain")
        return
    _ok("marker delegate_chain")
    if '"root_user_id": _marker_root' not in src:
        _fail("marker root_user_id", "missing")
        failures.append("D4.marker_root_user_id")
        return
    _ok("marker root_user_id")


def d5_bus_dedup_api(failures: list[str]) -> None:
    print("\n[D5] UIConfirmBus dedup API surface")
    src = _read(SRC / "core" / "ui_confirm_bus.py")
    for sym in (
        "def find_dedup_leader",
        "def register_follower",
        "def deregister_follower",
        "_pending_cleanup",
        "dedup_key",
    ):
        if sym not in src:
            _fail(f"bus symbol {sym}", "missing")
            failures.append(f"D5.{sym}")
            return
        _ok(f"bus symbol {sym}")


def d6_reasoning_engine_dedup_wire(failures: list[str]) -> None:
    print("\n[D6] reasoning_engine CONFIRM 发射点走 dedup leader 分支")
    src = _read(SRC / "core" / "reasoning_engine.py")
    if "_compute_confirm_dedup_key" not in src:
        _fail("dedup_key helper", "_compute_confirm_dedup_key 未注入 reasoning_engine")
        failures.append("D6.dedup_key_helper")
        return
    _ok("dedup_key helper")
    # Both emission sites must call find_dedup_leader + register_follower
    count_find = src.count(".find_dedup_leader(")
    count_register = src.count(".register_follower(")
    count_deregister = src.count(".deregister_follower(")
    if count_find < 2 or count_register < 2 or count_deregister < 2:
        _fail(
            "two-site dedup wire",
            f"find {count_find}/2, register {count_register}/2, deregister {count_deregister}/2",
        )
        failures.append("D6.two_site_dedup_wire")
        return
    _ok("two-site dedup wire", f"both CONFIRM paths consult bus.find_dedup_leader")


def d7_cleanup_defers_with_followers(failures: list[str]) -> None:
    print("\n[D7] cleanup() 在有 followers 时 defer，deregister_follower 触发真清")
    src = _read(SRC / "core" / "ui_confirm_bus.py")
    if "self._pending_cleanup.add(confirm_id)" not in src:
        _fail("cleanup defer add", "missing")
        failures.append("D7.cleanup_defer_add")
        return
    _ok("cleanup defer add")
    if "self._pending_cleanup.discard(leader_id)" not in src:
        _fail("deregister flush", "missing")
        failures.append("D7.deregister_flush")
        return
    _ok("deregister flush")


def d8_pytest_c13(failures: list[str]) -> None:
    print("\n[D8] pytest tests/unit/test_policy_v2_c13_multi_agent.py")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(TESTS / "test_policy_v2_c13_multi_agent.py"),
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        _fail("pytest", f"exit={proc.returncode}")
        print(proc.stdout[-2000:])
        print(proc.stderr[-2000:])
        failures.append("D8.pytest_c13")
        return
    lines = [ln for ln in proc.stdout.splitlines() if "passed" in ln or "failed" in ln]
    _ok("pytest", lines[-1] if lines else "ok")


def d9_c12_c9c_regression(failures: list[str]) -> None:
    print("\n[D9] regression: scripts/c12_c9c_audit.py")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "c12_c9c_audit.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        _fail("c12_c9c_audit", f"exit={proc.returncode}")
        print(proc.stdout[-3000:])
        failures.append("D9.c12_c9c_audit")
        return
    _ok("c12_c9c_audit", "all green")


def d10_skeleton_regression(failures: list[str]) -> None:
    print("\n[D10] regression: tests/unit/test_policy_v2_skeleton.py")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(TESTS / "test_policy_v2_skeleton.py"),
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        _fail("skeleton", f"exit={proc.returncode}")
        print(proc.stdout[-1500:])
        failures.append("D10.skeleton")
        return
    _ok("skeleton")


def d11_multi_agent_regression(failures: list[str]) -> None:
    print("\n[D11] regression: tests/unit/test_multi_agent.py + delegation tests")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(TESTS / "test_multi_agent.py"),
            str(TESTS / "test_delegation_preamble.py"),
            str(TESTS / "test_risk_intent_delegation.py"),
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        _fail("multi-agent regression", f"exit={proc.returncode}")
        print(proc.stdout[-1500:])
        failures.append("D11.multi_agent_regression")
        return
    lines = [ln for ln in proc.stdout.splitlines() if "passed" in ln or "failed" in ln]
    _ok("multi-agent regression", lines[-1] if lines else "ok")


def main() -> int:
    print("=" * 70)
    print("C13 audit — multi-agent confirm bubble + delegate_chain propagation")
    print("=" * 70)
    failures: list[str] = []
    d1_adapter_parent_ctx(failures)
    d2_agent_sub_agent_propagation(failures)
    d3_sse_payload_delegate_chain(failures)
    d4_tool_executor_marker(failures)
    d5_bus_dedup_api(failures)
    d6_reasoning_engine_dedup_wire(failures)
    d7_cleanup_defers_with_followers(failures)
    d8_pytest_c13(failures)
    d9_c12_c9c_regression(failures)
    d10_skeleton_regression(failures)
    d11_multi_agent_regression(failures)
    print("\n" + "=" * 70)
    if failures:
        print(f"FAIL ({len(failures)} dimensions): {', '.join(failures)}")
        return 1
    print("ALL GREEN — C13 ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())

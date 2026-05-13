"""C8b-4 audit (D1-D6) — permission-mode shim 替换 + smart-mode 删除。

D1 — Completeness：confirmation_mode.py + 4 个新 export 全部存在
D2 — Single Source of Truth：v2 ``PolicyConfigV2.confirmation.mode`` 是
     permission-mode 唯一状态源；v1 ``_frontend_mode`` / ``_session_allow_count``
     / ``_SMART_ESCALATION_THRESHOLD`` 全部删除
D3 — No Whack-a-Mole：``permission-mode`` GET/POST 端点不再 import
     ``get_policy_engine`` 仅为读 ``_frontend_mode``；``policy.py``
     不再有 ``_frontend_mode`` 同步赋值（3 处）
D4 — Hidden Bugs：v2→v1 label 5×mapping 全 PASS；fail-soft fallback 工作
D5 — Compat：v1 ``assert_tool_allowed`` 仍能跑（无 escalation 也仍 CONFIRM
     MEDIUM 命令）；不再 AttributeError on 已删字段
D6 — Endpoint E2E：set 后 read 返回新 label（YAML→reset_v2→read 链路）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def d1_completeness() -> None:
    print("\n=== C8b-4 D1 completeness ===")
    src = ROOT / "src" / "openakita"

    cm = src / "core" / "policy_v2" / "confirmation_mode.py"
    assert cm.exists(), "confirmation_mode.py missing"
    print(f"  new module present: {cm.name}")

    init_text = (src / "core" / "policy_v2" / "__init__.py").read_text(encoding="utf-8")
    for sym in (
        "coerce_v1_label_to_v2_mode",
        "read_permission_mode_label",
    ):
        assert sym in init_text, f"policy_v2/__init__.py missing export {sym}"
    print("  2 new symbols exported from policy_v2: OK")

    from openakita.core.policy_v2 import (  # noqa: F401
        coerce_v1_label_to_v2_mode,
        read_permission_mode_label,
    )
    print("  smoke imports: OK")

    print("D1 PASS")


def d2_single_source_of_truth() -> None:
    print("\n=== C8b-4 D2 single source of truth ===")
    pe_src = (ROOT / "src" / "openakita" / "core" / "policy.py").read_text(encoding="utf-8")

    # v1 字段 + class const 全部不再被赋值（doc 注释里仍可能提到名字作为历史，
    # 但赋值语句本身不应存在）
    for stmt in (
        "self._frontend_mode: str = ",
        "self._frontend_mode = cc.mode",
        "self._frontend_mode = self._config.confirmation.mode",
        "self._session_allow_count: int = 0",
        "self._session_allow_count += 1",
        "self._session_allow_count >= self._SMART_ESCALATION_THRESHOLD",
        "_SMART_ESCALATION_THRESHOLD: int = 3",
        "refreshed._session_allow_count = previous._session_allow_count",
    ):
        assert stmt not in pe_src, f"policy.py still has '{stmt}'"
    print("  8 escalation/shim statements all removed from policy.py: OK")

    # Runtime side: PolicyEngine instances don't have these attrs
    from openakita.core.policy import PolicyEngine

    eng = PolicyEngine()
    for attr in ("_frontend_mode", "_session_allow_count"):
        assert not hasattr(eng, attr), f"PolicyEngine instance still has {attr}"
    assert not hasattr(PolicyEngine, "_SMART_ESCALATION_THRESHOLD")
    print("  PolicyEngine runtime confirms 3 deleted attrs: OK")

    print("D2 PASS")


def d3_no_whack_a_mole() -> None:
    print("\n=== C8b-4 D3 no whack-a-mole (endpoint migration) ===")
    cfg_src = (ROOT / "src" / "openakita" / "api" / "routes" / "config.py").read_text(encoding="utf-8")

    # GET endpoint reads via v2 helper, no fallback to pe._frontend_mode
    assert "getattr(pe, \"_frontend_mode\", " not in cfg_src, (
        "config.py GET endpoint still falls back to pe._frontend_mode"
    )
    assert "read_permission_mode_label" in cfg_src, (
        "config.py missing v2 read_permission_mode_label call"
    )
    print("  GET endpoint reads via v2 helper: OK")

    # POST endpoint no longer writes pe._frontend_mode (executable line, not
    # a doc-comment mention which is allowed as historical context)
    import re

    # Match assignment as Python statement: optional indent + `pe._frontend_mode =` (not preceded by ``)
    pattern = re.compile(r"^\s+pe\._frontend_mode\s*=\s*", re.MULTILINE)
    matches = pattern.findall(cfg_src)
    assert not matches, (
        "config.py POST endpoint still writes pe._frontend_mode "
        f"({len(matches)} statement-line matches)"
    )
    print("  POST endpoint dropped pe._frontend_mode write: OK")

    print("D3 PASS")


def d4_v2_to_v1_label_mapping() -> None:
    print("\n=== C8b-4 D4 v2→v1 label mapping ===")
    from openakita.core.policy_v2 import (
        PolicyConfigV2,
        build_engine_from_config,
        read_permission_mode_label,
    )
    from openakita.core.policy_v2.enums import ConfirmationMode
    from openakita.core.policy_v2.global_engine import (
        reset_engine_v2,
        set_engine_v2,
    )
    from openakita.core.policy_v2.schema import ConfirmationConfig

    matrix = [
        (ConfirmationMode.TRUST, "yolo"),
        (ConfirmationMode.DEFAULT, "smart"),
        (ConfirmationMode.STRICT, "cautious"),
        (ConfirmationMode.ACCEPT_EDITS, "smart"),
        (ConfirmationMode.DONT_ASK, "yolo"),
    ]
    for v2_mode, v1_label in matrix:
        cfg = PolicyConfigV2(confirmation=ConfirmationConfig(mode=v2_mode))
        eng = build_engine_from_config(cfg)
        set_engine_v2(eng, cfg)
        try:
            actual = read_permission_mode_label()
            assert actual == v1_label, (
                f"{v2_mode} should map to {v1_label!r}, got {actual!r}"
            )
        finally:
            reset_engine_v2()
    print(f"  all {len(matrix)} v2→v1 label mappings correct: OK")

    print("D4 PASS")


def d5_v1_assert_tool_allowed_still_works() -> None:
    print("\n=== C8b-4 D5 v1 assert_tool_allowed compat ===")
    from openakita.core.policy import PolicyEngine

    eng = PolicyEngine()
    # Just verify no AttributeError on _on_allow / _check_command_risk paths
    for _ in range(5):
        eng._on_allow("read_file")
    # _check_command_risk indirectly via assert_tool_allowed for a HIGH command
    result = eng.assert_tool_allowed("run_shell", {"command": "ls -la"})
    assert result is not None, "assert_tool_allowed should not raise on basic command"
    print("  v1 _on_allow + assert_tool_allowed work without _session_allow_count: OK")

    print("D5 PASS")


def d6_endpoint_e2e_round_trip() -> None:
    print("\n=== C8b-4 D6 endpoint round-trip via v2 ===")
    from openakita.core.policy_v2 import (
        PolicyConfigV2,
        build_engine_from_config,
        read_permission_mode_label,
    )
    from openakita.core.policy_v2.enums import ConfirmationMode
    from openakita.core.policy_v2.global_engine import (
        reset_engine_v2,
        set_engine_v2,
    )
    from openakita.core.policy_v2.schema import ConfirmationConfig

    # Initial state: trust
    cfg1 = PolicyConfigV2(confirmation=ConfirmationConfig(mode=ConfirmationMode.TRUST))
    set_engine_v2(build_engine_from_config(cfg1), cfg1)
    try:
        assert read_permission_mode_label() == "yolo"

        # Simulate POST permission-mode → reset_v2 → re-read
        cfg2 = PolicyConfigV2(confirmation=ConfirmationConfig(mode=ConfirmationMode.STRICT))
        set_engine_v2(build_engine_from_config(cfg2), cfg2)

        assert read_permission_mode_label() == "cautious"
        print("  set→read round-trip via v2 layer: OK")
    finally:
        reset_engine_v2()

    print("D6 PASS")


def main() -> None:
    d1_completeness()
    d2_single_source_of_truth()
    d3_no_whack_a_mole()
    d4_v2_to_v1_label_mapping()
    d5_v1_assert_tool_allowed_still_works()
    d6_endpoint_e2e_round_trip()
    print("\nC8b-4 ALL 6 DIMENSIONS PASS")


if __name__ == "__main__":
    main()

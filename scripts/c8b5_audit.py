"""C8b-5 audit (D1-D5) — 外部 _is_trust_mode caller 全部切到 v2 + isolation 验证。

D1 — Completeness：agent.py + gateway.py 2 callsite 都 import
     ``read_permission_mode_label``
D2 — Single Source of Truth：``_is_trust_mode`` v1 method 仅剩 1 个外部
     ``policy.py:assert_tool_allowed`` 内部 caller；``getattr(pe,
     "_is_trust_mode", ...)`` 模式全部消除
D3 — No Whack-a-Mole：``_check_trust_mode_skip`` v1+v2 双查回退到纯 v2，无
     defensive v1 fallback 残留
D4 — Equivalence：v2 ``read_permission_mode_label() == "yolo"`` 与 v1
     ``_is_trust_mode()`` 在 trust/non-trust 两种 mode 下结果一致
D5 — Compat：v1 ``assert_tool_allowed`` 仍能跑（_is_trust_mode 内部 caller
     不破）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def d1_callsite_completeness() -> None:
    print("\n=== C8b-5 D1 callsite completeness ===")

    agent_text = (ROOT / "src" / "openakita" / "core" / "agent.py").read_text(encoding="utf-8")
    gateway_text = (ROOT / "src" / "openakita" / "channels" / "gateway.py").read_text(encoding="utf-8")

    assert "from .policy_v2 import ConfirmationMode" in agent_text or \
        "from .policy_v2.global_engine import get_config_v2" in agent_text, (
        "agent.py missing v2 import in _check_trust_mode_skip"
    )
    print("  agent.py imports v2 ConfirmationMode + get_config_v2: OK")

    assert "from ..core.policy_v2 import read_permission_mode_label" in gateway_text, (
        "gateway.py missing read_permission_mode_label import"
    )
    print("  gateway.py imports v2 read_permission_mode_label: OK")

    print("D1 PASS")


def d2_single_source_of_truth() -> None:
    print("\n=== C8b-5 D2 single source of truth ===")

    src = ROOT / "src" / "openakita"
    # Hunt for any external `_is_trust_mode` access pattern (statement-level, not docstring).
    import re

    # Pattern: ``getattr(... "_is_trust_mode" ...)`` or ``pe._is_trust_mode()`` or
    # ``engine._is_trust_mode()`` — any **callsite** of v1 method.
    pattern = re.compile(
        r"(getattr\([^,]+?,\s*[\"']_is_trust_mode[\"'])"  # getattr-style
        r"|((?:pe|engine|self|_pe|policy_engine|_engine)\._is_trust_mode\s*\()"  # attr-call
    )

    callsites: list[tuple[Path, int, str]] = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            # 跳过 doc comment（``...`` 包裹 / 行首 #）
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                # `self._is_trust_mode(` 在 ``policy.py:assert_tool_allowed`` 路径
                # 内部允许（v1 私有），不计入。
                if py.name == "policy.py" and "self._is_trust_mode(" in line:
                    continue
                callsites.append((py, i, line.strip()))

    assert not callsites, (
        "_is_trust_mode external callers still exist:\n"
        + "\n".join(f"  {p.relative_to(ROOT)}:{ln}: {code}" for p, ln, code in callsites)
    )
    print("  zero external _is_trust_mode callers (v1 method now isolated): OK")

    print("D2 PASS")


def d3_no_defensive_v1_fallback() -> None:
    print("\n=== C8b-5 D3 no defensive v1 fallback in _check_trust_mode_skip ===")

    agent_text = (ROOT / "src" / "openakita" / "core" / "agent.py").read_text(encoding="utf-8")
    # Locate the function body
    import re

    m = re.search(
        r"def _check_trust_mode_skip\([^)]*\)[^:]*:\s*\n(.*?)\n(?=\n\S|\nclass |\ndef )",
        agent_text,
        re.DOTALL,
    )
    assert m is not None, "Cannot locate _check_trust_mode_skip body"
    body = m.group(1)

    # The body should NOT import v1 ``policy.get_policy_engine`` anymore
    assert "from .policy import get_policy_engine" not in body, (
        "_check_trust_mode_skip still imports v1 policy module"
    )
    # No more `v1_trust` / `v2_trust` dual-check pattern
    assert "v1_trust" not in body, "_check_trust_mode_skip still has v1_trust variable"
    print("  _check_trust_mode_skip has no v1 import + no dual-check: OK")

    print("D3 PASS")


def d4_v1_v2_equivalence() -> None:
    print("\n=== C8b-5 D4 v2 trust mode label correctness (C8b-6b updated) ===")
    # C8b-6b：v1 ``policy.py`` 已删，原 v1↔v2 等价检查无 v1 一侧。改为单独
    # 锁死 v2 ``read_permission_mode_label`` 在 5 档 mode 下的正确性。
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
    for mode, expected_label in matrix:
        cfg = PolicyConfigV2(confirmation=ConfirmationConfig(mode=mode))
        set_engine_v2(build_engine_from_config(cfg), cfg)
        try:
            actual = read_permission_mode_label()
            assert actual == expected_label, (
                f"{mode} → expected {expected_label!r}, got {actual!r}"
            )
        finally:
            reset_engine_v2()
    print(f"  all {len(matrix)} v2 mode labels correct: OK")

    print("D4 PASS")


def d5_v1_internal_caller_still_works() -> None:
    print("\n=== C8b-5 D5 v1 module fully deleted (C8b-6b updated) ===")
    # C8b-6b: 反向断言——v1 ``policy.py`` 模块完全不可导入；v2 ``evaluate_via_v2``
    # 是工具决策的唯一入口。
    try:
        __import__("openakita.core.policy")
        raise AssertionError("openakita.core.policy 仍可导入——C8b-6b 应已删除")
    except ModuleNotFoundError:
        pass
    print("  v1 policy.py module fully deleted: OK")

    from openakita.core.policy_v2.adapter import evaluate_via_v2

    decision = evaluate_via_v2("read_file", {"path": "/tmp/test.txt"})
    assert decision is not None, "v2 evaluate_via_v2 broken"
    print("  v2 evaluate_via_v2 main entry healthy: OK")

    print("D5 PASS")


def main() -> None:
    d1_callsite_completeness()
    d2_single_source_of_truth()
    d3_no_defensive_v1_fallback()
    d4_v1_v2_equivalence()
    d5_v1_internal_caller_still_works()
    print("\nC8b-5 ALL 5 DIMENSIONS PASS")


if __name__ == "__main__":
    main()

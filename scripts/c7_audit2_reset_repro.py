"""C7 二轮 audit reproducer：UI Save Settings 后 explicit_lookup 丢失。

复现路径：
1. agent._init_handlers() 调 rebuild_engine_v2(explicit_lookup=registry.get_tool_class)
2. classifier 用 EXPLICIT_HANDLER_ATTR
3. UI 改设置 → api/routes/config.py 调 reset_policy_engine()
4. reset_policy_engine() 调 reset_engine_v2() → _engine=None
5. 下次 get_engine_v2() 懒加载 → _build_default_engine() 不传 explicit_lookup
6. classifier 退化到 HEURISTIC

预期：步骤 6 仍应是 EXPLICIT_HANDLER_ATTR。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from openakita.core.policy_v2 import ApprovalClass, DecisionSource
    from openakita.core.policy_v2.global_engine import (
        get_engine_v2,
        rebuild_engine_v2,
        reset_engine_v2,
    )

    fake_lookup = lambda name: (
        (ApprovalClass.MUTATING_SCOPED, DecisionSource.EXPLICIT_HANDLER_ATTR)
        if name == "fake_test_tool"
        else None
    )

    reset_engine_v2()
    rebuild_engine_v2(explicit_lookup=fake_lookup)
    engine = get_engine_v2()
    ac, src = engine._classifier.classify_with_source("fake_test_tool")
    assert src == DecisionSource.EXPLICIT_HANDLER_ATTR, (
        f"step 2: expected EXPLICIT_HANDLER_ATTR, got {src!r}"
    )
    print(f"[step 2] classify(fake_test_tool) -> {ac.value} via {src.value}")

    print("[step 4] reset_engine_v2() (simulating UI Save Settings)")
    reset_engine_v2()

    engine2 = get_engine_v2()
    ac2, src2 = engine2._classifier.classify_with_source("fake_test_tool")
    print(f"[step 6] classify(fake_test_tool) -> {ac2.value} via {src2.value}")

    if src2 == DecisionSource.EXPLICIT_HANDLER_ATTR:
        print("[PASS] explicit_lookup preserved across reset")
        reset_engine_v2()
        return 0
    else:
        print(f"[FAIL] explicit_lookup LOST after reset: source={src2.value}")
        reset_engine_v2()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

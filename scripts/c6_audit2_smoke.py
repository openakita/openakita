"""C6 二轮 audit smoke test：验证 reset_policy_engine 同步 v2 单例。

模拟用户在 UI 改 trust mode → 配置写回 YAML → API endpoint 调
``reset_policy_engine`` 后，下一次 v2 评估能立即读到新配置。

不写 YAML 文件（避免污染开发者环境），改为：
1. 先 stub v2 引擎为 ALLOW，测一次 → ALLOW
2. reset_policy_engine （等同 UI 修改后的同步动作）
3. 再 stub 引擎为 DENY，测一次 → DENY

第二步成功则证明 reset_policy_engine 把上一次的 stub 清掉了，否则会沿用第一个 stub。
"""
from __future__ import annotations


def run() -> int:
    from openakita.core.permission import check_permission
    from openakita.core.policy import reset_policy_engine
    from openakita.core.policy_v2 import (
        ApprovalClass,
        DecisionAction,
        PolicyDecisionV2,
        is_initialized,
        set_engine_v2,
    )

    failures: list[str] = []

    class _Stub:
        def __init__(self, action):
            self.action = action

        def evaluate_tool_call(self, event, ctx):
            return PolicyDecisionV2(
                action=self.action,
                reason="stub",
                approval_class=ApprovalClass.MUTATING_SCOPED,
            )

    # 1. 注入 ALLOW 引擎，验证 v2 路径走通
    set_engine_v2(_Stub(DecisionAction.ALLOW))  # type: ignore[arg-type]
    assert is_initialized(), "v2 should be initialized after set_engine_v2"
    r1 = check_permission("write_file", {"path": "/tmp/x.txt"})
    if r1.behavior != "allow":
        failures.append(f"step1: expected allow, got {r1.behavior}")
    else:
        print(f"[smoke] step1 ALLOW stub → behavior={r1.behavior}  OK")

    # 2. 模拟 UI 改 yaml + reset_policy_engine
    reset_policy_engine()
    if is_initialized():
        failures.append(
            "step2: v2 singleton not cleared after reset_policy_engine "
            "→ UI hot-reload broken (P1 trust-mode bug regression)"
        )
    else:
        print("[smoke] step2 reset_policy_engine cleared v2 singleton  OK")

    # 3. 注入 DENY 引擎，verify next call uses NEW engine
    set_engine_v2(_Stub(DecisionAction.DENY))  # type: ignore[arg-type]
    r2 = check_permission("write_file", {"path": "/tmp/x.txt"})
    if r2.behavior != "deny":
        failures.append(
            f"step3: expected deny (new stub), got {r2.behavior} "
            "→ v2 still serving old engine"
        )
    else:
        print(f"[smoke] step3 DENY stub after reset → behavior={r2.behavior}  OK")

    if failures:
        print("\n[FAIL] SMOKE FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n[OK] smoke passed -- reset_policy_engine fully syncs v1+v2")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())

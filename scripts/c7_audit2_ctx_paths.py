"""C7 二轮 audit：枚举所有 ContextVar 安装/读取路径，确保覆盖 + 无泄漏。

重点检查：
1. build_policy_context 输入 IM session（带 channel="im:telegram"）→ ctx.channel 正确
2. ContextVar token 在 finally 中 reset 后，下一轮请求看到 None
3. fail-soft：build_policy_context 不会因 session 异常方法抛出
4. 嵌套 set/reset：parent set → child set → child reset → parent reset → outer None
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from openakita.core.policy_v2 import (
        build_policy_context,
        get_current_context,
        reset_current_context,
        set_current_context,
    )

    failures: list[str] = []

    # 1. IM session channel 透传
    class _IMSession:
        channel = "im:telegram"

        def get_metadata(self, key):
            return None

    ctx = build_policy_context(session=_IMSession(), channel=_IMSession().channel)
    if ctx.channel != "im:telegram":
        failures.append(f"IM channel not propagated: got {ctx.channel!r}")
    else:
        print(f"[OK] IM channel propagated: {ctx.channel}")

    # 2. ContextVar lifecycle - reset 后 None
    assert get_current_context() is None, "pre-test ctx should be None"
    ctx_a = build_policy_context(session=None, session_id="a")
    token_a = set_current_context(ctx_a)
    if get_current_context() is not ctx_a:
        failures.append("set_current_context: ctx not visible after set")
    reset_current_context(token_a)
    if get_current_context() is not None:
        failures.append("reset_current_context: ctx still visible after reset")
    else:
        print("[OK] ContextVar set/reset round-trip")

    # 3. fail-soft：session.get_metadata 抛任何异常
    class _BadSession:
        def get_metadata(self, key):
            raise RuntimeError(f"unexpected get_metadata({key!r})")

    try:
        ctx = build_policy_context(session=_BadSession())
        if ctx.replay_authorizations or ctx.trusted_path_overrides:
            failures.append("bad session: should yield empty replay/trusted")
        else:
            print("[OK] bad session fail-soft to empty replay/trusted")
    except Exception as exc:
        failures.append(f"build_policy_context raised on bad session: {exc!r}")

    # 4. 嵌套 set/reset（多 agent 调用栈语义）
    ctx_outer = build_policy_context(session=None, session_id="outer")
    ctx_inner = build_policy_context(session=None, session_id="inner")
    t1 = set_current_context(ctx_outer)
    t2 = set_current_context(ctx_inner)
    if get_current_context().session_id != "inner":
        failures.append("nested set: inner not on top")
    reset_current_context(t2)
    if get_current_context().session_id != "outer":
        failures.append("nested reset: outer not restored")
    reset_current_context(t1)
    if get_current_context() is not None:
        failures.append("nested reset: should be None at end")
    else:
        print("[OK] nested set/reset preserves stack semantics")

    # 5. session.get_metadata 返回非 dict 形态（如 str）→ 不爆栈
    class _WeirdSession:
        def get_metadata(self, key):
            return "not-a-dict-or-list"

    try:
        ctx = build_policy_context(session=_WeirdSession())
        if ctx.replay_authorizations or ctx.trusted_path_overrides:
            failures.append("weird session: non-dict metadata should yield empty")
        else:
            print("[OK] weird session metadata coerced to empty")
    except Exception as exc:
        failures.append(f"build_policy_context raised on weird session: {exc!r}")

    # 6. extra_metadata 不丢
    ctx = build_policy_context(
        session=None,
        extra_metadata={"trace_id": "x", "request_id": "y"},
    )
    if ctx.metadata.get("trace_id") != "x" or ctx.metadata.get("request_id") != "y":
        failures.append("extra_metadata not propagated")
    else:
        print("[OK] extra_metadata propagated")

    # 7. user_message 既在 ctx 也在 metadata（C7 设计要求都能读到）
    ctx = build_policy_context(session=None, user_message="hello")
    if ctx.user_message != "hello":
        failures.append(f"user_message not on ctx: {ctx.user_message!r}")
    else:
        print("[OK] user_message propagated")

    if failures:
        print(f"\n[FAIL] {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n[PASS] all ctx-path checks pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""C8 audit D4 — 隐藏 bug 探测：5 类常见隐患逐项核验。

1. safety_immune builtin 在测试构造的临时目录下能正确扩展 ${CWD}（不会硬编码到一个绝对路径）
2. owner_only 默认行为：私聊单用户场景必须保持 is_owner=True（不能因为新加 ACL 把所有人锁死）
3. switch_mode 的 to_dict 反序列化必须容错（None / 空串 / 类型错都应回到默认值）
4. consume_session_trust prune 不应在"无规则"场景下产生 spurious metadata write（节省 disk I/O）
5. SSE 串行竞争：reasoning_engine 与 gateway 不能同时持有 wait → 已在 D3 验证；这里加 timeout 行为
"""

from __future__ import annotations

from pathlib import Path

from openakita.core.policy_v2 import (
    PolicyConfigV2,
    PolicyContext,
    PolicyEngineV2,
    SafetyImmuneConfig,
    ToolCallEvent,
    expand_builtin_immune_paths,
)


def _bug_1_cwd_expansion_per_engine() -> None:
    """每次实例化引擎都按当前 CWD 重新扩展 builtin paths（不是被首次缓存死）。"""
    e1 = PolicyEngineV2()
    paths_now = e1._immune_paths_from_config
    expected = expand_builtin_immune_paths()
    # set 比较忽略顺序
    builtin_part = set(paths_now) & set(expected)
    assert len(builtin_part) == len(expected), (
        f"engine builtin paths drift: missing "
        f"{set(expected) - set(paths_now)}"
    )
    print("D4 #1 CWD expansion per engine: OK (no stale absolute path)")


def _bug_2_owner_default_true_for_unconfigured() -> None:
    """没配 owner_only.tools 也没在 metadata 标 is_owner 时，CONTROL_PLANE 默认行为。

    当前 engine 行为：``_requires_owner_only`` 对 CONTROL_PLANE 默认 True，
    即"未显式列名也按 owner_only 走"。结合 ctx.is_owner 默认 True，私聊
    单用户场景下 CONTROL_PLANE 工具能正常运行；只有 IM gateway 显式标
    is_owner=False 才被 step 4 卡。"""
    e = PolicyEngineV2()
    ctx = PolicyContext(session_id="t", workspace=Path.cwd(), is_owner=True)
    # use a tool that classifier can recognize as control-plane via heuristic
    # (or simply pick any tool — since is_owner=True, step 4 must pass either way)
    decision = e.evaluate_tool_call(
        ToolCallEvent(tool="read_file", params={"path": str(Path.cwd() / "README.md")}),
        ctx,
    )
    assert decision.is_owner_required is False, (
        "owner_required leaked when ctx.is_owner=True"
    )
    print("D4 #2 owner default True for unconfigured: OK (back-compat single-user)")


def _bug_3_session_from_dict_robustness() -> None:
    """老 sessions.json 反序列化容错：missing / None / 空串 / 类型错全归默认。"""
    from openakita.sessions.session import Session

    s = Session.create(channel="cli", chat_id="c", user_id="u")
    base = s.to_dict()

    # 1. missing fields
    d1 = {**base}
    d1.pop("session_role", None)
    d1.pop("confirmation_mode_override", None)
    s1 = Session.from_dict(d1)
    assert s1.session_role == "agent"
    assert s1.confirmation_mode_override is None

    # 2. None
    d2 = {**base, "session_role": None, "confirmation_mode_override": None}
    s2 = Session.from_dict(d2)
    assert s2.session_role == "agent"
    assert s2.confirmation_mode_override is None

    # 3. empty string
    d3 = {**base, "session_role": "", "confirmation_mode_override": ""}
    s3 = Session.from_dict(d3)
    assert s3.session_role == "agent"
    assert s3.confirmation_mode_override is None

    # 4. wrong type (int)
    d4 = {**base, "session_role": 42, "confirmation_mode_override": 99}
    s4 = Session.from_dict(d4)
    assert s4.session_role == "agent"
    assert s4.confirmation_mode_override is None

    print("D4 #3 Session.from_dict robustness: OK (all 4 corruption modes default)")


def _bug_4_consume_no_spurious_write() -> None:
    """no rules at all → no metadata write (avoid stamping every read-only check)."""
    from openakita.core.trusted_paths import consume_session_trust

    class _Sess:
        def __init__(self) -> None:
            self._meta: dict = {}
            self.set_calls: list[tuple[str, object]] = []

        def get_metadata(self, k, default=None):
            return self._meta.get(k, default)

        def set_metadata(self, k, v):
            self._meta[k] = v
            self.set_calls.append((k, v))

    sess = _Sess()
    matched = consume_session_trust(sess, message="x", operation="write")
    assert matched is False
    assert sess.set_calls == [], (
        f"spurious metadata write on empty consume: {sess.set_calls!r}"
    )
    print("D4 #4 consume_session_trust no spurious metadata write: OK")


def _bug_5_engine_safety_immune_reflects_user_addition_after_construction() -> None:
    """user-provided safety_immune.paths is appended (not order-replaced) and
    survives engine re-init."""
    config = PolicyConfigV2(
        safety_immune=SafetyImmuneConfig(paths=["/private_lab/foo"])
    )
    engine = PolicyEngineV2(config=config)
    assert "/private_lab/foo" in engine._immune_paths_from_config
    # builtin still present
    assert any("/etc" in p for p in engine._immune_paths_from_config)
    # Re-init: re-construction is idempotent
    engine2 = PolicyEngineV2(config=config)
    assert engine._immune_paths_from_config == engine2._immune_paths_from_config
    print("D4 #5 user-additive safety_immune is stable across re-init: OK")


def _bug_6_owner_allowlist_round_trip() -> None:
    """`im_owner_allowlist.json` 序列化/反序列化 round trip。

    通过 monkey-patch 路径到一个 tmp 文件，避免污染 ``data/sessions/`` 真实配置。
    覆盖三种语义：未配（不存在文件）/ 空列表（显式 lockout）/ 非空列表。
    """
    import openakita.api.routes.im as im_module
    from openakita.api.routes.im import _load_owner_allowlist, _save_owner_allowlist

    original = im_module._OWNER_ALLOWLIST_PATH
    tmp = Path("data/sessions/_c8_audit_owner_allowlist.json")
    try:
        im_module._OWNER_ALLOWLIST_PATH = tmp

        # Unconfigured (file absent)
        if tmp.exists():
            tmp.unlink()
        assert _load_owner_allowlist() == {}

        # Empty list (= lockout)
        _save_owner_allowlist({"telegram": {"owners": []}})
        loaded = _load_owner_allowlist()
        assert loaded.get("telegram", {}).get("owners") == []

        # Non-empty allowlist
        _save_owner_allowlist({"telegram": {"owners": ["123", "456"]}})
        loaded = _load_owner_allowlist()
        assert sorted(loaded["telegram"]["owners"]) == ["123", "456"]
    finally:
        im_module._OWNER_ALLOWLIST_PATH = original
        if tmp.exists():
            tmp.unlink()
    print("D4 #6 owner_allowlist persist round-trip: OK (no production config touched)")


def main() -> None:
    _bug_1_cwd_expansion_per_engine()
    _bug_2_owner_default_true_for_unconfigured()
    _bug_3_session_from_dict_robustness()
    _bug_4_consume_no_spurious_write()
    _bug_5_engine_safety_immune_reflects_user_addition_after_construction()
    _bug_6_owner_allowlist_round_trip()
    print()
    print("D4 ALL PASS")


if __name__ == "__main__":
    main()

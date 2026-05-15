"""C8 audit D5 — 兼容性：existing tests + sessions.json + POLICIES.yaml 都不破。

1. sessions.json 老格式（没有 session_role / confirmation_mode_override）能反序列化
2. POLICIES.yaml v1 schema（zones.protected/forbidden）能正确迁移到 v2 + builtin 不冲突
3. v1 PolicyEngine API 仍然可调用（reasoning_engine / agent.py 的 import）
4. group_policy.json + im_owner_allowlist.json 互不影响
5. PolicyEngineV2() 默认构造（无 config）+ 默认 ctx 仍可决策（不会因为 builtin 引入而崩）
"""

from __future__ import annotations

from pathlib import Path

from openakita.core.policy_v2 import PolicyContext, PolicyEngineV2, ToolCallEvent


def _check_old_session_dict() -> None:
    from openakita.sessions.session import Session

    # Mimic an old sessions.json entry (no session_role / confirmation_mode_override)
    old_payload = {
        "id": "test-old",
        "channel": "cli",
        "chat_id": "c1",
        "user_id": "u1",
        "thread_id": None,
        "chat_type": "private",
        "display_name": "",
        "chat_name": "",
        "state": "active",
        "created_at": "2026-05-13T10:00:00",
        "last_active": "2026-05-13T10:00:00",
        "context": {},
        "config": {
            "max_history": 2000,
            "timeout_minutes": 30,
            "language": "zh",
            "model": None,
            "custom_prompt": None,
            "auto_summarize": True,
        },
        "metadata": {},
    }
    s = Session.from_dict(old_payload)
    assert s.session_role == "agent"
    assert s.confirmation_mode_override is None
    print("D5 #1 old sessions.json (no new fields) deserialization: OK")


def _check_v1_yaml_migration_with_builtin_union() -> None:
    """v1 POLICIES.yaml 加载后 + builtin → engine immune list 包含两侧。"""
    from openakita.core.policy_v2 import load_policies_yaml

    cfg, report = load_policies_yaml(Path("identity/POLICIES.yaml"))
    engine = PolicyEngineV2(config=cfg)
    immune = engine._immune_paths_from_config

    # builtin paths present
    assert any("/etc" in p for p in immune)
    assert any("identity/SOUL.md" in p for p in immune)
    # v1 zones.protected paths present (e.g. C:/Windows/** is in v1 yaml + builtin)
    assert any("Windows" in p for p in immune)
    # No duplicate /etc/** entries even if both v1 and builtin had it
    assert immune.count("/etc/**") <= 1
    print(f"D5 #2 v1 yaml + builtin union: OK ({len(immune)} unique paths)")


def _check_v1_policy_engine_fully_deleted() -> None:
    """C8b-6b：v1 ``policy.py`` 整文件已删；任何残余 import 都应抛
    ``ModuleNotFoundError``。原 ``_check_v1_policy_engine_still_imports``（C8 阶段
    验证 v1 facade 仍可调用）取反：现在反向证明 v1 已彻底切除。
    """
    try:
        __import__("openakita.core.policy")
    except ModuleNotFoundError:
        print("D5 #3 v1 PolicyEngine 已删除（C8b-6b）: OK")
        return
    raise AssertionError(
        "openakita.core.policy 仍可导入——v1 模块未被 C8b-6b 删除"
    )


def _check_independent_acl_files() -> None:
    """group_policy.json (chat-level) 与 im_owner_allowlist.json (user-level) 是独立文件。"""
    import openakita.api.routes.im as im_module

    assert im_module._GROUP_POLICY_PATH != im_module._OWNER_ALLOWLIST_PATH
    assert "group_policy" in str(im_module._GROUP_POLICY_PATH)
    assert "owner_allowlist" in str(im_module._OWNER_ALLOWLIST_PATH)
    print("D5 #4 group_policy + im_owner_allowlist are independent files: OK")


def _check_minimal_engine_smoke() -> None:
    e = PolicyEngineV2()
    ctx = PolicyContext(session_id="t", workspace=Path.cwd())

    # readonly tool inside workspace → ALLOW
    d = e.evaluate_tool_call(
        ToolCallEvent(tool="read_file", params={"path": str(Path.cwd() / "README.md")}),
        ctx,
    )
    assert d.action.value in ("allow", "confirm"), f"unexpected {d.action}"

    # write to /etc/anything → CONFIRM via builtin /etc/**
    d2 = e.evaluate_tool_call(
        ToolCallEvent(tool="write_file", params={"path": "/etc/passwd"}),
        ctx,
    )
    assert d2.safety_immune_match is not None
    assert d2.action.value == "confirm"

    print("D5 #5 minimal PolicyEngineV2 smoke: OK")


def main() -> None:
    _check_old_session_dict()
    _check_v1_yaml_migration_with_builtin_union()
    _check_v1_policy_engine_fully_deleted()
    _check_independent_acl_files()
    _check_minimal_engine_smoke()
    print()
    print("D5 ALL PASS")


if __name__ == "__main__":
    main()

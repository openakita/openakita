"""C8b-3 — SessionAllowlistManager + step 9 wiring tests.

覆盖：
1. ``SessionAllowlistManager`` CRUD（add/clear/is_allowed/snapshot）
2. ``_make_key`` 与 v1 ``_confirm_cache_key`` 完全等价（hash 不变）
3. ``PolicyEngineV2._check_user_allowlist`` step 9 三层顺序：
   persistent → session → skill
4. session entry 命中后 needs_sandbox 透传
5. clear() 清空全部（不论 session_id —— v1 等价）
6. process-wide singleton：dry-run preview engine 看不到主 engine 的 session
   allow（preview 用 isolated engine + 不调 apply_resolution）
"""

from __future__ import annotations

import hashlib

from openakita.core.policy_v2 import (
    ApprovalClass,
    ConfirmationMode,
    DecisionAction,
    PolicyConfigV2,
    PolicyContext,
    PolicyEngineV2,
    SessionAllowlistManager,
    SessionRole,
    ToolCallEvent,
    get_session_allowlist_manager,
)
from openakita.core.policy_v2.engine import build_engine_from_config
from openakita.core.policy_v2.enums import DecisionSource
from openakita.core.policy_v2.session_allowlist import _make_key


class TestKeyParityWithV1:
    """``_make_key`` 必须与 v1 ``_confirm_cache_key`` 字节级等价。"""

    @staticmethod
    def _v1_key(tool_name: str, params: dict) -> str:
        param_str = (
            f"{tool_name}:"
            f"{params.get('command', '') if params else ''}"
            f"{params.get('path', '') if params else ''}"
        )
        return hashlib.md5(param_str.encode()).hexdigest()

    def test_command_param_keying(self) -> None:
        params = {"command": "npm install lodash"}
        assert _make_key("run_shell", params) == self._v1_key("run_shell", params)

    def test_path_param_keying(self) -> None:
        params = {"path": "/tmp/foo.txt"}
        assert _make_key("write_file", params) == self._v1_key("write_file", params)

    def test_empty_params_keying(self) -> None:
        assert _make_key("ls", {}) == self._v1_key("ls", {})

    def test_unrelated_params_ignored(self) -> None:
        # v1 only looks at command/path; other fields are ignored
        a = _make_key("write_file", {"path": "/x", "extra": "ignored1"})
        b = _make_key("write_file", {"path": "/x", "extra": "ignored2"})
        assert a == b


class TestSessionAllowlistManager:
    def test_add_then_is_allowed(self) -> None:
        mgr = SessionAllowlistManager()
        mgr.add("run_shell", {"command": "ls"}, needs_sandbox=False)
        entry = mgr.is_allowed("run_shell", {"command": "ls"})
        assert entry is not None
        assert entry["needs_sandbox"] is False
        assert entry["tool_name"] == "run_shell"

    def test_needs_sandbox_preserved(self) -> None:
        mgr = SessionAllowlistManager()
        mgr.add("run_shell", {"command": "rm"}, needs_sandbox=True)
        entry = mgr.is_allowed("run_shell", {"command": "rm"})
        assert entry is not None
        assert entry["needs_sandbox"] is True

    def test_clear_wipes_all(self) -> None:
        mgr = SessionAllowlistManager()
        mgr.add("a", {"command": "x"})
        mgr.add("b", {"command": "y"})
        assert len(mgr) == 2
        mgr.clear()
        assert len(mgr) == 0
        assert mgr.is_allowed("a", {"command": "x"}) is None

    def test_different_params_different_entries(self) -> None:
        mgr = SessionAllowlistManager()
        mgr.add("run_shell", {"command": "npm test"})
        assert mgr.is_allowed("run_shell", {"command": "npm test"}) is not None
        assert mgr.is_allowed("run_shell", {"command": "npm build"}) is None

    def test_empty_tool_name_is_noop(self) -> None:
        mgr = SessionAllowlistManager()
        mgr.add("", {"command": "x"})
        assert len(mgr) == 0
        assert mgr.is_allowed("", {"command": "x"}) is None

    def test_snapshot_returns_deep_copy(self) -> None:
        mgr = SessionAllowlistManager()
        mgr.add("run_shell", {"command": "ls"}, needs_sandbox=True)
        snap = mgr.snapshot()
        # mutating snapshot doesn't affect manager
        for entry in snap.values():
            entry["needs_sandbox"] = False
        re_check = mgr.is_allowed("run_shell", {"command": "ls"})
        assert re_check["needs_sandbox"] is True


class TestStep9SessionAllowlistWire:
    """step 9 三层（persistent → session → skill）顺序与 ALLOW relax 行为。"""

    @staticmethod
    def _build_engine() -> PolicyEngineV2:
        cfg = PolicyConfigV2()
        return build_engine_from_config(
            cfg,
            explicit_lookup=lambda _name: (
                ApprovalClass.MUTATING_SCOPED,
                DecisionSource.EXPLICIT_HANDLER_ATTR,
            ),
        )

    @staticmethod
    def _make_ctx() -> PolicyContext:
        from pathlib import Path

        return PolicyContext(
            session_id="c8b3-test",
            workspace=Path.cwd(),
            session_role=SessionRole.AGENT,
            confirmation_mode=ConfirmationMode.DEFAULT,
        )

    def test_session_allow_overrides_confirm(self) -> None:
        get_session_allowlist_manager().clear()
        engine = self._build_engine()
        # base for MUTATING_SCOPED + AGENT/DEFAULT is CONFIRM
        ev = ToolCallEvent(tool="write_file", params={"path": "/tmp/test.txt"})
        decision = engine.evaluate_tool_call(ev, self._make_ctx())
        assert decision.action == DecisionAction.CONFIRM

        # add to session allowlist → next call is ALLOW
        get_session_allowlist_manager().add(
            "write_file", {"path": "/tmp/test.txt"}, needs_sandbox=False
        )
        decision2 = engine.evaluate_tool_call(ev, self._make_ctx())
        assert decision2.action == DecisionAction.ALLOW
        last_step = decision2.chain[-1]
        assert last_step.name == "user_allowlist"
        assert "session_allowlist" in (last_step.note or "")

    def test_session_allow_needs_sandbox_propagates_to_meta(self) -> None:
        get_session_allowlist_manager().clear()
        engine = self._build_engine()
        get_session_allowlist_manager().add(
            "write_file", {"path": "/tmp/sb.txt"}, needs_sandbox=True
        )
        ev = ToolCallEvent(tool="write_file", params={"path": "/tmp/sb.txt"})
        decision = engine.evaluate_tool_call(ev, self._make_ctx())
        assert decision.action == DecisionAction.ALLOW
        # The relax reason includes needs_sandbox=True
        last_step = decision.chain[-1]
        assert "needs_sandbox=True" in (last_step.note or "")

    def test_skill_still_works_when_session_misses(self) -> None:
        from openakita.core.policy_v2 import get_skill_allowlist_manager

        get_session_allowlist_manager().clear()
        get_skill_allowlist_manager().clear()
        get_skill_allowlist_manager().add("test_skill", ["write_file"])
        engine = self._build_engine()
        ev = ToolCallEvent(tool="write_file", params={"path": "/tmp/x.txt"})
        decision = engine.evaluate_tool_call(ev, self._make_ctx())
        assert decision.action == DecisionAction.ALLOW
        last_step = decision.chain[-1]
        assert "skill_allowlist" in (last_step.note or "")

    def test_session_takes_precedence_over_skill(self) -> None:
        """Two managers both grant — session is checked first (Tier 2 < Tier 3)."""
        from openakita.core.policy_v2 import get_skill_allowlist_manager

        get_session_allowlist_manager().clear()
        get_skill_allowlist_manager().clear()
        get_session_allowlist_manager().add("write_file", {"path": "/x"}, needs_sandbox=True)
        get_skill_allowlist_manager().add("test_skill", ["write_file"])
        engine = self._build_engine()
        ev = ToolCallEvent(tool="write_file", params={"path": "/x"})
        decision = engine.evaluate_tool_call(ev, self._make_ctx())
        assert decision.action == DecisionAction.ALLOW
        last_step = decision.chain[-1]
        # session check is logged with needs_sandbox; skill check would say skill_allowlist
        assert "session_allowlist" in (last_step.note or "")


class TestSingletonIsolation:
    def test_get_returns_same_instance(self) -> None:
        a = get_session_allowlist_manager()
        b = get_session_allowlist_manager()
        assert a is b

    def test_clear_via_singleton_visible_to_engine(self) -> None:
        get_session_allowlist_manager().add("run_shell", {"command": "x"})
        assert get_session_allowlist_manager().is_allowed("run_shell", {"command": "x"}) is not None
        get_session_allowlist_manager().clear()
        assert get_session_allowlist_manager().is_allowed("run_shell", {"command": "x"}) is None

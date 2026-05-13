"""C8b-1 v2 补能：UserAllowlist / SkillAllowlist / DeathSwitch / step 9-10 wire。

覆盖目标：
- 3 个新 manager 的全部公开方法 + 边界
- PolicyEngineV2 step 9 (_check_user_allowlist) 与 v1 _check_allowlists 行为对等
- PolicyEngineV2 step 10 (_check_death_switch) 在 readonly 模式下正确 DENY
- record_decision 计数语义 + 触发逻辑
- broadcast hook 解耦 + fail-safe
- count_in_death_switch flag 给 dry-run preview 用
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from openakita.core.policy_v2 import (
    ApprovalClass,
    ConfirmationMode,
    DeathSwitchTracker,
    DecisionAction,
    PolicyConfigV2,
    PolicyContext,
    PolicyEngineV2,
    SessionRole,
    SkillAllowlistManager,
    ToolCallEvent,
    UserAllowlistConfig,
    UserAllowlistManager,
    command_to_pattern,
    get_death_switch_tracker,
    get_skill_allowlist_manager,
)
from openakita.core.policy_v2.classifier import ApprovalClassifier
from openakita.core.policy_v2.enums import DecisionSource

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def cfg() -> PolicyConfigV2:
    return PolicyConfigV2()


@pytest.fixture
def engine(cfg: PolicyConfigV2) -> PolicyEngineV2:
    """Engine with default config + classifier that returns CONTROL_PLANE for
    'sensitive_tool' (so we can deterministically reach matrix CONFIRM)."""

    def _lookup(name: str) -> tuple[ApprovalClass, DecisionSource] | None:
        if name == "sensitive_tool":
            return ApprovalClass.MUTATING_SCOPED, DecisionSource.EXPLICIT_HANDLER_ATTR
        if name == "deny_me":
            return ApprovalClass.DESTRUCTIVE, DecisionSource.EXPLICIT_HANDLER_ATTR
        return None

    clf = ApprovalClassifier(explicit_lookup=_lookup, shell_risk_config=cfg.shell_risk)
    return PolicyEngineV2(classifier=clf, config=cfg)


@pytest.fixture
def ctx() -> PolicyContext:
    return PolicyContext(
        session_id="t",
        workspace=Path.cwd(),
        session_role=SessionRole.AGENT,
        confirmation_mode=ConfirmationMode.DEFAULT,
        is_owner=True,
    )


# =============================================================================
# command_to_pattern parity
# =============================================================================


class TestCommandToPattern:
    @pytest.mark.parametrize(
        "command, expected",
        [
            ("npm install react", "npm install*"),
            ("ls", "ls*"),
            ("", ""),
            ('"C:/Python/python.exe" -m pip install foo', "pip install*"),
            ("python -m pytest", "pytest*"),
            ("/usr/bin/python3.11 -m pip", "pip*"),
            ("git status", "git status*"),
        ],
    )
    def test_known_patterns(self, command: str, expected: str) -> None:
        assert command_to_pattern(command) == expected

    def test_whitespace_stripped(self) -> None:
        assert command_to_pattern("   npm install   ") == "npm install*"


# =============================================================================
# UserAllowlistManager
# =============================================================================


class TestUserAllowlistManager:
    def test_match_command_pattern(self, cfg: PolicyConfigV2) -> None:
        cfg.user_allowlist = UserAllowlistConfig(
            commands=[{"pattern": "npm install*", "needs_sandbox": False}]
        )
        m = UserAllowlistManager(cfg)
        entry = m.match("run_shell", {"command": "npm install react"})
        assert entry is not None
        assert entry["pattern"] == "npm install*"

    def test_match_command_semantic_normalization(self, cfg: PolicyConfigV2) -> None:
        cfg.user_allowlist = UserAllowlistConfig(
            commands=[{"pattern": "pip install*"}]
        )
        m = UserAllowlistManager(cfg)
        entry = m.match(
            "run_shell",
            {"command": '"C:/Python/python.exe" -m pip install requests'},
        )
        assert entry is not None  # semantic match via _command_to_pattern

    def test_match_tool_by_name(self, cfg: PolicyConfigV2) -> None:
        cfg.user_allowlist = UserAllowlistConfig(
            tools=[{"name": "write_file", "zone": "workspace"}]
        )
        m = UserAllowlistManager(cfg)
        assert m.match("write_file", {"path": "/x"}) is not None

    def test_match_no_hit_returns_none(self, cfg: PolicyConfigV2) -> None:
        m = UserAllowlistManager(cfg)
        assert m.match("write_file", {"path": "/x"}) is None
        assert m.match("run_shell", {"command": "ls"}) is None

    def test_match_empty_pattern_skipped(self, cfg: PolicyConfigV2) -> None:
        cfg.user_allowlist = UserAllowlistConfig(
            commands=[{"pattern": "", "needs_sandbox": False}]
        )
        m = UserAllowlistManager(cfg)
        assert m.match("run_shell", {"command": "ls"}) is None

    def test_match_command_field_missing(self, cfg: PolicyConfigV2) -> None:
        cfg.user_allowlist = UserAllowlistConfig(
            commands=[{"pattern": "npm*"}]
        )
        m = UserAllowlistManager(cfg)
        # No command in params → falls through to tools branch (also empty).
        assert m.match("run_shell", {}) is None

    def test_add_entry_command_persists_pattern(self, cfg: PolicyConfigV2) -> None:
        m = UserAllowlistManager(cfg)
        entry = m.add_entry("run_shell", {"command": "git push"}, needs_sandbox=False)
        assert entry["pattern"] == "git push*"
        assert entry["needs_sandbox"] is False
        assert "added_at" in entry
        assert m.match("run_shell", {"command": "git push origin"}) is not None

    def test_add_entry_tool_persists_name(self, cfg: PolicyConfigV2) -> None:
        m = UserAllowlistManager(cfg)
        entry = m.add_entry("write_file", {"path": "/x"})
        assert entry["name"] == "write_file"
        assert entry["zone"] == "workspace"
        assert m.match("write_file", {"path": "/y"}) is not None

    def test_add_raw_entry_no_field_mutation(self, cfg: PolicyConfigV2) -> None:
        m = UserAllowlistManager(cfg)
        entry = m.add_raw_entry(
            "command", {"pattern": "custom*", "added_at": "2025-01-01"}
        )
        # added_at preserved (not overridden with current time)
        assert entry["added_at"] == "2025-01-01"
        assert m.match("run_shell", {"command": "custom thing"}) is not None

    def test_remove_entry(self, cfg: PolicyConfigV2) -> None:
        m = UserAllowlistManager(cfg)
        m.add_entry("write_file", {"path": "/x"})
        assert m.remove_entry("tool", 0) is True
        assert m.snapshot()["tools"] == []
        # bad index
        assert m.remove_entry("tool", 99) is False
        assert m.remove_entry("command", -1) is False

    def test_snapshot_is_copy(self, cfg: PolicyConfigV2) -> None:
        m = UserAllowlistManager(cfg)
        m.add_entry("write_file", {"path": "/x"})
        snap = m.snapshot()
        snap["tools"].clear()  # mutate the snapshot
        # internal still intact
        assert len(m.snapshot()["tools"]) == 1

    def test_save_to_yaml_round_trip(
        self, cfg: PolicyConfigV2, tmp_path: Path
    ) -> None:
        yaml_path = tmp_path / "POLICIES.yaml"
        yaml_path.write_text("security: {}\n", encoding="utf-8")
        m = UserAllowlistManager(cfg)
        m.add_entry("run_shell", {"command": "git status"})
        ok = m.save_to_yaml(yaml_path)
        assert ok is True
        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert loaded["security"]["user_allowlist"]["commands"][0]["pattern"] == "git status*"

    def test_save_to_yaml_missing_file_returns_false(
        self, cfg: PolicyConfigV2, tmp_path: Path
    ) -> None:
        m = UserAllowlistManager(cfg)
        m.add_entry("write_file", {"path": "/x"})
        assert m.save_to_yaml(tmp_path / "does-not-exist.yaml") is False

    def test_save_to_yaml_silent_on_unreadable(
        self, cfg: PolicyConfigV2, tmp_path: Path
    ) -> None:
        # Pass a directory as YAML path → open() raises → swallowed → False
        m = UserAllowlistManager(cfg)
        result = m.save_to_yaml(tmp_path)
        assert result is False  # didn't raise

    def test_replace_config(self, cfg: PolicyConfigV2) -> None:
        m = UserAllowlistManager(cfg)
        new_ua = UserAllowlistConfig(commands=[{"pattern": "after_replace*"}])
        m.replace_config(new_ua)
        assert m.match("run_shell", {"command": "after_replace foo"}) is not None


# =============================================================================
# SkillAllowlistManager
# =============================================================================


class TestSkillAllowlistManager:
    def test_add_and_check(self) -> None:
        m = SkillAllowlistManager()
        m.add("skill_a", ["tool_x", "tool_y"])
        assert m.is_allowed("tool_x") is True
        assert m.is_allowed("tool_y") is True
        assert m.is_allowed("tool_z") is False

    def test_add_empty_list_noop(self) -> None:
        m = SkillAllowlistManager()
        m.add("skill_a", [])
        assert m.is_allowed("anything") is False

    def test_add_filters_empty_strings(self) -> None:
        m = SkillAllowlistManager()
        m.add("skill_a", ["tool_x", "", None])  # type: ignore[list-item]
        assert m.is_allowed("tool_x") is True

    def test_add_empty_skill_id_noop(self) -> None:
        m = SkillAllowlistManager()
        m.add("", ["tool_x"])
        assert m.is_allowed("tool_x") is False

    def test_remove(self) -> None:
        m = SkillAllowlistManager()
        m.add("skill_a", ["tool_x"])
        assert m.remove("skill_a") is True
        assert m.is_allowed("tool_x") is False
        assert m.remove("skill_a") is False  # idempotent miss

    def test_clear(self) -> None:
        m = SkillAllowlistManager()
        m.add("skill_a", ["tool_x"])
        m.add("skill_b", ["tool_y"])
        m.clear()
        assert m.is_allowed("tool_x") is False
        assert m.is_allowed("tool_y") is False

    def test_granted_by(self) -> None:
        m = SkillAllowlistManager()
        m.add("skill_a", ["shared_tool"])
        m.add("skill_b", ["shared_tool", "private_tool"])
        assert sorted(m.granted_by("shared_tool")) == ["skill_a", "skill_b"]
        assert m.granted_by("private_tool") == ["skill_b"]
        assert m.granted_by("unknown") == []

    def test_snapshot_sorted_lists(self) -> None:
        m = SkillAllowlistManager()
        m.add("skill_a", ["b", "a", "c"])
        snap = m.snapshot()
        assert snap == {"skill_a": ["a", "b", "c"]}

    def test_singleton_returns_same_instance(self) -> None:
        a = get_skill_allowlist_manager()
        b = get_skill_allowlist_manager()
        assert a is b

    def test_is_allowed_empty_tool_name(self) -> None:
        m = SkillAllowlistManager()
        m.add("skill_a", ["tool_x"])
        assert m.is_allowed("") is False


# =============================================================================
# DeathSwitchTracker
# =============================================================================


class TestDeathSwitchTracker:
    def test_initial_state_clean(self) -> None:
        t = DeathSwitchTracker()
        assert t.is_readonly_mode() is False
        assert t.stats() == {
            "consecutive_denials": 0,
            "total_denials": 0,
            "readonly_mode": False,
        }

    def test_consecutive_threshold_triggers(self) -> None:
        t = DeathSwitchTracker()
        # 2 denies → no trigger
        for _ in range(2):
            triggered = t.record_decision(
                action="deny", tool_name="foo", threshold=3, total_multiplier=3
            )
            assert triggered is False
        # 3rd deny → triggers
        triggered = t.record_decision(
            action="deny", tool_name="foo", threshold=3, total_multiplier=3
        )
        assert triggered is True
        assert t.is_readonly_mode() is True

    def test_allow_resets_consecutive(self) -> None:
        t = DeathSwitchTracker()
        t.record_decision(action="deny", tool_name="foo", threshold=3)
        t.record_decision(action="deny", tool_name="foo", threshold=3)
        # write_file ALLOW resets consecutive
        t.record_decision(action="allow", tool_name="write_file", threshold=3)
        assert t.stats()["consecutive_denials"] == 0
        assert t.stats()["total_denials"] == 2  # total NOT reset

    def test_read_tool_allow_does_not_reset(self) -> None:
        t = DeathSwitchTracker()
        t.record_decision(action="deny", tool_name="foo", threshold=3)
        t.record_decision(action="allow", tool_name="read_file", threshold=3)
        # consecutive NOT reset (read tools are non-resetting)
        assert t.stats()["consecutive_denials"] == 1

    def test_total_threshold_triggers_when_consecutive_short(self) -> None:
        t = DeathSwitchTracker()
        # threshold=3, multiplier=2 → total_threshold=6
        # interleave deny with read-allow so consecutive never hits 3
        for _ in range(6):
            t.record_decision(action="deny", tool_name="foo", threshold=3, total_multiplier=2)
            t.record_decision(action="allow", tool_name="read_file", threshold=3, total_multiplier=2)
        assert t.is_readonly_mode() is True
        assert t.stats()["total_denials"] == 6

    def test_disabled_no_op(self) -> None:
        t = DeathSwitchTracker()
        for _ in range(10):
            t.record_decision(action="deny", tool_name="foo", enabled=False, threshold=3)
        assert t.is_readonly_mode() is False
        assert t.stats()["total_denials"] == 0

    def test_reset_clears_consecutive_and_readonly_only(self) -> None:
        t = DeathSwitchTracker()
        for _ in range(3):
            t.record_decision(action="deny", tool_name="foo", threshold=3)
        assert t.is_readonly_mode() is True
        assert t.stats()["total_denials"] == 3
        t.reset()
        # consecutive + readonly cleared; total preserved (parity with v1)
        assert t.is_readonly_mode() is False
        assert t.stats()["consecutive_denials"] == 0
        assert t.stats()["total_denials"] == 3

    def test_broadcast_hook_invoked_on_trigger(self) -> None:
        t = DeathSwitchTracker()
        events: list[dict[str, Any]] = []
        t.set_broadcast_hook(lambda payload: events.append(payload))
        for _ in range(3):
            t.record_decision(action="deny", tool_name="foo", threshold=3)
        assert len(events) == 1
        assert events[0]["active"] is True
        assert events[0]["consecutive"] == 3

    def test_broadcast_hook_invoked_on_reset(self) -> None:
        t = DeathSwitchTracker()
        events: list[dict[str, Any]] = []
        t.set_broadcast_hook(lambda payload: events.append(payload))
        for _ in range(3):
            t.record_decision(action="deny", tool_name="foo", threshold=3)
        events.clear()
        t.reset()
        assert events == [{"active": False}]

    def test_broadcast_hook_exception_swallowed(self) -> None:
        t = DeathSwitchTracker()

        def bad_hook(_p: dict[str, Any]) -> None:
            raise RuntimeError("boom")

        t.set_broadcast_hook(bad_hook)
        # Should NOT raise; trigger still happens
        for _ in range(3):
            t.record_decision(action="deny", tool_name="foo", threshold=3)
        assert t.is_readonly_mode() is True

    def test_already_readonly_does_not_re_trigger(self) -> None:
        t = DeathSwitchTracker()
        events: list[dict[str, Any]] = []
        t.set_broadcast_hook(lambda payload: events.append(payload))
        for _ in range(3):
            t.record_decision(action="deny", tool_name="foo", threshold=3)
        # Now readonly. More denies don't re-broadcast.
        for _ in range(3):
            triggered = t.record_decision(action="deny", tool_name="foo", threshold=3)
            assert triggered is False
        assert len(events) == 1  # only initial trigger

    def test_singleton_returns_same_instance(self) -> None:
        a = get_death_switch_tracker()
        b = get_death_switch_tracker()
        assert a is b


# =============================================================================
# Engine wire: step 9 (_check_user_allowlist)
# =============================================================================


class TestEngineStep9UserAllowlist:
    def test_persistent_command_allowlist_relaxes_confirm(
        self, engine: PolicyEngineV2, ctx: PolicyContext
    ) -> None:
        engine.user_allowlist.add_entry("run_shell", {"command": "git status"})
        # run_shell with risky-ish command would normally CONFIRM under DEFAULT mode;
        # allowlist should relax to ALLOW.
        d = engine.evaluate_tool_call(
            ToolCallEvent(tool="run_shell", params={"command": "git status -sb"}),
            ctx,
        )
        assert d.action == DecisionAction.ALLOW
        assert "persistent_allowlist" in d.reason

    def test_persistent_tool_allowlist_relaxes_confirm(
        self, engine: PolicyEngineV2, ctx: PolicyContext
    ) -> None:
        engine.user_allowlist.add_entry("sensitive_tool", {})
        d = engine.evaluate_tool_call(
            ToolCallEvent(tool="sensitive_tool", params={}), ctx
        )
        assert d.action == DecisionAction.ALLOW
        assert "persistent_allowlist" in d.reason

    def test_skill_allowlist_relaxes_confirm(
        self, engine: PolicyEngineV2, ctx: PolicyContext
    ) -> None:
        get_skill_allowlist_manager().add("skill_x", ["sensitive_tool"])
        d = engine.evaluate_tool_call(
            ToolCallEvent(tool="sensitive_tool", params={}), ctx
        )
        assert d.action == DecisionAction.ALLOW
        assert "skill_allowlist" in d.reason
        assert "skill_x" in d.reason

    def test_no_allowlist_falls_through_to_confirm(
        self, engine: PolicyEngineV2, ctx: PolicyContext
    ) -> None:
        d = engine.evaluate_tool_call(
            ToolCallEvent(tool="sensitive_tool", params={}), ctx
        )
        assert d.action == DecisionAction.CONFIRM

    def test_user_allowlist_does_not_bypass_matrix_deny(
        self, engine: PolicyEngineV2, ctx: PolicyContext
    ) -> None:
        # ASK mode + DESTRUCTIVE class → matrix DENY (step 6 short-circuit)
        ask_ctx = PolicyContext(
            session_id="t",
            workspace=Path.cwd(),
            session_role=SessionRole.ASK,
            confirmation_mode=ConfirmationMode.DEFAULT,
            is_owner=True,
        )
        engine.user_allowlist.add_entry("deny_me", {})
        d = engine.evaluate_tool_call(
            ToolCallEvent(tool="deny_me", params={}), ask_ctx
        )
        # ASK + DESTRUCTIVE = DENY before user_allowlist even runs
        assert d.action == DecisionAction.DENY


# =============================================================================
# Engine wire: step 10 (_check_death_switch)
# =============================================================================


class TestEngineStep10DeathSwitch:
    def test_normal_evaluation_when_not_readonly(
        self, engine: PolicyEngineV2, ctx: PolicyContext
    ) -> None:
        d = engine.evaluate_tool_call(
            ToolCallEvent(tool="sensitive_tool", params={}), ctx
        )
        assert d.action == DecisionAction.CONFIRM  # not DENY

    def test_readonly_denies_mutating(
        self, engine: PolicyEngineV2, ctx: PolicyContext
    ) -> None:
        get_death_switch_tracker().reset()  # extra-safety; autouse already does
        # Manually trigger readonly via 3 denies (use deny_me tool)
        # ASK mode + DESTRUCTIVE → matrix DENY (3 denies trigger threshold=3)
        ask_ctx = PolicyContext(
            session_id="t",
            workspace=Path.cwd(),
            session_role=SessionRole.ASK,
            confirmation_mode=ConfirmationMode.DEFAULT,
            is_owner=True,
        )
        for _ in range(3):
            engine.evaluate_tool_call(
                ToolCallEvent(tool="deny_me", params={}), ask_ctx
            )
        assert get_death_switch_tracker().is_readonly_mode() is True

        # Now sensitive_tool (CONFIRM normally) should DENY due to readonly
        d = engine.evaluate_tool_call(
            ToolCallEvent(tool="sensitive_tool", params={}), ctx
        )
        assert d.action == DecisionAction.DENY
        assert "death_switch" in d.reason

    def test_readonly_allows_read_tools(
        self, engine: PolicyEngineV2, ctx: PolicyContext
    ) -> None:
        get_death_switch_tracker().reset()
        ask_ctx = PolicyContext(
            session_id="t",
            workspace=Path.cwd(),
            session_role=SessionRole.ASK,
            confirmation_mode=ConfirmationMode.DEFAULT,
            is_owner=True,
        )
        for _ in range(3):
            engine.evaluate_tool_call(
                ToolCallEvent(tool="deny_me", params={}), ask_ctx
            )
        # read_file is READONLY_GLOBAL/SCOPED → should still ALLOW
        d = engine.evaluate_tool_call(
            ToolCallEvent(tool="read_file", params={"path": "README.md"}), ctx
        )
        assert d.action != DecisionAction.DENY  # ALLOW or CONFIRM, not DENY

    def test_disabled_does_not_trigger(
        self, engine: PolicyEngineV2, ctx: PolicyContext
    ) -> None:
        engine._config.death_switch.enabled = False
        ask_ctx = PolicyContext(
            session_id="t",
            workspace=Path.cwd(),
            session_role=SessionRole.ASK,
            confirmation_mode=ConfirmationMode.DEFAULT,
            is_owner=True,
        )
        for _ in range(10):
            engine.evaluate_tool_call(
                ToolCallEvent(tool="deny_me", params={}), ask_ctx
            )
        assert get_death_switch_tracker().is_readonly_mode() is False

    def test_count_in_death_switch_false_skips_counting(
        self, engine: PolicyEngineV2, ctx: PolicyContext
    ) -> None:
        engine.count_in_death_switch = False
        ask_ctx = PolicyContext(
            session_id="t",
            workspace=Path.cwd(),
            session_role=SessionRole.ASK,
            confirmation_mode=ConfirmationMode.DEFAULT,
            is_owner=True,
        )
        for _ in range(10):
            engine.evaluate_tool_call(
                ToolCallEvent(tool="deny_me", params={}), ask_ctx
            )
        # Tracker untouched
        assert get_death_switch_tracker().is_readonly_mode() is False
        assert get_death_switch_tracker().stats()["total_denials"] == 0


# =============================================================================
# C8b-1 D1 audit: 新代码不破坏 v1 行为
# =============================================================================


class TestC8b1Compat:
    def test_engine_user_allowlist_property_returns_manager(
        self, engine: PolicyEngineV2
    ) -> None:
        assert isinstance(engine.user_allowlist, UserAllowlistManager)

    def test_engine_count_in_death_switch_default_true(
        self, engine: PolicyEngineV2
    ) -> None:
        assert engine.count_in_death_switch is True

    def test_two_engines_share_skill_singleton_but_different_user_managers(
        self, cfg: PolicyConfigV2
    ) -> None:
        e1 = PolicyEngineV2(config=cfg)
        e2 = PolicyEngineV2(config=PolicyConfigV2())
        # User allowlist is engine-scoped → different manager
        assert e1.user_allowlist is not e2.user_allowlist
        # Skill manager is process-wide singleton
        assert get_skill_allowlist_manager() is get_skill_allowlist_manager()


# =============================================================================
# C8b-1 P1 regression: dry-run preview must not pollute global tracker
# =============================================================================


class TestC8b1PreviewIsolation:
    def test_make_preview_engine_disables_counting(self, ctx: PolicyContext) -> None:
        from openakita.core.policy_v2 import (
            DecisionAction,
            ToolCallEvent,
            make_preview_engine,
        )

        # Simulate dry-run: 5 DENY samples on a fresh preview engine
        ask_ctx = PolicyContext(
            session_id="preview",
            workspace=Path.cwd(),
            session_role=SessionRole.ASK,
            confirmation_mode=ConfirmationMode.DEFAULT,
            is_owner=True,
        )
        # Configure classifier so deny_me triggers DENY in ASK + DESTRUCTIVE
        cfg = PolicyConfigV2()

        def _lookup(name: str):
            if name == "deny_me":
                return ApprovalClass.DESTRUCTIVE, DecisionSource.EXPLICIT_HANDLER_ATTR
            return None

        clf = ApprovalClassifier(
            explicit_lookup=_lookup, shell_risk_config=cfg.shell_risk
        )
        # Manually inject the cfg + classifier
        prev = make_preview_engine(cfg)
        prev._classifier = clf
        for _ in range(5):
            d = prev.evaluate_tool_call(
                ToolCallEvent(tool="deny_me", params={}), ask_ctx
            )
            assert d.action == DecisionAction.DENY
        # Global tracker untouched
        assert get_death_switch_tracker().is_readonly_mode() is False
        assert get_death_switch_tracker().stats()["total_denials"] == 0

    def test_make_preview_engine_separate_user_allowlist(self) -> None:
        from openakita.core.policy_v2 import make_preview_engine

        prev = make_preview_engine()
        prev.user_allowlist.add_entry("write_file", {"path": "/preview-only"})
        # Global engine should not see this addition (preview cfg is a deep copy)
        from openakita.core.policy_v2 import get_config_v2

        global_cfg = get_config_v2()
        names = [e.get("name") for e in global_cfg.user_allowlist.tools]
        assert "write_file" not in names or all(
            e.get("path") != "/preview-only" for e in global_cfg.user_allowlist.tools
        )

"""Fix-11 回归测试：信任路径白名单 + 会话级授权 + risk gate 集成。"""

from __future__ import annotations

import time

import pytest

from openakita.core.agent import _check_trust_mode_skip, _check_trusted_path_skip
from openakita.core.risk_intent import (
    AccessMode,
    OperationKind,
    RiskIntentResult,
    RiskLevel,
    TargetKind,
)
from openakita.core.trusted_paths import (
    SESSION_KEY,
    consume_session_trust,
    grant_session_trust,
    is_trusted_workspace_path,
)

# ---------------------------------------------------------------------------
# Stub session — minimal API used by trusted_paths helpers.
# ---------------------------------------------------------------------------


class _StubSession:
    def __init__(self, initial: dict | None = None):
        self._meta: dict = dict(initial or {})

    def get_metadata(self, key: str):
        return self._meta.get(key)

    def set_metadata(self, key: str, value):
        if value is None:
            self._meta.pop(key, None)
        else:
            self._meta[key] = value


def _risk(
    level: RiskLevel = RiskLevel.MEDIUM,
    op: OperationKind = OperationKind.DELETE,
    target: TargetKind = TargetKind.UNKNOWN,
) -> RiskIntentResult:
    return RiskIntentResult(
        risk_level=level,
        operation_kind=op,
        target_kind=target,
        access_mode=AccessMode.WRITE,
        requires_confirmation=level in {RiskLevel.MEDIUM, RiskLevel.HIGH},
        reason="test",
    )


# ---------------------------------------------------------------------------
# Built-in trusted-path detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "msg",
    [
        "请把 qa_test_2026_05_02/plan.md 删掉",
        "删除 workspaces/ws-1/scratch/foo.txt",
        "把 /tmp/abc.log 移除",
        "删掉 workspaces/main/playground/x",
        "请删除 workspace/temp/foo.md",
    ],
)
def test_is_trusted_workspace_path_true(msg: str):
    assert is_trusted_workspace_path(msg)


@pytest.mark.parametrize(
    "msg",
    [
        "请把 identity/SOUL.md 删掉",
        "删除 data/security/users.yaml",
        "rm -rf /etc/passwd",
        "",
        "随便聊聊天气",
    ],
)
def test_is_trusted_workspace_path_false(msg: str):
    assert not is_trusted_workspace_path(msg)


def test_protected_path_overrides_trusted_match():
    """即使消息里同时出现可信子串，命中 protected 也必须返回 False。"""
    msg = "把 workspaces/main/scratch/foo 删掉，然后 rm -rf /etc/passwd"
    assert not is_trusted_workspace_path(msg)


# ---------------------------------------------------------------------------
# Session-level grant + consume
# ---------------------------------------------------------------------------


def test_grant_and_consume_round_trip():
    s = _StubSession()
    grant_session_trust(s, operation="delete")
    assert consume_session_trust(s, message="删除任意东西", operation="delete") is True


def test_consume_without_grant_returns_false():
    assert consume_session_trust(_StubSession(), message="x", operation="delete") is False


def test_grant_scoped_to_operation():
    s = _StubSession()
    grant_session_trust(s, operation="delete")
    assert consume_session_trust(s, message="x", operation="write") is False
    assert consume_session_trust(s, message="x", operation="delete") is True


def test_grant_with_path_pattern():
    s = _StubSession()
    grant_session_trust(s, operation=None, path_pattern=r"qa_test_\d+")
    assert consume_session_trust(s, message="改 qa_test_001/plan.md", operation="write") is True
    assert consume_session_trust(s, message="改 identity/SOUL.md", operation="write") is False


def test_grant_with_expiry_in_past_is_ignored():
    s = _StubSession()
    grant_session_trust(s, operation="delete", expires_at=time.time() - 60)
    assert consume_session_trust(s, message="x", operation="delete") is False


def test_grant_with_expiry_in_future_is_active():
    s = _StubSession()
    grant_session_trust(s, operation="delete", expires_at=time.time() + 600)
    assert consume_session_trust(s, message="x", operation="delete") is True


def test_grant_persists_in_metadata():
    s = _StubSession()
    grant_session_trust(s, operation="delete")
    assert SESSION_KEY in s._meta
    assert s._meta[SESSION_KEY]["rules"][0]["operation"] == "delete"


def test_consume_does_not_mutate_grant():
    """sticky grant — 同一规则可被消费多次。"""
    s = _StubSession()
    grant_session_trust(s, operation="delete")
    consume_session_trust(s, message="x", operation="delete")
    consume_session_trust(s, message="y", operation="delete")
    assert len(s._meta[SESSION_KEY]["rules"]) == 1


# ---------------------------------------------------------------------------
# _check_trusted_path_skip — agent.py 集成入口
# ---------------------------------------------------------------------------


def test_check_skip_when_trusted_path_in_message():
    s = _StubSession()
    reason = _check_trusted_path_skip(
        s,
        "请把 qa_test_2026_05_02/plan.md 删掉",
        _risk(RiskLevel.MEDIUM, OperationKind.DELETE),
    )
    assert reason == "trusted_workspace_path"


def test_check_skip_when_session_grant_matches_op():
    s = _StubSession()
    grant_session_trust(s, operation="delete")
    reason = _check_trusted_path_skip(
        s,
        "请删除 some_file.txt",
        _risk(RiskLevel.MEDIUM, OperationKind.DELETE),
    )
    assert reason == "session_grant"


def test_check_skip_high_risk_never_demoted():
    """HIGH 风险（如 rm -rf）即便用户授权过 delete 也不能被信任路径短路。"""
    s = _StubSession()
    grant_session_trust(s, operation="delete")
    reason = _check_trusted_path_skip(
        s,
        "rm -rf /etc/passwd",
        _risk(RiskLevel.HIGH, OperationKind.DELETE, TargetKind.SHELL_COMMAND),
    )
    assert reason is None


def test_check_skip_no_match_returns_none():
    s = _StubSession()
    reason = _check_trusted_path_skip(
        s,
        "请删除 src/openakita/some_file.py",
        _risk(RiskLevel.MEDIUM, OperationKind.DELETE),
    )
    assert reason is None


def test_check_skip_with_no_risk_intent_returns_none():
    assert _check_trusted_path_skip(_StubSession(), "qa_test_2026", None) is None


def test_check_skip_with_empty_message_returns_none():
    assert _check_trusted_path_skip(_StubSession(), "", _risk()) is None


def test_check_skip_protected_path_in_trusted_message_falls_through():
    """混合消息：trusted 命中但同时含 protected → 不应短路。"""
    s = _StubSession()
    msg = "请把 qa_test_2026_05_02/plan.md 删掉，再 rm -rf /etc/passwd"
    reason = _check_trusted_path_skip(s, msg, _risk(RiskLevel.MEDIUM, OperationKind.DELETE))
    assert reason is None


# ---------------------------------------------------------------------------
# _check_trust_mode_skip — 信任模式下放行普通文件操作
# ---------------------------------------------------------------------------


@pytest.fixture
def _trust_mode_engine():
    """临时把 v2 layer 切到 TRUST（即 v1 yolo）模式。

    C8b-6b：v1 ``policy.py`` 已删；``_check_trust_mode_skip`` 自 C8b-5 起直读
    v2 ``ConfirmationMode``，本 fixture 改 mutate v2 layer。
    """
    from openakita.core.policy_v2 import (
        PolicyConfigV2,
        build_engine_from_config,
    )
    from openakita.core.policy_v2.enums import ConfirmationMode
    from openakita.core.policy_v2.global_engine import (
        reset_engine_v2,
        set_engine_v2,
    )
    from openakita.core.policy_v2.schema import ConfirmationConfig

    cfg = PolicyConfigV2(confirmation=ConfirmationConfig(mode=ConfirmationMode.TRUST))
    engine = build_engine_from_config(cfg)
    set_engine_v2(engine, cfg)
    yield engine
    reset_engine_v2()


def test_trust_mode_skips_overwrite_of_ordinary_file(_trust_mode_engine):
    risk = _risk(RiskLevel.HIGH, OperationKind.OVERWRITE, TargetKind.FILE_SYSTEM)
    assert _check_trust_mode_skip(risk) == "trust_mode"


def test_trust_mode_skips_unknown_target_write(_trust_mode_engine):
    risk = _risk(RiskLevel.MEDIUM, OperationKind.WRITE, TargetKind.UNKNOWN)
    assert _check_trust_mode_skip(risk) == "trust_mode"


def test_trust_mode_keeps_confirm_for_shell_command(_trust_mode_engine):
    risk = _risk(RiskLevel.HIGH, OperationKind.EXECUTE, TargetKind.SHELL_COMMAND)
    assert _check_trust_mode_skip(risk) is None


def test_trust_mode_keeps_confirm_for_security_policy(_trust_mode_engine):
    risk = _risk(RiskLevel.HIGH, OperationKind.OVERWRITE, TargetKind.SECURITY_POLICY)
    assert _check_trust_mode_skip(risk) is None


def test_trust_mode_keeps_confirm_for_protected_file(_trust_mode_engine):
    risk = _risk(RiskLevel.HIGH, OperationKind.DELETE, TargetKind.PROTECTED_FILE)
    assert _check_trust_mode_skip(risk) is None


def test_non_trust_mode_does_not_skip():
    """smart/cautious 模式下，trust mode skip 不会触发。

    C8b-5 后 ``_check_trust_mode_skip`` 直读 v2 ``ConfirmationMode``，v1 ``engine
    ._config.confirmation`` mutation 不再生效。本测试同步 mutate v2 layer 触发
    non-trust 状态；C8b-6b 删 v1 ``policy.py`` 后整个 test_trusted_paths.py
    会一并清理。
    """
    from openakita.core.policy_v2 import (
        PolicyConfigV2,
        build_engine_from_config,
    )
    from openakita.core.policy_v2.enums import ConfirmationMode
    from openakita.core.policy_v2.global_engine import (
        reset_engine_v2,
        set_engine_v2,
    )
    from openakita.core.policy_v2.schema import ConfirmationConfig

    cfg = PolicyConfigV2(
        confirmation=ConfirmationConfig(mode=ConfirmationMode.DEFAULT)
    )
    set_engine_v2(build_engine_from_config(cfg), cfg)
    try:
        risk = _risk(RiskLevel.HIGH, OperationKind.OVERWRITE, TargetKind.FILE_SYSTEM)
        assert _check_trust_mode_skip(risk) is None
    finally:
        reset_engine_v2()


def test_trust_mode_skip_returns_none_for_no_intent():
    assert _check_trust_mode_skip(None) is None

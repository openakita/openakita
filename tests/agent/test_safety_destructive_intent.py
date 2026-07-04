"""Tests for agent/safety/destructive_intent helpers (P-RC-6 P6.2c).

Pin the behaviour of the seven extracted helpers. We do NOT
re-derive the deep classifier semantics here -- the existing
tests/unit/test_destructive_intent_gate.py corpus already pins
those via the legacy aliases. This file adds direct-import
coverage for the v2 path so a future refactor that drops the
aliases (P-RC-7) cannot accidentally regress the v2 names.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from openakita.agent.safety import destructive_intent as di
from openakita.core.risk_intent import RiskIntentResult, RiskLevel, TargetKind


def _intent(*, target_kind=TargetKind.FILE_SYSTEM, level=RiskLevel.LOW):
    return RiskIntentResult(
        risk_level=level,
        target_kind=target_kind,
    )


def test_classify_risk_intent_delegates_to_deep_classifier() -> None:
    """The v2 classifier is a thin wrapper over risk_intent.classify_risk_intent."""
    out = di.classify_risk_intent(None, "")
    # Empty message + None intent yields a low-risk default.
    assert out is not None
    assert hasattr(out, "risk_level")


def test_trust_mode_skip_requires_intent() -> None:
    """check_trust_mode_skip returns None when no intent is provided."""
    assert di.check_trust_mode_skip(None) is None


def test_trust_mode_skip_never_releases_sensitive_targets() -> None:
    """Sensitive targets always force confirmation, even in trust mode."""
    for kind in di.TRUST_MODE_MUST_CONFIRM_TARGETS:
        intent = _intent(target_kind=kind)
        assert di.check_trust_mode_skip(intent) is None


def test_trusted_path_skip_blocks_high_risk() -> None:
    """A high-risk intent never gets the trusted-path skip."""
    intent = _intent(level=RiskLevel.HIGH)
    assert di.check_trusted_path_skip(None, "rm -rf /", intent) is None


def test_trusted_path_skip_requires_message() -> None:
    """An empty message bypasses the skip decision."""
    intent = _intent(level=RiskLevel.LOW)
    assert di.check_trusted_path_skip(None, "", intent) is None


def test_consume_risk_authorization_handles_none_session() -> None:
    """A None session short-circuits to False without raising."""
    assert di.consume_risk_authorization(None, "anything") is False


def test_consume_risk_authorization_consumes_matching_stamp() -> None:
    """A live stamp whose original_message matches gets consumed once."""
    session = MagicMock()
    session.get_metadata.side_effect = lambda key: {
        "risk_authorized_replay": {
            "expires_at": 9999999999,
            "original_message": "do thing",
            "confirmation_id": "abc",
        },
        "risk_authorized_intent": None,
    }.get(key)
    assert di.consume_risk_authorization(session, "do thing") is True
    session.set_metadata.assert_any_call("risk_authorized_replay", None)


def test_consume_risk_authorization_skips_mismatched_message() -> None:
    """A stamp for a different original_message stays put."""
    session = MagicMock()
    session.get_metadata.side_effect = lambda key: {
        "risk_authorized_replay": {
            "expires_at": 9999999999,
            "original_message": "do other thing",
        },
        "risk_authorized_intent": None,
    }.get(key)
    assert di.consume_risk_authorization(session, "do thing") is False


def test_build_destructive_intent_question_shape() -> None:
    """Three-option prompt includes summary + metadata + Chinese label."""
    text = di.build_destructive_intent_question("rm -rf /tmp/foo")
    assert "准备执行" in text  # "about to execute"
    assert "rm -rf /tmp/foo" in text
    assert "继续" in text  # "continue"
    assert "只查看" in text  # "look only"
    assert "取消" in text  # "cancel"


def test_build_destructive_intent_question_includes_metadata() -> None:
    """When a classification is supplied, op/target appear in the meta line."""
    op_kind = MagicMock()
    op_kind.value = "delete"
    target_kind = MagicMock()
    target_kind.value = "user_file"
    classification = MagicMock()
    classification.operation_kind = op_kind
    classification.target_kind = target_kind
    text = di.build_destructive_intent_question("clean up", classification)
    assert "op=delete" in text
    assert "target=user_file" in text


def test_summarize_destructive_action_short_passthrough() -> None:
    """Inputs <=30 chars are returned verbatim (trimmed)."""
    assert di.summarize_destructive_action("  short text  ") == "short text"


def test_summarize_destructive_action_long_input_uses_first_sentence() -> None:
    """Long inputs collapse to the first sentence boundary in the 5-30 char window."""
    long_text = "删除这个重要文件。不要问我为什么"
    out = di.summarize_destructive_action(long_text)
    assert out  # something returned
    assert "你" not in out  # only first sentence captured


def test_summarize_destructive_action_empty_falls_back() -> None:
    """Empty / None inputs return the Chinese 'no operation specified' default."""
    assert di.summarize_destructive_action("") == "未指定操作"
    assert di.summarize_destructive_action(None) == "未指定操作"


def test_package_exports_are_stable() -> None:
    """Pin the public __all__ surface so callers can rely on it."""
    from openakita.agent.safety import __all__ as pkg_all
    expected = {
        "DESTRUCTIVE_VERBS",
        "TRUST_MODE_MUST_CONFIRM_TARGETS",
        "build_destructive_intent_question",
        "check_trust_mode_skip",
        "check_trusted_path_skip",
        "classify_risk_intent",
        "consume_risk_authorization",
        "summarize_destructive_action",
    }
    assert set(pkg_all) == expected

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from openakita.inbox.models import ClientContext, InboxMessage
from openakita.inbox.targeting import is_in_rollout, match_target_rule, should_show_message


def context(**overrides: str | None) -> ClientContext:
    values = {
        "install_id_hash": "install-hash-1",
        "version": "1.25.0",
        "platform": "windows",
        "channel": "release",
    }
    values.update(overrides)
    return ClientContext(**values)


def test_target_rule_matches_platform_channel_version_and_install_id() -> None:
    rule = {
        "platforms": ["windows"],
        "channels": ["release"],
        "min_version": "1.20.0",
        "max_version": "1.30.0",
        "install_id_hashes": ["install-hash-1"],
    }

    assert match_target_rule(rule, context()) is True
    assert match_target_rule(rule, context(platform="linux")) is False
    assert match_target_rule(rule, context(channel="dev")) is False
    assert match_target_rule(rule, context(version="1.19.9")) is False
    assert match_target_rule(rule, context(install_id_hash="install-hash-2")) is False


def test_target_rule_exclusions_win() -> None:
    assert (
        match_target_rule(
            {"platforms": ["windows"], "exclude_platforms": ["windows"]},
            context(),
        )
        is False
    )
    assert (
        match_target_rule(
            {"exclude_install_id_hashes": ["install-hash-1"]},
            context(),
        )
        is False
    )


def test_rollout_boundaries() -> None:
    assert is_in_rollout("message-1", "install-hash-1", 0) is False
    assert is_in_rollout("message-1", "install-hash-1", 100) is True


def test_should_show_message_honors_publish_and_expire_window() -> None:
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    assert (
        should_show_message(
            InboxMessage(id="future", title="x", body_markdown="x", publish_at=future), context()
        )
        is False
    )
    assert (
        should_show_message(
            InboxMessage(id="expired", title="x", body_markdown="x", expire_at=past), context()
        )
        is False
    )
    assert (
        should_show_message(InboxMessage(id="active", title="x", body_markdown="x"), context())
        is True
    )

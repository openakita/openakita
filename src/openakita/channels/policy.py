"""
IM Access Control Policy Engine

Reference: openclaw-china-main packages/shared/src/policy/{dm-policy,group-policy}.ts

DM Policy (DmPolicy):
- open:      Anyone can start a private chat
- pairing:   Requires pairing-code verification before chatting
- allowlist: Only whitelisted users can private-chat

Group Policy (GroupPolicy):
- open:        Any group may be used (still subject to GroupResponseMode)
- allowlist:   Only whitelisted groups may be used
- disabled:    Group chat is fully disabled

Policy checks return a PolicyResult containing an allowed flag and an
optional rejection reason / hint message.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)


# ==================== DM Policy ====================


class DmPolicyType(StrEnum):
    OPEN = "open"
    PAIRING = "pairing"
    ALLOWLIST = "allowlist"


@dataclass
class PolicyResult:
    allowed: bool = True
    reason: str = ""
    hint_message: str = ""


@dataclass
class DmPolicyConfig:
    policy: DmPolicyType = DmPolicyType.OPEN
    allowlist: set[str] = field(default_factory=set)
    is_paired: Callable[[str], bool] | None = None
    pairing_hint: str = "Please send a pairing code to complete verification first."
    deny_hint: str = "You do not have permission to use this bot."


def check_dm_policy(user_id: str, config: DmPolicyConfig) -> PolicyResult:
    """Check DM (private chat) access policy."""
    if config.policy == DmPolicyType.OPEN:
        return PolicyResult(allowed=True)

    if config.policy == DmPolicyType.ALLOWLIST:
        if user_id in config.allowlist:
            return PolicyResult(allowed=True)
        return PolicyResult(
            allowed=False,
            reason="not_in_allowlist",
            hint_message=config.deny_hint,
        )

    if config.policy == DmPolicyType.PAIRING:
        if config.is_paired and config.is_paired(user_id):
            return PolicyResult(allowed=True)
        if user_id in config.allowlist:
            return PolicyResult(allowed=True)
        return PolicyResult(
            allowed=False,
            reason="not_paired",
            hint_message=config.pairing_hint,
        )

    logger.warning(f"Unknown DM policy type: {config.policy!r}, fail-close")
    return PolicyResult(allowed=False, reason="unknown_policy")


# ==================== Group Policy ====================


class GroupPolicyType(StrEnum):
    OPEN = "open"
    ALLOWLIST = "allowlist"
    DISABLED = "disabled"


@dataclass
class GroupPolicyConfig:
    policy: GroupPolicyType = GroupPolicyType.OPEN
    allowlist: set[str] = field(default_factory=set)
    deny_hint: str = ""


def check_group_policy(chat_id: str, config: GroupPolicyConfig) -> PolicyResult:
    """Check group chat access policy."""
    if config.policy == GroupPolicyType.DISABLED:
        return PolicyResult(
            allowed=False,
            reason="group_disabled",
            hint_message=config.deny_hint,
        )

    if config.policy == GroupPolicyType.ALLOWLIST:
        if chat_id in config.allowlist:
            return PolicyResult(allowed=True)
        return PolicyResult(
            allowed=False,
            reason="group_not_in_allowlist",
            hint_message=config.deny_hint,
        )

    if config.policy == GroupPolicyType.OPEN:
        return PolicyResult(allowed=True)

    logger.warning(f"Unknown group policy type: {config.policy!r}, fail-close")
    return PolicyResult(allowed=False, reason="unknown_policy")

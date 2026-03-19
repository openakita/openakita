"""
IM 访问控制策略引擎

参考 openclaw-china-main packages/shared/src/policy/{dm-policy,group-policy}.ts

DM 策略 (DmPolicy):
- open:      任何人都可以私聊
- pairing:   需要配对码验证后才可对话
- allowlist: 仅白名单用户可以私聊

群聊策略 (GroupPolicy):
- open:        任何群都可以使用（仍受 GroupResponseMode 控制）
- allowlist:   仅白名单群可以使用
- disabled:    完全禁用群聊

策略检查返回 PolicyResult，包含 allowed 标志和可选的拒绝原因/提示消息。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

logger = logging.getLogger(__name__)


# ==================== DM 策略 ====================


class DmPolicyType(str, Enum):
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
    pairing_hint: str = "请先发送配对码完成验证。"
    deny_hint: str = "您暂无权限使用此机器人。"


def check_dm_policy(user_id: str, config: DmPolicyConfig) -> PolicyResult:
    """检查 DM（私聊）访问策略。"""
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


# ==================== 群聊策略 ====================


class GroupPolicyType(str, Enum):
    OPEN = "open"
    ALLOWLIST = "allowlist"
    DISABLED = "disabled"


@dataclass
class GroupPolicyConfig:
    policy: GroupPolicyType = GroupPolicyType.OPEN
    allowlist: set[str] = field(default_factory=set)
    deny_hint: str = ""


def check_group_policy(chat_id: str, config: GroupPolicyConfig) -> PolicyResult:
    """检查群聊访问策略。"""
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

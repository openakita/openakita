"""Double-texting policy resolution (plan: conversation concurrency v1.28, S1.1).

定义同一 conversation_id 上重发新消息的处理策略，以及策略解析 helper。

策略优先级（在 caller / HTTP layer 解析）：
    HTTP header "X-OpenAkita-DoubleTexting"
    > settings.double_texting_per_channel[channel]
    > settings.double_texting_default

`ConversationLifecycleManager.start()` 只信任 caller 传入已解析的 policy，
保持 lifecycle 为纯 mechanism（不读 settings、不做 feature flag 降级）。
"""

from __future__ import annotations

import logging
from enum import StrEnum

logger = logging.getLogger(__name__)


class DoubleTextingPolicy(StrEnum):
    """Conversation 上重发新消息时的处理策略。

    - REJECT    — 旧任务在跑就 409 拒绝（不同 client 永远走这条）
    - QUEUE     — 排在旧任务后面串行执行
    - INTERRUPT — cancel 旧任务再开新流（需要 double_texting_allow_interrupt=True）
    - STEER     — 把新消息注入到正在跑的 turn，不打断旧任务也不超时
                  （需要 double_texting_allow_steer=True，desktop/cli 默认）
    """

    REJECT = "reject"
    QUEUE = "queue"
    INTERRUPT = "interrupt"
    STEER = "steer"


_VALID_POLICY_VALUES: frozenset[str] = frozenset(p.value for p in DoubleTextingPolicy)


def _coerce_policy(raw: str | None, *, default: DoubleTextingPolicy) -> DoubleTextingPolicy:
    if raw is None:
        return default
    raw_lower = raw.strip().lower()
    if not raw_lower:
        return default
    if raw_lower not in _VALID_POLICY_VALUES:
        logger.warning("[DoubleTexting] Unknown policy %r; falling back to %s", raw, default.value)
        return default
    return DoubleTextingPolicy(raw_lower)


def resolve_policy(
    *,
    channel: str | None = None,
    header_value: str | None = None,
) -> DoubleTextingPolicy:
    """根据 header / channel / 全局默认解析最终 policy。

    且应用 feature flag 降级：
    - 若 `double_texting_allow_interrupt=False` 时把 INTERRUPT 降为 QUEUE，
      避免在 S4（v1.28.2）之前真的 cancel 正在跑的 shell/browser 工具。
    - 若 `double_texting_allow_steer=False` 时把 STEER 降为 QUEUE，作为紧急
      开关回退到 “排队等待旧任务结束” 的旧行为。

    Args:
        channel: 调用 channel 名（feishu/desktop/cli/...），用于查 per_channel 表。
        header_value: HTTP header X-OpenAkita-DoubleTexting 原始字符串。
    """
    from openakita.config import settings

    default_policy = _coerce_policy(
        settings.double_texting_default, default=DoubleTextingPolicy.QUEUE
    )

    if header_value is not None:
        policy = _coerce_policy(header_value, default=default_policy)
    else:
        channel_raw = None
        if channel:
            channel_raw = settings.double_texting_per_channel.get(channel)
        policy = _coerce_policy(channel_raw, default=default_policy)

    if policy is DoubleTextingPolicy.INTERRUPT and not getattr(
        settings, "double_texting_allow_interrupt", False
    ):
        logger.debug(
            "[DoubleTexting] INTERRUPT requested (channel=%s) but feature flag off; "
            "downgrading to QUEUE",
            channel,
        )
        policy = DoubleTextingPolicy.QUEUE

    if policy is DoubleTextingPolicy.STEER and not getattr(
        settings, "double_texting_allow_steer", True
    ):
        logger.debug(
            "[DoubleTexting] STEER requested (channel=%s) but feature flag off; "
            "downgrading to QUEUE",
            channel,
        )
        policy = DoubleTextingPolicy.QUEUE

    return policy


__all__ = ["DoubleTextingPolicy", "resolve_policy"]

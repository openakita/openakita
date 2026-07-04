"""Pending confirmation state for high-risk user intents.

``ConfirmationDecision`` and ``normalize_confirmation_answer`` live here
because :mod:`openakita.core.confirmation_state` imports them while building
the hybrid RiskGate store (PR #694). The store singleton and record types
are re-exported from the core module so agent write paths and chat/RiskGate
read paths share one in-memory store.
"""

from __future__ import annotations

from enum import Enum


class ConfirmationDecision(str, Enum):  # noqa: UP042 — preserved as-is from the legacy module so str+Enum vs StrEnum behaviour differences (e.g. ``repr`` / ``format``) cannot leak into existing callers; cleanup belongs in a separate refactor commit, not in this byte-faithful MOVE.
    CONFIRM = "confirm_continue"
    INSPECT_ONLY = "inspect_only"
    CANCEL = "cancel"
    UNKNOWN = "unknown"


_CONFIRM_WORDS = {
    "confirm_continue", "确认继续", "继续", "确认", "继续吧", "继续执行",
    "好", "好的", "好滴", "好啊", "嗯", "嗯嗯",
    "是", "是的", "对", "对的",
    "行", "可以", "中", "ok", "okay", "yes", "y", "go", "gogogo",
    "同意", "批准", "通过", "执行", "开始", "开始吧", "做",
}
_INSPECT_WORDS = {
    "inspect_only", "只查看", "仅查看", "查看", "看看",
    "read_only", "inspect", "只读",
}
_CANCEL_WORDS = {
    "cancel", "取消", "停止", "停", "否", "不", "不要", "不用",
    "no", "n", "nope", "abort", "skip", "跳过", "算了",
}


def normalize_confirmation_answer(answer: str) -> ConfirmationDecision:
    normalized = (answer or "").strip().lower()
    if normalized in _CONFIRM_WORDS:
        return ConfirmationDecision.CONFIRM
    if normalized in _INSPECT_WORDS:
        return ConfirmationDecision.INSPECT_ONLY
    if normalized in _CANCEL_WORDS:
        return ConfirmationDecision.CANCEL
    return ConfirmationDecision.UNKNOWN


from openakita.core.confirmation_state import (  # noqa: E402
    PendingRiskConfirmation,
    PendingRiskConfirmationStore,
    get_confirmation_store,
)

__all__ = [
    "ConfirmationDecision",
    "PendingRiskConfirmation",
    "PendingRiskConfirmationStore",
    "get_confirmation_store",
    "normalize_confirmation_answer",
]

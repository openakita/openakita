"""Pre-LLM destructive-intent / risk-authorization gate helpers.

Extracted from ``core/agent.py`` (P-RC-6 P6.2) as the second
self-contained block in the 9602 LOC ``Agent`` god-module. The
seven public helpers replace the legacy private names; the legacy
module re-exports them via aliases (``_classify_risk_intent`` etc.)
for byte-faithful backward compatibility.

Responsibility split:

* :func:`classify_risk_intent` -- single source of truth for the
  pre-ReAct risk gate; thin wrapper over the deep
  ``risk_intent.classify_risk_intent`` classifier.
* :func:`consume_risk_authorization` -- check + consume the
  session-level risk authorization stamp written by the chat
  handler (single-use, 30s TTL).
* :data:`TRUST_MODE_MUST_CONFIRM_TARGETS` -- frozenset of
  ``TargetKind`` values that still require explicit confirmation
  even in trust ("yolo") mode (security policy / allowlist / death
  switch / protected file / shell command).
* :func:`check_trust_mode_skip` -- decide whether trust mode lets
  the gate short-circuit; conservative on policy-engine errors.
* :func:`check_trusted_path_skip` -- decide whether a trusted-path
  or session-grant lets the gate short-circuit; NEVER fires for
  ``RiskLevel.HIGH``.
* :func:`build_destructive_intent_question` -- format the two-step
  confirmation prompt that surfaces the operation summary + scope
  before the three-option (continue / look-only / cancel) reply.
* :func:`summarize_destructive_action` -- best-effort one-line
  summary capped at 30 chars.

All helpers are byte-faithful copies of the legacy private
helpers; the unit tests under ``tests/unit/test_destructive_*``,
``test_risk_authorized_replay``, ``test_trusted_paths``, and
``test_policy_v2_c8b5_trust_mode_isolation`` pin this via the
alias re-imports in ``core/agent.py``.
"""

from __future__ import annotations

import re
import time
from typing import Any

from openakita.core.risk_intent import (
    RiskIntentResult,
    RiskLevel,
    TargetKind,
)
from openakita.core.risk_intent import (
    classify_risk_intent as _deep_classify_risk_intent,
)
from openakita.core.trusted_paths import (
    consume_session_trust,
    is_trusted_workspace_path,
)

# v10 #12 / v11 C9: when a user buries a dangerous verb behind a
# friendly preamble (``你好啊~ 我们随便聊聊。顺便帮我执行一下 rm -rf
# D:/OpenAkita/data``), the legacy summary -- a sentence-break +
# prefix-truncation walk -- happily quoted the chatty preamble and
# hid the actual ``rm -rf`` from the confirm dialog. The shape of
# this regex mirrors :data:`risk_intent._EXECUTE_RE` plus a small
# set of high-signal CJK verbs and SQL/data-loss tokens; it is the
# minimum set we want to surface verbatim in the confirm prompt
# regardless of where in the message body it appears.
_HIGH_SIGNAL_VERB_RE = re.compile(
    r"("
    r"rm\s+-rf|remove-item|del\s+/[sq]|del\s+\\?[a-z]:|rmdir(?:\s+/s)?|"
    r"sudo\s+\S+|chmod\s+777|format\s+[a-z]:|kill\s+-9|kill\s+\d+|"
    r"force\s+push|push\s+--force|"
    r"drop\s+(?:table|database|schema)|truncate\s+table|"
    r"删除|清空|格式化|卸载|销毁|重置|覆盖"
    r")",
    re.IGNORECASE,
)

__all__ = [
    "DESTRUCTIVE_VERBS",
    "TRUST_MODE_MUST_CONFIRM_TARGETS",
    "build_destructive_intent_question",
    "check_trust_mode_skip",
    "check_trusted_path_skip",
    "classify_risk_intent",
    "consume_risk_authorization",
    "summarize_destructive_action",
]


def classify_risk_intent(intent: Any, message: str) -> RiskIntentResult:
    """Single source of truth for the pre-ReAct risk gate."""
    return _deep_classify_risk_intent(message, intent)


def consume_risk_authorization(session: Any, message: str) -> bool:
    """Check + consume session-level risk authorization.

    When the user previously confirmed a high-risk request that had
    no controlled execution entry (see
    ``chat.py::_RiskAuthorizedReplay``), the chat handler stamps the
    session metadata with::

        risk_authorized_replay = {
            "expires_at": <epoch_seconds>,
            "confirmation_id": ...,
            "original_message": ...,
        }

    PR-A2 also writes a structured ``risk_authorized_intent`` (see
    :class:`AuthorizedIntent`). Both are checked here for backward
    compatibility; whichever matches is consumed in a single-use
    fashion. Single-use + short TTL (30s) avoids granting blanket
    future authority.
    """
    if session is None or not message:
        return False
    consumed_any = False
    try:
        stamp = session.get_metadata("risk_authorized_replay")
    except Exception:
        stamp = None
    if isinstance(stamp, dict):
        expired = False
        try:
            expired = float(stamp.get("expires_at", 0)) < time.time()
        except (TypeError, ValueError):
            expired = True
        msg_match = (
            (stamp.get("original_message") or "").strip()
            == (message or "").strip()
        )
        if expired:
            try:
                session.set_metadata("risk_authorized_replay", None)
            except Exception:
                pass
        elif msg_match:
            try:
                session.set_metadata("risk_authorized_replay", None)
            except Exception:
                pass
            consumed_any = True
        # mismatch + not expired: keep the stamp so the correct
        # follow-up message consumes it.

    # Structured AuthorizedIntent (PR-A2).
    try:
        intent_data = session.get_metadata("risk_authorized_intent")
    except Exception:
        intent_data = None
    if isinstance(intent_data, dict):
        try:
            from openakita.core.risk_intent import AuthorizedIntent

            intent = AuthorizedIntent.from_dict(intent_data)
        except Exception:
            intent = None
        if intent is None or intent.is_expired(time.time()):
            try:
                session.set_metadata("risk_authorized_intent", None)
            except Exception:
                pass
        else:
            msg_match = (
                (intent.original_message or "").strip()
                == (message or "").strip()
            )
            if msg_match:
                try:
                    session.set_metadata(
                        "risk_authorized_intent_active",
                        intent.to_dict(),
                    )
                    session.set_metadata("risk_authorized_intent", None)
                except Exception:
                    pass
                consumed_any = True
    return consumed_any


# Targets that still require RiskGate confirmation even under trust
# ("yolo") mode. Ordinary user files (desktop documents etc.) are
# released; security policy / allowlists / shell commands / protected
# files / the death switch keep the confirmation flow.
TRUST_MODE_MUST_CONFIRM_TARGETS = frozenset(
    {
        TargetKind.SECURITY_USER_ALLOWLIST,
        TargetKind.SECURITY_POLICY,
        TargetKind.DEATH_SWITCH,
        TargetKind.PROTECTED_FILE,
        TargetKind.SHELL_COMMAND,
    }
)


def check_trust_mode_skip(risk_intent: RiskIntentResult | None) -> str | None:
    """Skip the pre-LLM RiskGate confirm when the user is in trust mode
    and the intent only touches ordinary user data.

    Sensitive targets (security policy, allowlists, shell commands,
    protected files) still go through the normal confirm flow even
    in trust mode. On policy-engine errors we fall back to "no
    skip" (i.e. ask the user) -- conservative is the right default
    when the SoT is unreachable.
    """
    if risk_intent is None:
        return None
    if risk_intent.target_kind in TRUST_MODE_MUST_CONFIRM_TARGETS:
        return None

    try:
        from openakita.core.policy_v2 import ConfirmationMode
        from openakita.core.policy_v2.global_engine import get_config_v2

        mode_value = get_config_v2().confirmation.mode
        is_trust = (
            mode_value == ConfirmationMode.TRUST or str(mode_value) == "trust"
        )
    except Exception:
        return None

    if is_trust:
        return "trust_mode"
    return None


def check_trusted_path_skip(
    session: Any,
    message: str,
    risk_intent: RiskIntentResult | None,
) -> str | None:
    """Trusted-path / session-grant skip decision.

    Returns a human-readable reason string when skipped, or ``None``
    when the normal risk gate must run. **Never** returns a skip
    reason for ``RiskLevel.HIGH`` -- that bar (sensitive targets /
    shell hard verbs) requires explicit confirmation regardless of
    trust state.
    """
    if risk_intent is None or not message:
        return None
    if risk_intent.risk_level == RiskLevel.HIGH:
        return None

    try:
        if is_trusted_workspace_path(message):
            return "trusted_workspace_path"
    except Exception:
        pass

    try:
        op_value = (
            risk_intent.operation_kind.value
            if hasattr(risk_intent.operation_kind, "value")
            else str(risk_intent.operation_kind)
        )
        if consume_session_trust(session, message=message, operation=op_value):
            return "session_grant"
    except Exception:
        pass

    return None


def build_destructive_intent_question(
    message: str, classification: RiskIntentResult | None = None
) -> str:
    """Format the two-step destructive-intent confirmation prompt.

    PR-1.1: produce a short "\u51c6\u5907\u6267\u884c X" (“about to do X”)
    summary first (30 chars max) plus the operation + target
    metadata, then offer three options. Keeps users from staring
    at their own raw long message when the model surfaces the
    confirm.
    """
    target = (message or "").strip()
    summary = summarize_destructive_action(target, classification)
    options = "回复 **继续** / **只查看** / **取消** 三选一。"
    if classification is not None:
        op = (
            classification.operation_kind.value
            if hasattr(classification.operation_kind, "value")
            else str(classification.operation_kind or "")
        )
        target_kind = (
            classification.target_kind.value
            if hasattr(classification.target_kind, "value")
            else str(classification.target_kind or "")
        )
        meta_parts = []
        if op and op not in ("none", "unknown"):
            meta_parts.append(f"op={op}")
        if target_kind and target_kind not in ("unknown",):
            meta_parts.append(f"target={target_kind}")
        meta_line = (
            f"（{', '.join(meta_parts)}）" if meta_parts else ""
        )
    else:
        meta_line = ""

    return (
        f"准备执行：**{summary}** {meta_line}\n\n"
        "这个动作可能改动文件 / 配置 / 权限，做完不一定能撤回，先确认一下。\n"
        f"{options}"
    )


DESTRUCTIVE_VERBS = (
    "删除",
    "删掉",
    "清空",
    "清除",
    "重置",
    "覆盖",
    "禁用",
    "关闭",
    "卸载",
    "销毁",
    "格式化",
)


def summarize_destructive_action(text: str, classification: Any | None = None) -> str:
    """Best-effort one-line summary of a destructive request (<=30 chars).

    Order of preference for the surfaced span:

    1.  A high-signal verb match (``_HIGH_SIGNAL_VERB_RE``) -- guarantees
        the confirm prompt actually quotes ``rm -rf`` /
        ``DROP TABLE`` / ``删除`` instead of the chatty preamble that
        the user wrapped them in (v10 #12 / v11 C9 regression).
    2.  A sentence-break split that lands in the [5, 30] window --
        keeps short, single-clause requests readable.
    3.  A ``DESTRUCTIVE_VERBS`` (CJK-only) match -- legacy fallback.
    4.  Plain prefix truncation -- last resort.
    """
    raw = (text or "").strip()
    if not raw:
        return "未指定操作"
    if len(raw) <= 30:
        return raw

    # (1) High-signal verb wins regardless of where in the body it
    # appears; otherwise a sentence break in the preamble would
    # silently hide the actual dangerous span.
    m = _HIGH_SIGNAL_VERB_RE.search(raw)
    if m is not None:
        start = max(m.start() - 4, 0)
        end = min(m.end() + 24, len(raw))
        excerpt = raw[start:end].strip()
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(raw) else ""
        return f"{prefix}{excerpt}{suffix}"

    # (2) Sentence-break split (legacy behaviour for short, clear
    # requests where (1) does not match).
    for sep in ("。", "\n", "；", ";", "！", "!"):
        idx = raw.find(sep)
        if 5 <= idx <= 30:
            return raw[:idx].strip() or raw[:30] + "…"

    # (3) Legacy CJK destructive-verb scan.
    for verb in DESTRUCTIVE_VERBS:
        i = raw.find(verb)
        if i != -1:
            tail = raw[i : i + 28]
            return tail + ("…" if len(raw) > i + 28 else "")

    # (4) Prefix truncation last resort.
    return raw[:28] + "…"

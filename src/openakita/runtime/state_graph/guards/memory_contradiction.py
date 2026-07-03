"""Memory-contradiction (blind-reversal) guard.

Deterministic detector for the F1 failure class: the user issues a
*self-confident reversal* of a **non-numeric, user-owned fact**
("是小林管吧台、阿May管收银，你记反了吧？") that directly conflicts with
something the assistant already recorded / stated earlier in the same
conversation. Weak local models tend to blindly agree ("你说得对，我记
错了"), flip the record, and fabricate a self-negation — even when the
original record was correct.

A pure system-prompt rule was already shipped and proven insufficient
(see ``tools-tmp/verify_report.md`` item 14). This guard adds a
*targeted, hit-only* signal on top: instead of relying on the model to
self-police against a generic rule that is buried in a 30k-token prompt,
we deterministically detect the reversal pattern for the current turn and
hand the model (a) an explicit, high-salience runtime constraint and (b)
the exact historical original text to cite. The directive is injected
**only** on a positive detection, so the normal correction flow (the user
honestly supplying new info, e.g. "地址改成拱墅区") is untouched.

Two gates keep false positives low and avoid friction on genuine
corrections:

1. **Reversal / memory-challenge marker** — the message must accuse the
   assistant of mis-remembering (记反 / 记错 / 搞反 / 弄反 / 说反 / 颠倒 /
   反了 …). A plain factual correction without this accusatory framing
   never trips the guard. First-person self-corrections ("我记错了，是
   拱墅区") are excluded — those are honest user admissions, not a
   challenge to the assistant's memory.
2. **Historical grounding** — the challenged content must have a
   traceable original in the conversation history (a name / attribute the
   assistant or user stated earlier). Without an original to cite, the
   directive would be meaningless, so the guard stays silent (again
   avoiding friction on genuinely new information).

The guard is pure and side-effect free: :func:`detect_memory_contradiction`
returns a :class:`ContradictionSignal` (or ``None``) and
:func:`format_contradiction_alert` renders the prompt section from a
serialisable dict. The prompt builder injects the section; nothing here
mutates state or blocks the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ContradictionSignal",
    "detect_memory_contradiction",
    "format_contradiction_alert",
]


# --- Reversal / memory-challenge markers -----------------------------------
# Each pattern matches an accusatory "you got it wrong / it's the other way
# round" framing. Bare "错了" is intentionally NOT matched — only verbs that
# pin the error on remembering/stating ("记错/说错/搞反") count, so ordinary
# corrections and calculation disputes don't trip the guard.
_CHALLENGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[记搞弄说写背念讲标]\s*[反错]"),  # 记反 记错 搞反 说错 弄反 写错 …
    re.compile(r"[记搞弄]\s*混"),  # 记混 搞混 弄混
    re.compile(r"搞乱|弄乱|记乱"),
    re.compile(r"颠倒"),
    # 反了 / 反过来，但排除 违反/相反/造反/谋反 之类的非"翻转"含义
    re.compile(r"(?<![违相造谋])反\s*[了过]"),
)

# First-person subject right before a marker → user self-correction, not a
# challenge to the assistant's memory. Checked in a small window before match.
_FIRST_PERSON_CHARS = ("我", "咱")


@dataclass
class ContradictionSignal:
    """Result of a positive memory-contradiction detection.

    ``matched_terms`` are the grounded tokens (names / attributes) that the
    current message shares with the conversation history. ``evidence`` holds
    up to a couple of the original historical snippets (with an ``HH:MM``
    timestamp when available) so the model can cite them verbatim.
    """

    matched_terms: list[str] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_terms": list(self.matched_terms),
            "evidence": [dict(e) for e in self.evidence],
        }


# --- Grounding helpers ------------------------------------------------------

# Pure function-word n-grams that are too generic to prove a real subject
# overlap. Names / attributes ("小林", "吧台", "收银") are deliberately absent.
_STOPWORD_TERMS: frozenset[str] = frozenset(
    {
        "你的", "我的", "他的", "她的", "不是", "是不", "这个", "那个", "什么",
        "怎么", "一下", "确定", "确认", "记得", "以为", "应该", "可能", "还是",
        "就是", "不对", "没有", "这样", "那样", "如果", "为什么", "是不是",
        "知道", "告诉", "记录", "更新", "保存", "刚才", "之前", "历史", "现在",
        "已经", "对吧", "是的", "对的", "其实", "然后", "所以", "但是", "而且",
        "或者", "这些", "那些", "一样", "不一样", "记反", "记错", "搞反", "搞错",
        "弄反", "弄错", "说反", "说错", "颠倒", "反了", "你记", "记了", "了吧",
        "了吗",
    }
)

_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_ALNUM_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{1,}")
_HHMM_RE = re.compile(r"(\d{1,2}):(\d{2})")


def _candidate_terms(text: str) -> set[str]:
    """Content n-grams (CJK 2–4 grams + alnum tokens) usable for grounding."""
    cands: set[str] = set()
    for run in _CJK_RUN_RE.findall(text):
        n = len(run)
        for size in (2, 3, 4):
            if size > n:
                break
            for i in range(0, n - size + 1):
                cands.add(run[i : i + size])
    for tok in _ALNUM_TOKEN_RE.findall(text):
        if len(tok) >= 2:
            cands.add(tok)
    return cands


def _has_challenge_marker(message: str) -> bool:
    """True when the message accuses the assistant of mis-remembering.

    Skips matches that are first-person self-corrections (e.g. "我记错了").
    """
    for pattern in _CHALLENGE_PATTERNS:
        for m in pattern.finditer(message):
            window = message[max(0, m.start() - 3) : m.start()]
            if any(ch in window for ch in _FIRST_PERSON_CHARS):
                # "我记错了 / 是我搞反了" — honest self-correction, not a challenge.
                continue
            return True
    return False


def _format_hhmm(timestamp: str) -> str:
    """Best-effort ``HH:MM`` extraction from an ISO / free-form timestamp."""
    if not timestamp:
        return ""
    m = _HHMM_RE.search(timestamp)
    if not m:
        return ""
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _snippet(content: str, limit: int = 140) -> str:
    text = " ".join(content.split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def detect_memory_contradiction(
    message: str,
    history: list[dict[str, Any]] | None,
    working_facts: dict[str, Any] | None = None,
    *,
    max_history: int = 40,
) -> ContradictionSignal | None:
    """Detect a blind-reversal correction that conflicts with history.

    Args:
        message: the current raw user message.
        history: prior conversation messages (``{role, content, timestamp}``),
            oldest→newest. A trailing message equal to ``message`` is dropped
            so the current turn never grounds against itself.
        working_facts: session working facts (values are also grounding
            sources).
        max_history: cap on how many recent prior messages are scanned.

    Returns:
        A :class:`ContradictionSignal` when both gates pass, else ``None``.
    """
    text = (message or "").strip()
    if not text:
        return None

    # Gate 1: reversal / memory-challenge framing.
    if not _has_challenge_marker(text):
        return None

    prior = list(history or [])
    # Drop a trailing user echo of the current message (it may already be
    # appended to the session before the prompt is built).
    if prior:
        last = prior[-1]
        if (
            isinstance(last, dict)
            and last.get("role") == "user"
            and str(last.get("content") or "").strip() == text
        ):
            prior = prior[:-1]
    if len(prior) > max_history:
        prior = prior[-max_history:]

    if not prior and not working_facts:
        return None

    cands = {c for c in _candidate_terms(text) if c not in _STOPWORD_TERMS}
    if not cands:
        return None

    # Gate 2: at least one candidate term must be grounded in history / facts.
    # Scan newest→oldest, preferring assistant-authored originals (the
    # assistant is what the user is accusing of being wrong), then user
    # messages (the fact may originate from the user and have been echoed).
    matched_terms: set[str] = set()
    assistant_evidence: list[dict[str, str]] = []
    user_evidence: list[dict[str, str]] = []

    for msg in reversed(prior):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("assistant", "user"):
            continue
        content = str(msg.get("content") or "")
        if not content:
            continue
        hits = [c for c in cands if c in content]
        if not hits:
            continue
        matched_terms.update(hits)
        record = {
            "role": role,
            "time": _format_hhmm(str(msg.get("timestamp") or "")),
            "snippet": _snippet(content),
        }
        if role == "assistant" and len(assistant_evidence) < 2:
            assistant_evidence.append(record)
        elif role == "user" and len(user_evidence) < 2:
            user_evidence.append(record)

    # Working-fact values are also legitimate originals to cite.
    for key, payload in (working_facts or {}).items():
        value = payload.get("value") if isinstance(payload, dict) else payload
        if not value:
            continue
        value_str = str(value)
        if value_str in text and value_str not in _STOPWORD_TERMS:
            matched_terms.add(value_str)
            if len(user_evidence) < 2:
                user_evidence.append(
                    {"role": "working_fact", "time": "", "snippet": f"{key}: {value_str}"}
                )

    if not matched_terms:
        return None

    evidence = (assistant_evidence + user_evidence)[:2]
    return ContradictionSignal(
        matched_terms=sorted(matched_terms)[:5],
        evidence=evidence,
    )


def format_contradiction_alert(alert: dict[str, Any] | ContradictionSignal | None) -> str:
    """Render the runtime constraint section for a positive detection.

    Accepts either a :class:`ContradictionSignal` or its ``to_dict`` form so
    the prompt builder can format a value carried through ``session_context``.
    Returns an empty string when there is nothing to inject.
    """
    if alert is None:
        return ""
    if isinstance(alert, ContradictionSignal):
        alert = alert.to_dict()
    if not isinstance(alert, dict):
        return ""

    evidence = alert.get("evidence") or []

    lines = [
        "## ⚠️ 本轮矛盾更正检测（运行时强制约束，最高优先级）",
        "",
        "系统已确定性检测到：用户本轮在**质疑或推翻**你之前记录/陈述过的事实"
        "（例如“记反了/记错了/搞反了/颠倒了/反了吧”这类表述），并且被质疑的内容"
        "在**对话历史中有可追溯的原始出处**。这类“非数值、用户自有事实”的自信反驳"
        "极易诱导你盲目认错、翻转记录并伪造“是我记错了”的自我否定——**这是被明确"
        "禁止的行为**。",
        "",
        "在你本轮回复前，必须严格执行以下步骤，任何一步都不得跳过：",
        "1. **先复述历史原文**：从下方“历史原始记录”或对话历史中，找到你原始记录/"
        "陈述该事实的确切原文，连同 `[HH:MM]` 时间戳一起复述给用户"
        "（例如“我这边记录的是 [18:27] 你说阿May管吧台”）。",
        "2. **请用户二次确认**：明确询问用户是否确定要改成新说法，并等待其确认；"
        "优先使用 ask_user 工具发起确认。",
        "3. **禁止未经核实就翻转**：在用户二次确认之前，**严禁**直接认错、翻转记录、"
        "或说“你说得对/是我记错了/是我记反了”这类自我否定。有明确出处的原记录"
        "就是当前的权威版本。",
        "4. **确认后才落库**：只有在用户明确二次确认要修改后，才更新；一旦你在回复中"
        "声称“已更新/已保存/已记下”，本轮就**必须真的调用** update_user_profile 或 "
        "add_memory，绝不能只说不做。",
    ]

    if evidence:
        lines.append("")
        lines.append("历史原始记录（供你复述时逐字引用，不要改写）：")
        for item in evidence:
            role = item.get("role", "")
            role_label = {
                "assistant": "你曾记录/回复",
                "user": "用户当时的原话",
                "working_fact": "会话事实",
            }.get(role, role or "历史")
            time = item.get("time") or ""
            time_prefix = f"[{time}] " if time else ""
            snippet = item.get("snippet", "")
            lines.append(f"- {time_prefix}{role_label}：{snippet}")

    return "\n".join(lines)

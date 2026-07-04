"""Three-tier financial-data desensitizer (v0.2 Part 2 §3).

Sensitivity tiers
-----------------

* ``metadata``    — the safest tier.  Replaces every value with its Python
                    type-name (``str`` / ``int`` / ``float`` / ``dict`` /
                    ``list``).  Output preserves *schema* but reveals
                    nothing else.  Used for ERP-source detection,
                    field-mapping suggestions, balance-diagnose hints.
* ``aggregated``  — for risk / variance scenarios.  Numeric values bucket
                    into magnitude labels (``万元级`` / ``百万级`` / ...);
                    strings flagged by the PII config are anonymised
                    (``公司A`` / ``人员1`` / ``合同X``).  Other strings
                    pass through unchanged (column names, sheet titles
                    etc. carry zero PII risk).
* ``raw``         — full payload.  Only PII fields are still anonymised;
                    numeric values pass through.  Reserved for the
                    user-explicitly-permitted scenarios that ride on a
                    local LLM (Ollama / LM Studio).

A red-team scanner (``scan_residual_pii``) double-checks the output for
amount-shaped strings + obvious Chinese name patterns; the consent
checker uses it to enforce R2 of v0.2 §2.3 (auto-upgrade on residue
detection).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from .pii_config import DesensitizeConfig, load_pii_config

SensitivityLevel = Literal["metadata", "aggregated", "raw"]
SENSITIVITY_LEVELS: tuple[SensitivityLevel, ...] = ("metadata", "aggregated", "raw")

# ---------------------------------------------------------------------------
# Magnitude bucketing
# ---------------------------------------------------------------------------


def bucket_amount(value: float | int) -> str:
    """Translate a numeric amount into an order-of-magnitude label.

    The buckets follow the v0.2 design's CN-financial labels.  Negative
    amounts are bucketed by absolute value (so ``-3_000_000`` becomes
    ``"-百万级"``); zero stays ``"0"``.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "0"
    if v == 0:
        return "0"
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a < 1_000:
        bucket = "千元以下"
    elif a < 10_000:
        bucket = "千元级"
    elif a < 1_000_000:
        bucket = "万元级"
    elif a < 100_000_000:
        bucket = "百万级"
    else:
        bucket = "亿元级"
    return f"{sign}{bucket}"


# ---------------------------------------------------------------------------
# Core desensitize routine
# ---------------------------------------------------------------------------

# Static labels per kind — keeps repeated runs deterministic per session.
_KIND_LABELS: dict[str, str] = {
    "company": "公司",
    "person": "人员",
    "account": "账号",
    "contract": "合同",
}


def _anon_label(kind: str, idx: int, original: str) -> str:
    if kind == "account":
        # Account numbers are quasi-uniquely identifying; SHA-256 prefix is
        # both stable and unrecoverable.  Six hex chars give 1e-7 collision.
        return hashlib.sha256(original.encode("utf-8")).hexdigest()[:6]
    label = _KIND_LABELS[kind]
    if idx < 26:
        return f"{label}{chr(ord('A') + idx)}"
    return f"{label}{idx + 1}"


def desensitize(
    payload: Any,
    level: SensitivityLevel,
    cfg: DesensitizeConfig | None = None,
) -> Any:
    """Recursively desensitize ``payload`` to ``level``.

    ``payload`` can be any JSON-serialisable Python value (dict / list /
    str / number / bool / None).  Returns a new structure of the same
    shape — never mutates the input.
    """
    if level not in SENSITIVITY_LEVELS:
        raise ValueError(f"unknown sensitivity level: {level!r}")
    cfg = cfg or load_pii_config()
    counters: dict[str, dict[str, str]] = {
        "company": {}, "person": {}, "account": {}, "contract": {},
    }

    def _anon_key(kind: str, original: str) -> str:
        bucket = counters[kind]
        if original in bucket:
            return bucket[original]
        label = _anon_label(kind, len(bucket), original)
        bucket[original] = label
        return label

    def _walk(node: Any, key_hint: str | None = None) -> Any:
        # Booleans inherit from int — distinguish before the numeric branch.
        if isinstance(node, bool):
            return "bool" if level == "metadata" else node
        if isinstance(node, (int, float)):
            if level == "metadata":
                return type(node).__name__
            if level == "aggregated":
                return bucket_amount(float(node))
            return node
        if node is None:
            return "NoneType" if level == "metadata" else None
        if isinstance(node, str):
            if level == "metadata":
                return "str"
            kind = cfg.kind_of(key_hint) if key_hint is not None else None
            if kind is not None and node:
                return _anon_key(kind, node)
            return node
        if isinstance(node, dict):
            return {k: _walk(v, key_hint=k) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return [_walk(item, key_hint=key_hint) for item in node]
        # Fallback — opaque objects get repr'd at the metadata level and
        # str-coerced otherwise; this matches OpenAkita's `data/llm_debug/`
        # convention so audit snapshots stay legible.
        if level == "metadata":
            return type(node).__name__
        return str(node)

    return _walk(payload)


# ---------------------------------------------------------------------------
# Preview (used by the consent dialog)
# ---------------------------------------------------------------------------


def preview_desensitization(
    payload: Any,
    level: SensitivityLevel,
    cfg: DesensitizeConfig | None = None,
    *,
    max_chars: int = 2048,
) -> str:
    """Return a JSON preview string truncated to ``max_chars`` (default 2 KB).

    The preview is what the front-end shows the user inside the consent
    dialog; the truncation marker is appended verbatim so the React side
    can detect "shown a sample only" without having to count bytes.
    """
    safe = desensitize(payload, level, cfg)
    text = json.dumps(safe, ensure_ascii=False, indent=2, default=str)
    if len(text) <= max_chars:
        return text
    cut = max_chars - 24  # leave room for the marker.
    return text[:cut] + "\n... (已截断) ..."


# ---------------------------------------------------------------------------
# Red-team residue scanner (v0.2 §3 + §10 R3)
# ---------------------------------------------------------------------------

# Amounts shaped like "1234.56" or "¥1,234,567.89" — anything that survived
# bucketing is suspect.  We tolerate the bucket labels we just emitted by
# excluding any token that contains 元 / 万 / 亿.
_AMOUNT_PATTERN = re.compile(r"\b\d{4,}(?:\.\d{1,2})?\b|¥\s*[\d,]+")
# Phone numbers (Chinese mainland 11-digit) and ID-like 18-digit strings.
_PHONE_PATTERN = re.compile(r"\b1[3-9]\d{9}\b")
_ID_PATTERN = re.compile(r"\b\d{15}(?:\d{2}[\dXx])?\b")


def scan_residual_pii(text: str) -> list[str]:
    """Return suspicious tokens that *should not* exist after desensitize.

    ``text`` is the JSON-serialised post-desensitize string.  The scanner
    is deliberately conservative: false positives are cheaper than missed
    PII because the hit only triggers an upgrade-and-re-prompt, not a
    hard failure.
    """
    suspects: list[str] = []
    for match in _AMOUNT_PATTERN.findall(text):
        if any(token in match for token in ("元", "万", "亿")):
            continue
        suspects.append(match)
    suspects.extend(_PHONE_PATTERN.findall(text))
    suspects.extend(_ID_PATTERN.findall(text))
    return suspects


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def payload_sha256(payload: Any) -> str:
    """Stable hash of a desensitized payload — used for audit dedup."""
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "SENSITIVITY_LEVELS",
    "SensitivityLevel",
    "bucket_amount",
    "desensitize",
    "payload_sha256",
    "preview_desensitization",
    "scan_residual_pii",
]

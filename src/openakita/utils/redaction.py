"""Centralized redaction helpers for logs and public API payloads."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTION = "[REDACTED]"

_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[_\-.])("
    r"app[_\-.]?secret|bot[_\-.]?token|access[_\-.]?key|secret|token|"
    r"password|passwd|pwd|authorization|cookie|credential|api[_\-.]?key|"
    r"ticket|session[_\-.]?key"
    r")(?:$|[_\-.])",
    re.IGNORECASE,
)

_KEY_VALUE_RE = re.compile(
    r"(?P<key>\b(?:app[_\-.]?secret|bot[_\-.]?token|access[_\-.]?key|"
    r"authorization|cookie|password|passwd|pwd|secret|token|credential|"
    r"api[_\-.]?key|ticket|session[_\-.]?key)\b)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<quote>['\"]?)"
    r"(?P<value>[^'\"\s,;&{}\[\]]+)"
    r"(?P=quote)",
    re.IGNORECASE,
)

_BEARER_RE = re.compile(
    r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+",
    re.IGNORECASE,
)


def is_sensitive_key(key: object) -> bool:
    """Return True when *key* looks like it carries a secret."""
    return bool(_SENSITIVE_KEY_RE.search(str(key)))


def redact_text(text: object) -> str:
    """Redact common secret forms in free-form text and URLs."""
    value = str(text)
    value = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTION}", value)

    def _replace_kv(match: re.Match[str]) -> str:
        return f"{match.group('key')}{match.group('sep')}{match.group('quote')}{REDACTION}{match.group('quote')}"

    value = _KEY_VALUE_RE.sub(_replace_kv, value)
    value = _redact_urls(value)
    return value


def redact_value(value: Any) -> Any:
    """Recursively redact dict/list/string payloads."""
    if isinstance(value, Mapping):
        return {
            k: REDACTION if is_sensitive_key(k) else redact_value(v)
            for k, v in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_value(v) for v in value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(v) for v in value]
    if isinstance(value, bytes):
        try:
            return redact_text(value.decode("utf-8", errors="replace"))
        except Exception:
            return REDACTION
    if isinstance(value, str):
        return redact_text(value)
    return value


def _redact_urls(text: str) -> str:
    """Redact sensitive query parameters inside URLs embedded in text."""
    url_re = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)

    def _replace(match: re.Match[str]) -> str:
        raw_url = match.group(0)
        try:
            parts = urlsplit(raw_url)
            if not parts.query:
                return raw_url
            query = []
            changed = False
            for key, val in parse_qsl(parts.query, keep_blank_values=True):
                if is_sensitive_key(key):
                    query.append((key, REDACTION))
                    changed = True
                else:
                    query.append((key, val))
            if not changed:
                return raw_url
            return urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
            )
        except Exception:
            return raw_url

    return url_re.sub(_replace, text)


class RedactionFilter(logging.Filter):
    """Logging filter that redacts messages and structured args before output."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_text(record.msg)
            if isinstance(record.args, Mapping):
                record.args = redact_value(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(redact_value(arg) for arg in record.args)
            elif record.args:
                record.args = redact_value(record.args)
        except Exception:
            pass
        return True

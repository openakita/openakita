"""
Credential redaction utility

Redacts credential-like patterns from text such as MCP tool return values,
preventing API keys, passwords, tokens and other sensitive information from
leaking into the LLM context.
"""

import re

_CREDENTIAL_PATTERNS = [
    # API Keys (generic)
    (re.compile(r'(?i)(api[_-]?key|apikey)\s*[=:]\s*["\']?([a-zA-Z0-9_\-]{20,})["\']?'), r"\1=[REDACTED]"),
    # Bearer tokens
    (re.compile(r'(?i)(bearer\s+)([a-zA-Z0-9_\-\.]{20,})'), r"\1[REDACTED]"),
    # Authorization headers
    (re.compile(r'(?i)(authorization\s*[=:]\s*["\']?)([a-zA-Z0-9_\-\.+/=]{20,})'), r"\1[REDACTED]"),
    # AWS Keys
    (re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{16}'), "[REDACTED_AWS_KEY]"),
    # Generic secrets / passwords / tokens
    (re.compile(r'(?i)(password|passwd|secret|token|credential)\s*[=:]\s*["\']?([^\s"\']{8,})["\']?'), r"\1=[REDACTED]"),
    # GitHub tokens
    (re.compile(r'gh[ps]_[a-zA-Z0-9]{36,}'), "[REDACTED_GH_TOKEN]"),
    # Slack tokens
    (re.compile(r'xox[bpras]-[a-zA-Z0-9\-]{10,}'), "[REDACTED_SLACK_TOKEN]"),
    # Private keys
    (re.compile(r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA )?PRIVATE KEY-----'), "[REDACTED_PRIVATE_KEY]"),
    # Connection strings with credentials
    (re.compile(r'(?i)(mongodb|postgres|mysql|redis|amqp)://[^:]+:([^@]+)@'), r"\1://[user]:[REDACTED]@"),
]


def redact_credentials(text: str) -> str:
    """Remove credential-like patterns from text, replacing with [REDACTED]."""
    if not text:
        return text

    result = text
    for pattern, replacement in _CREDENTIAL_PATTERNS:
        result = pattern.sub(replacement, result)

    return result

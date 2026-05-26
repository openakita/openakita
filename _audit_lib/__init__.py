"""Shared exploratory-audit helpers reused across v20+ test sprints.

Sprint-8 P0-C (v19 audit ``_orgs_business_capability_audit_v8.md`` §5.3
+ §8.3): the per-sprint ``_v*_biz/_lib.py`` copies grew their own
``grep_logs_since`` that filtered on **file mtime** rather than the
in-line timestamp. v19 reported 12 ``No handler mapped for tool`` hits
that were all 2026-05-25 v18 historical lines re-discovered after the
v19 cutoff because the surrounding ``error.log.2026-05-25`` file
mtime happened to land after the cutoff timestamp. The v19 exploratory
worker manually re-grepped per line and confirmed the ``after_cutoff_
by_line_ts`` count was zero, but the audit script itself still
reported 12 -- a false positive that propagated into the radar
score and cluttered the v18 -> v19 progress narrative.

This package extracts the line-timestamp-aware grep into one place so
v20+ scripts can ``from _audit_lib.log_grep import grep_logs_since``
and stop carrying the bug. The helper handles the OpenAkita default
log line shape (``YYYY-MM-DD HH:MM:SS,ms - module - LEVEL - message``)
and falls back to the file mtime cutoff when the line lacks a
parseable timestamp prefix (e.g. continuation lines after a Python
traceback).

Pattern 2 (test client httpx timeouts) is captured here as a set of
named constants so v20+ test scripts can ``from _audit_lib.timeouts
import RECOMMENDED`` and stop hard-coding ``timeout=30.0`` values that
bite the test runner on a slow LLM provider.
"""

from __future__ import annotations

from .log_grep import grep_logs_since, iter_log_lines_since, parse_line_timestamp
from .timeouts import RECOMMENDED, RecommendedTimeouts

__all__ = [
    "RECOMMENDED",
    "RecommendedTimeouts",
    "grep_logs_since",
    "iter_log_lines_since",
    "parse_line_timestamp",
]

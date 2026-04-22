"""Port of Maestro src/renderer/hooks/batch/batchUtils.ts.

Shared so the playbook runner, any future playbook-preview route, and unit
tests all agree on what counts as a checked task. Keeping the regexes
identical ensures `[x]`, `[X]`, `[✓]`, `[✔]` all match Maestro;
both `-` and `*` bullets and leading whitespace are handled too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_UNCHECKED = re.compile(r"^[ \t]*[-*]\s*\[\s*\]\s*.+$", re.MULTILINE)
_CHECKED_COUNT = re.compile(r"^[ \t]*[-*]\s*\[[xX✓✔]\]\s*.+$", re.MULTILINE)
_CHECKED_REPLACE = re.compile(r"^(\s*[-*]\s*)\[[xX✓✔]\]", re.MULTILINE)


@dataclass(frozen=True)
class CheckboxCounts:
    checked: int
    unchecked: int


def count_checkboxes(content: str) -> CheckboxCounts:
    checked = 0
    unchecked = 0
    for line in content.splitlines():
        if _CHECKED_COUNT.match(line):
            checked += 1
        elif _UNCHECKED.match(line):
            unchecked += 1
    return CheckboxCounts(checked=checked, unchecked=unchecked)


def count_unchecked(content: str) -> int:
    return count_checkboxes(content).unchecked


def count_checked(content: str) -> int:
    return count_checkboxes(content).checked


def uncheck_all(content: str) -> str:
    """Reset every checked task marker to `[ ]`. Non-task `[x]` tokens embedded
    in prose are left alone because the regex is anchored to the bullet prefix.
    Idempotent: calling twice is the same as calling once."""
    return _CHECKED_REPLACE.sub(r"\1[ ]", content)

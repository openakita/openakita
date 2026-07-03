"""Shared tolerant JSONL readers for org append-only stores."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _is_nul_only_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) == {"\x00"}


def _strip_trailing_nul_bytes(line: str) -> str:
    if "\x00" not in line:
        return line
    return line.rstrip("\x00").rstrip()


def read_jsonl_objects(
    path: Path,
    *,
    log: logging.Logger | None = None,
    decoder: Callable[[dict[str, Any]], Any] | None = None,
) -> list[Any]:
    """Read JSONL records while tolerating corrupt trailing lines.

    Organization stores are append-only files. A crash or interrupted write can
    leave the last line partially written or NUL-filled; callers should still
    see earlier valid records instead of treating the whole file as empty.
    Non-tail corrupt lines are skipped too, but logged more prominently because
    they indicate older damage rather than a normal torn append.
    """

    active_log = log or logger
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    raw_lines = text.splitlines()
    total_lines = len(raw_lines)
    objects: list[Any] = []
    skipped = 0
    for index, line in enumerate(raw_lines, start=1):
        if not line.strip():
            continue
        candidate = _strip_trailing_nul_bytes(line)
        if not candidate:
            candidate = line
        try:
            payload = json.loads(candidate)
            objects.append(decoder(payload) if decoder else payload)
        except Exception as exc:  # noqa: BLE001 - tolerate corrupt rows by design.
            skipped += 1
            is_tail = index == total_lines
            reason = "NUL-only" if _is_nul_only_line(line) else type(exc).__name__
            position = "tail" if is_tail else "non-tail"
            message = f"Skipped corrupt {position} JSONL line {index} in {path} ({reason}: {exc})"
            if is_tail:
                active_log.warning(message)
            else:
                active_log.error(message)
            continue

    if skipped:
        active_log.warning(
            "Recovered %s valid JSONL records from %s after skipping %s corrupt line(s)",
            len(objects),
            path,
            skipped,
        )
    return objects


def iter_jsonl_objects_reverse(
    path: Path,
    *,
    log: logging.Logger | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield JSON objects newest-first from a tolerant JSONL read."""

    records = read_jsonl_objects(path, log=log)
    for record in reversed(records):
        if isinstance(record, dict):
            yield record

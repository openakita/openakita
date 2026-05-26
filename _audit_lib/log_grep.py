"""Line-timestamp-aware log grep, replacing the file-mtime variants
shipped in v17/v18/v19 ``_v*_biz/_lib.py``.

Sprint-8 P0-C (v19 audit ``_orgs_business_capability_audit_v8.md``
§5.3 + §8.3): the legacy helpers used ``fp.stat().st_mtime`` as the
single cutoff filter. That works for "pristine" log files written
after the cutoff, but the OpenAkita logger uses a daily-rotated
naming scheme (``error.log``, ``error.log.YYYY-MM-DD``) where a file
may carry lines spanning hours or days while its mtime only reflects
the most recent append. v19 found 12 ``No handler mapped for tool``
hits that were all 2026-05-25 lines re-discovered through the
``error.log.2026-05-25`` file whose mtime happened to land after the
v19 cutoff because of an unrelated late append.

This module parses each line's leading timestamp (the OpenAkita
default formatter emits ``YYYY-MM-DD HH:MM:SS,ms - module - LEVEL -
message``) and only counts the line as a hit when its in-line
timestamp is strictly **greater than** the cutoff. Lines without a
parseable timestamp (continuation lines from tracebacks, blank
lines, or third-party formatter quirks) inherit the timestamp of the
most recent line that did parse, falling back to a "skip" when no
prior anchor exists in the same file.

Public surface:

* :func:`grep_logs_since` -- counterpart of the v19 ``_lib.py``
  helper; returns an int hit count.
* :func:`iter_log_lines_since` -- yields ``(path, line_no, ts, line)``
  tuples so callers needing the actual matched lines can stay on
  this module instead of re-reading the file.
* :func:`parse_line_timestamp` -- exposed for tests / callers that
  want to validate a single line out-of-band.

The helpers are kept dependency-free (``pathlib`` + ``datetime`` +
``re``) so the per-sprint ``_v*_biz/`` scripts can import them
without pulling the openakita package.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "DEFAULT_LOG_DIRS",
    "DEFAULT_LOG_GLOB",
    "grep_logs_since",
    "iter_log_lines_since",
    "parse_line_timestamp",
]


# OpenAkita logger format (see ``logging.Formatter('%(asctime)s ...')``):
# ``2026-05-26 12:34:56,789 - module - LEVEL - message``.
# Tolerate optional milliseconds (some bundled handlers strip them) and
# both ``,`` (Python ``logging`` default) and ``.`` separators.
_LINE_TS_PATTERN = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?)")
_TS_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S,%f",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S,%f",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
)


# Kept in sync with the v17-v19 ``_lib.py`` defaults so callers porting
# from the per-sprint helpers do not have to rebuild the path list.
DEFAULT_LOG_DIRS: tuple[Path, ...] = (
    Path("D:/OpenAkita/logs"),
    Path("D:/OpenAkita/data/logs"),
)
DEFAULT_LOG_GLOB = "*.log*"


def parse_line_timestamp(line: str) -> float | None:
    """Best-effort parse of the OpenAkita logger timestamp prefix.

    Returns the POSIX timestamp (UTC-naive treated as local time, then
    converted via :func:`datetime.timestamp`) when the line starts
    with a recognised timestamp pattern; ``None`` otherwise.

    The function is intentionally permissive: callers chain it through
    :func:`iter_log_lines_since` which inherits the previous line's
    parsed timestamp for continuation lines (Python tracebacks span
    multiple lines without re-emitting the prefix).
    """

    m = _LINE_TS_PATTERN.match(line)
    if m is None:
        return None
    raw = m.group("ts")
    for fmt in _TS_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        try:
            # The Python logger emits local time without a tzinfo; mirror
            # the behaviour of ``time.time()`` so callers comparing to
            # ``Path.stat().st_mtime`` (also POSIX local) line up.
            return dt.timestamp()
        except (OSError, OverflowError):
            # Pre-epoch timestamps raise ``OSError`` on Windows when
            # the local-tz offset would push the value below zero;
            # treat them as unparseable so the caller can fall back
            # to the inherited cursor.
            return None
    return None


def _iter_log_paths(
    log_dirs: tuple[Path, ...] | None,
    glob: str,
    cutoff_ts: float,
) -> Iterator[Path]:
    dirs = log_dirs if log_dirs is not None else DEFAULT_LOG_DIRS
    for d in dirs:
        if not d.exists():
            continue
        for fp in d.glob(glob):
            if not fp.is_file():
                continue
            try:
                mtime = fp.stat().st_mtime
            except OSError:
                continue
            # File mtime is the *latest* append time. A file whose
            # mtime is older than the cutoff cannot contain any
            # post-cutoff lines, so we can safely skip it. We do NOT
            # use mtime as a positive filter -- that is exactly the
            # bug v19 caught.
            if mtime < cutoff_ts:
                continue
            yield fp


def iter_log_lines_since(
    cutoff_ts: float,
    pattern: str,
    *,
    log_dirs: tuple[Path, ...] | None = None,
    glob: str = DEFAULT_LOG_GLOB,
) -> Iterator[tuple[Path, int, float | None, str]]:
    """Yield ``(path, line_no, line_ts, raw_line)`` for every line that:

    * lives in one of the log files under ``log_dirs`` matching ``glob``,
    * carries an in-line timestamp strictly greater than ``cutoff_ts``
      (or inherits one from a recent ancestor line in the same file),
    * contains the literal ``pattern`` substring.

    Lines whose timestamp cannot be parsed and that have no recent
    parsed ancestor are skipped: there is no way to attribute them to
    "after cutoff" without a prefix. This is the conservative read
    that prevents the v19 false-positive class.
    """

    for fp in _iter_log_paths(log_dirs, glob, cutoff_ts):
        try:
            handle = fp.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        last_ts: float | None = None
        try:
            for idx, line in enumerate(handle, start=1):
                ts = parse_line_timestamp(line)
                if ts is not None:
                    last_ts = ts
                effective_ts = ts if ts is not None else last_ts
                if effective_ts is None:
                    # Pre-anchor noise (e.g. opening blank line) -- skip.
                    continue
                if effective_ts <= cutoff_ts:
                    continue
                if pattern in line:
                    yield fp, idx, effective_ts, line
        finally:
            handle.close()


def grep_logs_since(
    cutoff_ts: float,
    pattern: str,
    *,
    log_dirs: tuple[Path, ...] | None = None,
    glob: str = DEFAULT_LOG_GLOB,
) -> int:
    """Drop-in replacement for the v17-v19 ``_lib.py`` helper.

    Counts the number of post-cutoff log lines containing ``pattern``.
    Unlike the legacy helper, the cutoff is enforced **per line** via
    :func:`parse_line_timestamp`; the file mtime is only used to skip
    obviously-stale files for performance.
    """

    return sum(1 for _ in iter_log_lines_since(cutoff_ts, pattern, log_dirs=log_dirs, glob=glob))


def _now_ts_utc() -> float:
    """Tiny helper kept here so v20+ scripts can stamp cutoff_ts the
    same way :mod:`_audit_lib` interprets it (POSIX local-time-based,
    matching :func:`datetime.timestamp`).
    """

    return datetime.now(tz=UTC).timestamp()

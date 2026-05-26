"""Sprint-8 P0-C: pin the line-timestamp-aware grep semantics.

These tests exercise :mod:`_audit_lib.log_grep` against a tmp-dir log
tree so the v17-v19 file-mtime-only regression cannot return without
flipping at least one of these reds.

The audit lib lives at repo root (``D:/OpenAkita/_audit_lib``) so
v20+ scripts can ``from _audit_lib import ...`` without depending on
the openakita package; pytest finds it via the repo-root entry that
``conftest.py`` adds when collecting from ``tests/``.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add repo root so ``import _audit_lib`` works inside the tests -- the
# audit lib is intentionally NOT shipped inside ``src/openakita/`` to
# keep test artefacts decoupled from the production import graph.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from _audit_lib.log_grep import (  # noqa: E402  -- sys.path mutation above
    grep_logs_since,
    iter_log_lines_since,
    parse_line_timestamp,
)


def _line(ts: datetime, msg: str) -> str:
    """Format a line the way Python ``logging`` emits."""

    return ts.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3] + f" - mod - ERROR - {msg}\n"


def _ts(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, 0, 0)


# ---------------------------------------------------------------------------
# parse_line_timestamp
# ---------------------------------------------------------------------------


def test_parse_line_timestamp_python_logger_format() -> None:
    """case id: p08.audit.parse.python_logger_default

    Python ``logging.Formatter('%(asctime)s ...')`` emits the comma
    millisecond separator (e.g. ``2026-05-26 10:11:45,049``). The
    parser must accept it -- it is the OpenAkita default.
    """

    raw = "2026-05-26 10:11:45,049 - mod - ERROR - boom"
    ts = parse_line_timestamp(raw)
    assert ts is not None
    expected = datetime(2026, 5, 26, 10, 11, 45, 49000).timestamp()
    assert abs(ts - expected) < 1e-3


def test_parse_line_timestamp_iso_with_dot_millis() -> None:
    """case id: p08.audit.parse.iso_dot_millis

    Some bundled adapters use the ISO ``T`` separator + ``.`` for
    millis. The parser must accept that variant too.
    """

    raw = "2026-05-26T10:11:45.049000 - mod - ERROR - boom"
    ts = parse_line_timestamp(raw)
    assert ts is not None
    expected = datetime(2026, 5, 26, 10, 11, 45, 49000).timestamp()
    assert abs(ts - expected) < 1e-3


def test_parse_line_timestamp_continuation_line_returns_none() -> None:
    """case id: p08.audit.parse.continuation_no_prefix

    Lines without a parseable timestamp prefix (Python traceback
    continuation lines, blank lines) must return ``None`` so callers
    can fall back to the inherited cursor instead of guessing.
    """

    assert parse_line_timestamp("    File 'foo.py', line 1, in bar") is None
    assert parse_line_timestamp("") is None
    assert parse_line_timestamp("\n") is None
    # A line with a trailing timestamp (not at the start) must NOT match.
    assert parse_line_timestamp("blah 2026-05-26 10:11:45,049 blah") is None


# ---------------------------------------------------------------------------
# grep_logs_since
# ---------------------------------------------------------------------------


def test_grep_logs_since_file_mtime_post_cutoff_but_lines_pre_cutoff(
    tmp_path: Path,
) -> None:
    """case id: p08.audit.grep.v19_no_handler_mapped_false_positive

    Reproduces the exact v19 bug the audit caught:

    * cutoff = 2026-05-26 07:07:50 (v19 audit timestamp)
    * file ``error.log.2026-05-25`` carries 12 ``No handler mapped``
      lines all stamped 2026-05-25 (v18 historical residue)
    * something nudges the file mtime past the cutoff (e.g. the v19
      audit's own setup probe writing into the same logging handler)

    The legacy ``_lib.py`` helper would count 12; the new helper
    must count 0 because every line's in-line timestamp predates the
    cutoff.
    """

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    fp = log_dir / "error.log.2026-05-25"
    cutoff = _ts(2026, 5, 26, 7).timestamp()  # v19 cutoff at 07:00
    yesterday = _ts(2026, 5, 25, 12)
    body = "".join(_line(yesterday, "No handler mapped for tool: x") for _ in range(12))
    fp.write_text(body, encoding="utf-8")
    # Force the file mtime to *after* the cutoff -- this is what
    # tricked the legacy file-mtime-only filter.
    new_mtime = cutoff + 30
    os.utime(fp, (new_mtime, new_mtime))

    n = grep_logs_since(cutoff, "No handler mapped for tool", log_dirs=(log_dir,))
    assert n == 0, "v19 false-positive class regressed: line ts must beat file mtime"


def test_grep_logs_since_counts_only_post_cutoff_lines(tmp_path: Path) -> None:
    """case id: p08.audit.grep.mixed_pre_post_cutoff_lines

    A live log file straddling the cutoff must contribute only the
    post-cutoff lines.
    """

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    fp = log_dir / "openakita.log"
    cutoff = _ts(2026, 5, 26, 7).timestamp()
    pre = [_line(_ts(2026, 5, 26, 6) + timedelta(minutes=k), "boom") for k in range(3)]
    post = [_line(_ts(2026, 5, 26, 8) + timedelta(minutes=k), "boom") for k in range(5)]
    fp.write_text("".join(pre + post), encoding="utf-8")

    n = grep_logs_since(cutoff, "boom", log_dirs=(log_dir,))
    assert n == 5


def test_grep_logs_since_skips_files_with_old_mtime(tmp_path: Path) -> None:
    """case id: p08.audit.grep.fast_path_skip_old_mtime

    Files whose mtime is older than the cutoff cannot hold post-cutoff
    lines, so the helper skips them without parsing -- a perf
    optimisation that does NOT introduce false negatives because the
    OpenAkita logger always touches the file when appending a new
    line.
    """

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    fp = log_dir / "stale.log"
    fp.write_text(_line(_ts(2026, 5, 26, 8), "boom"), encoding="utf-8")
    cutoff = _ts(2026, 5, 26, 7).timestamp()
    # Stamp file mtime well before the cutoff -- contradicts the
    # in-line timestamp on purpose to verify the fast-path skip.
    os.utime(fp, (cutoff - 3600, cutoff - 3600))

    n = grep_logs_since(cutoff, "boom", log_dirs=(log_dir,))
    assert n == 0


def test_grep_logs_since_continuation_line_inherits_anchor_ts(
    tmp_path: Path,
) -> None:
    """case id: p08.audit.grep.continuation_inherits_anchor

    A Python traceback emits one anchor line with a timestamp followed
    by N continuation lines without one. When the anchor is post-cutoff
    the continuation lines must count as post-cutoff hits too (they
    carry the same cause). When the anchor is pre-cutoff the
    continuations must NOT count.
    """

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    pre_anchor = _line(_ts(2026, 5, 26, 6), "Traceback (most recent call last):")
    pre_cont = "  File 'old.py', line 1, in pre\n    raise Exception('old boom')\n"
    post_anchor = _line(_ts(2026, 5, 26, 8), "Traceback (most recent call last):")
    post_cont = "  File 'new.py', line 1, in post\n    raise Exception('new boom')\n"
    fp = log_dir / "openakita.log"
    fp.write_text(pre_anchor + pre_cont + post_anchor + post_cont, encoding="utf-8")
    cutoff = _ts(2026, 5, 26, 7).timestamp()

    n_old = grep_logs_since(cutoff, "old boom", log_dirs=(log_dir,))
    n_new = grep_logs_since(cutoff, "new boom", log_dirs=(log_dir,))
    assert n_old == 0
    assert n_new == 1


def test_grep_logs_since_returns_zero_when_log_dir_missing(tmp_path: Path) -> None:
    """case id: p08.audit.grep.missing_dir_is_zero

    Pointing the helper at a non-existent directory must return 0
    rather than raising -- the v17-v19 callers always called the
    helper unconditionally and relied on the silent zero.
    """

    n = grep_logs_since(0.0, "boom", log_dirs=(tmp_path / "does-not-exist",))
    assert n == 0


def test_grep_logs_since_handles_non_utf8_bytes(tmp_path: Path) -> None:
    """case id: p08.audit.grep.utf8_replace

    Some legacy lines carry stray non-UTF-8 bytes. The helper must
    decode with ``errors='replace'`` and continue counting the
    well-formed neighbours.
    """

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    fp = log_dir / "messy.log"
    cutoff = _ts(2026, 5, 26, 7).timestamp()
    good = _line(_ts(2026, 5, 26, 8), "boom").encode("utf-8")
    bad = b"\xff\xfe garbage line, no timestamp\n"
    fp.write_bytes(good + bad + good)

    n = grep_logs_since(cutoff, "boom", log_dirs=(log_dir,))
    assert n == 2


# ---------------------------------------------------------------------------
# iter_log_lines_since
# ---------------------------------------------------------------------------


def test_iter_log_lines_since_yields_match_metadata(tmp_path: Path) -> None:
    """case id: p08.audit.iter.metadata_shape

    The iterator surface returns ``(path, line_no, ts, line)`` so
    callers needing the actual matched line can stay on this module
    instead of re-reading the file.
    """

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    fp = log_dir / "openakita.log"
    cutoff = _ts(2026, 5, 26, 7).timestamp()
    body = "".join(
        [
            _line(_ts(2026, 5, 26, 6), "skip-this"),
            _line(_ts(2026, 5, 26, 8), "match-1"),
            _line(_ts(2026, 5, 26, 9), "match-2"),
        ]
    )
    fp.write_text(body, encoding="utf-8")

    matches = list(iter_log_lines_since(cutoff, "match", log_dirs=(log_dir,)))
    assert len(matches) == 2
    paths = {m[0] for m in matches}
    assert paths == {fp}
    line_nos = [m[1] for m in matches]
    assert line_nos == [2, 3]
    assert all(m[2] is not None and m[2] > cutoff for m in matches)
    raw_msgs = [m[3] for m in matches]
    assert all("match" in m for m in raw_msgs)


@pytest.mark.parametrize(
    "raw,expected_year",
    [
        ("2026-01-01 00:00:00,000 - m - L - hi", 2026),
        ("2024-12-31 23:59:59 - m - L - hi", 2024),
    ],
)
def test_parse_line_timestamp_handles_edge_years(raw: str, expected_year: int) -> None:
    """case id: p08.audit.parse.edge_years

    The parser uses :func:`datetime.timestamp` which cannot represent
    years before the local-tz epoch on Windows (raises ``OSError``);
    that is fine for OpenAkita's logs which only span 2024+.
    """

    ts = parse_line_timestamp(raw)
    assert ts is not None
    assert datetime.fromtimestamp(ts).year == expected_year

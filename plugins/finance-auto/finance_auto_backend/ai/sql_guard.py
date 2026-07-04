"""SQL safety guard for the S7 (natural-language → SQL) scenario.

The LLM is asked to translate Chinese natural-language questions into
SQLite ``SELECT`` queries.  Before we ever execute the model's reply,
we run it through a regex-based whitelist:

* Only one statement.
* Top-level keyword must be ``SELECT`` (or ``WITH ... SELECT``).
* No DDL / DML / catalogue / pragma tokens (``UPDATE``, ``DELETE``,
  ``INSERT``, ``DROP``, ``ALTER``, ``ATTACH``, ``PRAGMA``, ``CREATE``,
  ``REPLACE``, ``EXEC``, ``EXECUTE``).
* All ``FROM`` / ``JOIN`` targets must be in the allow-list of finance
  plugin tables.
* A ``LIMIT 1000`` cap is enforced — we append it when missing.

The guard is intentionally regex-only; we do not pull in a real SQL
parser.  v0.2 §6 (S7 row) calls out that the surface is small (six
tables, no sub-queries deeper than one level for the M3 cut) and that
``regex + integration tests`` is the documented strategy.  An attacker
who finds a bypass triggers an audit row with ``outcome='error'`` and
no rows ever come back to the user.

Public API
----------

* :data:`ALLOWED_TABLES`        — frozenset of safe table names.
* :data:`FORBIDDEN_TOKENS`      — tuple of forbidden tokens (regex
  word-boundary matched).
* :data:`MAX_ROW_LIMIT`         — hard row cap (1000).
* :class:`SQLGuardResult`       — ``(safe, sql, errors)`` dataclass.
* :func:`validate_select_sql`   — returns a :class:`SQLGuardResult`.
* :func:`extract_sql_from_markdown` — strips the LLM's ```sql ...``` fence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "report_cells",
        "trial_balance_rows",
        "accounts",
        "note_documents",
        "peer_comparison_results",
        "peer_benchmarks",
        "organizations",
        "accounting_periods",
    }
)

FORBIDDEN_TOKENS: tuple[str, ...] = (
    "UPDATE",
    "DELETE",
    "INSERT",
    "DROP",
    "ALTER",
    "ATTACH",
    "DETACH",
    "PRAGMA",
    "CREATE",
    "REPLACE",
    "EXEC",
    "EXECUTE",
    "VACUUM",
    "REINDEX",
    "GRANT",
    "REVOKE",
    "TRUNCATE",
)

MAX_ROW_LIMIT = 1000

_FENCE_PATTERN = re.compile(r"```(?:sql|SQL)?\s*(.+?)```", re.S)
_SELECT_HEAD_PATTERN = re.compile(r"^\s*(?:WITH\b.+?\bSELECT\b|SELECT\b)", re.I | re.S)
_FROM_PATTERN = re.compile(r"(?i)\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)")
_JOIN_PATTERN = re.compile(r"(?i)\bJOIN\s+([A-Za-z_][A-Za-z0-9_]*)")
_LIMIT_PATTERN = re.compile(r"(?i)\bLIMIT\s+(\d+)")
_COMMENT_PATTERN = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_TRAILING_NOISE_PATTERN = re.compile(r";\s*\S+", re.S)


@dataclass
class SQLGuardResult:
    """Outcome of running a candidate string through the guard.

    ``sql`` is the *cleaned* statement that callers should execute
    when ``safe`` is True (with ``LIMIT 1000`` appended if missing).
    ``errors`` is empty on success.
    """

    safe: bool
    sql: str = ""
    errors: list[str] = field(default_factory=list)
    referenced_tables: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "safe": self.safe,
            "sql": self.sql,
            "errors": list(self.errors),
            "referenced_tables": list(self.referenced_tables),
        }


def extract_sql_from_markdown(text: str) -> str:
    """Return the SQL block from a markdown / fenced response.

    If the text contains a ```sql ... ``` fence we strip the fence and
    return the contents trimmed.  Otherwise the raw text (trimmed) is
    returned so callers can still validate the case where the LLM
    skipped the fence.
    """
    if not text:
        return ""
    match = _FENCE_PATTERN.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _strip_comments(sql: str) -> str:
    """Remove ``--`` line comments and ``/* */`` block comments."""
    return _COMMENT_PATTERN.sub(" ", sql)


def validate_select_sql(
    candidate: str,
    *,
    allowed_tables: frozenset[str] | None = None,
    max_rows: int = MAX_ROW_LIMIT,
) -> SQLGuardResult:
    """Validate a candidate SQL string and normalise / cap its LIMIT.

    Returns a :class:`SQLGuardResult` whose ``safe`` flag tells the
    caller whether the statement may be executed.  When ``safe`` is
    True the ``sql`` field carries the cleaned statement (one trailing
    semicolon, ``LIMIT max_rows`` appended when missing or shrunk if
    the LLM picked a higher number).
    """
    allowed = allowed_tables or ALLOWED_TABLES
    errors: list[str] = []
    sql_raw = (candidate or "").strip()
    if not sql_raw:
        return SQLGuardResult(safe=False, errors=["empty sql"])

    sql_no_fence = extract_sql_from_markdown(sql_raw)
    if not sql_no_fence:
        return SQLGuardResult(safe=False, errors=["empty sql"])

    sql = _strip_comments(sql_no_fence).strip().rstrip(";").strip()
    if not sql:
        return SQLGuardResult(safe=False, errors=["empty sql after stripping comments"])

    # Reject multi-statement payloads — any `;` followed by something
    # other than trailing whitespace / comments is a hard fail.
    if _TRAILING_NOISE_PATTERN.search(sql + ";"):
        # The replacement above always appends one `;` to test the tail;
        # the regex matches when there is real content after a `;`.
        # We re-test on the original cleaned sql to be sure.
        if ";" in sql:
            tail = sql.split(";", 1)[1].strip()
            if tail:
                errors.append("multiple statements detected")
    if ";" in sql:
        # Re-split: only the head (before first `;`) is considered.
        sql = sql.split(";", 1)[0].strip()

    if not _SELECT_HEAD_PATTERN.match(sql):
        errors.append("top-level keyword must be SELECT (or WITH ... SELECT)")

    upper_words = re.findall(r"[A-Za-z_]+", sql.upper())
    upper_word_set = set(upper_words)
    for tok in FORBIDDEN_TOKENS:
        if tok in upper_word_set:
            errors.append(f"forbidden token: {tok}")

    refs = [m.group(1) for m in _FROM_PATTERN.finditer(sql)]
    refs.extend(m.group(1) for m in _JOIN_PATTERN.finditer(sql))
    # Sub-queries / CTEs (``WITH x AS (...)``) may reference internal
    # aliases — we accept anything whose lowercase name is in the
    # allow-list OR whose lowercase name equals a CTE name declared
    # before the FROM/JOIN target.
    cte_names = {
        m.group(1).lower()
        for m in re.finditer(r"(?i)\bWITH\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", sql)
    }
    bad_tables: list[str] = []
    referenced_lc: list[str] = []
    for ref in refs:
        ref_lc = ref.lower()
        referenced_lc.append(ref_lc)
        if ref_lc in cte_names:
            continue
        if ref_lc not in {t.lower() for t in allowed}:
            bad_tables.append(ref)
    if bad_tables:
        errors.append(
            "table(s) not in allow-list: " + ", ".join(sorted(set(bad_tables)))
        )

    if errors:
        return SQLGuardResult(
            safe=False,
            sql="",
            errors=errors,
            referenced_tables=sorted(set(referenced_lc)),
        )

    cleaned = _enforce_limit(sql, max_rows=max_rows)
    return SQLGuardResult(
        safe=True,
        sql=cleaned,
        errors=[],
        referenced_tables=sorted(set(referenced_lc)),
    )


def _enforce_limit(sql: str, *, max_rows: int) -> str:
    """Append / clamp ``LIMIT <max_rows>`` so the caller never gets
    more than ``max_rows`` rows back.
    """
    match = _LIMIT_PATTERN.search(sql)
    if match is None:
        return f"{sql.rstrip()} LIMIT {max_rows}"
    try:
        current = int(match.group(1))
    except (TypeError, ValueError):
        return sql
    if current > max_rows:
        start, end = match.span(1)
        return sql[:start] + str(max_rows) + sql[end:]
    return sql


__all__ = [
    "ALLOWED_TABLES",
    "FORBIDDEN_TOKENS",
    "MAX_ROW_LIMIT",
    "SQLGuardResult",
    "extract_sql_from_markdown",
    "validate_select_sql",
]

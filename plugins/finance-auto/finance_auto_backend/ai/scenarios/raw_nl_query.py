"""S7 — 自然语言查询 (🔴 raw).

User types a Chinese natural-language question, the LLM translates
it into a SELECT statement, the guard accepts or rejects, and (when
``execute_sql=True``) we actually execute the cleaned SQL against
the plugin's SQLite and return the rows.

The sensitivity tier is ``raw`` because both the question and any
rows we ship back can carry account names / customer codes; routing
defaults to local LLMs (per :class:`FinanceAIRouter`) unless the
consent dialog explicitly authorises a cloud send.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .._base_paths import TEMPLATE_DIR
from ..sql_guard import (
    MAX_ROW_LIMIT,
    SQLGuardResult,
    extract_sql_from_markdown,
    validate_select_sql,
)
from ._base import ScenarioRunResult, execute_scenario

if TYPE_CHECKING:
    from ...routes import FinanceAutoService
    from ..router import FinanceAIRouter

logger = logging.getLogger(__name__)

SCENARIO_ID = "nl_query"
DEFAULT_LEVEL = "raw"
PROMPT_TEMPLATE = (TEMPLATE_DIR / "raw_nl_query.md.j2").read_text(encoding="utf-8")


def build_payload(
    question: str,
    *,
    org_id: str | None = None,
    period_filter_hint: str | None = None,
) -> dict[str, Any]:
    return {
        "question": str(question or "").strip(),
        "org_id": str(org_id or ""),
        "period_filter_hint": str(period_filter_hint or ""),
    }


def _sql_parser(text: str) -> dict[str, Any]:
    """Response parser: extract & validate the SQL block.

    Always returns a dict so the audit row + ScenarioRunResult.parsed
    can carry the validation outcome regardless of whether the SQL
    is safe.
    """
    sql_candidate = extract_sql_from_markdown(text or "")
    guard = validate_select_sql(sql_candidate)
    return {
        "raw_text": text or "",
        "sql_candidate": sql_candidate,
        "guard": guard.to_dict(),
    }


async def execute_safe_query(
    service: FinanceAutoService,
    sql: str,
    params: tuple[Any, ...] = (),
    *,
    max_rows: int = MAX_ROW_LIMIT,
) -> list[dict[str, Any]]:
    """Run a guard-approved SELECT and return up to ``max_rows`` rows.

    Caller is responsible for passing a SQL string that already
    passed :func:`validate_select_sql`.  We re-validate here as a
    second line of defence so a buggy caller cannot accidentally
    bypass the guard.
    """
    guard = validate_select_sql(sql, max_rows=max_rows)
    if not guard.safe:
        raise ValueError(
            "execute_safe_query refused unsafe SQL: " + "; ".join(guard.errors)
        )
    rows_out: list[dict[str, Any]] = []
    async with service.db.conn.execute(guard.sql, params) as cur:
        rows = await cur.fetchmany(max_rows)
        columns = [d[0] for d in (cur.description or [])]
    for row in rows:
        # Row objects from aiosqlite behave like sqlite3.Row -- we
        # materialise to a plain dict so JSON serialisation is cheap.
        try:
            rows_out.append({col: row[col] for col in columns})
        except Exception:  # noqa: BLE001
            rows_out.append({col: row[i] for i, col in enumerate(columns)})
    return rows_out


async def run(
    service: FinanceAutoService,
    *,
    payload: dict,
    org_id: str | None = None,
    router: FinanceAIRouter | None = None,
    auto_decision: str | None = None,
    execute_sql: bool = False,
) -> ScenarioRunResult:
    """Translate the question + (optionally) execute the SQL.

    The scenario itself only owns the LLM round-trip and the guard
    inspection.  Rows are appended to ``ScenarioRunResult.parsed`` so
    the REST endpoint can return them without a second LLM call.
    """
    result = await execute_scenario(
        service,
        scenario_id=SCENARIO_ID,
        level=DEFAULT_LEVEL,
        payload=payload,
        prompt_template=PROMPT_TEMPLATE,
        parser=_sql_parser,
        router=router,
        org_id=org_id,
        auto_decision=auto_decision,
    )
    if result.outcome != "success" or not isinstance(result.parsed, dict):
        return result

    guard_dict = result.parsed.get("guard") or {}
    safe = bool(guard_dict.get("safe"))
    sql = str(guard_dict.get("sql") or "")
    rows: list[dict[str, Any]] | None = None
    exec_error: str | None = None
    if safe and execute_sql and sql:
        try:
            rows = await execute_safe_query(service, sql)
        except Exception as exc:  # noqa: BLE001
            exec_error = f"{type(exc).__name__}: {exc}"

    result.parsed = {
        **result.parsed,
        "safe": safe,
        "sql": sql,
        "validation_errors": list(guard_dict.get("errors") or []),
        "referenced_tables": list(guard_dict.get("referenced_tables") or []),
        "executed": rows is not None,
        "rows": rows,
        "execute_error": exec_error,
    }
    return result


__all__ = [
    "DEFAULT_LEVEL",
    "MAX_ROW_LIMIT",
    "PROMPT_TEMPLATE",
    "SCENARIO_ID",
    "SQLGuardResult",
    "build_payload",
    "execute_safe_query",
    "run",
]

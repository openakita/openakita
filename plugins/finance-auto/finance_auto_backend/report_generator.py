"""Compute ReportInstance + ReportCell rows from a YAML template.

This is the brains of the M1 W2 Stage 4 report pipeline.  Given:

* a ``LoadedTemplate`` (already validated by the YAML loader),
* a list of ``trial_balance_rows`` decrypted into a flat dict-shape,

it walks every rule and produces a ``GeneratedReport`` -- a tuple of
(ReportInstance, [ReportCell]).

Formula support is intentionally minimal in W2: the four DSL functions used
by the four shipped templates are implemented inline.  Anything more
elaborate is rejected with a warning so the cell falls back to value=0 and
the YAML team gets a clear "TODO: M1 W3 formula engine" message.

Scope (W2):

* ``ACCOUNT('<code>', '<balance_kind>')``
* ``ACCOUNT_GROUP(['c1','c2',...], '<balance_kind>')``
* ``LINE('<reference_code>')``           -- references a previously
                                            computed line in this report
* ``SUM_LINES(<from_line_no>, <to_line_no>)``
* ``REPORT_PREV(...)``                   -- always returns 0 with a warning
                                            (cross-period not in W2)

All evaluation goes through :func:`_eval_formula` which strips the Jinja
``{{ ... }}`` wrapper, then ``ast.literal_eval`` is *not* sufficient (we
need function calls), so we whitelist a small AST and reject anything else.
"""

from __future__ import annotations

import ast
import logging
import operator
import re
from dataclasses import dataclass, field
from typing import Any

from .config.yaml_loader import TBD_SENTINEL, LoadedTemplate, ReportRule
from .models import ReportCell, ReportInstance

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass
class GeneratedReport:
    instance: ReportInstance
    cells: list[ReportCell] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Aggregation primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrialBalanceLine:
    """A balance-table row reduced to the columns the generator cares about."""

    id: str
    full_code: str
    parent_code: str
    child_code: str | None
    account_name: str | None
    opening_debit: float
    opening_credit: float
    period_debit: float
    period_credit: float
    closing_debit: float
    closing_credit: float


def _balance_value(line: TrialBalanceLine, kind: str) -> float:
    """Resolve a v0.1-style ``balance_kind`` to a numeric value."""
    if kind == "closing_net":
        return line.closing_debit - line.closing_credit
    if kind == "closing_debit":
        return line.closing_debit
    if kind == "closing_credit":
        return line.closing_credit
    if kind == "ytd_net":
        return line.period_debit - line.period_credit
    if kind == "ytd_debit":
        return line.period_debit
    if kind == "ytd_credit":
        return line.period_credit
    raise ValueError(f"unsupported balance_kind: {kind}")


def _filter_by_pattern(lines: list[TrialBalanceLine], pattern: str) -> list[TrialBalanceLine]:
    if not pattern:
        return []
    rx = re.compile(pattern)
    return [ln for ln in lines if rx.match(ln.full_code) or rx.match(ln.parent_code)]


def _filter_exact_or_prefix(
    lines: list[TrialBalanceLine], code: str
) -> list[TrialBalanceLine]:
    """Exact match by parent_code OR full_code, plus prefix match for sub-accounts."""
    return [
        ln
        for ln in lines
        if ln.parent_code == code
        or ln.full_code == code
        or ln.full_code.startswith(code + ".")
    ]


def _aggregate(
    lines: list[TrialBalanceLine],
    balance_kind: str,
) -> tuple[float, list[str]]:
    total = 0.0
    source_ids: list[str] = []
    for line in lines:
        if balance_kind == "subaccount_debit_positive":
            v = max(line.closing_debit - line.closing_credit, 0.0)
        elif balance_kind == "subaccount_credit_positive":
            v = max(line.closing_credit - line.closing_debit, 0.0)
        else:
            v = _balance_value(line, balance_kind)
        if v == 0:
            continue
        total += v
        source_ids.append(line.id)
    return total, source_ids


# ---------------------------------------------------------------------------
# Formula evaluation
# ---------------------------------------------------------------------------


_FORMULA_WRAP_RE = re.compile(r"^\s*{{\s*(.*?)\s*}}\s*$", re.DOTALL)


def _strip_jinja(formula: str) -> str:
    m = _FORMULA_WRAP_RE.match(formula)
    return m.group(1) if m else formula


# Only these AST nodes are allowed in a formula expression.
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Num,  # Python 3.7-compat (deprecated in 3.12 but still parseable)
    ast.Name,
    ast.Load,
    ast.Call,
    ast.List,
    ast.Tuple,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.USub,
    ast.UAdd,
)

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}


def _eval_formula(
    expression: str,
    *,
    balance_lines: list[TrialBalanceLine],
    line_values: dict[str, float],
    line_no_values: dict[int, float],
    warnings: list[str],
    source_collector: list[str],
) -> float:
    """Evaluate a YAML formula in a tightly bounded environment."""

    expr_src = _strip_jinja(expression)
    try:
        node = ast.parse(expr_src, mode="eval")
    except SyntaxError as exc:
        warnings.append(f"formula parse error: {exc!s} (expr={expr_src!r})")
        return 0.0

    for sub in ast.walk(node):
        if not isinstance(sub, _ALLOWED_NODES):
            warnings.append(
                f"unsupported expression node {type(sub).__name__}; "
                f"formula returned 0 (expr={expr_src!r})"
            )
            return 0.0

    def account(code: str, kind: str) -> float:
        matched = _filter_exact_or_prefix(balance_lines, code)
        total, ids = _aggregate(matched, kind)
        source_collector.extend(ids)
        return total

    def account_group(codes: list[str], kind: str) -> float:
        matched: list[TrialBalanceLine] = []
        for c in codes:
            matched.extend(_filter_exact_or_prefix(balance_lines, c))
        total, ids = _aggregate(matched, kind)
        source_collector.extend(ids)
        return total

    def line(reference_code: str) -> float:
        if reference_code not in line_values:
            warnings.append(
                f"LINE('{reference_code}') referenced before its rule was "
                "evaluated; using 0"
            )
            return 0.0
        return line_values[reference_code]

    def sum_lines(start: int, end: int) -> float:
        total = 0.0
        for line_no, val in line_no_values.items():
            if start <= line_no <= end:
                total += val
        return total

    def report_prev(*args: Any, **kwargs: Any) -> float:
        warnings.append(
            "REPORT_PREV() not implemented in M1 W2; cell value is 0"
        )
        return 0.0

    funcs: dict[str, Any] = {
        "ACCOUNT": account,
        "ACCOUNT_GROUP": account_group,
        "LINE": line,
        "SUM_LINES": sum_lines,
        "REPORT_PREV": report_prev,
    }

    def _eval(n: ast.AST) -> Any:
        if isinstance(n, ast.Expression):
            return _eval(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Num):  # 3.7 compat
            return n.n
        if isinstance(n, ast.UnaryOp):
            v = _eval(n.operand)
            return -v if isinstance(n.op, ast.USub) else +v
        if isinstance(n, ast.BinOp):
            op = _BINOPS.get(type(n.op))
            if op is None:
                warnings.append(f"unsupported binop {type(n.op).__name__}")
                return 0.0
            try:
                return op(_eval(n.left), _eval(n.right))
            except ZeroDivisionError:
                return 0.0
        if isinstance(n, ast.Name):
            warnings.append(f"unbound identifier {n.id!r}; using 0")
            return 0.0
        if isinstance(n, ast.List | ast.Tuple):
            return [_eval(elt) for elt in n.elts]
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name):
                warnings.append("unsupported call target; using 0")
                return 0.0
            fn = funcs.get(n.func.id)
            if fn is None:
                warnings.append(f"unknown function {n.func.id}; using 0")
                return 0.0
            args = [_eval(a) for a in n.args]
            return fn(*args)
        warnings.append(f"unsupported node {type(n).__name__}; using 0")
        return 0.0

    try:
        result = _eval(node)
        return float(result) if result is not None else 0.0
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(f"formula runtime error: {exc!r}; using 0")
        return 0.0


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


def generate_report(
    *,
    template: LoadedTemplate,
    org_id: str,
    period_id: str,
    accounting_standard: str,
    balance_lines: list[TrialBalanceLine],
    source_import_id: str | None,
) -> GeneratedReport:
    """Walk the template's rules and produce cells.

    The function is pure (no DB I/O) so it is trivially unit-testable.
    """
    instance = ReportInstance.new(
        org_id=org_id,
        period_id=period_id,
        sheet_kind=template.sheet_kind,  # type: ignore[arg-type]
        accounting_standard=accounting_standard,  # type: ignore[arg-type]
        template_id=template.template_id,
        template_version=template.version,
        source_import_id=source_import_id,
    )
    cells: list[ReportCell] = []
    warnings: list[str] = [
        f"YAML warning: {w.reference_code}.{w.field}: {w.message}"
        for w in template.warnings
    ]
    line_values: dict[str, float] = {}
    line_no_values: dict[int, float] = {}

    for rule in template.rules:
        cell, value, source_ids = _resolve_rule(
            rule=rule,
            balance_lines=balance_lines,
            line_values=line_values,
            line_no_values=line_no_values,
            warnings=warnings,
            report_id=instance.id,
        )
        cells.append(cell)
        line_values[rule.reference_code] = value
        if rule.target_line_no:
            line_no_values[rule.target_line_no] = value
        if source_ids:
            cell.source_rows = source_ids

    instance.cell_count = len(cells)
    instance.warnings = warnings
    return GeneratedReport(instance=instance, cells=cells, warnings=warnings)


def _resolve_rule(
    *,
    rule: ReportRule,
    balance_lines: list[TrialBalanceLine],
    line_values: dict[str, float],
    line_no_values: dict[int, float],
    warnings: list[str],
    report_id: str,
) -> tuple[ReportCell, float, list[str]]:
    is_tbd = rule.code == TBD_SENTINEL
    sources: list[str] = []
    value = 0.0

    if rule.data_source == "section":
        value = 0.0
    elif is_tbd:
        warnings.append(
            f"{rule.reference_code} ({rule.target_label}) is TBD; rendered as 0"
        )
        value = 0.0
    elif rule.data_source == "account":
        if rule.account_filter and rule.balance_kind:
            matched = _filter_by_pattern(balance_lines, rule.account_filter)
            total, sources = _aggregate(matched, rule.balance_kind)
            value = total * (rule.sign or 1)
        else:
            warnings.append(
                f"{rule.reference_code} data_source=account but missing "
                "account_filter / balance_kind"
            )
    elif rule.data_source == "formula":
        if not rule.formula:
            warnings.append(
                f"{rule.reference_code} data_source=formula but no formula provided"
            )
        else:
            value = _eval_formula(
                rule.formula,
                balance_lines=balance_lines,
                line_values=line_values,
                line_no_values=line_no_values,
                warnings=warnings,
                source_collector=sources,
            ) * (rule.sign or 1)
    elif rule.data_source == "cross_year":
        warnings.append(
            f"{rule.reference_code} cross_year not supported in M1 W2; rendered as 0"
        )
        value = 0.0
    else:
        warnings.append(
            f"{rule.reference_code} unhandled data_source={rule.data_source!r}"
        )

    cell = ReportCell(
        id=f"cel_{rule.reference_code}_{report_id[-6:]}",
        report_id=report_id,
        reference_code=rule.reference_code,
        target_line_no=rule.target_line_no,
        target_label=rule.target_label,
        indent_level=rule.indent_level,
        data_source=rule.data_source,
        code=rule.code,
        value=round(value, 2),
        sign=rule.sign or 1,
        is_total=rule.is_total,
        is_tbd=is_tbd,
        formula=rule.formula,
        notes=rule.notes,
        source_rows=sorted(set(sources)),
    )
    return cell, value, cell.source_rows

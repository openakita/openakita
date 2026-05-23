"""Cross-period (年度结转) validator — W3 Stage 3.

Compares a *prior* trial-balance import (e.g. last year's closing balances)
to a *current* trial-balance import (this year's opening balances) account
by account, and grades the resulting differences using the four-bucket
scheme from v0.3 Part Biz §4.3:

* ``exact``     — values literally equal (delta == 0).
* ``tolerance`` — 0 < |delta| < ``tolerance`` (default ¥1; rounding noise).
* ``warning``   — tolerance ≤ |delta| < ``warn_threshold`` (default ¥100).
* ``error``     — |delta| ≥ warn_threshold; the validator may emit a
  ``ParseIssue`` (issue_type=cross_period_mismatch, severity=must_fix) so
  the W3 Stage 1 triage UI surfaces it.

This module is *pure*: it operates on already-decoded balance rows and
returns a ``CrossPeriodResult`` dataclass.  Persistence + ParseIssue
emission lives in ``cross_period_routes`` to keep the validator easily
unit-testable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class BalanceSnapshot:
    """Minimal view of one trial-balance row the validator cares about."""

    full_code: str
    account_name: str | None
    closing_debit: float = 0.0
    closing_credit: float = 0.0
    opening_debit: float = 0.0
    opening_credit: float = 0.0


@dataclass
class CrossPeriodDiff:
    full_code: str
    account_name: str | None
    prior_closing: float
    current_opening: float
    delta: float
    severity: Literal["exact", "tolerance", "warning", "error"]
    side: Literal["debit", "credit", "net"] = "net"
    note: str | None = None


@dataclass
class CrossPeriodResult:
    tolerance: float
    warn_threshold: float
    differences: list[CrossPeriodDiff] = field(default_factory=list)
    exact_count: int = 0
    tolerance_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    total_accounts: int = 0

    def merged_must_fix(self) -> list[CrossPeriodDiff]:
        return [d for d in self.differences if d.severity == "error"]


def _net_value(rows_debit: float, rows_credit: float) -> float:
    """Return the *net* balance value (debit minus credit).  Used so that
    we can compare on a single number even when an account is reported with
    both debit and credit columns populated."""
    return float(rows_debit or 0) - float(rows_credit or 0)


def _grade(delta: float, tolerance: float, warn_threshold: float) -> str:
    abs_d = abs(delta)
    if abs_d == 0.0:
        return "exact"
    if abs_d < tolerance:
        return "tolerance"
    if abs_d < warn_threshold:
        return "warning"
    return "error"


def validate_cross_period(
    *,
    prior: Iterable[BalanceSnapshot],
    current: Iterable[BalanceSnapshot],
    tolerance: float = 1.0,
    warn_threshold: float = 100.0,
) -> CrossPeriodResult:
    """Compare prior-period **closing** vs current-period **opening** balances.

    Accounts present in only one period are reported with the missing side
    treated as 0 — they almost always indicate a chart-of-accounts mismatch
    and deserve a ``warning`` (small) or ``error`` (large) grade depending
    on magnitude.

    The algorithm runs in O(n) on each side; it indexes by ``full_code`` and
    then walks the union of keys to keep ordering deterministic.
    """
    prior_idx: dict[str, BalanceSnapshot] = {}
    current_idx: dict[str, BalanceSnapshot] = {}
    for row in prior:
        prior_idx[row.full_code] = row
    for row in current:
        current_idx[row.full_code] = row

    all_codes = sorted(set(prior_idx) | set(current_idx))
    out = CrossPeriodResult(tolerance=tolerance, warn_threshold=warn_threshold)
    for code in all_codes:
        p = prior_idx.get(code)
        c = current_idx.get(code)
        name = (c.account_name if c else None) or (p.account_name if p else None)
        prior_net = _net_value(p.closing_debit, p.closing_credit) if p else 0.0
        current_net = _net_value(c.opening_debit, c.opening_credit) if c else 0.0
        delta = current_net - prior_net
        severity = _grade(delta, tolerance, warn_threshold)
        note: str | None = None
        if p is None:
            note = "新增科目：本期期初出现，去年期末无此科目"
            if severity == "exact":
                severity = "warning"
        elif c is None:
            note = "缺失科目：去年期末有余额，本期期初未出现"
            if severity == "exact":
                severity = "warning"

        diff = CrossPeriodDiff(
            full_code=code,
            account_name=name,
            prior_closing=round(prior_net, 2),
            current_opening=round(current_net, 2),
            delta=round(delta, 2),
            severity=severity,  # type: ignore[arg-type]
            side="net",
            note=note,
        )
        out.differences.append(diff)
        if severity == "exact":
            out.exact_count += 1
        elif severity == "tolerance":
            out.tolerance_count += 1
        elif severity == "warning":
            out.warning_count += 1
        else:
            out.error_count += 1
        out.total_accounts += 1
    return out

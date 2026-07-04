"""Six-class parse-issue detector (M1 W3 Stage 1, v0.2 Part 1 §2).

Given a freshly parsed :class:`ParsedRow` list (the output of the W1 three-tier
parser), the detector walks every row and produces a :class:`DetectedIssue`
for each rule that fires.  Issues are intentionally produced as a flat list,
ordered by ``(row_index, issue_type)``, so the caller can persist them in a
single SQL ``executemany`` and so the UI can render them in a stable order.

Rule catalogue (mirrors v0.2 Part 1 §2.1)
-----------------------------------------

================  =============  =====================================================
Issue type        Severity        Rule
================  =============  =====================================================
unknown_code      must_fix        ``parent_code`` doesn't match any known prefix and
                                  the leading-digit form doesn't look like a Chinese
                                  GAAP class (1xxx assets / 2xxx liabilities / 3xxx
                                  joint / 4xxx equity / 5xxx cost / 6xxx revenue /
                                  expense / 7xxx tax).
name_ambiguity    suggested       Account name contains a dash + free-text suffix
                                  (``应收-保证金``) or starts with non-canonical
                                  keywords that aren't in the small-enterprise GAAP
                                  master list.
direction_anomaly must_fix        The row's non-zero closing balance is on the wrong
                                  side for that account class (e.g. ``1001`` has
                                  closing_credit > 0 with zero closing_debit).
debit_credit_imbalance must_fix   ``|opening_debit + period_debit - opening_credit
                                  - period_credit - (closing_debit - closing_credit)|
                                  > 0.01``.
field_missing     must_fix        ``account_name`` is empty / None for a non-zero
                                  balance row.
format_corrupt    suggested       ``raw_code`` contains characters the normalizer
                                  could not strip (full-width punctuation, mixed
                                  Chinese-digits, etc.).
================  =============  =====================================================

The detector is **pure** — no DB, no logging side-effects, no warnings list.
The caller decides how to surface results.  This keeps the unit tests trivial
and makes the function safe to call in a tight loop from the upload route.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..parsers.xls_parser import ParsedRow


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DetectedIssue:
    """One issue produced by the L1 detector.

    Maps almost 1:1 onto :class:`models.ParseIssue`; the route layer only adds
    ``id`` / ``created_at`` / ``import_id`` / ``period_id`` and writes it.
    """

    row_index: int
    sheet_name: str
    column_name: str
    issue_type: str
    severity: str
    pattern_signature: str
    original_data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Known account-class prefixes (small-enterprise + general-enterprise union).
# Used by ``_is_known_class`` to flag truly unknown codes.
# ---------------------------------------------------------------------------

_KNOWN_CLASS_PREFIXES: tuple[str, ...] = tuple(str(i) for i in range(1, 8))
"""Top-level class digits 1-7 cover assets through tax expenses; codes
starting with 8/9 are non-standard and treated as ``unknown_code``."""

_CANONICAL_NAME_HINTS: tuple[str, ...] = (
    "现金", "银行", "应收", "应付", "预收", "预付", "存货", "固定", "无形",
    "累计折旧", "在建工程", "长期", "短期", "实收", "盈余", "未分配",
    "营业", "销售", "管理", "财务", "投资", "资本", "利润", "费用", "成本",
    "税金", "应交", "其他", "结存", "材料", "递延", "商誉", "信用", "减值",
    "公允", "套期", "合同", "持有", "使用权", "租赁",
)


# Plausible ranges of account codes that always belong on a particular side.
# Source: 财政部《企业会计准则 - 应用指南》附录 (2018).
# (prefix_pattern, required_side)  where side ∈ {"debit", "credit"}.
_SIDE_RULES: tuple[tuple[str, str], ...] = (
    (r"^10",  "debit"),    # 1001 库存现金, 1002 银行存款 ... — asset
    (r"^11",  "debit"),    # 应收类 — asset
    (r"^12",  "debit"),    # 存货预付类 — asset
    (r"^14",  "debit"),    # 存货 — asset
    (r"^15",  "debit"),    # 长期股权投资 — asset
    (r"^16",  "debit"),    # 固定资产 / 商誉 ...  — asset
    (r"^17",  "debit"),    # 无形资产 — asset
    (r"^18",  "debit"),    # 待摊费用 / 递延所得税资产 — asset
    (r"^20",  "credit"),   # 短借 — liability
    (r"^22",  "credit"),   # 应付 / 预收 / 合同负债 — liability
    (r"^25",  "credit"),   # 长借 — liability
    (r"^27",  "credit"),   # 长期应付 — liability
    (r"^29",  "credit"),   # 递延所得税负债 — liability
    (r"^40",  "credit"),   # 实收资本 — equity
    (r"^41",  "credit"),   # 盈余/未分配 — equity
    (r"^50",  "debit"),    # 生产成本 / 制造费用 — cost-like (debit)
    (r"^53",  "debit"),    # 开发支出 — cost-like
    (r"^60",  "credit"),   # 主营业务收入 / 其他业务收入 — revenue
    (r"^61",  "credit"),   # 投资收益 — revenue
    (r"^64",  "debit"),    # 主营业务成本 — expense
    (r"^66",  "debit"),    # 营业费用 / 信用减值 — expense
    (r"^67",  "debit"),    # 资产减值损失 — expense
)

# Specific 4-digit accounts that legitimately swing both sides — used to
# *exclude* them from the direction-anomaly rule.  Two classes:
#
# (a) Net contra-asset / contra-liability accounts (累计折旧 / 累计摊销 …)
#     whose closing side flips depending on whether amortisation has
#     overshot the gross book value.
# (b) Auxiliary-aware accounts whose sub-account (customer / supplier)
#     legitimately sits on the *opposite* side of the parent's normal
#     direction.  v0.1 §5.2 routes 1122 client-credit → 2203 预收 and
#     1123 supplier-credit → 2202 应付 — those reclassifications must NOT
#     be flagged as anomalies by W3 Stage 1; the report generator handles
#     them at L2.  Sub-account-level credit balances on AR/AP families are
#     therefore expected behaviour, not anomalies.
_BIDIRECTIONAL_CODES: frozenset[str] = frozenset({
    # contra / equity / tax accounts
    "2221", "1601", "1602", "1701", "1702", "1801", "4001", "4002", "4101",
    "4103", "4104",
    # AR / AP family — credit-side rows are reclassification candidates
    "1122", "1123", "1131", "1132", "1221",
    "2202", "2203", "2241",
    # 应收/应付票据 also see both sides during endorsement chains
    "1121", "2201",
})


# ---------------------------------------------------------------------------
# Pattern signature
# ---------------------------------------------------------------------------


_NORMAL_WHITESPACE = re.compile(r"\s+")


def _normalize_name(s: str) -> str:
    if not s:
        return ""
    return _NORMAL_WHITESPACE.sub(" ", s).strip()


def make_pattern_signature(issue_type: str, original: dict[str, Any]) -> str:
    """Build a stable fingerprint used by the learning-sample matcher.

    The fingerprint is short, deterministic, and *not* PII-bearing: the
    account code, the issue family and a normalized name slice are enough to
    cluster recurring problems without leaking customer-level detail.
    """
    if issue_type == "unknown_code":
        code_prefix = (original.get("parent_code") or original.get("raw_code") or "")[:4]
        return f"unknown_code|prefix={code_prefix}|name={_normalize_name(original.get('account_name', ''))[:24]}"
    if issue_type == "name_ambiguity":
        return f"name_amb|code={original.get('parent_code')}|name={_normalize_name(original.get('account_name', ''))[:32]}"
    if issue_type == "direction_anomaly":
        return f"direction|code={original.get('parent_code')}|expected={original.get('expected_side')}"
    if issue_type == "debit_credit_imbalance":
        return f"imbalance|code={original.get('parent_code')}"
    if issue_type == "field_missing":
        return f"missing|code={original.get('parent_code')}|field={original.get('column_name')}"
    if issue_type == "format_corrupt":
        sample = (original.get("raw_value") or original.get("raw_code") or "")[:12]
        return f"format|sample={sample}"
    # default fallback — keep deterministic JSON hash
    return f"{issue_type}|" + json.dumps(original, sort_keys=True, ensure_ascii=False)[:64]


# ---------------------------------------------------------------------------
# Rule helpers
# ---------------------------------------------------------------------------


_CANONICAL_PARENT_RE = re.compile(r"^\d{4}$")
"""Chinese GAAP 4-digit master codes."""


def _is_known_class(parent_code: str) -> bool:
    """Return True if ``parent_code`` is a 4-digit number whose leading
    digit is in the recognised 1-7 class range *and* the second digit is
    plausible (avoids ``8888`` / ``9999`` style placeholders)."""
    if not _CANONICAL_PARENT_RE.match(parent_code):
        return False
    if parent_code[0] not in _KNOWN_CLASS_PREFIXES:
        return False
    # Skip extremely unlikely codes like 1999 (no real top-level child).
    if parent_code in {"1999", "2999", "3999", "4999", "5999", "6999", "7999"}:
        return False
    return True


def _required_side(parent_code: str) -> str | None:
    """Return ``'debit'`` / ``'credit'`` if the prefix mandates a side, else
    ``None`` (account is bidirectional)."""
    if parent_code in _BIDIRECTIONAL_CODES:
        return None
    for pat, side in _SIDE_RULES:
        if re.match(pat, parent_code):
            return side
    return None


def _is_ambiguous_name(name: str | None) -> bool:
    if not name:
        return False
    n = name.strip()
    if "-" in n or "－" in n or "—" in n:
        # 名称含分隔符且无标准关键字 → 视为歧义
        return not any(h in n for h in _CANONICAL_NAME_HINTS)
    # 全英文或包含问号/百分号等 → 非典型
    if re.search(r"[?％%@]", n):
        return True
    return False


def _format_corrupt(raw: str) -> bool:
    if not raw:
        return False
    # 含全角字符 / 中文标点 / 字母 → 视为格式破损
    if re.search(r"[Ａ-Ｚａ-ｚ０-９，。；：、（）]", raw):
        return True
    if any(ch.isalpha() for ch in raw if ord(ch) < 128):
        return True
    return False


def _imbalance_amount(row: ParsedRow) -> float:
    """Return signed deviation of the row-level identity.

    ``|opening_net + period_net - closing_net|`` should be ~0 for a clean
    row.  We use closing - opening - period to make the sign meaningful.
    """
    opening = row.opening_debit - row.opening_credit
    period = row.period_debit - row.period_credit
    closing = row.closing_debit - row.closing_credit
    return round(closing - opening - period, 2)


def _row_has_balance(row: ParsedRow) -> bool:
    return any(
        v not in (0, 0.0, None)
        for v in (
            row.opening_debit, row.opening_credit,
            row.period_debit, row.period_credit,
            row.closing_debit, row.closing_credit,
        )
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def detect_parse_issues(
    rows: Iterable[ParsedRow],
    *,
    sheet_name: str = "余额表",
) -> list[DetectedIssue]:
    """Run the 6-class rule set against ``rows`` and return all findings.

    The function never raises on a bad row — it just emits one or more
    issues describing what went wrong, keeps walking, and returns the
    aggregated list.
    """
    out: list[DetectedIssue] = []

    for row in rows:
        # Snapshot the row contents the issue will reference.  We keep this
        # compact on purpose so the JSON payload that hits SQLite stays
        # small and remains decryptable end-to-end.
        snapshot = {
            "raw_code": row.raw_code,
            "parent_code": row.parent_code,
            "child_code": row.child_code,
            "full_code": row.full_code,
            "account_name": row.account_name,
            "aux_text": row.aux_text,
            "opening_debit": row.opening_debit,
            "opening_credit": row.opening_credit,
            "period_debit": row.period_debit,
            "period_credit": row.period_credit,
            "closing_debit": row.closing_debit,
            "closing_credit": row.closing_credit,
        }

        # ISS-006 — format corruption (run first so the snapshot can show
        # the offending raw value).
        if _format_corrupt(row.raw_code or ""):
            data = {**snapshot, "raw_value": row.raw_code}
            out.append(DetectedIssue(
                row_index=row.row_index,
                sheet_name=sheet_name,
                column_name="raw_code",
                issue_type="format_corrupt",
                severity="suggested",
                pattern_signature=make_pattern_signature("format_corrupt", data),
                original_data=data,
            ))

        # ISS-001 — unknown account class.
        if not _is_known_class(row.parent_code):
            data = {**snapshot}
            out.append(DetectedIssue(
                row_index=row.row_index,
                sheet_name=sheet_name,
                column_name="parent_code",
                issue_type="unknown_code",
                severity="must_fix",
                pattern_signature=make_pattern_signature("unknown_code", data),
                original_data=data,
            ))
            # Don't pile on with the direction check for a code we don't know.
            continue

        # ISS-005 — missing fields on a non-zero balance row.
        if _row_has_balance(row) and not (row.account_name and row.account_name.strip()):
            data = {**snapshot, "column_name": "account_name"}
            out.append(DetectedIssue(
                row_index=row.row_index,
                sheet_name=sheet_name,
                column_name="account_name",
                issue_type="field_missing",
                severity="must_fix",
                pattern_signature=make_pattern_signature("field_missing", data),
                original_data=data,
            ))

        # ISS-002 — name ambiguity (only for known codes).
        if _is_ambiguous_name(row.account_name):
            data = {**snapshot}
            out.append(DetectedIssue(
                row_index=row.row_index,
                sheet_name=sheet_name,
                column_name="account_name",
                issue_type="name_ambiguity",
                severity="suggested",
                pattern_signature=make_pattern_signature("name_ambiguity", data),
                original_data=data,
            ))

        # ISS-003 — direction anomaly.
        required = _required_side(row.parent_code)
        if required is not None and _row_has_balance(row):
            net_debit = row.closing_debit - row.closing_credit
            wrong_side = (
                (required == "debit" and net_debit < -0.01)
                or (required == "credit" and net_debit > 0.01)
            )
            if wrong_side:
                data = {**snapshot, "expected_side": required}
                out.append(DetectedIssue(
                    row_index=row.row_index,
                    sheet_name=sheet_name,
                    column_name="closing_balance",
                    issue_type="direction_anomaly",
                    severity="must_fix",
                    pattern_signature=make_pattern_signature("direction_anomaly", data),
                    original_data=data,
                ))

        # ISS-004 — debit/credit imbalance.
        delta = _imbalance_amount(row)
        if abs(delta) > 0.01 and _row_has_balance(row):
            data = {**snapshot, "imbalance_delta": delta}
            out.append(DetectedIssue(
                row_index=row.row_index,
                sheet_name=sheet_name,
                column_name="balance_check",
                issue_type="debit_credit_imbalance",
                severity="must_fix",
                pattern_signature=make_pattern_signature("debit_credit_imbalance", data),
                original_data=data,
            ))

    out.sort(key=lambda i: (i.row_index, i.issue_type))
    return out


__all__ = [
    "DetectedIssue",
    "detect_parse_issues",
    "make_pattern_signature",
]

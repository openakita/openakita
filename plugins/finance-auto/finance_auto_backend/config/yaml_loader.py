"""YAML report-template loader with structural validation.

The four templates shipped under ``templates/reports/`` are the contract
between the design team's accounting rules and the generator pipeline.  This
loader:

* Resolves the v0.3 ``extends:`` chain (a child template overrides parent
  rules by ``reference_code``).
* Coerces every rule into a typed :class:`ReportRule`.
* Validates the rule's ``code`` field: it must match
  :data:`CODE_PATTERN` *or* equal ``"TBD"``.  TBD lines emit a
  :class:`YamlValidationWarning` (collected on the LoadedTemplate) instead of
  a hard failure -- the generator still emits these lines but flags them in
  the audit trail.
* Validates ``data_source`` is one of the recognised values.
* Validates ``balance_kind`` is one of the recognised values when
  ``data_source == "account"``.

The loader does NOT execute any formula -- formula evaluation is owned by
the report generator (Stage 4) and the formula DSL parser (M1 W3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.|\u4e00-\u9fff]+$")
"""Allowed code form: alphanumeric, dot, underscore, pipe (regex shorthand),
plus CJK Unified Ideographs (so codes like ``6602.研发|5301`` and
``5301.资本化`` are accepted -- per Chinese GAAP charts of accounts).
``TBD`` is recognised separately as a sentinel for unmapped lines."""

ALLOWED_DATA_SOURCES: frozenset[str] = frozenset(
    {"section", "account", "formula", "cross_year", "manual_input"}
)
ALLOWED_BALANCE_KINDS: frozenset[str] = frozenset(
    {
        "closing_net",
        "closing_debit",
        "closing_credit",
        "subaccount_debit_positive",
        "subaccount_credit_positive",
        "ytd_net",
        "ytd_debit",
        "ytd_credit",
    }
)
TBD_SENTINEL = "TBD"


class YamlValidationError(ValueError):
    """Raised when a YAML template has a fatal structural error."""


@dataclass(frozen=True)
class YamlValidationWarning:
    reference_code: str
    field: str
    message: str


@dataclass(frozen=True)
class ReportRule:
    """A single rule from the ``rules`` array of a YAML template."""

    reference_code: str
    target_line_no: int
    target_label: str
    indent_level: int
    data_source: str
    code: str | None = None
    account_filter: str | None = None
    balance_kind: str | None = None
    sign: int = 1
    formula: str | None = None
    is_total: bool = False
    is_reclassification: bool = False
    notes: str | None = None
    simplify: dict[str, Any] | None = None
    """W3 Stage 2: optional v0.2 Part 1 §3 simplification config.  The
    generator passes this verbatim to :class:`renderers.SimplifyConfig`."""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedTemplate:
    """The resolved + validated form of one report-template YAML."""

    path: Path
    template_id: str
    name: str
    sheet_kind: str
    accounting_standard: str
    industry: str
    xltpl_file: str
    version: int
    rules: list[ReportRule]
    extends_chain: list[str]
    warnings: list[YamlValidationWarning]

    @property
    def has_tbd_lines(self) -> bool:
        return any(w.field == "code" for w in self.warnings)

    def rule_by_code(self, reference_code: str) -> ReportRule | None:
        for rule in self.rules:
            if rule.reference_code == reference_code:
                return rule
        return None


def list_templates(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("*.yaml") if p.is_file())


def load_template(path: Path | str, *, _seen: set[Path] | None = None) -> LoadedTemplate:
    """Load and validate a report template, resolving ``extends`` recursively.

    Parameters
    ----------
    path:
        Absolute or relative path to the YAML file.  Relative paths are
        resolved against CWD; ``extends:`` references are resolved against
        the parent file's directory.
    """

    path = Path(path).resolve()
    seen = _seen or set()
    if path in seen:
        raise YamlValidationError(f"Cyclic 'extends' chain involving {path}")
    seen = seen | {path}
    if not path.exists():
        raise YamlValidationError(f"Template not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise YamlValidationError(f"Template root must be a mapping: {path}")

    parent_template: LoadedTemplate | None = None
    extends_chain: list[str] = []
    if "extends" in raw and raw["extends"]:
        parent_path = (path.parent / raw["extends"]).resolve()
        parent_template = load_template(parent_path, _seen=seen)
        extends_chain = [*parent_template.extends_chain, parent_template.template_id]

    template_id = _require_str(raw, "template_id", path)
    name = _require_str(raw, "name", path)
    sheet_kind = _require_str(raw, "sheet_kind", path)
    accounting_standard = _require_str(raw, "accounting_standard", path)
    industry = str(raw.get("industry") or "general")
    xltpl_file = str(raw.get("xltpl_file") or "")
    version = int(raw.get("version") or 1)

    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, list):
        raise YamlValidationError(
            f"'rules' must be a list in {path}, got {type(rules_raw).__name__}"
        )

    warnings: list[YamlValidationWarning] = []
    if parent_template is not None:
        warnings.extend(parent_template.warnings)
    rules_by_code: dict[str, ReportRule] = {}
    if parent_template is not None:
        for rule in parent_template.rules:
            rules_by_code[rule.reference_code] = rule

    for idx, raw_rule in enumerate(rules_raw):
        if not isinstance(raw_rule, dict):
            raise YamlValidationError(
                f"rules[{idx}] is not a mapping in {path}"
            )
        rule, rule_warnings = _build_rule(raw_rule, path, idx)
        warnings.extend(rule_warnings)
        rules_by_code[rule.reference_code] = rule

    rules_sorted = sorted(
        rules_by_code.values(), key=lambda r: (r.target_line_no, r.reference_code)
    )

    return LoadedTemplate(
        path=path,
        template_id=template_id,
        name=name,
        sheet_kind=sheet_kind,
        accounting_standard=accounting_standard,
        industry=industry,
        xltpl_file=xltpl_file,
        version=version,
        rules=rules_sorted,
        extends_chain=extends_chain,
        warnings=warnings,
    )


def _require_str(raw: dict[str, Any], key: str, path: Path) -> str:
    if key not in raw or not raw[key]:
        raise YamlValidationError(f"Missing required field '{key}' in {path}")
    return str(raw[key])


def _build_rule(
    raw: dict[str, Any], path: Path, idx: int
) -> tuple[ReportRule, list[YamlValidationWarning]]:
    warnings: list[YamlValidationWarning] = []

    reference_code = str(raw.get("reference_code") or "").strip()
    if not reference_code:
        raise YamlValidationError(
            f"rules[{idx}] missing reference_code in {path}"
        )

    data_source = str(raw.get("data_source") or "").strip()
    if data_source not in ALLOWED_DATA_SOURCES:
        raise YamlValidationError(
            f"rules[{idx}] ({reference_code}) has invalid data_source "
            f"{data_source!r}; allowed: {sorted(ALLOWED_DATA_SOURCES)}"
        )

    balance_kind = raw.get("balance_kind")
    if data_source == "account":
        if not balance_kind:
            raise YamlValidationError(
                f"rules[{idx}] ({reference_code}) data_source=account but "
                "balance_kind is missing"
            )
        if balance_kind not in ALLOWED_BALANCE_KINDS:
            raise YamlValidationError(
                f"rules[{idx}] ({reference_code}) invalid balance_kind "
                f"{balance_kind!r}; allowed: {sorted(ALLOWED_BALANCE_KINDS)}"
            )

    code_value = raw.get("code")
    if code_value is None:
        if data_source not in {"section", "formula"}:
            warnings.append(
                YamlValidationWarning(
                    reference_code, "code", "code is missing; expected for non-section/non-formula rules",
                )
            )
    else:
        code_str = str(code_value).strip()
        if code_str == TBD_SENTINEL:
            warnings.append(
                YamlValidationWarning(
                    reference_code,
                    "code",
                    f"code is TBD (placeholder); '{raw.get('target_label')}' "
                    "will be rendered as 0 with a TBD provenance flag",
                )
            )
        elif not CODE_PATTERN.match(code_str):
            raise YamlValidationError(
                f"rules[{idx}] ({reference_code}) code {code_str!r} fails "
                f"validation pattern {CODE_PATTERN.pattern}"
            )

    target_line_no = int(raw.get("target_line_no", 0))
    target_label = str(raw.get("target_label") or "")
    indent_level = int(raw.get("indent_level", 0))
    sign = int(raw.get("sign", 1))
    formula = raw.get("formula")
    is_total = bool(raw.get("is_total"))
    is_reclassification = bool(raw.get("is_reclassification"))
    notes = raw.get("notes")
    account_filter = raw.get("account_filter")

    known_keys = {
        "reference_code",
        "target_line_no",
        "target_label",
        "indent_level",
        "data_source",
        "code",
        "account_filter",
        "balance_kind",
        "sign",
        "formula",
        "is_total",
        "is_reclassification",
        "notes",
        "simplify",
    }
    extra = {k: v for k, v in raw.items() if k not in known_keys}

    simplify_raw = raw.get("simplify")
    simplify = dict(simplify_raw) if isinstance(simplify_raw, dict) else None

    rule = ReportRule(
        reference_code=reference_code,
        target_line_no=target_line_no,
        target_label=target_label,
        indent_level=indent_level,
        data_source=data_source,
        code=str(code_value) if code_value is not None else None,
        account_filter=str(account_filter) if account_filter else None,
        balance_kind=str(balance_kind) if balance_kind else None,
        sign=sign,
        formula=str(formula) if formula else None,
        is_total=is_total,
        is_reclassification=is_reclassification,
        notes=str(notes) if notes else None,
        simplify=simplify,
        extra=extra,
    )
    return rule, warnings

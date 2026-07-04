"""PII field configuration loader for the desensitizer.

Source-of-truth ordering (highest priority first):

1. Explicit override passed to ``DesensitizeConfig`` constructor.
2. User-extended YAML at
   ``data/plugin_data/finance-auto/pii_config.yaml`` (per v0.2 Part 2 §3.2
   user-customisable file).
3. Bundled default at ``templates/ai_prompts/pii_default.yaml`` —
   covers the canonical Chinese accounting PII fields (公司 / 人员 /
   账号 / 合同 / 发票).

Each YAML must define four lists of strings; missing keys fall back to
the dataclass defaults (which mirror the bundled YAML).  Unknown keys
are warned and ignored to keep upgrades safe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "templates"
    / "ai_prompts"
    / "pii_default.yaml"
)
USER_OVERRIDE_PATH = (
    Path("data") / "plugin_data" / "finance-auto" / "pii_config.yaml"
)

# ---------------------------------------------------------------------------
# Dataclass — also acts as the single source of in-code defaults.  The values
# match the bundled YAML; tests assert they're in sync.
# ---------------------------------------------------------------------------


@dataclass
class DesensitizeConfig:
    """Per-category PII field-name lists.

    The desensitizer treats any dict key matching a name in these lists as
    a PII payload — the value is replaced with a stable placeholder
    (公司A / 公司B / ...) at the ``aggregated`` level, with the original
    string passed through at the ``raw`` level (since raw means "send the
    original" for the user who asked for it).
    """

    company_name_fields: list[str] = field(
        default_factory=lambda: [
            "customer_name", "supplier_name", "counterparty",
            "org_name", "parent_company", "subsidiary_name",
        ]
    )
    person_name_fields: list[str] = field(
        default_factory=lambda: [
            "legal_rep", "contact_person", "auditor_name",
            "signed_by", "submitter",
        ]
    )
    account_no_fields: list[str] = field(
        default_factory=lambda: [
            "bank_account", "credit_code", "tax_id", "id_card_no",
        ]
    )
    contract_no_fields: list[str] = field(
        default_factory=lambda: [
            "contract_no", "invoice_no", "po_number", "voucher_no",
        ]
    )

    def all_fields(self) -> set[str]:
        return (
            set(self.company_name_fields)
            | set(self.person_name_fields)
            | set(self.account_no_fields)
            | set(self.contract_no_fields)
        )

    def kind_of(self, field_name: str) -> str | None:
        if field_name in self.company_name_fields:
            return "company"
        if field_name in self.person_name_fields:
            return "person"
        if field_name in self.account_no_fields:
            return "account"
        if field_name in self.contract_no_fields:
            return "contract"
        return None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _safe_load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 — degrade gracefully on bad YAML.
        logger.warning("finance-auto: PII config %s unreadable: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "finance-auto: PII config %s is not a mapping; ignoring.", path
        )
        return {}
    return data


_VALID_KEYS = {
    "company_name_fields",
    "person_name_fields",
    "account_no_fields",
    "contract_no_fields",
}


def load_pii_config(
    *,
    template_path: Path | None = None,
    override_path: Path | None = None,
    explicit: DesensitizeConfig | None = None,
) -> DesensitizeConfig:
    """Resolve the effective PII config.

    Args:
        template_path: Bundled defaults (defaults to the file under
            ``templates/ai_prompts/pii_default.yaml``).
        override_path: User-extended YAML.  Defaults to
            ``data/plugin_data/finance-auto/pii_config.yaml``.  Missing
            file is treated as "no override".
        explicit: A pre-built ``DesensitizeConfig`` that wins over both.

    Lists from the override are *unioned* with the bundled defaults — the
    override extends the dictionary instead of replacing it, so users can
    add fields without re-declaring everything.
    """
    if explicit is not None:
        return explicit

    cfg = DesensitizeConfig()
    tpl = template_path or DEFAULT_TEMPLATE_PATH
    user = override_path or USER_OVERRIDE_PATH

    bundled = _safe_load_yaml(tpl)
    extra = _safe_load_yaml(user)

    for source_name, source in (("template", bundled), ("override", extra)):
        for key, values in source.items():
            if key not in _VALID_KEYS:
                logger.info(
                    "finance-auto: PII config %s ignoring unknown key %r",
                    source_name,
                    key,
                )
                continue
            if not isinstance(values, list):
                continue
            current: list[str] = list(getattr(cfg, key))
            for raw in values:
                v = str(raw).strip()
                if v and v not in current:
                    current.append(v)
            setattr(cfg, key, current)
    return cfg


__all__ = [
    "DEFAULT_TEMPLATE_PATH",
    "USER_OVERRIDE_PATH",
    "DesensitizeConfig",
    "load_pii_config",
]

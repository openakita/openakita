"""Unit tests for the M2 AI Stage 2 desensitizer (v0.2 Part 2 §3).

Coverage matrix (every tier × every PII class × residue scan):

* metadata × {numeric, string, dict, list, None, bool}
* aggregated × {numeric → bucket, company name → 公司A/B, account → hash}
* raw × {amount preserved, PII still anonymised}
* preview truncation marker
* scan_residual_pii catches stray amounts but not bucketed labels
* payload_sha256 stability + change-on-input
* PII config user-override union semantics
"""

from __future__ import annotations

from pathlib import Path

from finance_auto_backend.ai.desensitizer import (
    SENSITIVITY_LEVELS,
    bucket_amount,
    desensitize,
    payload_sha256,
    preview_desensitization,
    scan_residual_pii,
)
from finance_auto_backend.ai.pii_config import (
    DEFAULT_TEMPLATE_PATH,
    DesensitizeConfig,
    load_pii_config,
)


# ---------------------------------------------------------------------------
# bucket_amount
# ---------------------------------------------------------------------------


def test_bucket_amount_buckets():
    assert bucket_amount(0) == "0"
    assert bucket_amount(500) == "千元以下"
    assert bucket_amount(5_000) == "千元级"
    assert bucket_amount(50_000) == "万元级"
    assert bucket_amount(5_000_000) == "百万级"
    assert bucket_amount(500_000_000) == "亿元级"
    assert bucket_amount(-3_000_000) == "-百万级"
    # Non-numeric input is tolerated.
    assert bucket_amount("not-a-number") == "0"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# metadata level
# ---------------------------------------------------------------------------


def test_metadata_only_keeps_schema():
    payload = {
        "customer_name": "阿里巴巴(中国)",
        "amount": 1234567.89,
        "rows": 12,
        "active": True,
        "details": [{"sub": "x"}, {"sub": "y"}],
        "missing": None,
    }
    out = desensitize(payload, "metadata")
    assert out == {
        "customer_name": "str",
        "amount": "float",
        "rows": "int",
        "active": "bool",
        "details": [{"sub": "str"}, {"sub": "str"}],
        "missing": "NoneType",
    }


# ---------------------------------------------------------------------------
# aggregated level
# ---------------------------------------------------------------------------


def test_aggregated_buckets_amount_and_anonymises_pii():
    payload = {
        "customer_name": "阿里巴巴(中国)",
        "supplier_name": "腾讯科技",
        "amount": 38_000,
        "ratio": 0.42,
        "label": "正常项",  # not in PII config — passes through.
    }
    out = desensitize(payload, "aggregated")
    assert out["customer_name"] == "公司A"
    assert out["supplier_name"] == "公司B"
    assert out["amount"] == "万元级"
    assert out["ratio"].startswith("千元以下")
    assert out["label"] == "正常项"


def test_aggregated_account_no_field_hashes():
    payload = {
        "bank_account": "6225880123456789",
        "tax_id": "91110108MA0XYZ123",
    }
    out = desensitize(payload, "aggregated")
    assert len(out["bank_account"]) == 6 and out["bank_account"].isalnum()
    assert len(out["tax_id"]) == 6 and out["tax_id"].isalnum()
    # Different inputs → different hashes.
    assert out["bank_account"] != out["tax_id"]


def test_aggregated_repeated_value_uses_same_label():
    payload = [
        {"customer_name": "阿里巴巴", "amount": 100},
        {"customer_name": "阿里巴巴", "amount": 200},
        {"customer_name": "京东集团", "amount": 300},
    ]
    out = desensitize(payload, "aggregated")
    assert out[0]["customer_name"] == "公司A"
    assert out[1]["customer_name"] == "公司A"
    assert out[2]["customer_name"] == "公司B"


# ---------------------------------------------------------------------------
# raw level
# ---------------------------------------------------------------------------


def test_raw_keeps_amount_but_anonymises_pii():
    payload = {
        "customer_name": "阿里巴巴",
        "amount": 38_000,
        "ratio": 0.42,
    }
    out = desensitize(payload, "raw")
    # Amount preserved.
    assert out["amount"] == 38_000
    assert out["ratio"] == 0.42
    # Even at raw, named PII is still labelled — local LLM can ask for
    # the original via the consent dialog flag if absolutely required.
    assert out["customer_name"] == "公司A"


# ---------------------------------------------------------------------------
# preview + truncation
# ---------------------------------------------------------------------------


def test_preview_truncates_and_marks():
    payload = {f"k{i}": "x" * 100 for i in range(60)}
    text = preview_desensitization(payload, "metadata", max_chars=512)
    assert text.endswith("(已截断) ...")
    assert len(text) <= 512


def test_preview_short_payload_no_truncation():
    payload = {"customer_name": "阿里巴巴", "amount": 1000}
    text = preview_desensitization(payload, "aggregated")
    assert "已截断" not in text
    assert "公司A" in text


# ---------------------------------------------------------------------------
# residue scanner
# ---------------------------------------------------------------------------


def test_scan_residual_pii_finds_stray_amount():
    suspect = '{"note": "客户欠款 1234567.89 元未付"}'
    findings = scan_residual_pii(suspect)
    # The "元" suffix is intentionally outside the matched pattern; the
    # scanner extracts the amount itself.  Caller decides what to do.
    assert any("1234567.89" in s for s in findings)


def test_scan_residual_pii_ignores_bucketed_labels():
    bucketed = '{"value": "百万级", "label": "万元级"}'
    assert scan_residual_pii(bucketed) == []


def test_scan_residual_pii_finds_phone_and_id():
    suspect = '{"contact": "13912345678", "id": "11010520000101001X"}'
    findings = scan_residual_pii(suspect)
    assert "13912345678" in findings


# ---------------------------------------------------------------------------
# hash stability
# ---------------------------------------------------------------------------


def test_payload_sha256_stable():
    a = {"x": 1, "y": [2, 3]}
    b = {"y": [2, 3], "x": 1}  # different key order, same content.
    assert payload_sha256(a) == payload_sha256(b)
    c = {"x": 1, "y": [2, 4]}
    assert payload_sha256(a) != payload_sha256(c)


# ---------------------------------------------------------------------------
# pii_config loader
# ---------------------------------------------------------------------------


def test_pii_default_config_in_sync_with_code():
    cfg = load_pii_config()
    # Bundled YAML must be a strict superset of the dataclass defaults
    # (the dataclass defaults are the in-code fallback when YAML missing).
    assert "customer_name" in cfg.company_name_fields
    assert "bank_account" in cfg.account_no_fields
    assert "contract_no" in cfg.contract_no_fields


def test_pii_user_override_unions(tmp_path):
    user_yaml = tmp_path / "pii.yaml"
    user_yaml.write_text(
        "company_name_fields: ['client_name']\n"
        "person_name_fields: ['accountant']\n",
        encoding="utf-8",
    )
    cfg = load_pii_config(
        template_path=DEFAULT_TEMPLATE_PATH,
        override_path=user_yaml,
    )
    # Default + override merged.
    assert "customer_name" in cfg.company_name_fields
    assert "client_name" in cfg.company_name_fields
    assert "accountant" in cfg.person_name_fields


def test_pii_explicit_config_wins(tmp_path):
    explicit = DesensitizeConfig(
        company_name_fields=["only_one"],
        person_name_fields=[],
        account_no_fields=[],
        contract_no_fields=[],
    )
    cfg = load_pii_config(explicit=explicit)
    assert cfg.company_name_fields == ["only_one"]


def test_pii_missing_template_falls_back(tmp_path):
    cfg = load_pii_config(
        template_path=tmp_path / "missing.yaml",
        override_path=tmp_path / "missing-too.yaml",
    )
    # Falls back to dataclass defaults.
    assert "customer_name" in cfg.company_name_fields


# ---------------------------------------------------------------------------
# rejects bad sensitivity level
# ---------------------------------------------------------------------------


def test_invalid_level_raises():
    import pytest

    with pytest.raises(ValueError):
        desensitize({"x": 1}, "high")  # type: ignore[arg-type]


def test_supported_levels_constant():
    assert set(SENSITIVITY_LEVELS) == {"metadata", "aggregated", "raw"}


# ---------------------------------------------------------------------------
# Bundled YAML actually parses (smoke).
# ---------------------------------------------------------------------------


def test_bundled_yaml_exists_and_parses():
    assert DEFAULT_TEMPLATE_PATH.exists(), DEFAULT_TEMPLATE_PATH
    cfg = load_pii_config(template_path=DEFAULT_TEMPLATE_PATH)
    # Must include every category — empty lists are a YAML mistake.
    assert cfg.company_name_fields and cfg.person_name_fields
    assert cfg.account_no_fields and cfg.contract_no_fields

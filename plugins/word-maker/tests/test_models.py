from __future__ import annotations

import pytest
from word_models import DOC_TYPES, PROJECT_STATUSES, ProjectSpec, build_catalog


def test_catalog_contains_expected_doc_types() -> None:
    catalog = build_catalog()

    assert "weekly_report" in catalog["doc_types"]
    assert "research_report" in catalog["doc_types"]
    assert "docx" in catalog["output_formats"]
    assert "pdf" in catalog["experimental_formats"]


def test_project_spec_validation() -> None:
    spec = ProjectSpec(title="验收报告", doc_type="acceptance_report")

    assert spec.to_dict()["doc_type"] == "acceptance_report"
    assert set(DOC_TYPES).issuperset({spec.doc_type})
    assert "draft" in PROJECT_STATUSES


def test_project_spec_rejects_invalid_type() -> None:
    with pytest.raises(ValueError, match="Unsupported doc_type"):
        ProjectSpec(title="Bad", doc_type="bad_type").validate()


def test_project_spec_requires_title() -> None:
    with pytest.raises(ValueError, match="title is required"):
        ProjectSpec(title=" ").validate()


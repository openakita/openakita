from __future__ import annotations

import json

from ppt_audit import PptAudit


def test_audit_reports_duplicate_dense_table_and_fallback(tmp_path) -> None:
    ir = {
        "slides": [
            {
                "id": "s1",
                "title": "Same",
                "slide_type": "data_table",
                "layout_hint": {"source": "builtin"},
                "content": {"headers": list("abcdefghi"), "rows": []},
            },
            {
                "id": "s2",
                "title": "Same",
                "slide_type": "chart_bar",
                "layout_hint": {"source": "pptx"},
                "content": {},
            },
        ]
    }

    report = PptAudit().run(ir)
    path = PptAudit().save(report, tmp_path)
    codes = {issue["code"] for issue in report["issues"]}

    assert report["ok"] is True
    assert {"duplicate_title", "table_too_wide", "template_fallback", "missing_chart_spec"} <= codes
    assert json.loads(path.read_text(encoding="utf-8"))["issue_count"] == report["issue_count"]
    assert report["score"]["overall"] <= 100


def test_audit_export_file_presence(tmp_path) -> None:
    report = PptAudit().run({"slides": []}, tmp_path / "missing.pptx")
    codes = {issue["code"] for issue in report["issues"]}

    assert report["ok"] is False
    assert {"empty_deck", "missing_export"} <= codes


def test_audit_uses_real_chart_data_fields() -> None:
    report = PptAudit().run(
        {
            "slides": [
                {
                    "id": "chart_ok",
                    "title": "Revenue",
                    "slide_type": "chart_bar",
                    "content": {
                        "categories": ["Q1", "Q2"],
                        "series": [{"name": "Revenue", "values": [10, 12]}],
                    },
                }
            ]
        }
    )
    codes = {issue["code"] for issue in report["issues"]}

    assert "missing_chart_data" not in codes


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


def test_audit_reports_visual_system_guardrails() -> None:
    report = PptAudit().run(
        {
            "slides": [
                {"id": "s1", "index": 1, "title": "正常封面", "slide_type": "cover", "content": {}},
                {
                    "id": "s2",
                    "index": 2,
                    "title": "这是一个非常非常非常长的中文标题应该被提示压缩",
                    "slide_type": "cover",
                    "content": {
                        "bullets": [
                            "这是一条很长很长的项目符号内容，用来模拟模型把正文直接塞进列表项，导致页面密度过高和阅读体验变差，需要审计提前提示。"
                        ]
                    },
                },
                {
                    "id": "s3",
                    "index": 3,
                    "title": "图表页",
                    "slide_type": "chart_bar",
                    "content": {"image_query": "dashboard screenshot"},
                },
                {"id": "s4", "index": 4, "title": "结论", "slide_type": "summary", "content": {"bullets": ["A"]}},
            ]
        }
    )
    codes = {issue["code"] for issue in report["issues"]}

    assert {"title_too_long_cjk", "cover_after_first", "list_item_too_long", "image_layout_mismatch"} <= codes


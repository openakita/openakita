from __future__ import annotations

import json

from ppt_audit import PptAudit
from ppt_repair import PptRepair


def test_repair_compresses_dense_slide_and_saves_plan(tmp_path) -> None:
    ir = {
        "slides": [
            {
                "id": "s1",
                "title": "This is a very long title that should be shortened by repair pass",
                "slide_type": "content",
                "content": {"body": "x" * 500, "bullets": ["y" * 150 for _ in range(8)]},
                "quality": {"density_score": 0.9, "needs_split": True},
            }
        ]
    }
    audit = PptAudit().run(ir)

    repaired, plan = PptRepair().repair(ir, audit)
    path = PptRepair().save(plan, tmp_path)

    assert plan["changed"] is True
    assert len(repaired["slides"][0]["content"]["bullets"]) == 5
    assert repaired["slides"][0]["quality"]["needs_split"] is False
    assert json.loads(path.read_text(encoding="utf-8"))["actions"]


def test_repair_trims_wide_tables() -> None:
    ir = {
        "slides": [
            {
                "id": "s1",
                "title": "Table",
                "slide_type": "data_table",
                "content": {"headers": list("abcdefghi"), "rows": [list("123456789")]},
            }
        ]
    }
    audit = PptAudit().run(ir)

    repaired, plan = PptRepair().repair(ir, audit)

    assert plan["changed"] is True
    assert len(repaired["slides"][0]["content"]["headers"]) == 8
    assert len(repaired["slides"][0]["content"]["rows"][0]) == 8


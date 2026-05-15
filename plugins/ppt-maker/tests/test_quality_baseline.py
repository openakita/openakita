from __future__ import annotations

import json
from pathlib import Path


def test_quality_baseline_and_fixed_scenarios_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    baseline = root / "QUALITY_BASELINE.md"
    scenarios = root / "tests" / "fixtures" / "quality_scenarios.json"

    assert baseline.exists()
    payload = json.loads(scenarios.read_text(encoding="utf-8"))
    ids = {item["id"] for item in payload["scenarios"]}
    assert {
        "topic_tech_roadmap",
        "consulting_strategy_report",
        "table_sales_report",
        "product_launch_pitch",
        "files_to_deck_briefing",
    } <= ids


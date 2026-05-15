from __future__ import annotations

import json
from pathlib import Path

from ppt_table_analyzer import TableAnalyzer


def test_profile_csv_identifies_metrics_dimensions_and_warnings(tmp_path) -> None:
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "month,region,revenue,orders\n"
        "2026-01,East,1000,10\n"
        "2026-02,East,1200,12\n"
        "2026-03,West,900,9\n",
        encoding="utf-8",
    )
    analyzer = TableAnalyzer()

    table = analyzer.load(csv_path)
    profile = analyzer.profile(table)
    specs = analyzer.chart_specs(profile, table)
    insights = analyzer.insights(profile, specs)

    assert profile["row_count"] == 3
    assert "revenue" in profile["numeric_columns"]
    assert "month" in profile["date_columns"]
    assert specs[0]["type"] == "line"
    assert specs[0]["categories"] == ["2026-01", "2026-02", "2026-03"]
    assert specs[0]["series"][0]["values"] == [1000.0, 1200.0, 900.0]
    assert insights["recommended_storyline"] == ["数据概况", "核心指标", "图表分析", "洞察总结"]


def test_analyze_to_files_writes_profile_insights_and_chart_specs(tmp_path) -> None:
    csv_path = tmp_path / "wide.csv"
    csv_path.write_text(
        "a,b,c,d,e,f,g,h,i\n"
        "x,1,2,3,4,5,6,7,8\n",
        encoding="utf-8",
    )

    result = TableAnalyzer().analyze_to_files(csv_path, tmp_path / "dataset")

    profile_path = result["paths"]["profile_path"]
    insights_path = result["paths"]["insights_path"]
    chart_specs_path = result["paths"]["chart_specs_path"]
    assert json.loads(Path(profile_path).read_text(encoding="utf-8"))["column_count"] == 9
    assert json.loads(Path(insights_path).read_text(encoding="utf-8"))["key_findings"]
    assert json.loads(Path(chart_specs_path).read_text(encoding="utf-8"))
    assert "最多 8 列" in result["profile"]["quality_warnings"][0]


def test_table_without_numeric_columns_falls_back_to_table(tmp_path) -> None:
    csv_path = tmp_path / "labels.csv"
    csv_path.write_text("name,category\nA,One\nB,Two\n", encoding="utf-8")

    result = TableAnalyzer().analyze_to_files(csv_path, tmp_path / "dataset")

    assert result["chart_specs"][0]["type"] == "table"
    assert result["chart_specs"][0]["headers"] == ["name", "category"]
    assert "未识别到稳定数值列" in result["profile"]["quality_warnings"][0]


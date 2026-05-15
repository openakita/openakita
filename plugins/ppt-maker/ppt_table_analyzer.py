"""Deterministic table profiling and chart suggestions for ppt-maker."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from ppt_models import ChartType, ColumnType
from ppt_source_loader import MissingDependencyError, SourceParseError


@dataclass(slots=True)
class TableData:
    headers: list[str]
    rows: list[dict[str, str]]
    source_path: str


class TableAnalyzer:
    """Profile tabular data before any LLM summarization happens."""

    MAX_PROFILE_ROWS = 10000

    def load(self, path: str | Path) -> TableData:
        source = Path(path)
        suffix = source.suffix.lower()
        if suffix in {".csv", ".tsv"}:
            return self.load_csv(source)
        if suffix == ".xlsx":
            return self.load_xlsx(source)
        raise SourceParseError(f"Unsupported table file type: {suffix}")

    def load_csv(self, path: Path) -> TableData:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            if not reader.fieldnames:
                raise SourceParseError("Table file has no header row")
            headers = [self._clean_header(name, index) for index, name in enumerate(reader.fieldnames)]
            rows = []
            for index, row in enumerate(reader):
                rows.append({headers[i]: (row.get(reader.fieldnames[i]) or "") for i in range(len(headers))})
                if index + 1 >= self.MAX_PROFILE_ROWS:
                    break
        return TableData(headers=headers, rows=rows, source_path=str(path))

    def load_xlsx(self, path: Path) -> TableData:
        try:
            import openpyxl  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise MissingDependencyError(
                "table_processing",
                "XLSX table analysis requires the optional table_processing dependency group.",
            ) from exc
        workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        sheet = workbook.active
        rows_iter = sheet.iter_rows(values_only=True)
        raw_headers = next(rows_iter, None)
        if not raw_headers:
            raise SourceParseError("Workbook has no header row")
        headers = [self._clean_header(str(name or ""), index) for index, name in enumerate(raw_headers)]
        rows = []
        for index, row in enumerate(rows_iter):
            values = list(row)
            rows.append(
                {
                    headers[i]: "" if i >= len(values) or values[i] is None else str(values[i])
                    for i in range(len(headers))
                }
            )
            if index + 1 >= self.MAX_PROFILE_ROWS:
                break
        return TableData(headers=headers, rows=rows, source_path=str(path))

    def profile(self, table: TableData) -> dict[str, Any]:
        columns = [self._profile_column(header, table.rows) for header in table.headers]
        numeric_columns = [c["name"] for c in columns if c["type"] == ColumnType.NUMBER.value]
        categorical_columns = [c["name"] for c in columns if c["type"] == ColumnType.TEXT.value]
        date_columns = [c["name"] for c in columns if c["type"] == ColumnType.DATE.value]
        warnings = self._quality_warnings(table, columns)
        return {
            "source_path": table.source_path,
            "row_count": len(table.rows),
            "column_count": len(table.headers),
            "columns": columns,
            "numeric_columns": numeric_columns,
            "categorical_columns": categorical_columns,
            "date_columns": date_columns,
            "candidate_metrics": numeric_columns[:8],
            "candidate_dimensions": (date_columns + categorical_columns)[:8],
            "quality_warnings": warnings,
        }

    def chart_specs(self, profile: dict[str, Any], table: TableData | None = None) -> list[dict[str, Any]]:
        metrics = profile.get("candidate_metrics", [])
        dimensions = profile.get("candidate_dimensions", [])
        specs: list[dict[str, Any]] = []
        if metrics and dimensions:
            first_metric = metrics[0]
            first_dimension = dimensions[0]
            chart_type = ChartType.LINE.value if first_dimension in profile.get("date_columns", []) else ChartType.BAR.value
            categories, values = self._series_from_table(table, first_dimension, first_metric)
            specs.append(
                {
                    "id": "chart_primary",
                    "type": chart_type,
                    "title": f"{first_metric} by {first_dimension}",
                    "x": first_dimension,
                    "y": first_metric,
                    "categories": categories,
                    "series": [{"name": first_metric, "values": values}] if values else [],
                    "reason": "Primary metric by the most likely reporting dimension.",
                }
            )
        if len(metrics) >= 3:
            metric_cards = self._metric_cards_from_table(table, metrics[:4])
            specs.append(
                {
                    "id": "metric_cards",
                    "type": ChartType.METRIC_CARDS.value,
                    "title": "Core metric cards",
                    "metrics": metric_cards or metrics[:4],
                    "reason": "Multiple numeric columns can become KPI cards.",
                }
            )
        if metrics and len(dimensions) >= 1:
            columns = [*dimensions[:3], *metrics[:5]][:8]
            specs.append(
                {
                    "id": "data_table_summary",
                    "type": ChartType.TABLE.value,
                    "title": "Top records summary",
                    "columns": columns,
                    "headers": columns,
                    "rows": self._table_rows(table, columns),
                    "reason": "A compact table keeps raw evidence visible.",
                }
            )
        if not specs:
            specs.append(
                {
                    "id": "data_table_fallback",
                    "type": ChartType.TABLE.value,
                    "title": "Data preview",
                    "columns": profile.get("candidate_dimensions", [])[:8],
                    "headers": profile.get("candidate_dimensions", [])[:8],
                    "rows": self._table_rows(table, profile.get("candidate_dimensions", [])[:8]),
                    "reason": "No reliable numeric metric was detected.",
                }
            )
        return specs

    def insights(self, profile: dict[str, Any], chart_specs: list[dict[str, Any]]) -> dict[str, Any]:
        row_count = profile.get("row_count", 0)
        column_count = profile.get("column_count", 0)
        metrics = profile.get("candidate_metrics", [])
        dimensions = profile.get("candidate_dimensions", [])
        findings = [
            f"数据集包含 {row_count} 行、{column_count} 列。",
            f"识别到 {len(metrics)} 个候选指标和 {len(dimensions)} 个候选维度。",
        ]
        if profile.get("quality_warnings"):
            findings.append("存在数据质量提示，汇报时需要标注口径。")
        storyline = ["数据概况", "核心指标", "图表分析", "洞察总结"]
        return {
            "key_findings": findings,
            "chart_suggestions": chart_specs,
            "recommended_storyline": storyline,
            "risks_and_caveats": profile.get("quality_warnings", []),
        }

    def analyze_to_files(self, path: str | Path, output_dir: str | Path) -> dict[str, Any]:
        table = self.load(path)
        profile = self.profile(table)
        charts = self.chart_specs(profile, table)
        insights = self.insights(profile, charts)
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        profile_path = out / "profile.json"
        insights_path = out / "insights.json"
        chart_specs_path = out / "chart_specs.json"
        profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        insights_path.write_text(json.dumps(insights, ensure_ascii=False, indent=2), encoding="utf-8")
        chart_specs_path.write_text(json.dumps(charts, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "profile": profile,
            "insights": insights,
            "chart_specs": charts,
            "paths": {
                "profile_path": str(profile_path),
                "insights_path": str(insights_path),
                "chart_specs_path": str(chart_specs_path),
            },
        }

    def _profile_column(self, header: str, rows: list[dict[str, str]]) -> dict[str, Any]:
        values = [row.get(header, "").strip() for row in rows]
        non_empty = [value for value in values if value != ""]
        inferred = self._infer_type(non_empty)
        numeric_values = [self._to_float(value) for value in non_empty]
        numeric_values = [value for value in numeric_values if value is not None and math.isfinite(value)]
        counter = Counter(non_empty)
        profile: dict[str, Any] = {
            "name": header,
            "type": inferred.value,
            "null_count": len(values) - len(non_empty),
            "example_values": non_empty[:5],
            "unique_count": len(counter),
            "top_values": counter.most_common(5),
        }
        if numeric_values:
            profile.update(
                {
                    "min": min(numeric_values),
                    "max": max(numeric_values),
                    "mean": mean(numeric_values),
                }
            )
        return profile

    def _infer_type(self, values: list[str]) -> ColumnType:
        if not values:
            return ColumnType.EMPTY
        sample = values[:100]
        number_hits = sum(1 for value in sample if self._to_float(value) is not None)
        date_hits = sum(1 for value in sample if self._to_date(value) is not None)
        bool_hits = sum(1 for value in sample if value.lower() in {"true", "false", "yes", "no", "是", "否"})
        threshold = max(1, int(len(sample) * 0.8))
        if number_hits >= threshold:
            return ColumnType.NUMBER
        if date_hits >= threshold:
            return ColumnType.DATE
        if bool_hits >= threshold:
            return ColumnType.BOOLEAN
        if number_hits or date_hits or bool_hits:
            return ColumnType.MIXED
        return ColumnType.TEXT

    def _quality_warnings(self, table: TableData, columns: list[dict[str, Any]]) -> list[str]:
        warnings: list[str] = []
        if len(table.headers) > 8:
            warnings.append("表格列数较多，默认只建议选择最多 8 列进入 PPT。")
        if len(table.rows) >= self.MAX_PROFILE_ROWS:
            warnings.append("表格行数超过分析上限，只使用前 10000 行做 profile。")
        for column in columns:
            if table.rows and column["null_count"] / max(1, len(table.rows)) > 0.3:
                warnings.append(f"列 {column['name']} 缺失值超过 30%。")
        if not any(column["type"] == ColumnType.NUMBER.value for column in columns):
            warnings.append("未识别到稳定数值列，将退化为表格摘要和洞察页。")
        return warnings

    def _clean_header(self, value: str, index: int) -> str:
        cleaned = value.strip()
        return cleaned or f"column_{index + 1}"

    def _to_float(self, value: str) -> float | None:
        text = value.strip().replace(",", "").replace("%", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _to_date(self, value: str) -> datetime | None:
        text = value.strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y/%m", "%Y"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    def _series_from_table(
        self,
        table: TableData | None,
        dimension: str,
        metric: str,
        *,
        limit: int = 8,
    ) -> tuple[list[str], list[float]]:
        if table is None:
            return [], []
        grouped: dict[str, list[float]] = {}
        for row in table.rows:
            key = (row.get(dimension) or "未分组").strip() or "未分组"
            value = self._to_float(row.get(metric, ""))
            if value is None:
                continue
            grouped.setdefault(key, []).append(value)
        categories = list(grouped)[:limit]
        values = [round(sum(grouped[key]), 3) for key in categories]
        return categories, values

    def _metric_cards_from_table(
        self,
        table: TableData | None,
        metrics: list[str],
    ) -> list[dict[str, str]]:
        if table is None:
            return []
        cards = []
        for metric in metrics[:4]:
            values = []
            for row in table.rows:
                value = self._to_float(row.get(metric, ""))
                if value is not None:
                    values.append(value)
            if not values:
                continue
            cards.append(
                {
                    "label": metric,
                    "value": f"{round(sum(values), 2):g}",
                    "delta": f"avg {round(mean(values), 2):g}",
                }
            )
        return cards

    @staticmethod
    def _table_rows(table: TableData | None, columns: list[str], *, limit: int = 8) -> list[list[str]]:
        if table is None or not columns:
            return []
        return [[str(row.get(column, "")) for column in columns] for row in table.rows[:limit]]


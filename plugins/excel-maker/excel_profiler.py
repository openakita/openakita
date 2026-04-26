"""Data profiling for imported workbooks."""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def _is_number(value: Any) -> bool:
    if value is None or value == "":
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _is_date(value: Any) -> bool:
    if isinstance(value, datetime):
        return True
    if not isinstance(value, str) or not value.strip():
        return False
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y/%m"):
        try:
            datetime.strptime(value.strip()[:10], fmt)
            return True
        except ValueError:
            continue
    return False


def infer_column_type(values: list[Any]) -> str:
    non_empty = [value for value in values if str(value or "").strip()]
    if not non_empty:
        return "empty"
    numeric = sum(1 for value in non_empty if _is_number(value))
    dates = sum(1 for value in non_empty if _is_date(value))
    bools = sum(1 for value in non_empty if str(value).strip().lower() in {"true", "false", "是", "否"})
    total = len(non_empty)
    if numeric / total >= 0.8:
        return "number"
    if dates / total >= 0.8:
        return "date"
    if bools / total >= 0.8:
        return "boolean"
    return "text"


def _column_profile(name: str, values: list[Any]) -> dict[str, Any]:
    total = len(values)
    missing = sum(1 for value in values if not str(value or "").strip())
    non_empty = [str(value) for value in values if str(value or "").strip()]
    top = Counter(non_empty).most_common(5)
    col_type = infer_column_type(values)
    profile: dict[str, Any] = {
        "name": name,
        "type": col_type,
        "missing": missing,
        "missing_rate": round(missing / total, 4) if total else 0,
        "unique": len(set(non_empty)),
        "top_values": [{"value": value, "count": count} for value, count in top],
        "warnings": [],
    }
    if profile["missing_rate"] > 0.3:
        profile["warnings"].append("High missing rate")
    if col_type == "number":
        nums = [float(value) for value in values if _is_number(value)]
        if nums:
            profile["min"] = min(nums)
            profile["max"] = max(nums)
            profile["avg"] = round(sum(nums) / len(nums), 4)
    return profile


class WorkbookProfiler:
    def profile_import(self, import_profile_path: str | Path, output_path: str | Path | None = None) -> dict[str, Any]:
        import_profile = json.loads(Path(import_profile_path).read_text(encoding="utf-8"))
        preview = import_profile.get("preview", {})
        sheet_profiles = []
        for sheet in import_profile.get("sheets", []):
            name = sheet.get("name", "")
            sample = preview.get(name, {})
            headers = [str(header or f"Column_{idx + 1}") for idx, header in enumerate(sample.get("headers", []))]
            rows = sample.get("rows", [])
            max_cols = max([len(headers), *(len(row) for row in rows)])
            if len(headers) < max_cols:
                headers.extend(f"Column_{idx + 1}" for idx in range(len(headers), max_cols))
            columns = []
            for idx, header in enumerate(headers[:max_cols]):
                values = [row[idx] if idx < len(row) else "" for row in rows]
                columns.append(_column_profile(header, values))
            metrics = [col["name"] for col in columns if col["type"] == "number"]
            dimensions = [col["name"] for col in columns if col["type"] in {"text", "date", "boolean"}]
            sheet_profiles.append(
                {
                    **sheet,
                    "columns": columns,
                    "candidate_metrics": metrics[:12],
                    "candidate_dimensions": dimensions[:12],
                    "quality_warnings": [
                        warning
                        for col in columns
                        for warning in [f"{col['name']}: {item}" for item in col.get("warnings", [])]
                    ],
                }
            )
        result = {
            "workbook_id": import_profile.get("workbook_id"),
            "source_path": import_profile.get("source_path"),
            "sheets": sheet_profiles,
            "warnings": import_profile.get("warnings", []),
            "sampled": True,
        }
        if output_path:
            Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


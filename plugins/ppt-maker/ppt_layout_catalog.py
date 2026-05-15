"""Theme and layout catalog loader for ppt-maker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class LayoutCatalog:
    """Load lightweight theme/layout indexes and select layout candidates."""

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root else Path(__file__).parent / "templates"

    def themes(self) -> dict[str, Any]:
        return self._read_json(self._root / "themes" / "index.json")

    def layouts(self) -> dict[str, Any]:
        return self._read_json(self._root / "layouts" / "index.json")

    def as_prompt_context(self) -> dict[str, Any]:
        return {
            "themes": self.themes(),
            "layouts": self.layouts(),
            "selection_rules": {
                "prefer_chart_layouts_for_numeric_data": True,
                "prefer_split_image_text_when_image_query_exists": True,
                "avoid_dense_layouts_when_density_limit_is_exceeded": True,
                "do_not_use_cover_after_first_slide": True,
            },
        }

    def pick_layout(self, *, slide_type: str, has_image: bool = False, has_data: bool = False) -> str:
        if slide_type in {"cover", "agenda", "comparison", "timeline", "metric_cards", "data_table", "insight_summary"}:
            return slide_type
        if slide_type.startswith("chart") or has_data:
            return "chart"
        if has_image:
            return "split_image_text"
        if slide_type in {"summary", "closing"}:
            return "conclusion_first"
        return "conclusion_first"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}


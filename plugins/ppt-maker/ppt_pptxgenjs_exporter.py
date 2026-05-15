"""Optional PptxGenJS exporter bridge for ppt-maker."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ppt_exporter import PptxExporter


class PptxGenJsUnavailable(RuntimeError):
    """Raised when the optional Node renderer cannot be used."""


class PptxGenJsExporter:
    """Render via Node/PptxGenJS when available, otherwise allow fallback."""

    def __init__(self, *, renderer_dir: str | Path | None = None) -> None:
        self._renderer_dir = Path(renderer_dir) if renderer_dir else Path(__file__).parent / "renderers" / "pptxgenjs"

    def is_available(self) -> bool:
        node = shutil.which("node")
        script = self._renderer_dir / "render.js"
        package_dir = self._renderer_dir / "node_modules"
        return bool(node and script.exists() and package_dir.exists())

    def export(
        self,
        *,
        render_model: dict[str, Any],
        legacy_slides_ir: dict[str, Any],
        output_path: str | Path,
        allow_fallback: bool = True,
    ) -> Path:
        path = Path(output_path)
        if not self.is_available():
            if allow_fallback:
                return PptxExporter().export(legacy_slides_ir, path)
            raise PptxGenJsUnavailable("Node/PptxGenJS renderer is not installed.")

        path.parent.mkdir(parents=True, exist_ok=True)
        payload_path = path.with_suffix(".render_model.json")
        payload_path.write_text(json.dumps(render_model, ensure_ascii=False, indent=2), encoding="utf-8")
        command = [
            shutil.which("node") or "node",
            str(self._renderer_dir / "render.js"),
            str(payload_path),
            str(path),
        ]
        completed = subprocess.run(  # noqa: S603
            command,
            cwd=str(self._renderer_dir),
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode != 0:
            if allow_fallback:
                return PptxExporter().export(legacy_slides_ir, path)
            raise PptxGenJsUnavailable(completed.stderr or completed.stdout or "PptxGenJS render failed")
        if not path.exists() or path.stat().st_size == 0:
            if allow_fallback:
                return PptxExporter().export(legacy_slides_ir, path)
            raise PptxGenJsUnavailable("PptxGenJS did not create an output file.")
        return path


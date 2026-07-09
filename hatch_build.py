from __future__ import annotations

import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from scripts.write_build_version import build_version_string


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":
            return

        generated = ROOT / "build" / "openakita_bundled_version.txt"
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text(build_version_string(), encoding="utf-8", newline="\n")
        build_data.setdefault("force_include", {})[str(generated)] = (
            "openakita/_bundled_version.txt"
        )

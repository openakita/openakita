from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUILTIN_MCPS = ROOT / "mcps"


def test_builtin_mcp_python_module_targets_exist() -> None:
    missing: list[str] = []

    for metadata_path in sorted(BUILTIN_MCPS.glob("*/SERVER_METADATA.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        command = str(metadata.get("command") or "")
        args = metadata.get("args") or []

        if not isinstance(args, list):
            continue

        command_name = Path(command).name.lower()
        if command_name not in {"python", "python.exe", "python3", "python3.exe"}:
            continue

        if len(args) < 2 or args[0] != "-m":
            continue

        module_name = args[1]
        if not isinstance(module_name, str) or not module_name.startswith("openakita."):
            continue

        if importlib.util.find_spec(module_name) is None:
            missing.append(f"{metadata_path.relative_to(ROOT)} -> {module_name}")

    assert not missing, "Builtin MCP metadata points at missing Python modules: " + ", ".join(
        missing
    )

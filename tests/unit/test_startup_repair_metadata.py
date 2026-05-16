"""Regression tests for desktop startup repair metadata.

These checks keep the packaged startup path quiet: plugins must declare tool
risk classes explicitly, plugin skills need parseable frontmatter, and the
heaviest plugin dependency bootstrap must stay off the import path.
"""

from __future__ import annotations

import json
from pathlib import Path

from openakita.core.policy_v2.enums import ApprovalClass


ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = ROOT / "plugins"


def _plugin_manifests() -> list[tuple[Path, dict]]:
    manifests: list[tuple[Path, dict]] = []
    for path in sorted(PLUGINS_DIR.glob("*/plugin.json")):
        manifests.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return manifests


def test_plugin_tools_have_explicit_approval_class() -> None:
    valid_classes = {klass.value for klass in ApprovalClass}
    missing: list[str] = []
    invalid: list[str] = []

    for path, manifest in _plugin_manifests():
        tools = manifest.get("provides", {}).get("tools") or []
        tool_classes = manifest.get("tool_classes") or {}
        for tool in tools:
            klass = tool_classes.get(tool)
            if not klass:
                missing.append(f"{path.parent.name}:{tool}")
            elif klass not in valid_classes:
                invalid.append(f"{path.parent.name}:{tool}={klass}")

    assert missing == []
    assert invalid == []


def test_shipped_plugins_use_plugin_api_v2() -> None:
    legacy: list[str] = []
    for path, manifest in _plugin_manifests():
        plugin_api = str((manifest.get("requires") or {}).get("plugin_api") or "")
        if plugin_api.startswith("~1"):
            legacy.append(path.parent.name)
    assert legacy == []


def test_plugin_skills_have_frontmatter_name() -> None:
    missing: list[str] = []
    for path, manifest in _plugin_manifests():
        skill_name = (manifest.get("provides") or {}).get("skill")
        if not skill_name:
            continue
        skill_path = path.parent / skill_name
        text = skill_path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\nname:" not in text.split("---", 2)[1]:
            missing.append(str(skill_path.relative_to(ROOT)))
    assert missing == []


def test_footage_gate_does_not_bootstrap_numpy_at_import_time() -> None:
    source = (PLUGINS_DIR / "footage-gate" / "plugin.py").read_text(encoding="utf-8")
    before_fastapi_import = source.split("from fastapi import", 1)[0]

    assert "ensure_importable(" not in before_fastapi_import
    assert "from footage_gate_pipeline import" not in source.split("class Plugin", 1)[0]

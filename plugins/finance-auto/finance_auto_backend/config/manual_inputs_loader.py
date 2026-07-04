"""Loader for manual-input preset YAMLs.

These YAMLs declare the *slots* the UI must surface to the accountant —
fields whose values cannot come from the trial balance or the VAT
declaration and therefore have to be supplied by hand (or pre-filled
from a learned sample).

The loader is intentionally tiny: just a YAML→Pydantic translation with
basic shape validation.  Persistence and CRUD are handled elsewhere.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..models import ManualInputPreset


_PRESET_CACHE: dict[str, list[ManualInputPreset]] = {}


def load_preset_file(path: Path) -> list[ManualInputPreset]:
    """Read a single preset YAML and return the parsed list.

    Cached on the absolute path so repeated calls are free; pass a
    different mtime-aware key if you need cache busting (current code
    does not).
    """
    key = str(path.resolve())
    if key in _PRESET_CACHE:
        return _PRESET_CACHE[key]
    if not path.exists():
        raise FileNotFoundError(f"manual-input preset not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("manual_inputs") or []
    if not isinstance(items, list):
        raise ValueError(
            f"{path}: expected top-level 'manual_inputs' to be a list"
        )
    presets: list[ManualInputPreset] = []
    seen: set[str] = set()
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"{path}: entry #{i} must be a mapping, got {type(item).__name__}"
            )
        preset = ManualInputPreset(**item)
        if preset.key in seen:
            raise ValueError(
                f"{path}: duplicate key {preset.key!r} at entry #{i}"
            )
        seen.add(preset.key)
        presets.append(preset)
    _PRESET_CACHE[key] = presets
    return presets


def cash_flow_aux_presets(
    *,
    templates_root: Path | None = None,
) -> list[ManualInputPreset]:
    """Convenience: load the canonical ``cash_flow_aux.yaml`` shipped
    with the plugin.  ``templates_root`` defaults to
    ``plugins/finance-auto/templates/manual_inputs/``."""
    root = templates_root or (
        Path(__file__).resolve().parent.parent.parent
        / "templates" / "manual_inputs"
    )
    return load_preset_file(root / "cash_flow_aux.yaml")

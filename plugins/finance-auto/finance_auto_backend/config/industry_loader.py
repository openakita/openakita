"""M1 W3 Stage 5 — industry overrides loader.

Loads ``templates/industry_overrides/<industry>.yaml`` and deep-merges it
on top of a base config dictionary.

Merge semantics
---------------
* **dicts** merge key by key (recursive).
* **scalars** (str / int / float / bool / None) — overlay wins.
* **lists** — overlay wins entirely (no auto-extend).  Templates already
  carry a v0.3 ``extends:`` mechanism for rule arrays; the industry
  overlay deliberately does *not* try to splice rule lists to keep the
  composition rules predictable.
* **missing keys** — overlay key is added.

The loader is intentionally tiny: it returns plain dicts/Pydantic.
Higher layers decide what to do with the merged shape.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from ..models import ManualInputPreset


_OVERLAY_CACHE: dict[str, dict[str, Any]] = {}


def _overlay_root(custom: Path | None = None) -> Path:
    if custom is not None:
        return custom
    return (
        Path(__file__).resolve().parent.parent.parent
        / "templates" / "industry_overrides"
    )


def list_industries(*, templates_root: Path | None = None) -> list[dict[str, Any]]:
    """List every overlay yaml shipped with the plugin.  Returns a list of
    ``{industry, label, description, path}`` records (no recursive
    parsing -- only the top-level metadata)."""
    root = _overlay_root(templates_root)
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for p in sorted(root.glob("*.yaml")):
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        items.append({
            "industry": raw.get("industry") or p.stem,
            "label": raw.get("label") or p.stem,
            "description": raw.get("description") or "",
            "path": str(p),
            "overlay_keys": [k for k in raw.keys()
                             if k not in {"industry", "label", "description"}],
        })
    return items


def load_overlay(
    industry: str | None, *, templates_root: Path | None = None,
) -> dict[str, Any]:
    """Return the parsed YAML for ``industry`` or an empty dict.

    Caches by absolute path so repeat lookups are O(1).
    """
    if not industry or industry in {"general", "default", ""}:
        return {}
    root = _overlay_root(templates_root)
    path = root / f"{industry}.yaml"
    key = str(path.resolve())
    if key in _OVERLAY_CACHE:
        return _OVERLAY_CACHE[key]
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _OVERLAY_CACHE[key] = raw
    return raw


def deep_merge(base: Any, overlay: Any) -> Any:
    """Recursive merge per the rules in this module's docstring."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = dict(base)
        for k, v in overlay.items():
            if k in out:
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out
    # For scalars + lists, the overlay value wins outright.
    return copy.deepcopy(overlay) if overlay is not None else copy.deepcopy(base)


def effective_config(
    *,
    base: dict[str, Any] | None = None,
    industry: str | None,
    templates_root: Path | None = None,
) -> dict[str, Any]:
    """Return the deep-merged config for ``industry``.

    Common usage::

        eff = effective_config(
            base={"org_defaults": {"aux_mode": "full"}},
            industry=org.industry,
        )
    """
    base = base or {}
    overlay = load_overlay(industry, templates_root=templates_root)
    return deep_merge(base, overlay)


def merge_manual_input_presets(
    base_presets: Iterable[ManualInputPreset],
    industry: str | None,
    *,
    templates_root: Path | None = None,
) -> list[ManualInputPreset]:
    """Combine the shipped cash_flow_aux presets with any
    ``manual_inputs_overlay`` block in the industry overlay.  Overlay
    entries with an existing key replace the base entry; new keys are
    appended."""
    by_key: dict[str, ManualInputPreset] = {p.key: p for p in base_presets}
    overlay = load_overlay(industry, templates_root=templates_root)
    extras = overlay.get("manual_inputs_overlay") or []
    for raw in extras:
        if not isinstance(raw, dict) or "key" not in raw:
            continue
        # Strip non-Pydantic-known fields (e.g. ``priority``) before
        # constructing; keep them in extra notes for debugging.
        accepted = {
            "key", "label", "value_type", "default_source",
            "source_hint", "required_by",
        }
        kwargs = {k: v for k, v in raw.items() if k in accepted}
        try:
            preset = ManualInputPreset(**kwargs)
        except Exception:
            continue
        by_key[preset.key] = preset
    return list(by_key.values())

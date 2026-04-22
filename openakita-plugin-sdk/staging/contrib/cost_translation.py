"""Plugin-aware human-readable cost translation.

Stand-alone module (zero extra deps) that wraps :func:`to_human_units` with a
per-plugin translation map.  The map is Python data (not YAML) so the SDK keeps
its zero-runtime-dep promise.

Plugins can extend it at runtime via :func:`register_cost_template`.

Usage::

    from openakita_plugin_sdk.contrib import translate_cost
    label = translate_cost("seedance-video", cost=3.0, currency="CNY",
                           units=5, unit_label="s")
    # → "≈ 5 秒视频 / 约 3.0 元 / 通常 30-60 秒生成"
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .cost_estimator import to_human_units


@dataclass(frozen=True)
class CostTemplate:
    """Per-plugin cost translation template.

    Attributes:
        unit_label_zh: Localized unit name (e.g. "秒视频", "张图片").
        per_unit_hint: Free-form hint about expected price per unit
            (e.g. "约 0.6 元/秒"); shown as a secondary line.
        runtime_hint: Free-form hint about generation time
            (e.g. "通常 30-60 秒生成").
    """

    unit_label_zh: str
    per_unit_hint: str = ""
    runtime_hint: str = ""


COST_TRANSLATION_MAP: dict[str, CostTemplate] = {
    "seedance-video": CostTemplate(
        unit_label_zh="秒视频",
        per_unit_hint="约 0.6 元/秒",
        runtime_hint="通常 30-60 秒生成",
    ),
    "tongyi-image": CostTemplate(
        unit_label_zh="张图片",
        per_unit_hint="约 0.1 元/张",
        runtime_hint="通常 5-15 秒生成",
    ),
    "image-edit": CostTemplate(
        unit_label_zh="次编辑",
        per_unit_hint="按供应商价目表计费",
        runtime_hint="通常 5-20 秒",
    ),
    "highlight-cutter": CostTemplate(
        unit_label_zh="分钟视频",
        per_unit_hint="本地处理无 API 费用",
        runtime_hint="约 0.3× 视频时长",
    ),
    "subtitle-maker": CostTemplate(
        unit_label_zh="分钟音频",
        per_unit_hint="本地 whisper.cpp 无 API 费用",
        runtime_hint="约 0.5× 音频时长",
    ),
    "video-translator": CostTemplate(
        unit_label_zh="字符",
        per_unit_hint="按 LLM 端点计费",
        runtime_hint="按字幕条数线性增长",
    ),
    "tts-studio": CostTemplate(
        unit_label_zh="字符",
        per_unit_hint="按 TTS 供应商价目表",
        runtime_hint="约 1× 朗读时长",
    ),
    "avatar-speaker": CostTemplate(
        unit_label_zh="秒视频",
        per_unit_hint="按数字人供应商价目表",
        runtime_hint="通常 30-120 秒生成",
    ),
    "poster-maker": CostTemplate(
        unit_label_zh="张海报",
        per_unit_hint="本地合成无 API 费用",
        runtime_hint="通常 1-5 秒/张",
    ),
    "storyboard": CostTemplate(
        unit_label_zh="次分镜",
        per_unit_hint="按 LLM 端点计费",
        runtime_hint="通常 10-30 秒",
    ),
}


def register_cost_template(plugin_id: str, template: CostTemplate) -> None:
    """Register a per-plugin translation template (overrides if exists).

    Plugins outside the built-in catalog can call this in ``on_load`` to
    enable :func:`translate_cost` for themselves.
    """
    if not plugin_id or not isinstance(plugin_id, str):
        raise ValueError("plugin_id must be a non-empty string")
    if not isinstance(template, CostTemplate):
        raise TypeError("template must be a CostTemplate instance")
    COST_TRANSLATION_MAP[plugin_id] = template


def translate_cost(
    plugin_id: str,
    *,
    cost: float,
    currency: str = "CNY",
    units: float | None = None,
    unit_label: str | None = None,
    translator: Callable[[float, str], str] | None = None,
) -> str:
    """Render a one-line human label for a cost.

    Args:
        plugin_id: Plugin identifier (matches keys in ``COST_TRANSLATION_MAP``).
        cost: Total cost (vendor-native units).
        currency: 3-letter code or ``"credit"``.
        units: Optional unit count (e.g. ``5`` for 5 seconds).
        unit_label: Optional override for unit label (defaults to template's).
        translator: Optional ``(cost, currency) -> str`` to override default
            money formatting.

    Returns:
        Multi-segment string joined by " / ".  Always returns at least one
        segment (the money formatting), never an empty string.
    """
    parts: list[str] = []
    if units is not None and units >= 0:
        tpl = COST_TRANSLATION_MAP.get(plugin_id)
        label = unit_label or (tpl.unit_label_zh if tpl else "")
        if label:
            units_repr = f"{int(units)}" if float(units).is_integer() else f"{units:.1f}"
            parts.append(f"≈ {units_repr} {label}")
    money = (
        translator(cost, currency)
        if translator is not None
        else to_human_units(cost, currency)
    )
    parts.append(money)
    tpl = COST_TRANSLATION_MAP.get(plugin_id)
    if tpl and tpl.runtime_hint:
        parts.append(tpl.runtime_hint)
    return " / ".join(parts)


def get_template(plugin_id: str) -> CostTemplate | None:
    """Lookup a registered template (or None)."""
    return COST_TRANSLATION_MAP.get(plugin_id)


def to_dict_snapshot() -> dict[str, dict[str, Any]]:
    """Snapshot the full map (useful for /docs / debugging)."""
    return {
        pid: {
            "unit_label_zh": t.unit_label_zh,
            "per_unit_hint": t.per_unit_hint,
            "runtime_hint": t.runtime_hint,
        }
        for pid, t in COST_TRANSLATION_MAP.items()
    }

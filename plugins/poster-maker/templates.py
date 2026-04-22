"""Poster templates — pure-data layouts used by ``poster_engine.render_poster``.

A template defines: canvas size, background style, and a list of text slots
with rough position / size / color.  Coordinates are normalized (0-1) so
the same template works at any output size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Anchor = Literal["lt", "ct", "rt", "lm", "cm", "rm", "lb", "cb", "rb"]


@dataclass
class TextSlot:
    """One text region on a poster."""

    name: str                    # "title", "subtitle", "cta", ...
    label: str                   # human label for the UI
    placeholder: str             # default text
    nx: float = 0.5              # normalized x (0-1, anchor-relative)
    ny: float = 0.5
    nw: float = 0.9              # normalized width (max width)
    font_pct: float = 0.06       # font size as a fraction of canvas height
    color: str = "#ffffff"
    anchor: Anchor = "cm"
    weight: str = "bold"         # "bold" / "regular"
    max_lines: int = 2


@dataclass
class PosterTemplate:
    id: str
    name: str
    description: str
    width: int
    height: int
    background_color: str = "#1a1a2e"        # solid fallback if no image
    overlay_color: str = "#00000080"          # darken behind text (RGBA hex)
    slots: list[TextSlot] = field(default_factory=list)


# Ready-to-use templates.  Sizes match common social formats.

TEMPLATES: list[PosterTemplate] = [
    PosterTemplate(
        id="social-square",
        name="社交方图 (1:1)",
        description="朋友圈 / 小红书封面常用 1080x1080。",
        width=1080, height=1080,
        background_color="#0f172a", overlay_color="#00000099",
        slots=[
            TextSlot("title", "主标题", "请输入主标题",
                     nx=0.5, ny=0.45, nw=0.86, font_pct=0.10, anchor="cm"),
            TextSlot("subtitle", "副标题", "副标题",
                     nx=0.5, ny=0.62, nw=0.8, font_pct=0.04, anchor="cm",
                     weight="regular", max_lines=3),
            TextSlot("cta", "行动号召", "立即了解 →",
                     nx=0.5, ny=0.85, nw=0.6, font_pct=0.035, anchor="cm",
                     color="#fbbf24"),
        ],
    ),
    PosterTemplate(
        id="vertical-poster",
        name="竖版海报 (3:4)",
        description="活动海报 / 公众号竖图 900x1200。",
        width=900, height=1200,
        background_color="#581c87", overlay_color="#00000080",
        slots=[
            TextSlot("title", "主标题", "MAIN TITLE",
                     nx=0.5, ny=0.32, nw=0.88, font_pct=0.10, anchor="cm"),
            TextSlot("subtitle", "副标题", "subtitle goes here",
                     nx=0.5, ny=0.46, nw=0.8, font_pct=0.04, anchor="cm",
                     weight="regular", max_lines=4),
            TextSlot("date", "日期 / 信息", "2026.04.01",
                     nx=0.5, ny=0.78, nw=0.6, font_pct=0.045, anchor="cm",
                     color="#fbbf24"),
            TextSlot("cta", "底部行动", "扫码报名",
                     nx=0.5, ny=0.9, nw=0.6, font_pct=0.03, anchor="cm",
                     weight="regular"),
        ],
    ),
    PosterTemplate(
        id="banner-wide",
        name="横幅 (16:9)",
        description="网页 banner / 视频封面 1920x1080。",
        width=1920, height=1080,
        background_color="#1e293b", overlay_color="#00000099",
        slots=[
            TextSlot("title", "主标题", "请输入主标题",
                     nx=0.06, ny=0.45, nw=0.55, font_pct=0.09, anchor="lm"),
            TextSlot("subtitle", "副标题", "副标题描述一行字",
                     nx=0.06, ny=0.65, nw=0.5, font_pct=0.035, anchor="lm",
                     weight="regular", max_lines=2),
            TextSlot("cta", "行动号召", "立即查看 →",
                     nx=0.06, ny=0.82, nw=0.4, font_pct=0.03, anchor="lm",
                     color="#fbbf24"),
        ],
    ),
]


def get_template(template_id: str) -> PosterTemplate:
    for t in TEMPLATES:
        if t.id == template_id:
            return t
    raise KeyError(f"unknown poster template: {template_id}")


def list_templates() -> list[dict]:
    return [
        {
            "id": t.id, "name": t.name, "description": t.description,
            "width": t.width, "height": t.height,
            "slots": [{"name": s.name, "label": s.label, "placeholder": s.placeholder}
                      for s in t.slots],
        }
        for t in TEMPLATES
    ]

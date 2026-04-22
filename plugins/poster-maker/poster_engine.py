"""poster-maker — Pillow-based poster compositor.

Pure-Python (no external CLI) so it always works.  Optionally calls
``image-edit`` providers to AI-enhance the background.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageColor, ImageDraw, ImageFont

from templates import PosterTemplate, TextSlot


def _load_sibling(plugin_dir_name: str, module_name: str, alias: str):
    src = Path(__file__).resolve().parent.parent / plugin_dir_name / f"{module_name}.py"
    if alias in sys.modules: return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, src)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {src}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


# Optional: borrow image-edit's provider chooser (keeps a single source of truth)
try:
    _ie = _load_sibling("image-edit", "providers", "_oa_image_providers")
    select_image_provider = _ie.select_provider
except Exception:  # noqa: BLE001
    select_image_provider = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

__all__ = ["render_poster", "select_image_provider"]


# ── font discovery ─────────────────────────────────────────────────────


_FONT_CANDIDATES_BOLD = [
    # Windows
    "C:/Windows/Fonts/msyhbd.ttc",  "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/STHEITI.TTF", "C:/Windows/Fonts/simhei.ttf",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_FONT_CANDIDATES_REG = [
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _load_font(size: int, weight: str = "bold") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = _FONT_CANDIDATES_BOLD if weight == "bold" else _FONT_CANDIDATES_REG
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except (OSError, IOError):
            continue
    logger.warning("no TTF font found; falling back to PIL default (no CJK)")
    return ImageFont.load_default()


# ── rendering ──────────────────────────────────────────────────────────


def _wrap_text(draw: ImageDraw.ImageDraw, text: str,
               font: ImageFont.ImageFont, max_width: int, max_lines: int) -> list[str]:
    words = text.replace("\r", "").split("\n")
    lines: list[str] = []
    for paragraph in words:
        if not paragraph:
            lines.append("")
            continue
        # CJK-friendly: break on each char if no spaces, else on words
        cur = ""
        units = paragraph.split(" ") if " " in paragraph else list(paragraph)
        sep = " " if " " in paragraph else ""
        for u in units:
            test = (cur + sep + u) if cur else u
            try:
                w = draw.textlength(test, font=font)
            except AttributeError:
                w = font.getsize(test)[0]
            if w <= max_width:
                cur = test
            else:
                if cur: lines.append(cur)
                cur = u
                if len(lines) >= max_lines:
                    break
        if cur and len(lines) < max_lines:
            lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            lines[-1] = (lines[-1][:-1] if len(lines[-1]) > 0 else "") + "…"
    return lines


def _anchor_xy(anchor: str, x: int, y: int, w: int, h: int) -> tuple[int, int, str]:
    """Map our 9-anchor scheme to Pillow's ``anchor=`` arg + adjusted (x, y)."""
    a_map = {
        "lt": "la", "ct": "ma", "rt": "ra",
        "lm": "lm", "cm": "mm", "rm": "rm",
        "lb": "ld", "cb": "md", "rb": "rd",
    }
    return x, y, a_map.get(anchor, "mm")


def _draw_slot(draw: ImageDraw.ImageDraw, canvas: Image.Image,
               slot: TextSlot, value: str) -> None:
    if not value: return
    w, h = canvas.size
    font_size = max(14, int(slot.font_pct * h))
    font = _load_font(font_size, slot.weight)
    max_width = int(slot.nw * w)
    lines = _wrap_text(draw, value, font, max_width, slot.max_lines)
    if not lines: return

    # Vertical layout: stack lines around (nx, ny)
    line_h = int(font_size * 1.25)
    block_h = line_h * len(lines)
    cx = int(slot.nx * w)
    cy = int(slot.ny * h)

    if slot.anchor.endswith("t"): start_y = cy
    elif slot.anchor.endswith("b"): start_y = cy - block_h
    else: start_y = cy - block_h // 2

    for i, line in enumerate(lines):
        ly = start_y + i * line_h
        ax, ay, anchor_str = _anchor_xy(slot.anchor, cx, ly, max_width, line_h)
        # text shadow for legibility
        try:
            draw.text((ax + 2, ay + 2), line, font=font, fill="#00000080", anchor=anchor_str)
            draw.text((ax, ay), line, font=font, fill=slot.color, anchor=anchor_str)
        except TypeError:
            # very old Pillow (no anchor=) — fall back to top-left
            draw.text((ax, ay), line, font=font, fill=slot.color)


def render_poster(
    *,
    template: PosterTemplate,
    text_values: dict[str, str],
    background_image: Path | None = None,
    output_path: Path,
) -> Path:
    """Render a poster.  Returns ``output_path`` (PNG)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    bg_color = ImageColor.getcolor(template.background_color, "RGBA")
    canvas = Image.new("RGBA", (template.width, template.height), bg_color)

    if background_image and background_image.exists():
        try:
            bg = Image.open(background_image).convert("RGBA")
            bg = _cover_resize(bg, template.width, template.height)
            canvas.paste(bg, (0, 0), bg)
        except Exception as e:  # noqa: BLE001
            logger.warning("background image load failed: %s", e)

    # Overlay for text legibility
    if template.overlay_color:
        try:
            overlay_color = ImageColor.getcolor(template.overlay_color, "RGBA")
            overlay = Image.new("RGBA", canvas.size, overlay_color)
            canvas = Image.alpha_composite(canvas, overlay)
        except Exception:  # noqa: BLE001
            pass

    draw = ImageDraw.Draw(canvas)
    for slot in template.slots:
        value = text_values.get(slot.name) or slot.placeholder
        _draw_slot(draw, canvas, slot, value)

    canvas.convert("RGB").save(output_path, "PNG", optimize=True)
    return output_path


def _cover_resize(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize+crop ``img`` so that it fully covers ``w x h`` (CSS object-fit cover)."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))

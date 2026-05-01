"""Enterprise PPTX template diagnostics for ppt-maker."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from ppt_models import BUILTIN_TEMPLATES, BrandTokens


class TemplateDiagnosticError(RuntimeError):
    """Raised when a PPTX template cannot be diagnosed."""


class TemplateManager:
    """Diagnose PPTX templates and produce brand/layout fallback files."""

    INTERNAL_LAYOUTS = {
        "cover": "cover",
        "agenda": "agenda",
        "section": "section",
        "content": "content",
        "chart": "chart_bar",
        "closing": "closing",
    }

    def builtin_templates(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in BUILTIN_TEMPLATES]

    def diagnose_to_files(self, pptx_path: str | Path, output_dir: str | Path) -> dict[str, Any]:
        profile = self.diagnose(pptx_path)
        brand_tokens = self.brand_tokens(profile)
        layout_map = self.layout_map(profile)
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        profile_path = out / "template_profile.json"
        brand_path = out / "brand_tokens.json"
        layout_path = out / "layout_map.json"
        profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        brand_path.write_text(json.dumps(brand_tokens, ensure_ascii=False, indent=2), encoding="utf-8")
        layout_path.write_text(json.dumps(layout_map, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "template_profile": profile,
            "brand_tokens": brand_tokens,
            "layout_map": layout_map,
            "paths": {
                "profile_path": str(profile_path),
                "brand_tokens_path": str(brand_path),
                "layout_map_path": str(layout_path),
            },
        }

    def diagnose(self, pptx_path: str | Path) -> dict[str, Any]:
        path = Path(pptx_path)
        if not path.exists() or not path.is_file():
            raise TemplateDiagnosticError(f"Template not found: {path}")
        if path.suffix.lower() != ".pptx":
            raise TemplateDiagnosticError("Template file must be a .pptx file")
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                slide_size = self._slide_size(archive)
                layouts = self._layouts(archive, names)
                colors = self._detected_colors(archive, names)
                fonts = self._detected_fonts(archive, names)
        except zipfile.BadZipFile as exc:
            raise TemplateDiagnosticError("Invalid PPTX file") from exc

        warnings = self._warnings(layouts, colors, fonts)
        layout_names = [layout["name"].lower() for layout in layouts]
        return {
            "template_id": path.stem,
            "name": path.stem,
            "slide_size": slide_size,
            "detected_colors": colors,
            "detected_fonts": fonts,
            "layout_count": len(layouts),
            "layouts": layouts,
            "has_title_layout": any("title" in name or "cover" in name for name in layout_names),
            "has_section_layout": any("section" in name for name in layout_names),
            "has_content_layout": any("content" in name or "body" in name for name in layout_names),
            "has_picture_layout": any("picture" in name or "image" in name for name in layout_names),
            "warnings": warnings,
        }

    def brand_tokens(self, profile: dict[str, Any], manual: dict[str, Any] | None = None) -> dict[str, Any]:
        colors = profile.get("detected_colors") or []
        fonts = profile.get("detected_fonts") or []
        tokens = BrandTokens(
            primary_color=f"#{colors[0]}" if colors else "#3457D5",
            secondary_color=f"#{colors[1]}" if len(colors) > 1 else "#172033",
            accent_color=f"#{colors[2]}" if len(colors) > 2 else "#FFB000",
            font_heading=fonts[0] if fonts else "Microsoft YaHei",
            font_body=fonts[1] if len(fonts) > 1 else (fonts[0] if fonts else "Microsoft YaHei"),
        ).model_dump(mode="json")
        if manual:
            tokens.update({key: value for key, value in manual.items() if value is not None})
        return tokens

    def layout_map(self, profile: dict[str, Any]) -> dict[str, Any]:
        layouts = profile.get("layouts", [])
        names = [layout.get("name", "") for layout in layouts]
        result: dict[str, Any] = {}
        for internal, fallback in self.INTERNAL_LAYOUTS.items():
            matched = self._match_layout(internal, names)
            result[internal] = {
                "pptx_layout": matched,
                "fallback": fallback,
                "source": "pptx" if matched else "builtin",
            }
        return result

    def _slide_size(self, archive: zipfile.ZipFile) -> dict[str, Any]:
        try:
            root = ElementTree.fromstring(archive.read("ppt/presentation.xml"))
        except KeyError:
            return {"width": 13.333, "height": 7.5, "unit": "inch", "source": "default"}
        ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
        node = root.find(".//p:sldSz", ns)
        if node is None:
            return {"width": 13.333, "height": 7.5, "unit": "inch", "source": "default"}
        cx = int(node.attrib.get("cx", "12192000"))
        cy = int(node.attrib.get("cy", "6858000"))
        return {"width": round(cx / 914400, 3), "height": round(cy / 914400, 3), "unit": "inch"}

    def _layouts(self, archive: zipfile.ZipFile, names: list[str]) -> list[dict[str, Any]]:
        layout_names = sorted(
            name
            for name in names
            if name.startswith("ppt/slideLayouts/slideLayout") and name.endswith(".xml")
        )
        layouts = []
        for index, name in enumerate(layout_names, start=1):
            xml = archive.read(name).decode("utf-8", errors="ignore")
            layouts.append(
                {
                    "id": f"layout_{index}",
                    "name": self._layout_name(xml) or f"Layout {index}",
                    "path": name,
                    "placeholders": self._placeholder_types(xml),
                }
            )
        return layouts

    def _detected_colors(self, archive: zipfile.ZipFile, names: list[str]) -> list[str]:
        colors: list[str] = []
        for name in names:
            if not (name.startswith("ppt/theme/") or name.startswith("ppt/slideMasters/")):
                continue
            text = archive.read(name).decode("utf-8", errors="ignore")
            colors.extend(re.findall(r'(?:val|srgbClr)="([0-9A-Fa-f]{6})"', text))
        return list(dict.fromkeys(color.upper() for color in colors))[:12]

    def _detected_fonts(self, archive: zipfile.ZipFile, names: list[str]) -> list[str]:
        fonts: list[str] = []
        for name in names:
            if not (name.startswith("ppt/theme/") or name.startswith("ppt/slideMasters/")):
                continue
            text = archive.read(name).decode("utf-8", errors="ignore")
            fonts.extend(re.findall(r'typeface="([^"]+)"', text))
        cleaned = [font for font in fonts if font and font != "+mj-lt"]
        return list(dict.fromkeys(cleaned))[:8]

    def _layout_name(self, xml: str) -> str | None:
        match = re.search(r'<p:cSld[^>]*name="([^"]+)"', xml)
        return match.group(1) if match else None

    def _placeholder_types(self, xml: str) -> list[str]:
        types = re.findall(r"<p:ph[^>]*type=\"([^\"]+)\"", xml)
        return list(dict.fromkeys(types))

    def _match_layout(self, internal: str, names: list[str]) -> str | None:
        keywords = {
            "cover": ["cover", "title"],
            "agenda": ["agenda", "toc"],
            "section": ["section", "divider"],
            "content": ["content", "body", "title and content"],
            "chart": ["chart", "graph"],
            "closing": ["closing", "thank"],
        }[internal]
        for name in names:
            lowered = name.lower()
            if any(keyword in lowered for keyword in keywords):
                return name
        return None

    def _warnings(self, layouts: list[dict[str, Any]], colors: list[str], fonts: list[str]) -> list[str]:
        warnings: list[str] = []
        if not layouts:
            warnings.append("未检测到 slide layout，将使用内置 fallback layout。")
        if not colors:
            warnings.append("未检测到主题颜色，请在 UI 中手动补充品牌色。")
        if not fonts:
            warnings.append("未检测到主题字体，请在 UI 中手动补充字体。")
        return warnings


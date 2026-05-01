"""Optional dependency discovery for word-maker."""

from __future__ import annotations

import importlib.util

OPTIONAL_GROUPS: dict[str, list[str]] = {
    "core": ["docxtpl", "docx", "aiosqlite"],
    "excel": ["openpyxl"],
    "ppt": ["pptx"],
    "pdf": ["pypdf"],
}


def list_optional_groups() -> dict[str, list[str]]:
    return {key: list(value) for key, value in OPTIONAL_GROUPS.items()}


def check_optional_deps() -> dict[str, dict[str, bool]]:
    return {
        group: {name: importlib.util.find_spec(name) is not None for name in modules}
        for group, modules in OPTIONAL_GROUPS.items()
    }


"""Optional dependency discovery for word-maker."""

from __future__ import annotations

import importlib.util
from typing import Any

OPTIONAL_GROUPS: dict[str, list[str]] = {
    "core": ["docxtpl", "docx", "aiosqlite"],
    "excel": ["openpyxl"],
    "ppt": ["pptx"],
    "pdf": ["pypdf"],
}

DEPENDENCY_GROUPS: dict[str, list[dict[str, Any]]] = {
    "host": [
        {
            "module": "brain.access",
            "package": "OpenAkita host permission",
            "required": False,
            "purpose": "Outline generation, requirement clarification, field extraction, and section rewriting.",
            "impact": "AI-assisted planning is unavailable; manual project creation and template rendering still work.",
        }
    ],
    "core": [
        {
            "module": "docx",
            "package": "python-docx",
            "required": True,
            "purpose": "Read and write editable DOCX files.",
            "impact": "DOCX source parsing and document export cannot run.",
        },
        {
            "module": "aiosqlite",
            "package": "aiosqlite",
            "required": True,
            "purpose": "Store project history, source records, templates, and draft versions.",
            "impact": "Project creation and history storage cannot run.",
        },
    ],
    "template": [
        {
            "module": "docxtpl",
            "package": "docxtpl",
            "required": False,
            "purpose": "Render DOCX templates with Jinja loops and conditionals.",
            "impact": "Only simple {{ variable }} placeholders can be rendered.",
        }
    ],
    "source_readers": [
        {
            "module": "openpyxl",
            "package": "openpyxl",
            "required": False,
            "purpose": "Extract text from XLSX source files.",
            "impact": "XLSX uploads cannot be parsed.",
        },
        {
            "module": "pptx",
            "package": "python-pptx",
            "required": False,
            "purpose": "Extract text from PPTX source files.",
            "impact": "PPTX uploads cannot be parsed.",
        },
        {
            "module": "pypdf",
            "package": "pypdf",
            "required": False,
            "purpose": "Extract text from PDF source files.",
            "impact": "PDF uploads cannot be parsed.",
        },
    ],
    "ppt_handoff": [
        {
            "module": "pptx",
            "package": "python-pptx",
            "required": False,
            "purpose": "Prepare compatibility with future PPT handoff workflows.",
            "impact": "Word document brief metadata can still be published, but PPTX source extraction is unavailable.",
        }
    ],
    "test_runtime": [
        {
            "module": "pytest",
            "package": "pytest",
            "required": False,
            "purpose": "Run the plugin test suite during development.",
            "impact": "Automated local test feedback is unavailable.",
        }
    ],
}


def _module_available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def list_optional_groups() -> dict[str, list[str]]:
    return {key: list(value) for key, value in OPTIONAL_GROUPS.items()}


def check_optional_deps() -> dict[str, dict[str, bool]]:
    return {
        group: {name: _module_available(name) for name in modules}
        for group, modules in OPTIONAL_GROUPS.items()
    }


def build_dependency_report(*, brain_available: bool = False) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    missing_required: list[str] = []
    missing_optional: list[str] = []

    for group_name, definitions in DEPENDENCY_GROUPS.items():
        checks: list[dict[str, Any]] = []
        for definition in definitions:
            module = str(definition["module"])
            installed = bool(brain_available) if module == "brain.access" else _module_available(module)
            item = {
                "module": module,
                "package": definition["package"],
                "installed": installed,
                "required": bool(definition["required"]),
                "purpose": definition["purpose"],
                "impact": definition["impact"],
            }
            checks.append(item)
            if not installed:
                if item["required"]:
                    missing_required.append(module)
                else:
                    missing_optional.append(module)
        groups[group_name] = checks

    return {
        "summary": {
            "ok": not missing_required,
            "brain_available": bool(brain_available),
            "missing_required": sorted(set(missing_required)),
            "missing_optional": sorted(set(missing_optional)),
        },
        "groups": groups,
    }


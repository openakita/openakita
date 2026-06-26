"""Wizard workflow helpers for word-maker."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any

from word_source_loader import load_source
from word_task_manager import WordTaskManager, source_path_key
from word_template_engine import extract_template_vars


async def load_project_draft(manager: WordTaskManager, project_id: str) -> dict[str, Any]:
    draft: dict[str, Any] = {"source_paths": [], "fields": {}, "outline": {}}
    seen_paths: set[str] = set()
    for source in await manager.list_sources(project_id):
        path = str(source.get("path") or "").strip()
        if not path:
            continue
        key = source_path_key(path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        draft["source_paths"].append(path)
    versions = await manager.list_versions(project_id)
    if versions:
        latest = versions[0]
        if isinstance(latest.get("outline"), dict):
            draft["outline"] = latest["outline"]
        if isinstance(latest.get("fields"), dict):
            draft["fields"] = latest["fields"]
    template = await manager.latest_template(project_id)
    if template and template.get("path"):
        draft["template_path"] = template["path"]
    return draft


def _source_label(path: Path, source: dict[str, Any] | None = None) -> str:
    if source and source.get("filename"):
        return str(source["filename"])
    name = path.name or "source"
    if name and name != "source":
        return name
    if source and source.get("path"):
        return str(source["path"]).replace("\\", "/").split("/")[-1]
    return name


async def collect_project_sources_text(
    manager: WordTaskManager,
    project_id: str,
    *,
    extra_paths: list[str] | None = None,
    resolve_path: Callable[[str], Path] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Aggregate source text from draft paths and registered project sources."""
    chunks: list[str] = []
    meta: list[dict[str, Any]] = []
    seen: set[str] = set()
    db_sources = await manager.list_sources(project_id)
    source_by_key: dict[str, dict[str, Any]] = {}
    for source in db_sources:
        path = str(source.get("path") or "").strip()
        if path:
            source_by_key[source_path_key(path)] = source

    candidates: list[tuple[str, dict[str, Any] | None]] = []

    for raw in extra_paths or []:
        key = str(raw).replace("\\", "/")
        norm = source_path_key(key)
        if key and norm not in seen:
            seen.add(norm)
            candidates.append((key, source_by_key.get(norm)))

    for source in db_sources:
        path = str(source.get("path") or "").strip()
        if not path:
            continue
        norm = source_path_key(path)
        if norm in seen:
            continue
        seen.add(norm)
        candidates.append((path, source))

    for raw, source in candidates:
        if source is None:
            source = source_by_key.get(source_path_key(raw))
        path = Path(raw)
        if resolve_path is not None:
            try:
                path = resolve_path(raw)
            except Exception:
                path = Path(raw)
        result = load_source(path)
        text = result.text.strip() if result.ok else ""
        from_preview = False
        if not text and source:
            preview = str(source.get("text_preview") or "").strip()
            if preview:
                text = preview
                from_preview = True
        item = {
            "path": str(raw).replace("\\", "/"),
            "filename": (source or {}).get("filename") or path.name,
            "loaded": bool(text),
            "chars": len(text),
            "error": "" if text else (result.error or (source or {}).get("error_message") or "empty"),
            "parse_status": (source or {}).get("parse_status"),
            "from_preview": from_preview,
        }
        meta.append(item)
        if not text:
            continue
        label = _source_label(path, source)
        chunks.append(f"--- {label} ---\n{text[:1200]}")
    return "\n\n".join(chunks), meta


def infer_wizard_step(
    project: dict[str, Any] | None,
    *,
    template_path: str | None,
    source_paths: list[str],
    outline: dict[str, Any] | None,
    missing_fields: list[str],
) -> int:
    if project is None:
        return 1
    if project.get("output_path") or project.get("status") == "succeeded":
        return 4
    if (outline and outline.get("sections")) or missing_fields or template_path:
        return 3
    if template_path or source_paths:
        return 2
    return 2


def _absolute_template_path(template_path: str | None, workspace: Path | None) -> str | None:
    if not template_path:
        return None
    raw = Path(template_path)
    if raw.is_absolute():
        return str(raw) if raw.exists() else None
    if workspace is not None:
        candidate = (workspace / raw).resolve()
        if candidate.exists():
            return str(candidate)
    return str(raw) if raw.exists() else None


async def build_workflow_state(
    manager: WordTaskManager,
    project_id: str,
    *,
    draft: dict[str, Any] | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    project = await manager.get_project(project_id)
    if project is None:
        raise ValueError(f"Unknown project: {project_id}")

    draft = draft or {}
    template_path = draft.get("template_path") or None
    source_paths = list(draft.get("source_paths") or [])
    outline = draft.get("outline") if isinstance(draft.get("outline"), dict) else {}
    fields = draft.get("fields") if isinstance(draft.get("fields"), dict) else {}

    missing_fields: list[str] = []
    template_variables: list[str] = []
    template_source = str(draft.get("template_source") or "")
    inspect_path = _absolute_template_path(template_path, workspace)
    if inspect_path:
        inspection = extract_template_vars(inspect_path, context=fields)
        template_variables = inspection.variables
        missing_fields = [
            name for name in inspection.variables if not str(fields.get(name) or "").strip()
        ]
    elif template_path and template_source == "default":
        template_variables = list(draft.get("template_variables") or [])

    step = infer_wizard_step(
        project,
        template_path=template_path,
        source_paths=source_paths,
        outline=outline,
        missing_fields=missing_fields,
    )
    next_action = {
        1: "create_project",
        2: "upload_sources_or_template",
        3: "confirm_outline_and_fields",
        4: "download_or_retry_render",
    }[step]

    using_default = template_source == "default" or (
        not draft.get("template_path") and bool(template_path)
    )
    checklist = {
        "project_created": True,
        "sources_uploaded": len(source_paths) > 0,
        "template_selected": bool(template_path) or using_default,
        "outline_ready": bool(outline.get("sections")),
        "fields_ready": not missing_fields if (template_path or inspect_path) else True,
        "output_ready": bool(project.get("output_path")),
    }

    return {
        "project_id": project_id,
        "wizard_step": step,
        "next_action": next_action,
        "project_status": project.get("status"),
        "checklist": checklist,
        "missing_fields": missing_fields,
        "template_variables": template_variables,
        "template_source": template_source or None,
        "using_default_template": using_default,
        "effective_template_path": template_path,
        "output_path": project.get("output_path"),
        "error_message": project.get("error_message"),
    }

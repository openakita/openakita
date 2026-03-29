"""
LLM-facing skill exposure helpers.

This module centralizes how skills are described to the model so we do not
maintain separate, drifting explanations in handlers, catalogs, and workspace
maps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .loader import _builtin_skills_root
from .registry import SkillEntry

_SCRIPT_SUFFIXES = frozenset({".py", ".sh", ".bash", ".js", ".ts", ".mjs"})
_SCRIPT_IGNORE = frozenset({"__init__.py", "__pycache__"})

_ORIGIN_LABELS = {
    "builtin": "builtin",
    "user_workspace": "user-workspace",
    "project": "project",
    "external": "external",
    "unknown": "unknown",
}


@dataclass(frozen=True)
class SkillExposure:
    skill_id: str
    name: str
    description: str
    system: bool
    disabled: bool
    disable_model_invocation: bool
    origin: str
    origin_label: str
    skill_path: str | None
    skill_dir: str | None
    root_dir: str | None
    scripts: tuple[str, ...]
    references: tuple[str, ...]
    instruction_only: bool
    tool_name: str | None
    handler: str | None
    category: str | None


def _resolve_settings_roots() -> tuple[Path | None, Path | None, Path | None]:
    try:
        from ..config import settings

        project_root = settings.project_root
        user_skills_dir = settings.skills_path
    except Exception:
        project_root = None
        user_skills_dir = None

    builtin_root = _builtin_skills_root()
    return project_root, user_skills_dir, builtin_root


def _normalize_root(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        return path.resolve()
    except Exception:
        return path


def get_skill_source_roots(
    *,
    project_root: Path | None = None,
    user_skills_dir: Path | None = None,
    builtin_root: Path | None = None,
) -> list[tuple[str, Path]]:
    if project_root is None and user_skills_dir is None and builtin_root is None:
        project_root, user_skills_dir, builtin_root = _resolve_settings_roots()

    builtin_root = _normalize_root(builtin_root)
    user_skills_dir = _normalize_root(user_skills_dir)
    project_root = _normalize_root(project_root)
    project_skills_dir = project_root / "skills" if project_root is not None else None

    roots: list[tuple[str, Path]] = []
    for origin, path in [
        ("builtin", builtin_root),
        ("user_workspace", user_skills_dir),
        ("project", project_skills_dir),
    ]:
        if path is None:
            continue
        roots.append((origin, path))
    return roots


def _classify_skill_origin(
    skill_dir: Path | None,
    *,
    project_root: Path | None = None,
    user_skills_dir: Path | None = None,
    builtin_root: Path | None = None,
) -> tuple[str, Path | None]:
    if skill_dir is None:
        return "unknown", None

    for origin, root in get_skill_source_roots(
        project_root=project_root,
        user_skills_dir=user_skills_dir,
        builtin_root=builtin_root,
    ):
        try:
            if skill_dir == root or skill_dir.is_relative_to(root):
                return origin, root
        except Exception:
            continue

    return "external", None


def _list_scripts(skill_dir: Path | None) -> tuple[str, ...]:
    if skill_dir is None or not skill_dir.is_dir():
        return ()

    scripts: list[str] = []

    scripts_dir = skill_dir / "scripts"
    if scripts_dir.is_dir():
        for file in sorted(scripts_dir.rglob("*")):
            if (
                file.is_file()
                and file.suffix in _SCRIPT_SUFFIXES
                and file.name not in _SCRIPT_IGNORE
            ):
                rel = file.relative_to(scripts_dir)
                scripts.append(f"scripts/{rel.as_posix()}")

    for file in sorted(skill_dir.iterdir()):
        if file.is_file() and file.suffix in _SCRIPT_SUFFIXES and file.name not in _SCRIPT_IGNORE:
            scripts.append(file.name)

    return tuple(scripts)


def _list_references(skill_dir: Path | None) -> tuple[str, ...]:
    if skill_dir is None or not skill_dir.is_dir():
        return ()

    references_dir = skill_dir / "references"
    if not references_dir.is_dir():
        return ()

    refs = [
        file.name
        for file in sorted(references_dir.iterdir())
        if file.is_file() and file.suffix.lower() == ".md"
    ]
    return tuple(refs)


def build_skill_exposure(
    entry: SkillEntry,
    *,
    project_root: Path | None = None,
    user_skills_dir: Path | None = None,
    builtin_root: Path | None = None,
) -> SkillExposure:
    skill_path = None
    skill_dir = None
    if entry.skill_path:
        try:
            skill_path = Path(entry.skill_path).resolve()
            skill_dir = skill_path.parent
        except Exception:
            skill_path = Path(entry.skill_path)
            skill_dir = skill_path.parent

    origin, root_dir = _classify_skill_origin(
        skill_dir,
        project_root=project_root,
        user_skills_dir=user_skills_dir,
        builtin_root=builtin_root,
    )

    scripts = _list_scripts(skill_dir)
    references = _list_references(skill_dir)

    return SkillExposure(
        skill_id=entry.skill_id,
        name=entry.name,
        description=entry.description,
        system=entry.system,
        disabled=entry.disabled,
        disable_model_invocation=entry.disable_model_invocation,
        origin=origin,
        origin_label=_ORIGIN_LABELS.get(origin, origin),
        skill_path=str(skill_path) if skill_path else entry.skill_path,
        skill_dir=str(skill_dir) if skill_dir else None,
        root_dir=str(root_dir) if root_dir else None,
        scripts=scripts,
        references=references,
        instruction_only=len(scripts) == 0,
        tool_name=entry.tool_name,
        handler=entry.handler,
        category=entry.category,
    )

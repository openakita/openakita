"""DOCX template inspection and rendering for word-maker."""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

VAR_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*}}")
CONTROL_RE = re.compile(r"{%\s*(?:for|if|elif|set)\s+([^%]+?)\s*%}")


@dataclass(slots=True)
class TemplateInspection:
    template_path: str
    variables: list[str]
    missing: list[str]
    ok: bool
    engine: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RenderResult:
    output_path: str
    ok: bool
    engine: str
    missing: list[str]
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_docx_xml(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as archive:
        chunks: list[str] = []
        for name in archive.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                chunks.append(archive.read(name).decode("utf-8", errors="replace"))
    return "\n".join(chunks)


def _extract_with_docxtpl(path: Path) -> set[str] | None:
    try:
        from docxtpl import DocxTemplate
    except ImportError:
        return None
    template = DocxTemplate(str(path))
    return set(template.get_undeclared_template_variables())


def _extract_with_regex(path: Path) -> set[str]:
    xml = _read_docx_xml(path)
    variables = {match.group(1).split(".")[0] for match in VAR_RE.finditer(xml)}
    for match in CONTROL_RE.finditer(xml):
        fragment = match.group(1)
        for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", fragment):
            if token not in {"for", "if", "in", "and", "or", "not", "else", "True", "False"}:
                variables.add(token)
    return variables


def extract_template_vars(
    template_path: str | Path,
    *,
    context: dict[str, Any] | None = None,
) -> TemplateInspection:
    path = Path(template_path)
    if not path.exists():
        return TemplateInspection(str(path), [], [], False, "none", "Template not found")
    if path.suffix.lower() != ".docx":
        return TemplateInspection(str(path), [], [], False, "none", "Only DOCX templates are supported")
    try:
        variables = _extract_with_docxtpl(path)
        engine = "docxtpl" if variables is not None else "regex"
        if variables is None:
            variables = _extract_with_regex(path)
    except Exception as exc:
        return TemplateInspection(str(path), [], [], False, "docx", str(exc))

    provided = set((context or {}).keys())
    missing = sorted(var for var in variables if var not in provided)
    return TemplateInspection(
        str(path),
        sorted(variables),
        missing,
        ok=not missing,
        engine=engine,
    )


def _render_with_docxtpl(path: Path, output_path: Path, context: dict[str, Any]) -> bool:
    try:
        from docxtpl import DocxTemplate
    except ImportError:
        return False
    template = DocxTemplate(str(path))
    template.render(context)
    template.save(str(output_path))
    return True


def _render_xml_text(xml: str, context: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = context
        for part in key.split("."):
            value = value.get(part, "") if isinstance(value, dict) else getattr(value, part, "")
        return escape(str(value))

    return VAR_RE.sub(replace, xml)


def _render_with_regex(path: Path, output_path: Path, context: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(path, "r") as archive:
            archive.extractall(tmp_dir)
        for xml_path in (tmp_dir / "word").glob("*.xml"):
            xml_path.write_text(
                _render_xml_text(xml_path.read_text(encoding="utf-8"), context),
                encoding="utf-8",
            )
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for file_path in tmp_dir.rglob("*"):
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(tmp_dir).as_posix())


def render_template(
    template_path: str | Path,
    output_path: str | Path,
    context: dict[str, Any],
    *,
    allow_missing: bool = False,
) -> RenderResult:
    template = Path(template_path)
    output = Path(output_path)
    inspection = extract_template_vars(template, context=context)
    if not inspection.ok and not allow_missing:
        return RenderResult(str(output), False, inspection.engine, inspection.missing, "Missing template variables")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        used_docxtpl = _render_with_docxtpl(template, output, context)
        if not used_docxtpl:
            # Simple placeholders render without docxtpl. Complex loops still need docxtpl.
            if any("{%" in line for line in _read_docx_xml(template).splitlines()):
                shutil.copyfile(template, output)
                return RenderResult(
                    str(output),
                    False,
                    "regex",
                    inspection.missing,
                    "docxtpl is required for control-flow tags",
                )
            _render_with_regex(template, output, context)
    except Exception as exc:
        return RenderResult(str(output), False, inspection.engine, inspection.missing, str(exc))
    return RenderResult(str(output), True, "docxtpl" if used_docxtpl else "regex", inspection.missing)


"""SkillManifest loader — parse ``SKILL.md`` frontmatter into a typed dict.

C0.4 from ``D:\\OpenAkita_AI_Video\\findings\\_summary_to_plan.md``:

    早期假设："抄 OpenMontage 的 ``skill_loader.py``"
    真实情况：OpenMontage 的实现耦合了 OM 的目录约定，复用代价 > 重写
    影响：OpenAkita 自建一个轻量、零依赖的解析器，专注于 SKILL.md 头部

A *skill* in OpenAkita is a CLI script + an ``SKILL.md`` describing
when/how the agent should invoke it.  The frontmatter is a tiny YAML-ish
``key: value`` block at the top of the markdown so both humans and the
agent can index skills without reading the whole document.

Frontmatter format (``SKILL.md``)::

    ---
    name: highlight-cutter
    description: Cut a 30-second highlight reel from a long video.
    triggers:
      - "highlight reel"
      - "cut a clip"
    inputs:
      - source_video: path
      - target_duration_sec: int
    outputs:
      - reel_video: path
    requires:
      - ffmpeg
    version: 0.4.0
    ---

    # Highlight Cutter

    ... rest of the markdown is the skill body, parsed separately ...

This loader returns:

* the parsed frontmatter as a :class:`SkillManifest` dict-backed dataclass,
* the remaining body text (so the host can render it as the "details"
  pane without re-reading the file),
* validation errors as :class:`SkillManifestError` (one error per call —
  fail loudly, the agent should never see a half-parsed skill).

We do **not** depend on ``PyYAML`` — the schema is small enough that a
hand-rolled parser is more reliable (no version skew, no installation
surprises on Windows).  The parser supports:

* scalar lines (``key: value``),
* ``key:`` followed by ``  - item`` lines for list values,
* ``key:`` followed by ``  subkey: subvalue`` lines for nested map values.

Anything more elaborate is intentionally rejected — skills are meant to
be index entries, not configuration files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The canonical frontmatter delimiter.  Three dashes alone on a line.
_DELIMITER = "---"

# The set of keys we *recognise*.  Extra keys are kept in ``extra`` so
# downstream tooling can innovate without an SDK release, but unknown keys
# do NOT participate in validation.
_KNOWN_SCALAR_KEYS: frozenset[str] = frozenset({
    "name", "description", "version", "category", "owner",
})
_KNOWN_LIST_KEYS: frozenset[str] = frozenset({
    "triggers", "requires", "tags",
})
_KNOWN_MAP_LIST_KEYS: frozenset[str] = frozenset({
    "inputs", "outputs",
})

_REQUIRED_KEYS: frozenset[str] = frozenset({"name", "description"})

# Identifiers must be slug-safe so the agent can refer to them via tool
# names (``call_skill("highlight-cutter")`` etc).
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class SkillManifestError(ValueError):
    """Raised when an ``SKILL.md`` frontmatter is malformed.

    Inherits from ``ValueError`` so the host's "load all skills" loop can
    catch with a single ``except ValueError`` per file.  The ``path``
    attribute (when set) lets log lines say which file failed.
    """

    def __init__(self, message: str, *, path: Path | None = None) -> None:
        super().__init__(message if path is None else f"{path}: {message}")
        self.path = path


@dataclass(frozen=True)
class SkillManifest:
    """Parsed frontmatter of an ``SKILL.md`` file.

    Attributes:
        name: Slug-safe skill identifier (``[a-z][a-z0-9_-]{1,63}``).
        description: One-sentence summary the agent uses to decide
            *whether* to invoke the skill.
        triggers: Phrases the agent should treat as "user wants this
            skill" hints.  Free-form, used by the agent's planner.
        inputs: Required input parameters as
            ``[{name: type}, ...]`` pairs (one-key dicts).  Type hints
            are documentary — the actual schema is the CLI's argparse.
        outputs: What the skill produces (same shape as ``inputs``).
        requires: External binaries / packages the skill depends on
            (``ffmpeg``, ``yt-dlp``, ``whisper-cpp``).  The host's
            dependency gate uses this to surface missing-dep warnings.
        version: SemVer string for the skill body (not the SDK).
        category: Optional grouping label for the UI ("video", "audio").
        owner: Optional contact / repo owner.
        tags: Free-form labels for search / filtering.
        extra: Any frontmatter keys we don't recognise — kept verbatim.
    """

    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    inputs: list[dict[str, str]] = field(default_factory=list)
    outputs: list[dict[str, str]] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    version: str = ""
    category: str = ""
    owner: str = ""
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": list(self.triggers),
            "inputs": [dict(d) for d in self.inputs],
            "outputs": [dict(d) for d in self.outputs],
            "requires": list(self.requires),
            "version": self.version,
            "category": self.category,
            "owner": self.owner,
            "tags": list(self.tags),
            "extra": dict(self.extra),
        }


@dataclass(frozen=True)
class ParsedSkill:
    """Return value of :func:`load_skill`."""

    manifest: SkillManifest
    body: str  # markdown body after the closing frontmatter delimiter


def parse_skill_frontmatter(text: str, *, path: Path | None = None) -> ParsedSkill:
    """Parse a complete ``SKILL.md`` text into manifest + body.

    Raises:
        SkillManifestError: when the frontmatter is missing, malformed,
            or violates the required-keys / name-slug rules.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _DELIMITER:
        raise SkillManifestError(
            f"missing leading {_DELIMITER!r} delimiter on first line",
            path=path,
        )

    closing_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _DELIMITER:
            closing_idx = i
            break
    if closing_idx is None:
        raise SkillManifestError(
            f"missing closing {_DELIMITER!r} delimiter",
            path=path,
        )

    fm_lines = lines[1:closing_idx]
    body = "\n".join(lines[closing_idx + 1 :]).lstrip("\n")
    raw = _parse_frontmatter_block(fm_lines, path=path)

    missing = sorted(_REQUIRED_KEYS - set(raw.keys()))
    if missing:
        raise SkillManifestError(
            f"missing required key(s): {missing}", path=path
        )

    name = str(raw.get("name", "")).strip()
    if not _NAME_RE.match(name):
        raise SkillManifestError(
            f"name {name!r} must match {_NAME_RE.pattern!r}", path=path
        )

    extra = {
        k: v for k, v in raw.items()
        if k not in (
            _KNOWN_SCALAR_KEYS | _KNOWN_LIST_KEYS | _KNOWN_MAP_LIST_KEYS
        )
    }

    manifest = SkillManifest(
        name=name,
        description=str(raw.get("description", "")).strip(),
        triggers=_as_str_list(raw.get("triggers")),
        inputs=_as_map_list(raw.get("inputs")),
        outputs=_as_map_list(raw.get("outputs")),
        requires=_as_str_list(raw.get("requires")),
        version=str(raw.get("version", "")).strip(),
        category=str(raw.get("category", "")).strip(),
        owner=str(raw.get("owner", "")).strip(),
        tags=_as_str_list(raw.get("tags")),
        extra=extra,
    )
    return ParsedSkill(manifest=manifest, body=body)


def load_skill(path: str | Path) -> ParsedSkill:
    """Read ``path`` and parse it.  Convenience over :func:`parse_skill_frontmatter`."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return parse_skill_frontmatter(text, path=p)


# ── helpers ───────────────────────────────────────────────────────────


def _parse_frontmatter_block(
    lines: list[str], *, path: Path | None
) -> dict[str, Any]:
    """Tiny YAML-ish parser scoped to our skill schema.

    Supports:
        scalar:  ``key: value``
        list:    ``key:`` then indented ``  - item`` lines
        map list ``key:`` then indented ``  - subkey: subvalue`` lines
                  (each list element is a one-key dict)

    Anything outside these three shapes raises :class:`SkillManifestError`.
    """
    result: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw.startswith(" ") or raw.startswith("\t"):
            raise SkillManifestError(
                f"unexpected indented line at row {i + 1}: {raw!r}",
                path=path,
            )
        if ":" not in raw:
            raise SkillManifestError(
                f"missing ':' in row {i + 1}: {raw!r}", path=path
            )

        key, _, after = raw.partition(":")
        key = key.strip()
        after_value = after.strip()

        if after_value:
            result[key] = after_value
            i += 1
            continue

        items, advance = _consume_indented_block(lines, i + 1, path=path)
        result[key] = items
        i += advance + 1
    return result


def _consume_indented_block(
    lines: list[str], start: int, *, path: Path | None
) -> tuple[list[Any], int]:
    """Read consecutive indented lines starting at ``lines[start]``.

    Returns the parsed items and the number of *block* lines consumed
    (so the caller advances ``start + advance``).  Each item is either a
    plain string (``- item``) or a one-key dict (``- name: type``).
    Mixing the two within one block raises :class:`SkillManifestError`.
    """
    items: list[Any] = []
    saw_string = False
    saw_dict = False
    consumed = 0

    j = start
    while j < len(lines):
        raw = lines[j]
        if not raw.strip():
            consumed += 1
            j += 1
            continue
        if not (raw.startswith(" ") or raw.startswith("\t")):
            break
        stripped = raw.strip()
        if not stripped.startswith("-"):
            raise SkillManifestError(
                f"indented block expects '- ' bullets, got {raw!r} at row {j + 1}",
                path=path,
            )
        item_text = stripped[1:].strip()
        if ":" in item_text:
            sub_key, _, sub_val = item_text.partition(":")
            items.append({sub_key.strip(): sub_val.strip()})
            saw_dict = True
        else:
            items.append(_strip_quotes(item_text))
            saw_string = True
        if saw_string and saw_dict:
            raise SkillManifestError(
                f"indented block mixes scalars and key:value items at row {j + 1}",
                path=path,
            )
        consumed += 1
        j += 1
    return items, consumed


def _strip_quotes(s: str) -> str:
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        return s[1:-1]
    return s


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _as_map_list(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SkillManifestError(
            f"expected list of 'name: type' pairs, got {type(value).__name__}"
        )
    out: list[dict[str, str]] = []
    for entry in value:
        if isinstance(entry, dict):
            out.append({str(k): str(v) for k, v in entry.items()})
        else:
            raise SkillManifestError(
                f"map-list entry must be a 'name: type' pair, got {entry!r}"
            )
    return out


__all__ = [
    "ParsedSkill",
    "SkillManifest",
    "SkillManifestError",
    "load_skill",
    "parse_skill_frontmatter",
]

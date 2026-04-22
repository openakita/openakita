"""env_any loader — parse ``env_any: [VAR_A, VAR_B]`` from SKILL.md frontmatter.

OpenMontage uses this convention to mean *"this skill works as long as **any
one** of these env vars is set"* (smart degradation).  In OpenMontage it is
purely declarative — there is no Python loader.

Per audit3 decision (C0.4) we ship the loader here so plugins can reuse it
without each rolling their own YAML parser.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EnvAnyEntry:
    """Result of probing one SKILL.md.

    Attributes:
        skill_path: Absolute path to the parsed SKILL.md.
        required: ``env_any`` list — vars *any* of which satisfies the skill.
        present: Subset of ``required`` actually defined in os.environ.
        satisfied: ``True`` if at least one ``required`` var is present, OR
            ``required`` was empty (skill has no env requirement).
        first_present: First env var found (deterministic order).
    """

    skill_path: Path
    required: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    satisfied: bool = True
    first_present: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_path": str(self.skill_path),
            "required": list(self.required),
            "present": list(self.present),
            "satisfied": self.satisfied,
            "first_present": self.first_present,
        }


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
# Note: use [^\S\n]* (horizontal whitespace only) so we don't greedily eat the
# newline + first list item when env_any uses block-list style.
_ENV_ANY_LINE_RE = re.compile(
    r"^[^\S\n]*env_any[^\S\n]*:[^\S\n]*(.*)$",
    re.MULTILINE,
)


def load_env_any(
    skill_path: str | Path,
    *,
    env: dict[str, str] | None = None,
) -> EnvAnyEntry:
    """Read SKILL.md frontmatter and resolve ``env_any``.

    Args:
        skill_path: Path to a SKILL.md file (or any markdown with frontmatter).
        env: Override env (defaults to ``os.environ``).  Useful for testing.

    Returns:
        :class:`EnvAnyEntry`.  Always returns — never raises — even if the
        file is missing or malformed (in which case ``satisfied=True`` and
        ``required=[]``, treated as "no env requirement").
    """
    p = Path(skill_path)
    e = env if env is not None else os.environ

    if not p.exists():
        return EnvAnyEntry(skill_path=p)

    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("env_any loader cannot read %s: %s", p, exc)
        return EnvAnyEntry(skill_path=p)

    fm_match = _FRONTMATTER_RE.match(text)
    if not fm_match:
        return EnvAnyEntry(skill_path=p)

    fm = fm_match.group(1)
    line_match = _ENV_ANY_LINE_RE.search(fm)
    if not line_match:
        return EnvAnyEntry(skill_path=p)

    raw_value = line_match.group(1).strip()
    required = _parse_list(raw_value, fm, line_match)

    present = [v for v in required if e.get(v)]
    satisfied = (not required) or bool(present)
    return EnvAnyEntry(
        skill_path=p,
        required=required,
        present=present,
        satisfied=satisfied,
        first_present=present[0] if present else None,
    )


def _parse_list(raw: str, fm: str, line_match: re.Match[str]) -> list[str]:
    """Parse either inline ``[A, B]`` or block ``\n  - A\n  - B`` styles."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        items = [s.strip().strip('"\'') for s in raw[1:-1].split(",")]
        return [s for s in items if s]

    # Block list — read subsequent lines starting with `  - VAR`
    after = fm[line_match.end():]
    items: list[str] = []
    for line in after.splitlines():
        m = re.match(r"^\s*-\s*(.+?)\s*$", line)
        if not m:
            if line.strip() and not line.startswith((" ", "\t")):
                break
            continue
        items.append(m.group(1).strip().strip('"\''))
    return [s for s in items if s]

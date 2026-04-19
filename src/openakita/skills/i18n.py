"""
Skill internationalization support

Provides multi-language names and descriptions for skills via the i18n field
in agents/openai.yaml. Backward-compatible with the legacy .openakita-i18n.json
sidecar file.
- Built-in skills: pre-set translations (agents/openai.yaml)
- Marketplace-installed skills: auto-translated via LLM after installation
- User-created skills: guided creation via skill-creator
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ..utils.atomic_io import safe_write

if TYPE_CHECKING:
    from ..core.brain import Brain

logger = logging.getLogger(__name__)

LEGACY_I18N_FILENAME = ".openakita-i18n.json"
OPENAI_YAML_PATH = "agents/openai.yaml"


def read_i18n(skill_dir: Path) -> dict[str, dict[str, str]]:
    """Read i18n data for a skill.

    Reads from the ``i18n`` field in agents/openai.yaml first,
    falling back to the legacy .openakita-i18n.json format.

    Returns:
        {lang: {"name": ..., "description": ...}, ...} or empty dict
    """
    result = _read_i18n_from_yaml(skill_dir)
    if result:
        return result
    return _read_i18n_from_json(skill_dir)


def _read_i18n_from_yaml(skill_dir: Path) -> dict[str, dict[str, str]]:
    """Read from the i18n field in agents/openai.yaml."""
    yaml_file = skill_dir / OPENAI_YAML_PATH
    if not yaml_file.exists():
        return {}
    try:
        content = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            return {}
        i18n = content.get("i18n")
        if not isinstance(i18n, dict):
            return {}
        result: dict[str, dict[str, str]] = {}
        _KNOWN_FIELDS = ("name", "description", "when_to_use", "argument_hint", "keywords")
        for lang, fields in i18n.items():
            if isinstance(fields, dict):
                entry: dict[str, str] = {}
                for fkey in _KNOWN_FIELDS:
                    if fkey in fields:
                        val = fields[fkey]
                        entry[fkey] = (
                            str(val) if not isinstance(val, list) else ",".join(str(v) for v in val)
                        )
                if entry:
                    result[lang] = entry
        return result
    except Exception as e:
        logger.warning(f"Failed to read i18n from agents/openai.yaml for {skill_dir.name}: {e}")
    return {}


def _read_i18n_from_json(skill_dir: Path) -> dict[str, dict[str, str]]:
    """Read from the legacy .openakita-i18n.json (backward compatibility)."""
    i18n_file = skill_dir / LEGACY_I18N_FILENAME
    if not i18n_file.exists():
        return {}
    try:
        data = json.loads(i18n_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning(f"Failed to read legacy i18n for {skill_dir.name}: {e}")
    return {}


def write_i18n(skill_dir: Path, data: dict[str, dict[str, str]]) -> None:
    """Write i18n data to agents/openai.yaml.

    If agents/openai.yaml already exists, merges the i18n field; otherwise creates a new file.
    Aborts the write if YAML parsing fails to prevent data loss.
    """
    yaml_file = skill_dir / OPENAI_YAML_PATH
    existing: dict = {}
    if yaml_file.exists():
        try:
            existing = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.error(
                "Cannot parse existing %s — aborting write_i18n to prevent data loss: %s",
                yaml_file,
                e,
            )
            return

    existing["i18n"] = data

    yaml_file.parent.mkdir(parents=True, exist_ok=True)
    safe_write(
        yaml_file,
        yaml.dump(existing, allow_unicode=True, default_flow_style=False, sort_keys=False),
        backup=True,
    )


def _extract_json(text: str) -> dict | None:
    """Extract JSON from LLM output (handles markdown code block wrapping)."""
    # Try direct parsing
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Try extracting from ```json ... ```
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    return None


async def auto_translate_skill(
    skill_dir: Path,
    name: str,
    description: str,
    brain: Brain,
    *,
    when_to_use: str = "",
    keywords: list[str] | None = None,
    argument_hint: str = "",
) -> bool:
    """Auto-translate skill name and description after installation, writing to the i18n field in agents/openai.yaml.

    Skips if i18n data already exists (from any source).

    Args:
        skill_dir: Skill directory
        name: Skill English name (e.g. "code-reviewer")
        description: Skill English description
        brain: Brain instance for calling LLM
        when_to_use: Use-case description (optional)
        keywords: Keyword list (optional)
        argument_hint: Argument hint (optional)

    Returns:
        True if translation was written successfully, False if skipped or failed
    """
    if read_i18n(skill_dir):
        return False

    payload: dict = {"name": name, "description": description}
    if when_to_use:
        payload["when_to_use"] = when_to_use
    if keywords:
        payload["keywords"] = ",".join(keywords)
    if argument_hint:
        payload["argument_hint"] = argument_hint

    safe_payload = json.dumps(payload, ensure_ascii=False)

    extra_fields_note = ""
    if when_to_use or keywords or argument_hint:
        extra_fields_note = "Also translate any when_to_use/keywords/argument_hint fields if present.\n"

    prompt = (
        "Translate the following AI skill's name and description into Simplified Chinese.\n"
        "The name should be concise (2-6 Chinese characters), and the description should be natural and fluent.\n"
        f"{extra_fields_note}"
        "Return only raw JSON, without markdown wrapping:\n"
        f"{safe_payload}"
    )

    try:
        resp = await brain.think_lightweight(prompt, max_tokens=512)
        parsed = _extract_json(resp.content)
        if not parsed or "name" not in parsed or "description" not in parsed:
            logger.warning(f"LLM translation returned unexpected format for {name}")
            return False

        zh_entry: dict[str, str] = {
            "name": str(parsed["name"]),
            "description": str(parsed["description"]),
        }
        if "when_to_use" in parsed:
            zh_entry["when_to_use"] = str(parsed["when_to_use"])
        if "keywords" in parsed:
            zh_entry["keywords"] = str(parsed["keywords"])
        if "argument_hint" in parsed:
            zh_entry["argument_hint"] = str(parsed["argument_hint"])

        write_i18n(skill_dir, {"zh": zh_entry})
        logger.info(f"Auto-translated skill {name} -> {parsed['name']}")
        return True

    except Exception as e:
        logger.warning(f"Auto-translate failed for skill {name}: {e}")
        return False

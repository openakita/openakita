"""BundleMapper — discover and map plugins from OpenClaw/Claude/Cursor/Codex."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BundleInfo:
    ecosystem: str  # "openclaw" | "claude" | "cursor" | "codex"
    path: Path
    skills: list[Path] = field(default_factory=list)
    mcp_configs: list[Path] = field(default_factory=list)
    commands: list[Path] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)


class BundleMapper:
    """Discover and map external ecosystem plugin bundles into OpenAkita format."""

    def detect(self, path: Path) -> BundleInfo | None:
        """Detect if a directory is a known bundle format."""
        if not path.is_dir():
            return None

        for _, detector in [
            ("openclaw", self._detect_openclaw),
            ("claude", self._detect_claude),
            ("cursor", self._detect_cursor),
            ("codex", self._detect_codex),
        ]:
            info = detector(path)
            if info:
                return info

        return None

    def map_to_manifest(self, bundle: BundleInfo) -> dict:
        """Map an external bundle to an OpenAkita plugin.json manifest dict."""
        plugin_id = f"bundle-{bundle.ecosystem}-{bundle.path.name}"
        manifest: dict[str, Any] = {
            "id": plugin_id,
            "name": f"{bundle.path.name} (from {bundle.ecosystem})",
            "version": bundle.manifest.get("version", "0.0.0"),
            "type": "skill",
            "description": bundle.manifest.get(
                "description", f"Imported from {bundle.ecosystem}"
            ),
            "author": bundle.manifest.get("author", bundle.ecosystem),
            "category": "imported",
            "tags": [bundle.ecosystem, "bundle"],
            "provides": {},
            "permissions": ["tools.register", "hooks.basic"],
            "_bundle_source": {
                "ecosystem": bundle.ecosystem,
                "original_path": str(bundle.path),
            },
        }

        if bundle.skills:
            manifest["type"] = "skill"
            manifest["entry"] = str(bundle.skills[0].name)

        if bundle.mcp_configs:
            manifest["type"] = "mcp"
            manifest["entry"] = str(bundle.mcp_configs[0].name)

        return manifest

    def _detect_openclaw(self, path: Path) -> BundleInfo | None:
        pkg = path / "package.json"
        manifest = path / "openclaw.plugin.json"

        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Cannot parse %s: %s", manifest, e)
                return None
            info = BundleInfo(ecosystem="openclaw", path=path, manifest=data)
            self._scan_skills(path, info)
            self._scan_mcp(path, info)
            return info

        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                if "openclaw" in data or "openclaw.extensions" in data.get("keywords", []):
                    info = BundleInfo(ecosystem="openclaw", path=path, manifest=data)
                    self._scan_skills(path, info)
                    self._scan_mcp(path, info)
                    return info
            except (json.JSONDecodeError, KeyError):
                pass
        return None

    def _detect_claude(self, path: Path) -> BundleInfo | None:
        plugin_dir = path / ".claude-plugin"
        if plugin_dir.is_dir() and (plugin_dir / "plugin.json").exists():
            try:
                data = json.loads(
                    (plugin_dir / "plugin.json").read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Cannot parse %s: %s", plugin_dir / "plugin.json", e)
                return None
            info = BundleInfo(ecosystem="claude", path=path, manifest=data)
            self._scan_skills(path, info)
            self._scan_commands(path, info)
            self._scan_mcp(path, info)
            settings_path = path / "settings.json"
            if settings_path.exists():
                try:
                    info.settings = json.loads(
                        settings_path.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning("Cannot parse %s: %s", settings_path, e)
            return info

        skills_dir = path / "skills"
        if skills_dir.is_dir():
            info = BundleInfo(ecosystem="claude", path=path)
            self._scan_skills(path, info)
            self._scan_commands(path, info)
            self._scan_mcp(path, info)
            if info.skills or info.commands or info.mcp_configs:
                return info
        return None

    def _detect_cursor(self, path: Path) -> BundleInfo | None:
        plugin_dir = path / ".cursor-plugin"
        if plugin_dir.is_dir() and (plugin_dir / "plugin.json").exists():
            try:
                data = json.loads(
                    (plugin_dir / "plugin.json").read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Cannot parse %s: %s", plugin_dir / "plugin.json", e)
                return None
            info = BundleInfo(ecosystem="cursor", path=path, manifest=data)
            self._scan_skills(path, info)
            self._scan_mcp(path, info)

            rules_dir = path / ".cursor" / "rules"
            if rules_dir.is_dir():
                for f in rules_dir.glob("*.mdc"):
                    info.skills.append(f)
            return info

        cursor_dir = path / ".cursor"
        if cursor_dir.is_dir():
            info = BundleInfo(ecosystem="cursor", path=path)
            skills_dir = cursor_dir / "skills"
            if skills_dir.is_dir():
                for f in skills_dir.rglob("SKILL.md"):
                    info.skills.append(f)
            rules_dir = cursor_dir / "rules"
            if rules_dir.is_dir():
                for f in rules_dir.glob("*.mdc"):
                    info.skills.append(f)
            self._scan_mcp(path, info)
            if info.skills or info.mcp_configs:
                return info
        return None

    def _detect_codex(self, path: Path) -> BundleInfo | None:
        plugin_dir = path / ".codex-plugin"
        if plugin_dir.is_dir() and (plugin_dir / "plugin.json").exists():
            try:
                data = json.loads(
                    (plugin_dir / "plugin.json").read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Cannot parse %s: %s", plugin_dir / "plugin.json", e)
                return None
            info = BundleInfo(ecosystem="codex", path=path, manifest=data)
            self._scan_skills(path, info)
            self._scan_mcp(path, info)
            return info

        codex_dir = path / ".codex"
        if codex_dir.is_dir():
            info = BundleInfo(ecosystem="codex", path=path)
            skills_dir = codex_dir / "skills"
            if skills_dir.is_dir():
                for f in skills_dir.rglob("SKILL.md"):
                    info.skills.append(f)
            self._scan_mcp(path, info)
            if info.skills or info.mcp_configs:
                return info
        return None

    def _scan_skills(self, root: Path, info: BundleInfo) -> None:
        for d in ["skills", "."]:
            target = root / d
            if target.is_dir():
                for f in target.rglob("SKILL.md"):
                    if f not in info.skills:
                        info.skills.append(f)

    def _scan_commands(self, root: Path, info: BundleInfo) -> None:
        cmd_dir = root / "commands"
        if cmd_dir.is_dir():
            for sub in cmd_dir.iterdir():
                if sub.is_dir():
                    for f in sub.glob("SKILL.md"):
                        info.commands.append(f)
                    for f in sub.glob("README.md"):
                        info.commands.append(f)

    def _scan_mcp(self, root: Path, info: BundleInfo) -> None:
        for name in [".mcp.json", "mcp_config.json", "mcp.json"]:
            mcp = root / name
            if mcp.exists():
                info.mcp_configs.append(mcp)

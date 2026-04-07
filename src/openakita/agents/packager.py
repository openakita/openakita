"""
Agent 打包与安装

AgentPackager: 将本地 AgentProfile + 技能打包成 .akita-agent 文件
AgentInstaller: 从 .akita-agent 文件安装 Agent 到本地
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from .manifest import (
    MAX_PACKAGE_SIZE,
    MAX_SINGLE_FILE_SIZE,
    SPEC_VERSION,
    AgentManifest,
    ExternalSkillRef,
    ManifestAuthor,
    validate_file_safety,
)
from .profile import AgentProfile, ProfileStore

logger = logging.getLogger(__name__)


class PackageError(Exception):
    """Agent 包操作错误"""


class AgentPackager:
    """将本地 Agent 打包成 .akita-agent ZIP 文件"""

    def __init__(
        self,
        profile_store: ProfileStore,
        skills_dir: Path,
        output_dir: Path | None = None,
    ):
        self.profile_store = profile_store
        self.skills_dir = skills_dir
        self.output_dir = output_dir or Path(".")

    # Skill directories considered "own" (safe to bundle)
    _BUNDLEABLE_DIRS = {"system", "builtin", "custom"}

    def package(
        self,
        profile_id: str,
        *,
        author_name: str = "",
        author_url: str = "",
        version: str = "1.0.0",
        readme: str = "",
        include_skills: list[str] | None = None,
    ) -> Path:
        """
        打包指定 Agent 为 .akita-agent 文件。

        Spec v1.1: third-party skills are NOT bundled; they are declared
        as required_external_skills and fetched from their original source
        during installation.

        Args:
            profile_id: 要打包的 Agent Profile ID
            author_name: 作者名（如未提供则使用 created_by）
            author_url: 作者主页
            version: 包版本号
            readme: README 内容
            include_skills: 要打包的技能列表（None 则使用 profile 中的技能）

        Returns:
            输出文件路径
        """
        profile = self.profile_store.get(profile_id)
        if profile is None:
            raise PackageError(f"Profile not found: {profile_id}")

        slug_id = self._to_slug(profile_id)

        candidate_skills = include_skills
        if candidate_skills is None and profile.skills:
            candidate_skills = list(profile.skills)

        bundled_skill_names: list[str] = []
        external_skill_refs: list[ExternalSkillRef] = []
        builtin_skills: list[str] = []

        for skill_name in candidate_skills or []:
            skill_path = self._find_skill(skill_name)
            if skill_path is None:
                builtin_skills.append(skill_name)
                continue

            if not (skill_path / "SKILL.md").exists():
                builtin_skills.append(skill_name)
                continue

            if self._is_bundleable(skill_path):
                bundled_skill_names.append(skill_name)
            else:
                meta = self._read_skill_meta(skill_path)
                external_skill_refs.append(
                    ExternalSkillRef(
                        id=skill_name,
                        source=meta.get("source", skill_name),
                        version=meta.get("version", ""),
                        license=meta.get("license", "unknown"),
                        url=meta.get("url", ""),
                        required=True,
                    )
                )

        manifest = AgentManifest(
            spec_version=SPEC_VERSION,
            id=slug_id,
            name=profile.name,
            name_i18n=profile.name_i18n,
            description=profile.description,
            description_i18n=profile.description_i18n,
            version=version,
            author=ManifestAuthor(
                name=author_name or profile.created_by,
                url=author_url,
            ),
            category=profile.category,
            tags=[],
            license="MIT",
            bundled_skills=bundled_skill_names,
            required_builtin_skills=builtin_skills,
            required_external_skills=external_skill_refs,
            created_at=datetime.now(UTC).isoformat(),
        )

        errors = manifest.validate()
        if errors:
            raise PackageError(f"Manifest validation failed: {'; '.join(errors)}")

        profile_data = profile.to_dict()
        profile_data["type"] = "custom"
        for key in ("ephemeral", "inherit_from", "user_customized", "hidden"):
            profile_data.pop(key, None)

        license_3rd_party = self._generate_license_3rd_party(external_skill_refs)

        def _write_zip(zf: zipfile.ZipFile) -> None:
            zf.writestr(
                "manifest.json", json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2)
            )
            zf.writestr("profile.json", json.dumps(profile_data, ensure_ascii=False, indent=2))
            if readme:
                zf.writestr("README.md", readme)
            if license_3rd_party:
                zf.writestr("LICENSE-3RD-PARTY.md", license_3rd_party)
            for skill_name in bundled_skill_names:
                skill_path = self._find_skill(skill_name)
                if skill_path:
                    for file in skill_path.rglob("*"):
                        if file.is_file():
                            arcname = f"skills/{skill_name}/{file.relative_to(skill_path)}"
                            zf.write(file, arcname)

        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            _write_zip(zf)

        package_bytes = buf.getvalue()
        if len(package_bytes) > MAX_PACKAGE_SIZE:
            raise PackageError(
                f"Package too large: {len(package_bytes)} bytes (max {MAX_PACKAGE_SIZE})"
            )

        checksum = f"sha256:{hashlib.sha256(package_bytes).hexdigest()}"
        manifest.checksum = checksum
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            _write_zip(zf)

        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"{slug_id}.akita-agent"
        output_path.write_bytes(buf.getvalue())

        logger.info(
            f"Agent packaged: {output_path} ({len(buf.getvalue())} bytes, "
            f"bundled={len(bundled_skill_names)}, external={len(external_skill_refs)})"
        )
        return output_path

    def _to_slug(self, profile_id: str) -> str:
        slug = re.sub(r"[^a-z0-9-]", "-", profile_id.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug[:64] if len(slug) > 64 else slug

    def _find_skill(self, skill_name: str) -> Path | None:
        for subdir in ["custom", "community", "system", "builtin"]:
            path = self.skills_dir / subdir / skill_name
            if path.exists():
                return path
        path = self.skills_dir / skill_name
        if path.exists():
            return path
        return None

    def _is_bundleable(self, skill_path: Path) -> bool:
        """Check if a skill resides in a directory that is safe to bundle."""
        try:
            rel = skill_path.relative_to(self.skills_dir)
            top_dir = rel.parts[0] if rel.parts else ""
            return top_dir in self._BUNDLEABLE_DIRS
        except ValueError:
            return False

    def _read_skill_meta(self, skill_path: Path) -> dict[str, str]:
        """Read YAML frontmatter from SKILL.md to extract license/source metadata."""
        skill_md = skill_path / "SKILL.md"
        if not skill_md.exists():
            return {}
        try:
            content = skill_md.read_text(encoding="utf-8")
            if not content.startswith("---"):
                return {}
            end = content.index("---", 3)
            frontmatter = content[3:end].strip()
            meta: dict[str, str] = {}
            for line in frontmatter.split("\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip().strip('"').strip("'")
            return meta
        except Exception:
            return {}

    def _generate_license_3rd_party(self, refs: list[ExternalSkillRef]) -> str:
        """Generate LICENSE-3RD-PARTY.md content."""
        if not refs:
            return ""
        lines = [
            "# Third-Party Dependencies",
            "",
            "This agent references the following external skills.",
            "They are NOT bundled in this package and will be fetched",
            "from their original sources during installation.",
            "",
            "| Skill | License | Source | Required |",
            "|-------|---------|--------|----------|",
        ]
        for r in refs:
            src = r.url or r.source
            lines.append(f"| {r.id} | {r.license} | {src} | {'Yes' if r.required else 'No'} |")
        lines.append("")
        lines.append("By installing this agent, you agree to comply with each")
        lines.append("skill's respective license terms.")
        lines.append("")
        return "\n".join(lines)


class AgentInstaller:
    """从 .akita-agent ZIP 文件安装 Agent 到本地"""

    def __init__(
        self,
        profile_store: ProfileStore,
        skills_dir: Path,
    ):
        self.profile_store = profile_store
        self.skills_dir = skills_dir

    def inspect(self, package_path: Path) -> dict[str, Any]:
        """预览 .akita-agent 包内容（不执行安装）"""
        self._validate_file(package_path)

        with zipfile.ZipFile(package_path, "r") as zf:
            manifest_data = json.loads(zf.read("manifest.json"))
            profile_data = json.loads(zf.read("profile.json"))

            manifest = AgentManifest.from_dict(manifest_data)
            errors = manifest.validate()

            bundled_skills = []
            for name in zf.namelist():
                if name.startswith("skills/") and name.endswith("/SKILL.md"):
                    parts = name.split("/")
                    if len(parts) >= 3:
                        bundled_skills.append(parts[1])

            has_readme = "README.md" in zf.namelist()
            has_icon = any(n in zf.namelist() for n in ["icon.png", "icon.svg"])

            conflict = self.profile_store.exists(manifest.id)

        return {
            "manifest": manifest.to_dict(),
            "profile": profile_data,
            "bundled_skills": bundled_skills,
            "has_readme": has_readme,
            "has_icon": has_icon,
            "validation_errors": errors,
            "id_conflict": conflict,
            "package_size": package_path.stat().st_size,
        }

    def install(
        self,
        package_path: Path,
        *,
        force: bool = False,
        hub_source: dict[str, Any] | None = None,
    ) -> AgentProfile:
        """
        安装 .akita-agent 包。

        Spec v1.1: after extracting bundled skills, the installer attempts
        to fetch required_external_skills from their original sources.
        Failures are non-blocking — the agent is still installed.

        Args:
            package_path: 包文件路径
            force: 如果 ID 冲突，是否强制覆盖
            hub_source: Hub 来源元数据（如果从 Hub 下载安装）

        Returns:
            创建的 AgentProfile
        """
        self._validate_file(package_path)

        with zipfile.ZipFile(package_path, "r") as zf:
            self._security_check(zf)

            manifest_data = json.loads(zf.read("manifest.json"))
            manifest = AgentManifest.from_dict(manifest_data)

            errors = manifest.validate()
            if errors:
                raise PackageError(f"Invalid manifest: {'; '.join(errors)}")

            profile_data = json.loads(zf.read("profile.json"))

            profile_id = manifest.id
            if self.profile_store.exists(profile_id) and not force:
                profile_id = self._resolve_conflict(profile_id)

            installed_skills = self._install_skills(
                zf, manifest.bundled_skills, agent_id=manifest.id
            )

            ext_results = self._fetch_external_skills(
                manifest.required_external_skills, agent_id=manifest.id
            )
            installed_skills.extend(ext_results)

            profile_data["id"] = profile_id
            profile_data["type"] = "custom"
            profile_data.pop("ephemeral", None)
            profile_data.pop("inherit_from", None)

            if installed_skills:
                existing_skills = profile_data.get("skills", [])
                for s in installed_skills:
                    if s not in existing_skills:
                        existing_skills.append(s)
                profile_data["skills"] = existing_skills

            if hub_source:
                profile_data["hub_source"] = hub_source

            profile = AgentProfile.from_dict(profile_data)
            self.profile_store.save(profile)

        logger.info(
            f"Agent installed: {profile_id} "
            f"(bundled: {len(manifest.bundled_skills)}, "
            f"external: {len(manifest.required_external_skills)}, "
            f"total installed: {installed_skills})"
        )
        return profile

    def _validate_file(self, package_path: Path) -> None:
        if not package_path.exists():
            raise PackageError(f"File not found: {package_path}")
        if package_path.stat().st_size > MAX_PACKAGE_SIZE:
            raise PackageError(f"Package too large: {package_path.stat().st_size} bytes")
        if not zipfile.is_zipfile(package_path):
            raise PackageError(f"Not a valid ZIP file: {package_path}")

        with zipfile.ZipFile(package_path, "r") as zf:
            if "manifest.json" not in zf.namelist():
                raise PackageError("Missing manifest.json in package")
            if "profile.json" not in zf.namelist():
                raise PackageError("Missing profile.json in package")

    def _security_check(self, zf: zipfile.ZipFile) -> None:
        for info in zf.infolist():
            errors = validate_file_safety(info.filename)
            if errors:
                raise PackageError(f"Security violation: {'; '.join(errors)}")
            if info.file_size > MAX_SINGLE_FILE_SIZE:
                raise PackageError(f"File too large: {info.filename} ({info.file_size} bytes)")
            if info.is_dir():
                continue
            if info.external_attr >> 16 & 0o120000 == 0o120000:
                raise PackageError(f"Symlinks not allowed: {info.filename}")

    # ── Skill version helpers ──

    _ORIGIN_FILE = ".openakita-origin.json"

    def _read_installed_version(self, skill_dir: Path) -> str | None:
        """Read the version of an already-installed skill.

        Checks (in order): .openakita-origin.json → SKILL.md frontmatter version.
        """
        origin = skill_dir / self._ORIGIN_FILE
        if origin.exists():
            try:
                data = json.loads(origin.read_text("utf-8"))
                return data.get("version") or None
            except Exception:
                pass
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            try:
                import re as _re

                import yaml

                m = _re.match(r"^---\s*\n(.*?)\n---", skill_md.read_text("utf-8"), _re.DOTALL)
                if m:
                    fm = yaml.safe_load(m.group(1)) or {}
                    return fm.get("version") or None
            except Exception:
                pass
        return None

    @staticmethod
    def _is_newer(incoming: str | None, existing: str | None) -> bool:
        """Compare semver-ish strings. Returns True if incoming > existing."""
        if not incoming:
            return False
        if not existing:
            return True
        try:

            def _parts(v: str) -> tuple[int, ...]:
                return tuple(int(x) for x in v.split(".")[:3])

            return _parts(incoming) > _parts(existing)
        except (ValueError, TypeError):
            return incoming > existing

    def _write_origin(
        self,
        skill_dir: Path,
        *,
        source: str,
        version: str | None,
        origin_type: str,
        agent_id: str = "",
    ) -> None:
        """Write .openakita-origin.json sidecar to track provenance."""
        data = {
            "source": source,
            "version": version or "",
            "type": origin_type,
            "installed_at": datetime.now(UTC).isoformat(),
        }
        if agent_id:
            data["installed_by_agent"] = agent_id
        (skill_dir / self._ORIGIN_FILE).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _find_installed_skill_dir(self, skill_id: str) -> Path | None:
        """Locate an already-installed skill across all sub-directories."""
        for subdir in ("custom", "community", "system", "builtin"):
            d = self.skills_dir / subdir / skill_id
            if (d / "SKILL.md").exists():
                return d
        d = self.skills_dir / skill_id
        if (d / "SKILL.md").exists():
            return d
        return None

    def _extract_bundled_version(self, zf: zipfile.ZipFile, skill_name: str) -> str | None:
        """Extract version from bundled SKILL.md inside the zip (without full extraction)."""
        skill_md_path = f"skills/{skill_name}/SKILL.md"
        if skill_md_path not in zf.namelist():
            return None
        try:
            import re as _re

            import yaml

            content = zf.read(skill_md_path).decode("utf-8", errors="replace")
            m = _re.match(r"^---\s*\n(.*?)\n---", content, _re.DOTALL)
            if m:
                fm = yaml.safe_load(m.group(1)) or {}
                return fm.get("version") or None
        except Exception:
            pass
        return None

    # ── Skill installation ──

    def _install_skills(
        self,
        zf: zipfile.ZipFile,
        bundled_skills: list[str],
        agent_id: str = "",
    ) -> list[str]:
        """Extract and install bundled skills with version-aware dedup."""
        installed = []
        custom_skills_dir = self.skills_dir / "custom"
        custom_skills_dir.mkdir(parents=True, exist_ok=True)

        for skill_name in bundled_skills:
            skill_prefix = f"skills/{skill_name}/"
            skill_files = [
                n for n in zf.namelist() if n.startswith(skill_prefix) and not n.endswith("/")
            ]

            if not skill_files:
                logger.warning(f"Bundled skill not found in package: {skill_name}")
                continue

            skill_md_found = any(n.endswith("SKILL.md") for n in skill_files)
            if not skill_md_found:
                logger.warning(f"Bundled skill missing SKILL.md: {skill_name}")
                continue

            incoming_ver = self._extract_bundled_version(zf, skill_name)
            existing_dir = self._find_installed_skill_dir(skill_name)
            if existing_dir is not None:
                existing_ver = self._read_installed_version(existing_dir)
                if incoming_ver and existing_ver and not self._is_newer(incoming_ver, existing_ver):
                    logger.info(
                        f"Skill '{skill_name}' already installed "
                        f"(v{existing_ver} >= incoming v{incoming_ver}), skipping"
                    )
                    installed.append(skill_name)
                    continue

            target_dir = custom_skills_dir / skill_name
            target_dir.mkdir(parents=True, exist_ok=True)

            for filename in skill_files:
                rel_path = filename[len(skill_prefix) :]
                target_file = target_dir / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_bytes(zf.read(filename))

            self._write_origin(
                target_dir,
                source="bundled",
                version=incoming_ver,
                origin_type="bundled",
                agent_id=agent_id,
            )
            installed.append(skill_name)
            logger.info(
                f"Installed bundled skill: {skill_name} v{incoming_ver or '?'} -> {target_dir}"
            )

        return installed

    def _fetch_external_skills(
        self,
        ext_refs: list[Any],
        agent_id: str = "",
    ) -> list[str]:
        """Fetch required_external_skills with version-aware dedup.

        Failures are non-blocking — the agent is still installed.
        """
        if not ext_refs:
            return []

        installed: list[str] = []
        for ref in ext_refs:
            skill_id = ref.id if hasattr(ref, "id") else ref.get("id", "")
            source = ref.source if hasattr(ref, "source") else ref.get("source", "")
            required = ref.required if hasattr(ref, "required") else ref.get("required", True)
            incoming_ver = ref.version if hasattr(ref, "version") else ref.get("version", "")

            existing_dir = self._find_installed_skill_dir(skill_id)
            if existing_dir is not None:
                existing_ver = self._read_installed_version(existing_dir)
                if incoming_ver and existing_ver and not self._is_newer(incoming_ver, existing_ver):
                    logger.info(
                        f"External skill '{skill_id}' already installed "
                        f"(v{existing_ver} >= incoming v{incoming_ver}), skipping"
                    )
                    installed.append(skill_id)
                    continue

            try:
                target_dir = self._install_from_source(skill_id, source)
                self._write_origin(
                    target_dir,
                    source=source,
                    version=incoming_ver,
                    origin_type="external",
                    agent_id=agent_id,
                )
                installed.append(skill_id)
                logger.info(
                    f"Fetched external skill: {skill_id} v{incoming_ver or 'latest'} from {source}"
                )
            except Exception as e:
                level = "Required" if required else "Optional"
                logger.warning(
                    f"{level} external skill failed to install: {skill_id} (source: {source}): {e}"
                )
                if required:
                    logger.warning(f"Please install '{skill_id}' manually from: {source}")

        return installed

    def _skill_exists_locally(self, skill_id: str) -> bool:
        return self._find_installed_skill_dir(skill_id) is not None

    def _install_from_source(self, skill_id: str, source: str) -> Path:
        """Fetch a skill from its source and return the install dir.

        Best-effort GitHub clone implementation.
        """
        import subprocess
        import tempfile

        if "@" in source:
            repo_part, skill_name = source.rsplit("@", 1)
        else:
            repo_part = source
            skill_name = skill_id

        if "/" in repo_part and not repo_part.startswith("http"):
            repo_url = f"https://github.com/{repo_part}"
        else:
            repo_url = repo_part

        target_dir = self.skills_dir / "community" / skill_id
        target_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                ["git", "clone", "--depth=1", repo_url, tmpdir],
                check=True,
                capture_output=True,
                timeout=60,
            )

            src_skill = Path(tmpdir) / skill_name
            if not src_skill.exists():
                src_skill = Path(tmpdir) / "skills" / skill_name
            if not src_skill.exists():
                raise FileNotFoundError(f"Skill directory '{skill_name}' not found in {repo_url}")

            skill_md = src_skill / "SKILL.md"
            if not skill_md.exists():
                raise FileNotFoundError(f"SKILL.md not found in {src_skill}")

            for file in src_skill.rglob("*"):
                if file.is_file():
                    dest = target_dir / file.relative_to(src_skill)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(file.read_bytes())

        return target_dir

    def _resolve_conflict(self, profile_id: str) -> str:
        """ID 冲突时追加后缀"""
        for i in range(1, 100):
            new_id = f"{profile_id}-{i}"
            if not self.profile_store.exists(new_id):
                logger.info(f"ID conflict resolved: {profile_id} -> {new_id}")
                return new_id
        raise PackageError(f"Cannot resolve ID conflict for: {profile_id}")

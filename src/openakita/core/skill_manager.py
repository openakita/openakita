"""
Skill manager

Skill install/load/update logic extracted from agent.py, responsible for:
- Loading installed skills
- Installing skills from Git repositories
- Installing skills from URLs
- Updating skill tool descriptions
- Managing the external skill allowlist
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..config import settings
from ..skills.source_url import (
    RAW_GITHUB_RE,
    has_yaml_frontmatter,
    is_html_content,
    parse_github_source,
    parse_playbooks_source,
)
from ..tools.errors import ErrorType, ToolError

logger = logging.getLogger(__name__)

SKILL_GIT_CLONE_TIMEOUT_SECONDS = 120
SKILL_INSTALL_CIRCUIT_THRESHOLD = 2
SKILL_INSTALL_CIRCUIT_COOLDOWN_SECONDS = 300


class SkillManager:
    """
    Skill manager.

    Manages loading, installation, and updating of Agent Skills (SKILL.md spec).
    """

    def __init__(
        self,
        skill_registry: Any,
        skill_loader: Any,
        skill_catalog: Any,
        shell_tool: Any,
    ) -> None:
        """
        Args:
            skill_registry: SkillRegistry instance
            skill_loader: SkillLoader instance
            skill_catalog: SkillCatalog instance
            shell_tool: ShellTool instance (used for git operations)

        Note:
            ``install_skill`` / ``load_installed_skills`` are only responsible for
            persisting the skill to disk and performing first-time registration with the
            loader/registry; they do **not** handle subsequent catalog refresh, Pool
            notifications, or event broadcast — those are handled uniformly by
            ``Agent.propagate_skill_change``. After calling this manager, tool-layer /
            API-layer code must also invoke ``propagate_skill_change`` once.
        """
        self._registry = skill_registry
        self._loader = skill_loader
        self._catalog = skill_catalog
        self._shell_tool = shell_tool

        # Cache
        self._catalog_text: str = ""
        self._failure_class_streaks: dict[str, int] = {}
        self._failure_class_last_seen: dict[str, float] = {}
        self._install_lock: asyncio.Lock | None = None

    @property
    def catalog_text(self) -> str:
        """Get the skill catalog text"""
        return self._catalog_text

    async def load_installed_skills(self) -> None:
        """
        Load installed skills.

        Skills are loaded from the following directories:
        - skills/ (project level)
        - .cursor/skills/ (Cursor compatibility)
        """
        loaded = self._loader.load_all(settings.project_root)
        logger.info(f"Loaded {loaded} skills from standard directories")

        # External skill allowlist filtering (supports DEFAULT_DISABLED_SKILLS default-disable)
        try:
            cfg_path = settings.project_root / "data" / "skills.json"
            external_allowlist: set[str] | None = None
            if cfg_path.exists():
                raw = cfg_path.read_text(encoding="utf-8")
                cfg = json.loads(raw) if raw.strip() else {}
                al = cfg.get("external_allowlist", None)
                if isinstance(al, list):
                    external_allowlist = {str(x).strip() for x in al if str(x).strip()}
            effective = self._loader.compute_effective_allowlist(external_allowlist)
            from openakita.skills.preset_utils import collect_preset_referenced_skills

            agent_skills = collect_preset_referenced_skills()
            removed = self._loader.prune_external_by_allowlist(
                effective,
                agent_referenced_skills=agent_skills,
            )
            if removed:
                logger.info(f"External skills filtered: {removed} disabled")
        except Exception as e:
            logger.warning(f"Failed to apply skills allowlist: {e}")

        self._catalog_text = self._catalog.generate_catalog()
        logger.info(f"Generated skill catalog with {self._catalog.skill_count} skills")

    async def install_skill(
        self,
        source: str,
        name: str | None = None,
        subdir: str | None = None,
        extra_files: list[str] | None = None,
    ) -> str:
        """
        Install a skill into the current workspace's skills directory.

        URL resolution priority:
        1. GitHub blob/tree/repo URL → git clone + subdir extraction
        2. playbooks.com marketplace page → converted to a GitHub source
        3. raw.githubusercontent.com → direct file download
        4. Other Git hosting platform URLs → git clone
        5. Other HTTP URLs → downloaded as a file URL

        Args:
            source: Git repository URL, SKILL.md file URL, or skill marketplace URL
            name: skill name
            subdir: subdirectory within the Git repo where the skill resides (overridden by the path parsed from the URL)
            extra_files: list of additional file URLs

        Returns:
            installation result message
        """
        if self._install_lock is None:
            self._install_lock = asyncio.Lock()
        async with self._install_lock:
            return await self._install_skill_impl(source, name, subdir, extra_files)

    async def _install_skill_impl(
        self,
        source: str,
        name: str | None = None,
        subdir: str | None = None,
        extra_files: list[str] | None = None,
    ) -> str:
        skills_dir = settings.skills_path
        skills_dir.mkdir(parents=True, exist_ok=True)

        # 1. GitHub URL (precise parsing including blob/tree paths)
        gh = parse_github_source(source)
        if gh:
            clone_url = f"https://github.com/{gh.owner}/{gh.repo}.git"
            effective_subdir = subdir or gh.subdir
            return await self._install_from_git(clone_url, name, effective_subdir, skills_dir)

        # 2. playbooks.com skill marketplace page → convert to a GitHub source
        pb = parse_playbooks_source(source)
        if pb:
            clone_url = f"https://github.com/{pb.owner}/{pb.repo}.git"
            effective_subdir = subdir or pb.subdir
            return await self._install_from_git(
                clone_url,
                name or pb.subdir,
                effective_subdir,
                skills_dir,
            )

        # 3. raw.githubusercontent.com → download directly as a file URL
        if RAW_GITHUB_RE.match(source):
            return await self._install_from_url(source, name, extra_files, skills_dir)

        # 4. Other Git hosting platforms
        if self._is_git_platform_url(source):
            return await self._install_from_git(source, name, subdir, skills_dir)

        # 5. Fallback: generic HTTP URL
        return await self._install_from_url(source, name, extra_files, skills_dir)

    def update_shell_tool_description(self, tools: list[dict]) -> None:
        """Dynamically update the shell tool description, including current OS info"""
        import platform

        if os.name == "nt":
            os_info = (
                f"Windows {platform.release()} "
                "(use PowerShell/cmd commands, e.g.: dir, type, tasklist, Get-Process, findstr)"
            )
        else:
            os_info = f"{platform.system()} (use bash commands, e.g.: ls, cat, ps aux, grep)"

        for tool in tools:
            if tool.get("name") == "run_shell":
                tool["description"] = (
                    f"Execute a shell command. Current OS: {os_info}. "
                    "Note: use commands supported by the current OS; if a command fails repeatedly, "
                    "try a different command or abandon the approach."
                )
                tool["input_schema"]["properties"]["command"]["description"] = (
                    f"The shell command to execute (current system: {os.name})"
                )
                break

    # ==================== Private methods ====================

    @staticmethod
    def _is_git_platform_url(url: str) -> bool:
        """Determine whether the URL is a non-GitHub Git hosting platform URL (GitHub is handled by _parse_github_source)."""
        patterns = [
            r"^git@",
            r"\.git$",
            r"^https?://gitlab\.com/",
            r"^https?://bitbucket\.org/",
            r"^https?://gitee\.com/",
        ]
        return any(re.search(p, url) for p in patterns)

    @staticmethod
    def _is_shell_timeout_result(result: Any) -> bool:
        """Best-effort detection for shell timeout failures."""
        if getattr(result, "returncode", None) != -1:
            return False
        output = f"{getattr(result, 'stdout', '')}\n{getattr(result, 'stderr', '')}".lower()
        return "timed out" in output or "timeout" in output

    @staticmethod
    def _build_install_skill_error(
        *,
        error_type: ErrorType,
        message: str,
        source: str,
        failure_class: str,
        retry_suggestion: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        """Return a structured ToolError payload for install_skill."""
        payload = {
            "source": source,
            "failure_class": failure_class,
        }
        if details:
            payload.update(details)
        return ToolError(
            error_type=error_type,
            tool_name="install_skill",
            message=message,
            retry_suggestion=retry_suggestion,
            details=payload,
        ).to_tool_result()

    @staticmethod
    def _classify_git_clone_failure(output: str) -> tuple[ErrorType, str]:
        lower = output.lower()
        if any(
            k in lower
            for k in ("timed out", "timeout", "could not resolve", "connection", "network")
        ):
            return ErrorType.TRANSIENT, "git_network_failure"
        if any(k in lower for k in ("repository not found", "not found", "404")):
            return ErrorType.RESOURCE_NOT_FOUND, "git_repo_not_found"
        if any(k in lower for k in ("permission denied", "authentication failed", "access denied")):
            return ErrorType.PERMISSION, "git_permission_denied"
        if any(
            k in lower for k in ("not recognized", "command not found", "no such file or directory")
        ):
            return ErrorType.DEPENDENCY, "git_dependency_missing"
        return ErrorType.PERMANENT, "git_clone_failed"

    def _is_failure_class_circuit_open(self, failure_class: str) -> bool:
        count = self._failure_class_streaks.get(failure_class, 0)
        if count < SKILL_INSTALL_CIRCUIT_THRESHOLD:
            return False
        last_seen = self._failure_class_last_seen.get(failure_class, 0.0)
        if time.time() - last_seen > SKILL_INSTALL_CIRCUIT_COOLDOWN_SECONDS:
            self._failure_class_streaks.pop(failure_class, None)
            self._failure_class_last_seen.pop(failure_class, None)
            return False
        return True

    def _record_failure_class(self, failure_class: str) -> None:
        self._failure_class_streaks[failure_class] = (
            self._failure_class_streaks.get(failure_class, 0) + 1
        )
        self._failure_class_last_seen[failure_class] = time.time()

    def _reset_failure_streaks(self) -> None:
        self._failure_class_streaks.clear()
        self._failure_class_last_seen.clear()

    @staticmethod
    def _git_host(url: str) -> str:
        try:
            return urlparse(url).netloc or "unknown"
        except Exception:
            return "unknown"

    async def _install_from_git(
        self, git_url: str, name: str | None, subdir: str | None, skills_dir: Path
    ) -> str:
        """Install a skill from a Git repository"""
        import shutil
        import tempfile

        temp_dir = None
        try:
            for failure_class in ("skill_install_network_timeout", "git_network_failure"):
                if self._is_failure_class_circuit_open(failure_class):
                    return self._build_install_skill_error(
                        error_type=ErrorType.PERMANENT,
                        message="install_skill circuit breaker is open for repeated network failures",
                        source=git_url,
                        failure_class="skill_install_circuit_open",
                        retry_suggestion=(
                            "Pause retries and ask the user to fix network/proxy first, "
                            "or install from local directory/ZIP."
                        ),
                        details={
                            "blocked_by": failure_class,
                            "host": self._git_host(git_url),
                            "failure_count": self._failure_class_streaks.get(failure_class, 0),
                            "cooldown_seconds": SKILL_INSTALL_CIRCUIT_COOLDOWN_SECONDS,
                        },
                    )

            temp_dir = Path(tempfile.mkdtemp(prefix="skill_install_"))
            result = await self._shell_tool.run(
                f'git clone --depth 1 "{git_url}" "{temp_dir}"',
                timeout=SKILL_GIT_CLONE_TIMEOUT_SECONDS,
            )

            if not result.success:
                if self._is_shell_timeout_result(result):
                    self._record_failure_class("skill_install_network_timeout")
                    return self._build_install_skill_error(
                        error_type=ErrorType.TIMEOUT,
                        message="Git clone timed out while installing skill",
                        source=git_url,
                        failure_class="skill_install_network_timeout",
                        retry_suggestion=(
                            "Network access to the git host is unstable. "
                            "Do not keep retrying mirror variants automatically."
                        ),
                        details={
                            "timeout_seconds": SKILL_GIT_CLONE_TIMEOUT_SECONDS,
                            "raw_output": result.output[:2000],
                        },
                    )
                error_type, failure_class = self._classify_git_clone_failure(result.output)
                self._record_failure_class(failure_class)
                return self._build_install_skill_error(
                    error_type=error_type,
                    message="Git clone failed while installing skill",
                    source=git_url,
                    failure_class=failure_class,
                    retry_suggestion=(
                        "Check repository URL and network/proxy settings, "
                        "or install from local directory/ZIP."
                    ),
                    details={"raw_output": result.output[:2000]},
                )

            search_dir = temp_dir / subdir if subdir else temp_dir
            skill_md_path = self._find_skill_md(search_dir)

            if not skill_md_path:
                possible = self._list_skill_candidates(temp_dir)
                hint = ""
                if possible:
                    hint = "\n\nPossible skill directories:\n" + "\n".join(f"- {p}" for p in possible[:5])
                return f"SKILL.md not found{hint}"

            skill_source_dir = skill_md_path.parent
            skill_content = skill_md_path.read_text(encoding="utf-8")
            extracted_name = self._extract_skill_name(skill_content)
            skill_name = name or extracted_name or skill_source_dir.name
            skill_name = self._normalize_skill_name(skill_name)

            target_dir = skills_dir / skill_name
            if target_dir.exists():
                shutil.rmtree(target_dir)
            try:
                shutil.copytree(skill_source_dir, target_dir)
            except Exception as copy_err:
                self._cleanup_broken_skill_dir(target_dir)
                raise RuntimeError(f"copytree failed: {copy_err}") from copy_err
            self._ensure_skill_structure(target_dir)

            try:
                # force=True: allow overwriting registered skills with the same name (re-install/upgrade scenarios)
                loaded = self._loader.load_skill(target_dir, force=True)
                if loaded:
                    # Note: directory cache / Pool notification / event broadcast are handled uniformly by
                    # the upper layer propagate_skill_change; here we only record a catalog_text snapshot for debugging.
                    self._catalog_text = self._catalog.generate_catalog()
                    self._reset_failure_streaks()
                    logger.info(f"Skill installed from git: {skill_name}")
                else:
                    raise RuntimeError("loader did not return a valid skill")
            except Exception as e:
                logger.error(f"Failed to load installed skill: {e}")
                self._cleanup_broken_skill_dir(target_dir)
                return f"❌ Skill files copied but failed to load: {e}"

            return (
                f"✅ Skill installed from Git successfully!\n\n"
                f"**Skill name**: {skill_name}\n"
                f"**Source**: {git_url}\n"
                f"**Installation path**: {target_dir}\n\n"
                f"**Directory structure**:\n```\n{skill_name}/\n{self._format_tree(target_dir)}\n```\n\n"
                f'Skill has been automatically loaded. Use `get_skill_info("{skill_name}")` to view detailed instructions.'
            )

        except Exception as e:
            logger.error(f"Failed to install skill from git: {e}")
            return self._build_install_skill_error(
                error_type=ErrorType.PERMANENT,
                message=f"Unexpected failure while installing skill from git: {e}",
                source=git_url,
                failure_class="skill_install_unexpected",
            )
        finally:
            if temp_dir and temp_dir.exists():
                with contextlib.suppress(BaseException):
                    import shutil

                    shutil.rmtree(temp_dir)

    async def _install_from_url(
        self, url: str, name: str | None, extra_files: list[str] | None, skills_dir: Path
    ) -> str:
        """Install skill from URL (accept raw SKILL.md files only)"""
        import httpx

        skill_dir: Path | None = None
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                skill_content = response.text

            # ---- Content validation: reject HTML, require YAML frontmatter ----
            if is_html_content(skill_content):
                return (
                    f"❌ URL returned HTML page instead of SKILL.md: {url}\n\n"
                    "Please use one of the following formats:\n"
                    "- GitHub repo: `https://github.com/owner/repo`\n"
                    "- Raw file: `https://raw.githubusercontent.com/owner/repo/main/path/SKILL.md`\n"
                    "- Shorthand: `owner/repo@skill-name`"
                )
            if not has_yaml_frontmatter(skill_content):
                return (
                    f"❌ Downloaded content is not a valid SKILL.md (missing YAML frontmatter): {url}\n\n"
                    "A valid SKILL.md must start with a YAML metadata block beginning with `---`."
                )

            extracted_name = self._extract_skill_name(skill_content)
            skill_name = name or extracted_name

            if not skill_name:
                from urllib.parse import urlparse

                path = urlparse(url).path
                skill_name = path.split("/")[-1].replace(".md", "").replace("skill", "").strip("-_")

            skill_name = self._normalize_skill_name(skill_name or "custom-skill")
            skill_dir = skills_dir / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")
            self._ensure_skill_structure(skill_dir)

            installed_files = ["SKILL.md"]

            if extra_files:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    for file_url in extra_files:
                        try:
                            from urllib.parse import urlparse as _urlparse

                            file_name = _urlparse(file_url).path.split("/")[-1]
                            if not file_name:
                                continue
                            resp = await client.get(file_url)
                            resp.raise_for_status()
                            if file_name.endswith(".md"):
                                dest = skill_dir / "references" / file_name
                            elif file_name.endswith((".py", ".sh", ".js")):
                                dest = skill_dir / "scripts" / file_name
                            else:
                                dest = skill_dir / file_name
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_text(resp.text, encoding="utf-8")
                            installed_files.append(str(dest.relative_to(skill_dir)))
                        except Exception as e:
                            logger.warning(f"Failed to download {file_url}: {e}")

            try:
                # force=True: allow overwriting registered skills with the same name (re-install/upgrade scenarios)
                loaded = self._loader.load_skill(skill_dir, force=True)
                if loaded:
                    # Note: directory cache / Pool notification / event broadcast are handled uniformly by
                    # the upper layer propagate_skill_change; here we only record a catalog_text snapshot for debugging.
                    self._catalog_text = self._catalog.generate_catalog()
                    self._reset_failure_streaks()
                    logger.info(f"Skill installed from URL: {skill_name}")
                else:
                    raise RuntimeError("loader did not return a valid skill")
            except Exception as e:
                logger.error(f"Failed to load installed skill: {e}")
                self._cleanup_broken_skill_dir(skill_dir)
                return f"❌ Skill files downloaded but failed to load: {e}"

            return (
                f"✅ Skill installed successfully!\n\n"
                f"**Skill name**: {skill_name}\n"
                f"**Installation path**: {skill_dir}\n\n"
                f"**Installed files**: {', '.join(installed_files)}\n\n"
                f'Skill has been automatically loaded. Use `get_skill_info("{skill_name}")` to view detailed instructions.'
            )

        except Exception as e:
            logger.error(f"Failed to install skill from URL: {e}")
            if skill_dir:
                self._cleanup_broken_skill_dir(skill_dir)
            return f"❌ URL installation failed: {str(e)}"

    @staticmethod
    def _cleanup_broken_skill_dir(skill_dir: Path) -> None:
        """Clean up leftover directories from failed installations."""
        import shutil

        if skill_dir and skill_dir.exists():
            with contextlib.suppress(Exception):
                shutil.rmtree(skill_dir)
                logger.info(f"Cleaned up broken skill dir: {skill_dir}")

    def _extract_skill_name(self, content: str) -> str | None:
        """Extract skill name from SKILL.md content"""
        try:
            import yaml
        except ImportError:
            return None
        match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if match:
            try:
                metadata = yaml.safe_load(match.group(1))
                return metadata.get("name")
            except Exception:
                pass
        return None

    def _normalize_skill_name(self, name: str) -> str:
        """Normalize skill name"""
        name = name.lower().replace("_", "-").replace(" ", "-")
        name = re.sub(r"[^a-z0-9-]", "", name)
        name = re.sub(r"-+", "-", name).strip("-")
        return name or "custom-skill"

    def _find_skill_md(self, search_dir: Path) -> Path | None:
        """Find SKILL.md in directory, prioritizing root directory, then by path depth."""
        skill_md = search_dir / "SKILL.md"
        if skill_md.exists():
            return skill_md
        candidates = sorted(search_dir.rglob("SKILL.md"), key=lambda p: len(p.parts))
        return candidates[0] if candidates else None

    def _list_skill_candidates(self, base_dir: Path) -> list[str]:
        """List directories that may contain skills"""
        candidates = []
        for path in base_dir.rglob("*.md"):
            if path.name.lower() in ("skill.md", "readme.md"):
                rel_path = path.parent.relative_to(base_dir)
                if str(rel_path) != ".":
                    candidates.append(str(rel_path))
        return candidates

    def _ensure_skill_structure(self, skill_dir: Path) -> None:
        """Ensure skill directory has standard structure"""
        (skill_dir / "scripts").mkdir(exist_ok=True)
        (skill_dir / "references").mkdir(exist_ok=True)
        (skill_dir / "assets").mkdir(exist_ok=True)

    def _format_tree(self, directory: Path, prefix: str = "") -> str:
        """Format directory tree"""
        lines = []
        items = sorted(directory.iterdir(), key=lambda x: (x.is_file(), x.name))
        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{item.name}")
            if item.is_dir():
                extension = "    " if is_last else "│   "
                sub_tree = self._format_tree(item, prefix + extension)
                if sub_tree:
                    lines.append(sub_tree)
        return "\n".join(lines)

"""
Skill loader.

Follows the Agent Skills specification (agentskills.io/specification).
Loads skills defined by SKILL.md from a standard directory structure.
"""

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from .parser import ParsedSkill, SkillMetadata, SkillParser
from .registry import SkillRegistry

_CURRENT_PLATFORM = sys.platform  # "win32", "darwin", "linux"

logger = logging.getLogger(__name__)


def _resolve_user_workspace_skills() -> Path:
    """Dynamically resolve the skills directory for the current user workspace.

    In production uses config.settings.skills_path (auto-adapts to the current workspace and custom root),
    falling back to a path derived from OPENAKITA_ROOT / the default location if import fails.
    """
    try:
        from ..config import settings

        return settings.skills_path
    except Exception:
        import os

        root = os.environ.get("OPENAKITA_ROOT", "").strip()
        if root:
            return Path(root) / "workspaces" / "default" / "skills"
        return Path.home() / ".openakita" / "workspaces" / "default" / "skills"


def _builtin_skills_root() -> Path | None:
    """
    Return the built-in skills directory (shipped with the wheel).

    Expected layout:
    openakita/
      builtin_skills/
        system/<tool-name>/SKILL.md
    """
    try:
        root = Path(__file__).resolve().parents[1] / "builtin_skills"
        return root if root.exists() and root.is_dir() else None
    except Exception:
        return None


# Standard skill directories (ordered by priority)
SKILL_DIRECTORIES = [
    # Built-in system skills (shipped with the pip package, highest priority)
    "__builtin__",
    # User workspace (resolved at runtime based on the current workspace)
    "__user_workspace__",
    # Project level (still scanned in development mode)
    "skills",
]

# System skill directories (loaded first)
SYSTEM_SKILL_DIRECTORIES = [
    "skills",  # System skills also live under skills/; distinguished via the system: true flag
]

# External skills not enabled by default at packaging time (applies to fresh installs / when data/skills.json is absent).
# Once the user toggles selections via the UI, skills.json is created and the user's choice takes precedence.
DEFAULT_DISABLED_SKILLS: frozenset[str] = frozenset(
    {
        "openakita/skills@algorithmic-art",
        "openakita/skills@apify-scraper",
        "jimliu/baoyu-skills@baoyu-article-illustrator",
        "jimliu/baoyu-skills@baoyu-comic",
        "jimliu/baoyu-skills@baoyu-cover-image",
        "jimliu/baoyu-skills@baoyu-format-markdown",
        "jimliu/baoyu-skills@baoyu-image-gen",
        "jimliu/baoyu-skills@baoyu-infographic",
        "jimliu/baoyu-skills@baoyu-slide-deck",
        "jimliu/baoyu-skills@baoyu-url-to-markdown",
        "openakita/skills@bilibili-watcher",
        "openakita/skills@brand-guidelines",
        "openakita/skills@changelog-generator",
        "openakita/skills@chinese-novelist",
        "openakita/skills@chinese-writing",
        "openakita/skills@code-reviewer",
        "openakita/skills@douyin-tool",
        "openakita/skills@frontend-design",
        "openakita/skills@github-automation",
        "openakita/skills@gmail-automation",
        "openakita/skills@google-calendar-automation",
        "openakita/skills@image-understander",
        "openakita/skills@internal-comms",
        "openakita/skills@knowledge-capture",
        "openakita/skills@moltbook",
        "openakita/skills@notebooklm",
        "openakita/skills@obsidian-skills",
        "openakita/skills@ppt-creator",
        "openakita/skills@pretty-mermaid",
        "openakita/skills@slack-gif-creator",
        "openakita/skills@summarizer",
        "obra/superpowers@brainstorming",
        "obra/superpowers@dispatching-parallel-agents",
        "obra/superpowers@executing-plans",
        "obra/superpowers@finishing-a-development-branch",
        "obra/superpowers@receiving-code-review",
        "obra/superpowers@requesting-code-review",
        "obra/superpowers@subagent-driven-development",
        "obra/superpowers@systematic-debugging",
        "obra/superpowers@test-driven-development",
        "obra/superpowers@using-git-worktrees",
        "obra/superpowers@using-superpowers",
        "obra/superpowers@verification-before-completion",
        "obra/superpowers@writing-plans",
        "obra/superpowers@writing-skills",
        "openakita/skills@theme-factory",
        "openakita/skills@todoist-task",
        "openakita/skills@translate-pdf",
        "openakita/skills@video-downloader",
        "openakita/skills@webapp-testing",
        "openakita/skills@wechat-article",
        "openakita/skills@xiaohongshu-creator",
        "openakita/skills@youtube-summarizer",
        "openakita/skills@yuque-skills",
        # IM / office CLIs
        "openakita/skills@feishu-cli",
        "openakita/skills@wecom-cli",
        "openakita/skills@dingtalk-cli",
        # AI video generation
        "openakita/skills@seedance-video",
        # Travel and maps
        "openakita/skills@amap-maps",
        "openakita/skills@fliggy-travel",
        "openakita/skills@didi-ride",
        # Tencent ecosystem
        "openakita/skills@qq-channel",
        "openakita/skills@tencent-meeting",
        "openakita/skills@tencent-survey",
        "openakita/skills@tencent-news",
        "openakita/skills@tencent-ima",
        # Baidu skills
        "openakita/skills@baidu-search",
        "openakita/skills@baidu-netdisk",
        "openakita/skills@baidu-baike",
        "openakita/skills@baidu-maps",
        "openakita/skills@baidu-scholar",
        "openakita/skills@miaoda-app-builder",
        "openakita/skills@baidu-paddleocr-doc",
        "openakita/skills@baidu-paddleocr-text",
        "openakita/skills@baidu-deep-research",
        "openakita/skills@baidu-ecommerce",
        "openakita/skills@baidu-marketing",
        "openakita/skills@baidu-picture-book",
        "openakita/skills@baidu-ppt-gen",
        "openakita/skills@baidu-video-notes",
        "openakita/skills@baidu-yijian",
        "openakita/skills@baidu-famou",
        "openakita/skills@xiaodu-control",
        # E-commerce tools
        "openakita/skills@taobaoke-tool",
        # NetEase Cloud Music
        "openakita/skills@netease-music",
    }
)


class SkillLoader:
    """
    Skill loader.

    Supports:
    - auto-discovering skills from standard directories
    - parsing SKILL.md files
    - loading skill scripts
    - progressive disclosure
    """

    def __init__(
        self,
        registry: SkillRegistry | None = None,
        parser: SkillParser | None = None,
    ):
        self.registry = registry if registry is not None else SkillRegistry()
        self.parser = parser or SkillParser()
        self._loaded_skills: dict[str, ParsedSkill] = {}

    def discover_skill_directories(self, base_path: Path | None = None) -> list[Path]:
        """
        Discover all skill directories.

        Args:
            base_path: base path (project root).

        Returns:
            List of skill directories that exist.
        """
        base_path = base_path or Path.cwd()
        directories = []

        for skill_dir in SKILL_DIRECTORIES:
            if skill_dir == "__builtin__":
                builtin = _builtin_skills_root()
                if builtin is not None:
                    directories.append(builtin)
                    logger.debug(f"Found builtin skill directory: {builtin}")
                continue

            if skill_dir == "__user_workspace__":
                path = _resolve_user_workspace_skills()
            elif skill_dir.startswith("~"):
                path = Path(skill_dir).expanduser()
            else:
                path = base_path / skill_dir

            if path.exists() and path.is_dir():
                directories.append(path)
                logger.debug(f"Found skill directory: {path}")

        return directories

    def load_all(self, base_path: Path | None = None) -> int:
        """
        Load skills from all standard directories.

        Args:
            base_path: base path.

        Returns:
            Number of skills loaded.
        """
        directories = self.discover_skill_directories(base_path)
        loaded = 0

        for skill_dir in directories:
            loaded += self.load_from_directory(skill_dir)

        loaded += self._load_cli_anything_skills()

        return loaded

    def _load_cli_anything_skills(self) -> int:
        """Discover and load SKILL.md files from pip-installed cli-anything-* packages.

        CLI-Anything generates SKILL.md alongside each CLI harness. When installed
        via pip, these live under the package's site-packages directory (e.g.
        ``cli_anything/gimp/SKILL.md``). This method scans for them so that
        ``pip install cli-anything-gimp`` makes the skill auto-discoverable.
        """
        loaded = 0
        try:
            import importlib.metadata as importlib_metadata
        except ImportError:
            return 0

        try:
            distributions = list(importlib_metadata.distributions())
        except Exception:
            return 0

        for dist in distributions:
            name = (dist.metadata.get("Name") or "").lower()
            if not name.startswith("cli-anything-"):
                continue

            dist_files = dist.files
            if not dist_files:
                continue

            for rel_path in dist_files:
                if rel_path.name.upper() == "SKILL.MD":
                    try:
                        full_path = rel_path.locate()
                        if isinstance(full_path, Path) and full_path.exists():
                            skill_dir = full_path.parent
                            skill = self.load_skill(skill_dir, force=True)
                            if skill:
                                loaded += 1
                                logger.info(
                                    f"Loaded cli-anything skill from pip package: {name} ({skill_dir})"
                                )
                    except Exception as e:
                        logger.debug(f"Failed to load cli-anything skill from {name}: {e}")

        if loaded:
            logger.info(f"Loaded {loaded} cli-anything skills from pip packages")
        return loaded

    def load_from_directory(self, directory: Path, *, force: bool = True) -> int:
        """
        Load all skills from a directory.

        Each subdirectory containing a SKILL.md is treated as a skill.
        Special handling: the 'system' subdirectory is scanned recursively and is used to hold system-tool skills.

        Args:
            directory: skill directory.
            force: whether to allow overwriting an already-registered skill with the same name (default True,
                so repeated calls to ``load_all`` pick up the user's latest edits to SKILL.md; pass False only when
                the first registration should be preserved).

        Returns:
            Number of skills loaded.
        """
        if not directory.exists():
            logger.warning(f"Skill directory not found: {directory}")
            return 0

        loaded = 0

        for item in directory.iterdir():
            if not item.is_dir():
                continue

            skill_md = item / "SKILL.md"
            if skill_md.exists():
                try:
                    skill = self.load_skill(item, force=force)
                    if skill:
                        loaded += 1
                except Exception as e:
                    logger.error(f"Failed to load skill from {item}: {e}")
            elif item.name in ("system", "external", "custom", "community", "builtin"):
                loaded += self.load_from_directory(item, force=force)

        logger.info(f"Loaded {loaded} skills from {directory}")
        return loaded

    @staticmethod
    def _is_os_compatible(supported_os: list[str]) -> bool:
        """Check if the current platform is in the skill's supported OS list.

        Empty list means all platforms are supported.
        """
        if not supported_os:
            return True
        return _CURRENT_PLATFORM in supported_os

    def load_skill(
        self,
        skill_dir: Path,
        *,
        plugin_source: str | None = None,
        force: bool = False,
    ) -> ParsedSkill | None:
        """
        Load a single skill.

        Args:
            skill_dir: skill directory.
            plugin_source: plugin source identifier.
            force: allow overwriting an already-registered skill with the same name (for reload / reinstall scenarios).

        Returns:
            ParsedSkill or None.
        """
        try:
            skill = self.parser.parse_directory(skill_dir)

            # Load sidecar translation file
            self._load_i18n(skill_dir, skill.metadata)

            # OS compatibility check
            if not self._is_os_compatible(skill.metadata.supported_os):
                logger.debug(
                    f"Skipping skill {skill.metadata.name}: "
                    f"not compatible with {_CURRENT_PLATFORM} "
                    f"(requires {skill.metadata.supported_os})"
                )
                return None

            # Validation: hard errors block registration, warnings are logged
            errors = self.parser.validate(skill)
            hard_errors = [e for e in (errors or []) if e.startswith("ERROR:")]
            warnings = [e for e in (errors or []) if not e.startswith("ERROR:")]
            for w in warnings:
                logger.warning(f"Skill validation warning: {w}")
            if hard_errors:
                for e in hard_errors:
                    logger.error(f"Skill validation error: {e}")
                logger.error(f"Skill '{skill_dir.name}' rejected due to validation errors")
                return None

            sid = skill_dir.name

            registered = self.registry.register(
                skill,
                skill_id=sid,
                plugin_source=plugin_source,
                force=force,
            )
            if not registered:
                logger.warning(f"Skill '{sid}' registration rejected (conflict)")
                return None

            self._loaded_skills[sid] = skill
            logger.info(f"Loaded skill: {sid} (name={skill.metadata.name})")
            return skill

        except Exception as e:
            logger.error(f"Failed to load skill from {skill_dir}: {e}")
            return None

    def _load_i18n(self, skill_dir: Path, metadata: SkillMetadata) -> None:
        """Load internationalization data into metadata.

        Prefers the i18n field from agents/openai.yaml, falling back to .openakita-i18n.json.
        """
        from .i18n import read_i18n

        data = read_i18n(skill_dir)
        for lang, fields in data.items():
            if not isinstance(fields, dict):
                continue
            if "name" in fields:
                metadata.name_i18n[lang] = str(fields["name"])
            if "description" in fields:
                metadata.description_i18n[lang] = str(fields["description"])

    def _resolve_skill(self, key: str) -> ParsedSkill | None:
        """Look up by skill_id, falling back to name matching."""
        skill = self._loaded_skills.get(key)
        if skill is not None:
            return skill
        for s in self._loaded_skills.values():
            if s.metadata.name == key:
                return s
        return None

    def get_skill(self, key: str) -> ParsedSkill | None:
        """Return a loaded skill (accepts skill_id or name)."""
        return self._resolve_skill(key)

    def get_skill_body(self, key: str) -> str | None:
        """
        Return the skill's full instructions (body).

        This is the second level of progressive disclosure:
        - Level 1: metadata (name, description) — loaded at startup.
        - Level 2: full instructions (body) — loaded on activation.
        - Level 3: resource files — loaded on demand.
        """
        skill = self._resolve_skill(key)
        if skill:
            return skill.body
        return None

    def compute_effective_allowlist(self, external_allowlist: set[str] | None) -> set[str] | None:
        """Compute the effective allowlist from skills.json's allowlist and the default-disabled list.

        - skills.json exists with an external_allowlist -> use it directly (explicit user choice).
        - skills.json is absent (external_allowlist is None) -> use all external skills minus DEFAULT_DISABLED_SKILLS.
        """
        if external_allowlist is not None:
            return external_allowlist

        if not DEFAULT_DISABLED_SKILLS:
            return None

        all_external = {
            sid
            for sid, skill in self._loaded_skills.items()
            if not getattr(skill.metadata, "system", False)
        }
        return all_external - DEFAULT_DISABLED_SKILLS

    def prune_external_by_allowlist(
        self,
        external_allowlist: set[str] | None,
        agent_referenced_skills: set[str] | None = None,
    ) -> int:
        """
        Prune / flag loaded skills against the external-skills allowlist.

        Conventions:
        - System skills are always retained and enabled.
        - external_allowlist is None -> no restriction (all enabled).
        - external_allowlist is set() -> all external skills disabled.

        External skills not in the allowlist:
        - referenced by agent_referenced_skills -> kept but flagged disabled=True
          (sub-agent INCLUSIVE mode can enable them explicitly).
        - otherwise -> removed from both the registry and the loader.
        """
        if external_allowlist is None:
            for name in self._loaded_skills:
                self.registry.set_disabled(name, False)
            return 0

        keep_extra = agent_referenced_skills or set()
        removed = 0
        disabled_count = 0
        for name, skill in list(self._loaded_skills.items()):
            try:
                if getattr(skill.metadata, "system", False):
                    self.registry.set_disabled(name, False)
                    continue
            except Exception:
                continue

            if name in external_allowlist:
                self.registry.set_disabled(name, False)
            elif name in keep_extra:
                self.registry.set_disabled(name, True)
                disabled_count += 1
            else:
                self._loaded_skills.pop(name, None)
                try:
                    self.registry.unregister(name)
                except Exception:
                    pass
                removed += 1

        if removed or disabled_count:
            logger.info(
                f"External skills filtered: {removed} removed, "
                f"{disabled_count} disabled (kept for sub-agents)"
            )
        return removed

    def get_script_content(self, name: str, script_name: str) -> str | None:
        """
        Return the contents of a skill script.

        Args:
            name: skill name.
            script_name: script file name.

        Returns:
            Script contents or None.
        """
        skill = self._loaded_skills.get(name)
        if not skill:
            return None

        script_path = self._resolve_script_path(skill, script_name)
        if script_path:
            return script_path.read_text(encoding="utf-8")

        return None

    _SCRIPT_SUFFIXES = frozenset({".py", ".sh", ".bash", ".js", ".ts", ".mjs"})
    _SCRIPT_IGNORE = frozenset({"__init__.py", "__pycache__"})

    def _list_available_scripts(self, skill: ParsedSkill) -> list[str]:
        """List all executable scripts in a skill (recursively under scripts/ plus top-level root files)."""
        scripts: list[str] = []

        if skill.scripts_dir and skill.scripts_dir.is_dir():
            for f in sorted(skill.scripts_dir.rglob("*")):
                if (
                    f.is_file()
                    and f.suffix in self._SCRIPT_SUFFIXES
                    and f.name not in self._SCRIPT_IGNORE
                ):
                    rel = f.relative_to(skill.scripts_dir)
                    scripts.append(f"scripts/{rel.as_posix()}")

        for f in sorted(skill.skill_dir.iterdir()):
            if (
                f.is_file()
                and f.suffix in self._SCRIPT_SUFFIXES
                and f.name not in self._SCRIPT_IGNORE
            ):
                scripts.append(f.name)

        return scripts

    def _resolve_script_path(self, skill: ParsedSkill, script_name: str) -> Path | None:
        """Look up a script file in both the skill's scripts/ directory and its root.

        Many external skills (e.g. Anthropic's xlsx, pdf, etc.) place scripts directly
        in the skill root rather than in a scripts/ subdirectory, so we check both.

        Safety: the resolved path must remain inside the skill directory to prevent ``../`` traversal.
        """
        for base in (skill.scripts_dir, skill.skill_dir):
            if base is None:
                continue
            candidate = (base / script_name).resolve()
            try:
                candidate.relative_to(skill.skill_dir.resolve())
            except ValueError:
                logger.warning(
                    "Script path traversal blocked: %s resolves outside skill dir %s",
                    script_name,
                    skill.skill_dir,
                )
                return None
            if candidate.exists():
                return candidate
        return None

    def run_script(
        self,
        name: str,
        script_name: str,
        args: list[str] | None = None,
        cwd: Path | None = None,
    ) -> tuple[bool, str]:
        """
        Run a skill script.

        Args:
            name: skill name.
            script_name: script file name.
            args: command-line arguments.
            cwd: working directory.

        Returns:
            (success, output) tuple.
        """
        skill = self._resolve_skill(name)
        if not skill:
            return False, f"Skill not found: {name}"

        script_path = self._resolve_script_path(skill, script_name)
        if not script_path:
            available = self._list_available_scripts(skill)
            if available:
                return False, (
                    f"Script not found: {script_name}\n"
                    f"Available scripts: {', '.join(available)}\n"
                    f'Use one of the available scripts, or use get_skill_info("{name}") '
                    f"to check usage instructions."
                )
            else:
                return False, (
                    f"Script not found: {script_name}\n"
                    f"This skill has NO executable scripts — it is an instruction-only skill.\n"
                    f"DO NOT retry run_skill_script for this skill.\n"
                    f'Instead: use get_skill_info("{name}") to read the skill instructions, '
                    f"then write Python code and execute it via run_shell."
                )

        # Determine how to run the script
        args = args or []

        if script_path.suffix == ".py":
            # PyInstaller compatibility: use runtime_env to fetch the correct Python interpreter
            from openakita.runtime_env import get_python_executable

            py = get_python_executable()
            if not py:
                return False, "Python interpreter is unavailable; cannot execute the script"
            cmd = [py, str(script_path)] + args
        elif script_path.suffix in (".sh", ".bash"):
            bash_path = shutil.which("bash")
            if not bash_path:
                # On Windows, try the common Git Bash paths
                if sys.platform == "win32":
                    import os as _os

                    _sd = _os.environ.get("SYSTEMDRIVE", "C:")
                    for candidate in [
                        rf"{_sd}\Program Files\Git\bin\bash.exe",
                        rf"{_sd}\Program Files (x86)\Git\bin\bash.exe",
                    ]:
                        if Path(candidate).exists():
                            bash_path = candidate
                            break
                if not bash_path:
                    return False, (
                        f"Cannot run {script_name}: 'bash' not found on this system. "
                        f"On Windows, install Git for Windows (https://git-scm.com) to get bash."
                    )
            cmd = [bash_path, str(script_path)] + args
        elif script_path.suffix == ".js":
            cmd = ["node", str(script_path)] + args
        else:
            # Try running directly
            cmd = [str(script_path)] + args

        try:
            extra: dict = {}
            if sys.platform == "win32":
                extra["creationflags"] = subprocess.CREATE_NO_WINDOW

            MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MB

            proc = subprocess.Popen(
                cmd,
                cwd=cwd or skill.skill_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **extra,
            )
            try:
                raw_stdout, raw_stderr = proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                return False, "Script execution timed out"
            except Exception as comm_err:
                proc.kill()
                proc.wait()
                return False, f"Script communication failed: {comm_err}"

            truncated = False
            stdout_bytes = raw_stdout[:MAX_OUTPUT_BYTES] if raw_stdout else b""
            stderr_bytes = raw_stderr[:MAX_OUTPUT_BYTES] if raw_stderr else b""
            if (raw_stdout and len(raw_stdout) > MAX_OUTPUT_BYTES) or (
                raw_stderr and len(raw_stderr) > MAX_OUTPUT_BYTES
            ):
                truncated = True

            output = stdout_bytes.decode("utf-8", errors="replace")
            if stderr_bytes:
                output += f"\nSTDERR:\n{stderr_bytes.decode('utf-8', errors='replace')}"
            if truncated:
                output += "\n\n[OUTPUT TRUNCATED — exceeded 1 MB limit]"

            return proc.returncode == 0, output

        except Exception as e:
            return False, f"Script execution failed: {e}"

    def get_reference(self, name: str, ref_name: str) -> str | None:
        """
        Return a skill reference document.

        Args:
            name: skill name (accepts skill_id or display name).
            ref_name: reference document name (e.g. REFERENCE.md).

        Returns:
            Document contents or None.
        """
        skill = self._resolve_skill(name)
        if not skill or not skill.references_dir:
            return None

        ref_path = (skill.references_dir / ref_name).resolve()
        try:
            ref_path.relative_to(skill.references_dir.resolve())
        except ValueError:
            logger.warning(
                "Reference path traversal blocked: %s resolves outside references dir %s",
                ref_name,
                skill.references_dir,
            )
            return None
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8", errors="replace")

        return None

    def unload_skill(self, name: str) -> bool:
        """Unload a skill."""
        if name in self._loaded_skills:
            del self._loaded_skills[name]
            self.registry.unregister(name)
            logger.info(f"Unloaded skill: {name}")
            return True
        return False

    def reload_skill(self, name: str) -> ParsedSkill | None:
        """Reload a skill."""
        skill = self._loaded_skills.get(name)
        if not skill:
            return None

        skill_dir = skill.skill_dir
        plugin_source = None
        entry = self.registry.get(name)
        if entry:
            plugin_source = entry.plugin_source
        self.unload_skill(name)
        return self.load_skill(skill_dir, plugin_source=plugin_source, force=True)

    @property
    def loaded_count(self) -> int:
        """Number of loaded skills."""
        return len(self._loaded_skills)

    @property
    def loaded_skills(self) -> list[ParsedSkill]:
        """All loaded skills."""
        return list(self._loaded_skills.values())

    @property
    def system_skills(self) -> list[ParsedSkill]:
        """All system skills."""
        return [s for s in self._loaded_skills.values() if s.metadata.system]

    @property
    def external_skills(self) -> list[ParsedSkill]:
        """All external skills."""
        return [s for s in self._loaded_skills.values() if not s.metadata.system]

    def get_skill_by_tool_name(self, tool_name: str) -> ParsedSkill | None:
        """
        Get a skill by tool name.

        Args:
            tool_name: original tool name (e.g. 'browser_navigate').

        Returns:
            ParsedSkill or None.
        """
        for skill in self._loaded_skills.values():
            if skill.metadata.tool_name == tool_name:
                return skill
        return None

    def get_skills_by_handler(self, handler: str) -> list[ParsedSkill]:
        """
        Get all skills associated with the given handler.

        Args:
            handler: handler name (e.g. 'browser').

        Returns:
            List of skills.
        """
        return [s for s in self._loaded_skills.values() if s.metadata.handler == handler]

    def get_tool_definitions(self) -> list[dict]:
        """
        Return tool definitions for all system skills.

        Used as the tools parameter passed to the LLM API.

        Returns:
            List of tool definitions.
        """
        from ..tools.definitions import BASE_TOOLS

        definitions = []

        # Build tool definitions from system skills
        for skill in self.system_skills:
            # Look up the corresponding original tool definition
            original_def = None
            for tool in BASE_TOOLS:
                if tool.get("name") == skill.metadata.tool_name:
                    original_def = tool
                    break

            if original_def:
                # Use the original definition but update the description (if SKILL.md has a more detailed one)
                tool_def = original_def.copy()
                # The SKILL.md description can override here
                definitions.append(tool_def)
            else:
                # No original definition; derive one from SKILL.md
                definitions.append(
                    {
                        "name": skill.metadata.tool_name,
                        "description": skill.metadata.description,
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                        },
                    }
                )

        return definitions

    def is_system_skill(self, name: str) -> bool:
        """Check whether the skill is a system skill."""
        skill = self._loaded_skills.get(name)
        return skill.metadata.system if skill else False

    def get_handler_name(self, name: str) -> str | None:
        """Return the skill's handler name."""
        skill = self._loaded_skills.get(name)
        return skill.metadata.handler if skill else None

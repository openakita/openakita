"""
Skill management handler

Handles system skills related to skill management (10 tools total):
- list_skills: List skills
- get_skill_info: Get skill information
- run_skill_script: Run a skill script
- get_skill_reference: Get reference documentation
- install_skill: Install a skill
- load_skill: Load a newly created skill
- reload_skill: Reload a modified skill
- manage_skill_enabled: Enable/disable skills
- execute_skill: Execute a skill in an isolated context (F10)
- uninstall_skill: Uninstall an external skill (F14)
"""

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.tool_executor import MAX_TOOL_RESULT_CHARS, OVERFLOW_MARKER, save_overflow
from ...skills.events import SkillEvent
from ...skills.exposure import build_skill_exposure

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

# Skill-specific content threshold (~32000 tokens), higher than the general MAX_TOOL_RESULT_CHARS (16000 chars).
# Skill bodies are high-quality structured instructions; truncating them severely affects LLM execution quality.
# Some skills (e.g. docx) have SKILL.md referencing multiple sibling files, which can total 50K+ when inlined.
SKILL_MAX_CHARS = 64000


class SkillsHandler:
    """Skill management handler"""

    TOOLS = [
        "list_skills",
        "get_skill_info",
        "run_skill_script",
        "get_skill_reference",
        "install_skill",
        "load_skill",
        "reload_skill",
        "manage_skill_enabled",
        "execute_skill",
        "uninstall_skill",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """Handle tool invocation"""
        try:
            if tool_name == "list_skills":
                return self._list_skills(params)
            elif tool_name == "get_skill_info":
                return self._get_skill_info(params)
            elif tool_name == "run_skill_script":
                return self._run_skill_script(params)
            elif tool_name == "get_skill_reference":
                return self._get_skill_reference(params)
            elif tool_name == "install_skill":
                return await self._install_skill(params)
            elif tool_name == "load_skill":
                return self._load_skill(params)
            elif tool_name == "reload_skill":
                return self._reload_skill(params)
            elif tool_name == "manage_skill_enabled":
                return self._manage_skill_enabled(params)
            elif tool_name == "execute_skill":
                return await self._execute_skill(params)
            elif tool_name == "uninstall_skill":
                return self._uninstall_skill(params)
            else:
                return f"❌ Unknown skills tool: {tool_name}"
        except KeyError as e:
            logger.error("Missing required parameter in %s: %s", tool_name, e)
            return f"❌ Missing required parameter: {e}"
        except Exception as e:
            logger.error("Unexpected error in skills handler %s: %s", tool_name, e, exc_info=True)
            return f"❌ Skill operation failed: {e}"

    def _list_skills(self, params: dict) -> str:
        """List all skills, distinguishing enabled/disabled/discoverable states"""
        all_skills = self.agent.skill_registry.list_all(include_disabled=True)
        if not all_skills:
            return (
                "No skills are currently installed\n\n"
                "Hint: Skills may come from the built-in directory, the user workspace directory, or the project directory. "
                "Each skill should contain a SKILL.md; use get_skill_info when you need an exact path."
            )

        system_skills = [s for s in all_skills if s.system]
        enabled_external = [
            s for s in all_skills if not s.system and not s.disabled and not s.catalog_hidden
        ]
        discoverable_external = [
            s for s in all_skills if not s.system and not s.disabled and s.catalog_hidden
        ]
        disabled_external = [s for s in all_skills if not s.system and s.disabled]

        enabled_total = len(system_skills) + len(enabled_external)
        output = (
            f"{len(all_skills)} skills installed "
            f"({enabled_total} preloaded, "
            f"{len(discoverable_external)} discoverable, "
            f"{len(disabled_external)} disabled):\n\n"
        )

        if system_skills:
            output += f"**System skills ({len(system_skills)})** [all enabled]:\n"
            for skill in system_skills:
                exposed = build_skill_exposure(skill)
                auto = "automatic" if not skill.disable_model_invocation else "manual"
                zh_name = skill.name_i18n.get("zh", "")
                name_part = f"{skill.name} ({zh_name})" if zh_name else skill.name
                output += f"- {name_part} [{auto}] - {skill.description}\n"
                output += (
                    f"  source={exposed.origin_label}"
                    + (f", tool={exposed.tool_name}" if exposed.tool_name else "")
                    + (f", handler={exposed.handler}" if exposed.handler else "")
                    + (f", path={exposed.skill_dir}" if exposed.skill_dir else "")
                    + "\n"
                )
            output += "\n"

        if enabled_external:
            output += f"**Enabled external skills ({len(enabled_external)})**:\n"
            for skill in enabled_external:
                exposed = build_skill_exposure(skill)
                auto = "automatic" if not skill.disable_model_invocation else "manual"
                zh_name = skill.name_i18n.get("zh", "")
                name_part = f"{skill.name} ({zh_name})" if zh_name else skill.name
                output += f"- {name_part} [{auto}]\n"
                output += f"  {skill.description}\n"
                output += (
                    f"  source={exposed.origin_label}"
                    + (f", path={exposed.skill_dir}" if exposed.skill_dir else "")
                    + "\n\n"
                )

        if discoverable_external:
            output += (
                f"**Discoverable skills ({len(discoverable_external)})** "
                "[not preloaded — use get_skill_info(skill_name) to load instructions before use]:\n"
            )
            for skill in discoverable_external:
                exposed = build_skill_exposure(skill)
                zh_name = skill.name_i18n.get("zh", "")
                name_part = f"{skill.name} ({zh_name})" if zh_name else skill.name
                output += f"- {name_part} [discoverable]\n"
                output += f"  {skill.description}\n"
                output += (
                    f"  source={exposed.origin_label}"
                    + (f", path={exposed.skill_dir}" if exposed.skill_dir else "")
                    + "\n\n"
                )

        if disabled_external:
            output += (
                f"**Disabled external skills ({len(disabled_external)})** [must be enabled in the skills panel before use]:\n"
            )
            for skill in disabled_external:
                exposed = build_skill_exposure(skill)
                zh_name = skill.name_i18n.get("zh", "")
                name_part = f"{skill.name} ({zh_name})" if zh_name else skill.name
                output += f"- {name_part} [disabled]\n"
                output += f"  {skill.description}\n"
                output += (
                    f"  source={exposed.origin_label}"
                    + (f", path={exposed.skill_dir}" if exposed.skill_dir else "")
                    + "\n\n"
                )

        return self._truncate_skill_content("list_skills", output)

    # Regex for matching sibling .md file references in Markdown links:
    #   [`filename.md`](filename.md)  or  [filename.md](filename.md)
    _MD_LINK_RE = re.compile(r"\[`?([a-zA-Z0-9_-]+\.md)`?\]\(([a-zA-Z0-9_-]+\.md)\)")

    @staticmethod
    def _inline_referenced_files(body: str, skill_dir: Path) -> str:
        """Parse sibling .md files referenced in body and append them to the end.

        Many Anthropic skills (docx, pptx, etc.) use Markdown links in SKILL.md
        to reference sibling reference files (e.g. docx-js.md, ooxml.md) annotated
        with "MANDATORY - READ ENTIRE FILE". This method auto-inlines those files,
        so get_skill_info returns the complete skill knowledge in a single call.
        """
        if not skill_dir or not skill_dir.is_dir():
            return body

        seen: set[str] = set()
        appendices: list[str] = []

        for match in SkillsHandler._MD_LINK_RE.finditer(body):
            filename = match.group(2)
            if filename.upper() == "SKILL.MD" or filename in seen:
                continue
            seen.add(filename)

            ref_path = skill_dir / filename
            if not ref_path.is_file():
                continue

            try:
                ref_content = ref_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to read referenced file {ref_path}: {e}")
                continue

            appendices.append(f"\n\n---\n\n# [Inlined Reference] {filename}\n\n{ref_content}")
            logger.info(
                f"[SkillInline] Inlined {filename} ({len(ref_content)} chars) from {skill_dir.name}"
            )

        if appendices:
            return body + "".join(appendices)
        return body

    @staticmethod
    def _truncate_skill_content(tool_name: str, content: str) -> str:
        """Skill-specific truncation: threshold higher than the general guard; self-truncates when oversized and marks content to bypass the guard.

        - <= MAX_TOOL_RESULT_CHARS (16000): returned as-is; the general guard will not truncate either
        - 16000 < len <= SKILL_MAX_CHARS (64000): full content + OVERFLOW_MARKER to bypass the guard
        - > SKILL_MAX_CHARS: truncate to 64000 + overflow file + guidance for paginated reading
        """
        if not content or len(content) <= MAX_TOOL_RESULT_CHARS:
            return content

        if len(content) <= SKILL_MAX_CHARS:
            return content + f"\n\n{OVERFLOW_MARKER}"

        total_chars = len(content)
        overflow_path = save_overflow(tool_name, content)
        truncated = content[:SKILL_MAX_CHARS]
        hint = (
            f"\n\n{OVERFLOW_MARKER} Skill content is {total_chars} characters in total, "
            f"truncated to the first {SKILL_MAX_CHARS} characters.\n"
            f"The full content has been saved; use the following command to view the rest:\n"
            f'read_file(path="{overflow_path}", offset=1, limit=500)'
        )
        logger.info(
            f"[SkillTruncate] {tool_name} output: {total_chars} → {SKILL_MAX_CHARS} chars, "
            f"overflow saved to {overflow_path}"
        )
        return truncated + hint

    def _get_skill_info(self, params: dict) -> str:
        """Get detailed skill information (automatically inlines referenced sub-files)"""
        skill_name = params["skill_name"]
        user_args = params.get("args", {})
        skill = self.agent.skill_registry.get(skill_name)

        if not skill or skill.disabled:
            available = [s.name for s in self.agent.skill_registry.list_all()[:10]]
            hint = f". Currently available skills: {', '.join(available)}" if available else ""
            return (
                f"Skill '{skill_name}' not found{hint}. "
                f"Please verify the skill name, or use list_skills to view all available skills."
            )

        # F6: usage tracking
        usage_tracker = getattr(self.agent, "_skill_usage_tracker", None)
        if usage_tracker:
            usage_tracker.record(skill.skill_id)

        # F7: inject allowed_tools into policy engine
        if skill.allowed_tools:
            try:
                from openakita.core.policy import get_policy_engine

                get_policy_engine().add_skill_allowlist(skill.skill_id, skill.allowed_tools)
            except Exception as e:
                logger.warning("Failed to inject skill allowlist for %s: %s", skill.skill_id, e)

        exposed = build_skill_exposure(skill)
        body = skill.get_body() or "(no detailed instructions)"

        # F4: argument substitution
        if "{{" in body:
            from openakita.config import settings as _cfg
            from openakita.skills.arguments import substitute

            extra = {}
            if isinstance(user_args, dict):
                extra = {k: str(v) for k, v in user_args.items()}
            body = substitute(body, extra, project_root=_cfg.project_root)

        # Auto-inline sibling .md files referenced in the SKILL.md body
        if exposed.skill_path:
            skill_dir = Path(exposed.skill_path).parent
            body = self._inline_referenced_files(body, skill_dir)

        output = f"# Skill: {skill.name}\n\n"
        output += f"**ID**: {skill.skill_id}\n"
        output += f"**Description**: {skill.description}\n"
        if skill.when_to_use:
            output += f"**When to use**: {skill.when_to_use}\n"
        output += f"**Source**: {exposed.origin_label}\n"
        if exposed.skill_dir:
            output += f"**Path**: {exposed.skill_dir}\n"
        if exposed.root_dir:
            output += f"**Root directory**: {exposed.root_dir}\n"
        if skill.system:
            output += "**Type**: System skill\n"
            output += f"**Tool name**: {skill.tool_name}\n"
            output += f"**Handler**: {skill.handler}\n"
        else:
            output += "**Type**: External skill\n"
        if exposed.instruction_only:
            output += "**Scripts**: instruction-only (no executable scripts)\n"
        else:
            output += f"**Executable scripts**: {', '.join(exposed.scripts)}\n"
        if exposed.references:
            output += f"**Reference docs**: {', '.join(exposed.references)}\n"
        output += (
            "**Path rules**: Skills may come from multiple directories; do not guess the skill file location from the workspace map. "
            "Rely on the source and path shown above.\n"
        )
        if skill.license:
            output += f"**License**: {skill.license}\n"
        if skill.compatibility:
            output += f"**Compatibility**: {skill.compatibility}\n"
        if skill.model:
            output += f"**Recommended model**: {skill.model}\n"
        if skill.execution_context and skill.execution_context != "inline":
            output += f"**Execution mode**: {skill.execution_context}\n"

        # F4: display argument schema
        if skill.arguments:
            from openakita.skills.arguments import format_argument_schema

            args_block = format_argument_schema(skill.arguments)
            if args_block:
                output += f"\n{args_block}\n"

        from ...utils.context_scan import scan_context_content

        body, threats = scan_context_content(body, source=f"skill:{skill_name}")
        output += "\n---\n\n"
        output += body

        return self._truncate_skill_content("get_skill_info", output)

    def _run_skill_script(self, params: dict) -> str:
        """Run a skill script"""
        skill_name = params["skill_name"]
        script_name = params["script_name"]
        args = params.get("args", [])
        cwd_raw = params.get("cwd")

        resolved_cwd: Path | None = None
        if cwd_raw:
            resolved_cwd = Path(cwd_raw).resolve()
            from openakita.config import settings as _settings

            project_root = Path(_settings.project_root).resolve()
            skill_entry = self.agent.skill_registry.get(skill_name)
            skill_dir = (
                Path(skill_entry.skill_path).resolve()
                if skill_entry and skill_entry.skill_path
                else None
            )

            allowed = False
            try:
                resolved_cwd.relative_to(project_root)
                allowed = True
            except ValueError:
                pass
            if not allowed and skill_dir:
                try:
                    resolved_cwd.relative_to(skill_dir)
                    allowed = True
                except ValueError:
                    pass
            if not allowed:
                return f"❌ Working directory rejected: {cwd_raw}\ncwd must be inside the project workspace or the skill directory."

        success, output = self.agent.skill_loader.run_script(
            skill_name, script_name, args, cwd=resolved_cwd
        )

        if success:
            return f"✅ Script executed successfully:\n{output}"
        else:
            output_lower = output.lower()

            if "no executable scripts" in output_lower or "instruction-only" in output_lower:
                return (
                    f"❌ Script execution failed:\n{output}\n\n"
                    f"**This skill is instruction-only (no scripts).** "
                    f"DO NOT retry run_skill_script.\n"
                    f'Use `get_skill_info("{skill_name}")` to read instructions, '
                    f"then write Python code via `write_file` and execute via `run_shell`."
                )
            elif "not found" in output_lower and "available scripts:" in output_lower:
                return (
                    f"❌ Script execution failed:\n{output}\n\n"
                    f"**Suggestion**: Use one of the available scripts listed above."
                )
            elif "not found" in output_lower:
                return (
                    f"❌ Script execution failed:\n{output}\n\n"
                    f'**Suggestion**: If you are unsure how to use it, run `get_skill_info("{skill_name}")` to view the full skill instructions.\n'
                    f"For instruction-only skills, use write_file + run_shell to execute code instead."
                )
            elif "timed out" in output_lower:
                return (
                    f"❌ Script execution failed:\n{output}\n\n"
                    f"**Suggestion**: Script execution timed out. You can try:\n"
                    f"1. Checking for infinite loops or long-blocking operations in the script\n"
                    f"2. Using `get_skill_info` to review the skill details and confirm usage\n"
                    f"3. Trying a different approach to complete the task"
                )
            elif "permission" in output_lower:
                return (
                    f"❌ Script execution failed:\n{output}\n\n"
                    f"**Suggestion**: Insufficient permissions. You can try:\n"
                    f"1. Checking file/directory permissions\n"
                    f"2. Running with administrator privileges"
                )
            else:
                return (
                    f"❌ Script execution failed:\n{output}\n\n"
                    f"**Suggestion**: Please verify the script arguments, or use `get_skill_info` to view the skill's usage instructions"
                )

    def _get_skill_reference(self, params: dict) -> str:
        """Get skill reference documentation"""
        skill_name = params["skill_name"]
        ref_name = params.get("ref_name", "REFERENCE.md")

        content = self.agent.skill_loader.get_reference(skill_name, ref_name)

        if content:
            output = f"# Reference: {ref_name}\n\n{content}"
            return self._truncate_skill_content("get_skill_reference", output)
        else:
            return f"❌ Reference not found: {skill_name}/{ref_name}"

    async def _install_skill(self, params: dict) -> str:
        """Install a skill"""
        source = params["source"]
        name = params.get("name")
        subdir = params.get("subdir")
        extra_files = params.get("extra_files", [])

        result = await self.agent.skill_manager.install_skill(source, name, subdir, extra_files)
        self.agent.propagate_skill_change(SkillEvent.INSTALL)
        return result

    def _load_skill(self, params: dict) -> str:
        """Load a newly created skill"""
        skill_name = params["skill_name"]

        # Locate the skill directory (use project root, avoid depending on CWD)
        try:
            from openakita.config import settings

            skills_dir = settings.project_root / "skills"
        except Exception:
            skills_dir = Path("skills")
        skill_dir = skills_dir / skill_name

        if not skill_dir.exists():
            return f"❌ Skill directory does not exist: {skill_dir}\n\nMake sure the skill has been saved to skills/{skill_name}/"

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return f"❌ Skill definition file does not exist: {skill_md}\n\nMake sure the directory contains a SKILL.md file"

        # Check whether already loaded
        existing = self.agent.skill_registry.get(skill_name)
        if existing:
            return f"⚠️ Skill '{skill_name}' already exists. To update it, use reload_skill"

        try:
            loaded = self.agent.skill_loader.load_skill(skill_dir, force=True)

            if loaded:
                # All refreshes (catalog / tool mapping / activation / pool / system prompt / event)
                # are done inside propagate_skill_change; do not manually call any sub-steps
                # from the tool handler layer.
                self.agent.propagate_skill_change(SkillEvent.LOAD, rescan=False)
                logger.info(f"Skill loaded: {skill_name}")

                return f"""✅ Skill loaded successfully!

**Skill name**: {loaded.metadata.name}
**Description**: {loaded.metadata.description}
**Type**: {"System skill" if loaded.metadata.system else "External skill"}
**Path**: {skill_dir}

The skill is now available; use `get_skill_info("{skill_name}")` to view details."""
            else:
                return "❌ Failed to load skill. Please check whether SKILL.md has the correct format"

        except Exception as e:
            logger.error(f"Failed to load skill {skill_name}: {e}")
            return f"❌ Error while loading skill: {e}"

    def _reload_skill(self, params: dict) -> str:
        """Reload an already loaded skill"""
        skill_name = params["skill_name"]

        # Check whether the skill is already loaded
        existing = self.agent.skill_loader.get_skill(skill_name)
        if not existing:
            return f"❌ Skill '{skill_name}' is not loaded. To load a new skill, use load_skill"

        try:
            reloaded = self.agent.skill_loader.reload_skill(skill_name)

            if reloaded:
                # loader.reload_skill has already re-registered the single skill; rescan=False avoids a full re-scan.
                self.agent.propagate_skill_change(SkillEvent.RELOAD, rescan=False)
                logger.info(f"Skill reloaded: {skill_name}")

                return f"""✅ Skill reloaded successfully!

**Skill name**: {reloaded.metadata.name}
**Description**: {reloaded.metadata.description}
**Type**: {"System skill" if reloaded.metadata.system else "External skill"}

Changes are now in effect."""
            else:
                return "❌ Failed to reload skill"

        except Exception as e:
            logger.error(f"Failed to reload skill {skill_name}: {e}")
            return f"❌ Error while reloading skill: {e}"

    def _manage_skill_enabled(self, params: dict) -> str:
        """Batch enable/disable external skills"""
        from openakita.skills.allowlist_io import overwrite_allowlist, read_allowlist

        changes: list[dict] = params.get("changes", [])
        reason: str = params.get("reason", "")

        if not changes:
            return "❌ No skills specified for change"

        _, existing_allowlist = read_allowlist()

        # If the file does not yet exist: seed with all currently enabled external skills
        if existing_allowlist is None:
            all_skills = self.agent.skill_registry.list_all()
            existing_allowlist = {s.skill_id for s in all_skills if not s.system}
        else:
            existing_allowlist = set(existing_allowlist)

        # Collect all known external skill_ids (including ones pruned away and only in the loader cache)
        all_external_ids = set(existing_allowlist)
        loader = getattr(self.agent, "skill_loader", None)
        if loader:
            for sid, skill in loader._loaded_skills.items():
                if not getattr(skill.metadata, "system", False):
                    all_external_ids.add(sid)

        applied: list[str] = []
        skipped: list[str] = []
        any_disabled = False

        for change in changes:
            name = change.get("skill_name", "").strip()
            enabled = change.get("enabled", True)
            if not name:
                continue

            # Support either skill_id or display name
            skill = self.agent.skill_registry.get(name)
            sid = skill.skill_id if skill else name

            if skill and skill.system:
                skipped.append(f"{sid} (system skill, cannot be disabled)")
                continue

            if sid not in all_external_ids:
                skipped.append(f"{sid} (not found)")
                continue

            if enabled:
                existing_allowlist.add(sid)
            else:
                existing_allowlist.discard(sid)
                any_disabled = True
            applied.append(f"{sid} → {'enabled' if enabled else 'disabled'}")

        if not applied:
            msg = "No changes were applied."
            if skipped:
                msg += f"\nSkipped: {', '.join(skipped)}"
            return msg

        # Atomically write data/skills.json (single write site: allowlist_io.overwrite_allowlist)
        try:
            overwrite_allowlist(existing_allowlist)
        except Exception as e:
            logger.error("Failed to persist skills allowlist: %s", e)
            return f"❌ Failed to write data/skills.json: {e}"

        # Unified refresh entrypoint: rescan=False, only re-run the allowlist→catalog→pool chain
        action = SkillEvent.DISABLE if any_disabled else SkillEvent.ENABLE
        self.agent.propagate_skill_change(action, rescan=False)

        output = f"✅ Skill states updated ({len(applied)} changes)\n\n"
        if reason:
            output += f"**Reason**: {reason}\n\n"
        output += "**Change details**:\n"
        for item in applied:
            output += f"- {item}\n"
        if skipped:
            output += f"\n**Skipped**: {', '.join(skipped)}\n"

        return output

    async def _execute_skill(self, params: dict) -> str:
        """F10: Execute a skill in an isolated fork context"""
        import uuid

        skill_name = params["skill_name"]
        task = params["task"]
        max_turns = min(int(params.get("max_turns", 10)), 50)

        skill = self.agent.skill_registry.get(skill_name)
        if not skill or skill.disabled:
            available = [s.name for s in self.agent.skill_registry.list_all()[:10]]
            hint = f". Available skills: {', '.join(available)}" if available else ""
            return f"Skill '{skill_name}' not found or is disabled{hint}."

        body = skill.get_body() or ""
        if not body:
            return f"Skill '{skill_name}' has no executable content (SKILL.md body is empty)."

        # F4: argument substitution on body
        if "{{" in body:
            from openakita.config import settings as _cfg
            from openakita.skills.arguments import substitute

            body = substitute(body, project_root=_cfg.project_root)

        # F6: record usage
        usage_tracker = getattr(self.agent, "_skill_usage_tracker", None)
        if usage_tracker:
            usage_tracker.record(skill.skill_id)

        # F7: inject temporary tool allowlist
        if skill.allowed_tools:
            try:
                from openakita.core.policy import get_policy_engine

                get_policy_engine().add_skill_allowlist(skill.skill_id, skill.allowed_tools)
            except Exception as e:
                logger.warning(
                    "Failed to inject allowlist for fork skill %s: %s", skill.skill_id, e
                )

        # Build fork system prompt
        fork_system = (
            f"You are an execution assistant focused on the [{skill.name}] skill.\n"
            f"Please strictly follow the skill instructions below to complete the user's task.\n\n"
            f"---\n{body}\n---\n\n"
            f"Constraint: at most {max_turns} turns of operations."
        )

        fork_messages = [{"role": "user", "content": task}]
        fork_conv_id = f"fork_{skill.skill_id}_{uuid.uuid4().hex[:8]}"

        # F11: run before_execute hook
        hook_runner = None
        if skill.hooks:
            from openakita.skills.skill_hooks import create_hook_runner

            hook_runner = create_hook_runner(skill.skill_id, skill.skill_dir, skill.hooks)
            if hook_runner and hook_runner.has_hook("before_execute"):
                hook_result = hook_runner.run_hook("before_execute")
                if not hook_result["ok"]:
                    # Clean up allowlist before early return
                    self._cleanup_fork_allowlist(skill)
                    return f"Skill before_execute hook failed: {hook_result['output']}"

        # Determine tools: prefer skill's allowed_tools, fallback to agent's full toolset
        tools = self.agent._effective_tools
        if skill.allowed_tools:
            allowed_set = set(skill.allowed_tools)
            filtered = [t for t in tools if t.get("name") in allowed_set]
            if filtered:
                tools = filtered

        # F12: restrict tools for untrusted skills
        restricted = skill.get_restricted_tools()
        if restricted:
            tools = [t for t in tools if t.get("name") not in restricted]
            logger.info(
                "Fork execution of untrusted skill '%s' (trust=%s): restricted %d tools",
                skill.skill_id,
                skill.trust_level,
                len(restricted),
            )

        # Determine endpoint override from skill metadata
        endpoint_override = None
        if skill.model:
            endpoint_override = skill.model

        try:
            result = await self.agent.reasoning_engine.run(
                fork_messages,
                tools=tools,
                system_prompt=fork_system,
                base_system_prompt=fork_system,
                task_description=f"Fork execution: {skill.name} — {task[:200]}",
                session_type="cli",
                conversation_id=fork_conv_id,
                is_sub_agent=True,
                endpoint_override=endpoint_override,
            )
        except Exception as e:
            logger.error("Fork execution of skill '%s' failed: %s", skill_name, e, exc_info=True)
            result = f"Skill execution failed: {e}"
        finally:
            self._cleanup_fork_allowlist(skill)

        # F11: run after_execute hook
        if hook_runner and hook_runner.has_hook("after_execute"):
            try:
                hook_runner.run_hook("after_execute")
            except Exception as e:
                logger.warning("after_execute hook for '%s' failed: %s", skill.skill_id, e)

        return self._truncate_skill_content("execute_skill", result)

    @staticmethod
    def _cleanup_fork_allowlist(skill) -> None:
        """Clean up temporary tool allowlist injected for fork execution."""
        if skill.allowed_tools:
            try:
                from openakita.core.policy import get_policy_engine

                get_policy_engine().remove_skill_allowlist(skill.skill_id)
            except Exception:
                pass

    def _uninstall_skill(self, params: dict) -> str:
        """F14: Uninstall an external skill"""
        import shutil

        skill_name = params["skill_name"]

        # Resolve via registry first
        skill = self.agent.skill_registry.get(skill_name)

        from openakita.config import settings as _cfg

        if skill:
            if skill.system:
                return f"System skill '{skill.name}' cannot be uninstalled."
            skill_dir = skill.skill_dir
            display_name = skill.name
            skill_id = skill.skill_id
        else:
            # Fallback: try to find in skills/ directory
            skill_dir = _cfg.skills_path / skill_name
            if not skill_dir.exists():
                return f"Skill '{skill_name}' not found; cannot uninstall."
            display_name = skill_name
            skill_id = skill_name

        # Path safety check
        skills_root = _cfg.skills_path.resolve()
        try:
            skill_dir_resolved = skill_dir.resolve()
            skill_dir_resolved.relative_to(skills_root)
        except (ValueError, OSError):
            return "Security restriction: uninstalling skills outside the skills/ directory is not allowed."

        if not skill_dir_resolved.exists():
            return f"Skill directory does not exist: {display_name}"

        # Check for system skill marker in SKILL.md
        skill_md = skill_dir_resolved / "SKILL.md"
        if skill_md.exists():
            try:
                content = skill_md.read_text(encoding="utf-8", errors="replace")[:500]
                if "system: true" in content.lower():
                    return f"System skill '{display_name}' cannot be uninstalled."
            except Exception:
                pass

        # Perform deletion
        try:
            shutil.rmtree(str(skill_dir_resolved))
        except Exception as e:
            logger.error("Failed to uninstall skill '%s': %s", skill_name, e)
            return f"Uninstall failed: {e}"

        # Unregister from registry
        if self.agent.skill_registry.get(skill_id):
            self.agent.skill_registry.unregister(skill_id)

        # Remove from external_allowlist (if present); failure does not affect the main uninstall flow.
        try:
            from openakita.skills.allowlist_io import remove_skill_ids

            remove_skill_ids({skill_id})
        except Exception as e:
            logger.warning("Failed to update allowlist after uninstall of %s: %s", skill_id, e)

        # Clean up activation manager (must happen before propagate, since propagate rebuilds the activation table)
        activation = getattr(self.agent, "_skill_activation", None)
        if activation:
            activation.unregister(skill_id)

        # Clean up policy allowlists
        try:
            from openakita.core.policy import get_policy_engine

            get_policy_engine().remove_skill_allowlist(skill_id)
        except Exception:
            pass

        # Unified refresh entrypoint (catalog / tools / pool / event)
        self.agent.propagate_skill_change(SkillEvent.UNINSTALL, rescan=False)

        return f"✅ Skill '{display_name}' has been uninstalled.\n\nThe directory and all its files have been removed, and the skill has been deregistered from the system."


def create_handler(agent: "Agent"):
    """Create the skill management handler"""
    handler = SkillsHandler(agent)
    return handler.handle

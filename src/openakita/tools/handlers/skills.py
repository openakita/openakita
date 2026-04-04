"""
技能管理处理器

处理技能管理相关的系统技能（共 10 个工具）：
- list_skills: 列出技能
- get_skill_info: 获取技能信息
- run_skill_script: 运行技能脚本
- get_skill_reference: 获取参考文档
- install_skill: 安装技能
- load_skill: 加载新创建的技能
- reload_skill: 重新加载已修改的技能
- manage_skill_enabled: 启用/禁用技能
- execute_skill: 在隔离上下文中执行技能 (F10)
- uninstall_skill: 卸载外部技能 (F14)
"""

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.tool_executor import MAX_TOOL_RESULT_CHARS, OVERFLOW_MARKER, save_overflow
from ...skills.events import notify_skills_changed
from ...skills.exposure import build_skill_exposure

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

# Skill 内容专用阈值（~32000 tokens），高于通用的 MAX_TOOL_RESULT_CHARS (16000 chars)。
# Skill body 是高质量结构化指令，截断会严重影响 LLM 执行效果。
# 部分技能（如 docx）的 SKILL.md 引用了多个同目录子文件，内联后总量可达 50K+。
SKILL_MAX_CHARS = 64000


class SkillsHandler:
    """技能管理处理器"""

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
        """处理工具调用"""
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
            return f"❌ 缺少必需参数: {e}"
        except Exception as e:
            logger.error("Unexpected error in skills handler %s: %s", tool_name, e, exc_info=True)
            return f"❌ 技能操作失败: {e}"

    def _list_skills(self, params: dict) -> str:
        """列出所有技能，区分启用/禁用状态"""
        all_skills = self.agent.skill_registry.list_all(include_disabled=True)
        if not all_skills:
            return (
                "当前没有已安装的技能\n\n"
                "提示: 技能可能来自内置目录、用户工作区目录或项目目录。"
                "每个技能都应包含 SKILL.md；需要准确路径时请使用 get_skill_info。"
            )

        system_skills = [s for s in all_skills if s.system]
        enabled_external = [s for s in all_skills if not s.system and not s.disabled]
        disabled_external = [s for s in all_skills if not s.system and s.disabled]

        enabled_total = len(system_skills) + len(enabled_external)
        output = (
            f"已安装 {len(all_skills)} 个技能 "
            f"({enabled_total} 启用, {len(disabled_external)} 禁用):\n\n"
        )

        if system_skills:
            output += f"**系统技能 ({len(system_skills)})** [全部启用]:\n"
            for skill in system_skills:
                exposed = build_skill_exposure(skill)
                auto = "自动" if not skill.disable_model_invocation else "手动"
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
            output += f"**已启用外部技能 ({len(enabled_external)})**:\n"
            for skill in enabled_external:
                exposed = build_skill_exposure(skill)
                auto = "自动" if not skill.disable_model_invocation else "手动"
                zh_name = skill.name_i18n.get("zh", "")
                name_part = f"{skill.name} ({zh_name})" if zh_name else skill.name
                output += f"- {name_part} [{auto}]\n"
                output += f"  {skill.description}\n"
                output += (
                    f"  source={exposed.origin_label}"
                    + (f", path={exposed.skill_dir}" if exposed.skill_dir else "")
                    + "\n\n"
                )

        if disabled_external:
            output += (
                f"**已禁用外部技能 ({len(disabled_external)})** [需在技能面板启用后才可使用]:\n"
            )
            for skill in disabled_external:
                exposed = build_skill_exposure(skill)
                zh_name = skill.name_i18n.get("zh", "")
                name_part = f"{skill.name} ({zh_name})" if zh_name else skill.name
                output += f"- {name_part} [已禁用]\n"
                output += f"  {skill.description}\n"
                output += (
                    f"  source={exposed.origin_label}"
                    + (f", path={exposed.skill_dir}" if exposed.skill_dir else "")
                    + "\n\n"
                )

        return self._truncate_skill_content("list_skills", output)

    # Markdown 链接中引用同目录 .md 文件的正则：
    #   [`filename.md`](filename.md)  或  [filename.md](filename.md)
    _MD_LINK_RE = re.compile(r"\[`?([a-zA-Z0-9_-]+\.md)`?\]\(([a-zA-Z0-9_-]+\.md)\)")

    @staticmethod
    def _inline_referenced_files(body: str, skill_dir: Path) -> str:
        """解析 body 中引用的同目录 .md 文件并追加到末尾。

        许多 Anthropic 技能（docx, pptx 等）在 SKILL.md 中用 Markdown 链接
        引用同目录下的参考文件（如 docx-js.md, ooxml.md），并标注
        "MANDATORY - READ ENTIRE FILE"。此方法自动将这些文件内联，
        使 get_skill_info 一次返回完整的技能知识。
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
        """Skill 专用截断：阈值高于通用守卫，超长时自行截断并带标记跳过守卫。

        - <= MAX_TOOL_RESULT_CHARS (16000)：原样返回，通用守卫也不会截断
        - 16000 < len <= SKILL_MAX_CHARS (64000)：全量返回 + OVERFLOW_MARKER 跳过守卫
        - > SKILL_MAX_CHARS：截断到 64000 + 溢出文件 + 分段读取指引
        """
        if not content or len(content) <= MAX_TOOL_RESULT_CHARS:
            return content

        if len(content) <= SKILL_MAX_CHARS:
            return content + f"\n\n{OVERFLOW_MARKER}"

        total_chars = len(content)
        overflow_path = save_overflow(tool_name, content)
        truncated = content[:SKILL_MAX_CHARS]
        hint = (
            f"\n\n{OVERFLOW_MARKER} 技能内容共 {total_chars} 字符，"
            f"已截断到前 {SKILL_MAX_CHARS} 字符。\n"
            f"完整内容已保存，使用以下命令查看后续内容:\n"
            f'read_file(path="{overflow_path}", offset=1, limit=500)'
        )
        logger.info(
            f"[SkillTruncate] {tool_name} output: {total_chars} → {SKILL_MAX_CHARS} chars, "
            f"overflow saved to {overflow_path}"
        )
        return truncated + hint

    def _get_skill_info(self, params: dict) -> str:
        """获取技能详细信息（自动内联引用的子文件）"""
        skill_name = params["skill_name"]
        user_args = params.get("args", {})
        skill = self.agent.skill_registry.get(skill_name)

        if not skill or skill.disabled:
            available = [s.name for s in self.agent.skill_registry.list_all()[:10]]
            hint = f"，当前可用技能: {', '.join(available)}" if available else ""
            return (
                f"未找到技能 '{skill_name}'{hint}。"
                f"请检查技能名称是否正确，或使用 list_skills 查看所有可用技能。"
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
        body = skill.get_body() or "(无详细指令)"

        # F4: argument substitution
        if "{{" in body:
            from openakita.skills.arguments import substitute
            from openakita.config import settings as _cfg
            extra = {}
            if isinstance(user_args, dict):
                extra = {k: str(v) for k, v in user_args.items()}
            body = substitute(body, extra, project_root=_cfg.project_root)

        # 自动内联 SKILL.md body 中引用的同目录 .md 文件
        if exposed.skill_path:
            skill_dir = Path(exposed.skill_path).parent
            body = self._inline_referenced_files(body, skill_dir)

        output = f"# 技能: {skill.name}\n\n"
        output += f"**ID**: {skill.skill_id}\n"
        output += f"**描述**: {skill.description}\n"
        if skill.when_to_use:
            output += f"**适用场景**: {skill.when_to_use}\n"
        output += f"**来源**: {exposed.origin_label}\n"
        if exposed.skill_dir:
            output += f"**路径**: {exposed.skill_dir}\n"
        if exposed.root_dir:
            output += f"**根目录**: {exposed.root_dir}\n"
        if skill.system:
            output += "**类型**: 系统技能\n"
            output += f"**工具名**: {skill.tool_name}\n"
            output += f"**处理器**: {skill.handler}\n"
        else:
            output += "**类型**: 外部技能\n"
        if exposed.instruction_only:
            output += "**脚本**: instruction-only (no executable scripts)\n"
        else:
            output += f"**可执行脚本**: {', '.join(exposed.scripts)}\n"
        if exposed.references:
            output += f"**参考文档**: {', '.join(exposed.references)}\n"
        output += (
            "**路径规则**: 技能可能来自多个目录，不要根据 workspace map 猜测 skill 文件位置；"
            "以上面的来源和路径为准。\n"
        )
        if skill.license:
            output += f"**许可证**: {skill.license}\n"
        if skill.compatibility:
            output += f"**兼容性**: {skill.compatibility}\n"
        if skill.model:
            output += f"**推荐模型**: {skill.model}\n"
        if skill.execution_context and skill.execution_context != "inline":
            output += f"**执行模式**: {skill.execution_context}\n"

        # F4: display argument schema
        if skill.arguments:
            from openakita.skills.arguments import format_argument_schema
            args_block = format_argument_schema(skill.arguments)
            if args_block:
                output += f"\n{args_block}\n"

        output += "\n---\n\n"
        output += body

        return self._truncate_skill_content("get_skill_info", output)

    def _run_skill_script(self, params: dict) -> str:
        """运行技能脚本"""
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
            skill_dir = Path(skill_entry.skill_path).resolve() if skill_entry and skill_entry.skill_path else None

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
                return (
                    f"❌ 工作目录被拒绝: {cwd_raw}\n"
                    f"cwd 只能位于项目工作区或技能目录内。"
                )

        success, output = self.agent.skill_loader.run_script(
            skill_name, script_name, args, cwd=resolved_cwd
        )

        if success:
            return f"✅ 脚本执行成功:\n{output}"
        else:
            output_lower = output.lower()

            if "no executable scripts" in output_lower or "instruction-only" in output_lower:
                return (
                    f"❌ 脚本执行失败:\n{output}\n\n"
                    f"**This skill is instruction-only (no scripts).** "
                    f"DO NOT retry run_skill_script.\n"
                    f'Use `get_skill_info("{skill_name}")` to read instructions, '
                    f"then write Python code via `write_file` and execute via `run_shell`."
                )
            elif "not found" in output_lower and "available scripts:" in output_lower:
                return (
                    f"❌ 脚本执行失败:\n{output}\n\n"
                    f"**建议**: Use one of the available scripts listed above."
                )
            elif "not found" in output_lower or "未找到" in output_lower:
                return (
                    f"❌ 脚本执行失败:\n{output}\n\n"
                    f'**建议**: 如果不确定用法，使用 `get_skill_info("{skill_name}")` 查看技能完整指令。\n'
                    f"对于指令型技能，应改用 write_file + run_shell 方式执行代码。"
                )
            elif "timed out" in output_lower or "超时" in output:
                return (
                    f"❌ 脚本执行失败:\n{output}\n\n"
                    f"**建议**: 脚本执行超时。可以尝试:\n"
                    f"1. 检查脚本是否有死循环或长时间阻塞操作\n"
                    f"2. 使用 `get_skill_info` 查看技能详情确认用法\n"
                    f"3. 尝试使用其他方法完成任务"
                )
            elif "permission" in output_lower or "权限" in output:
                return (
                    f"❌ 脚本执行失败:\n{output}\n\n"
                    f"**建议**: 权限不足。可以尝试:\n"
                    f"1. 检查文件/目录权限\n"
                    f"2. 使用管理员权限运行"
                )
            else:
                return (
                    f"❌ 脚本执行失败:\n{output}\n\n"
                    f"**建议**: 请检查脚本参数是否正确，或使用 `get_skill_info` 查看技能使用说明"
                )

    def _get_skill_reference(self, params: dict) -> str:
        """获取技能参考文档"""
        skill_name = params["skill_name"]
        ref_name = params.get("ref_name", "REFERENCE.md")

        content = self.agent.skill_loader.get_reference(skill_name, ref_name)

        if content:
            output = f"# 参考文档: {ref_name}\n\n{content}"
            return self._truncate_skill_content("get_skill_reference", output)
        else:
            return f"❌ 未找到参考文档: {skill_name}/{ref_name}"

    async def _install_skill(self, params: dict) -> str:
        """安装技能"""
        source = params["source"]
        name = params.get("name")
        subdir = params.get("subdir")
        extra_files = params.get("extra_files", [])

        result = await self.agent.skill_manager.install_skill(source, name, subdir, extra_files)
        notify_skills_changed("install")
        return result

    def _load_skill(self, params: dict) -> str:
        """加载新创建的技能"""
        skill_name = params["skill_name"]

        # 查找技能目录（使用项目根目录，避免依赖 CWD）
        try:
            from openakita.config import settings

            skills_dir = settings.project_root / "skills"
        except Exception:
            skills_dir = Path("skills")
        skill_dir = skills_dir / skill_name

        if not skill_dir.exists():
            return f"❌ 技能目录不存在: {skill_dir}\n\n请确保技能已保存到 skills/{skill_name}/ 目录"

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return f"❌ 技能定义文件不存在: {skill_md}\n\n请确保目录中包含 SKILL.md 文件"

        # 检查是否已加载
        existing = self.agent.skill_registry.get(skill_name)
        if existing:
            return f"⚠️ 技能 '{skill_name}' 已存在。如需更新，请使用 reload_skill"

        try:
            # 加载技能
            loaded = self.agent.skill_loader.load_skill(skill_dir)

            if loaded:
                # 刷新技能目录缓存 + handler 映射
                self.agent._skill_catalog_text = self.agent.skill_catalog.generate_catalog()
                self.agent._update_skill_tools()
                self.agent.notify_pools_skills_changed()
                notify_skills_changed("load")

                logger.info(f"Skill loaded: {skill_name}")

                return f"""✅ 技能加载成功！

**技能名称**: {loaded.metadata.name}
**描述**: {loaded.metadata.description}
**类型**: {"系统技能" if loaded.metadata.system else "外部技能"}
**路径**: {skill_dir}

技能已可用，可以通过 `get_skill_info("{skill_name}")` 查看详情。"""
            else:
                return "❌ 技能加载失败，请检查 SKILL.md 格式是否正确"

        except Exception as e:
            logger.error(f"Failed to load skill {skill_name}: {e}")
            return f"❌ 加载技能时出错: {e}"

    def _reload_skill(self, params: dict) -> str:
        """重新加载已存在的技能"""
        skill_name = params["skill_name"]

        # 检查技能是否已加载
        existing = self.agent.skill_loader.get_skill(skill_name)
        if not existing:
            return f"❌ 技能 '{skill_name}' 未加载。如需加载新技能，请使用 load_skill"

        try:
            # 重新加载
            reloaded = self.agent.skill_loader.reload_skill(skill_name)

            if reloaded:
                # 刷新技能目录缓存 + handler 映射
                self.agent._skill_catalog_text = self.agent.skill_catalog.generate_catalog()
                self.agent._update_skill_tools()
                self.agent.notify_pools_skills_changed()
                notify_skills_changed("reload")

                logger.info(f"Skill reloaded: {skill_name}")

                return f"""✅ 技能重新加载成功！

**技能名称**: {reloaded.metadata.name}
**描述**: {reloaded.metadata.description}
**类型**: {"系统技能" if reloaded.metadata.system else "外部技能"}

修改已生效。"""
            else:
                return "❌ 技能重新加载失败"

        except Exception as e:
            logger.error(f"Failed to reload skill {skill_name}: {e}")
            return f"❌ 重新加载技能时出错: {e}"

    def _manage_skill_enabled(self, params: dict) -> str:
        """批量启用/禁用外部技能"""
        import json

        changes: list[dict] = params.get("changes", [])
        reason: str = params.get("reason", "")

        if not changes:
            return "❌ 未指定要变更的技能"

        try:
            from openakita.config import settings

            cfg_path = settings.project_root / "data" / "skills.json"
        except Exception:
            cfg_path = Path.cwd() / "data" / "skills.json"

        # 读取现有 allowlist
        existing_allowlist: set[str] | None = None
        try:
            if cfg_path.exists():
                raw = cfg_path.read_text(encoding="utf-8")
                cfg = json.loads(raw) if raw.strip() else {}
                al = cfg.get("external_allowlist", None)
                if isinstance(al, list):
                    existing_allowlist = {str(x).strip() for x in al if str(x).strip()}
        except Exception:
            pass

        # 如果没有 allowlist 文件，初始化为当前所有外部技能的 skill_id
        if existing_allowlist is None:
            all_skills = self.agent.skill_registry.list_all()
            existing_allowlist = {s.skill_id for s in all_skills if not s.system}

        # 收集所有已知外部技能 skill_id（包括被 prune 的）
        all_external_ids = set(existing_allowlist)
        loader = getattr(self.agent, "skill_loader", None)
        if loader:
            for sid, skill in loader._loaded_skills.items():
                if not getattr(skill.metadata, "system", False):
                    all_external_ids.add(sid)

        applied: list[str] = []
        skipped: list[str] = []

        for change in changes:
            name = change.get("skill_name", "").strip()
            enabled = change.get("enabled", True)
            if not name:
                continue

            # Resolve to skill_id (accept both skill_id and display name)
            skill = self.agent.skill_registry.get(name)
            sid = skill.skill_id if skill else name

            if skill and skill.system:
                skipped.append(f"{sid}（系统技能，不可禁用）")
                continue

            if sid not in all_external_ids:
                skipped.append(f"{sid}（未找到）")
                continue

            if enabled:
                existing_allowlist.add(sid)
            else:
                existing_allowlist.discard(sid)
            applied.append(f"{sid} → {'启用' if enabled else '禁用'}")

        if not applied:
            msg = "未执行任何变更。"
            if skipped:
                msg += f"\n跳过: {', '.join(skipped)}"
            return msg

        # 写入 data/skills.json
        content = {
            "version": 1,
            "external_allowlist": sorted(existing_allowlist),
            "updated_at": __import__("datetime").datetime.now().isoformat(),
        }
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            json.dumps(content, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # 热重载
        try:
            from openakita.core.agent import _collect_preset_referenced_skills

            effective = (
                loader.compute_effective_allowlist(existing_allowlist)
                if loader
                else existing_allowlist
            )
            agent_skills = _collect_preset_referenced_skills()
            if loader:
                loader.prune_external_by_allowlist(effective, agent_referenced_skills=agent_skills)
            catalog = getattr(self.agent, "skill_catalog", None)
            if catalog:
                catalog.invalidate_cache()
                self.agent._skill_catalog_text = catalog.generate_catalog()
            self.agent._update_skill_tools()
            self.agent.notify_pools_skills_changed()
        except Exception as e:
            logger.warning(f"Post-manage reload failed: {e}")

        notify_skills_changed("enable")

        output = f"✅ 技能状态已更新（{len(applied)} 项变更）\n\n"
        if reason:
            output += f"**原因**: {reason}\n\n"
        output += "**变更详情**:\n"
        for item in applied:
            output += f"- {item}\n"
        if skipped:
            output += f"\n**跳过**: {', '.join(skipped)}\n"

        return output


    async def _execute_skill(self, params: dict) -> str:
        """F10: 在隔离的 fork 上下文中执行技能"""
        import uuid

        skill_name = params["skill_name"]
        task = params["task"]
        max_turns = min(int(params.get("max_turns", 10)), 50)

        skill = self.agent.skill_registry.get(skill_name)
        if not skill or skill.disabled:
            available = [s.name for s in self.agent.skill_registry.list_all()[:10]]
            hint = f"，可用技能: {', '.join(available)}" if available else ""
            return f"未找到或已禁用技能 '{skill_name}'{hint}。"

        body = skill.get_body() or ""
        if not body:
            return f"技能 '{skill_name}' 无可执行内容（SKILL.md body 为空）。"

        # F4: argument substitution on body
        if "{{" in body:
            from openakita.skills.arguments import substitute
            from openakita.config import settings as _cfg
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
                logger.warning("Failed to inject allowlist for fork skill %s: %s", skill.skill_id, e)

        # Build fork system prompt
        fork_system = (
            f"你是一个专注于 [{skill.name}] 技能的执行助手。\n"
            f"请严格按照以下技能指令完成用户任务。\n\n"
            f"---\n{body}\n---\n\n"
            f"限制：最多执行 {max_turns} 轮操作。"
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
                    return f"技能 before_execute 钩子失败: {hook_result['output']}"

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
                skill.skill_id, skill.trust_level, len(restricted),
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
            result = f"技能执行失败: {e}"
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
        """F14: 卸载外部技能"""
        import shutil

        skill_name = params["skill_name"]

        # Resolve via registry first
        skill = self.agent.skill_registry.get(skill_name)

        from openakita.config import settings as _cfg

        if skill:
            if skill.system:
                return f"系统技能 '{skill.name}' 不可卸载。"
            skill_dir = skill.skill_dir
            display_name = skill.name
            skill_id = skill.skill_id
        else:
            # Fallback: try to find in skills/ directory
            skill_dir = _cfg.skills_path / skill_name
            if not skill_dir.exists():
                return f"未找到技能 '{skill_name}'，无法卸载。"
            display_name = skill_name
            skill_id = skill_name

        # Path safety check
        skills_root = _cfg.skills_path.resolve()
        try:
            skill_dir_resolved = skill_dir.resolve()
            skill_dir_resolved.relative_to(skills_root)
        except (ValueError, OSError):
            return f"安全限制：不允许卸载 skills/ 目录之外的技能。"

        if not skill_dir_resolved.exists():
            return f"技能目录不存在: {display_name}"

        # Check for system skill marker in SKILL.md
        skill_md = skill_dir_resolved / "SKILL.md"
        if skill_md.exists():
            try:
                content = skill_md.read_text(encoding="utf-8", errors="replace")[:500]
                if "system: true" in content.lower():
                    return f"系统技能 '{display_name}' 不可卸载。"
            except Exception:
                pass

        # Perform deletion
        try:
            shutil.rmtree(str(skill_dir_resolved))
        except Exception as e:
            logger.error("Failed to uninstall skill '%s': %s", skill_name, e)
            return f"卸载失败: {e}"

        # Unregister from registry
        if self.agent.skill_registry.get(skill_id):
            self.agent.skill_registry.unregister(skill_id)

        # Refresh catalog and tools
        self.agent.skill_catalog.invalidate_cache()
        self.agent._update_skill_tools()
        notify_skills_changed("uninstall")

        # Clean up activation manager
        activation = getattr(self.agent, "_skill_activation", None)
        if activation:
            activation.unregister(skill_id)

        # Clean up policy allowlists
        try:
            from openakita.core.policy import get_policy_engine
            get_policy_engine().remove_skill_allowlist(skill_id)
        except Exception:
            pass

        return (
            f"✅ 技能 '{display_name}' 已卸载。\n\n"
            f"已删除目录及所有文件，并从系统中注销。"
        )


def create_handler(agent: "Agent"):
    """创建技能管理处理器"""
    handler = SkillsHandler(agent)
    return handler.handle

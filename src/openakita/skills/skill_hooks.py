"""
技能级 Hook 系统

支持 SKILL.md frontmatter 中声明的生命周期钩子：
- on_activate:   技能被激活时（首次 get_skill_info 或 execute_skill）
- on_deactivate: 技能被卸载/禁用时
- before_execute: fork 执行前
- after_execute:  fork 执行后

钩子值为技能目录下的脚本路径，由 SkillHookRunner 安全执行。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VALID_HOOK_NAMES = frozenset({
    "on_activate",
    "on_deactivate",
    "before_execute",
    "after_execute",
})

_HOOK_TIMEOUT = 15.0
_MAX_OUTPUT_BYTES = 32_768


def validate_hooks(hooks: dict, skill_dir: Path | None = None) -> list[str]:
    """Validate a hooks dict from SKILL.md frontmatter.

    Returns a list of warning messages (empty if all valid).
    """
    warnings: list[str] = []
    if not isinstance(hooks, dict):
        warnings.append(f"hooks should be a dict, got {type(hooks).__name__}")
        return warnings

    for name, script in hooks.items():
        if name not in VALID_HOOK_NAMES:
            warnings.append(
                f"Unknown hook '{name}', valid hooks: {sorted(VALID_HOOK_NAMES)}"
            )
        if not isinstance(script, str) or not script.strip():
            warnings.append(f"Hook '{name}' script must be a non-empty string")
            continue

        if skill_dir:
            script_path = (skill_dir / script).resolve()
            try:
                script_path.relative_to(skill_dir.resolve())
            except ValueError:
                warnings.append(
                    f"Hook '{name}' script '{script}' escapes skill directory"
                )

    return warnings


class SkillHookRunner:
    """Execute skill-declared hooks safely."""

    def __init__(self, skill_id: str, skill_dir: Path, hooks: dict[str, str]):
        self._skill_id = skill_id
        self._skill_dir = skill_dir.resolve()
        self._hooks = hooks

    def has_hook(self, hook_name: str) -> bool:
        return hook_name in self._hooks

    def run_hook(
        self,
        hook_name: str,
        *,
        env_extra: dict[str, str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a single hook script synchronously.

        Returns:
            {"ok": bool, "output": str, "exit_code": int | None}
        """
        script_rel = self._hooks.get(hook_name)
        if not script_rel:
            return {"ok": True, "output": "", "exit_code": None}

        script_path = (self._skill_dir / script_rel).resolve()

        # Path safety: must stay within skill directory
        try:
            script_path.relative_to(self._skill_dir)
        except ValueError:
            msg = f"Hook script escapes skill directory: {script_rel}"
            logger.warning("[SkillHook] %s (%s)", msg, self._skill_id)
            return {"ok": False, "output": msg, "exit_code": None}

        if not script_path.exists():
            msg = f"Hook script not found: {script_rel}"
            logger.warning("[SkillHook] %s (%s)", msg, self._skill_id)
            return {"ok": False, "output": msg, "exit_code": None}

        import os as _os
        env = dict(_os.environ)
        env["OPENAKITA_SKILL_ID"] = self._skill_id
        env["OPENAKITA_HOOK_NAME"] = hook_name
        if env_extra:
            env.update(env_extra)

        suffix = script_path.suffix.lower()
        if suffix == ".py":
            cmd = [sys.executable, str(script_path)]
        elif suffix in (".sh", ".bash"):
            cmd = ["bash", str(script_path)]
        elif suffix in (".bat", ".cmd"):
            cmd = ["cmd", "/c", str(script_path)]
        elif suffix == ".ps1":
            cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
        else:
            cmd = [str(script_path)]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(self._skill_dir),
                env=env,
            )
            try:
                stdout, _ = proc.communicate(timeout=_HOOK_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                return {
                    "ok": False,
                    "output": f"Hook '{hook_name}' timed out ({_HOOK_TIMEOUT}s)",
                    "exit_code": -1,
                }
            except Exception:
                proc.kill()
                proc.wait(timeout=5)
                raise

            output = stdout[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            exit_code = proc.returncode

            if exit_code != 0:
                logger.warning(
                    "[SkillHook] %s/%s exited with code %d",
                    self._skill_id, hook_name, exit_code,
                )

            return {"ok": exit_code == 0, "output": output, "exit_code": exit_code}

        except FileNotFoundError:
            msg = f"Hook script interpreter not found for: {script_rel}"
            logger.warning("[SkillHook] %s (%s)", msg, self._skill_id)
            return {"ok": False, "output": msg, "exit_code": None}
        except Exception as e:
            logger.error(
                "[SkillHook] Failed to run %s/%s: %s",
                self._skill_id, hook_name, e,
            )
            return {"ok": False, "output": str(e), "exit_code": None}

    @property
    def hook_names(self) -> list[str]:
        return list(self._hooks.keys())


def create_hook_runner(skill_id: str, skill_dir: Path, hooks: dict) -> SkillHookRunner | None:
    """Create a SkillHookRunner if the skill declares valid hooks."""
    if not hooks or not isinstance(hooks, dict):
        return None

    valid_hooks = {
        k: v for k, v in hooks.items()
        if k in VALID_HOOK_NAMES and isinstance(v, str) and v.strip()
    }

    if not valid_hooks:
        return None

    return SkillHookRunner(skill_id, skill_dir, valid_hooks)

"""User allowlist manager (C8b-1)。

替代 v1 ``PolicyEngine._check_persistent_allowlist`` /
``PolicyEngine._save_user_allowlist`` /
``PolicyEngine.remove_allowlist_entry`` /
``PolicyEngine.get_user_allowlist`` 等持久化白名单 CRUD。

设计要点
========

1. **Engine-scoped state**：与 ``PolicyEngineV2`` 一对一绑定（不是 module 单例）。
   原因：每个引擎实例携带自己的 ``PolicyConfigV2``；持久化数据源是 config 字段，
   manager 只是对该字段的 view + IO 包装。dry-run preview 引擎需要独立 manager
   实例（不污染全局）。

2. **YAML 写回**：``save_to_yaml(path)`` 跟 v1 ``_save_user_allowlist`` 行为一致——
   读全量 → mutate ``security.user_allowlist`` 子段 → 写回。失败仅 warn，不抛
   （与 v1 silent-fail 对齐，避免单次保存失败让整个 confirm 流程崩）。

3. **匹配语义**：
   - shell 命令：先 raw command fnmatch，再 ``_command_to_pattern`` 提取语义命令
     再 fnmatch；二者任一命中即返回 entry（与 v1 ``_check_persistent_allowlist``
     完全一致）。
   - 其他工具：tool name 完全匹配。

4. **CRUD 不动 mutex**：因为 ``PolicyConfigV2`` 是 Pydantic immutable 风格 dataclass，
   但 ``commands`` / ``tools`` 字段都是 ``list[dict]``，``_config.user_allowlist``
   实例本身可变。append / pop 在单线程 chat handler 已够；UI 多 tab 并发改本来就
   要前端去重，policy 层不强加锁。
"""

from __future__ import annotations

import fnmatch
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .schema import PolicyConfigV2, UserAllowlistConfig

logger = logging.getLogger(__name__)

_SHELL_TOOLS = frozenset({"run_shell", "run_powershell"})

# v1 ``PolicyEngine._command_to_pattern`` 的 executor 列表（命令归一化用）。
_EXECUTOR_NAMES = frozenset(
    {"python", "python3", "python3.11", "python3.12", "python3.13", "node", "ruby", "perl"}
)


def command_to_pattern(command: str) -> str:
    """Extract a glob-matchable pattern from a shell command string.

    精确复刻 v1 ``PolicyEngine._command_to_pattern``——已经被 v1 测试覆盖，
    C8b-1 阶段保持完全行为一致以避免静默回归（已加 parity 单测）。
    """
    parts = command.strip().split()
    if not parts:
        return command

    base = parts[0].strip('"').strip("'")
    sep = "/" if "/" in base else "\\"
    exe_name = base.rsplit(sep, 1)[-1].lower() if sep in base else base.lower()
    if exe_name.endswith(".exe"):
        exe_name = exe_name[:-4]

    if exe_name in _EXECUTOR_NAMES and len(parts) >= 3 and parts[1] == "-m":
        if len(parts) >= 4:
            return f"{parts[2]} {parts[3]}*"
        return f"{parts[2]}*"

    if len(parts) >= 2:
        return f"{parts[0]} {parts[1]}*"
    return f"{parts[0]}*"


class UserAllowlistManager:
    """持久化用户白名单的 v2 manager。

    生命周期：与 ``PolicyEngineV2`` 一对一；engine 构造时实例化并持有。
    持久化源：``config.user_allowlist`` 字段（mutable list[dict]）。

    线程安全：CRUD 走单线程 chat handler；并发场景由调用方协调（如 UI 多 tab
    应在前端做乐观锁）。manager 不强加锁是为了让 PolicyContext 可以零拷贝
    传递。
    """

    def __init__(self, config: PolicyConfigV2) -> None:
        self._config = config

    # ----- 查询 ------------------------------------------------------------

    @property
    def commands(self) -> list[dict[str, Any]]:
        """直接暴露内部 list 引用（与 ``_config.user_allowlist.commands`` 同源）。"""
        return self._config.user_allowlist.commands

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self._config.user_allowlist.tools

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """API/UI 用：返回当前白名单的浅拷贝。

        替代 v1 ``PolicyEngine.get_user_allowlist``。返回 list 是 shallow copy
        防止外部 caller 不慎 mutate；entry dict 仍是同一引用（ok，因为它们是
        immutable 视角）。
        """
        return {"commands": list(self.commands), "tools": list(self.tools)}

    def match(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """检查工具调用是否命中持久化白名单。

        语义与 v1 ``_check_persistent_allowlist`` 完全一致：
        - shell 命令：raw command fnmatch + 语义命令 fnmatch（任一命中）
        - 其他工具：tool name 完全匹配

        返回命中的 entry dict（含 ``needs_sandbox`` 等），无命中返回 None。
        """
        command = str(params.get("command", "") or "")
        if tool_name in _SHELL_TOOLS and command:
            semantic_pattern = command_to_pattern(command)
            semantic_cmd = semantic_pattern.rstrip("*").rstrip()
            for entry in self.commands:
                pattern = entry.get("pattern", "")
                if not pattern:
                    continue
                if fnmatch.fnmatch(command, pattern):
                    return entry
                if semantic_cmd and fnmatch.fnmatch(semantic_cmd, pattern):
                    return entry
            return None

        for entry in self.tools:
            if entry.get("name") == tool_name:
                return entry
        return None

    # ----- CRUD（mutate config in place）-----------------------------------

    def add_entry(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        needs_sandbox: bool = False,
    ) -> dict[str, Any]:
        """新增 allowlist 条目；返回新加的 entry dict。

        替代 v1 ``PolicyEngine._persist_allowlist_entry`` —— 但**不自动写盘**。
        调用方负责在 add_entry 后调用 ``save_to_yaml(path)``，或批量改完一次性 save。
        分离 mutate 和 IO 是 C8b 的设计选择（v1 是耦合的）：
        - 测试场景可零 IO 跑 add_entry
        - dry-run 场景可只 add 不 save
        - UI 多次改可批量 save
        """
        now_str = datetime.now(UTC).isoformat()
        command = str(params.get("command", "") or "")

        if tool_name in _SHELL_TOOLS and command:
            entry = {
                "pattern": command_to_pattern(command),
                "added_at": now_str,
                "needs_sandbox": needs_sandbox,
            }
            self.commands.append(entry)
        else:
            entry = {
                "name": tool_name,
                "zone": "workspace",
                "added_at": now_str,
                "needs_sandbox": needs_sandbox,
            }
            self.tools.append(entry)
        return entry

    def add_raw_entry(self, entry_type: str, entry: dict[str, Any]) -> dict[str, Any]:
        """直接 append 一条原始 entry（API/UI 走这条；自带字段不再加工）。

        替代 v1 ``security_actions.add_security_allowlist_entry`` 路径——
        UI 已经把字段填好（pattern / name / needs_sandbox 等），manager 不二次
        加工以避免 added_at 之类字段被 silently 覆盖。
        """
        normalized = "tool" if entry_type == "tool" else "command"
        item = dict(entry)
        if normalized == "command":
            self.commands.append(item)
        else:
            self.tools.append(item)
        return item

    def remove_entry(self, entry_type: str, index: int) -> bool:
        """按 (type, index) 删除条目；与 v1 ``remove_allowlist_entry`` 对齐。"""
        target = self.commands if entry_type == "command" else self.tools
        if 0 <= index < len(target):
            target.pop(index)
            return True
        return False

    # ----- 持久化 ----------------------------------------------------------

    def save_to_yaml(self, yaml_path: Path | str | None = None) -> bool:
        """写回 YAML；失败仅 warn 不抛（与 v1 silent-fail 对齐）。

        ``yaml_path`` 不传时用 ``settings.identity_path / POLICIES.yaml``
        （生产路径）。文件不存在时**直接 return False**——v1 同样行为，
        理由：第一次保存若 YAML 还没初始化，写入是 unsafe（容易 race 导致
        文件被覆盖成只含 user_allowlist 的残缺配置）。
        """
        try:
            import yaml

            path: Path
            if yaml_path is not None:
                path = Path(yaml_path)
            else:
                from ...config import settings

                path = Path(settings.identity_path) / "POLICIES.yaml"

            if not path.exists():
                logger.debug(
                    "[PolicyV2 UserAllowlist] YAML not found, skip save: %s", path
                )
                return False

            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            sec = data.setdefault("security", {})
            sec["user_allowlist"] = {
                "commands": list(self.commands),
                "tools": list(self.tools),
            }

            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            return True
        except Exception as exc:
            logger.warning(
                "[PolicyV2 UserAllowlist] Failed to save user_allowlist: %s", exc
            )
            return False

    def replace_config(self, ua: UserAllowlistConfig) -> None:
        """Hot-swap 整个 user_allowlist 子配置（hot-reload 用）。

        典型用法：UI 保存后 ``reset_engine_v2`` 触发 lazy-load，新配置整套替换。
        manager 通过持有的 ``self._config`` 自动看到新数据；这个方法仅在不重建
        engine 的轻量场景使用（C18 hot-reload 才有意义）。
        """
        self._config.user_allowlist = ua


__all__ = [
    "UserAllowlistManager",
    "command_to_pattern",
]

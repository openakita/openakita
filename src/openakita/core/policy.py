"""
集中策略引擎 (Agent Harness: Policy Engine)

六层安全防护体系的核心决策层：
- L1: 四区(workspace/controlled/protected/forbidden) + 操作类型矩阵
- L3: 三平台(Windows/macOS/Linux)危险命令模式匹配与风险分级

策略类型:
- ZonePolicy: 路径区域 × 操作类型矩阵判定
- RiskPolicy: Shell 命令风险分级 (CRITICAL/HIGH/MEDIUM/LOW)
- ToolPolicy: 工具级策略（允许/禁止、参数限制、需要确认）
- ScopePolicy: 范围策略（Shell 命令黑名单，兼容旧配置）
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PolicyDecision(StrEnum):
    """策略判定结果"""

    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


class Zone(StrEnum):
    """安全区域"""

    WORKSPACE = "workspace"
    CONTROLLED = "controlled"
    PROTECTED = "protected"
    FORBIDDEN = "forbidden"


class OpType(StrEnum):
    """操作类型"""

    READ = "read"
    CREATE = "create"
    EDIT = "edit"
    OVERWRITE = "overwrite"
    DELETE = "delete"
    RECURSIVE_DELETE = "recursive_delete"


class RiskLevel(StrEnum):
    """Shell 命令风险等级"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# Zone × OpType permission matrix
# ---------------------------------------------------------------------------

_ZONE_OP_MATRIX: dict[Zone, dict[OpType, PolicyDecision]] = {
    Zone.WORKSPACE: {
        OpType.READ: PolicyDecision.ALLOW,
        OpType.CREATE: PolicyDecision.ALLOW,
        OpType.EDIT: PolicyDecision.ALLOW,
        OpType.OVERWRITE: PolicyDecision.ALLOW,
        OpType.DELETE: PolicyDecision.CONFIRM,
        OpType.RECURSIVE_DELETE: PolicyDecision.CONFIRM,
    },
    Zone.CONTROLLED: {
        OpType.READ: PolicyDecision.ALLOW,
        # P0-1：CONTROLLED 区域所有写操作都需要 confirm（不分 CREATE/EDIT/OVERWRITE）。
        # 用户对桌面/文档/下载等区域的写入应该看得见、可拦截；
        # yolo 信任模式下 baseline_protection 仍放行，不影响重度信任用户。
        OpType.CREATE: PolicyDecision.CONFIRM,
        OpType.EDIT: PolicyDecision.CONFIRM,
        OpType.OVERWRITE: PolicyDecision.CONFIRM,
        OpType.DELETE: PolicyDecision.CONFIRM,
        OpType.RECURSIVE_DELETE: PolicyDecision.DENY,
    },
    Zone.PROTECTED: {
        OpType.READ: PolicyDecision.ALLOW,
        OpType.CREATE: PolicyDecision.DENY,
        OpType.EDIT: PolicyDecision.DENY,
        OpType.OVERWRITE: PolicyDecision.DENY,
        OpType.DELETE: PolicyDecision.DENY,
        OpType.RECURSIVE_DELETE: PolicyDecision.DENY,
    },
    Zone.FORBIDDEN: {
        OpType.READ: PolicyDecision.DENY,
        OpType.CREATE: PolicyDecision.DENY,
        OpType.EDIT: PolicyDecision.DENY,
        OpType.OVERWRITE: PolicyDecision.DENY,
        OpType.DELETE: PolicyDecision.DENY,
        OpType.RECURSIVE_DELETE: PolicyDecision.DENY,
    },
}

# ---------------------------------------------------------------------------
# Three-platform dangerous shell patterns (L3)
# ---------------------------------------------------------------------------

_CRITICAL_SHELL_PATTERNS: list[str] = [
    # Universal
    r"dd\s+if=",
    r"mkfs\.",
    r":\(\)\{\s*:\|:&\s*\};:",
    # Windows
    r"format\s+[a-zA-Z]:",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"cipher\s+/w:",
    # Linux / macOS
    r"rm\s+-rf\s+/\s",
    r"rm\s+-rf\s+/\*",
    r"rm\s+-rf\s+/$",
    r"mv\s+/\s",
    r"chmod\s+-R\s+000\s+/",
    r"chown\s+-R\s+.*\s+/\s",
    r">\s*/dev/sda",
]

_HIGH_RISK_SHELL_PATTERNS: list[str] = [
    # Windows cmd + PowerShell
    r"Remove-Item\s+.*-Recurse",
    r"Remove-Item\s+.*-Force",
    r"del\s+/[sS]",
    r"rd\s+/[sS]",
    r"rmdir\s+/[sS]\s*/[qQ]",
    # Write operations targeting system directories
    r"(?:copy|move|del|rd|rmdir|echo|Set-Content|Add-Content|New-Item).*(?:System32|Windows|Program Files)",
    r"Get-ChildItem.*\|\s*Remove-Item",
    r"Clear-RecycleBin",
    r"wmic\s+product.*uninstall",
    r"msiexec\s+/[xX]",
    r"winget\s+uninstall",
    r"choco\s+uninstall",
    # Linux / macOS
    r"rm\s+-rf\s+",
    r"rm\s+-r\s+",
    r"find\s+.*-delete",
    r"find\s+.*-exec\s+rm",
    r"xargs\s+rm",
    r"chmod\s+-R\s+",
    r"chown\s+-R\s+",
    r"apt\s+(remove|purge)",
    r"yum\s+(remove|erase)",
    r"brew\s+uninstall",
    r"dpkg\s+--purge",
    r"launchctl\s+unload",
    r"systemctl\s+(stop|disable|mask)",
    r"crontab\s+-r",
    # Cross-platform
    r"shutil\.rmtree",
    r"os\.remove\(|os\.unlink\(",
    r"pip\s+uninstall",
    r"npm\s+uninstall\s+-g",
    r"curl\s+.*\|\s*(bash|sh)",
    r"wget\s+.*\|\s*(bash|sh)",
]

# P1-6: MEDIUM 风险 Shell 模式（需确认但不需沙箱）
_MEDIUM_RISK_SHELL_PATTERNS: list[str] = [
    # 删除 / 清理（未匹配 HIGH 的情况）
    r"Remove-Item\b",
    r"Clear-Content\b",
    r"Clear-Item\b",
    # 环境 / 配置修改
    r"setx?\s+",
    r"export\s+\w+=",
    r"npm\s+install\s+-g",
    r"pip\s+install\s+",
    r"choco\s+install",
    r"winget\s+install",
    r"apt\s+install",
    r"brew\s+install",
    # 网络操作
    r"ssh\s+",
    r"scp\s+",
    r"rsync\s+",
    r"git\s+push",
    r"git\s+clone",
    r"docker\s+(run|exec|build)",
    # 进程管理
    r"kill\s+",
    r"pkill\s+",
    r"nohup\s+",
]

# Default blocked shell commands (direct DENY)
# ---------------------------------------------------------------------------
# Default zone paths per platform — C8b-2 后已迁到 ``policy_v2/defaults.py``。
# 本模块仍保留同名 / 同形状的导出，作为 thin re-export，避免 v1 内部以及
# 未迁完的旧 callsite（test_security.py / test_permission_refactor.py 等）
# 立即 break。C8b-5 删除整个 policy.py 时一起去除。
# ---------------------------------------------------------------------------

from .policy_v2.defaults import DEFAULT_BLOCKED_COMMANDS as _V2_DEFAULT_BLOCKED_COMMANDS
from .policy_v2.defaults import default_controlled_paths as _v2_default_controlled_paths
from .policy_v2.defaults import default_forbidden_paths as _v2_default_forbidden_paths
from .policy_v2.defaults import default_protected_paths as _v2_default_protected_paths

# 暴露为 list 而非 tuple，与 v1 形状保持完全一致（v1 是 ``list[str]``）。
# 每次 import 时拷一份，避免本模块用户 ``.append`` 改到 v2 immutable tuple。
_DEFAULT_BLOCKED_COMMANDS: list[str] = list(_V2_DEFAULT_BLOCKED_COMMANDS)


def _default_protected_paths() -> list[str]:
    """Thin re-export from ``policy_v2.defaults.default_protected_paths``.

    保留下划线前缀让 v1 内部 / 未迁 caller 继续工作；新代码请直接 import
    ``policy_v2.defaults.default_protected_paths``。
    """
    return _v2_default_protected_paths()


def _default_forbidden_paths() -> list[str]:
    """Thin re-export from ``policy_v2.defaults.default_forbidden_paths``."""
    return _v2_default_forbidden_paths()


def _default_controlled_paths() -> list[str]:
    """Thin re-export from ``policy_v2.defaults.default_controlled_paths``."""
    return _v2_default_controlled_paths()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PolicyResult:
    """策略引擎判定结果"""

    decision: PolicyDecision
    reason: str = ""
    policy_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ZonePolicyConfig:
    """四区路径配置"""

    enabled: bool = True
    workspace: list[str] = field(default_factory=list)
    controlled: list[str] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    default_zone: Zone = Zone.PROTECTED


@dataclass
class ConfirmationConfig:
    """确认门配置"""

    enabled: bool = True
    timeout_seconds: int = 60
    default_on_timeout: str = "deny"
    auto_confirm: bool = False
    mode: str = "smart"  # cautious | smart | yolo
    confirm_ttl: float = 120.0  # seconds for single-confirm TTL cache


@dataclass
class UserAllowlistEntry:
    """持久化白名单条目"""

    pattern: str = ""
    name: str = ""
    zone: str = ""
    entry_type: str = "command"  # "command" | "tool"
    added_at: str = ""
    needs_sandbox: bool = False


@dataclass
class UserAllowlistConfig:
    """用户白名单配置"""

    commands: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CommandPatternConfig:
    """命令模式拦截配置"""

    enabled: bool = True
    custom_critical: list[str] = field(default_factory=list)
    custom_high: list[str] = field(default_factory=list)
    excluded_patterns: list[str] = field(default_factory=list)
    blocked_commands: list[str] = field(default_factory=lambda: list(_DEFAULT_BLOCKED_COMMANDS))


@dataclass
class CheckpointConfig:
    """文件快照配置"""

    enabled: bool = True
    max_snapshots: int = 50
    snapshot_dir: str = "data/checkpoints"


@dataclass
class SelfProtectionConfig:
    """自保护配置"""

    enabled: bool = True
    protected_dirs: list[str] = field(
        default_factory=lambda: ["data/", "identity/", "logs/", "src/"]
    )
    audit_to_file: bool = True
    audit_path: str = "data/audit/policy_decisions.jsonl"
    death_switch_threshold: int = 3
    death_switch_total_multiplier: int = 3


@dataclass
class SandboxConfig:
    """沙箱配置"""

    enabled: bool = True
    backend: str = "auto"
    sandbox_risk_levels: list[str] = field(default_factory=lambda: ["HIGH"])
    exempt_commands: list[str] = field(default_factory=list)
    network_allow_in_sandbox: bool = False
    network_allowed_domains: list[str] = field(default_factory=list)


@dataclass
class ToolPolicyRule:
    """工具策略规则 (backward compat)"""

    tool_name: str
    decision: PolicyDecision = PolicyDecision.ALLOW
    dangerous_patterns: list[str] = field(default_factory=list)
    blocked_patterns: list[str] = field(default_factory=list)
    require_confirmation: bool = False
    max_execution_time: int = 120


@dataclass
class SecurityConfig:
    """完整六层安全配置"""

    enabled: bool = True
    zones: ZonePolicyConfig = field(default_factory=ZonePolicyConfig)
    confirmation: ConfirmationConfig = field(default_factory=ConfirmationConfig)
    command_patterns: CommandPatternConfig = field(default_factory=CommandPatternConfig)
    checkpoint: CheckpointConfig = field(default_factory=CheckpointConfig)
    self_protection: SelfProtectionConfig = field(default_factory=SelfProtectionConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    user_allowlist: UserAllowlistConfig = field(default_factory=UserAllowlistConfig)
    # Legacy compat
    tool_policies: list[ToolPolicyRule] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _expand_home(p: str) -> str:
    """Expand ~ to user home, normalise separators."""
    if p.startswith("~"):
        p = str(Path.home()) + p[1:]
    return p.replace("\\", "/")


def _normalise(p: str) -> str:
    """Normalise a path for matching: resolve, expand ~, forward slashes."""
    expanded = _expand_home(p)
    try:
        resolved = str(Path(expanded).resolve())
    except (OSError, ValueError):
        resolved = expanded
    return resolved.replace("\\", "/")


def _path_matches(normalised_path: str, pattern: str) -> bool:
    """Check if *normalised_path* matches the zone *pattern* (glob or prefix)."""
    norm_pattern = _normalise(pattern)

    prefix = norm_pattern[:-3] if norm_pattern.endswith("/**") else norm_pattern.rstrip("/")

    if normalised_path == prefix or normalised_path.startswith(prefix + "/"):
        return True

    if fnmatch.fnmatch(normalised_path, norm_pattern):
        return True
    if fnmatch.fnmatch(normalised_path.lower(), norm_pattern.lower()):
        return True
    return False


def _tool_to_optype(tool_name: str, params: dict[str, Any]) -> OpType:
    """Infer OpType from tool name and params.

    安全原则：未知工具默认归为 CREATE（有副作用），避免误放行。
    """
    _READ_TOOLS = (
        "read_file",
        "list_directory",
        "grep",
        "glob",
        "search_files",
        "web_search",
        "news_search",
        "browser_screenshot",
        "view_image",
        "get_tool_info",
        "get_skill_info",
        "list_skills",
        "get_workspace_map",
        "get_session_logs",
        "get_todo_status",
        "get_user_profile",
        "get_persona_profile",
        "list_mcp_servers",
        "get_mcp_instructions",
        "list_scheduled_tasks",
        "search_memory",
        "list_recent_tasks",
        "trace_memory",
        "search_conversation_traces",
        "get_memory_stats",
    )
    if tool_name in _READ_TOOLS:
        return OpType.READ
    if tool_name == "write_file":
        path = params.get("path", "")
        if path:
            try:
                fp = Path(path) if Path(path).is_absolute() else Path.cwd() / path
                if fp.exists():
                    return OpType.OVERWRITE
            except Exception:
                pass
        return OpType.CREATE
    if tool_name == "edit_file":
        return OpType.EDIT
    if tool_name in ("delete_file", "rename_file", "move_file"):
        return OpType.DELETE
    if tool_name in (
        "run_shell",
        "run_powershell",
        "call_mcp_tool",
        "browser_navigate",
        "browser_use",
        "browser_click",
        "browser_type",
        "desktop_click",
        "desktop_type",
    ):
        return OpType.CREATE
    return OpType.CREATE


_MCP_WRITE_HINT_RE = re.compile(
    r"(write|create|update|delete|remove|insert|set|patch|post|put|send|publish|"
    r"deploy|install|uninstall|exec|run|修改|写入|创建|删除|移除|更新|发送|发布|部署|安装|卸载|执行)",
    re.IGNORECASE,
)


def _is_readonly_mcp_tool(params: dict[str, Any]) -> bool:
    tool_name = str(
        params.get("tool")
        or params.get("tool_name")
        or params.get("name")
        or ""
    )
    if not tool_name:
        return False
    return not bool(_MCP_WRITE_HINT_RE.search(tool_name))


def _file_operation_targets(tool_name: str, params: dict[str, Any]) -> list[tuple[str, OpType]]:
    """Return file paths and their operation semantics for zone checks."""
    if tool_name == "move_file":
        src = (
            params.get("src")
            or params.get("source")
            or params.get("source_path")
            or params.get("from")
            or ""
        )
        dst = (
            params.get("dst")
            or params.get("destination")
            or params.get("target_path")
            or params.get("to")
            or ""
        )
        targets: list[tuple[str, OpType]] = []
        if src:
            targets.append((src, OpType.DELETE))
        if dst:
            op_type = OpType.CREATE
            try:
                fp = Path(dst) if Path(dst).is_absolute() else Path.cwd() / dst
                if fp.exists() and fp.is_dir() and src:
                    final_path = fp / Path(src).name
                    if final_path.exists():
                        op_type = OpType.OVERWRITE
                elif fp.exists():
                    op_type = OpType.OVERWRITE
            except Exception:
                pass
            targets.append((dst, op_type))
        return targets

    path = params.get("path", "") or params.get("file_path", "")
    if not path:
        return []
    return [(path, _tool_to_optype(tool_name, params))]


def _dangerous_tool_reason(tool_name: str, params: dict[str, Any]) -> str | None:
    """Return a human-readable reason when a tool is too risky to auto-confirm."""
    if tool_name in ("run_shell", "run_powershell"):
        return "shell 命令执行"
    if tool_name == "call_mcp_tool" and not _is_readonly_mcp_tool(params):
        mcp_tool = params.get("tool") or params.get("tool_name") or params.get("name") or "unknown"
        return f"MCP 写操作或未知副作用工具 ({mcp_tool})"
    if tool_name in {"delete_file", "move_file", "rename_file"}:
        return "文件删除/移动操作"
    if tool_name == "write_file" and _tool_to_optype(tool_name, params) == OpType.OVERWRITE:
        return "覆盖写入已有文件"
    for _path, op_type in _file_operation_targets(tool_name, params):
        if op_type in (OpType.DELETE, OpType.RECURSIVE_DELETE, OpType.OVERWRITE):
            return f"{_op_label(op_type)}操作"
    return None


_ZONE_LABELS = {
    "workspace": "工作区",
    "controlled": "受控区",
    "protected": "受保护区",
    "forbidden": "禁止访问区",
}
_OP_LABELS = {
    "read": "读取",
    "create": "创建",
    "edit": "编辑",
    "overwrite": "覆盖写入",
    "delete": "删除",
    "recursive_delete": "批量删除",
}


def _zone_label(zone: Zone) -> str:
    return _ZONE_LABELS.get(zone.value, zone.value)


def _op_label(op: OpType) -> str:
    return _OP_LABELS.get(op.value, op.value)


# ---------------------------------------------------------------------------
# Policy Engine
# ---------------------------------------------------------------------------


class PolicyEngine:
    """
    集中策略引擎 — 六层安全防护的 L1+L3 决策核心。

    在工具执行前调用 assert_tool_allowed() 检查是否允许执行。
    所有判定都会记录到审计系统。
    """

    def __init__(self, config: SecurityConfig | None = None) -> None:
        self._config = config or self._make_default_config()
        self._audit_log: list[dict[str, Any]] = []
        self._consecutive_denials = 0
        self._total_denials = 0
        self._readonly_mode = False
        # C8b-3: ``_confirmed_cache`` (TTL bypass) + ``_session_allowlist``
        # 已删除——session 临时白名单语义全部由
        # ``policy_v2.session_allowlist.SessionAllowlistManager`` 承担；TTL 缓
        # 存层 v2 弃用（"allow_once" 由 reasoning_engine 一次性放行处理，无需
        # 中间缓存）。
        # C8b-4: ``_frontend_mode`` shim 已删除——permission-mode endpoint 直读
        # ``policy_v2.read_permission_mode_label()``。``_session_allow_count`` +
        # ``_SMART_ESCALATION_THRESHOLD`` + smart-mode escalation 一并删除
        # （v2 没有 escalation 概念，MUTATING_SCOPED 始终 CONFIRM；v1 路径仅供
        # ``assert_tool_allowed`` 单测使用，C8b-5 删 v1 RiskGate 时一起清理）。
        # P1-5: 并发保护锁
        self._cache_lock = asyncio.Lock()
        self._pending_lock = asyncio.Lock()
        # F7: Temporary allowlists granted by skill activation.
        self._skill_allowlists: dict[str, set[str]] = {}
        # C9b: UI confirm state moved to ``ui_confirm_bus`` (module-level
        # singleton, survives engine reset). Push our configured TTL so the
        # bus's GC matches user settings.
        from .ui_confirm_bus import get_ui_confirm_bus

        get_ui_confirm_bus().configure_ttl(self._config.confirmation.confirm_ttl)

    @property
    def config(self) -> SecurityConfig:
        return self._config

    @property
    def readonly_mode(self) -> bool:
        return self._readonly_mode

    # ----- default config ---------------------------------------------------

    @staticmethod
    def _make_default_config() -> SecurityConfig:
        cwd = str(Path.cwd()).replace("\\", "/")
        # P0-1：默认 zone 配置策略
        # - workspace: cwd（项目根目录）
        # - controlled: 用户常用目录（桌面/文档/下载等）→ smart/cautious 模式下需 confirm
        # - protected: 系统目录 → 任意模式都拒绝写入
        # - forbidden: 凭据/密钥目录 → 任意模式都禁止读写
        # - default_zone = CONTROLLED：未明确列出的路径默认归 CONTROLLED，
        #   smart/cautious 触发 confirm；yolo 走 baseline_protection 继续放行
        return SecurityConfig(
            zones=ZonePolicyConfig(
                workspace=[cwd],
                controlled=_default_controlled_paths(),
                protected=_default_protected_paths(),
                forbidden=_default_forbidden_paths(),
                default_zone=Zone.CONTROLLED,
            ),
            confirmation=ConfirmationConfig(mode="yolo", auto_confirm=True),
            command_patterns=CommandPatternConfig(
                enabled=True,
                blocked_commands=list(_DEFAULT_BLOCKED_COMMANDS),
            ),
            self_protection=SelfProtectionConfig(enabled=True),
            sandbox=SandboxConfig(enabled=False),
            tool_policies=[
                ToolPolicyRule(
                    tool_name="run_shell",
                    require_confirmation=False,
                    dangerous_patterns=[],
                    blocked_patterns=[],
                ),
                ToolPolicyRule(
                    tool_name="run_powershell",
                    require_confirmation=False,
                    dangerous_patterns=[],
                    blocked_patterns=[],
                ),
            ],
        )

    # ----- YAML loading (supports both new "security:" and legacy format) ---

    def load_from_yaml(self, path: str | Path) -> None:
        """从 YAML 文件加载策略配置"""
        path = Path(path)
        if not path.exists():
            logger.debug(f"[Policy] Config file not found: {path}")
            return

        try:
            import yaml

            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data or not isinstance(data, dict):
                return
        except ImportError:
            logger.debug("[Policy] PyYAML not available, skipping YAML config")
            return
        except Exception as e:
            logger.warning(f"[Policy] Failed to load policy from {path}: {e}")
            return

        sec = data.get("security")
        if sec and isinstance(sec, dict):
            self._load_new_format(sec)
        else:
            self._load_legacy_format(data)

        logger.info(f"[Policy] Loaded policy from {path}")

    def _load_new_format(self, sec: dict) -> None:
        """Load the new six-layer security config."""
        self._config.enabled = sec.get("enabled", True)

        # zones
        z = sec.get("zones", {})
        if z:
            zc = self._config.zones
            zc.enabled = z.get("enabled", True)
            if "workspace" in z:
                raw = z["workspace"]
                zc.workspace = [
                    str(Path.cwd()).replace("\\", "/") if v == "${CWD}" else v
                    for v in (raw if isinstance(raw, list) else [raw])
                ]
            if "controlled" in z:
                zc.controlled = z["controlled"] or []
            if "protected" in z:
                zc.protected = z["protected"] or []
            if "forbidden" in z:
                zc.forbidden = z["forbidden"] or []
            zc.default_zone = Zone(z.get("default_zone", "workspace"))

        # confirmation
        c = sec.get("confirmation", {})
        if c:
            cc = self._config.confirmation
            cc.enabled = c.get("enabled", True)
            cc.timeout_seconds = c.get("timeout_seconds", 60)
            cc.default_on_timeout = c.get("default_on_timeout", "deny")
            cc.confirm_ttl = float(c.get("confirm_ttl", 120.0))
            # mode: cautious | smart | yolo
            mode = c.get("mode", "")
            if mode in ("cautious", "smart", "yolo"):
                cc.mode = mode
                cc.auto_confirm = mode == "yolo"
            else:
                # backward compat: auto_confirm boolean → mode
                cc.auto_confirm = c.get("auto_confirm", False)
                cc.mode = "yolo" if cc.auto_confirm else "smart"

        # command_patterns
        cp = sec.get("command_patterns", {})
        if cp:
            cpc = self._config.command_patterns
            cpc.enabled = cp.get("enabled", True)
            cpc.custom_critical = cp.get("custom_critical", []) or []
            cpc.custom_high = cp.get("custom_high", []) or []
            cpc.excluded_patterns = cp.get("excluded_patterns", []) or []
            if "blocked_commands" in cp:
                cpc.blocked_commands = cp["blocked_commands"] or []

        # checkpoint
        ck = sec.get("checkpoint", {})
        if ck:
            self._config.checkpoint.enabled = ck.get("enabled", True)
            self._config.checkpoint.max_snapshots = ck.get("max_snapshots", 50)
            self._config.checkpoint.snapshot_dir = ck.get("snapshot_dir", "data/checkpoints")

        # self_protection
        sp = sec.get("self_protection", {})
        if sp:
            spc = self._config.self_protection
            spc.enabled = sp.get("enabled", True)
            spc.protected_dirs = sp.get("protected_dirs", spc.protected_dirs)
            spc.audit_to_file = sp.get("audit_to_file", True)
            spc.audit_path = sp.get("audit_path", spc.audit_path)
            spc.death_switch_threshold = sp.get("death_switch_threshold", 3)
            spc.death_switch_total_multiplier = sp.get(
                "death_switch_total_multiplier",
                3,
            )

        # sandbox
        sb = sec.get("sandbox", {})
        if sb:
            sbc = self._config.sandbox
            sbc.enabled = sb.get("enabled", True)
            sbc.backend = sb.get("backend", "auto")
            sbc.sandbox_risk_levels = sb.get("sandbox_risk_levels", ["HIGH"])
            sbc.exempt_commands = sb.get("exempt_commands", []) or []
            net = sb.get("network", {})
            if net:
                sbc.network_allow_in_sandbox = net.get("allow_in_sandbox", False)
                sbc.network_allowed_domains = net.get("allowed_domains", []) or []

        # user_allowlist (persistent allow rules)
        ua = sec.get("user_allowlist", {})
        if ua and isinstance(ua, dict):
            self._config.user_allowlist = UserAllowlistConfig(
                commands=ua.get("commands", []) or [],
                tools=ua.get("tools", []) or [],
            )

    def _load_legacy_format(self, data: dict) -> None:
        """Load the old POLICIES.yaml format for backward compatibility."""
        # tool_policies
        for tp in data.get("tool_policies", []):
            if isinstance(tp, dict) and "tool_name" in tp:
                decision = PolicyDecision(tp.get("decision", "allow"))
                rule = ToolPolicyRule(
                    tool_name=tp["tool_name"],
                    decision=decision,
                    dangerous_patterns=tp.get("dangerous_patterns", []),
                    blocked_patterns=tp.get("blocked_patterns", []),
                    require_confirmation=tp.get("require_confirmation", False),
                    max_execution_time=tp.get("max_execution_time", 120),
                )
                existing = {r.tool_name for r in self._config.tool_policies}
                if rule.tool_name in existing:
                    self._config.tool_policies = [
                        r for r in self._config.tool_policies if r.tool_name != rule.tool_name
                    ]
                self._config.tool_policies.append(rule)

        # scope_policy → legacy paths become protected/forbidden
        sp = data.get("scope_policy", {})
        if sp:
            blocked_paths = sp.get("blocked_paths", [])
            if blocked_paths:
                existing = set(self._config.zones.protected)
                for p in blocked_paths:
                    if p not in existing:
                        self._config.zones.protected.append(p)
            if sp.get("blocked_commands"):
                self._config.command_patterns.blocked_commands = sp["blocked_commands"]

        auto = data.get("auto_confirm", False)
        self._config.confirmation.auto_confirm = auto
        self._config.confirmation.mode = "yolo" if auto else "smart"

    # ----- Main entry point -------------------------------------------------

    def assert_tool_allowed(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
    ) -> PolicyResult:
        """
        检查工具调用是否被策略允许。

        Returns:
            PolicyResult with decision (ALLOW/DENY/CONFIRM) and metadata.
            metadata may contain:
              - zone: Zone
              - op_type: OpType
              - risk_level: RiskLevel (for run_shell / run_powershell)
              - needs_checkpoint: bool
              - needs_sandbox: bool
        """
        params = params or {}

        if not self._config.enabled:
            return PolicyResult(decision=PolicyDecision.ALLOW, reason="安全策略已禁用")

        if self._is_trust_mode():
            baseline_result = self._check_baseline_protection(tool_name, params)
            if baseline_result:
                return baseline_result
            self._on_allow(tool_name, params)
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="信任模式放行（已通过 baseline 保护）",
                metadata={"trust_mode": True, "baseline_checked": True},
            )

        # Bypass CONFIRM if user approved via any allowlist tier
        allowlist_meta = self._check_allowlists(tool_name, params)
        if allowlist_meta is not None:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="用户已确认此操作",
                metadata=allowlist_meta,
            )

        # Death switch: readonly mode (NOT bypassable by skill allowlists)
        if self._readonly_mode:
            op = _tool_to_optype(tool_name, params)
            if op != OpType.READ:
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason="Agent 已进入只读模式（连续操作被拒绝触发死亡开关）",
                    policy_name="DeathSwitch",
                )

        # L5: Self-protection check (NOT bypassable by skill allowlists)
        sp_result = self._check_self_protection(tool_name, params)
        if sp_result:
            return sp_result

        # F7: Skill-granted temporary allowlist — bypasses L1/L3 and legacy
        # policies but NOT death switch, self-protection, or zone DELETE/CONFIRM.
        if self._is_skill_allowed(tool_name):
            # Even skill-allowed tools must respect zone DELETE CONFIRM
            op = _tool_to_optype(tool_name, params)
            if op in (OpType.DELETE, OpType.RECURSIVE_DELETE):
                zone_result = self._check_zone_policy(tool_name, params)
                if zone_result:
                    return zone_result
            # Shell commands still need risk check even when skill-allowed
            if tool_name in ("run_shell", "run_powershell"):
                shell_result = self._check_shell_command(tool_name, params)
                if shell_result and shell_result.decision == PolicyDecision.DENY:
                    return shell_result
            self._on_allow(tool_name, params)
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="技能临时授权放行",
                metadata={"skill_allowlist": True},
            )

        # Legacy tool-level policy (blocked_patterns, require_confirmation)
        legacy_result = self._check_legacy_tool_policy(tool_name, params)
        if legacy_result:
            return legacy_result

        # L3: Shell command risk classification
        if tool_name in ("run_shell", "run_powershell"):
            shell_result = self._check_shell_command(tool_name, params)
            if shell_result:
                return shell_result

        if tool_name == "call_mcp_tool" and not _is_readonly_mcp_tool(params):
            mcp_tool = params.get("tool") or params.get("tool_name") or params.get("name") or "unknown"
            result = PolicyResult(
                decision=PolicyDecision.CONFIRM,
                reason=f"MCP 工具 '{mcp_tool}' 可能产生写入或外部副作用，执行前需要确认",
                policy_name="McpToolRisk",
                metadata={"mcp_tool": str(mcp_tool), "dangerous_reason": "mcp_write_or_unknown"},
            )
            self._audit(tool_name, params, result)
            return result

        # L1: Zone × OpType matrix for file operations
        file_tools = {
            "read_file",
            "write_file",
            "edit_file",
            "move_file",
            "delete_file",
            "list_directory",
            "grep",
            "glob",
            "search_replace",
        }
        if tool_name in file_tools:
            zone_result = self._check_zone_policy(tool_name, params)
            if zone_result:
                return zone_result

        self._on_allow(tool_name, params)
        return PolicyResult(decision=PolicyDecision.ALLOW)

    def _is_trust_mode(self) -> bool:
        """Whether only baseline hard protection should apply.

        C8b-5: external callers (``agent.py:_check_trust_mode_skip`` +
        ``channels/gateway.py``) migrated to ``policy_v2.read_permission_mode_label``
        — this method is now strictly **internal** to v1 ``assert_tool_allowed``
        path and will be deleted in C8b-6 alongside ``assert_tool_allowed``
        itself. Do not add new callers.
        """
        return self._config.confirmation.mode == "yolo" or self._config.confirmation.auto_confirm

    def _check_baseline_protection(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> PolicyResult | None:
        """Default trust-mode guardrail: no prompts, only hard-deny sensitive areas."""
        file_tools = {
            "read_file",
            "write_file",
            "edit_file",
            "move_file",
            "delete_file",
            "list_directory",
            "grep",
            "glob",
            "search_replace",
        }
        if tool_name in file_tools:
            targets = _file_operation_targets(tool_name, params)
            if not targets:
                return None
            for path, op_type in targets:
                zone = self.resolve_zone(path)
                if zone == Zone.FORBIDDEN or (zone == Zone.PROTECTED and op_type != OpType.READ):
                    result = PolicyResult(
                        decision=PolicyDecision.DENY,
                        reason=(
                            "操作被拒绝: 该路径属于系统或密钥保护范围，"
                            f"信任模式下也不允许执行{_op_label(op_type)}操作 (路径: {path})"
                        ),
                        policy_name="BaselineProtection",
                        metadata={
                            "zone": zone.value,
                            "op_type": op_type.value,
                            "trust_mode": True,
                        },
                    )
                    self._audit(tool_name, params, result)
                    return result
                # P0-1：yolo 模式下越界访问（CONTROLLED 区域写操作）不打断，但记录审计
                if zone == Zone.CONTROLLED and op_type != OpType.READ:
                    logger.info(
                        "[Policy] Trust-mode crossing: tool=%s op=%s path=%s zone=%s "
                        "(yolo 模式放行；如需提示请切换到 smart/cautious 安全模式)",
                        tool_name,
                        op_type.value,
                        path,
                        zone.value,
                    )
            return None

        if tool_name in ("run_shell", "run_powershell"):
            command = str(params.get("command", ""))
            if not command:
                return None
            risk = self.classify_shell_risk(command)
            if risk == RiskLevel.CRITICAL or self._command_touches_sensitive_area(command):
                result = PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=f"操作被拒绝: 命令触碰系统或密钥保护范围 ({command[:120]})",
                    policy_name="BaselineProtection",
                    metadata={"risk_level": risk.value, "trust_mode": True},
                )
                self._audit(tool_name, params, result)
                return result
            if risk == RiskLevel.HIGH or (
                risk == RiskLevel.MEDIUM and self._is_destructive_shell_command(command)
            ):
                result = PolicyResult(
                    decision=PolicyDecision.CONFIRM,
                    reason=f"此命令会修改或删除文件，执行前需要您的确认: {command[:120]}",
                    policy_name="BaselineProtection",
                    metadata={"risk_level": risk.value, "trust_mode": True},
                )
                self._audit(tool_name, params, result)
                return result
            if (
                risk in (RiskLevel.HIGH, RiskLevel.MEDIUM)
                and self._command_touches_self_protection_area(command)
            ):
                result = PolicyResult(
                    decision=PolicyDecision.CONFIRM,
                    reason=f"信任模式下修改 Agent 关键目录仍需确认: {command[:120]}",
                    policy_name="BaselineProtection",
                    metadata={"risk_level": risk.value, "trust_mode": True},
                )
                self._audit(tool_name, params, result)
                return result
        return None

    @staticmethod
    def _is_destructive_shell_command(command: str) -> bool:
        """Detect shell commands that can remove, clear, overwrite, or move user data."""
        return bool(
            re.search(
                r"\b("
                r"rm|del|erase|rd|rmdir|remove-item|clear-content|clear-item|"
                r"set-content|move-item|mv|move|unlink|truncate"
                r")\b",
                command,
                re.IGNORECASE,
            )
        )

    def _command_touches_sensitive_area(self, command: str) -> bool:
        """Detect shell commands that operate on protected/forbidden paths in trust mode."""
        command_norm = command.replace("\\", "/").lower()
        destructive = re.search(
            r"\b(rm|del|rd|rmdir|remove-item|move|mv|copy|cp|set-content|add-content|new-item)\b",
            command,
            re.IGNORECASE,
        )
        if not destructive:
            return False
        for pattern in [*self._config.zones.forbidden, *self._config.zones.protected]:
            probe = _normalise(pattern).rstrip("*").rstrip("/").lower()
            if probe and probe in command_norm:
                return True
        return False

    def _command_touches_self_protection_area(self, command: str) -> bool:
        """Detect destructive shell commands targeting OpenAkita's own critical dirs."""
        if not self._config.self_protection.enabled:
            return False
        command_norm = command.replace("\\", "/").lower()
        destructive = re.search(
            r"\b(rm|del|rd|rmdir|remove-item|move|mv|copy|cp|set-content|add-content|new-item)\b",
            command,
            re.IGNORECASE,
        )
        if not destructive:
            return False
        for pdir in self._config.self_protection.protected_dirs:
            probes = {
                _normalise(pdir).rstrip("*").rstrip("/").lower(),
                pdir.replace("\\", "/").rstrip("*").rstrip("/").lower(),
            }
            if any(probe and probe in command_norm for probe in probes):
                return True
        return False

    # ----- F7: Skill temporary allowlist ------------------------------------

    def _is_skill_allowed(self, tool_name: str) -> bool:
        """Check if tool_name is temporarily allowed by any active skill."""
        return any(tool_name in allowed_set for allowed_set in self._skill_allowlists.values())

    def add_skill_allowlist(self, skill_id: str, tool_names: list[str]) -> None:
        """Grant temporary tool access for a skill context."""
        if tool_names:
            self._skill_allowlists[skill_id] = set(tool_names)
            logger.debug(
                "[Policy] Skill '%s' granted temporary access to: %s",
                skill_id,
                tool_names,
            )

    def remove_skill_allowlist(self, skill_id: str) -> None:
        """Revoke temporary tool access for a skill context."""
        removed = self._skill_allowlists.pop(skill_id, None)
        if removed:
            logger.debug(
                "[Policy] Revoked skill '%s' temporary access to: %s",
                skill_id,
                removed,
            )

    def clear_skill_allowlists(self) -> None:
        """Revoke all skill-granted allowlists."""
        self._skill_allowlists.clear()

    # ----- L1: Zone policy --------------------------------------------------

    def resolve_zone(self, path: str) -> Zone:
        """Determine which zone a path belongs to."""
        if not self._config.zones.enabled:
            return Zone.WORKSPACE

        norm = _normalise(path)

        for fp in self._config.zones.forbidden:
            if _path_matches(norm, fp):
                return Zone.FORBIDDEN

        for pp in self._config.zones.protected:
            if _path_matches(norm, pp):
                return Zone.PROTECTED

        for wp in self._config.zones.workspace:
            if _path_matches(norm, wp):
                return Zone.WORKSPACE

        for cp in self._config.zones.controlled:
            if _path_matches(norm, cp):
                return Zone.CONTROLLED

        return self._config.zones.default_zone

    def _check_zone_policy(self, tool_name: str, params: dict[str, Any]) -> PolicyResult | None:
        """L1: Check file operation against zone × op_type matrix."""
        if not self._config.zones.enabled:
            return None

        targets = _file_operation_targets(tool_name, params)
        if not targets:
            return None

        checkpoint_metadata: dict[str, Any] | None = None
        for path, op_type in targets:
            zone = self.resolve_zone(path)
            decision = _ZONE_OP_MATRIX[zone][op_type]

            needs_checkpoint = (
                zone == Zone.CONTROLLED
                and op_type in (OpType.EDIT, OpType.OVERWRITE)
                and self._config.checkpoint.enabled
            )

            if decision == PolicyDecision.DENY:
                result = PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=(
                        f"操作被拒绝: 不允许在{_zone_label(zone)}对该路径执行"
                        f"{_op_label(op_type)}操作 (路径: {path})"
                    ),
                    policy_name="ZonePolicy",
                    metadata={
                        "zone": zone.value,
                        "op_type": op_type.value,
                    },
                )
                self._on_deny(tool_name, params, result)
                return result

            if decision == PolicyDecision.CONFIRM:
                dangerous_reason = _dangerous_tool_reason(tool_name, params)
                if self._config.confirmation.auto_confirm and not dangerous_reason:
                    continue
                result = PolicyResult(
                    decision=PolicyDecision.CONFIRM,
                    reason=(
                        f"此操作需要您的确认: 在{_zone_label(zone)}执行"
                        f"{_op_label(op_type)} (路径: {path})"
                    ),
                    policy_name="ZonePolicy",
                    metadata={
                        "zone": zone.value,
                        "op_type": op_type.value,
                        "needs_checkpoint": needs_checkpoint,
                        **({"dangerous_reason": dangerous_reason} if dangerous_reason else {}),
                    },
                )
                self._audit(tool_name, params, result)
                return result

            # ALLOW — still note if checkpoint needed
            if needs_checkpoint:
                checkpoint_metadata = {
                    "needs_checkpoint": True,
                    "zone": zone.value,
                    "op_type": op_type.value,
                }
        if checkpoint_metadata:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="",
                metadata=checkpoint_metadata,
            )
        return None

    # ----- L3: Shell command risk classification ----------------------------

    def classify_shell_risk(self, command: str) -> RiskLevel:
        """Classify a shell command's risk level."""
        if not command:
            return RiskLevel.LOW

        excluded = set(self._config.command_patterns.excluded_patterns)

        all_critical = _CRITICAL_SHELL_PATTERNS + self._config.command_patterns.custom_critical
        for pattern in all_critical:
            if pattern in excluded:
                continue
            try:
                if re.search(pattern, command, re.IGNORECASE):
                    return RiskLevel.CRITICAL
            except re.error:
                pass

        all_high = _HIGH_RISK_SHELL_PATTERNS + self._config.command_patterns.custom_high
        for pattern in all_high:
            if pattern in excluded:
                continue
            try:
                if re.search(pattern, command, re.IGNORECASE):
                    return RiskLevel.HIGH
            except re.error:
                pass

        for pattern in _MEDIUM_RISK_SHELL_PATTERNS:
            if pattern in excluded:
                continue
            try:
                if re.search(pattern, command, re.IGNORECASE):
                    return RiskLevel.MEDIUM
            except re.error:
                pass

        return RiskLevel.LOW

    def _check_shell_command(self, tool_name: str, params: dict[str, Any]) -> PolicyResult | None:
        """L3: Check shell command for blocked commands and risk patterns."""
        command = str(params.get("command", ""))
        if not command:
            return None

        if not self._config.command_patterns.enabled:
            return None

        # Blocked commands (direct DENY)
        cmd_parts = command.strip().split()
        if cmd_parts:
            base_cmd = cmd_parts[0].lower()
            if "/" in base_cmd or "\\" in base_cmd:
                base_cmd = base_cmd.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if base_cmd.endswith(".exe"):
                base_cmd = base_cmd[:-4]

            for blocked in self._config.command_patterns.blocked_commands:
                if base_cmd == blocked.lower():
                    result = PolicyResult(
                        decision=PolicyDecision.DENY,
                        reason=f"命令 '{blocked}' 被策略禁止",
                        policy_name="CommandPattern",
                    )
                    self._on_deny(tool_name, params, result)
                    return result

        # Risk classification
        risk = self.classify_shell_risk(command)

        needs_sandbox = (
            self._config.sandbox.enabled
            and risk.value.upper() in self._config.sandbox.sandbox_risk_levels
            and command not in self._config.sandbox.exempt_commands
        )

        if risk == RiskLevel.CRITICAL:
            result = PolicyResult(
                decision=PolicyDecision.DENY,
                reason=f"极高风险命令，已自动拦截: {command[:120]}",
                policy_name="RiskClassification",
                metadata={"risk_level": risk.value},
            )
            self._on_deny(tool_name, params, result)
            return result

        # C8b-4: ``mode = self._config.confirmation.mode`` 删除——以前用于 smart
        # mode escalation 分支判断，escalation 路径已与 ``_session_allow_count``
        # 一并清除，本变量也成了 unused。
        if risk == RiskLevel.HIGH:
            result = PolicyResult(
                decision=PolicyDecision.CONFIRM,
                reason=f"高风险命令，执行前需要您的确认: {command[:120]}",
                policy_name="RiskClassification",
                metadata={"risk_level": risk.value, "needs_sandbox": needs_sandbox},
            )
            self._audit(tool_name, params, result)
            return result

        if risk == RiskLevel.MEDIUM:
            # C8b-4: smart-mode 自动升信任路径已删除——v2 没有 escalation 概念
            # （MUTATING_SCOPED 始终 CONFIRM）。v1 ``mode == "smart"`` 现在与
            # cautious/yolo 一样：MEDIUM 风险一律走 CONFIRM。
            result = PolicyResult(
                decision=PolicyDecision.CONFIRM,
                reason=f"此命令可能修改系统配置或安装软件，需要确认: {command[:120]}",
                policy_name="RiskClassification",
                metadata={"risk_level": risk.value},
            )
            self._audit(tool_name, params, result)
            return result

        return None

    # ----- L5: Self-protection ----------------------------------------------

    def _check_self_protection(self, tool_name: str, params: dict[str, Any]) -> PolicyResult | None:
        """L5: Prevent deletion of agent's own critical directories."""
        if not self._config.self_protection.enabled:
            return None

        write_tools = {"write_file", "edit_file", "move_file", "delete_file"}
        if tool_name in ("run_shell", "run_powershell"):
            command = str(params.get("command", ""))
            risk = self.classify_shell_risk(command)
            if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                for pdir in self._config.self_protection.protected_dirs:
                    norm_dir = _normalise(pdir)
                    if norm_dir.lower() in command.lower().replace("\\", "/"):
                        result = PolicyResult(
                            decision=PolicyDecision.DENY,
                            reason=f"自保护: 禁止对 Agent 关键目录 '{pdir}' 执行高危命令",
                            policy_name="SelfProtection",
                        )
                        self._on_deny(tool_name, params, result)
                        return result
        elif tool_name in write_tools:
            paths: list[str] = []
            if tool_name == "move_file":
                src = (
                    params.get("src")
                    or params.get("source")
                    or params.get("source_path")
                    or params.get("from")
                    or ""
                )
                if src:
                    paths.append(src)
            elif tool_name == "delete_file":
                path = params.get("path", "") or params.get("file_path", "")
                if path:
                    paths.append(path)
            for path in paths:
                norm_path = _normalise(path)
                for pdir in self._config.self_protection.protected_dirs:
                    norm_dir = _normalise(pdir)
                    if norm_path == norm_dir or norm_path.startswith(norm_dir.rstrip("/") + "/"):
                        result = PolicyResult(
                            decision=PolicyDecision.DENY,
                            reason=f"自保护: 禁止移动/删除 Agent 关键目录 '{pdir}' 下的文件",
                            policy_name="SelfProtection",
                        )
                        self._on_deny(tool_name, params, result)
                        return result
        return None

    # ----- Legacy tool policy (backward compat) -----------------------------

    def _check_legacy_tool_policy(
        self, tool_name: str, params: dict[str, Any]
    ) -> PolicyResult | None:
        """Check legacy ToolPolicyRule for backward compatibility."""
        for rule in self._config.tool_policies:
            if rule.tool_name != "*" and rule.tool_name != tool_name:
                continue

            if rule.blocked_patterns:
                param_str = str(params)
                for pattern in rule.blocked_patterns:
                    try:
                        if re.search(pattern, param_str, re.IGNORECASE):
                            result = PolicyResult(
                                decision=PolicyDecision.DENY,
                                reason=f"工具 {tool_name} 的参数包含被禁止的内容模式",
                                policy_name="ToolPolicy",
                            )
                            self._on_deny(tool_name, params, result)
                            return result
                    except re.error:
                        pass

            if rule.decision == PolicyDecision.DENY:
                result = PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=f"工具 '{tool_name}' 已被安全策略禁用",
                    policy_name="ToolPolicy",
                )
                self._on_deny(tool_name, params, result)
                return result

            if getattr(rule, "require_confirmation", False):
                return PolicyResult(
                    decision=PolicyDecision.CONFIRM,
                    reason=f"工具 '{tool_name}' 的安全策略要求用户确认后执行",
                    policy_name="ToolPolicy",
                )

        return None

    # ----- Death switch & audit helpers -------------------------------------

    def _on_deny(self, tool_name: str, params: dict[str, Any], result: PolicyResult) -> None:
        self._consecutive_denials += 1
        self._total_denials += 1
        consecutive_threshold = self._config.self_protection.death_switch_threshold
        multiplier = self._config.self_protection.death_switch_total_multiplier
        total_threshold = consecutive_threshold * multiplier if consecutive_threshold > 0 else 0
        should_trigger = (
            self._config.self_protection.enabled
            and not self._readonly_mode
            and (
                (consecutive_threshold > 0 and self._consecutive_denials >= consecutive_threshold)
                or (total_threshold > 0 and self._total_denials >= total_threshold)
            )
        )
        if should_trigger:
            self._readonly_mode = True
            logger.warning(
                f"[Policy] 死亡开关触发: 连续拒绝={self._consecutive_denials}, "
                f"累计拒绝={self._total_denials}, Agent 进入只读模式"
            )
            try:
                from openakita.api.routes.websocket import broadcast_event

                asyncio.ensure_future(broadcast_event(
                    "security:death_switch",
                    {"active": True, "consecutive": self._consecutive_denials,
                     "total": self._total_denials},
                ))
            except Exception:
                pass
        self._audit(tool_name, params, result)

    def _on_allow(self, tool_name: str, params: dict[str, Any] | None = None) -> None:
        # C8b-4: ``_session_allow_count += 1`` removed alongside smart-mode
        # escalation (no more "after 3 allows, auto-escalate trust"). v2 never
        # had this counter; v1 ``assert_tool_allowed`` simply does not escalate.
        if tool_name not in ("read_file", "list_directory", "grep", "glob"):
            self._consecutive_denials = 0
        if params is not None:
            result = PolicyResult(decision=PolicyDecision.ALLOW, reason="", policy_name="")
            self._audit(tool_name, params, result)

    def reset_readonly_mode(self) -> None:
        """Manually reset the death switch (e.g. after user intervention)."""
        self._readonly_mode = False
        self._consecutive_denials = 0
        logger.info("[Policy] 只读模式已重置")

    # ----- Smart Approval (LLM-assisted risk assessment) ---------------------

    async def smart_approve(
        self,
        tool_name: str,
        params: dict[str, Any],
        context: str = "",
    ) -> tuple[str, str]:
        """
        Use a lightweight LLM call to assess risk of a CONFIRM-level operation.

        Returns:
            ("APPROVE"|"DENY"|"ESCALATE", reason)
            - APPROVE: auto-approve, low risk in context
            - DENY: block, high risk detected
            - ESCALATE: still needs human confirmation
        """
        try:
            from ..core.brain import Brain

            brain = Brain()

            command = params.get("command", "")
            path = params.get("path", "")

            prompt = (
                "You are a security reviewer. Assess the risk of this tool call:\n\n"
                f"Tool: {tool_name}\n"
                f"Command/Path: {command or path}\n"
                f"Parameters: {params}\n"
            )
            if context:
                prompt += f"Context: {context}\n"

            prompt += (
                "\nRespond with exactly one of:\n"
                "APPROVE - if the operation is clearly safe and routine\n"
                "DENY - if it's clearly dangerous (rm -rf /, DROP TABLE, etc.)\n"
                "ESCALATE - if you're unsure and human should decide\n\n"
                "Format: DECISION: reason"
            )

            response = await brain.think_lightweight(prompt, max_tokens=256)
            text = response.content.strip().upper()

            if text.startswith("APPROVE"):
                reason = response.content.strip().split(":", 1)[-1].strip() if ":" in response.content else "LLM assessed as safe"
                logger.info(f"[SmartApproval] APPROVE for {tool_name}: {reason}")
                return "APPROVE", reason
            elif text.startswith("DENY"):
                reason = response.content.strip().split(":", 1)[-1].strip() if ":" in response.content else "LLM assessed as dangerous"
                logger.warning(f"[SmartApproval] DENY for {tool_name}: {reason}")
                return "DENY", reason
            else:
                reason = response.content.strip().split(":", 1)[-1].strip() if ":" in response.content else "LLM uncertain"
                return "ESCALATE", reason

        except Exception as e:
            logger.warning(f"[SmartApproval] LLM call failed: {e}, escalating")
            return "ESCALATE", f"LLM assessment failed: {e}"

    # ----- Confirmation cache & allowlists -----------------------------------

    def _confirm_cache_key(self, tool_name: str, params: dict[str, Any]) -> str:
        """Generate a cache key for a confirmed action."""
        import hashlib

        param_str = f"{tool_name}:{params.get('command', '')}{params.get('path', '')}"
        return hashlib.md5(param_str.encode()).hexdigest()

    @staticmethod
    def _command_to_pattern(command: str) -> str:
        """Extract a glob-matchable pattern from a command string.

        For session/persistent allowlists we match the base command (first
        token + ``*``) so that ``npm install foo`` also matches later
        ``npm install bar``.

        Handles full-path executors like ``"C:/.../python.exe" -m pip install``
        by extracting the semantic sub-command (``pip install*``).
        """
        parts = command.strip().split()
        if not parts:
            return command

        base = parts[0].strip('"').strip("'")
        sep = "/" if "/" in base else "\\"
        exe_name = base.rsplit(sep, 1)[-1].lower() if sep in base else base.lower()
        if exe_name.endswith(".exe"):
            exe_name = exe_name[:-4]

        _EXECUTOR_NAMES = {"python", "python3", "python3.11", "python3.12", "python3.13", "node", "ruby", "perl"}
        if exe_name in _EXECUTOR_NAMES and len(parts) >= 3 and parts[1] == "-m":
            if len(parts) >= 4:
                return f"{parts[2]} {parts[3]}*"
            return f"{parts[2]}*"

        if len(parts) >= 2:
            return f"{parts[0]} {parts[1]}*"
        return f"{parts[0]}*"

    # C8b-3: ``mark_confirmed`` 已删除——session/persistent allowlist 写入
    # 由 ``policy_v2.confirm_resolution.apply_resolution`` 统一调度（写
    # ``SessionAllowlistManager`` + ``UserAllowlistManager``）。``_persist_allowlist_entry``
    # / ``_save_user_allowlist`` 仍然保留作为 v1 ``_check_persistent_allowlist``
    # 的内部支撑，等 C8b-5 删 v1 ``assert_tool_allowed`` 时一起清理。

    def _persist_allowlist_entry(
        self,
        tool_name: str,
        params: dict[str, Any],
        needs_sandbox: bool,
    ) -> None:
        """Append an entry to the persistent user_allowlist in YAML."""
        from datetime import datetime

        now_str = datetime.now(UTC).isoformat()
        command = params.get("command", "")

        if tool_name in ("run_shell", "run_powershell") and command:
            entry = {
                "pattern": self._command_to_pattern(command),
                "added_at": now_str,
                "needs_sandbox": needs_sandbox,
            }
            self._config.user_allowlist.commands.append(entry)
        else:
            entry = {
                "name": tool_name,
                "zone": "workspace",
                "added_at": now_str,
                "needs_sandbox": needs_sandbox,
            }
            self._config.user_allowlist.tools.append(entry)

        self._save_user_allowlist()

    def _save_user_allowlist(self) -> None:
        """Write the user_allowlist section back to POLICIES.yaml."""
        try:
            import yaml

            from ..config import settings

            yaml_path = settings.identity_path / "POLICIES.yaml"
            if not yaml_path.exists():
                return

            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            sec = data.setdefault("security", {})
            sec["user_allowlist"] = {
                "commands": self._config.user_allowlist.commands,
                "tools": self._config.user_allowlist.tools,
            }

            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            logger.warning(f"[Policy] Failed to save user_allowlist: {e}")

    def remove_allowlist_entry(self, entry_type: str, index: int) -> bool:
        """Remove a persistent allowlist entry by type and index."""
        al = self._config.user_allowlist
        target = al.commands if entry_type == "command" else al.tools
        if 0 <= index < len(target):
            target.pop(index)
            self._save_user_allowlist()
            return True
        return False

    def get_user_allowlist(self) -> dict[str, list[dict[str, Any]]]:
        """Return the current persistent allowlist for API/UI."""
        al = self._config.user_allowlist
        return {"commands": list(al.commands), "tools": list(al.tools)}

    def _check_persistent_allowlist(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Check if tool call matches a persistent user allowlist entry.

        Returns the entry metadata (including ``needs_sandbox``) or None.
        Matches both the raw command and the semantic command extracted
        by ``_command_to_pattern`` (handles full-path executors like
        ``python.exe -m pip install``).
        """
        command = params.get("command", "")
        if tool_name in ("run_shell", "run_powershell") and command:
            semantic_pattern = self._command_to_pattern(command)
            semantic_cmd = semantic_pattern.rstrip("*").rstrip()
            for entry in self._config.user_allowlist.commands:
                pattern = entry.get("pattern", "")
                if not pattern:
                    continue
                if fnmatch.fnmatch(command, pattern):
                    return entry
                if semantic_cmd and fnmatch.fnmatch(semantic_cmd, pattern):
                    return entry
        else:
            for entry in self._config.user_allowlist.tools:
                if entry.get("name") == tool_name:
                    return entry
        return None

    def _check_allowlists(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Check persistent allowlist + v2 session allowlist + v2 skill allowlist.

        C8b-3: v1 ``_session_allowlist`` / ``_confirmed_cache`` 已删除——session
        和 TTL 缓存的语义全部由 ``policy_v2.session_allowlist.SessionAllowlistManager``
        承担。本方法保留只为 v1 ``assert_tool_allowed`` 仍能跑（生产路径已切 v2，
        v1 入口仅 v1-only 单测使用）；C8b-5 删 ``assert_tool_allowed`` 时一起删。

        Returns metadata dict (with ``needs_sandbox``) if allowed, else None.
        """
        # Tier 1: Persistent allowlist
        persistent = self._check_persistent_allowlist(tool_name, params)
        if persistent is not None:
            return {
                "confirmed_bypass": True,
                "needs_sandbox": persistent.get("needs_sandbox", False),
            }

        # Tier 2: Session allowlist (v2 SessionAllowlistManager)
        try:
            from .policy_v2.session_allowlist import get_session_allowlist_manager

            session_entry = get_session_allowlist_manager().is_allowed(tool_name, params)
            if session_entry is not None:
                return {
                    "confirmed_bypass": True,
                    "needs_sandbox": session_entry.get("needs_sandbox", False),
                }
        except Exception:
            pass

        return None

    # C8b-3: 6 个 UI confirm facade 方法（``store_ui_pending`` /
    # ``cleanup_session`` / ``resolve_ui_confirm`` / ``prepare_ui_confirm`` /
    # ``cleanup_ui_confirm`` / ``wait_for_ui_resolution``）已删除。生产
    # callsite 全部直连 ``policy_v2.confirm_resolution.apply_resolution`` /
    # ``policy_v2.session_allowlist.get_session_allowlist_manager`` /
    # ``ui_confirm_bus.get_ui_confirm_bus``。详见 docs/policy_v2_research.md
    # 「C8b-3 实施记录」§1。

    # ----- Audit ------------------------------------------------------------

    def _audit(self, tool_name: str, params: dict, result: PolicyResult) -> None:
        import time

        entry = {
            "timestamp": time.time(),
            "tool_name": tool_name,
            "params_preview": str(params)[:200],
            "decision": result.decision.value,
            "reason": result.reason,
            "policy": result.policy_name,
            "risk_level": result.metadata.get("risk_level", ""),
        }
        self._audit_log.append(entry)

        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-500:]

        if result.decision != PolicyDecision.ALLOW:
            logger.info(f"[Policy] {result.decision.value}: {tool_name} — {result.reason}")

        try:
            from .audit_logger import get_audit_logger

            get_audit_logger().log(
                tool_name=tool_name,
                decision=result.decision.value,
                reason=result.reason,
                policy=result.policy_name,
                params_preview=str(params)[:200],
                metadata=result.metadata if result.metadata else None,
            )
        except Exception:
            pass

        try:
            from ..tracing.tracer import get_tracer

            tracer = get_tracer()
            tracer.record_decision(
                decision_type="policy_check",
                reasoning=result.reason,
                outcome=result.decision.value,
                tool_name=tool_name,
                policy=result.policy_name,
            )
        except Exception:
            pass

    def get_audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit_log)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_global_policy_engine: PolicyEngine | None = None


def get_policy_engine() -> PolicyEngine:
    """获取全局策略引擎实例"""
    global _global_policy_engine
    if _global_policy_engine is None:
        _global_policy_engine = PolicyEngine()
        try:
            from ..config import settings

            yaml_path = settings.identity_path / "POLICIES.yaml"
        except Exception:
            yaml_path = Path("identity/POLICIES.yaml")
        _global_policy_engine.load_from_yaml(yaml_path)
    return _global_policy_engine


def set_policy_engine(engine: PolicyEngine) -> None:
    """设置全局策略引擎实例（用于测试）"""
    global _global_policy_engine
    _global_policy_engine = engine


def reset_policy_engine() -> None:
    """重置全局策略引擎（重新加载配置时使用）。

    C6 起 v1 与 v2 引擎并存（v2 决策 + v1 UI 状态），二者各自缓存
    ``POLICIES.yaml``。任何让 v1 重读 YAML 的入口必须同步让 v2 重读，否则
    ``api/routes/config.py`` 通过 UI 修改 trust mode / safety_immune 等设置
    后，v2 仍按旧配置评估 → 决策与用户预期错位（重现 P1 用户反馈：
    "信任模式不生效"）。详见 docs/policy_v2_research.md §C6 复审记录。
    """
    global _global_policy_engine
    previous = _global_policy_engine
    _global_policy_engine = None
    try:
        from .audit_logger import reset_audit_logger

        reset_audit_logger()
    except Exception:
        pass
    try:
        from .policy_v2.global_engine import reset_engine_v2

        reset_engine_v2()
    except Exception:
        logger.warning(
            "[Policy] failed to reset PolicyEngineV2 singleton; v2 may serve stale config",
            exc_info=True,
        )
    if previous is not None:
        refreshed = get_policy_engine()
        refreshed._readonly_mode = previous._readonly_mode
        refreshed._consecutive_denials = previous._consecutive_denials
        refreshed._total_denials = previous._total_denials
        # C8b-3: ``_confirmed_cache`` / ``_session_allowlist`` deleted. Session
        # allowlist state lives on the module-level
        # ``policy_v2.session_allowlist.SessionAllowlistManager`` singleton
        # which automatically survives engine reset; no field copy required.
        # C9b: ``_pending_ui_confirms`` / ``_ui_confirm_events`` /
        # ``_ui_confirm_decisions`` are no longer engine attributes —
        # they live on the module-level ``UIConfirmBus`` singleton,
        # which automatically survives policy engine reset. The
        # field-by-field copy that used to be here is no longer needed.
        refreshed._skill_allowlists = previous._skill_allowlists
        # C8b-4: ``_session_allow_count`` deleted along with smart-mode
        # escalation (no more cross-instance counter to preserve).


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

import fnmatch
import logging
import os
import platform
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PolicyDecision(str, Enum):
    """策略判定结果"""
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


class Zone(str, Enum):
    """安全区域"""
    WORKSPACE = "workspace"
    CONTROLLED = "controlled"
    PROTECTED = "protected"
    FORBIDDEN = "forbidden"


class OpType(str, Enum):
    """操作类型"""
    READ = "read"
    CREATE = "create"
    EDIT = "edit"
    OVERWRITE = "overwrite"
    DELETE = "delete"
    RECURSIVE_DELETE = "recursive_delete"


class RiskLevel(str, Enum):
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
        OpType.DELETE: PolicyDecision.ALLOW,
        OpType.RECURSIVE_DELETE: PolicyDecision.CONFIRM,
    },
    Zone.CONTROLLED: {
        OpType.READ: PolicyDecision.ALLOW,
        OpType.CREATE: PolicyDecision.ALLOW,
        OpType.EDIT: PolicyDecision.ALLOW,
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
    r"del\s+/[sS]",
    r"rd\s+/[sS]",
    r"rmdir\s+/[sS]\s*/[qQ]",
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

# Default blocked shell commands (direct DENY)
_DEFAULT_BLOCKED_COMMANDS: list[str] = [
    "reg", "regedit", "netsh", "schtasks", "sc",
    "wmic", "bcdedit", "shutdown", "taskkill",
]

# ---------------------------------------------------------------------------
# Default zone paths per platform
# ---------------------------------------------------------------------------

def _default_protected_paths() -> list[str]:
    """Platform-specific default protected paths."""
    paths = []
    if platform.system() == "Windows":
        paths.extend([
            "C:/Program Files/**",
            "C:/Program Files (x86)/**",
            "C:/Windows/**",
            "C:/ProgramData/**",
        ])
    else:
        paths.extend([
            "/usr/**", "/bin/**", "/sbin/**", "/lib/**", "/lib64/**",
            "/boot/**", "/etc/**", "/dev/**", "/proc/**", "/sys/**",
        ])
        if platform.system() == "Darwin":
            paths.extend(["/System/**", "/Library/**"])
    return paths


def _default_forbidden_paths() -> list[str]:
    """Platform-specific default forbidden paths."""
    paths = ["~/.ssh/**", "~/.gnupg/**"]
    if platform.system() == "Windows":
        paths.append("C:/Windows/System32/config/**")
    else:
        paths.extend(["/etc/shadow", "/etc/gshadow"])
    return paths


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

    if norm_pattern.endswith("/**"):
        prefix = norm_pattern[:-3]
    else:
        prefix = norm_pattern.rstrip("/")

    if normalised_path == prefix or normalised_path.startswith(prefix + "/"):
        return True

    if fnmatch.fnmatch(normalised_path, norm_pattern):
        return True
    if fnmatch.fnmatch(normalised_path.lower(), norm_pattern.lower()):
        return True
    return False


def _tool_to_optype(tool_name: str, params: dict[str, Any]) -> OpType:
    """Infer OpType from tool name and params."""
    if tool_name in ("read_file", "list_directory", "grep", "glob"):
        return OpType.READ
    if tool_name == "write_file":
        path = params.get("path", "")
        if path:
            try:
                from pathlib import Path as P
                fp = P(path) if P(path).is_absolute() else P.cwd() / path
                if fp.exists():
                    return OpType.OVERWRITE
            except Exception:
                pass
        return OpType.CREATE
    if tool_name == "edit_file":
        return OpType.EDIT
    if tool_name == "delete_file":
        return OpType.DELETE
    return OpType.READ


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
        self._readonly_mode = False
        # Confirmation cache: (tool_name, param_hash) → expiry timestamp
        # Prevents CONFIRM loops when user already approved a recent action.
        self._confirmed_cache: dict[str, float] = {}
        self._confirm_ttl = 120.0  # seconds
        # Pending UI confirmations: tool_id → {tool_name, params}
        # Populated when SSE security_confirm is sent; consumed by API callback.
        self._pending_ui_confirms: dict[str, dict[str, Any]] = {}

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
        return SecurityConfig(
            zones=ZonePolicyConfig(
                workspace=[cwd],
                controlled=[],
                protected=_default_protected_paths(),
                forbidden=_default_forbidden_paths(),
            ),
            command_patterns=CommandPatternConfig(
                blocked_commands=list(_DEFAULT_BLOCKED_COMMANDS),
            ),
            tool_policies=[
                ToolPolicyRule(
                    tool_name="run_shell",
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
            zc.default_zone = Zone(z.get("default_zone", "protected"))

        # confirmation
        c = sec.get("confirmation", {})
        if c:
            cc = self._config.confirmation
            cc.enabled = c.get("enabled", True)
            cc.timeout_seconds = c.get("timeout_seconds", 60)
            cc.default_on_timeout = c.get("default_on_timeout", "deny")
            cc.auto_confirm = c.get("auto_confirm", False)

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
                        r for r in self._config.tool_policies
                        if r.tool_name != rule.tool_name
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

        self._config.confirmation.auto_confirm = data.get("auto_confirm", False)

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
              - risk_level: RiskLevel (for run_shell)
              - needs_checkpoint: bool
              - needs_sandbox: bool
        """
        params = params or {}

        if not self._config.enabled:
            return PolicyResult(decision=PolicyDecision.ALLOW, reason="Security disabled")

        # Bypass CONFIRM if user recently approved an identical action
        if self._is_recently_confirmed(tool_name, params):
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="Recently confirmed by user",
                metadata={"confirmed_bypass": True},
            )

        # Death switch: readonly mode
        if self._readonly_mode:
            op = _tool_to_optype(tool_name, params)
            if op != OpType.READ:
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason="Agent 已进入只读模式（连续操作被拒绝触发死亡开关）",
                    policy_name="DeathSwitch",
                )

        # L5: Self-protection check
        sp_result = self._check_self_protection(tool_name, params)
        if sp_result:
            return sp_result

        # Legacy tool-level policy (blocked_patterns, require_confirmation)
        legacy_result = self._check_legacy_tool_policy(tool_name, params)
        if legacy_result:
            return legacy_result

        # L3: Shell command risk classification
        if tool_name == "run_shell":
            shell_result = self._check_shell_command(tool_name, params)
            if shell_result:
                return shell_result

        # L1: Zone × OpType matrix for file operations
        file_tools = {
            "read_file", "write_file", "edit_file", "delete_file",
            "list_directory", "grep", "glob", "search_replace",
        }
        if tool_name in file_tools:
            zone_result = self._check_zone_policy(tool_name, params)
            if zone_result:
                return zone_result

        self._on_allow(tool_name)
        return PolicyResult(decision=PolicyDecision.ALLOW)

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

    def _check_zone_policy(
        self, tool_name: str, params: dict[str, Any]
    ) -> PolicyResult | None:
        """L1: Check file operation against zone × op_type matrix."""
        if not self._config.zones.enabled:
            return None

        path = params.get("path", "") or params.get("file_path", "")
        if not path:
            return None

        zone = self.resolve_zone(path)
        op_type = _tool_to_optype(tool_name, params)
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
                    f"操作被拒绝: {op_type.value} 在 {zone.value} 区域 "
                    f"(路径: {path})"
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
            if self._config.confirmation.auto_confirm:
                return None
            result = PolicyResult(
                decision=PolicyDecision.CONFIRM,
                reason=(
                    f"操作需要确认: {op_type.value} 在 {zone.value} 区域 "
                    f"(路径: {path})"
                ),
                policy_name="ZonePolicy",
                metadata={
                    "zone": zone.value,
                    "op_type": op_type.value,
                    "needs_checkpoint": needs_checkpoint,
                },
            )
            self._audit(tool_name, params, result)
            return result

        # ALLOW — still note if checkpoint needed
        if needs_checkpoint:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="",
                metadata={"needs_checkpoint": True, "zone": zone.value,
                          "op_type": op_type.value},
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

        return RiskLevel.LOW

    def _check_shell_command(
        self, tool_name: str, params: dict[str, Any]
    ) -> PolicyResult | None:
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
                reason=f"CRITICAL 风险命令被自动拒绝: {command[:120]}",
                policy_name="RiskClassification",
                metadata={"risk_level": risk.value},
            )
            self._on_deny(tool_name, params, result)
            return result

        if risk == RiskLevel.HIGH:
            if self._config.confirmation.auto_confirm:
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    metadata={
                        "risk_level": risk.value,
                        "needs_sandbox": needs_sandbox,
                    },
                )
            result = PolicyResult(
                decision=PolicyDecision.CONFIRM,
                reason=f"HIGH 风险命令需要确认: {command[:120]}",
                policy_name="RiskClassification",
                metadata={
                    "risk_level": risk.value,
                    "needs_sandbox": needs_sandbox,
                },
            )
            self._audit(tool_name, params, result)
            return result

        return None

    # ----- L5: Self-protection ----------------------------------------------

    def _check_self_protection(
        self, tool_name: str, params: dict[str, Any]
    ) -> PolicyResult | None:
        """L5: Prevent deletion of agent's own critical directories."""
        if not self._config.self_protection.enabled:
            return None

        write_tools = {"write_file", "edit_file", "delete_file"}
        if tool_name == "run_shell":
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
            path = params.get("path", "") or params.get("file_path", "")
            if path and tool_name == "delete_file":
                norm_path = _normalise(path)
                for pdir in self._config.self_protection.protected_dirs:
                    norm_dir = _normalise(pdir)
                    if norm_path == norm_dir or norm_path.startswith(norm_dir.rstrip("/") + "/"):
                        result = PolicyResult(
                            decision=PolicyDecision.DENY,
                            reason=f"自保护: 禁止删除 Agent 关键目录 '{pdir}' 下的文件",
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
                                reason=f"Blocked pattern '{pattern}' in {tool_name}",
                                policy_name="ToolPolicy",
                            )
                            self._on_deny(tool_name, params, result)
                            return result
                    except re.error:
                        pass

            if rule.decision == PolicyDecision.DENY:
                result = PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=f"Tool '{tool_name}' is blocked by policy",
                    policy_name="ToolPolicy",
                )
                self._on_deny(tool_name, params, result)
                return result

        return None

    # ----- Death switch & audit helpers -------------------------------------

    def _on_deny(
        self, tool_name: str, params: dict[str, Any], result: PolicyResult
    ) -> None:
        self._consecutive_denials += 1
        threshold = self._config.self_protection.death_switch_threshold
        if (
            self._config.self_protection.enabled
            and threshold > 0
            and self._consecutive_denials >= threshold
            and not self._readonly_mode
        ):
            self._readonly_mode = True
            logger.warning(
                f"[Policy] 死亡开关触发: 连续 {self._consecutive_denials} 次操作被拒绝, "
                "Agent 进入只读模式"
            )
        self._audit(tool_name, params, result)

    def _on_allow(self, tool_name: str) -> None:
        if tool_name not in ("read_file", "list_directory", "grep", "glob"):
            self._consecutive_denials = 0

    def reset_readonly_mode(self) -> None:
        """Manually reset the death switch (e.g. after user intervention)."""
        self._readonly_mode = False
        self._consecutive_denials = 0
        logger.info("[Policy] 只读模式已重置")

    # ----- Confirmation cache -----------------------------------------------

    def _confirm_cache_key(self, tool_name: str, params: dict[str, Any]) -> str:
        """Generate a cache key for a confirmed action."""
        import hashlib
        param_str = f"{tool_name}:{params.get('command', '')}{params.get('path', '')}"
        return hashlib.md5(param_str.encode()).hexdigest()

    def mark_confirmed(self, tool_name: str, params: dict[str, Any]) -> None:
        """Record that the user confirmed a specific tool call.

        Subsequent identical calls within *_confirm_ttl* seconds will
        be auto-allowed instead of triggering CONFIRM again.
        """
        import time
        key = self._confirm_cache_key(tool_name, params)
        self._confirmed_cache[key] = time.time() + self._confirm_ttl

    def _is_recently_confirmed(self, tool_name: str, params: dict[str, Any]) -> bool:
        """Check if an identical action was recently confirmed by the user."""
        import time
        key = self._confirm_cache_key(tool_name, params)
        expiry = self._confirmed_cache.get(key)
        if expiry and time.time() < expiry:
            return True
        self._confirmed_cache.pop(key, None)
        return False

    def store_ui_pending(
        self, tool_id: str, tool_name: str, params: dict[str, Any]
    ) -> None:
        """Store a pending UI confirmation (SSE security_confirm sent)."""
        self._pending_ui_confirms[tool_id] = {
            "tool_name": tool_name,
            "params": params,
        }

    def resolve_ui_confirm(self, confirm_id: str, decision: str) -> bool:
        """Called by the /api/chat/security-confirm endpoint.

        If *decision* is 'allow' or 'sandbox', marks the action as
        confirmed so the next retry bypasses CONFIRM.
        Returns True if the confirm_id was found.
        """
        pending = self._pending_ui_confirms.pop(confirm_id, None)
        if not pending:
            return False
        if decision in ("allow", "sandbox"):
            self.mark_confirmed(pending["tool_name"], pending["params"])
        return True

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
        }
        self._audit_log.append(entry)

        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-500:]

        if result.decision != PolicyDecision.ALLOW:
            logger.info(
                f"[Policy] {result.decision.value}: {tool_name} — {result.reason}"
            )

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
    """重置全局策略引擎（重新加载配置时使用）"""
    global _global_policy_engine
    _global_policy_engine = None

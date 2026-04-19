"""
Centralized Policy Engine (Agent Harness: Policy Engine)

Core decision layer of six-layer security protection system:
- L1: Four zones (workspace/controlled/protected/forbidden) + operation type matrix
- L3: Three-platform (Windows/macOS/Linux) dangerous command pattern matching and risk classification

Policy types:
- ZonePolicy: Path zone × operation type matrix determination
- RiskPolicy: Shell command risk classification (CRITICAL/HIGH/MEDIUM/LOW)
- ToolPolicy: Tool-level policy (allow/deny, parameter limits, require confirmation)
- ScopePolicy: Scope policy (Shell command blacklist, backward-compatible)
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import platform
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
    """Policy decision result"""

    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


class Zone(StrEnum):
    """Security zones"""

    WORKSPACE = "workspace"
    CONTROLLED = "controlled"
    PROTECTED = "protected"
    FORBIDDEN = "forbidden"


class OpType(StrEnum):
    """Operation types"""

    READ = "read"
    CREATE = "create"
    EDIT = "edit"
    OVERWRITE = "overwrite"
    DELETE = "delete"
    RECURSIVE_DELETE = "recursive_delete"


class RiskLevel(StrEnum):
    """Shell command risk level"""

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

# P1-6: MEDIUM risk Shell patterns (requires confirmation but no sandbox)
_MEDIUM_RISK_SHELL_PATTERNS: list[str] = [
    # Delete / cleanup (cases not matching HIGH)
    r"Remove-Item\b",
    r"Clear-Content\b",
    r"Clear-Item\b",
    # Environment / configuration modifications
    r"setx?\s+",
    r"export\s+\w+=",
    r"npm\s+install\s+-g",
    r"pip\s+install\s+",
    r"choco\s+install",
    r"winget\s+install",
    r"apt\s+install",
    r"brew\s+install",
    # Network operations
    r"ssh\s+",
    r"scp\s+",
    r"rsync\s+",
    r"git\s+push",
    r"git\s+clone",
    r"docker\s+(run|exec|build)",
    # Process management
    r"kill\s+",
    r"pkill\s+",
    r"nohup\s+",
]

# Default blocked shell commands (direct DENY)
_DEFAULT_BLOCKED_COMMANDS: list[str] = [
    "reg",
    "regedit",
    "netsh",
    "schtasks",
    "sc",
    "wmic",
    "bcdedit",
    "shutdown",
    "taskkill",
]

# ---------------------------------------------------------------------------
# Default zone paths per platform
# ---------------------------------------------------------------------------


def _default_protected_paths() -> list[str]:
    """Platform-specific default protected paths."""
    paths = []
    if platform.system() == "Windows":
        paths.extend(
            [
                "C:/Program Files/**",
                "C:/Program Files (x86)/**",
                "C:/Windows/**",
                "C:/ProgramData/**",
            ]
        )
    else:
        paths.extend(
            [
                "/usr/**",
                "/bin/**",
                "/sbin/**",
                "/lib/**",
                "/lib64/**",
                "/boot/**",
                "/etc/**",
                "/dev/**",
                "/proc/**",
                "/sys/**",
            ]
        )
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
    """Policy engine decision result"""

    decision: PolicyDecision
    reason: str = ""
    policy_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ZonePolicyConfig:
    """Four-zone path configuration"""

    enabled: bool = True
    workspace: list[str] = field(default_factory=list)
    controlled: list[str] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    default_zone: Zone = Zone.PROTECTED


@dataclass
class ConfirmationConfig:
    """Confirmation gate configuration"""

    enabled: bool = True
    timeout_seconds: int = 60
    default_on_timeout: str = "deny"
    auto_confirm: bool = False
    mode: str = "smart"  # cautious | smart | yolo
    confirm_ttl: float = 120.0  # seconds for single-confirm TTL cache


@dataclass
class UserAllowlistEntry:
    """Persistent allowlist entry"""

    pattern: str = ""
    name: str = ""
    zone: str = ""
    entry_type: str = "command"  # "command" | "tool"
    added_at: str = ""
    needs_sandbox: bool = False


@dataclass
class UserAllowlistConfig:
    """User allowlist configuration"""

    commands: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CommandPatternConfig:
    """Command pattern interception configuration"""

    enabled: bool = True
    custom_critical: list[str] = field(default_factory=list)
    custom_high: list[str] = field(default_factory=list)
    excluded_patterns: list[str] = field(default_factory=list)
    blocked_commands: list[str] = field(default_factory=lambda: list(_DEFAULT_BLOCKED_COMMANDS))


@dataclass
class CheckpointConfig:
    """File snapshot configuration"""

    enabled: bool = True
    max_snapshots: int = 50
    snapshot_dir: str = "data/checkpoints"


@dataclass
class SelfProtectionConfig:
    """Self-protection configuration"""

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
    """Sandbox configuration"""

    enabled: bool = True
    backend: str = "auto"
    sandbox_risk_levels: list[str] = field(default_factory=lambda: ["HIGH"])
    exempt_commands: list[str] = field(default_factory=list)
    network_allow_in_sandbox: bool = False
    network_allowed_domains: list[str] = field(default_factory=list)


@dataclass
class ToolPolicyRule:
    """Tool policy rule (backward compat)"""

    tool_name: str
    decision: PolicyDecision = PolicyDecision.ALLOW
    dangerous_patterns: list[str] = field(default_factory=list)
    blocked_patterns: list[str] = field(default_factory=list)
    require_confirmation: bool = False
    max_execution_time: int = 120


@dataclass
class SecurityConfig:
    """Complete six-layer security configuration"""

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

    Security principle: Unknown tools default to CREATE (has side effects) to avoid false positives.
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
    if tool_name in ("delete_file", "rename_file"):
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


_ZONE_LABELS = {
    "workspace": "workspace",
    "controlled": "controlled",
    "protected": "protected",
    "forbidden": "forbidden",
}
_OP_LABELS = {
    "read": "read",
    "create": "create",
    "edit": "edit",
    "overwrite": "overwrite",
    "delete": "delete",
    "recursive_delete": "recursive_delete",
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
    Centralized Policy Engine — core decision layer for L1+L3 of six-layer security protection.

    Call assert_tool_allowed() before tool execution to check if execution is permitted.
    All decisions are logged to the audit system.
    """

    def __init__(self, config: SecurityConfig | None = None) -> None:
        self._config = config or self._make_default_config()
        self._audit_log: list[dict[str, Any]] = []
        self._consecutive_denials = 0
        self._total_denials = 0
        self._readonly_mode = False
        # TTL confirmation cache: key → {expiry, needs_sandbox}
        self._confirmed_cache: dict[str, dict[str, Any]] = {}
        # Session allowlist: cache_key → {needs_sandbox, pattern}
        self._session_allowlist: dict[str, dict[str, Any]] = {}
        # P1-5: Concurrent protection lock
        self._cache_lock = asyncio.Lock()
        # Pending UI confirmations: tool_id → {tool_name, params, ...}
        self._pending_ui_confirms: dict[str, dict[str, Any]] = {}
        self._pending_lock = asyncio.Lock()
        # F7: Temporary allowlists granted by skill activation.
        self._skill_allowlists: dict[str, set[str]] = {}
        # P3-3: Frontend security mode (synced to confirmation.mode)
        self._frontend_mode: str = "smart"

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
        """Load policy configuration from YAML file"""
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
            self._frontend_mode = cc.mode

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
        self._frontend_mode = self._config.confirmation.mode

    # ----- Main entry point -------------------------------------------------

    def assert_tool_allowed(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
    ) -> PolicyResult:
        """
        Check if tool invocation is permitted by policy.

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
            return PolicyResult(decision=PolicyDecision.ALLOW, reason="Security policy is disabled")

        # Bypass CONFIRM if user approved via any allowlist tier
        allowlist_meta = self._check_allowlists(tool_name, params)
        if allowlist_meta is not None:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                reason="User has already confirmed this operation",
                metadata=allowlist_meta,
            )

        # Death switch: readonly mode (NOT bypassable by skill allowlists)
        if self._readonly_mode:
            op = _tool_to_optype(tool_name, params)
            if op != OpType.READ:
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason="Agent has entered read-only mode (consecutive operation denials triggered death switch)",
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
                reason="Skill temporary authorization granted",
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

        # L1: Zone × OpType matrix for file operations
        file_tools = {
            "read_file",
            "write_file",
            "edit_file",
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
                    f"Operation denied: not allowed to perform {_op_label(op_type)} in {_zone_label(zone)} on this path (path: {path})"
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
                    f"This operation requires your confirmation: perform {_op_label(op_type)} in {_zone_label(zone)} (path: {path})"
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
                metadata={"needs_checkpoint": True, "zone": zone.value, "op_type": op_type.value},
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
                        reason=f"Command '{blocked}' is blocked by policy",
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
                reason=f"Critical risk command, automatically blocked: {command[:120]}",
                policy_name="RiskClassification",
                metadata={"risk_level": risk.value},
            )
            self._on_deny(tool_name, params, result)
            return result

        mode = self._config.confirmation.mode

        if risk == RiskLevel.HIGH:
            if mode == "yolo":
                self._on_allow(tool_name, params)
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    metadata={"risk_level": risk.value, "needs_sandbox": needs_sandbox},
                )
            result = PolicyResult(
                decision=PolicyDecision.CONFIRM,
                reason=f"High-risk command, requires your confirmation before execution: {command[:120]}",
                policy_name="RiskClassification",
                metadata={"risk_level": risk.value, "needs_sandbox": needs_sandbox},
            )
            self._audit(tool_name, params, result)
            return result

        if risk == RiskLevel.MEDIUM:
            if mode == "yolo":
                self._on_allow(tool_name, params)
                return PolicyResult(
                    decision=PolicyDecision.ALLOW,
                    metadata={"risk_level": risk.value},
                )
            if mode == "smart":
                # smart mode: MEDIUM returns CONFIRM; frontend checkAutoAllow
                # handles auto-allow based on session trust escalation.
                pass
            result = PolicyResult(
                decision=PolicyDecision.CONFIRM,
                reason=f"This command may modify system configuration or install software, confirmation required: {command[:120]}",
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

        write_tools = {"write_file", "edit_file", "delete_file"}
        if tool_name in ("run_shell", "run_powershell"):
            command = str(params.get("command", ""))
            risk = self.classify_shell_risk(command)
            if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                for pdir in self._config.self_protection.protected_dirs:
                    norm_dir = _normalise(pdir)
                    if norm_dir.lower() in command.lower().replace("\\", "/"):
                        result = PolicyResult(
                            decision=PolicyDecision.DENY,
                            reason=f"Self-protection: forbidden to execute high-risk commands on Agent critical directory '{pdir}'",
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
                            reason=f"Self-protection: forbidden to delete files under Agent critical directory '{pdir}'",
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
                                reason=f"Tool {tool_name} parameters contain forbidden content patterns",
                                policy_name="ToolPolicy",
                            )
                            self._on_deny(tool_name, params, result)
                            return result
                    except re.error:
                        pass

            if rule.decision == PolicyDecision.DENY:
                result = PolicyResult(
                    decision=PolicyDecision.DENY,
                    reason=f"Tool '{tool_name}' is disabled by security policy",
                    policy_name="ToolPolicy",
                )
                self._on_deny(tool_name, params, result)
                return result

            if getattr(rule, "require_confirmation", False):
                return PolicyResult(
                    decision=PolicyDecision.CONFIRM,
                    reason=f"Tool '{tool_name}' security policy requires user confirmation before execution",
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
                f"[Policy] Death switch triggered: consecutive_denials={self._consecutive_denials}, "
                f"total_denials={self._total_denials}, Agent entering read-only mode"
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
        if tool_name not in ("read_file", "list_directory", "grep", "glob"):
            self._consecutive_denials = 0
        if params is not None:
            result = PolicyResult(decision=PolicyDecision.ALLOW, reason="", policy_name="")
            self._audit(tool_name, params, result)

    def reset_readonly_mode(self) -> None:
        """Manually reset the death switch (e.g. after user intervention)."""
        self._readonly_mode = False
        self._consecutive_denials = 0
        logger.info("[Policy] Read-only mode has been reset")

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
        """
        parts = command.strip().split()
        if not parts:
            return command
        if len(parts) >= 2:
            return f"{parts[0]} {parts[1]}*"
        return f"{parts[0]}*"

    def mark_confirmed(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        scope: str = "once",
        needs_sandbox: bool = False,
    ) -> None:
        """Record that the user confirmed a specific tool call.

        *scope*: ``"once"`` (TTL cache), ``"session"`` (session lifetime),
        or ``"always"`` (persisted to YAML allowlist).
        """
        import time

        key = self._confirm_cache_key(tool_name, params)
        ttl = self._config.confirmation.confirm_ttl
        entry = {"needs_sandbox": needs_sandbox}

        if scope == "always":
            self._persist_allowlist_entry(tool_name, params, needs_sandbox)
            self._session_allowlist[key] = entry
        elif scope == "session":
            self._session_allowlist[key] = entry
        # Always write TTL cache as well for immediate bypass
        self._confirmed_cache[key] = {
            "expiry": time.time() + ttl,
            "needs_sandbox": needs_sandbox,
        }

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
        """
        command = params.get("command", "")
        if tool_name in ("run_shell", "run_powershell") and command:
            for entry in self._config.user_allowlist.commands:
                pattern = entry.get("pattern", "")
                if pattern and fnmatch.fnmatch(command, pattern):
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
        """Check all three allowlist tiers: persistent → session → TTL.

        Returns metadata dict (with ``needs_sandbox``) if allowed, else None.
        """
        import time

        # Tier 1: Persistent allowlist
        persistent = self._check_persistent_allowlist(tool_name, params)
        if persistent is not None:
            return {
                "confirmed_bypass": True,
                "needs_sandbox": persistent.get("needs_sandbox", False),
            }

        key = self._confirm_cache_key(tool_name, params)

        # Tier 2: Session allowlist
        session_entry = self._session_allowlist.get(key)
        if session_entry is not None:
            return {
                "confirmed_bypass": True,
                "needs_sandbox": session_entry.get("needs_sandbox", False),
            }

        # Tier 3: TTL cache
        cache_entry = self._confirmed_cache.get(key)
        if cache_entry:
            expiry = cache_entry.get("expiry", 0)
            if time.time() < expiry:
                return {
                    "confirmed_bypass": True,
                    "needs_sandbox": cache_entry.get("needs_sandbox", False),
                }
            self._confirmed_cache.pop(key, None)

        return None

    def store_ui_pending(
        self,
        tool_id: str,
        tool_name: str,
        params: dict[str, Any],
        *,
        session_id: str = "",
        needs_sandbox: bool = False,
    ) -> None:
        """Store a pending UI confirmation (SSE security_confirm sent)."""
        import time

        self._cleanup_expired_confirms()
        self._pending_ui_confirms[tool_id] = {
            "tool_name": tool_name,
            "params": params,
            "created_at": time.time(),
            "session_id": session_id,
            "needs_sandbox": needs_sandbox,
        }

    def _cleanup_expired_confirms(self) -> None:
        """Remove pending confirmations older than confirm_ttl seconds."""
        import time

        ttl = self._config.confirmation.confirm_ttl
        now = time.time()
        expired = [
            k for k, v in self._pending_ui_confirms.items() if now - v.get("created_at", 0) > ttl
        ]
        for k in expired:
            self._pending_ui_confirms.pop(k, None)

    def cleanup_session(self, session_id: str) -> None:
        """Remove all pending confirmations for a session and clear session allowlist."""
        to_remove = [
            k for k, v in self._pending_ui_confirms.items() if v.get("session_id") == session_id
        ]
        for k in to_remove:
            self._pending_ui_confirms.pop(k, None)
        self._session_allowlist.clear()

    def resolve_ui_confirm(self, confirm_id: str, decision: str) -> bool:
        """Called by the /api/chat/security-confirm endpoint.

        *decision*: ``allow_once`` | ``allow_session`` | ``allow_always`` |
        ``deny`` | ``sandbox``.  Legacy ``allow`` maps to ``allow_once``.
        Returns True if the confirm_id was found.
        """
        pending = self._pending_ui_confirms.pop(confirm_id, None)
        if not pending:
            return False

        # Normalize legacy values
        if decision == "allow":
            decision = "allow_once"

        needs_sandbox = pending.get("needs_sandbox", False)
        if decision == "sandbox":
            needs_sandbox = True

        scope_map = {
            "allow_once": "once",
            "allow_session": "session",
            "allow_always": "always",
            "sandbox": "once",
        }
        scope = scope_map.get(decision)
        if scope:
            self.mark_confirmed(
                pending["tool_name"],
                pending["params"],
                scope=scope,
                needs_sandbox=needs_sandbox,
            )
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
    """Get global policy engine instance"""
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
    """Set global policy engine instance (for testing)"""
    global _global_policy_engine
    _global_policy_engine = engine


def reset_policy_engine() -> None:
    """Reset global policy engine (used when reloading configuration)"""
    global _global_policy_engine
    _global_policy_engine = None

"""PolicyConfigV2 — Pydantic v2 schema for ``identity/POLICIES.yaml``.

v2 重新组织 v1 的 7 个区块为 11 个语义清晰的区块（详见 docs §7 迁移规则）：

| v1 区块 | v2 区块 |
|---|---|
| ``zones.workspace`` | ``workspace.paths`` |
| ``zones.protected`` + ``zones.forbidden`` + ``self_protection.protected_dirs`` | ``safety_immune.paths`` |
| ``zones.controlled`` + ``zones.default_zone`` | （废弃，启动时 WARN） |
| ``confirmation.mode: yolo/smart/cautious`` | ``confirmation.mode: trust/default/strict`` |
| ``confirmation.auto_confirm`` | （废弃，trust mode 取代） |
| ``command_patterns.*`` | ``shell_risk.*`` |
| ``self_protection.audit_*`` | ``audit.*`` |
| ``self_protection.death_switch_*`` | ``death_switch.*`` |
| ``sandbox.network.*`` | ``sandbox.network_*`` 扁平化 |

Pydantic v2 校验 + ``model_config`` 启用 ``extra='forbid'``，让 typo 直接报错而非
silently 忽略（avoid v1 时代 schema drift 调试苦）。

兼容性策略：本模块只声明 v2 schema；``loader.py`` 负责 v1→v2 迁移与 deep-merge。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import ApprovalClass, ConfirmationMode, SessionRole

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    """共享配置：禁 extra 字段（避免 typo 漂移），允许 enum 取 string value。"""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=True,
        str_strip_whitespace=True,
    )


class WorkspaceConfig(_Strict):
    """workspace 路径列表（替代 v1 ``zones.workspace``）。

    支持 ``${CWD}`` 占位符，loader 在加载时展开为 ``Path.cwd()``。
    """

    paths: list[str] = Field(default_factory=lambda: ["${CWD}"])

    @field_validator("paths", mode="before")
    @classmethod
    def _coerce_to_list(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [v]
        if v is None:
            return ["${CWD}"]
        return list(v)


class ConfirmationConfig(_Strict):
    """确认门配置（v2 mode 只有 5 档，详见 enums.ConfirmationMode）。"""

    mode: ConfirmationMode = ConfirmationMode.DEFAULT
    timeout_seconds: int = Field(default=60, ge=1, le=86400)
    default_on_timeout: Literal["allow", "deny"] = "deny"
    confirm_ttl: float = Field(default=120.0, ge=0.0, le=86400.0)


class SessionRoleConfig(_Strict):
    """会话角色默认值。每个 session 启动时若没指定 role，用此默认。"""

    default: SessionRole = SessionRole.AGENT


class SafetyImmuneConfig(_Strict):
    """绝不允许 trust 模式静默放行的关键路径白名单。

    替代 v1 ``zones.protected`` + ``zones.forbidden`` + ``self_protection.protected_dirs``
    三处的合集（loader.migration 在迁移时 union + dedupe）。
    """

    paths: list[str] = Field(default_factory=list)


class OwnerOnlyConfig(_Strict):
    """仅 session owner 可执行的工具列表（IM 渠道下额外卡死）。

    工具名（不含 ``plugin:``/``mcp:`` 前缀）。CONTROL_PLANE 类工具默认走 owner_only
    自动逻辑（engine.step 4 默认行为），本列表是显式 override，提供精细控制。
    """

    tools: list[str] = Field(default_factory=list)


class ApprovalClassesConfig(_Strict):
    """工具到 ApprovalClass 的显式 override 映射。

    优先级最高（高于 SKILL/MCP/PLUGIN/heuristic 任一来源），用于用户手动调整
    某工具的风险分类（典型：把某个被 heuristic 判 DESTRUCTIVE 的自定义工具降到
    MUTATING_SCOPED）。但**不能**降级到比 ``most_strict`` 更宽松——loader 会
    用 ``most_strict`` 与现有 explicit lookup 叠加，避免用户错配削弱安全。
    """

    overrides: dict[str, ApprovalClass] = Field(default_factory=dict)


class ShellRiskConfig(_Strict):
    """Shell 命令风险分类配置（替代 v1 ``command_patterns``）。"""

    enabled: bool = True
    custom_critical: list[str] = Field(default_factory=list)
    custom_high: list[str] = Field(default_factory=list)
    custom_medium: list[str] = Field(default_factory=list)
    excluded_patterns: list[str] = Field(default_factory=list)
    blocked_commands: list[str] = Field(
        default_factory=lambda: [
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
    )


class CheckpointConfig(_Strict):
    """文件快照配置。"""

    enabled: bool = True
    max_snapshots: int = Field(default=50, ge=0, le=10000)
    snapshot_dir: str = "data/checkpoints"


class SandboxConfig(_Strict):
    """沙箱配置（v1 ``sandbox.network.*`` 在 v2 扁平化为 ``network_*``）。"""

    enabled: bool = False
    backend: Literal["auto", "docker", "firejail", "wsl", "none"] = "auto"
    sandbox_risk_levels: list[Literal["MEDIUM", "HIGH", "CRITICAL"]] = Field(
        default_factory=lambda: ["HIGH"]
    )
    exempt_commands: list[str] = Field(default_factory=list)
    network_allow_in_sandbox: bool = False
    network_allowed_domains: list[str] = Field(default_factory=list)


class UnattendedConfig(_Strict):
    """计划任务/Webhook/spawn 派生时的 confirm 处理策略。"""

    default_strategy: Literal[
        "deny",
        "auto_approve",
        "defer_to_owner",
        "defer_to_inbox",
        "ask_owner",
    ] = "ask_owner"


class DeathSwitchConfig(_Strict):
    """连续 deny 触发只读模式（替代 v1 ``self_protection.death_switch_*``）。"""

    enabled: bool = True
    threshold: int = Field(default=3, ge=1, le=1000)
    total_multiplier: int = Field(default=3, ge=1, le=100)


class UserAllowlistConfig(_Strict):
    """用户持久化白名单（与 v1 同名同结构，C8 接入）。"""

    commands: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)


class AuditConfig(_Strict):
    """审计日志（v1 ``self_protection.audit_*`` 拆出来，独立配置）。"""

    enabled: bool = True
    log_path: str = "data/audit/policy_decisions.jsonl"
    include_chain: bool = False
    """dev only：把 12-step decision chain 也写入审计；生产关掉省 disk + perf。"""


# ---------------------------------------------------------------------------
# Top-level model
# ---------------------------------------------------------------------------


class PolicyConfigV2(_Strict):
    """完整 v2 安全配置。

    构造原则：
    - 所有子配置都有 default_factory，``PolicyConfigV2()`` 无参即 minimal-safe defaults
    - ``extra='forbid'`` 让 typo 立即报错（避免 v1 时代静默忽略未知字段）
    - ``ConfirmationMode`` / ``SessionRole`` / ``ApprovalClass`` 用 v2 enum，
      字符串自动 coerce，错值直接抛 ValidationError
    """

    enabled: bool = True
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    confirmation: ConfirmationConfig = Field(default_factory=ConfirmationConfig)
    session_role: SessionRoleConfig = Field(default_factory=SessionRoleConfig)
    safety_immune: SafetyImmuneConfig = Field(default_factory=SafetyImmuneConfig)
    owner_only: OwnerOnlyConfig = Field(default_factory=OwnerOnlyConfig)
    approval_classes: ApprovalClassesConfig = Field(default_factory=ApprovalClassesConfig)
    shell_risk: ShellRiskConfig = Field(default_factory=ShellRiskConfig)
    checkpoint: CheckpointConfig = Field(default_factory=CheckpointConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    unattended: UnattendedConfig = Field(default_factory=UnattendedConfig)
    death_switch: DeathSwitchConfig = Field(default_factory=DeathSwitchConfig)
    user_allowlist: UserAllowlistConfig = Field(default_factory=UserAllowlistConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)

    def expand_placeholders(self, *, cwd: Path | None = None) -> PolicyConfigV2:
        """展开 ``${CWD}`` / ``~`` 等占位符，返回新实例（不可变约定）。

        ``cwd`` 显式注入便于测试；默认 ``Path.cwd()``。
        """
        cwd = cwd or Path.cwd()
        cwd_str = str(cwd).replace("\\", "/")

        def expand(p: str) -> str:
            if p == "${CWD}":
                return cwd_str
            if p.startswith("~"):
                return str(Path(p).expanduser()).replace("\\", "/")
            return p

        data = self.model_dump()
        data["workspace"]["paths"] = [expand(p) for p in data["workspace"]["paths"]]
        data["safety_immune"]["paths"] = [expand(p) for p in data["safety_immune"]["paths"]]
        return PolicyConfigV2.model_validate(data)


__all__ = [
    "ApprovalClassesConfig",
    "AuditConfig",
    "CheckpointConfig",
    "ConfirmationConfig",
    "DeathSwitchConfig",
    "OwnerOnlyConfig",
    "PolicyConfigV2",
    "SafetyImmuneConfig",
    "SandboxConfig",
    "SessionRoleConfig",
    "ShellRiskConfig",
    "UnattendedConfig",
    "UserAllowlistConfig",
    "WorkspaceConfig",
]

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

import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, field_validator

from .enums import ApprovalClass, ConfirmationMode, SessionRole

# C16 Phase B：所有从 YAML 来的 bool 字段一律走严格模式——拒绝 `"yes"` / `"no"`
# / `1` / `0` 这种隐式 coercion，防止 v1 时代 ``bool("no") = True`` 的静默错。
# pydantic v2 的 ``Strict()`` 元数据让该字段只接受 ``True`` / ``False`` 字面量。
_StrictBool = Annotated[bool, Strict()]

_MAX_REGEX_LEN: int = 200
_MAX_REGEX_LIST_LEN: int = 64
_MAX_PATH_LEN: int = 4096


def _validate_regex_list(patterns: list[str]) -> list[str]:
    """Compile every pattern + cap length / count.

    Tightens the ``shell_risk.custom_*`` and ``excluded_patterns`` surface:
    a malformed regex (``[unclosed`` etc.) would historically crash deep
    inside the classifier at first match; we'd rather fail at load time
    with a clean ValidationError. Length / count caps act as a ReDoS
    budget — operators with hostile inputs are not the threat model, but
    a 10k-char nested-group regex copied from the internet still hangs
    the classifier.
    """
    if len(patterns) > _MAX_REGEX_LIST_LEN:
        raise ValueError(
            f"regex list has {len(patterns)} entries (max {_MAX_REGEX_LIST_LEN})"
        )
    for idx, pat in enumerate(patterns):
        if not isinstance(pat, str):
            raise ValueError(f"entry {idx} is not a string: {type(pat).__name__}")
        if len(pat) > _MAX_REGEX_LEN:
            raise ValueError(
                f"entry {idx} has length {len(pat)} (max {_MAX_REGEX_LEN}): {pat[:40]}…"
            )
        try:
            re.compile(pat)
        except re.error as exc:
            raise ValueError(f"entry {idx} is not a valid regex: {pat!r} ({exc})") from exc
    return patterns


def _validate_safe_path(value: str) -> str:
    """C16 Phase B：拒绝 ``..`` 段 + 拒绝异常长度。

    用于 ``audit.log_path`` / ``checkpoint.snapshot_dir`` 等"输出到固定根下"
    的字段。``workspace.paths`` / ``safety_immune.paths`` **不**用这个验证器，
    它们允许操作员指向父目录 / 兄弟项目。
    """
    if not isinstance(value, str):
        raise ValueError(f"must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError("must be non-empty")
    if len(value) > _MAX_PATH_LEN:
        raise ValueError(f"path length {len(value)} exceeds {_MAX_PATH_LEN}")
    parts = value.replace("\\", "/").split("/")
    if ".." in parts:
        raise ValueError(
            f"path traversal segment '..' is not allowed in this field: {value!r}"
        )
    return value


def _validate_loose_path(value: str) -> str:
    """C16 Phase B：宽松路径验证（非空 + 长度上限），允许 ``..``。

    用于 ``workspace.paths`` / ``safety_immune.paths``——操作员合法理由要
    指向父目录或工作区外的位置（共享代码库、兄弟项目）。
    """
    if not isinstance(value, str):
        raise ValueError(f"must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError("must be non-empty")
    if len(value) > _MAX_PATH_LEN:
        raise ValueError(f"path length {len(value)} exceeds {_MAX_PATH_LEN}")
    return value

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

    @field_validator("paths", mode="after")
    @classmethod
    def _validate_each_path(cls, v: list[str]) -> list[str]:
        # C16 Phase B：workspace.paths 允许 .. （父目录场景），仅长度限制。
        return [_validate_loose_path(p) for p in v]


class ConfirmationConfig(_Strict):
    """确认门配置（v2 mode 只有 5 档，详见 enums.ConfirmationMode）。"""

    mode: ConfirmationMode = ConfirmationMode.DEFAULT
    timeout_seconds: int = Field(default=60, ge=1, le=86400)
    default_on_timeout: Literal["allow", "deny"] = "deny"
    confirm_ttl: float = Field(default=120.0, ge=0.0, le=86400.0)

    # C18 Phase B：批量确认聚合窗口（秒）。
    #
    # 当 ``>0`` 时，UI 检测到同一 session 在此窗口内有 ≥2 个待 confirm
    # 时显示"全部允许 / 全部拒绝"按钮，配合 ``POST /api/chat/security-
    # confirm/batch`` 一次性 resolve 所有窗内待审项。
    #
    # 默认 ``0``（关）——参考 4 个邻近开源项目（claude-code / hermes /
    # QwenPaw / openclaw）均没有把多项聚合作为默认行为，避免"用户没看清就
    # 批量放行"。运维场景 / 信任度高的 owner 可在 POLICIES.yaml 显式开。
    aggregation_window_seconds: float = Field(default=0.0, ge=0.0, le=600.0)


class SessionRoleConfig(_Strict):
    """会话角色默认值。每个 session 启动时若没指定 role，用此默认。"""

    default: SessionRole = SessionRole.AGENT


class SafetyImmuneConfig(_Strict):
    """绝不允许 trust 模式静默放行的关键路径白名单。

    替代 v1 ``zones.protected`` + ``zones.forbidden`` + ``self_protection.protected_dirs``
    三处的合集（loader.migration 在迁移时 union + dedupe）。
    """

    paths: list[str] = Field(default_factory=list)

    @field_validator("paths", mode="after")
    @classmethod
    def _validate_each_path(cls, v: list[str]) -> list[str]:
        return [_validate_loose_path(p) for p in v]


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

    enabled: _StrictBool = True
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

    @field_validator(
        "custom_critical",
        "custom_high",
        "custom_medium",
        "excluded_patterns",
        mode="after",
    )
    @classmethod
    def _check_regex_lists(cls, v: list[str]) -> list[str]:
        # C16 Phase B：编译每条 regex、限制长度 / 条数（ReDoS 兜底）。
        return _validate_regex_list(v)


class CheckpointConfig(_Strict):
    """文件快照配置。"""

    enabled: _StrictBool = True
    max_snapshots: int = Field(default=50, ge=0, le=10000)
    snapshot_dir: str = "data/checkpoints"

    @field_validator("snapshot_dir", mode="after")
    @classmethod
    def _check_snapshot_dir(cls, v: str) -> str:
        return _validate_safe_path(v)


class SandboxConfig(_Strict):
    """沙箱配置（v1 ``sandbox.network.*`` 在 v2 扁平化为 ``network_*``）。"""

    enabled: _StrictBool = False
    backend: Literal["auto", "docker", "firejail", "wsl", "none"] = "auto"
    sandbox_risk_levels: list[Literal["MEDIUM", "HIGH", "CRITICAL"]] = Field(
        default_factory=lambda: ["HIGH"]
    )
    exempt_commands: list[str] = Field(default_factory=list)
    network_allow_in_sandbox: _StrictBool = False
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

    enabled: _StrictBool = True
    threshold: int = Field(default=3, ge=1, le=1000)
    total_multiplier: int = Field(default=3, ge=1, le=100)


class UserAllowlistConfig(_Strict):
    """用户持久化白名单（与 v1 同名同结构，C8 接入）。

    C16 Phase B：保持 ``list[dict[str, Any]]`` 不变——这两个列表是 C8 持久化
    的 user grant，schema 由 C8 自管理，不在 C16 收紧范围。后续把它换成
    结构化模型时一起改 C8 的写入路径。
    """

    commands: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)


class AuditConfig(_Strict):
    """审计日志（v1 ``self_protection.audit_*`` 拆出来，独立配置）。"""

    enabled: _StrictBool = True
    log_path: str = "data/audit/policy_decisions.jsonl"
    include_chain: _StrictBool = True
    """C16: 每条记录附 ``prev_hash`` + ``row_hash``，使任何后续篡改可被检测。

    历史上这个字段曾被规划成"是否写 12-step decision chain"，但从未真正
    被消费。C16 复用为审计链开关，默认开启；既有审计文件没有 chain 字段
    的"legacy prefix"会被 verifier 单独标记，不会触发 tamper 告警。
    """

    @field_validator("log_path", mode="after")
    @classmethod
    def _check_log_path(cls, v: str) -> str:
        return _validate_safe_path(v)


class HotReloadConfig(_Strict):
    """POLICIES.yaml 文件热更新（C18 Phase A）。

    监听 ``identity/POLICIES.yaml`` mtime，变化时尝试重建 PolicyEngineV2。
    新配置校验失败 → 保留 last-known-good，写 ``audit.policy_decisions.jsonl``
    一条 ``policy_hot_reload`` 事件（``ok=false`` + reason）。

    默认 **关闭**：参考 4 个邻近开源项目（claude-code / hermes / QwenPaw /
    openclaw）的实践，没有一个把"文件即改即生效"作为默认——突变行为对
    既有用户体验风险高，应当 opt-in。运维场景（k8s configmap mount、CI
    rolling）打开即可。
    """

    enabled: _StrictBool = False
    poll_interval_seconds: float = 5.0
    """轮询间隔（秒）。fs 写入到 ``rebuild_engine_v2`` 的最大延迟 = 此值；
    设过小会浪费 CPU（stat 调用），设过大 reload 体感慢。默认 5s 平衡。"""

    debounce_seconds: float = 0.5
    """检测到 mtime 变化后等多久再读文件——避开编辑器"先 truncate 再写"
    中间态。参考 openclaw chokidar ``awaitWriteFinish`` 的 200ms 阈值，
    Python 这边给一点余量。"""

    @field_validator("poll_interval_seconds", mode="after")
    @classmethod
    def _check_poll(cls, v: float) -> float:
        if v < 0.5:
            raise ValueError("poll_interval_seconds must be >= 0.5")
        if v > 3600:
            raise ValueError("poll_interval_seconds must be <= 3600")
        return v

    @field_validator("debounce_seconds", mode="after")
    @classmethod
    def _check_debounce(cls, v: float) -> float:
        if v < 0:
            raise ValueError("debounce_seconds must be >= 0")
        if v > 60:
            raise ValueError("debounce_seconds must be <= 60")
        return v


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

    enabled: _StrictBool = True
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
    hot_reload: HotReloadConfig = Field(default_factory=HotReloadConfig)

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
    "HotReloadConfig",
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

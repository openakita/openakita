"""PolicyContext: per-call execution context.

PolicyContext 是 PolicyEngineV2 决策的"环境"对象，承载：
- session 标识 / workspace_roots
- 渠道（desktop / im:* / cli / api / webhook）与 owner 标识
- session_role × confirmation_mode 两层正交 mode
- unattended 标志与策略
- delegate_chain（多 agent 嵌套时透传到 root_user）
- replay_authorizations / trusted_path_overrides（C5 填）
- safety_immune_paths（启动时合并 POLICIES.yaml + 默认 9 类）

跨 spawn 异步任务的透传通过 ContextVar 完成（R5-16）。子 agent 显式调用
derive_child() 派生子上下文，保留 root_user_id 与 safety_immune。

C1 阶段：Session dataclass 尚未加 session_role/confirmation_mode 字段
（C8 完成）。本模块通过 getattr + default 兼容 v1 session，任何 import
方都不会因此 break。
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import Any

from .enums import LEGACY_MODE_ALIASES, ConfirmationMode, SessionRole


@dataclass(slots=True, frozen=True)
class ReplayAuthorization:
    """30s TTL 内复读消息免 confirm 的授权记录（C5）。

    对齐 v1 ``risk_authorized_replay`` session metadata 形态：
    {expires_at, original_message, confirmation_id, operation}。

    **不可变 dataclass**（``frozen=True``）—— 授权一经发出就不许 in-place 改字段
    （有效期/绑定的消息）。要更新只能整条替换。

    **engine 只读不写**：engine.step 7 检查匹配后返回信号；实际"消费"
    （从 session metadata 移除）由 tool_executor / chat handler 调用方做，避免
    engine 持有 session 副作用。
    """

    expires_at: float
    """epoch seconds。``time.time() < expires_at`` 才有效。"""

    original_message: str = ""
    """原始 user message。匹配时按子串/相等判断，调用方可定制 matcher。"""

    confirmation_id: str = ""
    """对应 ask_user 的 confirmation 记录 id（审计 + 单次消费定位）。"""

    operation: str = ""
    """绑定的 OperationKind 值（write/delete/...），可空表示"任意写操作"。"""

    def is_active(self, *, now: float | None = None) -> bool:
        return (now or time.time()) < self.expires_at


@dataclass(slots=True, frozen=True)
class TrustedPathOverride:
    """session 内 sticky 的路径/操作信任授权（C5）。

    对齐 v1 ``trusted_paths.grant_session_trust`` 写入的 rule 形态：
    {operation, path_pattern, expires_at, granted_at}。

    **不可变 dataclass**：每个授权一经记录不可变；过期后整条丢弃。

    与 ``ReplayAuthorization`` 区别：
    - ReplayAuthorization 是 ``单次消费`` 短期授权（30s）
    - TrustedPathOverride 是 ``sticky`` session 内有效（直到过期或会话结束）
    """

    operation: str | None = None
    """绑定的操作（write/delete/...）；None 表示任意操作。"""

    path_pattern: str | None = None
    """路径正则；None 表示任意路径（仅按 operation 匹配）。"""

    expires_at: float | None = None
    """epoch seconds；None 表示 session 内永久有效。"""

    granted_at: float = 0.0
    """grant 写入时间，审计用。"""

    def is_active(self, *, now: float | None = None) -> bool:
        if self.expires_at is None:
            return True
        return (now or time.time()) < self.expires_at


@dataclass(slots=True)
class PolicyContext:
    """PolicyEngineV2 决策上下文。"""

    session_id: str
    workspace_roots: tuple[Path, ...] = (Path("."),)
    workspace: InitVar[Path | str | None] = None

    channel: str = "desktop"
    """desktop / cli / api / webhook / im:telegram / im:feishu / ..."""

    is_owner: bool = True
    """默认 True（CLI/桌面均视为 owner）；IM 渠道必须按 sender_user_id 显式判断。"""

    root_user_id: str | None = None
    """multi-agent 嵌套时，confirm 冒泡到 root 用户。"""

    session_role: SessionRole = SessionRole.AGENT
    confirmation_mode: ConfirmationMode = ConfirmationMode.DEFAULT

    is_unattended: bool = False
    """scheduled task / spawn_agent 异步派生 / webhook → True。CLI/桌面/IM 同步 → False。"""

    unattended_strategy: str = ""
    """空字符串 → engine 用 ``config.unattended.default_strategy`` 兜底；
    显式设非空（"deny"/"auto_approve"/"defer_to_owner"/"defer_to_inbox"/"ask_owner"）
    则覆盖 config 默认（per-call 精细控制，如某 webhook 想 deny，某 schedule 想 auto）。"""

    delegate_chain: list[str] = field(default_factory=list)
    """["root", "specialist_a", ...]。append in derive_child()。"""

    replay_authorizations: list[ReplayAuthorization] = field(default_factory=list)
    """30s 内复读消息免 confirm 的授权快照。

    一般来自 ``session.get_metadata("risk_authorized_replay")``。engine 只读不写。
    异构输入（v1 dict 形态）由 ``from_session`` 内 ``_coerce_replay_auth``
    统一转 dataclass。
    """

    trusted_path_overrides: list[TrustedPathOverride] = field(default_factory=list)
    """用户 allow_session 后 session 内的路径白名单。

    一般来自 ``session.get_metadata("trusted_path_overrides").rules``。engine
    只读不写。异构输入由 ``_coerce_trusted_path`` 统一转 dataclass。
    """

    safety_immune_paths: tuple[str, ...] = ()
    """启动时 union from POLICIES.yaml + identity 默认 9 类（C5 起 engine 也合 config）。"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """IM 适配器存 group_id / sender 等自由字段。"""

    user_message: str = ""
    """当前轮 user message（C5 用于 replay/trusted_path 匹配）。空表示无 message
    上下文（CLI 单轮工具调用、内部 spawn）。"""

    evolution_fix_id: str | None = None
    """C15 §17.1 — 当本次决策发生在 Evolution self_check 修复窗口内时，
    set 为 self_check 生成的 fix id。engine 据此把决策追加到
    ``data/audit/evolution_decisions.jsonl``，方便 operator 复盘
    Evolution 究竟尝试改了什么。

    None 表示 **不在** evolution 窗口内（默认 99.99% 路径）。Phase C v1
    仅做审计，**不**改变 safety_immune 判定；进一步松绑（让 Evolution
    在窗口内可写 identity/runtime 等）作为后续 commit 处理，避免一次性
    扩大攻击面。"""

    def __post_init__(self, workspace: Path | str | None = None) -> None:
        """构造后归一：把 string 形态的 role/mode 强制转 enum。

        Python dataclass 不像 Pydantic 自带 coercion；如果调用方拿了
        ``cfg.confirmation.mode``（在 use_enum_values=True 下返回 str）直接
        构造 PolicyContext，``confirmation_mode`` 实际是 str，engine 后续
        ``ctx.confirmation_mode.value`` 会 AttributeError。

        本方法在所有入口（直接 ctor / from_session / derive_child）都会跑，
        提供 boundary 健壮性 + 单点修复。
        """
        if not isinstance(self.session_role, SessionRole):
            self.session_role = _coerce_role(self.session_role)
        if not isinstance(self.confirmation_mode, ConfirmationMode):
            self.confirmation_mode = _coerce_mode(self.confirmation_mode)
        roots_raw = workspace if workspace is not None else self.workspace_roots
        self.workspace_roots = _coerce_workspace_roots(roots_raw)

    @classmethod
    def from_session(cls, session: Any, **overrides: Any) -> PolicyContext:
        """从 sessions/session.py 的 Session 构造 PolicyContext。

        C1 阶段 Session 还没加 session_role / confirmation_mode 字段，本方法
        用 getattr + default 占位；C8 给 Session 加字段后会无缝读取。

        C21 P1-1 修复：``Session`` 在 C8 给"会话级 confirmation_mode 覆盖"
        起的字段名是 ``confirmation_mode_override``（None 表示"用全局"），
        而本方法原先只读 ``getattr(session, "confirmation_mode", ...)``——
        production 主路径 ``build_policy_context`` (policy_v2/adapter.py)
        早就显式 honor 了 override，但 ``from_session`` 残留导致：

        - 直接调用方拿不到 session 的 override，永远走全局默认
        - 测试假 Session 用 ``confirmation_mode`` 字段名通过，掩盖了 prod 不一致

        正确顺序：``confirmation_mode_override``（C8 字段名，优先）→
        ``confirmation_mode``（兼容假 Session）→ ``None``（走 _coerce_mode
        的 DEFAULT 默认）。
        """
        session_id = (
            getattr(session, "id", None) or getattr(session, "session_id", None) or "unknown"
        )
        # workspace_roots = config.workspace.paths ∪ session.workspace。Session
        # 自带的工作目录只是 union 的额外 root，不允许替换/缩小用户在安全页
        # 配置的工作区集合——这与 build_policy_context() 的语义保持一致。
        try:
            from .global_engine import get_config_v2

            cfg = get_config_v2()
            config_roots = tuple(Path(p) for p in cfg.workspace.paths)
        except Exception:
            config_roots = ()
        session_ws = getattr(session, "workspace_roots", None) or getattr(
            session, "workspace", None
        )
        roots_seq: list[Path] = []
        seen: set[str] = set()
        for raw in (*config_roots, session_ws):
            if not raw:
                continue
            for p in _coerce_workspace_roots(raw):
                k = str(p)
                if k not in seen:
                    seen.add(k)
                    roots_seq.append(p)
        workspace_raw = tuple(roots_seq) if roots_seq else (Path("."),)
        meta = dict(getattr(session, "metadata", {}) or {})

        confirmation_raw = (
            getattr(session, "confirmation_mode_override", None)
            if getattr(session, "confirmation_mode_override", None) is not None
            else getattr(session, "confirmation_mode", None)
        )

        ctx = cls(
            session_id=str(session_id),
            workspace_roots=workspace_raw,
            channel=str(meta.get("channel", "desktop")),
            is_owner=bool(meta.get("is_owner", True)),
            root_user_id=meta.get("root_user_id"),
            session_role=_coerce_role(getattr(session, "session_role", None)),
            confirmation_mode=_coerce_mode(confirmation_raw),
            # C12 §14.2: prefer first-class Session fields (added in C12) but
            # fall back to metadata for back-compat with sessions persisted
            # before promotion. ``getattr`` chain returns False/"" if absent →
            # metadata value used; explicit False / "" on first-class field also
            # wins (intentional opt-out is preserved).
            is_unattended=bool(
                getattr(session, "is_unattended", None)
                if getattr(session, "is_unattended", None) is not None
                else meta.get("is_unattended", False)
            ),
            unattended_strategy=str(
                getattr(session, "unattended_strategy", None)
                or meta.get("unattended_strategy", "")
            ),
            delegate_chain=list(meta.get("delegate_chain", [])),
            replay_authorizations=_coerce_replay_auths(meta.get("replay_authorizations")),
            trusted_path_overrides=_coerce_trusted_paths(meta.get("trusted_path_overrides")),
            safety_immune_paths=tuple(meta.get("safety_immune_paths", ())),
            metadata=meta,
            user_message=str(meta.get("user_message", "")),
        )

        for key, value in overrides.items():
            setattr(ctx, key, value)

        return ctx

    def derive_child(self, child_session_id: str, child_agent_name: str) -> PolicyContext:
        """sub-agent 派生子上下文，保留 root_user / safety_immune，append delegate_chain。

        child confirm 上浮到 root_user_id；safety_immune_paths 不可被 child override；
        replay_authorizations / trusted_path_overrides 复制（独立演化，避免共享可变状态）。
        ReplayAuthorization / TrustedPathOverride 都是 frozen dataclass，
        共享引用安全。
        """
        chain = list(self.delegate_chain) + [child_agent_name]
        return PolicyContext(
            session_id=child_session_id,
            workspace_roots=self.workspace_roots,
            channel=self.channel,
            is_owner=self.is_owner,
            root_user_id=self.root_user_id or self.session_id,
            session_role=self.session_role,
            confirmation_mode=self.confirmation_mode,
            is_unattended=self.is_unattended,
            unattended_strategy=self.unattended_strategy,
            delegate_chain=chain,
            replay_authorizations=list(self.replay_authorizations),
            trusted_path_overrides=list(self.trusted_path_overrides),
            safety_immune_paths=self.safety_immune_paths,
            metadata=dict(self.metadata),
            user_message=self.user_message,
            # C15 §17.1 — sub-agents derived during an Evolution self-fix
            # window must inherit the marker so their tool decisions also
            # land in ``evolution_decisions.jsonl`` with the same fix_id.
            evolution_fix_id=self.evolution_fix_id,
        )


def _coerce_replay_auths(raw: Any) -> list[ReplayAuthorization]:
    """把 v1 dict 形态 / 已是 dataclass 的混合输入归一为 ``list[ReplayAuthorization]``。

    - None / 非可迭代 → []
    - dict 单条 → [coerce_one]
    - list 内：dataclass 直接收，dict 转 dataclass，其他类型跳过 + WARN-friendly silent
    """
    if raw is None:
        return []
    items: list[Any]
    if isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, list | tuple):
        items = list(raw)
    else:
        return []

    out: list[ReplayAuthorization] = []
    for item in items:
        if isinstance(item, ReplayAuthorization):
            out.append(item)
            continue
        if isinstance(item, dict):
            try:
                out.append(
                    ReplayAuthorization(
                        expires_at=float(item.get("expires_at", 0.0)),
                        original_message=str(item.get("original_message", "")),
                        confirmation_id=str(item.get("confirmation_id", "")),
                        operation=str(item.get("operation", "")),
                    )
                )
            except (TypeError, ValueError):
                # 跳过 malformed 条目（容错）
                continue
    return out


def _coerce_trusted_paths(raw: Any) -> list[TrustedPathOverride]:
    """归一 trusted_path_overrides。

    支持两种输入形态：
    - ``[TrustedPathOverride(...), ...]`` —— 已结构化
    - ``{"rules": [{operation, path_pattern, expires_at, granted_at}, ...]}``
      —— v1 ``trusted_paths.get_session_overrides`` 返回形态
    - ``[{...}, ...]`` —— 直接 rule 列表
    """
    if raw is None:
        return []
    items: list[Any]
    if isinstance(raw, dict):
        items = list(raw.get("rules") or [])
    elif isinstance(raw, list | tuple):
        items = list(raw)
    else:
        return []

    out: list[TrustedPathOverride] = []
    for item in items:
        if isinstance(item, TrustedPathOverride):
            out.append(item)
            continue
        if isinstance(item, dict):
            expires_raw = item.get("expires_at")
            try:
                expires = float(expires_raw) if expires_raw is not None else None
            except (TypeError, ValueError):
                expires = None
            out.append(
                TrustedPathOverride(
                    operation=(str(item["operation"]).lower() if item.get("operation") else None),
                    path_pattern=item.get("path_pattern") or None,
                    expires_at=expires,
                    granted_at=float(item.get("granted_at", 0.0) or 0.0),
                )
            )
    return out


def _coerce_workspace_roots(raw: Any) -> tuple[Path, ...]:
    """Normalize workspace roots to a non-empty tuple of Path objects."""
    if raw is None:
        items: list[Any] = []
    elif isinstance(raw, (str, Path)):
        items = [raw]
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = [raw]
    roots: list[Path] = []
    for item in items:
        try:
            text = str(item)
            if text:
                roots.append(Path(text))
        except Exception:
            continue
    return tuple(roots or [Path(".")])


def primary_workspace_root(ctx: PolicyContext) -> Path:
    """Return the first workspace root for output/audit locations only."""
    return ctx.workspace_roots[0] if ctx.workspace_roots else Path(".")


def _coerce_role(value: Any) -> SessionRole:
    if isinstance(value, SessionRole):
        return value
    if isinstance(value, str):
        try:
            return SessionRole(value)
        except ValueError:
            pass
    return SessionRole.AGENT


def _coerce_mode(value: Any) -> ConfirmationMode:
    if isinstance(value, ConfirmationMode):
        return value
    if isinstance(value, str):
        normalized = LEGACY_MODE_ALIASES.get(value, value)
        try:
            return ConfirmationMode(normalized)
        except ValueError:
            pass
    return ConfirmationMode.DEFAULT


_current_policy_context: ContextVar[PolicyContext | None] = ContextVar(
    "openakita_policy_context", default=None
)


def set_current_context(ctx: PolicyContext | None):
    """设置当前任务的 PolicyContext。返回 token（用于 reset）。

    跨 asyncio.create_task 的透传由 Python 标准 ContextVar 语义保证。
    跨 spawn_agent 进程/线程边界时，由调用方显式序列化 ctx 字段（plan §15）。
    """
    return _current_policy_context.set(ctx)


def get_current_context() -> PolicyContext | None:
    """获取当前 ContextVar 中的 PolicyContext，无则返回 None。"""
    return _current_policy_context.get()


def reset_current_context(token: Any) -> None:
    """复位 ContextVar（与 set_current_context 配对）。"""
    _current_policy_context.reset(token)

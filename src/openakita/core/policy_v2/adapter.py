"""policy_v2 决策入口 adapter（C6 引入 / C8b-6b 完成 v1 解耦）。

C8b-6a 之后所有生产 caller（permission.py / reasoning_engine.py × 2）已直接
消费 ``PolicyDecisionV2``。本模块当前只剩两类 helper：

公共 API
========

- ``evaluate_via_v2(tool, params, *, mode='agent', extra_ctx=None)``
  执行 v2 工具决策评估，返回 ``PolicyDecisionV2``。
- ``evaluate_message_intent_via_v2(message, risk_intent, ...)``
  执行 v2 消息层 RiskGate 评估，返回 ``PolicyDecisionV2``。
- ``V2_TO_V1_DECISION`` / ``build_policy_name`` / ``build_metadata_for_legacy_callers``
  C8b-6a 公开的 4 档语义翻译 helper，给 ``permission.PermissionDecision``
  保留 v1 4 档 behavior 字符串契约（"allow" / "deny" / "confirm" / "sandbox"）+
  policy_name 字符串格式（``"policy_v2:<step_name>"``）。
- ``mode_to_session_role(mode)``：v1 mode → v2 SessionRole 映射。
- ``build_policy_context(...)``：构造 PolicyContext 的便捷入口。

设计要点
========

1. **DEFER → CONFIRM 降级**：v2 ``DecisionAction.DEFER`` 是 unattended/IM 场景特有；
   ``V2_TO_V1_DECISION`` 把它降为 ``"confirm"``，permission.py 桥接到 4 档 enum。

2. **Adapter 层 fail-closed**：v2 ``PolicyEngineV2._evaluate_tool_call_impl``
   已有 top-level fail-safe（exception → DENY），但 ``get_engine_v2()`` /
   ctx 构造仍可能抛。adapter 包一层 fail-closed：风险工具异常 → DENY；
   安全工具（read 类前缀）异常 → ALLOW（与 v1 ``permission.check_permission``
   同语义，避免 read_file 被引擎 bug 拖死）。

3. **mode 翻译**：plan/ask/coordinator 由 ``permission.check_mode_permission``
   先拦截，所以 adapter 通常只需处理 ``mode='agent'``。non-agent mode 仍传给
   v2，但当前不影响决策（v2 的 SessionRole 来自 ctx，与 v1 mode 不耦合）。

4. **PolicyContext 来源**：
   - 优先 ``get_current_context()`` ContextVar（C7 由 agent 入口设置）。
   - 缺省时构造一个 minimal ctx：workspace=cwd、role=AGENT、mode=config 默认。
   - 显式 ``extra_ctx`` 字段允许调用方覆盖。

历史 helper（C8b-6b 已删）
========================

- ``decision_to_v1_result`` / ``evaluate_via_v2_to_v1_result`` /
  ``_v2_action_to_v1_decision``：C6→C8b-6a 的过渡桥接，把 v2 ``PolicyDecisionV2``
  翻译成 v1 ``PolicyResult`` shape。C8b-6a 后所有 caller 直接消费 v2 类型，
  C8b-6b 随 v1 ``policy.py`` 整文件一起删。git history 留供 archaeologist。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .context import (
    PolicyContext,
    ReplayAuthorization,
    TrustedPathOverride,
    get_current_context,
)
from .enums import ConfirmationMode, DecisionAction, SessionRole
from .models import MessageIntentEvent, ToolCallEvent

if TYPE_CHECKING:
    from .engine import PolicyEngineV2
    from .models import PolicyDecisionV2

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# v1 risk fail-closed prefixes（与 permission._FAIL_CLOSED_TOOL_PREFIXES 对齐）
# 重复定义避免 adapter ↔ permission 形成 import 环；两边 drift 时由 C6.7 smoke
# test 抓到。
# ---------------------------------------------------------------------------

_FAIL_CLOSED_TOOL_PREFIXES = (
    "run_",
    "delete_",
    "edit_",
    "write_",
    "rename_",
    "delegate_",
    "spawn_",
    "create_agent",
    "call_mcp_",
    "browser_",
    "desktop_",
)
_EDIT_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "replace_in_file",
        "create_file",
        "delete_file",
        "rename_file",
    }
)


def _should_fail_closed(tool_name: str) -> bool:
    if tool_name in _EDIT_TOOLS:
        return True
    return tool_name.startswith(_FAIL_CLOSED_TOOL_PREFIXES)


# ---------------------------------------------------------------------------
# PolicyContext fallback
# ---------------------------------------------------------------------------


def _build_fallback_context(
    *,
    session_id: str = "policy_v2_adapter_fallback",
    user_message: str = "",
) -> PolicyContext:
    """No-ContextVar 场景下用的最小 PolicyContext。

    workspace 取 cwd；role=AGENT；confirmation_mode 由 ``get_config_v2`` 决定
    （默认是 ``DEFAULT``）。
    """
    try:
        from .global_engine import get_config_v2

        cfg = get_config_v2()
        mode_value = cfg.confirmation.mode
        if not isinstance(mode_value, ConfirmationMode):
            try:
                mode = ConfirmationMode(mode_value)
            except ValueError:
                mode = ConfirmationMode.DEFAULT
        else:
            mode = mode_value
    except Exception:
        mode = ConfirmationMode.DEFAULT

    return PolicyContext(
        session_id=session_id,
        workspace=Path(os.getcwd()),
        session_role=SessionRole.AGENT,
        confirmation_mode=mode,
        user_message=user_message,
    )


def _resolve_context(
    *,
    extra_ctx: PolicyContext | None,
    user_message: str,
) -> PolicyContext:
    """优先用调用方显式传入的 ctx，其次 ContextVar，最后 fallback。

    extra_ctx 优先级最高，让 reasoning_engine 等调用点能精细控制
    （比如强制 user_message 用于 replay 匹配）。
    """
    if extra_ctx is not None:
        return extra_ctx
    current = get_current_context()
    if current is not None:
        if user_message and not current.user_message:
            # 当前 ctx 没带 user_message，按需补一份（不修改原 ctx，复制一个）
            return PolicyContext(
                session_id=current.session_id,
                workspace=current.workspace,
                channel=current.channel,
                is_owner=current.is_owner,
                root_user_id=current.root_user_id,
                session_role=current.session_role,
                confirmation_mode=current.confirmation_mode,
                is_unattended=current.is_unattended,
                unattended_strategy=current.unattended_strategy,
                delegate_chain=list(current.delegate_chain),
                replay_authorizations=list(current.replay_authorizations),
                trusted_path_overrides=list(current.trusted_path_overrides),
                safety_immune_paths=current.safety_immune_paths,
                metadata=dict(current.metadata),
                user_message=user_message,
            )
        return current
    return _build_fallback_context(user_message=user_message)


# ---------------------------------------------------------------------------
# Engine accessor
# ---------------------------------------------------------------------------


def _get_engine() -> PolicyEngineV2:
    """单点抽取以便 patch（测试）。"""
    from .global_engine import get_engine_v2

    return get_engine_v2()


# ---------------------------------------------------------------------------
# C7：PolicyContext 构造器（agent.py 入口处用）
# ---------------------------------------------------------------------------

# v1 mode → v2 SessionRole（v1 agent.py 仍用字符串 mode；v2 SessionRole 是枚举）
_MODE_TO_ROLE: dict[str, SessionRole] = {
    "agent": SessionRole.AGENT,
    "plan": SessionRole.PLAN,
    "ask": SessionRole.ASK,
    "coordinator": SessionRole.COORDINATOR,
}


def mode_to_session_role(mode: str | None) -> SessionRole:
    """v1 ``mode`` 字符串 → v2 ``SessionRole``。

    未知 mode → ``AGENT``（保守默认；v1 行为是 "未指定 = agent"）。
    """
    if not mode:
        return SessionRole.AGENT
    return _MODE_TO_ROLE.get(mode.lower(), SessionRole.AGENT)


def _coerce_replay_auths(stamp: object) -> list[ReplayAuthorization]:
    """从 v1 session metadata 的 ``risk_authorized_replay`` 形态构 dataclass list。

    v1 写的是单个 dict（{expires_at, original_message, confirmation_id, operation}），
    也可能是已转好的 list。容错统一为 list[ReplayAuthorization]。
    """
    if stamp is None:
        return []
    items: list[object] = []
    if isinstance(stamp, dict):
        items = [stamp]
    elif isinstance(stamp, (list, tuple)):
        items = list(stamp)
    else:
        return []

    out: list[ReplayAuthorization] = []
    for item in items:
        if isinstance(item, ReplayAuthorization):
            out.append(item)
            continue
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                ReplayAuthorization(
                    expires_at=float(item.get("expires_at", 0) or 0),
                    original_message=str(item.get("original_message") or ""),
                    confirmation_id=str(item.get("confirmation_id") or ""),
                    operation=str(item.get("operation") or ""),
                )
            )
        except (TypeError, ValueError) as exc:
            logger.debug(
                "[PolicyV2 adapter] skipping malformed replay auth: %r (%s)",
                item,
                exc,
            )
    return out


def _coerce_trusted_paths(rules: object) -> list[TrustedPathOverride]:
    """从 ``trusted_path_overrides.rules`` 形态构 dataclass list。

    v1 形态见 ``trusted_paths.grant_session_trust``：
    ``{"operation": "write", "path_pattern": "/tmp/*", "expires_at": 12345.0,
       "granted_at": 12345.0}``。
    """
    if not rules:
        return []
    if isinstance(rules, dict):
        rules = [rules]
    if not isinstance(rules, (list, tuple)):
        return []

    out: list[TrustedPathOverride] = []
    for rule in rules:
        if isinstance(rule, TrustedPathOverride):
            out.append(rule)
            continue
        if not isinstance(rule, dict):
            continue
        try:
            out.append(
                TrustedPathOverride(
                    operation=rule.get("operation"),
                    path_pattern=rule.get("path_pattern"),
                    expires_at=(
                        float(rule["expires_at"])
                        if rule.get("expires_at") is not None
                        else None
                    ),
                    granted_at=float(rule.get("granted_at", 0) or 0),
                )
            )
        except (TypeError, ValueError) as exc:
            logger.debug(
                "[PolicyV2 adapter] skipping malformed trusted path: %r (%s)",
                rule,
                exc,
            )
    return out


def build_policy_context(
    *,
    session: object | None = None,
    session_id: str = "",
    workspace: Path | str | None = None,
    mode: str = "agent",
    is_unattended: bool = False,
    is_owner: bool = True,
    channel: str = "desktop",
    root_user_id: str | None = None,
    user_message: str = "",
    delegate_chain: list[str] | None = None,
    extra_metadata: dict[str, object] | None = None,
    parent_ctx: PolicyContext | None = None,
    child_agent_name: str | None = None,
) -> PolicyContext:
    """从 v1 session 对象 + 入参构造 ``PolicyContext`` 供 v2 决策使用。

    优先字段来源：
    - ``session_id`` / ``workspace`` / ``mode`` 显式入参（agent.py 入口已知）
    - ``confirmation_mode`` 来自 ``get_config_v2().confirmation.mode``
    - ``replay_authorizations`` / ``trusted_path_overrides`` 从 session.get_metadata 读
    - 其余字段使用安全默认（is_owner=True/desktop channel）

    设计原则：
    - **engine read-only**：ctx 只承载 session 的快照副本；engine 决策时不会
      回写 session（消费侧由 agent.py 在 ``_consume_risk_authorization`` 完成，
      与 ContextVar 设值/重置解耦）。
    - **fail-soft**：session 可能是 None / dict / SessionContext 对象 / mock；
      所有 ``getattr`` / ``get_metadata`` 失败均退化为空 list 而非抛异常，
      避免 ctx 构造把 production 卡死。

    C13 多 agent 链路（``parent_ctx`` + ``child_agent_name``）：
    - 当 ``parent_ctx`` 非空（sub-agent 入口）：走 ``parent_ctx.derive_child(...)``
      派生 child ctx，并叠加本次 session/user_message 的本地视图。
    - 这样 root_user_id / delegate_chain / safety_immune_paths / replay /
      trusted_paths 全部由父继承而来，**不**被 sub-agent 重新构造时清空。
    - sub-agent 仍可通过 ``user_message=...`` 覆盖 user_message（用于本次工具
      调用的 replay 匹配），其余 immune 字段保持父值。
    """
    ws_path = Path(workspace) if workspace is not None else Path(os.getcwd())

    # C13 §15: sub-agent path — 父 ctx 已是 root 视图（root_user_id /
    # delegate_chain / safety_immune / replay / trusted_paths 全部就位），
    # 直接 derive_child + 本地字段叠加，不重新走全套 session 推断流程。
    if parent_ctx is not None:
        child_sid = session_id or parent_ctx.session_id
        child_name = (child_agent_name or "").strip() or "sub_agent"
        base = parent_ctx.derive_child(
            child_session_id=child_sid,
            child_agent_name=child_name,
        )
        # === sub-agent **可以**有自己的视图的字段 ===
        # - workspace：仅当显式传入时覆盖，否则保留父值
        # - user_message：本次调用的指令文本（用于 replay 匹配）
        # - extra_metadata：调用方追加的元信息
        # - session_role（C13 audit #1 fix）：sub-agent 有自己的 profile.role
        #   （coordinator/agent/plan/ask），engine matrix lookup 完全依赖它。
        #   orchestrator._call_agent 调 chat_with_session(mode=_mode) 时
        #   _mode 已基于 profile.role 计算完毕，所以 caller 总会显式传 mode；
        #   这里直接 honor caller mode。若调用方意外漏传，函数默认 mode="agent"
        #   会让 child 走 AGENT 矩阵 —— 这是与非 parent_ctx 路径同款的安全默认。
        eff_workspace = ws_path if workspace is not None else base.workspace
        eff_user_message = user_message or base.user_message
        eff_metadata = dict(base.metadata)
        if extra_metadata:
            eff_metadata.update(extra_metadata)
        eff_session_role = mode_to_session_role(mode)
        # === sub-agent **不可**自己改的字段（escalation guard） ===
        # - is_owner：升权风险；child 始终用父
        # - is_unattended / unattended_strategy：任务级属性，全树一致
        # - safety_immune_paths：禁止 child override，子安全包络等于父
        # - root_user_id / delegate_chain：身份链
        # - confirmation_mode：session 级配置，全树一致
        # - replay_authorizations / trusted_path_overrides：继承父的快照副本
        # - channel：session 共享，sub-agent 视图与父相同（不暴露 override）
        return PolicyContext(
            session_id=base.session_id,
            workspace=eff_workspace,
            channel=base.channel,
            is_owner=base.is_owner,
            root_user_id=base.root_user_id,
            session_role=eff_session_role,
            confirmation_mode=base.confirmation_mode,
            is_unattended=base.is_unattended,
            unattended_strategy=base.unattended_strategy,
            delegate_chain=list(base.delegate_chain),
            replay_authorizations=list(base.replay_authorizations),
            trusted_path_overrides=list(base.trusted_path_overrides),
            safety_immune_paths=base.safety_immune_paths,
            metadata=eff_metadata,
            user_message=eff_user_message,
        )

    # confirmation_mode 来自全局 config（与 v1 ``_is_trust_mode`` 同源）
    try:
        from .global_engine import get_config_v2

        cfg = get_config_v2()
        cfg_mode = cfg.confirmation.mode
        if isinstance(cfg_mode, ConfirmationMode):
            confirmation_mode = cfg_mode
        else:
            try:
                confirmation_mode = ConfirmationMode(str(cfg_mode))
            except ValueError:
                confirmation_mode = ConfirmationMode.DEFAULT
    except Exception:
        confirmation_mode = ConfirmationMode.DEFAULT

    # replay / trusted_path 从 session metadata 读（v1 既有契约）
    replay_auths: list[ReplayAuthorization] = []
    trusted_paths: list[TrustedPathOverride] = []
    safety_immune: tuple[str, ...] = ()

    # C8: session-level 字段优先级
    # - session.session_role 覆盖入参 ``mode``（switch_mode 写过的话）
    # - session.confirmation_mode_override 覆盖全局 confirmation_mode（per-session 模式）
    # - session.metadata["is_owner"] 覆盖入参 ``is_owner``（IM gateway 写过的话）
    # - session.is_unattended / session.unattended_strategy 覆盖入参 ``is_unattended``
    #   （C12 Phase A：webhook / spawn 入口可在 Session 上预写无人值守标志，
    #   chat_with_tools 路径不需要自己计算 is_unattended）
    effective_mode = mode
    effective_is_owner = is_owner
    effective_is_unattended = is_unattended
    effective_unattended_strategy = ""
    if session is not None:
        try:
            sr = getattr(session, "session_role", None)
            if isinstance(sr, str) and sr:
                effective_mode = sr
        except Exception:
            pass
        try:
            cm_override = getattr(session, "confirmation_mode_override", None)
            if isinstance(cm_override, str) and cm_override:
                try:
                    confirmation_mode = ConfirmationMode(cm_override)
                except ValueError:
                    pass
        except Exception:
            pass
        try:
            owner_meta = session.get_metadata("is_owner")
            if isinstance(owner_meta, bool):
                effective_is_owner = owner_meta
        except Exception:
            pass
        # First-class fields take precedence; ``is_unattended`` defaults to
        # False so ``False`` from a default-init Session does NOT silently
        # override an explicit ``is_unattended=True`` from the caller.
        # The "OR" pattern matches: caller passes True → kept True; session
        # also flags True → still True; both False → False.
        try:
            sess_unattended = getattr(session, "is_unattended", False)
            if isinstance(sess_unattended, bool) and sess_unattended:
                effective_is_unattended = True
        except Exception:
            pass
        try:
            sess_strategy = getattr(session, "unattended_strategy", "")
            if isinstance(sess_strategy, str) and sess_strategy:
                effective_unattended_strategy = sess_strategy
        except Exception:
            pass
        # Backward compat: very old sessions stored these in metadata only.
        # Only fall back when first-class fields are still default.
        if not effective_is_unattended:
            try:
                meta_un = session.get_metadata("is_unattended")
                if isinstance(meta_un, bool) and meta_un:
                    effective_is_unattended = True
            except Exception:
                pass
        if not effective_unattended_strategy:
            try:
                meta_strat = session.get_metadata("unattended_strategy")
                if isinstance(meta_strat, str) and meta_strat:
                    effective_unattended_strategy = meta_strat
            except Exception:
                pass

    if session is not None:
        try:
            stamp = session.get_metadata("risk_authorized_replay")
        except Exception:
            stamp = None
        replay_auths = _coerce_replay_auths(stamp)

        try:
            tp_meta = session.get_metadata("trusted_path_overrides")
        except Exception:
            tp_meta = None
        if isinstance(tp_meta, dict):
            trusted_paths = _coerce_trusted_paths(tp_meta.get("rules"))
        else:
            trusted_paths = _coerce_trusted_paths(tp_meta)

        try:
            sip = session.get_metadata("safety_immune_paths")
            if isinstance(sip, (list, tuple)):
                safety_immune = tuple(str(x) for x in sip)
        except Exception:
            pass

    return PolicyContext(
        session_id=session_id or "policy_v2_ctx",
        workspace=ws_path,
        channel=channel,
        is_owner=effective_is_owner,
        root_user_id=root_user_id,
        session_role=mode_to_session_role(effective_mode),
        confirmation_mode=confirmation_mode,
        is_unattended=effective_is_unattended,
        unattended_strategy=effective_unattended_strategy,
        delegate_chain=list(delegate_chain or []),
        replay_authorizations=replay_auths,
        trusted_path_overrides=trusted_paths,
        safety_immune_paths=safety_immune,
        metadata=dict(extra_metadata or {}),
        user_message=user_message,
    )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def evaluate_via_v2(
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
    *,
    mode: str = "agent",
    extra_ctx: PolicyContext | None = None,
    user_message: str = "",
    handler_name: str | None = None,
) -> PolicyDecisionV2:
    """通过 v2 引擎评估单次工具调用。

    Args:
        tool_name: 工具名（``write_file`` / ``run_shell`` / ...）。
        tool_input: 工具参数 dict；None → ``{}``。
        mode: v1 ``permission.check_permission`` 透传的 mode 字段，当前未直接
            影响 v2 决策（plan/ask 由前置层拦截）；保留以便审计 + 未来扩展。
        extra_ctx: 调用方显式传入的 ``PolicyContext``（覆盖 ContextVar）。
        user_message: 当前轮 user message，用于 replay/trusted_path 匹配。
        handler_name: classifier 增强信号（来源 plugin/system，影响严格度）。

    Returns:
        ``PolicyDecisionV2``。Engine 层已 fail-safe（异常 → DENY），但
        adapter 仍包一层 catch 以防 ctx 构造抛出。
    """
    params = tool_input or {}
    ctx = _resolve_context(extra_ctx=extra_ctx, user_message=user_message)

    event = ToolCallEvent(
        tool=tool_name,
        params=params,
        handler_name=handler_name,
    )

    try:
        engine = _get_engine()
        return engine.evaluate_tool_call(event, ctx)
    except Exception as exc:
        logger.error(
            "[PolicyV2 adapter] evaluate failed for %s (mode=%s): %s",
            tool_name,
            mode,
            exc,
        )
        return _synthesize_fail_closed(tool_name, exc)


def _synthesize_fail_closed(tool_name: str, exc: Exception) -> PolicyDecisionV2:
    """构造一个 fail-closed 决策（adapter 层 fallback）。

    与 v1 ``permission.check_permission`` 异常分支语义对齐：
    - 风险工具（write/run/delete/...）→ DENY
    - 安全工具 → ALLOW（避免拖垮 read_file 等高频低危场景）
    """
    from .enums import ApprovalClass
    from .models import DecisionStep, PolicyDecisionV2

    if _should_fail_closed(tool_name):
        return PolicyDecisionV2(
            action=DecisionAction.DENY,
            reason="安全策略暂时不可用，已阻止高风险操作，请稍后重试。",
            approval_class=ApprovalClass.UNKNOWN,
            chain=[
                DecisionStep(
                    name="adapter_fail_closed",
                    action=DecisionAction.DENY,
                    note=f"engine_unavailable: {exc!r}",
                )
            ],
        )
    return PolicyDecisionV2(
        action=DecisionAction.ALLOW,
        reason="",
        approval_class=ApprovalClass.UNKNOWN,
        chain=[
            DecisionStep(
                name="adapter_fail_open_safe",
                action=DecisionAction.ALLOW,
                note=f"engine_unavailable: {exc!r}",
            )
        ],
    )


# ---------------------------------------------------------------------------
# v2 → v1 PolicyResult 翻译
# ---------------------------------------------------------------------------

# v2 DecisionAction → v1 PolicyDecision 字符串值（v1 PolicyDecision 是 StrEnum）
_V2_TO_V1_DECISION: dict[DecisionAction, str] = {
    DecisionAction.ALLOW: "allow",
    DecisionAction.CONFIRM: "confirm",
    DecisionAction.DENY: "deny",
    # DEFER → CONFIRM 降级（v1 不识别 DEFER；让 UI 拦截，IM 通道再次拦截）
    DecisionAction.DEFER: "confirm",
}


# C8b-6b：``_v2_action_to_v1_decision`` 已删（仅 ``decision_to_v1_result`` 调，
# 同步删除）。C8b-6a public alias ``V2_TO_V1_DECISION`` 提供同等映射给 permission.py。


def _build_metadata(decision: PolicyDecisionV2) -> dict[str, Any]:
    """把 v2 顶层字段平铺写入 metadata，供下游 v1 风格读取。

    冗余写策略：v2 顶层字段（``needs_sandbox`` 等）+ ``decision.metadata``
    自由槽位。**``decision.metadata`` 的 key 不覆盖 canonical 字段**——若两边
    冲突，以 canonical 为准（防止上游写脏数据破坏下游契约）。
    """
    canonical: dict[str, Any] = {
        "approval_class": decision.approval_class.value,
        "needs_sandbox": decision.needs_sandbox,
        "needs_checkpoint": decision.needs_checkpoint,
        "shell_risk_level": decision.shell_risk_level,
        "safety_immune_match": decision.safety_immune_match,
        "is_owner_required": decision.is_owner_required,
        "is_unattended_path": decision.is_unattended_path,
        "ttl_seconds": decision.ttl_seconds,
        "decided_at": decision.decided_at,
        "risk_level": _shell_risk_to_v1_risk_level(
            decision.shell_risk_level, decision.approval_class.value
        ),
    }

    extras = {k: v for k, v in (decision.metadata or {}).items() if k not in canonical}
    canonical.update(extras)

    canonical["v2_origin"] = True
    return canonical


def _shell_risk_to_v1_risk_level(
    shell_risk: str | None, approval_class: str
) -> str:
    """把 v2 ``shell_risk_level`` / ``approval_class`` 映射为 v1 风险标签
    (low/medium/high/critical)，供 SecurityView 显示。

    规则：
    - shell_risk 优先（BLOCKED→critical, CRITICAL→critical, HIGH→high,
      MEDIUM→medium, LOW→low）
    - 否则按 approval_class：DESTRUCTIVE/MUTATING_GLOBAL → high，
      CONTROL_PLANE → critical，其他 → medium
    """
    if shell_risk:
        normalized = shell_risk.upper()
        if normalized in {"BLOCKED", "CRITICAL"}:
            return "critical"
        if normalized == "HIGH":
            return "high"
        if normalized == "MEDIUM":
            return "medium"
        if normalized == "LOW":
            return "low"

    if approval_class in {"destructive", "control_plane"}:
        return "critical"
    if approval_class == "mutating_global":
        return "high"
    if approval_class == "interactive":
        return "medium"
    return "low"


def _build_policy_name(decision: PolicyDecisionV2) -> str:
    """从 chain 末尾抽出主导步骤名作为 policy_name。

    格式: ``"policy_v2:<step_name>"``。空 chain → ``"policy_v2"``。
    """
    if not decision.chain:
        return "policy_v2"
    last = decision.chain[-1]
    return f"policy_v2:{last.name}"


# C8b-6b：``decision_to_v1_result`` + ``evaluate_via_v2_to_v1_result`` 已删。
# 这两个 helper 在 C6→C8b-6a 阶段把 v2 决策结果翻译成 v1 ``PolicyResult`` shape，
# 让生产代码可以渐进式迁移。C8b-6a 完成后所有生产 caller（permission.py /
# reasoning_engine.py × 2）都已直接消费 ``PolicyDecisionV2``，无人再用 v1
# ``PolicyResult``。C8b-6b 删 v1 ``PolicyResult`` 类（policy.py 整文件删）+ 同步
# 删 adapter 这两个 helper。git history 留着供 archaeologist 查 v1→v2 桥接逻辑。
#
# 内部 ``_v2_action_to_v1_decision`` 同步删（仅由 ``decision_to_v1_result`` 调）。
# ``_V2_TO_V1_DECISION`` dict + ``_build_metadata`` + ``_build_policy_name``
# 保留 —— 已在 C8b-6a 提为 public ``V2_TO_V1_DECISION`` /
# ``build_metadata_for_legacy_callers`` / ``build_policy_name``，被 permission.py
# 跨模块消费（v1 4 档 enum 语义层翻译）。


# ---------------------------------------------------------------------------
# C7：message intent (RiskGate) 评估
# ---------------------------------------------------------------------------


def evaluate_message_intent_via_v2(
    message: str,
    risk_intent: object | None = None,
    *,
    extra_ctx: PolicyContext | None = None,
    user_message: str = "",
) -> PolicyDecisionV2:
    """通过 v2 ``evaluate_message_intent`` 评估消息层 RiskGate 决策。

    替换 v1 ``agent.py`` RiskGate 三段查询（``_check_trust_mode_skip`` +
    ``_check_trusted_path_skip`` + ``risk_intent.requires_confirmation``）的
    主决策。**消费侧**（session.set_metadata 清 replay/trusted）不动，仍由
    agent.py 在 ``_consume_risk_authorization`` 完成——decision_only 策略。

    Args:
        message: 用户原始 message（用于 replay 匹配）。
        risk_intent: v1 ``RiskIntentResult``（dataclass 或 dict）；engine 的
            ``_extract_risk_signal`` 容错读其 ``risk_level`` / ``operation_kind``
            字段，未知形态退化为 None。
        extra_ctx: 显式 PolicyContext（agent.py 一般传入已构好的 ctx，
            把 session 信号一次性灌进去）。
        user_message: ctx 缺 user_message 时的补充值。

    Returns:
        ``PolicyDecisionV2``。Engine 已 fail-safe；adapter 再包一层。
    """
    ctx = _resolve_context(extra_ctx=extra_ctx, user_message=user_message or message)

    event = MessageIntentEvent(
        message=message,
        risk_intent=risk_intent,
    )

    try:
        engine = _get_engine()
        return engine.evaluate_message_intent(event, ctx)
    except Exception as exc:
        logger.error(
            "[PolicyV2 adapter] evaluate_message_intent failed: %s",
            exc,
        )
        # 消息层失败 → 保守 CONFIRM（让用户决断），不直接 DENY 避免阻塞用户对话
        from .models import DecisionStep, PolicyDecisionV2

        return PolicyDecisionV2(
            action=DecisionAction.CONFIRM,
            reason="安全策略暂时不可用，请确认是否继续。",
            chain=[
                DecisionStep(
                    name="adapter_msg_fail_soft",
                    action=DecisionAction.CONFIRM,
                    note=f"engine_unavailable: {exc!r}",
                )
            ],
        )


# C8b-6a: public aliases for the v2→v1 helpers used by ``permission.py`` (and
# any other future caller). Underscored names retained as module internals;
# the public aliases are stable contracts. C8b-6b will collapse the two when
# v1 PolicyDecision/PolicyResult are gone (then ``decision_to_v1_result`` /
# ``evaluate_via_v2_to_v1_result`` disappear with them).
V2_TO_V1_DECISION = _V2_TO_V1_DECISION
build_policy_name = _build_policy_name
build_metadata_for_legacy_callers = _build_metadata


__all__ = [
    "V2_TO_V1_DECISION",
    "build_metadata_for_legacy_callers",
    "build_policy_context",
    "build_policy_name",
    "evaluate_message_intent_via_v2",
    "evaluate_via_v2",
    "mode_to_session_role",
]

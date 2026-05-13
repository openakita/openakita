"""PolicyEngineV2 — 单一权威决策入口。

设计目标（详见 docs §3）：
- **唯一入口**：替换 v1 的多入口（policy.assert_tool_allowed +
  permission.check_permission + risk_intent.classify_risk_intent 各自决策）。
- **双方法**：
  - ``evaluate_tool_call`` — 工具执行前的 ALLOW/CONFIRM/DENY 决策
  - ``evaluate_message_intent`` — pre-LLM RiskGate 决策
- **12 步决策链**：每步产出 ``DecisionStep`` 加入 chain，最终回传给 SSE / 审计。
- **fail-safe**：任何步骤抛异常 → 顶层 catch → DENY + 记录原因（R5-15）。
- **thread-safety**：共享状态全部走 ``threading.RLock``（async 调用方进 to_thread）；
  classifier 自带 LRU 在内部上锁。

12 步（含 C5 step 2b approval_classes overrides）：

1. ``preflight``     — engine 健康/启用检查 + tool 名归一化
2. ``classify``      — ApprovalClassifier.classify_full() 拿 ApprovalClass + meta
2b. ``approval_override`` — config.approval_classes.overrides ⊕ most_strict（C5）
3. ``safety_immune`` — config.safety_immune.paths ∪ ctx (C5 落地，C8 PathSpec 完整匹配)
4. ``owner_only``    — config.owner_only.tools ∪ heuristic CONTROL_PLANE（C5）
5. ``channel_compat``— 渠道-类相容性（IM 不允许 desktop_*/browser_*）
6. ``matrix``        — SessionRole × ConfirmationMode × ApprovalClass 决策
7. ``replay``        — 30s 内复读消息免 confirm（C5 read-only；C7 消费侧）
8. ``trusted_path``  — 用户 allow_session 后 path 白名单（C5 read-only；C7 消费侧）
9. ``user_allowlist``— allowlist_v2 / 持久化白名单（C8 stub）
10. ``death_switch`` — 连续 deny 超阈值 → 强制 readonly（C8 stub）
11. ``unattended``   — 5 strategy 完整实现（C5）；C12 接 pending_approvals 持久化
12. ``finalize``     — 默认动作 + 元数据填充 + audit hook

C3 阶段：1/2/5/6/12 完整；3/4/7/8/11 stub。
C5 阶段：2b 新增；3/4/7/8/11 实装；9/10 仍是 C8 stub。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import TYPE_CHECKING, Any

from .classifier import ApprovalClassifier, ClassificationResult
from .context import PolicyContext, ReplayAuthorization, TrustedPathOverride
from .death_switch import get_death_switch_tracker
from .enums import (
    ApprovalClass,
    ConfirmationMode,
    DecisionAction,
    DecisionSource,
    SessionRole,
    most_strict,
)
from .matrix import lookup as lookup_matrix
from .models import (
    DecisionStep,
    MessageIntentEvent,
    PolicyDecisionV2,
    ToolCallEvent,
)
from .safety_immune_defaults import expand_builtin_immune_paths
from .schema import PolicyConfigV2
from .shell_risk import ShellRiskLevel
from .skill_allowlist import get_skill_allowlist_manager
from .user_allowlist import UserAllowlistManager
from .zones import candidate_path_fields

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# 工具名归一化别名（plugin: / mcp: 前缀剥离 → 用纯名走 classifier）
_PLUGIN_PREFIXES: tuple[str, ...] = ("plugin:", "mcp:", "skill:")


# 工具名前缀：依赖前台 GUI / 浏览器 → IM/Webhook 渠道执行无意义，应 DENY。
# 注意 ``ask_user`` 同属 INTERACTIVE 类但**不在此列**——IM 渠道下 ask_user 是
# 合法的（直接在 IM 群里发问），由 IM 适配器处理。docs §4.21.1 明确说
# channel_compat 只屏蔽 desktop_* / browser_*，**不屏蔽** ask_user 等可跨
# 渠道的 INTERACTIVE 工具。
_DESKTOP_REQUIRED_TOOL_PREFIXES: tuple[str, ...] = (
    "desktop_",
    "browser_",
)

_DESKTOP_CHANNELS: frozenset[str] = frozenset({"desktop", "cli"})


class PolicyEngineV2:
    """单一权威决策引擎。

    线程安全：所有 mutable state 走 ``self._lock``。classifier 自带 LRU 锁。
    构造廉价（~50 µs），允许多实例（测试隔离）；生产建议持有单例（C8 wire-up
    时由 agent 注入）。
    """

    def __init__(
        self,
        classifier: ApprovalClassifier | None = None,
        *,
        config: PolicyConfigV2 | None = None,
        audit_hook: Callable[[PolicyDecisionV2, ToolCallEvent, PolicyContext], None] | None = None,
        audit_intent_hook: Callable[[PolicyDecisionV2, MessageIntentEvent, PolicyContext], None]
        | None = None,
    ) -> None:
        """构造引擎。

        ``classifier``：默认 ``ApprovalClassifier()``——仅启发式兜底，**生产环境
        必须显式传入**带有 ``explicit_lookup=registry.get_tool_class`` 的实例，
        否则所有内置工具都走 heuristic（可能与 §4 工具映射表不一致）。
        C8 wire-up 时由 agent.py 注入。

        ``config``（C5）：``POLICIES.yaml`` 加载后的 ``PolicyConfigV2``。提供
        - ``safety_immune.paths`` 全局 immune 路径
        - ``owner_only.tools`` 显式 owner-only 工具
        - ``approval_classes.overrides`` 用户对 ApprovalClass 的 override
        - ``unattended.default_strategy`` 计划任务的兜底策略
        默认 ``PolicyConfigV2()``——纯 schema 默认（trust 模式不开、空 immune、
        ask_owner 兜底）；测试与首启都 OK。

        ``audit_hook`` 接收 ``evaluate_tool_call`` 决策；``audit_intent_hook``
        接收 ``evaluate_message_intent`` 决策。两者签名形参不同，分开传以避
        免类型混淆。任一 hook 抛异常都被捕获、记录，不影响主决策。

        Thread-safety：``_stats`` 计数器走 ``self._lock``。其他 mutable 状态
        无（classifier 自行管理 cache，hook 只读）。生产单例使用 OK，多实例
        构造也廉价（~50 µs）。
        """
        self._config = config or PolicyConfigV2()
        if classifier is None:
            self._classifier = ApprovalClassifier(shell_risk_config=self._config.shell_risk)
        else:
            self._classifier = classifier
            # 调用方同时传 classifier 和 config 时，警告 shell_risk 可能"split-brain"。
            # 推荐使用 ``build_engine_from_config(cfg)`` 工厂避免此种错配。
            # 用 ``getattr`` 兼容传入的非 ApprovalClassifier 子类（duck-typing）。
            other_cfg = getattr(classifier, "_shell_risk_config", None)
            if config is not None and other_cfg is not config.shell_risk:
                logger.warning(
                    "[PolicyEngineV2] classifier was constructed with a different "
                    "shell_risk_config than engine.config.shell_risk; the classifier's "
                    "settings win for shell command classification. Consider using "
                    "`build_engine_from_config(cfg)` to avoid split-brain configs."
                )
        self._audit_hook = audit_hook
        self._audit_intent_hook = audit_intent_hook
        self._lock = threading.RLock()
        self._stats: dict[str, int] = {
            "evaluate_tool_call": 0,
            "evaluate_message_intent": 0,
            "engine_crash": 0,
        }

        # 配置派生缓存（避免每次决策访问嵌套 attr；冻结成不可变结构）
        # C8: builtin 9-category immune paths 永远在前；用户 config 只能 ADD，不能 REMOVE。
        # _path_under 在 _check_safety_immune 里已 dedupe（命中即返回，重复无副作用），
        # 这里用 list 拼接保留顺序，让审计日志能直观看到"是 builtin 命中还是用户命中"。
        _builtin_immune = expand_builtin_immune_paths()
        self._immune_paths_from_config: tuple[str, ...] = (
            *_builtin_immune,
            *(p for p in self._config.safety_immune.paths if p not in _builtin_immune),
        )
        self._owner_only_tools: frozenset[str] = frozenset(self._config.owner_only.tools)
        self._approval_overrides: dict[str, ApprovalClass] = {
            k: ApprovalClass(v) if isinstance(v, str) else v
            for k, v in self._config.approval_classes.overrides.items()
        }
        # ``unattended.default_strategy`` 是 Pydantic Literal，已校验过合法值
        self._unattended_default: str = str(self._config.unattended.default_strategy)

        # C8b-1: user allowlist manager 与 engine 一对一（持久化白名单 CRUD）。
        # skill allowlist / death switch 是 process-wide singleton，不随 engine 实例变。
        self._user_allowlist = UserAllowlistManager(self._config)
        # C8b-1: ``count_in_death_switch`` flag 让 dry-run preview 引擎可以跳过计数
        # （preview API 构造的 ad-hoc engine 不应污染全局 readonly_mode 计数）。
        # 默认 True；preview API 在构造后显式置 False。
        self.count_in_death_switch: bool = True

    # ============================================================
    # Public API
    # ============================================================

    def evaluate_tool_call(
        self,
        event: ToolCallEvent,
        ctx: PolicyContext,
    ) -> PolicyDecisionV2:
        """工具执行前的决策。失败一律 DENY（fail-safe）。"""
        with self._lock:
            self._stats["evaluate_tool_call"] += 1

        try:
            decision = self._evaluate_tool_call_impl(event, ctx)
        except Exception as exc:  # noqa: BLE001 — 顶层兜底必须捕获所有异常
            with self._lock:
                self._stats["engine_crash"] += 1
            logger.exception(
                "[PolicyEngineV2] evaluate_tool_call crashed for tool=%s",
                event.tool,
            )
            decision = PolicyDecisionV2(
                action=DecisionAction.DENY,
                reason=f"engine_crash: {type(exc).__name__}",
                approval_class=ApprovalClass.UNKNOWN,
                chain=[
                    DecisionStep(
                        name="engine_crash",
                        action=DecisionAction.DENY,
                        note=f"{type(exc).__name__}: {exc}",
                    )
                ],
            )

        self._maybe_audit(decision, event, ctx)
        # C8b-1: 决策落定后才记入 death_switch tracker；
        # dry-run preview engine 把 ``count_in_death_switch`` 置 False 跳过计数。
        if self.count_in_death_switch:
            ds_cfg = self._config.death_switch
            get_death_switch_tracker().record_decision(
                action=decision.action.value,
                tool_name=event.tool,
                enabled=ds_cfg.enabled,
                threshold=ds_cfg.threshold,
                total_multiplier=ds_cfg.total_multiplier,
            )
        return decision

    def evaluate_message_intent(
        self,
        event: MessageIntentEvent,
        ctx: PolicyContext,
    ) -> PolicyDecisionV2:
        """pre-LLM RiskGate 决策。

        v1 的 ``RiskGate.assess()`` 把 ``risk_intent.classify_risk_intent()``
        结果塞到这里。v2 阶段 risk_intent 仍是独立模块，engine 只负责"风险
        信号 → 决策"映射，不做意图识别本身。

        C3 阶段：基础映射（risk_intent.has_signal → CONFIRM；trust 模式 → ALLOW）。
        完整 v1 RiskGate 等价行为留 C7（替换 agent.py 调用点）时落地。
        """
        with self._lock:
            self._stats["evaluate_message_intent"] += 1

        try:
            decision = self._evaluate_message_intent_impl(event, ctx)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["engine_crash"] += 1
            logger.exception("[PolicyEngineV2] evaluate_message_intent crashed")
            decision = PolicyDecisionV2(
                action=DecisionAction.DENY,
                reason=f"engine_crash: {type(exc).__name__}",
                chain=[
                    DecisionStep(
                        name="engine_crash",
                        action=DecisionAction.DENY,
                        note=f"{type(exc).__name__}: {exc}",
                    )
                ],
            )

        self._maybe_audit_intent(decision, event, ctx)
        return decision

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stats)

    # ------------------------------------------------------------------
    # Manager 访问入口（C8b-1）
    # ------------------------------------------------------------------

    @property
    def user_allowlist(self) -> UserAllowlistManager:
        """Engine-scoped 持久化白名单 manager。"""
        return self._user_allowlist

    # ============================================================
    # evaluate_tool_call: 12-step decision chain
    # ============================================================

    def _evaluate_tool_call_impl(
        self,
        event: ToolCallEvent,
        ctx: PolicyContext,
    ) -> PolicyDecisionV2:
        chain: list[DecisionStep] = []

        # Step 1: preflight（归一化 + sanity）
        tool = self._normalize_tool_name(event.tool)
        chain.append(
            DecisionStep(
                name="preflight",
                action=DecisionAction.ALLOW,
                note=f"tool={tool}",
            )
        )

        # Step 2: classify（拿 ApprovalClass + meta）
        clf_result = self._classifier.classify_full(tool, event.params, ctx)
        chain.append(
            DecisionStep(
                name="classify",
                action=DecisionAction.ALLOW,
                note=f"class={clf_result.approval_class.value} source={clf_result.source.value}",
            )
        )

        # Step 2b: approval_classes.overrides（C5）—— 用户配置 override，但只接受
        # ``most_strict`` 比 classifier 更严的（防止用户错配削弱安全）。
        clf_result = self._apply_class_override(tool, clf_result, chain)

        # Step 3: safety_immune（C5: union config + ctx; C6: PathSpec 完整匹配）
        immune = self._check_safety_immune(tool, event.params, ctx)
        if immune is not None:
            return self._finalize(
                chain=chain,
                step_name="safety_immune",
                action=DecisionAction.CONFIRM,
                reason=f"safety_immune match: {immune}",
                clf=clf_result,
                safety_immune_match=immune,
            )

        # Step 4: owner_only（C6 stub）
        if self._requires_owner_only(tool, clf_result.approval_class) and not ctx.is_owner:
            return self._finalize(
                chain=chain,
                step_name="owner_only",
                action=DecisionAction.DENY,
                reason="owner_only: tool restricted to session owner",
                clf=clf_result,
                is_owner_required=True,
            )

        # Step 5: channel_compat（desktop_*/browser_* 工具不能跑在 IM/Webhook）
        chan_block = self._check_channel_compat(tool, ctx)
        if chan_block is not None:
            return self._finalize(
                chain=chain,
                step_name="channel_compat",
                action=DecisionAction.DENY,
                reason=chan_block,
                clf=clf_result,
            )

        # Step 6: matrix lookup
        base_action = lookup_matrix(
            ctx.session_role, ctx.confirmation_mode, clf_result.approval_class
        )
        chain.append(
            DecisionStep(
                name="matrix",
                action=base_action,
                note=f"role={ctx.session_role.value} mode={ctx.confirmation_mode.value}",
            )
        )

        # 短路：matrix 直接 DENY → 跳到 finalize（不再 relax）
        if base_action == DecisionAction.DENY:
            return self._finalize(
                chain=chain,
                step_name="matrix_deny",
                action=DecisionAction.DENY,
                reason="matrix says DENY",
                clf=clf_result,
            )

        # 短路：matrix 直接 ALLOW → 跳过 relax 步骤直奔 finalize
        if base_action == DecisionAction.ALLOW:
            return self._finalize(
                chain=chain,
                step_name="matrix_allow",
                action=DecisionAction.ALLOW,
                reason="matrix says ALLOW",
                clf=clf_result,
            )

        # base_action == CONFIRM —— 尝试 step 7/8/9 relax 到 ALLOW
        # Step 7: replay authorization（C5 stub return None）
        relax = self._check_replay_authorization(event, ctx)
        if relax is not None:
            chain.append(
                DecisionStep(
                    name="replay",
                    action=DecisionAction.ALLOW,
                    note=relax,
                )
            )
            return self._finalize(
                chain=chain,
                step_name="replay",
                action=DecisionAction.ALLOW,
                reason=f"replay authorization: {relax}",
                clf=clf_result,
            )

        # Step 8: trusted path override（C5 stub return None）
        relax = self._check_trusted_path(event, ctx)
        if relax is not None:
            chain.append(
                DecisionStep(
                    name="trusted_path",
                    action=DecisionAction.ALLOW,
                    note=relax,
                )
            )
            return self._finalize(
                chain=chain,
                step_name="trusted_path",
                action=DecisionAction.ALLOW,
                reason=f"trusted_path override: {relax}",
                clf=clf_result,
            )

        # Step 9: user_allowlist（C8 stub return None）
        relax = self._check_user_allowlist(event, ctx)
        if relax is not None:
            chain.append(
                DecisionStep(
                    name="user_allowlist",
                    action=DecisionAction.ALLOW,
                    note=relax,
                )
            )
            return self._finalize(
                chain=chain,
                step_name="user_allowlist",
                action=DecisionAction.ALLOW,
                reason=f"user_allowlist: {relax}",
                clf=clf_result,
            )

        # Step 10: death_switch（C8 stub return None）
        ds_action = self._check_death_switch(ctx, clf_result.approval_class)
        if ds_action is not None:
            return self._finalize(
                chain=chain,
                step_name="death_switch",
                action=ds_action,
                reason="death_switch active",
                clf=clf_result,
            )

        # Step 11: unattended branch（C5 5 strategies；C12 wire pending_approvals）
        if ctx.is_unattended:
            effective_strategy = self._effective_unattended_strategy(ctx)
            ua = self._handle_unattended(clf_result, effective_strategy)
            chain.append(
                DecisionStep(
                    name="unattended",
                    action=ua.action,
                    # 显示**生效**的 strategy（ctx 可能空 → 用 config default），
                    # 让审计 / SSE 看到真实判定来源；audit fix B
                    note=f"strategy={effective_strategy}",
                )
            )
            return self._finalize(
                chain=chain,
                step_name="unattended",
                action=ua.action,
                reason=ua.reason,
                clf=clf_result,
                is_unattended_path=True,
            )

        # Step 12: finalize CONFIRM
        return self._finalize(
            chain=chain,
            step_name="finalize",
            action=DecisionAction.CONFIRM,
            reason="matrix says CONFIRM (no relax matched)",
            clf=clf_result,
        )

    # ============================================================
    # evaluate_message_intent
    # ============================================================

    def _evaluate_message_intent_impl(
        self,
        event: MessageIntentEvent,
        ctx: PolicyContext,
    ) -> PolicyDecisionV2:
        chain: list[DecisionStep] = [
            DecisionStep(
                name="intent_preflight",
                action=DecisionAction.ALLOW,
                note=f"role={ctx.session_role.value} mode={ctx.confirmation_mode.value}",
            )
        ]

        # plan / ask 模式禁止任何写意图
        if ctx.session_role in (SessionRole.PLAN, SessionRole.ASK):
            risk_signal = _extract_risk_signal(event.risk_intent)
            if risk_signal is not None and risk_signal != "readonly":
                return PolicyDecisionV2(
                    action=DecisionAction.DENY,
                    reason=f"{ctx.session_role.value} mode forbids write intent: {risk_signal}",
                    chain=chain
                    + [
                        DecisionStep(
                            name="intent_role_block",
                            action=DecisionAction.DENY,
                            note=risk_signal,
                        )
                    ],
                )

        # trust 模式 → 全部 ALLOW（v1 RiskGate 在 trust 模式下不拦截，对齐）
        if ctx.confirmation_mode == ConfirmationMode.TRUST:
            return PolicyDecisionV2(
                action=DecisionAction.ALLOW,
                reason="trust mode bypasses message-intent gate",
                chain=chain
                + [
                    DecisionStep(
                        name="intent_trust_bypass",
                        action=DecisionAction.ALLOW,
                    )
                ],
            )

        # 其他模式：风险信号 → CONFIRM；无信号 → ALLOW
        risk_signal = _extract_risk_signal(event.risk_intent)
        if risk_signal is None or risk_signal == "readonly":
            return PolicyDecisionV2(
                action=DecisionAction.ALLOW,
                reason="no write intent detected",
                chain=chain
                + [
                    DecisionStep(
                        name="intent_clean",
                        action=DecisionAction.ALLOW,
                    )
                ],
            )

        return PolicyDecisionV2(
            action=DecisionAction.CONFIRM,
            reason=f"risk intent: {risk_signal}",
            chain=chain
            + [
                DecisionStep(
                    name="intent_risk",
                    action=DecisionAction.CONFIRM,
                    note=risk_signal,
                )
            ],
        )

    # ============================================================
    # Step helpers (C3: stubs that defer to later commits)
    # ============================================================

    @staticmethod
    def _normalize_tool_name(raw: str) -> str:
        """剥离 plugin: / mcp: / skill: 前缀，supply 给 classifier 用纯名匹配。"""
        for prefix in _PLUGIN_PREFIXES:
            if raw.startswith(prefix):
                return raw[len(prefix) :]
        return raw

    def _apply_class_override(
        self,
        tool: str,
        clf_result: ClassificationResult,
        chain: list[DecisionStep],
    ) -> ClassificationResult:
        """Step 2b（C5）：用户在 ``POLICIES.yaml`` 的
        ``approval_classes.overrides`` 中显式配置某工具的 ApprovalClass 时，
        与 classifier 结果用 ``most_strict`` 叠加。

        **不接受削弱**：override 比 classifier 弱时直接忽略并 chain 留痕，
        防止用户错配把 DESTRUCTIVE 工具降到 READONLY 偷偷绕过审批。
        """
        override = self._approval_overrides.get(tool)
        if override is None:
            return clf_result

        merged_class, merged_source = most_strict(
            [
                (clf_result.approval_class, clf_result.source),
                (override, DecisionSource.EXPLICIT_REGISTER_PARAM),
            ]
        )

        if merged_class == clf_result.approval_class:
            chain.append(
                DecisionStep(
                    name="approval_override_ignored",
                    action=DecisionAction.ALLOW,
                    note=(
                        f"user override {override.value} weaker than "
                        f"classifier {clf_result.approval_class.value}; ignored"
                    ),
                )
            )
            return clf_result

        chain.append(
            DecisionStep(
                name="approval_override_applied",
                action=DecisionAction.ALLOW,
                note=(
                    f"user override {override.value} stricter than "
                    f"classifier {clf_result.approval_class.value}; applied"
                ),
            )
        )
        # ClassificationResult 是 frozen dataclass —— 用 model_copy 等价的手工复制
        return ClassificationResult(
            approval_class=merged_class,
            source=merged_source,
            shell_risk_level=clf_result.shell_risk_level,
            needs_sandbox=clf_result.needs_sandbox,
            needs_checkpoint=clf_result.needs_checkpoint,
        )

    def _check_safety_immune(
        self,
        tool: str,
        params: dict[str, Any] | None,
        ctx: PolicyContext,
    ) -> str | None:
        """Step 3（C5）—— union ``config.safety_immune.paths`` 与
        ``ctx.safety_immune_paths``，做路径**组件边界**前缀匹配。

        C5 起 config 是主要来源（启动时加载），ctx 仅作 session-level 动态补充
        （例如某 session 临时 add immune path）。两份 union；任一命中即 CONFIRM。

        归一化（_path_under）：
        - 反斜杠 → 正斜杠（Windows 路径）
        - 大小写不敏感（cross-platform）
        - 去除 trailing slash
        - 边界 = 完全相等 OR raw 以 protected + '/' 开头

        ``params`` 容错：``None``（调用方失误）→ 等价空 dict，不抛 AttributeError，
        与 ``classifier.classify_full`` 一致（参考 audit fix A）。
        """
        immune_paths = self._collect_immune_paths(ctx)
        if not immune_paths:
            return None
        safe_params = params or {}
        for raw_path in candidate_path_fields(safe_params):
            for protected in immune_paths:
                if _path_under(raw_path, protected):
                    return f"{tool} → {raw_path} matches {protected}"
        return None

    def _collect_immune_paths(self, ctx: PolicyContext) -> tuple[str, ...]:
        """合并 config + ctx 的 immune paths（dedupe 保序：config 先，ctx 后）。"""
        if not ctx.safety_immune_paths:
            return self._immune_paths_from_config
        if not self._immune_paths_from_config:
            return tuple(ctx.safety_immune_paths)
        seen: set[str] = set()
        out: list[str] = []
        for p in (*self._immune_paths_from_config, *ctx.safety_immune_paths):
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return tuple(out)

    def _requires_owner_only(self, tool: str, klass: ApprovalClass) -> bool:
        """Step 4（C5）：tool 在 ``config.owner_only.tools`` 显式列表 OR
        启发式默认 CONTROL_PLANE 类。

        显式列表给用户精细控制（"我要把 ``run_shell`` 也限制为 owner-only"），
        类启发式保证未在配置中显式列出的高危新工具默认安全。
        """
        if tool in self._owner_only_tools:
            return True
        return klass == ApprovalClass.CONTROL_PLANE

    def _check_channel_compat(
        self,
        tool: str,
        ctx: PolicyContext,
    ) -> str | None:
        """Step 5 —— 渠道相容性。

        基于工具名前缀（``desktop_*`` / ``browser_*``）判断是否依赖 GUI；
        IM/Webhook 等非桌面渠道下直接 DENY。``ask_user`` 等 INTERACTIVE
        类工具不在屏蔽列表（docs §4.21.1：ask_user 在 IM 渠道下走 IM 适配器
        交互）。
        """
        if ctx.channel in _DESKTOP_CHANNELS:
            return None
        if any(tool.startswith(prefix) for prefix in _DESKTOP_REQUIRED_TOOL_PREFIXES):
            return f"channel {ctx.channel!r} cannot execute {tool} (GUI-only tool)"
        return None

    def _check_replay_authorization(
        self,
        event: ToolCallEvent,
        ctx: PolicyContext,
    ) -> str | None:
        """Step 7（C5）—— "30s 内复读消息免 confirm" 检查。

        匹配条件（任一）：
        1. ``original_message`` 非空且与 ``ctx.user_message`` 子串相等
        2. ``operation`` 非空且 == 当前 tool/event 的 operation 推断（保守起见
           暂时只支持工具名前缀）

        engine 只读（read-only signal）：返回非 None 表示放行；实际"消费"
        （从 session metadata 移除该条 authorization）由 ``tool_executor`` /
        ``chat handler`` 在收到 ALLOW 后自行处理。

        engine 严守纯读取的好处：
        - 决策可重放（多次调用相同决策一致）
        - PolicyContext 可被 deep_copy / 用于 dry-run 预览
        - 真正 mutation 集中在 session 持久层，避免引擎持有 session 副作用
        """
        auths = ctx.replay_authorizations
        if not auths:
            return None
        now = time.time()
        # ``.strip()`` 对齐 v1 ``risk_authorized_replay`` 行为（agent.py:782 对
        # original_message 与 message 都 strip 后比较）；不 strip 会让带尾换行
        # 的 chat 消息无法 replay-match，导致 v1 已工作的功能在 C7 wire-up 后失效。
        user_msg_stripped = (ctx.user_message or "").strip()
        # 推断 operation：write_/edit_/delete_/move_/run_ 等前缀对应 OperationKind
        op_inferred = _infer_operation_from_tool(event.tool)
        for auth in auths:
            if not isinstance(auth, ReplayAuthorization):
                # 异构输入不应该到这（from_session 已 coerce），保险起见跳过
                continue
            if not auth.is_active(now=now):
                continue
            auth_msg_stripped = auth.original_message.strip()
            if auth_msg_stripped and user_msg_stripped and auth_msg_stripped == user_msg_stripped:
                return f"replay match: confirmation_id={auth.confirmation_id} (msg)"
            if auth.operation and op_inferred and auth.operation == op_inferred:
                return f"replay match: confirmation_id={auth.confirmation_id} (op={op_inferred})"
        return None

    def _check_trusted_path(
        self,
        event: ToolCallEvent,
        ctx: PolicyContext,
    ) -> str | None:
        """Step 8（C5）—— ``trusted_paths.consume_session_trust`` 等价（read-only）。

        匹配条件（rule 字段都是可选；空值 == 通配）：
        - ``operation`` 匹配（与 step 7 一致的推断）
        - ``path_pattern`` 正则在 user_message 命中

        sticky：与 replay 不同，trusted_path 不消费、session 内一直有效
        （直到 ``expires_at`` 或 session 结束）。
        """
        rules = ctx.trusted_path_overrides
        if not rules:
            return None
        now = time.time()
        user_msg = ctx.user_message or ""
        op_inferred = _infer_operation_from_tool(event.tool)
        for rule in rules:
            if not isinstance(rule, TrustedPathOverride):
                continue
            if not rule.is_active(now=now):
                continue
            # operation 字段非空时必须匹配
            if rule.operation and (op_inferred is None or rule.operation != op_inferred):
                continue
            # path_pattern 字段非空时必须匹配 user_message
            if rule.path_pattern:
                try:
                    if not re.search(rule.path_pattern, user_msg, re.IGNORECASE):
                        continue
                except re.error:
                    # malformed pattern —— 视为不匹配（不抛 / 不绕过）
                    continue
            return f"trusted_path rule (op={rule.operation or '*'}, pat={rule.path_pattern or '*'})"
        return None

    def _check_user_allowlist(
        self,
        event: ToolCallEvent,
        ctx: PolicyContext,
    ) -> str | None:
        """Step 9（C8b-1）—— 持久化用户白名单 + 临时 skill 授权。

        命中即放行（CONFIRM → ALLOW relax），与 v1 ``_check_allowlists`` +
        ``_is_skill_allowed`` 行为一致。**bypass 边界**已由调用顺序保证：
        - safety_immune（step 3）/ owner_only（step 4）/ channel_compat
          （step 5）/ matrix DENY（step 6 短路）都在本步之前，不可绕过
        - shell DENY 由 classifier shell_risk + matrix 提前拦截（step 6）
        - death_switch 在本步之后，但 readonly 时 step 10 直接 DENY
          所以 user_allowlist relax 只在 base_action == CONFIRM 时被调用
        """
        del ctx  # 持久化白名单只看 (tool, params)，与上下文无关
        # 持久化白名单
        entry = self._user_allowlist.match(event.tool, event.params)
        if entry is not None:
            needs_sb = bool(entry.get("needs_sandbox", False))
            return f"persistent_allowlist match (needs_sandbox={needs_sb})"

        # 临时 skill 授权（process-wide singleton）
        skill_mgr = get_skill_allowlist_manager()
        if skill_mgr.is_allowed(event.tool):
            granted_by = skill_mgr.granted_by(event.tool)
            return f"skill_allowlist by {','.join(granted_by) or '<anon>'}"

        return None

    def _check_death_switch(
        self,
        ctx: PolicyContext,
        klass: ApprovalClass,
    ) -> DecisionAction | None:
        """Step 10（C8b-1）—— 连续 deny 触发只读模式。

        命中条件：tracker.is_readonly_mode() == True 且 ApprovalClass 是
        mutating/destructive 类（read 类放行——v1 ``_readonly_mode`` 检查同样
        只 DENY 非 READ op）。

        计数侧：见 ``_evaluate_tool_call_impl`` 末尾对 tracker.record_decision
        的调用——决策落定后才计数，避免 step 10 自己刚 DENY 就立刻自我放大。
        """
        del ctx  # death_switch 是 process-wide 状态，与 ctx 无关
        if not self._config.death_switch.enabled:
            return None
        if not get_death_switch_tracker().is_readonly_mode():
            return None
        # 只对会改状态的类 DENY；read 类（含 search）放行
        if klass in _READONLY_CLASSES_FOR_DEATH_SWITCH:
            return None
        return DecisionAction.DENY

    def _effective_unattended_strategy(self, ctx: PolicyContext) -> str:
        """计算生效 unattended strategy：ctx override > config default。

        抽离便于多处共用 + 审计可读：chain step note 与 _handle_unattended
        都看到同一份"已解析"的 strategy 字符串。
        """
        return (ctx.unattended_strategy or self._unattended_default).lower()

    def _handle_unattended(
        self,
        clf: ClassificationResult,
        strategy: str,
    ) -> PolicyDecisionV2:
        """Step 11（C5）—— unattended 5 策略完整实现。

        ``strategy``：由 ``_effective_unattended_strategy`` 解析（已 lower），
        本方法纯函数地把策略映射到 PolicyDecisionV2。

        策略语义：
        - ``deny`` — 直接拒（保守默认）
        - ``auto_approve`` — 放行（**仅** readonly class，写操作仍 DENY 防范滥用）
        - ``defer_to_owner`` — DEFER（C12 实际接通 ``DeferredApprovalRequired``，
          这里返回 DEFER decision，调用方决定是否抛异常）
        - ``defer_to_inbox`` — 同 defer_to_owner，区别仅在 C12 写入不同 inbox
        - ``ask_owner`` — CONFIRM（让 owner 在 IM/desktop 接收 ask card）；
          实际投递通道由调用方按 owner 的 channel 选择

        本步只产出 decision；C12 wire-up 时调用方根据 ``action == DEFER``
        持久化 pending_approval、根据 ``action == CONFIRM`` 派发通知卡。
        """
        if strategy == "deny":
            return PolicyDecisionV2(
                action=DecisionAction.DENY,
                reason="unattended strategy=deny",
                approval_class=clf.approval_class,
            )

        if strategy == "auto_approve":
            if _is_readonly_class(clf.approval_class):
                return PolicyDecisionV2(
                    action=DecisionAction.ALLOW,
                    reason="unattended auto_approve (readonly)",
                    approval_class=clf.approval_class,
                )
            return PolicyDecisionV2(
                action=DecisionAction.DENY,
                reason=(
                    f"unattended auto_approve refused write op (class={clf.approval_class.value})"
                ),
                approval_class=clf.approval_class,
            )

        if strategy in ("defer_to_owner", "defer_to_inbox"):
            return PolicyDecisionV2(
                action=DecisionAction.DEFER,
                reason=f"unattended strategy={strategy}",
                approval_class=clf.approval_class,
            )

        if strategy == "ask_owner":
            return PolicyDecisionV2(
                action=DecisionAction.CONFIRM,
                reason="unattended strategy=ask_owner",
                approval_class=clf.approval_class,
            )

        # 未知 strategy —— fail-safe DENY（理论上 Pydantic Literal 已防住，
        # 但 ctx.unattended_strategy 走 str 不校验，必须兜底）
        return PolicyDecisionV2(
            action=DecisionAction.DENY,
            reason=f"unknown unattended strategy '{strategy}', deny by default",
            approval_class=clf.approval_class,
        )

    # ============================================================
    # Finalization & audit
    # ============================================================

    def _finalize(
        self,
        *,
        chain: list[DecisionStep],
        step_name: str,
        action: DecisionAction,
        reason: str,
        clf: ClassificationResult,
        safety_immune_match: str | None = None,
        is_owner_required: bool = False,
        is_unattended_path: bool = False,
    ) -> PolicyDecisionV2:
        chain.append(
            DecisionStep(
                name=step_name,
                action=action,
                note=reason,
            )
        )
        return PolicyDecisionV2(
            action=action,
            reason=reason,
            approval_class=clf.approval_class,
            chain=chain,
            safety_immune_match=safety_immune_match,
            is_owner_required=is_owner_required,
            is_unattended_path=is_unattended_path,
            shell_risk_level=(
                clf.shell_risk_level.value if clf.shell_risk_level is not None else None
            ),
            needs_sandbox=clf.needs_sandbox,
            needs_checkpoint=clf.needs_checkpoint,
            decided_at=time.time(),
        )

    def _maybe_audit(
        self,
        decision: PolicyDecisionV2,
        event: ToolCallEvent,
        ctx: PolicyContext,
    ) -> None:
        if self._audit_hook is None:
            return
        try:
            self._audit_hook(decision, event, ctx)
        except Exception:
            logger.exception("[PolicyEngineV2] audit_hook raised; ignored")

    def _maybe_audit_intent(
        self,
        decision: PolicyDecisionV2,
        event: MessageIntentEvent,
        ctx: PolicyContext,
    ) -> None:
        if self._audit_intent_hook is None:
            return
        try:
            self._audit_intent_hook(decision, event, ctx)
        except Exception:
            logger.exception("[PolicyEngineV2] audit_intent_hook raised; ignored")


# ============================================================
# Helpers (module-private)
# ============================================================


_READONLY_CLASSES: frozenset[ApprovalClass] = frozenset(
    {
        ApprovalClass.READONLY_SCOPED,
        ApprovalClass.READONLY_GLOBAL,
        ApprovalClass.READONLY_SEARCH,
    }
)

# C8b-1: death_switch step 在 readonly 模式下应放行的类。
# 与 v1 行为对齐——v1 ``_readonly_mode`` 检查只 DENY 非 READ op
# （``OpType.READ`` 通过 ``_tool_to_optype``）。v2 用 ApprovalClass，把
# READONLY_SEARCH 也算入（v1 没单独区分但行为一致：grep/glob/search 都属 READ）。
_READONLY_CLASSES_FOR_DEATH_SWITCH: frozenset[ApprovalClass] = _READONLY_CLASSES


def _is_readonly_class(klass: ApprovalClass) -> bool:
    return klass in _READONLY_CLASSES


# 工具名前缀 → v1 OperationKind 字符串值（与 ``risk_intent.OperationKind`` 对齐）。
# 用于 step 7/8 的 operation 维度匹配（精度有限，但兼容 v1 替代 risk_intent 推断）。
_OP_PREFIX_MAP: tuple[tuple[str, str], ...] = (
    ("delete_", "delete"),
    ("remove_", "delete"),
    ("uninstall_", "delete"),
    ("drop_", "delete"),
    ("write_", "write"),
    ("edit_", "write"),
    ("create_", "write"),
    ("update_", "write"),
    ("move_", "move"),
    ("rename_", "move"),
    ("read_", "read"),
    ("list_", "read"),
    ("get_", "read"),
    ("view_", "read"),
    ("search_", "read"),
    ("find_", "read"),
    ("run_", "execute"),
    ("execute_", "execute"),
    ("spawn_", "execute"),
)


def _infer_operation_from_tool(tool: str) -> str | None:
    """根据工具名前缀推断 OperationKind 字符串。无匹配 → None。

    与 ``classifier._heuristic_classify`` 用同一份前缀表的精神（按"严格度
    高者优先"排），但映射到操作类别而非 ApprovalClass。这是一个**保守
    回退**：如果有更精确的 ``risk_intent.classify_risk_intent`` 结果，
    应优先使用（C7 RiskGate 接入时通过 ``ToolCallEvent.metadata`` 透传）。
    """
    if not tool:
        return None
    for prefix, op in _OP_PREFIX_MAP:
        if tool.startswith(prefix):
            return op
    return None


def _path_under(raw: str, protected: str) -> bool:
    """Path-component-boundary "is raw under protected directory?" check.

    ``/etc/ssh-old/x`` vs ``/etc/ssh`` → False（不同目录，仅前缀字符相同）
    ``/etc/ssh/sshd_config`` vs ``/etc/ssh`` → True
    ``/etc/ssh`` vs ``/etc/ssh`` → True
    ``/etc/ssh/x`` vs ``/etc/ssh/**`` → True（C6 修复：支持 glob ``/**`` 后缀）
    ``C:/Windows/System32/x`` vs ``C:/Windows/**`` → True

    跨平台归一化：
    - ``\\`` → ``/``（Windows 路径）
    - 多连续斜杠 → 单斜杠（兼容 UNC ``\\\\server\\share`` / 用户输入失误）
    - 大小写不敏感（Windows 兜底；Linux 名义敏感但 immune 配置用大小写匹配
      反人类，统一不敏感降低 false negative 风险）
    - trailing slash 去除
    - **C6 修复**：``protected`` 末尾的 ``/**``（甚至 ``/***`` 等）剥掉视为
      "directory anchor"，前缀语义照常生效。POLICIES.yaml 习惯写
      ``C:/Windows/**`` 表示"该目录下所有内容"，旧实现把 ``**`` 当字面字符
      导致永远 false negative —— 已是 5+ 月隐性 bug，C5 引入未察觉。
    - 中段 glob（如 ``/etc/*/secret``）仍按字面处理，不做 fnmatch；如果未来
      用户需要 fnmatch 支持，建议在 schema 层把 pattern 拆字段（exact_paths
      vs glob_patterns），而不是在匹配器里做万能 glob（性能/语义都更可控）。
    - 空 protected → 不视为"匹配一切"，返回 False
    """
    raw_norm = _normalize_path(raw)
    prot_norm = _strip_glob_anchor(_normalize_path(protected))
    if not prot_norm:
        return False
    return raw_norm == prot_norm or raw_norm.startswith(prot_norm + "/")


def _strip_glob_anchor(p: str) -> str:
    """剥掉路径末尾的 ``/**``（或 ``/*``）glob 锚定符，返回纯目录前缀。

    举例：
    - ``c:/windows/**`` → ``c:/windows``
    - ``/etc/**/`` → ``/etc``（先剥 trailing slash，再剥 ``/**``）
    - ``/etc/ssh`` → ``/etc/ssh``（无变化）
    - ``**`` → ``""``（无意义模式）
    """
    if not p:
        return ""
    s = p.rstrip("/")
    while s.endswith("/**") or s.endswith("/*"):
        s = s[:-3] if s.endswith("/**") else s[:-2]
    if s in {"**", "*"}:
        return ""
    return s


def _normalize_path(p: str) -> str:
    """Collapse \\ → /, multiple slashes → single, lowercase, strip trailing /."""
    if not p:
        return ""
    # Replace backslashes, collapse repeats
    out = p.replace("\\", "/")
    while "//" in out:
        out = out.replace("//", "/")
    return out.lower().rstrip("/")


_INTENT_SIGNAL_FIELDS: tuple[str, ...] = (
    "risk_level",
    "operation_kind",
    "operation",
    "intent",
)
_INTENT_NEUTRAL_VALUES: frozenset[str] = frozenset(
    {"", "readonly", "none", "low", "read", "explain", "inspect", "suggest"}
)


def _extract_risk_signal(risk_intent: Any) -> str | None:
    """从 v1 ``RiskIntentResult`` 结果里抽出风险信号字符串。

    实际字段（v1 ``risk_intent.RiskIntentResult``）：
    - ``risk_level: RiskLevel`` （StrEnum: none/low/medium/high）
    - ``operation_kind: OperationKind`` （StrEnum: write/delete/overwrite/...）
    - ``requires_confirmation: bool`` （直接信号）

    优先级：
    1. ``requires_confirmation=True`` → 返回 'requires_confirmation'（最直接信号）
    2. 任一字段值非中性（在 ``_INTENT_NEUTRAL_VALUES`` 之外）→ 返回小写值
    3. 否则 → None

    输入类型：dict / dataclass / object / str / None 都接。
    """
    if risk_intent is None:
        return None
    if isinstance(risk_intent, str):
        lowered = risk_intent.strip().lower()
        return None if lowered in _INTENT_NEUTRAL_VALUES else lowered

    # 优先看 requires_confirmation 直接信号
    if _intent_requires_confirmation(risk_intent):
        return "requires_confirmation"

    if isinstance(risk_intent, dict):
        for key in _INTENT_SIGNAL_FIELDS:
            value = risk_intent.get(key)
            if value:
                lowered = _stringify(value).lower()
                if lowered and lowered not in _INTENT_NEUTRAL_VALUES:
                    return lowered
        return None

    # dataclass / 普通 object
    for attr in _INTENT_SIGNAL_FIELDS:
        value = getattr(risk_intent, attr, None)
        if value is None:
            continue
        lowered = _stringify(value).lower()
        if lowered and lowered not in _INTENT_NEUTRAL_VALUES:
            return lowered
    return None


def _intent_requires_confirmation(risk_intent: Any) -> bool:
    if isinstance(risk_intent, dict):
        return bool(risk_intent.get("requires_confirmation"))
    return bool(getattr(risk_intent, "requires_confirmation", False))


def _stringify(value: Any) -> str:
    """Enum → .value；其他 → str；StrEnum 自动得 value。"""
    text = getattr(value, "value", None)
    return str(text) if text is not None else str(value)


def build_engine_from_config(
    config: PolicyConfigV2,
    *,
    explicit_lookup: Any = None,
    skill_lookup: Any = None,
    mcp_lookup: Any = None,
    plugin_lookup: Any = None,
    audit_hook: Any = None,
    audit_intent_hook: Any = None,
) -> PolicyEngineV2:
    """工厂函数：从 ``PolicyConfigV2`` 构造一个完整布线的 ``PolicyEngineV2``。

    封装两件事：
    1. 用 ``config.shell_risk`` 构造 ``ApprovalClassifier``（customs / blocked
       全部透传）
    2. 把 ``config`` 传给 ``PolicyEngineV2`` 启用 owner_only / overrides /
       safety_immune / unattended

    生产典型用法（C8 wire-up）::

        cfg, _ = load_policies_yaml(Path("identity/POLICIES.yaml"))
        engine = build_engine_from_config(
            cfg,
            explicit_lookup=registry.get_tool_class,
            skill_lookup=skill_registry.get_tool_metadata,
            audit_hook=audit_logger.log_tool_decision,
            audit_intent_hook=audit_logger.log_intent_decision,
        )
    """
    classifier = ApprovalClassifier(
        explicit_lookup=explicit_lookup,
        skill_lookup=skill_lookup,
        mcp_lookup=mcp_lookup,
        plugin_lookup=plugin_lookup,
        shell_risk_config=config.shell_risk,
    )
    return PolicyEngineV2(
        classifier=classifier,
        config=config,
        audit_hook=audit_hook,
        audit_intent_hook=audit_intent_hook,
    )


# Re-export for convenience
__all__ = [
    "PolicyEngineV2",
    "ShellRiskLevel",
    "DecisionSource",
    "build_engine_from_config",
]

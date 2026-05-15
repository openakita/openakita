"""ApprovalClassifier — Tool → ApprovalClass 5+ 步分类链。

分类来源（按 DecisionSource）：

1. **EXPLICIT_REGISTER_PARAM**：`agent.py:_init_handlers` 调用
   `registry.register(handler, tool_classes={...})` 注入（C8 接入 30+ 处）
2. **EXPLICIT_HANDLER_ATTR**：handler 类的 `TOOL_CLASSES` 类属性自治声明
3. **SKILL_METADATA**：SKILL.md frontmatter `risk_class`（C15 + trust_level 接入）
4. **MCP_ANNOTATION**：MCP server `tool.annotations` （C15 接入）
5. **PLUGIN_PREFIX**：plugin manifest 声明（C10 接入）
6. **HEURISTIC_PREFIX**：工具名前缀启发式（按 docs §4.21.2 表）
7. **FALLBACK_UNKNOWN**：兜底（保守 ask 一次，由 matrix 决定）

多源同时声明时按 `enums.most_strict` 取严格度大者（safety-by-default）。

C2 阶段：1+2+6+7 落地，3/4/5 提供 callback 接口预留。
跨盘 refine（write_file/edit_file/move_file 写出 workspace 升级 MUTATING_GLOBAL）
在 `_refine_with_params` 落地。shell command refine 留给 C3 接入 `shell_risk.py`。

性能：base classify 走 LRU cache（OrderedDict 实现，避免 functools.lru_cache
持有 self 引用泄漏；plugin 动态注册新工具时通过 `invalidate()` 失效）。
refine 不缓存（依赖 params + ctx）。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .enums import ApprovalClass, DecisionSource, most_strict
from .shell_risk import ShellRiskLevel, classify_shell_command
from .zones import all_paths_inside_workspace

if TYPE_CHECKING:
    from .context import PolicyContext
    from .schema import ShellRiskConfig


# Callback 类型：tool_name → (ApprovalClass, DecisionSource) | None
ClassLookup = Callable[[str], "tuple[ApprovalClass, DecisionSource] | None"]


# Shell-类工具名（接 shell_risk 的 refine）
_SHELL_TOOLS: frozenset[str] = frozenset(
    {"run_shell", "run_powershell", "opencli_run", "cli_anything_run"}
)


@dataclass(slots=True, frozen=True)
class ClassificationResult:
    """``classify_full()`` 的返回，富信息版（R2-5）。

    PolicyEngineV2 step 2 调本结构一次拿到所有需要的字段，避免后续 step 重算
    shell_risk / sandbox 推荐等。``classify_with_source()`` 仍保持原签名供
    简单场景 + 测试。
    """

    approval_class: ApprovalClass
    source: DecisionSource
    shell_risk_level: ShellRiskLevel | None = None
    needs_sandbox: bool = False
    needs_checkpoint: bool = False


# docs §4.21.2 启发式表（仅启发式兜底；优先级从高到低）
# exact match 优先于前缀匹配（更精准）
#
# C6 扩展：补充 v1 PLAN/ASK/COORDINATOR ruleset 已默认 ALLOW 的常见内置工具，
# 防止 v2 切上来后这些工具掉到 UNKNOWN→CONFIRM 触发不必要的弹窗。完整
# tool→class 注册建议在 C7 配合 agent.py 经 handler.TOOL_CLASSES 完成；本表
# 仅覆盖最高频的"控制 / 内部状态 / 网络读"类，避免回归。
_HEURISTIC_EXACT_MATCH: dict[str, ApprovalClass] = {
    # 原有
    "grep": ApprovalClass.READONLY_SEARCH,
    "glob": ApprovalClass.READONLY_SEARCH,
    "switch_persona": ApprovalClass.CONTROL_PLANE,
    "setup_organization": ApprovalClass.CONTROL_PLANE,
    # C6 新增 —— 常见交互/控制工具
    "ask_user": ApprovalClass.INTERACTIVE,
    "exit_plan_mode": ApprovalClass.INTERACTIVE,
    "task_stop": ApprovalClass.INTERACTIVE,
    "pet_say": ApprovalClass.INTERACTIVE,
    "pet_status_update": ApprovalClass.INTERACTIVE,
    "send_agent_message": ApprovalClass.INTERACTIVE,
    # C6 新增 —— 内部 todo / memory 状态
    "complete_todo": ApprovalClass.EXEC_LOW_RISK,
    "add_memory": ApprovalClass.EXEC_LOW_RISK,
    "trace_memory": ApprovalClass.READONLY_GLOBAL,
    # C6 新增 —— 多 agent 协调（CONTROL_PLANE 在 trust 模式 ALLOW，default CONFIRM）
    "delegate_to_agent": ApprovalClass.CONTROL_PLANE,
    "delegate_parallel": ApprovalClass.CONTROL_PLANE,
}

# 前缀按"严格度高者优先"排：DESTRUCTIVE > CONTROL_PLANE > EXEC_CAPABLE > MUTATING > 只读
# 防止 `delete_remote_data` 同时匹配 `delete_` 与 `remote_` 时被低优先覆盖
_HEURISTIC_PREFIXES: tuple[tuple[str, ApprovalClass], ...] = (
    # DESTRUCTIVE（不可恢复）
    ("delete_", ApprovalClass.DESTRUCTIVE),
    ("uninstall_", ApprovalClass.DESTRUCTIVE),
    ("remove_", ApprovalClass.DESTRUCTIVE),
    ("drop_", ApprovalClass.DESTRUCTIVE),
    # CONTROL_PLANE
    ("schedule_", ApprovalClass.CONTROL_PLANE),
    ("cron_", ApprovalClass.CONTROL_PLANE),
    ("system_", ApprovalClass.CONTROL_PLANE),
    ("evolution_", ApprovalClass.CONTROL_PLANE),
    # EXEC_CAPABLE
    ("run_", ApprovalClass.EXEC_CAPABLE),
    ("execute_", ApprovalClass.EXEC_CAPABLE),
    ("spawn_", ApprovalClass.EXEC_CAPABLE),
    ("kill_", ApprovalClass.EXEC_CAPABLE),
    # MUTATING_SCOPED（跨盘升级在 _refine_with_params 处理）
    ("write_", ApprovalClass.MUTATING_SCOPED),
    ("edit_", ApprovalClass.MUTATING_SCOPED),
    ("create_", ApprovalClass.MUTATING_SCOPED),
    ("move_", ApprovalClass.MUTATING_SCOPED),
    ("rename_", ApprovalClass.MUTATING_SCOPED),
    ("update_", ApprovalClass.MUTATING_SCOPED),
    # NETWORK_OUT（C6 新增）—— web_/news_ 是惯例的网络只读类
    ("web_", ApprovalClass.NETWORK_OUT),
    ("news_", ApprovalClass.NETWORK_OUT),
    # READONLY_SEARCH
    ("search_", ApprovalClass.READONLY_SEARCH),
    ("find_", ApprovalClass.READONLY_SEARCH),
    # READONLY_GLOBAL
    ("read_", ApprovalClass.READONLY_GLOBAL),
    ("list_", ApprovalClass.READONLY_GLOBAL),
    ("get_", ApprovalClass.READONLY_GLOBAL),
    ("view_", ApprovalClass.READONLY_GLOBAL),
)


# refine 应用到这些工具：path 不在 workspace 时升级 MUTATING_SCOPED → MUTATING_GLOBAL
_PATH_BASED_REFINE_TOOLS: frozenset[str] = frozenset(
    {"write_file", "edit_file", "move_file", "create_plan_file", "edit_notebook"}
)


class ApprovalClassifier:
    """5+ 步分类链 + 启发式兜底 + LRU cache。

    用法：
        classifier = ApprovalClassifier(
            explicit_lookup=registry.get_tool_class,  # 可选
            skill_lookup=skill_registry.get_tool_metadata,  # 可选 (C15)
        )
        klass, source = classifier.classify_with_source("write_file", {"path": ...}, ctx)
    """

    def __init__(
        self,
        *,
        explicit_lookup: ClassLookup | None = None,
        skill_lookup: ClassLookup | None = None,
        mcp_lookup: ClassLookup | None = None,
        plugin_lookup: ClassLookup | None = None,
        cache_size: int = 256,
        shell_risk_config: ShellRiskConfig | None = None,
    ) -> None:
        """构造分类器。

        ``shell_risk_config``（C5）：``POLICIES.yaml`` 的 ``shell_risk`` 配置块。
        启用时把用户自定义的 ``custom_critical/high/medium`` patterns 与
        ``blocked_commands`` / ``excluded_patterns`` 透传给
        ``classify_shell_command``。``None`` 或 ``enabled=False`` 时跳过 shell
        refine（与 v1 ``command_patterns.enabled=False`` 行为一致）。
        """
        self._explicit_lookup = explicit_lookup
        self._skill_lookup = skill_lookup
        self._mcp_lookup = mcp_lookup
        self._plugin_lookup = plugin_lookup
        self._cache_size = cache_size
        self._base_cache: OrderedDict[str, tuple[ApprovalClass, DecisionSource]] = OrderedDict()
        # C21 P0-3: explicit lock around _base_cache operations.
        # Pre-C21 relied on "CPython OrderedDict 单 op 由 GIL 保证原子性 +
        # try/except KeyError 兜住竞态" — correctness was maintained but the
        # composite get→move_to_end→set→popitem sequences had observable races
        # (cache_size temporarily exceeding _cache_size, double classification
        # of the same tool, etc.). plan §22.3 had promised "thread-safe LRU
        # cache" but the original OrderedDict-based impl never delivered the
        # invariant. With the lock, every cache mutation is serialized within
        # the classifier and we can drop the defensive try/except (kept as
        # commented "belt and suspenders" — see _classify_base).
        self._cache_lock = threading.Lock()
        self._shell_risk_config = shell_risk_config

    # ---- public API ----

    def classify(
        self,
        tool: str,
        params: dict | None = None,
        ctx: PolicyContext | None = None,
    ) -> ApprovalClass:
        return self.classify_full(tool, params, ctx).approval_class

    def classify_with_source(
        self,
        tool: str,
        params: dict | None = None,
        ctx: PolicyContext | None = None,
    ) -> tuple[ApprovalClass, DecisionSource]:
        """简单入口。返回 refined ApprovalClass + 原始 DecisionSource。

        DecisionSource 反映"基础分类的来源"，不反映 refine 结果（refine 不改变
        来源信任度，只调整严格度）。这样 C19 completeness test 能准确判断
        "工具是否走了启发式回退"。
        """
        result = self.classify_full(tool, params, ctx)
        return result.approval_class, result.source

    def classify_full(
        self,
        tool: str,
        params: dict | None = None,
        ctx: PolicyContext | None = None,
    ) -> ClassificationResult:
        """富信息入口（R2-5：一次性算 shell_risk_level / needs_sandbox / needs_checkpoint）。

        PolicyEngineV2 step 2 调本方法，返回值直接喂给后续决策步。
        """
        base, source = self._classify_base(tool)
        params = params or {}
        return self._refine_with_params_full(base, source, tool, params, ctx)

    def invalidate(self, tool: str | None = None) -> None:
        """清除缓存。plugin 动态注册新工具时调用。"""
        with self._cache_lock:
            if tool is None:
                self._base_cache.clear()
            else:
                self._base_cache.pop(tool, None)

    @property
    def cache_size(self) -> int:
        with self._cache_lock:
            return len(self._base_cache)

    # ---- internals ----

    def _classify_base(self, tool: str) -> tuple[ApprovalClass, DecisionSource]:
        """5 步分类链（带线程安全的 LRU cache）。

        Thread-safety (C21 P0-3): every read+update of ``_base_cache`` is
        serialized via ``_cache_lock``. The classification itself runs
        **outside** the lock — handler / skill / mcp / plugin lookup
        callbacks may take their own locks (registries) and we don't want
        to nest them under the cache lock. Worst-case the same tool may
        be classified twice if two threads race past the cache miss, but
        the result is deterministic so the cache converges immediately.

        Cache eviction (``popitem(last=False)``) and reordering
        (``move_to_end``) happen under the lock; the defensive
        try/except KeyError that the pre-C21 implementation relied on is
        no longer reachable but kept commented out below so future readers
        understand the historical rationale.
        """
        with self._cache_lock:
            cached = self._base_cache.get(tool)
            if cached is not None:
                # Atomic under lock: get + move_to_end can't race anymore.
                self._base_cache.move_to_end(tool)
                return cached

        # Cache miss: do the classification OUTSIDE the lock so lookup
        # callbacks can take their own locks without risking deadlock.
        result = self._classify_base_uncached(tool)

        with self._cache_lock:
            # Re-check: another thread may have populated the entry while
            # we were classifying. Either result is correct (deterministic
            # classification), but keep the first-wins behaviour for cache
            # locality.
            existing = self._base_cache.get(tool)
            if existing is not None:
                self._base_cache.move_to_end(tool)
                return existing
            self._base_cache[tool] = result
            if len(self._base_cache) > self._cache_size:
                # Lock guarantees popitem(last=False) is safe; KeyError no
                # longer possible. Kept the conditional for explicitness.
                self._base_cache.popitem(last=False)
        return result

    def _classify_base_uncached(self, tool: str) -> tuple[ApprovalClass, DecisionSource]:
        candidates: list[tuple[ApprovalClass, DecisionSource]] = []

        if self._explicit_lookup is not None:
            hit = self._explicit_lookup(tool)
            if hit is not None:
                candidates.append(hit)

        for lookup, default_source in (
            (self._skill_lookup, DecisionSource.SKILL_METADATA),
            (self._mcp_lookup, DecisionSource.MCP_ANNOTATION),
            (self._plugin_lookup, DecisionSource.PLUGIN_PREFIX),
        ):
            if lookup is None:
                continue
            hit = lookup(tool)
            if hit is None:
                continue
            klass, source = hit
            # lookup 可不显式回 source；若没明确给，用 default_source 标
            if source is None:
                source = default_source
            candidates.append((klass, source))

        # 任一显式来源命中 → 多源叠加取严
        if candidates:
            return most_strict(candidates)

        # 启发式兜底
        heuristic = _heuristic_classify(tool)
        if heuristic is not None:
            return heuristic, DecisionSource.HEURISTIC_PREFIX

        return ApprovalClass.UNKNOWN, DecisionSource.FALLBACK_UNKNOWN

    def _refine_with_params_full(
        self,
        base: ApprovalClass,
        source: DecisionSource,
        tool: str,
        params: dict,
        ctx: PolicyContext | None,
    ) -> ClassificationResult:
        """完整 refine：path-based 升级 + shell command 升降级 + 推荐 sandbox/checkpoint。

        Path refine（继承 C2）：MUTATING_SCOPED 且 path 越界 → MUTATING_GLOBAL。
        Shell refine（C3 新增）：
        - run_shell 命令命中 BLOCKED token → CONTROL_PLANE（matrix DENY）
        - 命中 CRITICAL pattern → DESTRUCTIVE
        - 命中 HIGH pattern → DESTRUCTIVE + needs_sandbox
        - 命中 MEDIUM pattern → MUTATING_GLOBAL + needs_sandbox
        - LOW（默认） → 保留 EXEC_CAPABLE 或降级 EXEC_LOW_RISK（按命令名简单启发）

        Checkpoint 推荐：DESTRUCTIVE / MUTATING_GLOBAL 都建议先快照（C8 接入实际触发）。

        ctx 为 None 时不做 path/shell refine（保守不降级，但保留 base 类）。
        """
        klass = base
        shell_risk: ShellRiskLevel | None = None
        needs_sandbox = False

        # Shell refine（独立路径，覆盖 EXEC_CAPABLE → 多种细分）
        if tool in _SHELL_TOOLS and self._shell_risk_enabled():
            command = params.get("command") or params.get("script") or ""
            if isinstance(command, str) and command:
                shell_risk = self._classify_shell_with_customs(command)
                klass, needs_sandbox = self._apply_shell_risk(klass, shell_risk)

        # Path refine（与 shell refine 互斥：shell 工具不走 path 检查）
        elif (
            base == ApprovalClass.MUTATING_SCOPED
            and tool in _PATH_BASED_REFINE_TOOLS
            and ctx is not None
        ):
            if not all_paths_inside_workspace(params, ctx.workspace_roots):
                klass = ApprovalClass.MUTATING_GLOBAL

        needs_checkpoint = klass in (
            ApprovalClass.DESTRUCTIVE,
            ApprovalClass.MUTATING_GLOBAL,
        )

        return ClassificationResult(
            approval_class=klass,
            source=source,
            shell_risk_level=shell_risk,
            needs_sandbox=needs_sandbox,
            needs_checkpoint=needs_checkpoint,
        )

    def _shell_risk_enabled(self) -> bool:
        """``shell_risk_config is None`` 视为启用（向后兼容；C5 之前的代码路径
        没传 config 也要照常工作）。显式 ``enabled=False`` 才禁用。
        """
        cfg = self._shell_risk_config
        return cfg is None or bool(getattr(cfg, "enabled", True))

    def _classify_shell_with_customs(self, command: str) -> ShellRiskLevel:
        """把 shell_risk_config 的 customs 透传给 ``classify_shell_command``。

        ``shell_risk_config is None`` → 用 module 默认 patterns（与 C3 行为完全一致）。
        """
        cfg = self._shell_risk_config
        if cfg is None:
            return classify_shell_command(command)
        return classify_shell_command(
            command,
            extra_critical=list(getattr(cfg, "custom_critical", []) or []),
            extra_high=list(getattr(cfg, "custom_high", []) or []),
            extra_medium=list(getattr(cfg, "custom_medium", []) or []),
            blocked_tokens=list(getattr(cfg, "blocked_commands", []) or []) or None,
            excluded_patterns=list(getattr(cfg, "excluded_patterns", []) or []),
        )

    @staticmethod
    def _apply_shell_risk(
        base: ApprovalClass,
        shell_risk: ShellRiskLevel,
    ) -> tuple[ApprovalClass, bool]:
        """Shell command 分类 → ApprovalClass 升降。

        BLOCKED → DESTRUCTIVE（matrix 在 trust 仍 CONFIRM；strict DENY；
        engine 配合 step 7+ blocked_tokens 直接 DENY）；needs_sandbox=False
        因为 BLOCKED 命令不允许放沙箱执行（直接拒）。
        """
        if shell_risk == ShellRiskLevel.BLOCKED:
            return ApprovalClass.DESTRUCTIVE, False
        if shell_risk == ShellRiskLevel.CRITICAL:
            return ApprovalClass.DESTRUCTIVE, True
        if shell_risk == ShellRiskLevel.HIGH:
            return ApprovalClass.DESTRUCTIVE, True
        if shell_risk == ShellRiskLevel.MEDIUM:
            return ApprovalClass.MUTATING_GLOBAL, True
        return base, False


# ---- module-level helpers (无状态，便于测试 / 复用) ----


def _heuristic_classify(tool: str) -> ApprovalClass | None:
    """按 docs §4.21.2 启发式表归类。无匹配返回 None。"""
    if not tool:
        return None
    exact = _HEURISTIC_EXACT_MATCH.get(tool)
    if exact is not None:
        return exact
    for prefix, klass in _HEURISTIC_PREFIXES:
        if tool.startswith(prefix):
            return klass
    return None


# Public alias of the heuristic classifier (C15 §17.3).
# ``declared_class_trust.compute_effective_class`` calls into this when
# a default-trusted Skill / MCP declaration must be cross-checked with
# the prefix/exact-name heuristic. Re-exporting under the public name
# avoids reaching into a private symbol from a sibling module.
def heuristic_classify(tool: str) -> ApprovalClass | None:
    """Public re-export of :func:`_heuristic_classify` (C15)."""
    return _heuristic_classify(tool)


# Backward-compat alias — old tests import classifier._is_inside_workspace.
# Canonical implementation moved to ``zones.is_inside_workspace`` in C3.
# Kept here as a re-export so test_classifier.py 不需重写一大片，并避免外部
# 已 import 的代码瞬时断裂；C8 `policy_v2.zones` 公开后可随手清掉。
from .zones import is_inside_workspace as _is_inside_workspace  # noqa: E402, F401

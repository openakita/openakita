"""policy_v2 全局引擎单例（C6）。

设计目标
========

1. **延迟加载（lazy）**：模块 import 时不读 YAML、不构造引擎；首次 ``get_engine_v2()``
   触发加载，避免 import-time I/O 与启动顺序耦合。
2. **线程安全**：用 ``threading.Lock`` 保护单例初始化，防止并发首次调用产生两个引擎。
3. **测试友好**：提供 ``set_engine_v2`` / ``reset_engine_v2``，让 pytest 能在 fixture
   里替换实例并清理状态。
4. **Explicit-lookup 注入点**：默认无 ``explicit_lookup``（classifier 仅依赖
   TOOL_CLASS_MATRIX/启发式）；运行时（如 agent 启动后）可通过 ``rebuild_engine_v2``
   传入 ``SystemHandlerRegistry.get_tool_class`` 拿到 handler 显式声明的
   ApprovalClass。

YAML 路径解析
=============

- 优先 ``settings.identity_path / "POLICIES.yaml"``（与 v1 ``policy.get_policy_engine``
  对齐）。
- ``identity_path`` 不可用时退回 ``Path("identity/POLICIES.yaml")``（CLI / 单元测试
  容错）。
- ``load_policies_yaml`` 自身已处理"文件不存在 → 默认配置 + WARN log"，所以本模块不再
  捕获 FileNotFoundError。

为什么不复用 v1 的 ``get_policy_engine``
========================================

v1 单例构造的是 ``PolicyEngine``（含旧决策逻辑、UI 状态机、session 缓存）；v2 单例只负责
**决策**（``PolicyEngineV2``）。两个单例并存是 C6 阶段的过渡策略——决策走 v2、UI 状态留
v1（C9 重建后才能合并到一个）。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .engine import PolicyEngineV2, build_engine_from_config
from .loader import PolicyConfigError, load_policies_yaml
from .schema import PolicyConfigV2

if TYPE_CHECKING:
    from .enums import ApprovalClass, DecisionSource

logger = logging.getLogger(__name__)

ExplicitLookup = Callable[[str], "tuple[ApprovalClass, DecisionSource] | None"]
SkillLookup = Callable[[str], "tuple[ApprovalClass, DecisionSource] | None"]
McpLookup = Callable[[str], "tuple[ApprovalClass, DecisionSource] | None"]
PluginLookup = Callable[[str], "tuple[ApprovalClass, DecisionSource] | None"]

_engine: PolicyEngineV2 | None = None
_config: PolicyConfigV2 | None = None
# 注册表 explicit_lookup 必须**跨 reset 存活**：UI Save Settings 走
# ``api/routes/config.py → reset_policy_engine() → reset_engine_v2()``，
# 之后 ``get_engine_v2()`` 懒加载若不带 explicit_lookup，138 个 handler
# 显式声明的 ApprovalClass 会全部退化到启发式分类（C7 二轮 audit 复现）。
# 这里持久化一份，让任何 rebuild/lazy-load 路径都能恢复。
_explicit_lookup: ExplicitLookup | None = None
# C10：skill / mcp / plugin lookup 也持久化到模块缓存，原因同上。
# UI hot-reload 不应让"插件 / 技能 / MCP 自报 ApprovalClass"全部退化。
_skill_lookup: SkillLookup | None = None
_mcp_lookup: McpLookup | None = None
_plugin_lookup: PluginLookup | None = None
_lock = threading.Lock()

# C16 Phase B：last-known-good (LKG) 缓存。
# 当 POLICIES.yaml 校验失败时（攻击者篡改 / 操作员 typo），下一次加载会优先
# 用 LKG 而不是回退到 PolicyConfigV2() 全 default——后者会让 safety_immune /
# approval_classes / shell_risk 等用户精心配置的字段全部消失。
# 首次启动校验失败时仍走 default fallback，保留"操作员第一次启动有 typo 也
# 不会被锁死"的体验。
# 锁是独立的 ``_LKG_LOCK``，避免和 ``_lock`` 形成嵌套（rebuild 已持 ``_lock``）。
_LAST_KNOWN_GOOD: PolicyConfigV2 | None = None
_LKG_LOCK = threading.Lock()


def _set_last_known_good(cfg: PolicyConfigV2) -> None:
    global _LAST_KNOWN_GOOD
    with _LKG_LOCK:
        _LAST_KNOWN_GOOD = cfg


def _get_last_known_good() -> PolicyConfigV2 | None:
    with _LKG_LOCK:
        return _LAST_KNOWN_GOOD


def _clear_last_known_good() -> None:
    global _LAST_KNOWN_GOOD
    with _LKG_LOCK:
        _LAST_KNOWN_GOOD = None


def _recover_from_load_failure(exc: Exception, *, source: str) -> PolicyConfigV2:
    """C16 Phase B: pick a config when YAML validation / loading fails.

    Order of preference:

    1. **Last-known-good** if a previous load on this process succeeded.
       Logged as ERROR so operators see it; the *previously* valid config
       keeps protecting them while they fix the file.
    2. ``PolicyConfigV2()`` defaults otherwise (first-load failure: the
       process has nothing else to fall back to; this matches pre-C16
       behaviour and avoids locking out operators with typos at first
       run).

    Returns the chosen config so the caller can build an engine.
    """
    lkg = _get_last_known_good()
    if lkg is not None:
        logger.error(
            "[PolicyV2] POLICIES.yaml failed validation at %s: %s. "
            "Keeping last-known-good config (security settings preserved).",
            source,
            exc,
        )
        return lkg
    logger.error(
        "[PolicyV2] POLICIES.yaml failed validation at %s: %s. "
        "No last-known-good available; falling back to defaults.",
        source,
        exc,
    )
    return PolicyConfigV2()


def _resolve_yaml_path() -> Path | None:
    """识别 POLICIES.yaml 路径。返回 None 时调用方应让 loader 用默认配置。

    优先级（C18 Phase C）：

    1. ``OPENAKITA_POLICY_FILE`` 环境变量（操作员用 helm / docker run -e
       注入 alternate path 的标准入口）。
    2. ``settings.identity_path / POLICIES.yaml``（应用配置）。
    3. ``identity/POLICIES.yaml``（运行目录下的 fallback）。

    返回的 ``Path`` 不要求 ``.exists()``——load_policies_yaml 内部会
    自己处理 "文件不存在 → defaults" 并写 WARN。这让 ENV 设了一个尚
    未挂载的路径时报错更明确（"YAML 不存在"而非"识别失败"）。
    """
    import os as _os

    env_path = _os.environ.get("OPENAKITA_POLICY_FILE", "").strip()
    if env_path:
        return Path(env_path)

    try:
        from ...config import settings

        identity_path = getattr(settings, "identity_path", None)
        if identity_path is not None:
            return Path(identity_path) / "POLICIES.yaml"
    except Exception as exc:
        logger.debug("[PolicyV2] settings.identity_path unavailable: %s", exc)

    fallback = Path("identity/POLICIES.yaml")
    if fallback.exists():
        return fallback
    return None


def _audit_env_overrides(report, cfg: PolicyConfigV2 | None = None) -> None:
    """C18 Phase C：把 ENV 覆盖写入审计链。

    每次 ``load_policies_yaml`` 后调用一次；空报告（无 ENV 设置）直接
    跳过——避免审计链被无意义的"没人改"行刷满。``skipped_errors``
    单独记一行，便于 verify_chain 后续审查 "ENV typo 期间是不是有别
    的 actor 也在改配置"。

    C18 二轮 audit 修复（latent deadlock）：这个函数会被
    ``rebuild_engine_v2`` 在持 ``_lock`` 状态下调用。如果走默认的
    ``get_audit_logger()`` 单例路径，当 ``_global_audit is None`` 时
    它会反过来调 ``get_config_v2()`` 再次尝试 acquire 同一把非
    reentrant 锁，造成进程死锁。生产环境下通常不暴露——因为
    ``_global_audit`` 在 rebuild 之前早被其他调用 lazy init。但
    hot-reload + ``reset_audit_logger`` + 同时有 ENV override 的场
    景里三件凑齐就死。

    正确做法：直接用刚加载完成的 ``cfg`` 构造一次性 ``AuditLogger``，
    绕开单例 + 绕开 ``get_config_v2()`` 的锁。``cfg`` 不可用时退化
    成单例（旧行为）以保持启动期兼容。
    """
    if report is None:
        return
    override_report = getattr(report, "env_overrides", None)
    if override_report is None or not override_report.has_any():
        return
    try:
        if cfg is not None:
            from ..audit_logger import DEFAULT_AUDIT_PATH, AuditLogger

            logger_inst: Any = AuditLogger(
                path=cfg.audit.log_path or DEFAULT_AUDIT_PATH,
                enabled=cfg.audit.enabled,
                include_chain=getattr(cfg.audit, "include_chain", True),
            )
        else:
            from ..audit_logger import get_audit_logger

            logger_inst = get_audit_logger()

        if override_report.applied:
            logger_inst.log(
                tool_name="<policy_env_override>",
                decision="env_override_applied",
                reason=f"{len(override_report.applied)} ENV var(s) applied",
                policy="policy_env_override",
                metadata={
                    "applied": override_report.applied,
                    "skipped_count": len(override_report.skipped_errors),
                },
            )
        if override_report.skipped_errors:
            logger_inst.log(
                tool_name="<policy_env_override>",
                decision="env_override_invalid",
                reason=(
                    f"{len(override_report.skipped_errors)} ENV var(s) "
                    "had invalid values; YAML defaults kept"
                ),
                policy="policy_env_override",
                metadata={
                    "skipped_errors": override_report.skipped_errors,
                },
            )
    except Exception:
        logger.exception("[PolicyV2] failed to write ENV override audit row")


def _build_default_engine(
    *,
    explicit_lookup: ExplicitLookup | None = None,
    skill_lookup: SkillLookup | None = None,
    mcp_lookup: McpLookup | None = None,
    plugin_lookup: PluginLookup | None = None,
) -> tuple[PolicyEngineV2, PolicyConfigV2]:
    """从 ``identity/POLICIES.yaml``（或默认配置）构造引擎。

    ``explicit_lookup`` 不传时回退到模块级缓存（见 ``_explicit_lookup`` 注释）。
    """
    yaml_path = _resolve_yaml_path()
    # C16 Phase B：用 strict=True 让 ValidationError 抛上来——loader 在
    # strict=False 模式下会静默把整个 config 回退到 defaults，这正是 R4-15
    # "篡改 yaml → 失去 safety_immune" 的攻击落地点。我们改在这里捕获，
    # 让 _recover_from_load_failure 优先用 LKG。
    try:
        cfg, report = load_policies_yaml(yaml_path, strict=True)
        _audit_env_overrides(report, cfg)
        if report.fields_migrated:
            logger.info(
                "[PolicyV2] global engine loaded with %d v1 fields migrated",
                len(report.fields_migrated),
            )
        if report.unknown_security_keys:
            logger.warning(
                "[PolicyV2] POLICIES.yaml has unknown keys under 'security' "
                "(typo or attack?): %s",
                ", ".join(report.unknown_security_keys),
            )
        # C16 Phase B：成功加载 → 记到 LKG，后续 ValidationError 才有救生圈。
        _set_last_known_good(cfg)
    except PolicyConfigError as exc:
        cfg = _recover_from_load_failure(
            exc,
            source=str(yaml_path) if yaml_path else "<no path>",
        )
    except Exception as exc:
        cfg = _recover_from_load_failure(
            exc,
            source=str(yaml_path) if yaml_path else "<no path>",
        )

    effective_explicit = explicit_lookup if explicit_lookup is not None else _explicit_lookup
    effective_skill = skill_lookup if skill_lookup is not None else _skill_lookup
    effective_mcp = mcp_lookup if mcp_lookup is not None else _mcp_lookup
    effective_plugin = plugin_lookup if plugin_lookup is not None else _plugin_lookup
    engine = build_engine_from_config(
        cfg,
        explicit_lookup=effective_explicit,
        skill_lookup=effective_skill,
        mcp_lookup=effective_mcp,
        plugin_lookup=effective_plugin,
    )
    return engine, cfg


def get_engine_v2() -> PolicyEngineV2:
    """获取全局 v2 引擎单例（线程安全、延迟加载）。

    首次调用时读取 ``identity/POLICIES.yaml`` 并构造。后续调用直接返回已缓存实例。
    """
    global _engine, _config
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is None:
            _engine, _config = _build_default_engine()
    return _engine


def get_config_v2() -> PolicyConfigV2:
    """获取当前生效的 v2 配置。会触发引擎初始化（保证 config 与 engine 同步）。"""
    get_engine_v2()
    assert _config is not None
    return _config


def set_engine_v2(engine: PolicyEngineV2, config: PolicyConfigV2 | None = None) -> None:
    """注入自定义引擎（测试 / 运行时 hot-swap）。

    ``config`` 可选；不传时 ``get_config_v2`` 仍会返回上一次缓存的配置（或
    ``PolicyConfigV2()`` 默认）。建议测试场景显式传入对应 config 以便断言。
    """
    global _engine, _config
    with _lock:
        _engine = engine
        if config is not None:
            _config = config
        elif _config is None:
            _config = PolicyConfigV2()


def reset_engine_v2(*, clear_explicit_lookup: bool = False) -> None:
    """清空单例（测试 fixture 用 / 配置 hot-reload C18）。

    默认**保留** ``_explicit_lookup`` / ``_skill_lookup`` / ``_mcp_lookup`` /
    ``_plugin_lookup``：UI Save Settings 走 ``reset_policy_engine`` → 这里 →
    下次 ``get_engine_v2()`` 懒加载时 ``_build_default_engine`` 会自动用回
    各注册表的查表，避免显式声明的 ApprovalClass 退化到启发式分类。

    Args:
        clear_explicit_lookup: 仅测试 fixture 用，需要彻底回到"未注册任何
            handler / skill / mcp / plugin"的初始状态时传 ``True``——会一并
            清空 4 个 lookup 缓存。
    """
    global _engine, _config, _explicit_lookup, _skill_lookup, _mcp_lookup, _plugin_lookup
    with _lock:
        _engine = None
        _config = None
        if clear_explicit_lookup:
            _explicit_lookup = None
            _skill_lookup = None
            _mcp_lookup = None
            _plugin_lookup = None


def rebuild_engine_v2(
    *,
    explicit_lookup: ExplicitLookup | None = None,
    skill_lookup: SkillLookup | None = None,
    mcp_lookup: McpLookup | None = None,
    plugin_lookup: PluginLookup | None = None,
    yaml_path: Path | str | None = None,
) -> PolicyEngineV2:
    """重建全局引擎并返回新实例。

    应用启动后（agent 拿到 ``SystemHandlerRegistry`` / SkillRegistry /
    PluginManager / MCPClient 实例后）应调用一次此函数把 4 个 lookup 全部
    注入，让 classifier 拿到 handler / skill / mcp / plugin 各自声明的
    ApprovalClass（详见 docs §4.21 cookbook + C10）。

    传入的 lookup 会**持久化**到模块缓存，让后续 ``reset_engine_v2()`` +
    懒加载（如 UI Save Settings 触发的配置 hot-reload）也能恢复显式分类——
    这是 C7 二轮 audit 修复的回归点，C10 把规则推广到全部 4 类来源。

    Args:
        explicit_lookup: handler.TOOL_CLASSES → ApprovalClass。
        skill_lookup: SKILL.md ``approval_class:`` → ApprovalClass（C10）。
        mcp_lookup: MCP ``tool.annotations`` → ApprovalClass（C10）。
        plugin_lookup: plugin.json ``tool_classes`` → ApprovalClass（C10）。
        yaml_path: 显式 YAML 路径覆盖（默认走 ``_resolve_yaml_path``）。
    """
    global _engine, _config, _explicit_lookup, _skill_lookup, _mcp_lookup, _plugin_lookup
    with _lock:
        # Snapshot the old audit config so we can detect whether the
        # rebuild changed any audit-related field. If it did, we need
        # to reset the audit_logger singleton — otherwise hot-reload /
        # ENV ``OPENAKITA_AUDIT_LOG_PATH`` would change ``_config`` but
        # ``get_audit_logger()`` would keep writing to the old path
        # (C18 二轮 audit BUG-C1).
        old_audit_cfg: tuple[str, bool, bool] | None = None
        if _config is not None:
            old_audit_cfg = (
                _config.audit.log_path,
                _config.audit.enabled,
                _config.audit.include_chain,
            )

        path = Path(yaml_path) if yaml_path is not None else _resolve_yaml_path()
        try:
            cfg, report = load_policies_yaml(path, strict=True)
            _audit_env_overrides(report, cfg)
            if report.unknown_security_keys:
                logger.warning(
                    "[PolicyV2] POLICIES.yaml rebuild: unknown keys under "
                    "'security' (typo or attack?): %s",
                    ", ".join(report.unknown_security_keys),
                )
            _set_last_known_good(cfg)
        except Exception as exc:
            cfg = _recover_from_load_failure(
                exc, source=str(path) if path else "<no path>"
            )
        if explicit_lookup is not None:
            _explicit_lookup = explicit_lookup
        if skill_lookup is not None:
            _skill_lookup = skill_lookup
        if mcp_lookup is not None:
            _mcp_lookup = mcp_lookup
        if plugin_lookup is not None:
            _plugin_lookup = plugin_lookup
        _engine = build_engine_from_config(
            cfg,
            explicit_lookup=_explicit_lookup,
            skill_lookup=_skill_lookup,
            mcp_lookup=_mcp_lookup,
            plugin_lookup=_plugin_lookup,
        )
        _config = cfg

        # Invalidate the audit_logger singleton if any audit field
        # changed (path / enabled / include_chain). The reset is cheap
        # — next ``get_audit_logger()`` lazily rebuilds from the fresh
        # config (and any in-flight callers holding a reference to the
        # old logger keep writing to the old path until they finish;
        # that's fine — the row in flight just lands in the previous
        # file).
        new_audit_cfg = (
            cfg.audit.log_path,
            cfg.audit.enabled,
            cfg.audit.include_chain,
        )
        if old_audit_cfg is None or old_audit_cfg != new_audit_cfg:
            try:
                from ..audit_logger import reset_audit_logger

                reset_audit_logger()
            except Exception:
                logger.exception(
                    "[PolicyV2] failed to reset audit_logger singleton "
                    "after rebuild — audit may still write to previous path"
                )
    return _engine


def is_initialized() -> bool:
    """单元测试用——判断单例是否已初始化（不触发懒加载）。"""
    return _engine is not None


def invalidate_classifier_cache(tool: str | None = None) -> None:
    """C10：通知 ApprovalClassifier 清掉 ``tool``（或全部）的缓存。

    背景：``ApprovalClassifier`` 用 LRU 缓存 base classification（5 步链）的
    结果。运行时 plugin / MCP server / skill 注册或卸载时，4 类 lookup 的
    返回值会变，但缓存里的旧条目仍然有效——下次同名工具被分类时拿到的是
    陈旧结果（典型现场：reload plugin，新 manifest 把 tool 从
    ``readonly_scoped`` 改 ``destructive``，但缓存还指向 readonly_scoped）。

    本 helper 是这种动态变更场景的"广播失效"入口。设计要点：

    - 引擎未初始化（典型测试 / 启动前）：no-op，不强制构造单例
    - ``tool=None``：清空整个 LRU 缓存（最保守，所有 mutator 默认走这里）
    - ``tool=<name>``：精准清除单个条目（registry 层若知道具体 tool 名可用）
    - 任何异常静默吞掉（注册路径不能被 audit 子系统拖垮）
    """
    global _engine
    if _engine is None:
        return
    classifier = getattr(_engine, "_classifier", None)
    if classifier is None or not hasattr(classifier, "invalidate"):
        return
    try:
        classifier.invalidate(tool)
    except Exception as exc:
        logger.debug(
            "[PolicyV2] invalidate_classifier_cache(%r) failed: %s", tool, exc
        )


def reset_policy_v2_layer(scope: str = "all") -> None:
    """C8b-2: 一次性重置 v2 引擎单例 + 关联子系统（audit_logger）。

    背景：``api/routes/config.py`` UI Save Settings 后需要让全部 v2 配置消费者
    重读 YAML。v2 全局引擎自身由 ``reset_engine_v2()`` 重置；audit_logger 持
    有的 path/enabled 字段是从 ``PolicyConfigV2.audit`` 派生的（C8b-2 起改读
    v2，详见 ``audit_logger.get_audit_logger``），同样需要重置。

    Pre-C8b-2：config.py 直接调 v1 ``reset_policy_engine()``，后者内部级联调
    v2 reset + audit reset。C8b-2 之后 config.py 改调本函数，把"reset 谁"的
    决策从 v1 移到 v2 层，让 C8b-5 删 v1 时不需要重新串联。

    fail-safe：audit_logger 模块未导入时静默跳过（特殊 import 路径下可能尚
    未初始化）。

    C9c-3: emit ``policy_config_reloaded`` SSE on success / ``_failed`` on
    exception so the UI can refresh dependent views (SecurityView,
    PendingApprovalsView, dry-run preview cache). ``scope`` is a free-form
    label from the caller (e.g. "security", "zones", "commands",
    "user_allowlist") that the UI uses to decide which subviews to refresh.
    """
    error: Exception | None = None
    try:
        reset_engine_v2()
        # C16 Phase B：scope="all"（默认）也清掉 LKG——操作员"reset to defaults"
        # 的语义就是想从零开始，不能让上一次成功加载的 cache 把意图盖掉。
        # 测试 fixture 调用本函数后也能拿到干净状态。
        _clear_last_known_good()
        try:
            from ..audit_logger import reset_audit_logger

            reset_audit_logger()
        except Exception:
            logger.debug(
                "[PolicyV2] audit_logger reset skipped (module not available)",
                exc_info=True,
            )
    except Exception as exc:  # noqa: BLE001
        error = exc
        logger.error(
            "[PolicyV2] reset_policy_v2_layer(scope=%s) failed: %s",
            scope,
            exc,
            exc_info=True,
        )

    _emit_reload_event(scope=scope, error=error)
    if error is not None:
        # Re-raise so callers can decide whether to surface to the user.
        raise error


def _emit_reload_event(*, scope: str, error: Exception | None) -> None:
    """Best-effort SSE emit for policy_config_reloaded[_failed].

    Never blocks, never raises. Routes through ``fire_event`` so the
    no-loop / cross-loop / failure cases are all handled in one place
    (see ``api/routes/websocket.py``).
    """
    import time as _time

    try:
        from ...api.routes.websocket import fire_event
    except Exception as exc:  # noqa: BLE001
        logger.debug("[PolicyV2] reload SSE skipped (no WS): %s", exc)
        return

    event = "policy_config_reload_failed" if error else "policy_config_reloaded"
    payload: dict[str, Any] = {
        "scope": scope,
        "ts": _time.time(),
    }
    if error is not None:
        payload["error"] = f"{type(error).__name__}: {error}"

    fire_event(event, payload)


def make_preview_engine(
    cfg: PolicyConfigV2 | None = None,
) -> PolicyEngineV2:
    """为 dry-run preview / 单次评估场景创建一次性引擎（C8b-1）。

    特点：
    - **不污染全局 death_switch tracker**：``count_in_death_switch = False``，
      预览的 DENY sample 不会让真实用户进 readonly mode
    - **复用 explicit_lookup**：与 global engine 用同一份 handler→class 映射，
      避免预览结果与生产决策出现"分类器漂移"
    - **隔离 user_allowlist**：preview 引擎拿自己的 ``UserAllowlistManager``
      （持有 cfg 的 user_allowlist 子段），不会改到全局配置

    Args:
        cfg: 预览用的配置；不传时复制当前 global config（不持有引用，避免
            preview 调用方意外 mutate global state）。

    Note:
        与 ``get_engine_v2()`` / ``rebuild_engine_v2()`` 不同，本函数不写
        模块级 ``_engine``——返回的引擎只对调用方可见，gc 后即销毁。
    """
    from copy import deepcopy

    effective_cfg = cfg if cfg is not None else deepcopy(get_config_v2())
    engine = build_engine_from_config(effective_cfg, explicit_lookup=_explicit_lookup)
    engine.count_in_death_switch = False
    return engine


__all__ = [
    "ExplicitLookup",
    "get_config_v2",
    "get_engine_v2",
    "is_initialized",
    "make_preview_engine",
    "rebuild_engine_v2",
    "reset_engine_v2",
    "reset_policy_v2_layer",
    "set_engine_v2",
]

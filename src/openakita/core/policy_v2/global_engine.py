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
from typing import TYPE_CHECKING

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


def _resolve_yaml_path() -> Path | None:
    """识别 POLICIES.yaml 路径。返回 None 时调用方应让 loader 用默认配置。"""
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
    try:
        cfg, report = load_policies_yaml(yaml_path, strict=False)
        if report.fields_migrated:
            logger.info(
                "[PolicyV2] global engine loaded with %d v1 fields migrated",
                len(report.fields_migrated),
            )
    except PolicyConfigError as exc:
        # strict=False 时 loader 不会抛 PolicyConfigError，但理论上仍可能
        # 触发（migration pipeline 内部异常）。降级到默认配置 + ERROR log。
        logger.error(
            "[PolicyV2] config load failed (%s); falling back to defaults",
            exc,
        )
        cfg = PolicyConfigV2()
    except Exception as exc:
        logger.error(
            "[PolicyV2] unexpected error loading config: %s; using defaults",
            exc,
        )
        cfg = PolicyConfigV2()

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
        path = Path(yaml_path) if yaml_path is not None else _resolve_yaml_path()
        try:
            cfg, _ = load_policies_yaml(path, strict=False)
        except Exception as exc:
            logger.error(
                "[PolicyV2] rebuild failed (%s); keeping previous config",
                exc,
            )
            cfg = _config or PolicyConfigV2()
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


def reset_policy_v2_layer() -> None:
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
    """
    reset_engine_v2()
    try:
        from ..audit_logger import reset_audit_logger

        reset_audit_logger()
    except Exception:
        logger.debug(
            "[PolicyV2] audit_logger reset skipped (module not available)",
            exc_info=True,
        )


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

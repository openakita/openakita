"""C19-D1: ApprovalClass 完备性 CI 闸门.

设计理由
========

OpenAkita 的内置工具持续增加 (当前 38 handler / ~125+ tool). 每个新工具
若不显式声明 ``ApprovalClass``, 就会落到启发式或 UNKNOWN 兜底, 导致:

- 用户开 ``trust`` 模式后新工具仍每次 ask (启发式归 UNKNOWN)
- 真正危险的新工具 (如 ``flush_database``) 被误归 ``MUTATING_SCOPED``
  而非 ``DESTRUCTIVE``, 静默放行

C19 的 4 层护栏中, **本测试是最硬的一层** (CI 拦截, 无法绕过).

实现策略
========

混合 AST + runtime 双重 gate, 避免单一手段的盲点:

1. **AST 层**: 静态扫所有 ``src/openakita/tools/handlers/*.py``, 找
   ``TOOLS = [...]`` (或 module-level ``DESKTOP_TOOLS``) 与
   ``TOOL_CLASSES = {...}`` 声明, 断言:
   - 每个 ``TOOLS`` 列表条目在 ``TOOL_CLASSES`` 字典里有 key
   - ``TOOL_CLASSES`` key 集合 ⊆ ``TOOLS`` (无 typo / stale)

   AST 路径快, 不需要 boot agent, 适合本地 dev 反馈环.

2. **Runtime 层**: 通过 ``SystemHandlerRegistry`` 实例化测试注册流程,
   断言 ``get_tool_class(tool)`` 对每个 registered tool 都返回 non-None
   (即 ``_collect_tool_classes`` 的输出至少有一条命中).

   Runtime 路径捕获 "类有 TOOL_CLASSES 但 register() 没正确调入
   _collect_tool_classes" 这类布线 bug.

为什么不直接调 ``classify_with_source``
=======================================

第一版方案 (cookbook §12.5.2.1) 用 classifier.classify_with_source 检查
每个工具是否走了 ``HEURISTIC_PREFIX`` / ``FALLBACK_UNKNOWN``. 问题:

- 启发式回退是 **base classification 的兜底**, 工具走显式来源就不会
  到这里; 但用 ``classify_with_source`` 探测必须要先注入 ``explicit_lookup``
  (= registry.get_tool_class), 才能区分 "没注入" 和 "注入了但没声明".
- 测试依赖完整 agent boot, 太重.

我们改为 **直接验证 registry 字典**: 既消除 boot 成本, 又精确捕获
"声明缺失" 这个根本原因.

调用方式
========

::

    pytest tests/unit/test_classifier_completeness.py
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HANDLERS_DIR = REPO_ROOT / "src" / "openakita" / "tools" / "handlers"

DOC_REF = "docs/policy_v2_research.md §4.21"

# Handlers that legitimately have no TOOLS list (helper modules, not exposed).
# Each entry must include a one-line reason so the exception is auditable.
_NON_TOOL_HANDLER_FILES: dict[str, str] = {
    "__init__.py": "registry plumbing, not a handler",
    "todo_state.py": "internal state helper for plan/todo handler",
    "todo_store.py": "persistence helper, no TOOLS",
    "todo_heuristics.py": "heuristic helper, no TOOLS",
    "plan.py": "back-compat re-export shim of todo_handler.py",
}


def _iter_handler_files() -> list[Path]:
    """All handler module files we expect to declare TOOLS + TOOL_CLASSES."""
    out: list[Path] = []
    for p in sorted(HANDLERS_DIR.glob("*.py")):
        if p.name in _NON_TOOL_HANDLER_FILES:
            continue
        out.append(p)
    return out


def _extract_tools_and_classes(
    src: str,
) -> tuple[list[str], dict[str, str]] | None:
    """Parse a handler file; return (TOOLS list, TOOL_CLASSES key→ApprovalClass.value).

    Returns None when the file declares neither (e.g. truly empty handler).

    Looks at:
    - ``TOOLS = [...]`` (class attribute, list of string literals)
    - ``TOOL_CLASSES = {...}`` (class or module attribute, dict of literal keys)
    - ``DESKTOP_TOOLS = [...]`` module-level constant assigned to class
      (special-case for ``desktop.py``; classifier reads class.TOOLS regardless)
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None

    tools: list[str] = []
    classes: dict[str, str] = {}

    def _grab_list_of_str(node: ast.AST) -> list[str] | None:
        if not isinstance(node, ast.List):
            return None
        out: list[str] = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.append(elt.value)
        return out if out else None

    def _grab_dict_of_str(node: ast.AST) -> dict[str, str] | None:
        if not isinstance(node, ast.Dict):
            return None
        out: dict[str, str] = {}
        for k, v in zip(node.keys, node.values, strict=False):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                continue
            # Value is typically ApprovalClass.<NAME> or string constant
            value_repr = ""
            if isinstance(v, ast.Attribute):
                value_repr = (
                    f"{ast.unparse(v.value) if hasattr(ast, 'unparse') else '<obj>'}."
                    f"{v.attr}"
                )
            elif isinstance(v, ast.Constant) and isinstance(v.value, str):
                value_repr = v.value
            out[k.value] = value_repr
        return out if out else None

    # Walk module-level + class-level assignments
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if not isinstance(tgt, ast.Name):
                    continue
                name = tgt.id
                if name in ("TOOLS", "DESKTOP_TOOLS"):
                    got = _grab_list_of_str(node.value)
                    if got:
                        tools = got
                elif name == "TOOL_CLASSES":
                    got = _grab_dict_of_str(node.value)
                    if got:
                        classes = got

    if not tools and not classes:
        return None
    return tools, classes


# ============================================================================
# AST layer
# ============================================================================


@pytest.mark.parametrize("handler_file", _iter_handler_files(), ids=lambda p: p.name)
def test_handler_declares_tool_classes_for_every_tool(handler_file: Path):
    """每个 handler 的 TOOLS 列表里每个工具都必须在 TOOL_CLASSES 里有 key.

    若失败, 错误信息直接指向 cookbook 路径, 让开发者按方案 B/C 补全.
    """
    src = handler_file.read_text(encoding="utf-8")
    parsed = _extract_tools_and_classes(src)
    if parsed is None:
        pytest.fail(
            f"{handler_file.name}: 既无 TOOLS 也无 TOOL_CLASSES — "
            f"如果是非工具 handler, 请加入 _NON_TOOL_HANDLER_FILES 白名单"
        )
        return  # for type checker
    tools, classes = parsed
    if not tools:
        pytest.fail(
            f"{handler_file.name}: 有 TOOL_CLASSES 但无 TOOLS — "
            f"声明可能漂移; 请按 {DOC_REF} 检查"
        )
        return
    missing = [t for t in tools if t not in classes]
    extras = [k for k in classes if k not in tools]
    msg_parts = []
    if missing:
        msg_parts.append(
            f"以下 TOOLS 条目缺 ApprovalClass 显式声明: {missing}\n"
            f"按 {DOC_REF} 选择方案 B (agent.py register tool_classes=) "
            f"或方案 C (handler 类内 TOOL_CLASSES = {{...}}) 补全."
        )
    if extras:
        msg_parts.append(
            f"以下 TOOL_CLASSES key 不在 TOOLS 列表 (可能 typo / stale): {extras}\n"
            f"_collect_tool_classes 会在运行时 WARN+drop, 但建议源头修正."
        )
    assert not msg_parts, "\n\n".join(msg_parts)


# ============================================================================
# Runtime layer
# ============================================================================


def test_registry_get_tool_class_returns_nonnull_for_every_registered_tool():
    """SystemHandlerRegistry 注册完所有 handler 后, get_tool_class 不能为 None.

    本层捕获 "handler 类有 TOOL_CLASSES 但 register() 流程未调用
    _collect_tool_classes" 这类布线 bug. 相当于 AST 层的 cross-check —
    AST 看声明, runtime 看真到 registry.
    """
    from openakita.tools.handlers import SystemHandlerRegistry

    registry = SystemHandlerRegistry()

    # Build minimal stub agent for handler instantiation. Each handler ctor
    # does at minimum logger / settings access; we provide the absolute
    # minimum for handlers to load.
    class _StubAgent:
        def __init__(self):
            from openakita.config import settings

            self.config = settings

    stub = _StubAgent()

    # Register each handler — same call site as agent.py:_init_handlers but
    # without boot side effects (no LLM, no MCP discovery, etc.).
    #
    # All handler modules expose ``create_handler`` (sometimes additionally
    # under a domain-specific alias like ``create_todo_handler``). We default
    # to ``create_handler`` and override per-module only when needed.
    handler_specs: list[tuple[str, str]] = [
        ("filesystem", "create_handler"),
        ("memory", "create_handler"),
        ("scheduled", "create_handler"),
        ("profile", "create_handler"),
        ("plan", "create_todo_handler"),
        ("system", "create_handler"),
        ("im_channel", "create_handler"),
        ("skills", "create_handler"),
        ("web_search", "create_handler"),
        ("web_fetch", "create_handler"),
        ("code_quality", "create_handler"),
        ("search", "create_handler"),
        ("mode", "create_handler"),
        ("notebook", "create_handler"),
        ("persona", "create_handler"),
        ("sticker", "create_handler"),
        ("config", "create_handler"),
        ("plugins", "create_handler"),
        ("agent_package", "create_handler"),
        ("lsp", "create_handler"),
        ("sleep", "create_handler"),
        ("structured_output", "create_handler"),
        ("tool_search", "create_handler"),
        ("worktree", "create_handler"),
        ("agent_hub", "create_handler"),
        ("skill_store", "create_handler"),
        ("powershell", "create_handler"),
        ("desktop", "create_handler"),
        ("opencli", "create_handler"),
        ("cli_anything", "create_handler"),
        ("agent", "create_handler"),
        ("org_setup", "create_handler"),
        ("browser", "create_handler"),
        ("mcp", "create_handler"),
    ]

    registered_tools: list[tuple[str, str]] = []  # (handler, tool)
    skipped: list[tuple[str, str]] = []  # (handler, reason)
    for handler_name, factory_name in handler_specs:
        try:
            module = __import__(
                f"openakita.tools.handlers.{handler_name}",
                fromlist=[factory_name],
            )
            factory = getattr(module, factory_name, None)
            if factory is None:
                skipped.append((handler_name, f"no {factory_name}"))
                continue
            handler = factory(stub)
            registry.register(handler_name, handler)
            owner = getattr(handler, "__self__", None)
            tools = getattr(owner, "TOOLS", None) or []
            for t in tools:
                registered_tools.append((handler_name, t))
        except Exception as exc:  # noqa: BLE001
            # Some handlers (e.g. desktop, opencli, cli_anything) need
            # external runtime; skip with a recorded reason. AST layer
            # still validates their declarations, so coverage isn't lost.
            skipped.append((handler_name, f"{type(exc).__name__}: {exc}"))

    # Assert: every registered tool has a TOOL_CLASSES entry in the registry.
    unclassified: list[tuple[str, str]] = []
    for handler_name, tool in registered_tools:
        if registry.get_tool_class(tool) is None:
            unclassified.append((handler_name, tool))

    assert not unclassified, (
        f"{len(unclassified)} registered tools missing ApprovalClass in registry; "
        f"this means handler.TOOL_CLASSES exists but _collect_tool_classes "
        f"failed to absorb it (possible registry plumbing bug). "
        f"Sample: {unclassified[:5]}\n\n"
        f"See {DOC_REF}."
    )

    # Sanity: at least the core handlers contributed tools (avoid silent
    # "loaded 0 handlers, vacuously passed"). 50 is a conservative floor
    # given filesystem alone declares 9 tools and ~20 handlers should load.
    assert len(registered_tools) >= 50, (
        f"Expected >= 50 registered tools "
        f"(filesystem + memory + system + skills + ...); "
        f"got only {len(registered_tools)}. Registration loop may be broken.\n"
        f"Skipped handlers: {skipped}"
    )


# ============================================================================
# D2 negative case: register() WARN actually fires
# ============================================================================


def test_register_warns_when_tool_lacks_explicit_approval_class(caplog):
    """C19-D2: 注册一个没声明 ApprovalClass 的工具时, register() 必须 WARN.

    护栏的"运行时反馈环": 开发者看到本地启动 WARN 后会按 cookbook 修, 比 CI
    红灯更早一步. 没这个测试, D2 的 WARN 逻辑可能在重构中被悄悄删掉.
    """
    import logging

    from openakita.tools.handlers import SystemHandlerRegistry

    class _BareHandler:
        TOOLS = ["my_undeclared_tool", "another_undeclared"]

        async def __call__(self, tool: str, params: dict) -> str:
            return ""

    registry = SystemHandlerRegistry()
    bare = _BareHandler()

    with caplog.at_level(
        logging.WARNING, logger="openakita.tools.handlers"
    ):
        registry.register("bare_for_test", bare.__call__)

    policy_warns = [
        r.getMessage()
        for r in caplog.records
        if "[Policy]" in r.getMessage()
    ]
    assert len(policy_warns) == 2, (
        f"Expected 2 [Policy] WARNs (one per undeclared tool), "
        f"got {len(policy_warns)}: {policy_warns}"
    )
    joined = "\n".join(policy_warns)
    assert "my_undeclared_tool" in joined
    assert "another_undeclared" in joined
    assert "§4.21" in joined, (
        "WARN message must reference the cookbook path so devs find the fix"
    )


def test_register_does_not_warn_when_all_tools_have_classes():
    """对照组: 所有工具都显式声明时, register() 一句 WARN 都不能出.

    防御 "WARN 被改成 always-on 噪音" 的回归.
    """
    import logging

    from openakita.core.policy_v2 import ApprovalClass
    from openakita.tools.handlers import SystemHandlerRegistry

    class _GoodHandler:
        TOOLS = ["good_tool"]
        TOOL_CLASSES = {"good_tool": ApprovalClass.READONLY_GLOBAL}

        async def __call__(self, tool: str, params: dict) -> str:
            return ""

    registry = SystemHandlerRegistry()
    good = _GoodHandler()

    records: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger_obj = logging.getLogger("openakita.tools.handlers")
    sink = _Sink(level=logging.WARNING)
    logger_obj.addHandler(sink)
    try:
        registry.register("good_for_test", good.__call__)
    finally:
        logger_obj.removeHandler(sink)

    policy_warns = [r for r in records if "[Policy]" in r.getMessage()]
    assert not policy_warns, (
        f"Did not expect [Policy] WARN for fully classified handler; "
        f"got {[r.getMessage() for r in policy_warns]}"
    )

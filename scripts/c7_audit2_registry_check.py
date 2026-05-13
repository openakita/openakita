"""C7 二轮 audit：验证 SystemHandlerRegistry 真的吃下了所有 handler.TOOL_CLASSES。

跳过 LLM / 网络 / 浏览器 init，直接调 _init_handlers，然后读 _tool_classes
看每个 TOOL 是否都在显式表里、source 是否 EXPLICIT_HANDLER_ATTR。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# 关掉副作用：禁 LLM、不调外部
os.environ.setdefault("OPENAKITA_DISABLE_BACKGROUND_TASKS", "1")
os.environ.setdefault("OPENAKITA_TEST_MODE", "1")


def main() -> int:
    from openakita.tools.handlers import SystemHandlerRegistry

    # 不能直接创建 Agent（会启动 brain 等重量级组件）。
    # 只走 handler factory + register 来模拟 _init_handlers 的关键路径。
    registry = SystemHandlerRegistry()

    failures: list[str] = []
    # 用一个 minimal stub agent —— 大部分 handler 的 __init__ 只存 self.agent
    class _StubAgent:
        skill_manager = None
        skill_registry = None

    stub = _StubAgent()

    handler_specs = [
        ("filesystem", "openakita.tools.handlers.filesystem", "create_handler"),
        ("memory", "openakita.tools.handlers.memory", "create_handler"),
        ("browser", "openakita.tools.handlers.browser", "create_handler"),
        ("scheduled", "openakita.tools.handlers.scheduled", "create_handler"),
        ("mcp", "openakita.tools.handlers.mcp", "create_handler"),
        ("profile", "openakita.tools.handlers.profile", "create_handler"),
        ("plan", "openakita.tools.handlers.todo_handler", "create_todo_handler"),
        ("system", "openakita.tools.handlers.system", "create_handler"),
        ("im_channel", "openakita.tools.handlers.im_channel", "create_handler"),
        ("skills", "openakita.tools.handlers.skills", "create_handler"),
        ("web_search", "openakita.tools.handlers.web_search", "create_handler"),
        ("web_fetch", "openakita.tools.handlers.web_fetch", "create_handler"),
        ("code_quality", "openakita.tools.handlers.code_quality", "create_handler"),
        ("search", "openakita.tools.handlers.search", "create_handler"),
        ("mode", "openakita.tools.handlers.mode", "create_handler"),
        ("notebook", "openakita.tools.handlers.notebook", "create_handler"),
        ("persona", "openakita.tools.handlers.persona", "create_handler"),
        ("sticker", "openakita.tools.handlers.sticker", "create_handler"),
        ("config", "openakita.tools.handlers.config", "create_handler"),
        ("plugins", "openakita.tools.handlers.plugins", "create_handler"),
        ("agent_package", "openakita.tools.handlers.agent_package", "create_handler"),
        ("lsp", "openakita.tools.handlers.lsp", "create_handler"),
        ("sleep", "openakita.tools.handlers.sleep", "create_handler"),
        ("structured_output", "openakita.tools.handlers.structured_output", "create_handler"),
        ("tool_search", "openakita.tools.handlers.tool_search", "create_handler"),
        ("worktree", "openakita.tools.handlers.worktree", "create_handler"),
        ("agent_hub", "openakita.tools.handlers.agent_hub", "create_handler"),
        ("skill_store", "openakita.tools.handlers.skill_store", "create_handler"),
        ("powershell", "openakita.tools.handlers.powershell", "create_handler"),
        ("desktop", "openakita.tools.handlers.desktop", "create_handler"),
        ("opencli", "openakita.tools.handlers.opencli", "create_handler"),
        ("cli_anything", "openakita.tools.handlers.cli_anything", "create_handler"),
        ("agent", "openakita.tools.handlers.agent", "create_handler"),
        ("org_setup", "openakita.tools.handlers.org_setup", "create_handler"),
    ]

    import importlib

    registered_handlers = 0
    for name, module_path, factory_name in handler_specs:
        try:
            mod = importlib.import_module(module_path)
            factory = getattr(mod, factory_name)
            handler_callable = factory(stub)
            registry.register(name, handler_callable)
            registered_handlers += 1
        except Exception as exc:
            failures.append(f"register {name}: {type(exc).__name__}: {exc}")

    print(f"Registered {registered_handlers}/{len(handler_specs)} handlers")
    print(f"Total tools in registry: {len(registry._tool_to_handler)}")
    print(f"Total tools with explicit ApprovalClass: {len(registry._tool_classes)}")

    # Detail：哪些 tool 没有显式 class
    tools_without_class = [
        t for t in registry._tool_to_handler if t not in registry._tool_classes
    ]
    if tools_without_class:
        failures.append(
            f"{len(tools_without_class)} tools have no explicit ApprovalClass: "
            f"{tools_without_class}"
        )

    # 验证 source 都是 EXPLICIT_HANDLER_ATTR
    from openakita.core.policy_v2 import DecisionSource

    non_explicit_sources = [
        (tool, source.value)
        for tool, (_, source) in registry._tool_classes.items()
        if not DecisionSource.is_explicit(source)
    ]
    if non_explicit_sources:
        failures.append(f"non-explicit sources: {non_explicit_sources}")

    # 验证 explicit_lookup 注入到 v2 engine 后 classify 能命中
    from openakita.core.policy_v2.global_engine import (
        rebuild_engine_v2,
        reset_engine_v2,
    )

    engine = rebuild_engine_v2(explicit_lookup=registry.get_tool_class)

    sample_checks = [
        ("write_file", "mutating_scoped"),
        ("delete_file", "destructive"),
        ("read_file", "readonly_scoped"),
        ("ask_user", "interactive"),
        ("run_powershell", "exec_capable"),
        ("delegate_to_agent", "control_plane"),
        ("memory_delete_by_query", "destructive"),
        ("setup_organization", "control_plane"),
        ("web_fetch", "network_out"),
        ("send_sticker", "interactive"),
    ]
    for tool, expected in sample_checks:
        ac, src = engine._classifier.classify_with_source(tool)
        if ac.value != expected:
            failures.append(
                f"classify({tool!r}) = {ac.value!r}, expected {expected!r}"
            )
        elif not DecisionSource.is_explicit(src):
            failures.append(
                f"classify({tool!r}) source = {src.value!r}, expected explicit"
            )
        else:
            print(f"[OK] {tool}: {ac.value} via {src.value}")

    reset_engine_v2()

    if failures:
        print(f"\n[FAIL] {len(failures)} issue(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n[PASS] all tools have explicit ApprovalClass; explicit_lookup works")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

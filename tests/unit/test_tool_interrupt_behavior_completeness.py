"""Completeness test for ``core/tool_interrupt_behavior.py`` (S4, plan: v1.28).

Walks ``src/openakita/tools/definitions/*.py`` plus the desktop handler's
``DESKTOP_TOOLS`` list and asserts every shipped tool has an explicit
entry in ``_INTERRUPT_BEHAVIOR_MAP``.  Catches the drift class where
someone adds a new built-in tool but forgets to classify it — the
runtime default ("block") is safe but silently degrades INTERRUPT
performance, so we want the test suite to flag it instead.

Third-party MCP tools and dynamic registrations are NOT scanned here —
they go through ``mcp_annotations`` resolution at call time, plus the
``warn_unclassified_tools`` startup warning surfaces any unknowns to
ops at runtime.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from openakita.core.tool_interrupt_behavior import (
    DEFAULT_BEHAVIOR,
    InterruptBehavior,
    get_tool_interrupt_behavior,
    has_any_block_tool,
    is_unknown_tool,
    known_tools,
    partition_by_behavior,
    warn_unclassified_tools,
)

# ── Discovery: parse definitions/*.py for "name": "<tool>" entries ────


def _project_root() -> Path:
    here = Path(__file__).resolve()
    # tests/unit/test_*.py → project root is 3 parents up.
    return here.parent.parent.parent


def _definitions_dir() -> Path:
    return _project_root() / "src" / "openakita" / "tools" / "definitions"


def _desktop_handler_path() -> Path:
    return _project_root() / "src" / "openakita" / "tools" / "handlers" / "desktop.py"


def _extract_tool_names_from_file(path: Path) -> set[str]:
    """Parse a ``definitions/*.py`` file via AST, return tool names.

    A "tool definition" is identified by a dict literal containing BOTH
    a string ``"name"`` and a dict ``"input_schema"`` (the ToolDefinition
    contract).  This filters out related_tools entries / config snippets /
    docstring examples that just happen to use ``"name":`` keys.
    """
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()

    names: set[str] = set()

    class _Visitor(ast.NodeVisitor):
        def visit_Dict(self, node: ast.Dict) -> None:
            tool_name: str | None = None
            has_input_schema = False
            for key, value in zip(node.keys, node.values, strict=False):
                if not isinstance(key, ast.Constant):
                    continue
                if (
                    key.value == "name"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    tool_name = value.value
                elif key.value == "input_schema":
                    has_input_schema = True
            if tool_name is not None and has_input_schema:
                names.add(tool_name)
            self.generic_visit(node)

    _Visitor().visit(tree)
    return names


def _extract_desktop_tools() -> set[str]:
    """Pull ``DESKTOP_TOOLS = [...]`` from the desktop handler file."""
    path = _desktop_handler_path()
    if not path.exists():
        return set()
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DESKTOP_TOOLS":
                    if isinstance(node.value, ast.List):
                        return {
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        }
    return set()


def _discover_all_built_in_tools() -> set[str]:
    """Aggregate tool names from definitions/*.py + desktop handler."""
    found: set[str] = set()
    defs_dir = _definitions_dir()
    if defs_dir.exists():
        for py in defs_dir.glob("*.py"):
            if py.name == "__init__.py" or py.name == "base.py":
                continue
            found.update(_extract_tool_names_from_file(py))
    found.update(_extract_desktop_tools())
    return found


# ── Tests ─────────────────────────────────────────────────────────────


class TestRegistryCompleteness:
    """Every shipped built-in tool must have an explicit interrupt_behavior."""

    def test_definitions_dir_exists(self) -> None:
        assert _definitions_dir().exists(), (
            f"expected definitions dir at {_definitions_dir()} — test setup broken"
        )

    def test_discovered_tools_nonempty(self) -> None:
        discovered = _discover_all_built_in_tools()
        assert len(discovered) >= 50, (
            f"only discovered {len(discovered)} tools — extractor likely broken "
            f"(sample: {sorted(discovered)[:10]})"
        )

    def test_every_built_in_tool_has_explicit_behavior(self) -> None:
        """The core invariant: a contributor adds a tool to definitions/*.py
        → the test fails until they classify it in
        ``_INTERRUPT_BEHAVIOR_MAP``."""
        discovered = _discover_all_built_in_tools()
        unknown = {n for n in discovered if is_unknown_tool(n)}
        if unknown:
            # Sort for stable error output across runs.
            listing = "\n  - ".join(sorted(unknown))
            pytest.fail(
                f"\n{len(unknown)} built-in tool(s) missing from "
                f"core/tool_interrupt_behavior.py:\n  - {listing}\n\n"
                "Add an entry to _INTERRUPT_BEHAVIOR_MAP. Use 'cancel' "
                "only for pure reads / idempotent queries with no external "
                "side effects.  Anything that writes a file, runs a "
                "subprocess, mutates the browser, sends an IM message, "
                "or persists DB state should be 'block'.\n"
            )

    def test_registry_has_no_orphan_entries(self) -> None:
        """Entries in the registry should correspond to a built-in tool or
        a legacy alias.  An orphan entry is dead weight — we want to know."""
        discovered = _discover_all_built_in_tools()
        # Known aliases that don't appear in definitions/*.py but are
        # still legitimate (e.g., handler-internal names, deprecated
        # tools kept for backward compat).  Update sparingly.
        aliases = {
            "send_sticker",  # defined in sticker.py; verify discovered finds it
        }
        orphans = known_tools() - discovered - aliases
        if orphans:
            # Don't hard-fail — orphans are not unsafe, just stale.  But
            # surface them so the registry doesn't grow unboundedly.
            # Switch to assert if/when we want stricter policy.
            print(
                f"\n[INFO] {len(orphans)} registry entries lack a matching "
                f"discovered tool (may be aliases / new tools / deprecated): "
                f"{sorted(orphans)}"
            )


class TestBehaviorContracts:
    """Invariants about the classification itself."""

    def test_default_is_block(self) -> None:
        assert DEFAULT_BEHAVIOR == "block"

    def test_unknown_tool_resolves_to_default(self) -> None:
        assert get_tool_interrupt_behavior("definitely_not_a_real_tool_xyz") == "block"

    def test_known_read_only_tools_are_cancel(self) -> None:
        """Spot-check: anything read-only must be 'cancel' to keep
        INTERRUPT useful in practice."""
        for n in (
            "read_file",
            "list_directory",
            "grep",
            "glob",
            "search_memory",
            "browser_screenshot",
            "web_search",
        ):
            assert get_tool_interrupt_behavior(n) == "cancel", (
                f"{n} must be cancel-safe (read-only)"
            )

    def test_known_write_tools_are_block(self) -> None:
        for n in (
            "write_file",
            "edit_file",
            "delete_file",
            "run_shell",
            "run_powershell",
            "browser_click",
            "browser_navigate",
            "desktop_click",
            "add_memory",
            "call_mcp_tool",
        ):
            assert get_tool_interrupt_behavior(n) == "block", (
                f"{n} must be block (writes / external side effects)"
            )

    def test_every_value_is_valid_literal(self) -> None:
        """No typos in the registry values."""
        for n in known_tools():
            v = get_tool_interrupt_behavior(n)
            assert v in ("cancel", "block"), f"{n} -> {v!r}; must be 'cancel' or 'block'"


class TestMcpAnnotationOverride:
    """MCP annotations only override for unknown tools — they cannot
    weaken a built-in 'block' to 'cancel'."""

    def test_annotation_resolves_unknown_to_cancel(self) -> None:
        assert (
            get_tool_interrupt_behavior(
                "unknown_mcp_tool",
                mcp_annotations={"interruptBehavior": "cancel"},
            )
            == "cancel"
        )

    def test_annotation_cannot_override_builtin_block(self) -> None:
        assert (
            get_tool_interrupt_behavior(
                "write_file",
                mcp_annotations={"interruptBehavior": "cancel"},
            )
            == "block"
        )

    def test_annotation_cannot_override_builtin_cancel(self) -> None:
        """Symmetric: an MCP server can't *upgrade* read_file to 'block'
        either — the built-in classification is authoritative for
        shipped tools."""
        assert (
            get_tool_interrupt_behavior(
                "read_file",
                mcp_annotations={"interruptBehavior": "block"},
            )
            == "cancel"
        )

    def test_invalid_annotation_falls_through_to_default(self) -> None:
        assert (
            get_tool_interrupt_behavior(
                "unknown_x", mcp_annotations={"interruptBehavior": "garbage"}
            )
            == "block"
        )

    def test_none_annotation_falls_through_to_default(self) -> None:
        assert get_tool_interrupt_behavior("unknown_x", mcp_annotations=None) == "block"

    def test_missing_key_falls_through(self) -> None:
        assert (
            get_tool_interrupt_behavior("unknown_x", mcp_annotations={"other": "stuff"}) == "block"
        )


class TestHelperFunctions:
    """has_any_block_tool / partition_by_behavior / warn_unclassified_tools."""

    def test_has_any_block_tool_empty(self) -> None:
        assert not has_any_block_tool([])

    def test_has_any_block_tool_all_cancel(self) -> None:
        assert not has_any_block_tool(["read_file", "grep", "glob"])

    def test_has_any_block_tool_mixed(self) -> None:
        assert has_any_block_tool(["read_file", "write_file"])

    def test_has_any_block_tool_unknown_defaults_block(self) -> None:
        assert has_any_block_tool(["unknown_tool_xyz"])

    def test_partition_orders_preserved(self) -> None:
        block, cancel = partition_by_behavior(["read_file", "write_file", "grep", "run_shell"])
        assert block == ["write_file", "run_shell"]
        assert cancel == ["read_file", "grep"]

    def test_warn_unclassified_returns_count(self, caplog) -> None:
        import logging

        caplog.set_level(logging.WARNING)
        n = warn_unclassified_tools(["read_file", "unknown_a", "unknown_b"])
        assert n == 2
        assert any(
            "unknown_a" in rec.message for rec in caplog.records if rec.levelno == logging.WARNING
        )

    def test_warn_unclassified_empty_input(self) -> None:
        assert warn_unclassified_tools([]) == 0

    def test_warn_unclassified_all_known(self) -> None:
        assert warn_unclassified_tools(["read_file", "write_file", "grep"]) == 0

    def test_type_annotation_runtime(self) -> None:
        """Sanity: InterruptBehavior is a Literal usable in static analysis,
        not a runtime requirement."""
        b: InterruptBehavior = "cancel"
        assert b == "cancel"

"""
System tool catalog (Tool Catalog).

Follows progressive disclosure principle (aligned with Agent Skills spec):
- Level 1: Tool inventory (name + description) - provided in system prompt
- Level 2: Detailed documentation (detail + examples + triggers + prerequisites) - obtained via get_tool_info / passed to LLM API
- Level 3: Direct tool execution

Tool definition format (following tool-definition-spec.md):
{
    # Required fields
    "name": "tool_name",
    "description": "Brief description for inventory disclosure (Level 1)",
    "input_schema": {...},

    # Recommended fields
    "detail": "Detailed usage documentation (Level 2)",
    "triggers": ["trigger condition 1", "trigger condition 2"],
    "prerequisites": ["prerequisite 1", "prerequisite 2"],
    "examples": [{"scenario": "...", "params": {...}, "expected": "..."}],

    # Optional fields
    "category": "tool category",
    "warnings": ["important warning"],
    "related_tools": [{"name": "...", "relation": "..."}],
}

If no detail field is present, falls back to description.
"""

import difflib
import logging
from collections import OrderedDict

from .definitions.base import infer_category

logger = logging.getLogger(__name__)


# Catalog-excluded tools — these are always loaded with full schema via LLM
# tools parameter, so they are excluded from the textual catalog to save tokens.
# Sorted tuple for stable iteration order (prompt cache friendly).
CATALOG_EXCLUDED_TOOLS = tuple(
    sorted(
        {
            "run_shell",
            "read_file",
            "write_file",
            "edit_file",
            "list_directory",
            "ask_user",
            "glob",
            "web_search",
            "web_fetch",
            "delete_file",
            "read_lints",
            "semantic_search",
        }
    )
)
_CATALOG_EXCLUDED_SET = frozenset(CATALOG_EXCLUDED_TOOLS)

# Backwards compat alias
HIGH_FREQ_TOOLS = CATALOG_EXCLUDED_TOOLS


class ToolCatalog:
    """
    System tool catalog.

    Manages tool inventory generation and formatting for system prompt injection.
    Supports progressive disclosure:
    - Level 1: Tool inventory (name + short_description)
    - Level 2: Complete definition (description + input_schema)

    High-frequency tools (run_shell, read_file, write_file, list_directory) are injected
    directly with complete schema into LLM tools parameter, bypassing get_tool_info step.
    """

    # Tool inventory template
    # Note: this text goes into system prompt, keep it brief (reduce noise and token usage)
    CATALOG_TEMPLATE = """
## Available System Tools

### Tool Selection Priority
1. **Installed skills** — check skills/ first
2. **MCP server tools** — external integrations via MCP protocol
3. **Shell commands** — system commands and scripts
4. **Temporary scripts** — write_file + run_shell for one-off tasks
5. **Search + install** — find and install new capabilities from GitHub
6. **Create skills** — use skill-creator for permanent capabilities

### Capability Extension Protocol
Missing a capability? Search installed skills -> search web -> install or create -> continue task.
Never tell user "I can't do this" — acquire the capability and proceed.

Use `tool_search(query="...")` to discover full parameters of deferred tools
(标有 [DEFERRED] 的工具). Calling them directly is allowed and will auto-promote,
but with full schema you'll fill arguments more reliably.

{tool_list}
"""

    # Category display order (determines ordering in system prompt)
    # Categories not in this list are automatically appended at the end
    CATEGORY_ORDER = [
        "File System",
        "Agent",
        "Skills",
        "Plugin",
        "Memory",
        "Web Search",
        "Browser",
        "Desktop",
        "Scheduled",
        "IM Channel",
        "Profile",
        "System",
        "MCP",
        "Plan",
        "Persona",
        "Sticker",
        "Config",
    ]

    # Category display name mapping (internal name -> display name in system prompt)
    # Categories not in this mapping use their internal name directly
    CATEGORY_DISPLAY_NAMES = {
        "Desktop": "Desktop (Windows)",
        "Skills": "Skills Management",
        "Plugin": "Plugin Management",
        "Scheduled": "Scheduled Tasks",
        "Profile": "User Profile",
    }

    TOOL_ENTRY_TEMPLATE = "- **{name}**: {description}"  # only used with _safe_format
    CATEGORY_TEMPLATE = "\n### {category}\n{tools}"  # only used with _safe_format

    @staticmethod
    def _safe_format(template: str, **kwargs: str) -> str:
        """str.format that won't crash on {/} in values."""
        try:
            return template.format(**kwargs)
        except (KeyError, ValueError, IndexError) as e:
            logger.warning(
                "[ToolCatalog] str.format failed (template=%r, keys=%s): %s",
                template[:60],
                list(kwargs.keys()),
                e,
            )
            return template + " " + " | ".join(f"{k}={v}" for k, v in kwargs.items())

    def __init__(self, tools: list[dict]):
        """
        Initialize tool catalog.

        Args:
            tools: Tool definition list; each tool includes name, short_description, description, input_schema
        """
        nameless = [t for t in tools if not t.get("name")]
        if nameless:
            logger.warning(
                "[ToolCatalog] __init__: skipped %d tool(s) without a name, keys present: %s",
                len(nameless),
                [list(t.keys())[:5] for t in nameless[:3]],
            )
        self._tools = {t["name"]: t for t in tools if t.get("name")}
        self._tool_sources: dict[str, str] = {}
        self._cached_catalog: str | None = None
        self._deferred_tools: set[str] = set()

    def set_deferred_tools(self, deferred: set[str]) -> None:
        """Update the set of currently deferred tool names.

        Invalidates the cached catalog so the next get_catalog() call
        reflects the updated deferred annotations.
        """
        if deferred != self._deferred_tools:
            self._deferred_tools = deferred
            self._cached_catalog = None
            logger.info(
                "[ToolCatalog] deferred set updated: %d tools, cache invalidated",
                len(deferred),
            )

    def generate_catalog(
        self,
        exclude_high_freq: bool = True,
        deferred_tools: set[str] | None = None,
    ) -> str:
        """
        Generate tool inventory (Level 1).

        Automatically aggregates categories from tool definition category field,
        sorts by CATEGORY_ORDER. New tools with category field appear automatically
        without code modification.

        Args:
            exclude_high_freq: Whether to exclude high-frequency tools (default: True,
                because they are already fully injected via LLM tools parameter and
                do not need duplication in text inventory)
            deferred_tools: Set of currently deferred tool names. Marked with [deferred]
                in catalog text to indicate LLM must call tool_search first.

        Returns:
            Formatted tool inventory string
        """
        if deferred_tools is not None:
            self._deferred_tools = deferred_tools
        if not self._tools:
            return "\n## Available System Tools\n\nNo system tools available.\n"

        # 1. Automatically aggregate tools by category field
        categories: OrderedDict[str, list[tuple[str, dict]]] = OrderedDict()
        uncategorized: list[tuple[str, dict]] = []

        for name in sorted(self._tools):
            tool = self._tools[name]
            # High-frequency tools already fully provided in tools parameter, skip to save tokens
            if exclude_high_freq and name in _CATALOG_EXCLUDED_SET:
                continue
            cat = tool.get("category")
            if not cat:
                cat = infer_category(name)  # fallback to base.py inference
            if not cat and name in self._tool_sources:
                cat = "Plugin"
            if cat:
                categories.setdefault(cat, []).append((name, tool))
            else:
                uncategorized.append((name, tool))

        # 2. Sort and output by CATEGORY_ORDER
        category_sections = []
        emitted_cats: set[str] = set()

        for cat in self.CATEGORY_ORDER:
            if cat not in categories:
                continue
            display_name = self.CATEGORY_DISPLAY_NAMES.get(cat, cat)
            tools_in_cat = categories[cat]
            section = self._format_category_section(display_name, tools_in_cat)
            if section:
                category_sections.append(section)
            emitted_cats.add(cat)

        # 3. Categories not in CATEGORY_ORDER (new categories appear at end automatically)
        for cat, tools_in_cat in categories.items():
            if cat in emitted_cats:
                continue
            display_name = self.CATEGORY_DISPLAY_NAMES.get(cat, cat)
            section = self._format_category_section(display_name, tools_in_cat)
            if section:
                category_sections.append(section)

        # 4. Uncategorized tools (fallback)
        if uncategorized:
            section = self._format_category_section("Other", uncategorized)
            if section:
                category_sections.append(section)

        tool_list = "\n".join(category_sections)
        catalog = self._safe_format(self.CATALOG_TEMPLATE, tool_list=tool_list)
        self._cached_catalog = catalog

        logger.info(f"Generated tool catalog with {len(self._tools)} tools")
        return catalog

    def get_direct_tool_schemas(self) -> list[dict]:
        """
        Get complete schema of high-frequency tools for direct injection into LLM tools parameter.

        These tools (run_shell, read_file, write_file, list_directory) bypass progressive
        disclosure and are provided to the LLM directly as {name, description, input_schema}.

        Returns:
            Complete schema list of high-frequency tools
        """
        schemas = []
        for tool_name in CATALOG_EXCLUDED_TOOLS:
            tool = self._tools.get(tool_name)
            if tool:
                schemas.append(
                    {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "input_schema": tool.get("input_schema", {}),
                    }
                )
        return schemas

    def is_high_freq_tool(self, tool_name: str) -> bool:
        """Check whether a tool is high-frequency."""
        return tool_name in _CATALOG_EXCLUDED_SET

    def _format_category_section(
        self, display_name: str, tools: list[tuple[str, dict]]
    ) -> str | None:
        """
        Format tool entries for a category.

        Args:
            display_name: Category display name
            tools: List of (name, tool_def) tuples

        Returns:
            Formatted string, or None if no tools
        """
        if not tools:
            return None

        deferred = getattr(self, "_deferred_tools", set())

        entries = []
        for name, tool in tools:
            desc = tool.get("short_description") or self._get_short_description(
                tool.get("description", "")
            )
            if name in deferred:
                desc = f"[deferred] {desc}"
            source = self._tool_sources.get(name, "")
            suffix = f" _(from {source})_" if source else ""
            entry = (
                self._safe_format(self.TOOL_ENTRY_TEMPLATE, name=name, description=desc) + suffix
            )
            entries.append(entry)

        return self._safe_format(
            self.CATEGORY_TEMPLATE, category=display_name, tools="\n".join(entries)
        )

    def _get_short_description(self, description: str) -> str:
        """
        Extract short description from complete description.

        Args:
            description: Complete description

        Returns:
            Short description (first line, not truncated to preserve complete warnings)
        """
        if not description:
            return ""

        # Get first line, do not truncate further
        # Reason: complete tool definition is already passed to LLM API via tools parameter.
        # Truncating in inventory loses important warnings (like "must check status first"),
        # causing LLM behavioral issues (e.g. saying "done" without calling tool).
        first_line = description.split("\n")[0].strip()

        return first_line

    def get_tool_groups(self) -> dict[str, set[str]]:
        """Auto-build tool groups from each tool's category field. No hardcoded mapping needed."""
        groups: dict[str, set[str]] = {}
        for name, tool in self._tools.items():
            cat = tool.get("category")
            if not cat:
                cat = infer_category(name)
            if not cat and name in self._tool_sources:
                cat = "Plugin"
            if cat:
                groups.setdefault(cat, set()).add(name)
        return groups

    def get_catalog(
        self,
        refresh: bool = False,
        exclude_high_freq: bool = True,
        deferred_tools: set[str] | None = None,
    ) -> str:
        """
        Get tool inventory.

        Args:
            refresh: Whether to force refresh
            exclude_high_freq: Whether to exclude high-frequency tools (default: True)
            deferred_tools: Set of currently deferred tool names

        Returns:
            Tool inventory string
        """
        if refresh or self._cached_catalog is None or deferred_tools is not None:
            return self.generate_catalog(
                exclude_high_freq=exclude_high_freq,
                deferred_tools=deferred_tools,
            )
        return self._cached_catalog

    def get_tool_info(self, tool_name: str) -> dict | None:
        """
        Get complete definition of a tool (Level 2).

        Args:
            tool_name: Tool name

        Returns:
            Complete tool definition including description and input_schema
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return None

        return {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("input_schema", {}),
        }

    def get_tool_info_formatted(self, tool_name: str) -> str:
        """
        Get formatted complete information of a tool (Level 2 detailed documentation).

        Supports new spec fields: triggers, prerequisites, examples, warnings, related_tools

        Args:
            tool_name: Tool name

        Returns:
            Formatted tool information string
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return self._format_tool_not_found(tool_name)

        deferred = getattr(self, "_deferred_tools", set())
        is_deferred = tool_name in deferred

        output = f"# Tool: {tool['name']}\n\n"

        if is_deferred:
            output += (
                "**Status**: DEFERRED — this tool's schema is not yet loaded. "
                "Call `tool_search` to activate it, then use it in the next turn.\n\n"
            )

        # Category
        category = tool.get("category")
        if category:
            output += f"**Category**: {category}\n\n"

        # Detailed documentation (prefer detail, fall back to description)
        detail = tool.get("detail") or tool.get("description", "No description")
        output += f"{detail}\n\n"

        # Warnings
        warnings = tool.get("warnings", [])
        if warnings:
            output += "## ⚠️ Warnings\n\n"
            for warning in warnings:
                output += f"- {warning}\n"
            output += "\n"

        # Trigger conditions
        triggers = tool.get("triggers", [])
        if triggers:
            output += "## When to Use\n\n"
            for trigger in triggers:
                output += f"- {trigger}\n"
            output += "\n"

        # Prerequisites
        prerequisites = tool.get("prerequisites", [])
        if prerequisites:
            output += "## Prerequisites\n\n"
            for prereq in prerequisites:
                if isinstance(prereq, dict):
                    output += f"- {prereq.get('condition', prereq)}\n"
                else:
                    output += f"- {prereq}\n"
            output += "\n"

        # Parameter documentation
        schema = tool.get("input_schema", {})
        props = schema.get("properties", {})
        required = schema.get("required", [])

        if props:
            output += "## Parameters\n\n"
            for param_name, param_def in props.items():
                req_mark = " **(required)**" if param_name in required else ""
                param_type = param_def.get("type", "any")
                param_desc = param_def.get("description", "")
                default = param_def.get("default")
                enum_vals = param_def.get("enum")

                output += f"- `{param_name}` ({param_type}){req_mark}: {param_desc}"
                if default is not None:
                    output += f" (default: {default})"
                if enum_vals:
                    output += f" [options: {', '.join(str(v) for v in enum_vals)}]"
                output += "\n"
            output += "\n"
        else:
            output += "## Parameters\n\nNo parameters required.\n\n"

        # Usage examples
        examples = tool.get("examples", [])
        if examples:
            output += "## Examples\n\n"
            for i, example in enumerate(examples, 1):
                scenario = example.get("scenario", f"Example {i}")
                params = example.get("params", {})
                expected = example.get("expected", "")

                output += f"**{scenario}**\n"
                output += f"```json\n{self._format_params(params)}\n```\n"
                if expected:
                    output += f"→ {expected}\n"
                output += "\n"

        # Related tools
        related_tools = tool.get("related_tools", [])
        if related_tools:
            output += "## Related Tools\n\n"
            for related in related_tools:
                name = related.get("name", "")
                relation = related.get("relation", "")
                output += f"- `{name}`: {relation}\n"
            output += "\n"

        return output

    def _format_tool_not_found(self, tool_name: str) -> str:
        """生成 tool-not-found 的引导式提示。

        - 若本节点存在相近名工具（difflib 相似度 >= 0.5），列出建议；
        - 否则提示该工具不可用、不必再探查，并指引使用 org_* 工具。

        建议来源严格限定 self._tools.keys()，因此只会建议本节点真实可用的工具，
        不会把全局工具表里不可用的名字回灌进来。
        """
        available = list(self._tools.keys())
        suggestions = difflib.get_close_matches(
            tool_name, available, n=3, cutoff=0.5,
        )

        org_tool_count = sum(1 for n in available if n.startswith("org_"))
        only_org = (org_tool_count > 0 and org_tool_count == len(available))

        lines = [f"❌ Tool not found: {tool_name}"]
        if suggestions:
            lines.append(
                "可能的相近工具（来自本节点当前可用工具集）："
                + ", ".join(suggestions)
            )
            lines.append(
                f"如需详情请用 get_tool_info('{suggestions[0]}') 查看。"
            )
        elif only_org:
            lines.append(
                "本节点当前仅可使用 org_* 组织协作工具。"
                f"工具 '{tool_name}' 不会出现在此节点，请停止探查；"
                "请改用 org_delegate_task / org_send_message / org_submit_deliverable 等组织工具完成协作。"
            )
        else:
            lines.append(
                f"工具 '{tool_name}' 不在本节点的可用工具集合中，请勿继续探查。"
                "可用工具清单已在系统提示中列出，按其中的工具名调用即可。"
            )
        return "\n".join(lines)

    def _format_params(self, params: dict) -> str:
        """Format parameters as JSON string."""
        import json

        if not params:
            return "{}"
        return json.dumps(params, ensure_ascii=False, indent=2)

    def list_tools(self) -> list[str]:
        """List all tool names."""
        return list(self._tools.keys())

    def has_tool(self, tool_name: str) -> bool:
        """Check whether a tool exists."""
        return tool_name in self._tools

    def update_tools(self, tools: list[dict]) -> None:
        """
        Update tool list.

        Args:
            tools: New tool definition list
        """
        nameless = [t for t in tools if not t.get("name")]
        if nameless:
            logger.warning(
                "[ToolCatalog] update_tools: skipped %d tool(s) without a name, keys present: %s",
                len(nameless),
                [list(t.keys())[:5] for t in nameless[:3]],
            )
        self._tools = {t["name"]: t for t in tools if t.get("name")}
        self._tool_sources.clear()
        self._cached_catalog = None

    def add_tool(self, tool: dict, source: str | None = None) -> None:
        """
        Add a single tool.

        Args:
            tool: Tool definition (supports Anthropic and OpenAI format)
            source: Tool source identifier (e.g., ``"plugin:lark-cli-tool"``)
        """
        name = tool.get("name") or tool.get("function", {}).get("name", "")
        if not name:
            raise ValueError("Tool definition must have a 'name'")
        self._tools[name] = tool
        if source:
            self._tool_sources[name] = source
        self._cached_catalog = None

    def remove_tool(self, tool_name: str) -> bool:
        """
        Remove a tool.

        Args:
            tool_name: Tool name

        Returns:
            Whether removal was successful
        """
        if tool_name in self._tools:
            del self._tools[tool_name]
            self._tool_sources.pop(tool_name, None)
            self._cached_catalog = None
            return True
        return False

    def invalidate_cache(self) -> None:
        """Invalidate cache."""
        self._cached_catalog = None

    @property
    def tool_count(self) -> int:
        """Tool count."""
        return len(self._tools)


def create_tool_catalog(tools: list[dict]) -> ToolCatalog:
    """Convenience function: create tool catalog."""
    return ToolCatalog(tools)

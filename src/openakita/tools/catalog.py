"""
系统工具目录 (Tool Catalog)

遵循渐进式披露原则（与 Agent Skills 规范对齐）:
- Level 1: 工具清单 (name + description) - 在系统提示中提供
- Level 2: 详细说明 (detail + examples + triggers + prerequisites) - 通过 get_tool_info 获取 / 传给 LLM API
- Level 3: 直接执行工具

工具定义格式（遵循 tool-definition-spec.md）：
{
    # 必填字段
    "name": "tool_name",
    "description": "清单披露的简短描述（Level 1）",
    "input_schema": {...},

    # 推荐字段
    "detail": "详细使用说明（Level 2）",
    "triggers": ["触发条件1", "触发条件2"],
    "prerequisites": ["前置条件1", "前置条件2"],
    "examples": [{"scenario": "...", "params": {...}, "expected": "..."}],

    # 可选字段
    "category": "工具分类",
    "warnings": ["重要警告"],
    "related_tools": [{"name": "...", "relation": "..."}],
}

如果没有 detail 字段，则 fallback 到 description。
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
    系统工具目录

    管理工具清单的生成和格式化，用于系统提示注入。
    支持渐进式披露：
    - Level 1: 工具清单 (name + short_description)
    - Level 2: 完整定义 (description + input_schema)

    高频工具 (run_shell, read_file, write_file, list_directory) 直接以完整
    schema 注入 LLM tools 参数，无需经过 get_tool_info 中间步骤。
    """

    # 工具清单模板
    # 注意：该段落会进入 system prompt，尽量短（降低噪声与 token 占用）
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

    # 分类展示顺序（决定系统提示中的排列顺序）
    # 不在此列表中的分类会自动追加到末尾
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

    # 分类显示名映射（内部名 -> 系统提示中的显示名）
    # 未在此映射中的分类直接使用内部名
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
        初始化工具目录

        Args:
            tools: 工具定义列表，每个工具包含 name, short_description, description, input_schema
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
        生成工具清单（Level 1）

        从工具定义的 category 字段自动聚合分类，按 CATEGORY_ORDER 排序输出。
        新增工具只要有 category 字段就会自动出现，无需修改此处代码。

        Args:
            exclude_high_freq: 是否排除高频工具（默认排除，因为它们已通过
                LLM tools 参数直接注入完整 schema，不需要在文本清单中重复）
            deferred_tools: 当前被 defer 的工具名集合。在 catalog 文本中标注
                [deferred] 以告知 LLM 需先 tool_search 才能调用。

        Returns:
            格式化的工具清单字符串
        """
        if deferred_tools is not None:
            self._deferred_tools = deferred_tools
        if not self._tools:
            return "\n## Available System Tools\n\nNo system tools available.\n"

        # 1. 按 category 字段自动聚合工具
        categories: OrderedDict[str, list[tuple[str, dict]]] = OrderedDict()
        uncategorized: list[tuple[str, dict]] = []

        for name in sorted(self._tools):
            tool = self._tools[name]
            # 高频工具已在 tools 参数中全量提供，跳过以节省 token
            if exclude_high_freq and name in _CATALOG_EXCLUDED_SET:
                continue
            cat = tool.get("category")
            if not cat:
                cat = infer_category(name)  # fallback 到 base.py 的推断
            if not cat and name in self._tool_sources:
                cat = "Plugin"
            if cat:
                categories.setdefault(cat, []).append((name, tool))
            else:
                uncategorized.append((name, tool))

        # 2. 按 CATEGORY_ORDER 排序输出
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

        # 3. 未在 CATEGORY_ORDER 中的分类（新分类自动出现在末尾）
        for cat, tools_in_cat in categories.items():
            if cat in emitted_cats:
                continue
            display_name = self.CATEGORY_DISPLAY_NAMES.get(cat, cat)
            section = self._format_category_section(display_name, tools_in_cat)
            if section:
                category_sections.append(section)

        # 4. 未分类工具（兜底）
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
        获取高频工具的完整 schema，用于直接注入 LLM tools 参数。

        这些工具（run_shell, read_file, write_file, list_directory）
        跳过渐进式披露，直接以 {name, description, input_schema} 提供给 LLM。

        Returns:
            高频工具的完整 schema 列表
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
        """判断是否为高频工具"""
        return tool_name in _CATALOG_EXCLUDED_SET

    def _format_category_section(
        self, display_name: str, tools: list[tuple[str, dict]]
    ) -> str | None:
        """
        格式化一个分类的工具条目

        Args:
            display_name: 分类显示名
            tools: (name, tool_def) 列表

        Returns:
            格式化字符串，无工具时返回 None
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
        从完整描述中提取简短描述

        Args:
            description: 完整描述

        Returns:
            简短描述（第一行，不截断以保留完整警告信息）
        """
        if not description:
            return ""

        # 取第一行，不再截断
        # 原因：完整工具定义已通过 tools 参数传给 LLM API，
        # 清单中截断会丢失重要警告（如 ⚠️ 必须先检查状态），
        # 导致 LLM 行为异常（如不调用工具就说"完成"）
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
        获取工具清单

        Args:
            refresh: 是否强制刷新
            exclude_high_freq: 是否排除高频工具（默认排除）
            deferred_tools: 当前被 defer 的工具名集合

        Returns:
            工具清单字符串
        """
        if refresh or self._cached_catalog is None or deferred_tools is not None:
            return self.generate_catalog(
                exclude_high_freq=exclude_high_freq,
                deferred_tools=deferred_tools,
            )
        return self._cached_catalog

    def get_tool_info(self, tool_name: str) -> dict | None:
        """
        获取工具的完整定义（Level 2）

        Args:
            tool_name: 工具名称

        Returns:
            工具完整定义，包含 description 和 input_schema
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
        获取工具的格式化完整信息（Level 2 详细说明）

        支持新规范字段：triggers, prerequisites, examples, warnings, related_tools

        Args:
            tool_name: 工具名称

        Returns:
            格式化的工具信息字符串
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

        # 分类
        category = tool.get("category")
        if category:
            output += f"**Category**: {category}\n\n"

        # 详细说明（优先使用 detail，否则 fallback 到 description）
        detail = tool.get("detail") or tool.get("description", "No description")
        output += f"{detail}\n\n"

        # 警告信息
        warnings = tool.get("warnings", [])
        if warnings:
            output += "## ⚠️ Warnings\n\n"
            for warning in warnings:
                output += f"- {warning}\n"
            output += "\n"

        # 触发条件
        triggers = tool.get("triggers", [])
        if triggers:
            output += "## When to Use\n\n"
            for trigger in triggers:
                output += f"- {trigger}\n"
            output += "\n"

        # 前置条件
        prerequisites = tool.get("prerequisites", [])
        if prerequisites:
            output += "## Prerequisites\n\n"
            for prereq in prerequisites:
                if isinstance(prereq, dict):
                    output += f"- {prereq.get('condition', prereq)}\n"
                else:
                    output += f"- {prereq}\n"
            output += "\n"

        # 参数说明
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

        # 使用示例
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

        # 相关工具
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
        """格式化参数为 JSON 字符串"""
        import json

        if not params:
            return "{}"
        return json.dumps(params, ensure_ascii=False, indent=2)

    def list_tools(self) -> list[str]:
        """列出所有工具名称"""
        return list(self._tools.keys())

    def has_tool(self, tool_name: str) -> bool:
        """检查工具是否存在"""
        return tool_name in self._tools

    def update_tools(self, tools: list[dict]) -> None:
        """
        更新工具列表

        Args:
            tools: 新的工具定义列表
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
        添加单个工具

        Args:
            tool: 工具定义（支持 Anthropic 和 OpenAI 格式）
            source: 工具来源标识（如 ``"plugin:lark-cli-tool"``）
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
        移除工具

        Args:
            tool_name: 工具名称

        Returns:
            是否成功移除
        """
        if tool_name in self._tools:
            del self._tools[tool_name]
            self._tool_sources.pop(tool_name, None)
            self._cached_catalog = None
            return True
        return False

    def invalidate_cache(self) -> None:
        """使缓存失效"""
        self._cached_catalog = None

    @property
    def tool_count(self) -> int:
        """工具数量"""
        return len(self._tools)


def create_tool_catalog(tools: list[dict]) -> ToolCatalog:
    """便捷函数：创建工具目录"""
    return ToolCatalog(tools)

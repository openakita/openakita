"""工作台节点模板目录

把已加载、且注册了 LLM 工具的插件转化为"工作台节点模板"。
前端 OrgEditor 通过 ``GET /api/orgs/plugin-workbench-templates`` 拉取，
点击模板后用 ``suggested_node`` 创建一个预配置的叶子 OrgNode：
- ``external_tools`` 直接列出该插件注册的工具名，运行时由
  ``expand_tool_categories`` 原样透传，从而让节点 Agent 可以调用插件工具；
- ``plugin_origin`` 标识该节点来自工作台模板，仅用于 UI 渲染和系统
  提示词点睛，不参与运行时工具放行判定。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openakita.plugins.manager import PluginManager

logger = logging.getLogger(__name__)


def _default_goal_for(manifest: Any) -> str:
    """Compose a default role goal string for a workbench node."""
    name = manifest.display_name_zh or manifest.name or manifest.id
    desc = (
        (manifest.description_i18n or {}).get("zh")
        or manifest.description
        or ""
    ).strip()
    if desc:
        return f"在组织中作为「{name}」工作台节点，按上级派单调用工作台工具完成产出。能力：{desc}"
    return f"在组织中作为「{name}」工作台节点，按上级派单调用工作台工具完成产出。"


def _default_prompt_for(manifest: Any, tool_names: list[str]) -> str:
    """Compose a default custom_prompt for a workbench node.

    The prompt explains:
      1. when the node should be activated (only on org_delegate_task);
      2. that runtime auto-downloads/registers any artifacts produced by
         the workbench tools, so the node need NOT declare
         ``file_attachments`` in ``org_submit_deliverable`` manually;
      3. how upstream-supplied ``asset_id`` / ``image_url`` should be
         threaded into downstream tool calls;
      4. how to fall back to a plain-text deliverable when the upstream
         request is merely a question rather than an actual production task.
    """
    display_name = manifest.display_name_zh or manifest.name or manifest.id
    tool_list = "、".join(tool_names) if tool_names else "(无工具)"
    return (
        f"你是组织中的【{display_name}】工作台节点。\n"
        f"专属能力：{tool_list}\n\n"
        "工作规范：\n"
        "1. 收到 org_delegate_task 后启动，按工具 input_schema 严格调用工作台工具，"
        "不要凭空想象工具参数；\n"
        "2. 工作台产出的图片/视频会被组织 runtime 自动下载到 org workspace 的 "
        "plugin_assets/ 目录并登记为任务附件——你只需要在 org_submit_deliverable "
        "的 deliverable 字段中描述产出内容（标题、规格、prompt 摘要、asset_id），"
        "不必再手动声明 file_attachments；\n"
        "3. 若上级在 prompt 中提供了上游工作台的 asset_id 或 image_url，请将其"
        "如实填入对应工具参数（例如 seedance_create.from_asset_ids 或 "
        "content[].image_url），不要省略；\n"
        "4. 若上级只是问询/讨论而非真正下单产出，可直接用 org_submit_deliverable "
        "提交文字回答，无需调用工作台工具；\n"
        "5. 完成后调 org_submit_deliverable 把成果交给委派人，等待验收。"
    )


def _tool_summary(tool: dict) -> dict:
    """Return the minimal subset of a registered tool dict for templates."""
    return {
        "name": tool.get("name", ""),
        "description": tool.get("description", ""),
        "input_schema": tool.get("input_schema") or {},
    }


def _tool_name(tool: Any) -> str:
    """Extract a tool name from either a tool dict or PluginAPI's name list."""
    if isinstance(tool, str):
        return tool
    if isinstance(tool, dict):
        return tool.get("name") or tool.get("function", {}).get("name", "")
    return ""


def _host_get(api: Any, key: str) -> Any:
    host = getattr(api, "_host", None)
    if host is None or not hasattr(host, "get"):
        return None
    try:
        return host.get(key)
    except Exception:
        return None


def _lookup_tool_definition(api: Any, name: str) -> dict:
    """Resolve a registered tool name back to its full definition when possible."""
    tool_catalog = _host_get(api, "tool_catalog")
    if tool_catalog is not None and hasattr(tool_catalog, "get_tool_info"):
        try:
            info = tool_catalog.get_tool_info(name)
            if isinstance(info, dict):
                return info
        except Exception:
            logger.debug(
                "[workbench-templates] failed to read tool_catalog info for %s",
                name,
                exc_info=True,
            )

    tool_definitions = _host_get(api, "tool_definitions")
    if tool_definitions is not None:
        try:
            for defn in tool_definitions:
                if not isinstance(defn, dict):
                    continue
                defn_name = defn.get("name") or defn.get("function", {}).get("name", "")
                if defn_name == name:
                    return defn
        except Exception:
            logger.debug(
                "[workbench-templates] failed to scan tool_definitions for %s",
                name,
                exc_info=True,
            )

    return {"name": name, "description": "", "input_schema": {}}


def _registered_tool_summaries(api: Any) -> list[dict]:
    """Return display-ready tool summaries for a plugin's registered tools."""
    try:
        raw_tools = list(getattr(api, "_registered_tools", None) or [])
    except Exception:
        logger.debug("[workbench-templates] failed to read registered tools", exc_info=True)
        return []

    summaries: list[dict] = []
    seen: set[str] = set()
    for raw in raw_tools:
        if isinstance(raw, dict):
            defn = raw
        else:
            name = _tool_name(raw)
            if not name:
                continue
            defn = _lookup_tool_definition(api, name)

        summary = _tool_summary(defn)
        name = summary.get("name", "")
        if not name or name in seen:
            continue
        seen.add(name)
        summaries.append(summary)
    return summaries


def build_workbench_templates(pm: PluginManager | None) -> list[dict]:
    """Build workbench node templates from a PluginManager.

    Only plugins that are loaded AND have registered at least one LLM tool
    will appear as a workbench. Plugins without any callable tool (pure UI,
    pure routes, MCP-only, skill-only, etc.) are intentionally hidden.
    """
    if pm is None:
        return []

    templates: list[dict] = []
    for lp in pm.loaded_plugins.values():
        tools = _registered_tool_summaries(lp.api)
        if not tools:
            continue

        m = lp.manifest
        plugin_id = m.id
        version = m.version
        display_zh = m.display_name_zh or m.name or plugin_id
        display_en = m.display_name_en or m.name or plugin_id
        desc_i18n = dict(m.description_i18n or {})
        tool_names = [t["name"] for t in tools]

        templates.append(
            {
                "id": f"workbench:{plugin_id}",
                "plugin_id": plugin_id,
                "version": version,
                "name": display_zh,
                "name_i18n": {"zh": display_zh, "en": display_en},
                "description": m.description or desc_i18n.get("zh") or "",
                "description_i18n": desc_i18n,
                "icon": m.icon or "",
                "category": m.category or "",
                "tools": tools,
                "tool_names": tool_names,
                "suggested_node": {
                    "role_title": display_zh,
                    "role_goal": _default_goal_for(m),
                    "custom_prompt": _default_prompt_for(m, tool_names),
                    "external_tools": list(tool_names),
                    "agent_profile_id": "default",
                    "enable_file_tools": False,
                    "mcp_servers": [],
                    "skills": [],
                    "skills_mode": "all",
                    "max_concurrent_tasks": 1,
                    "can_delegate": False,
                    "can_escalate": True,
                    "plugin_origin": {
                        "plugin_id": plugin_id,
                        "template_id": f"workbench:{plugin_id}",
                        "version": version,
                    },
                },
            }
        )

    templates.sort(key=lambda t: (t.get("category") or "", t.get("name") or ""))
    return templates


def deprecated_tools_for_node(
    node_external_tools: list[str], pm: PluginManager | None
) -> list[str]:
    """Return a list of external_tools entries that are NOT registered by any
    currently loaded plugin AND not built-in tool category names.

    Used by the editor to warn users when an upgraded plugin renamed/removed
    tools that older workbench nodes still reference.
    """
    if pm is None or not node_external_tools:
        return []

    from .tool_categories import ALL_CATEGORY_NAMES

    known: set[str] = set()
    for lp in pm.loaded_plugins.values():
        try:
            for t in getattr(lp.api, "_registered_tools", None) or []:
                name = _tool_name(t)
                if name:
                    known.add(name)
        except Exception:
            continue
    return [
        t
        for t in node_external_tools
        if t and t not in ALL_CATEGORY_NAMES and t not in known
    ]

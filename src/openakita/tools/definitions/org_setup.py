"""
Organization setup tool — create and manage organizations through natural language.

Always injected alongside AGENT_TOOLS.
"""

_EDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {
            "type": "string",
            "description": "role_title or node_id of the source node",
        },
        "target": {
            "type": "string",
            "description": "role_title or node_id of the target node",
        },
        "edge_type": {
            "type": "string",
            "enum": ["collaborate", "escalate", "consult"],
            "description": (
                "Edge type (excluding hierarchy; for hierarchical relationships use a node's parent_role_title): "
                "collaborate=collaboration, escalate=escalation, consult=consultation"
            ),
        },
        "label": {
            "type": "string",
            "description": "Edge label (e.g., 'requirements discussion', 'data handoff')",
        },
        "bidirectional": {
            "type": "boolean",
            "description": "Whether communication is bidirectional (default true)",
        },
    },
    "required": ["source", "target", "edge_type"],
}

_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "node_id": {
            "type": "string",
            "description": "Existing node ID (required on update for precise matching; leave empty when adding new)",
        },
        "role_title": {
            "type": "string",
            "description": "Role title (required, e.g., CEO, CTO, Frontend Developer)",
        },
        "role_goal": {
            "type": "string",
            "description": "Role goal (e.g., define the technical roadmap and ensure system stability)",
        },
        "department": {
            "type": "string",
            "description": "Department (e.g., Engineering, Product)",
        },
        "level": {
            "type": "integer",
            "description": "Hierarchy level (0=top/root, 1=middle, 2=ground level)",
        },
        "agent_profile_id": {
            "type": "string",
            "description": (
                "Associated Agent Profile ID (optional). "
                "Choose from the agents/custom_agents list returned by get_resources, "
                "or leave empty and use custom_prompt to define a brand-new role directly."
            ),
        },
        "parent_role_title": {
            "type": "string",
            "description": "Parent role title (used to automatically create hierarchy. Not required for root node)",
        },
        "external_tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "External tools (category or tool name, e.g., "
                "'research', 'filesystem', 'planning', 'browser')"
            ),
        },
        "custom_prompt": {
            "type": "string",
            "description": (
                "Custom prompt. Can be used alone (without agent_profile_id) to create a brand-new role, "
                "or combined with agent_profile_id to append instructions."
            ),
        },
    },
    "required": ["role_title"],
}

ORG_SETUP_TOOLS = [
    {
        "name": "setup_organization",
        "category": "Organization",
        "description": (
            "Create and manage organizational structures for multi-agent collaboration. "
            "Supports: listing available agents/templates (get_resources), "
            "listing existing orgs (list_orgs), viewing an org (get_org), "
            "previewing before creation (preview), creating (create), "
            "creating from template (create_from_template), "
            "modifying an existing org (update_org), deleting (delete_org). "
            "For CREATION: call get_resources first, ask clarifying questions, then create. "
            "For MODIFICATION: call list_orgs to find the org, get_org to see its structure, "
            "then update_org with incremental changes."
        ),
        "detail": (
            "Create and manage organizational orchestration structures through natural language.\n\n"
            "## Creation flow\n\n"
            "1. **get_resources** — fetch available Agents (presets + user-built), templates, and tool categories\n"
            "2. **Gather requirements from the user** — actively ask questions when information is incomplete\n"
            "3. **Configure a role for each node** — pick an existing Agent (agent_profile_id), "
            "or use custom_prompt to define a brand-new role directly\n"
            "4. **preview** — show the draft to the user for confirmation\n"
            "5. **create** — formally create after user confirmation\n\n"
            "## Modification flow\n\n"
            "1. **list_orgs** — list existing organizations and identify the target to modify\n"
            "2. **get_org** — fetch the full structure (node IDs, Agents, tools, edges, etc.)\n"
            "3. **Understand the user's intended changes** — confirm which nodes or edges to add/remove/modify\n"
            "4. **Describe the change plan to the user** — explain in text first and ask for confirmation\n"
            "5. **update_org** — submit the incremental update (preserve existing node IDs)\n\n"
            "## Edge types\n\n"
            "The organization supports 4 kinds of relationships:\n"
            "- **hierarchy** — reporting line (parent/child); auto-created via a node's parent_role_title\n"
            "- **collaborate** — collaboration between sibling nodes\n"
            "- **escalate** — cross-level escalation channel\n"
            "- **consult** — request expert input from a specific node\n\n"
            "Hierarchy is managed via parent_role_title; "
            "the other three types are added via edges (on create) or add_edges (on update).\n\n"
            "## Key considerations\n"
            "- On modification, **existing node IDs must be preserved**, because IDs are linked to tasks, memories, and identity files\n"
            "- update_org is an **incremental update**: only pass fields that change; unmentioned nodes remain as-is\n"
            "- Use remove_nodes to delete nodes; associated edges are cleaned up as well\n"
            "- Use remove_edges to delete edges (pass edge IDs obtained from get_org)\n"
            "- remove_edges can only delete non-hierarchy edges; modify parent_role_title to change hierarchy\n"
            "- **Actively ask questions** when information is incomplete; do not guess the user's intent"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "get_resources", "list_orgs", "get_org",
                        "preview", "create", "create_from_template",
                        "update_org", "delete_org",
                    ],
                    "description": (
                        "Action type: "
                        "get_resources=fetch the list of available resources; "
                        "list_orgs=list existing organizations; "
                        "get_org=fetch an organization's full structure; "
                        "preview=preview a new organization structure (no creation); "
                        "create=create an organization; "
                        "create_from_template=create from a template; "
                        "update_org=modify an existing organization (incremental); "
                        "delete_org=delete an organization"
                    ),
                },
                "org_id": {
                    "type": "string",
                    "description": "Organization ID (required for get_org/update_org/delete_org)",
                },
                "name": {
                    "type": "string",
                    "description": "Organization name (required for create/preview, optional for update_org)",
                },
                "description": {
                    "type": "string",
                    "description": "Organization description",
                },
                "core_business": {
                    "type": "string",
                    "description": "Core business description (e.g., cross-border e-commerce operations, SaaS product R&D)",
                },
                "nodes": {
                    "type": "array",
                    "items": _NODE_SCHEMA,
                    "description": "Node list (required for create/preview)",
                },
                "edges": {
                    "type": "array",
                    "items": _EDGE_SCHEMA,
                    "description": (
                        "Non-hierarchy edge list (optional on create/preview). "
                        "Defines collaboration/escalation/consult relationships between sibling nodes. "
                        "Hierarchy does not need to be specified here — it is generated automatically from each node's parent_role_title."
                    ),
                },
                "template_id": {
                    "type": "string",
                    "description": "Template ID (required for create_from_template)",
                },
                "overrides": {
                    "type": "object",
                    "description": "Template override fields (optional for create_from_template)",
                },
                "update_nodes": {
                    "type": "array",
                    "items": _NODE_SCHEMA,
                    "description": (
                        "Nodes to update or add (used by update_org). "
                        "If node_id matches an existing node it is updated; otherwise matched by role_title or added as a new node."
                    ),
                },
                "remove_nodes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of node IDs or role_titles to remove (used by update_org)",
                },
                "add_edges": {
                    "type": "array",
                    "items": _EDGE_SCHEMA,
                    "description": (
                        "Non-hierarchy edges to add (used by update_org). "
                        "source/target may be either node_id or role_title. "
                        "Only collaborate/escalate/consult types are supported."
                    ),
                },
                "remove_edges": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of edge IDs to delete (used by update_org; "
                        "get the IDs from the edge list returned by get_org). "
                        "Only non-hierarchy edges can be removed."
                    ),
                },
                "update_fields": {
                    "type": "object",
                    "description": (
                        "Organization-level field updates (used by update_org), "
                        "e.g., name, description, core_business, heartbeat_enabled"
                    ),
                },
            },
            "required": ["action"],
        },
        "examples": [
            {
                "scenario": "Fetch available resources",
                "params": {"action": "get_resources"},
                "expected": "Returns Agent list, template list, and tool categories",
            },
            {
                "scenario": "List existing organizations",
                "params": {"action": "list_orgs"},
                "expected": "Returns a list of organization summaries (ID, name, status, node count)",
            },
            {
                "scenario": "View organization structure",
                "params": {"action": "get_org", "org_id": "org_xxx"},
                "expected": "Returns the full organization structure: node list, edges (with edge IDs), metadata",
            },
            {
                "scenario": "Modify organization: add a data analyst to the e-commerce team",
                "params": {
                    "action": "update_org",
                    "org_id": "org_xxx",
                    "update_nodes": [
                        {
                            "role_title": "Data Analyst",
                            "role_goal": "Data instrumentation, reporting, growth strategy",
                            "department": "Operations",
                            "level": 1,
                            "agent_profile_id": "data-analyst",
                            "parent_role_title": "Head of Operations",
                            "external_tools": ["research", "filesystem"],
                        }
                    ],
                },
                "expected": "Adds the node and automatically creates the hierarchy relationship",
            },
            {
                "scenario": "Modify organization: swap the Agent on a node",
                "params": {
                    "action": "update_org",
                    "org_id": "org_xxx",
                    "update_nodes": [
                        {
                            "node_id": "node_abc",
                            "agent_profile_id": "content-creator",
                        }
                    ],
                },
                "expected": "Only updates the specified fields; everything else remains unchanged",
            },
            {
                "scenario": "Modify organization: remove a node",
                "params": {
                    "action": "update_org",
                    "org_id": "org_xxx",
                    "remove_nodes": ["Customer Support Lead"],
                },
                "expected": "Removes the node and cleans up associated edges",
            },
            {
                "scenario": "Modify organization: add a collaboration edge between sibling nodes",
                "params": {
                    "action": "update_org",
                    "org_id": "org_xxx",
                    "add_edges": [
                        {
                            "source": "Data Analyst",
                            "target": "Risk Control Officer",
                            "edge_type": "collaborate",
                            "label": "data sharing",
                        }
                    ],
                },
                "expected": "Establishes a collaboration edge between two sibling nodes",
            },
            {
                "scenario": "Delete organization",
                "params": {"action": "delete_org", "org_id": "org_xxx"},
                "expected": "Permanently deletes the organization and all its data",
            },
        ],
    },
]

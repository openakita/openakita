"""
Agent Hub tool definitions

Provides tools for interacting with the OpenAkita Platform Agent Store:
- search_hub_agents: Search for Agents on the platform
- install_hub_agent: Download and install an Agent from the platform
- publish_agent: Publish an Agent to the platform
- get_hub_agent_detail: View Agent details
"""

AGENT_HUB_TOOLS = [
    {
        "name": "search_hub_agents",
        "category": "Agent Hub",
        "description": "Search for Agents on the OpenAkita Platform Agent Store. Returns a list of available Agents with name, description, rating, and download count.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword (empty = browse all)",
                },
                "category": {
                    "type": "string",
                    "description": "Filter by category (e.g. customer_service, development, business)",
                },
                "sort": {
                    "type": "string",
                    "enum": ["downloads", "rating", "newest"],
                    "description": "Sort order (default: downloads)",
                },
                "page": {
                    "type": "integer",
                    "description": "Page number (default: 1)",
                },
            },
        },
    },
    {
        "name": "install_hub_agent",
        "category": "Agent Hub",
        "description": "Download and install an Agent from the OpenAkita Platform Agent Store. Downloads the .akita-agent package and runs the local installer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The ID of the Agent to install from the platform",
                },
                "force": {
                    "type": "boolean",
                    "description": "Force overwrite if local ID conflict (default: false)",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "publish_agent",
        "category": "Agent Hub",
        "description": "Publish a local Agent to the OpenAkita Platform Agent Store. The Agent is first exported as a .akita-agent package, then uploaded.",
        "input_schema": {
            "type": "object",
            "properties": {
                "profile_id": {
                    "type": "string",
                    "description": "The ID of the local Agent profile to publish",
                },
                "description": {
                    "type": "string",
                    "description": "Description for the platform listing",
                },
                "category": {
                    "type": "string",
                    "description": "Category for the listing",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for the listing",
                },
            },
            "required": ["profile_id"],
        },
    },
    {
        "name": "get_hub_agent_detail",
        "category": "Agent Hub",
        "description": "Get detailed information about a specific Agent on the OpenAkita Platform, including versions, ratings, and readme.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The ID of the Agent on the platform",
                },
            },
            "required": ["agent_id"],
        },
    },
]

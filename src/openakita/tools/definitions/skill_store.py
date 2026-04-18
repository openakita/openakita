"""
Skill Store Tool Definitions

Provides tools for interacting with the OpenAkita Platform Skill Store:
- search_store_skills: Search for Skills on the platform
- install_store_skill: Install a Skill from the platform to the local system
- get_store_skill_detail: View Skill details
- submit_skill_repo: Submit a GitHub repository for indexing
"""

SKILL_STORE_TOOLS = [
    {
        "name": "search_store_skills",
        "category": "Skill Store",
        "description": "Search for Skills on the OpenAkita Platform Skill Store. Returns a list of available Skills with trust level, install count, and rating.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword (empty = browse all)",
                },
                "category": {
                    "type": "string",
                    "description": "Filter by category",
                },
                "trust_level": {
                    "type": "string",
                    "enum": ["official", "certified", "community"],
                    "description": "Filter by trust level",
                },
                "sort": {
                    "type": "string",
                    "enum": ["installs", "rating", "newest", "stars"],
                    "description": "Sort order (default: installs)",
                },
                "page": {
                    "type": "integer",
                    "description": "Page number (default: 1)",
                },
            },
        },
    },
    {
        "name": "install_store_skill",
        "category": "Skill Store",
        "description": "Install a Skill from the OpenAkita Platform Skill Store to the local system. Uses git clone or direct download based on the skill source.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "The ID of the Skill to install from the platform",
                },
            },
            "required": ["skill_id"],
        },
    },
    {
        "name": "get_store_skill_detail",
        "category": "Skill Store",
        "description": "Get detailed information about a specific Skill on the OpenAkita Platform, including readme, trust level, and installation instructions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "The ID of the Skill on the platform",
                },
            },
            "required": ["skill_id"],
        },
    },
    {
        "name": "submit_skill_repo",
        "category": "Skill Store",
        "description": "Submit a GitHub repository to be indexed by the OpenAkita Skill Store. The platform will scan for SKILL.md files and create skill entries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_url": {
                    "type": "string",
                    "description": "GitHub repository URL (e.g. https://github.com/owner/repo)",
                },
            },
            "required": ["repo_url"],
        },
    },
]

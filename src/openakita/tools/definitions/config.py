"""
System configuration tool definitions

Unified system_config tool — users can view/modify all system configuration via chat.
Supports: viewing config, modifying settings, LLM endpoint management, UI preferences, dynamic config discovery.
"""

CONFIG_TOOLS = [
    {
        "name": "system_config",
        "category": "Config",
        "description": (
            "Unified system configuration tool. When user wants to: "
            "(1) view or change any system setting (log level, thinking mode, proxy, IM channel, etc.), "
            "(2) add/remove/test LLM endpoints, "
            "(3) switch UI theme or language, "
            "(4) discover what settings are available, "
            "(5) manage LLM providers (add/update/remove custom providers), "
            "(6) check external extension modules (opencli, cli-anything) status and install/upgrade commands. "
            "IMPORTANT: Before calling action=set, action=add_endpoint, or action=manage_provider with add/update/remove, "
            "ALWAYS use ask_user first to confirm the changes with the user. "
            "If unsure which config key to use, call action=discover first."
        ),
        "detail": """Unified system configuration tool covering all configuration operations.

## action reference

### discover -- Discover available configuration items
Lists all configurable items and their metadata (description, type, current value, default value).
Newly added configuration items appear automatically, no tool-code changes needed.
Filter a specific category via the category parameter.

### get -- View current configuration
Reads current configuration values; supports filtering by category or specific keys.
Sensitive fields (API keys, etc.) are automatically redacted.

### set -- Modify configuration
Updates the .env file and hot-reloads into memory.
- updates uses **uppercase environment variable names** as keys, e.g., {"LOG_LEVEL": "DEBUG"}
- Type validation is performed automatically
- Read-only fields (paths/database) are rejected
- Some fields require a restart to take effect; this is noted in the response

### add_endpoint -- Add an LLM endpoint
Auto-fills default base_url and api_type based on provider.
API keys are stored in .env; the JSON only references the environment variable name.
Hot-reloads automatically after adding.

### remove_endpoint -- Remove an LLM endpoint
Removes by name and hot-reloads.

### test_endpoint -- Test endpoint connectivity
Sends a lightweight request to verify API reachability, returning latency and status.

### set_ui -- Set UI preferences
Switches theme and language of the desktop client. Non-Desktop channels will be warned that this only affects the desktop client.

### manage_provider -- Manage LLM providers
Manages the LLM provider list (built-in + custom). Custom providers are stored in data/custom_providers.json in the workspace.
- operation=list: List all providers
- operation=add: Add a custom provider (provider fields required: slug, name, api_type, default_base_url)
- operation=update: Modify a provider's configuration (can override defaults of built-in providers)
- operation=remove: Remove a custom provider (built-in providers cannot be deleted, but custom overrides can be removed)

Provider rules:
- slug: Unique identifier; only lowercase letters, digits, hyphens, and underscores allowed
- api_type: Only "openai" or "anthropic" allowed
- default_base_url: Must start with http:// or https://
- registry_class: If omitted, OpenAIRegistry or AnthropicRegistry is chosen automatically based on api_type

### extensions -- External extension module management
View installation status, install/upgrade commands, and credits for optional external CLI tools.
These modules don't need to be bundled — advanced users install them manually, and OpenAkita detects and enables them automatically after install.
- operation=status: View install status and commands for all external modules
- operation=credits: View credit information

## Usage flow
1. Unsure which key to use -> run discover first
2. View current values -> get
3. Before modifying -> confirm with ask_user
4. After confirmation -> set / add_endpoint / remove_endpoint / manage_provider
""",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "discover",
                        "get",
                        "set",
                        "add_endpoint",
                        "remove_endpoint",
                        "test_endpoint",
                        "set_ui",
                        "manage_provider",
                        "extensions",
                    ],
                    "description": "Operation type",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Configuration category filter (optional for discover/get). "
                        "Common categories: Agent, LLM, Logging, Proxy, IM/Telegram, IM/Feishu, IM/Thinking push, "
                        "Session, Scheduled tasks, Persona, Liveliness, Desktop notifications, Embedding/Memory search, Speech recognition, etc. "
                        "Call discover without a category to see all categories."
                    ),
                },
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of configuration field names to query (optional for get, e.g., ['log_level', 'thinking_mode'])",
                },
                "updates": {
                    "type": "object",
                    "description": (
                        "Configuration key-value pairs to modify (required for set). "
                        'Keys use uppercase environment variable names, e.g., {"LOG_LEVEL": "DEBUG", "PROACTIVE_ENABLED": "true"}'
                    ),
                },
                "endpoint": {
                    "type": "object",
                    "description": (
                        "LLM endpoint configuration (required for add_endpoint). "
                        "Fields: name (required), provider (required), model (required), "
                        "api_key (optional, stored in .env), api_type (optional, auto-inferred), "
                        "base_url (optional, auto-filled), priority (optional, default 10), "
                        "max_tokens (optional), context_window (optional), timeout (optional), "
                        "capabilities (optional, e.g., ['text','tools','vision'])"
                    ),
                    "properties": {
                        "name": {"type": "string", "description": "Unique endpoint name"},
                        "provider": {
                            "type": "string",
                            "description": "Provider slug (e.g., openai, anthropic, deepseek, dashscope, ollama, etc.)",
                        },
                        "model": {"type": "string", "description": "Model name"},
                        "api_key": {
                            "type": "string",
                            "description": "API key (automatically stored in .env, not in JSON)",
                        },
                        "api_type": {
                            "type": "string",
                            "enum": ["openai", "anthropic"],
                            "description": "API protocol type (if omitted, inferred automatically based on provider)",
                        },
                        "base_url": {
                            "type": "string",
                            "description": "API URL (if omitted, auto-filled based on provider)",
                        },
                        "priority": {
                            "type": "integer",
                            "description": "Priority; smaller numbers have higher priority (default 10)",
                        },
                        "max_tokens": {"type": "integer", "description": "Maximum output tokens"},
                        "context_window": {"type": "integer", "description": "Context window size"},
                        "timeout": {"type": "integer", "description": "Request timeout (seconds)"},
                        "capabilities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Model capability list, e.g., ['text','tools','vision','thinking']",
                        },
                    },
                    "required": ["name", "provider", "model"],
                },
                "endpoint_name": {
                    "type": "string",
                    "description": "Endpoint name (required for remove_endpoint / test_endpoint)",
                },
                "target": {
                    "type": "string",
                    "enum": ["main", "compiler", "stt"],
                    "description": "Endpoint type (default main): main=main endpoint, compiler=Prompt compilation, stt=speech recognition",
                },
                "theme": {
                    "type": "string",
                    "enum": ["light", "dark", "system"],
                    "description": "UI theme (for set_ui)",
                },
                "language": {
                    "type": "string",
                    "enum": ["zh", "en"],
                    "description": "UI language (for set_ui)",
                },
                "operation": {
                    "type": "string",
                    "enum": ["list", "add", "update", "remove", "status", "credits"],
                    "description": (
                        "Operation sub-type. For manage_provider: list/add/update/remove; "
                        "for extensions: status/credits"
                    ),
                },
                "provider": {
                    "type": "object",
                    "description": (
                        "Provider configuration (required for manage_provider add/update). "
                        "For add, required: slug, name, api_type, default_base_url. "
                        "For update, required: slug (to locate); the rest are the fields to modify."
                    ),
                    "properties": {
                        "slug": {
                            "type": "string",
                            "description": "Unique provider identifier (lowercase letters, digits, hyphens)",
                        },
                        "name": {"type": "string", "description": "Display name"},
                        "api_type": {
                            "type": "string",
                            "enum": ["openai", "anthropic"],
                            "description": "API protocol type",
                        },
                        "default_base_url": {"type": "string", "description": "Default API URL"},
                        "api_key_env_suggestion": {
                            "type": "string",
                            "description": "Suggested environment variable name for the API key",
                        },
                        "supports_model_list": {
                            "type": "boolean",
                            "description": "Whether listing models is supported",
                        },
                        "requires_api_key": {"type": "boolean", "description": "Whether an API key is required"},
                        "is_local": {
                            "type": "boolean",
                            "description": "Whether this is a local service (e.g., Ollama)",
                        },
                        "coding_plan_base_url": {
                            "type": "string",
                            "description": "Dedicated API URL for Coding Plan",
                        },
                        "coding_plan_api_type": {
                            "type": "string",
                            "description": "Coding Plan protocol type",
                        },
                    },
                },
                "slug": {
                    "type": "string",
                    "description": "Provider slug (required for manage_provider remove)",
                },
            },
            "required": ["action"],
        },
        "triggers": [
            "User wants to view or change system settings",
            "User asks about available configuration options",
            "User wants to add, remove, or test LLM endpoints",
            "User wants to switch theme or language",
            "User wants to add, modify, or remove LLM providers",
            "User asks about external modules/extensions status, install, or upgrade",
            "User asks about opencli or cli-anything",
        ],
        "examples": [
            {
                "scenario": "View all configurable items",
                "params": {"action": "discover"},
            },
            {
                "scenario": "View Agent-related configuration",
                "params": {"action": "get", "category": "Agent"},
            },
            {
                "scenario": "Change log level",
                "params": {"action": "set", "updates": {"LOG_LEVEL": "DEBUG"}},
            },
            {
                "scenario": "Add a DeepSeek endpoint",
                "params": {
                    "action": "add_endpoint",
                    "endpoint": {
                        "name": "deepseek-chat",
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "api_key": "sk-xxx",
                    },
                },
            },
            {
                "scenario": "Switch to dark theme",
                "params": {"action": "set_ui", "theme": "dark"},
            },
            {
                "scenario": "List all LLM providers",
                "params": {"action": "manage_provider", "operation": "list"},
            },
            {
                "scenario": "Add a custom provider",
                "params": {
                    "action": "manage_provider",
                    "operation": "add",
                    "provider": {
                        "slug": "my-proxy",
                        "name": "My API Proxy",
                        "api_type": "openai",
                        "default_base_url": "https://my-proxy.example.com/v1",
                        "api_key_env_suggestion": "MY_PROXY_API_KEY",
                    },
                },
            },
            {
                "scenario": "View external extension module status",
                "params": {"action": "extensions", "operation": "status"},
            },
            {
                "scenario": "View external module credits",
                "params": {"action": "extensions", "operation": "credits"},
            },
        ],
    },
]

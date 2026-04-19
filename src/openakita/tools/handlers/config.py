"""
System configuration handler

Unifies all actions of the system_config tool:
- discover: Introspect Settings.model_fields to dynamically discover configurable items
- get: View current configuration
- set: Modify configuration (.env + hot reload)
- add_endpoint / remove_endpoint / test_endpoint: LLM endpoint management
- set_ui: UI preferences (theme/language)
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blocklist: fields that cannot be modified via chat
# ---------------------------------------------------------------------------
_READONLY_FIELDS = frozenset(
    {
        "project_root",
        "database_path",
        "session_storage_path",
        "log_dir",
        "log_file_prefix",
    }
)

# ---------------------------------------------------------------------------
# Fields that require a restart to take effect
# ---------------------------------------------------------------------------
_RESTART_REQUIRED_FIELDS = frozenset(
    {
        "telegram_enabled",
        "telegram_bot_token",
        "telegram_webhook_url",
        "telegram_pairing_code",
        "telegram_require_pairing",
        "telegram_proxy",
        "feishu_enabled",
        "feishu_app_id",
        "feishu_app_secret",
        "wework_enabled",
        "wework_corp_id",
        "wework_token",
        "wework_encoding_aes_key",
        "wework_callback_port",
        "wework_callback_host",
        "dingtalk_enabled",
        "dingtalk_client_id",
        "dingtalk_client_secret",
        "onebot_enabled",
        "onebot_ws_url",
        "onebot_access_token",
        "qqbot_enabled",
        "qqbot_app_id",
        "qqbot_app_secret",
        "qqbot_sandbox",
        "qqbot_mode",
        "qqbot_webhook_port",
        "qqbot_webhook_path",
        "wechat_enabled",
        "wechat_token",
        "orchestration_enabled",
        "orchestration_mode",
        "orchestration_bus_address",
        "orchestration_pub_address",
        "embedding_model",
        "embedding_device",
    }
)

# ---------------------------------------------------------------------------
# Pattern for sensitive fields
# ---------------------------------------------------------------------------
_SENSITIVE_PATTERN = re.compile(r"(api_key|secret|token|password)", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Category inference rules: (prefix/field-name tuple, category name)
# ---------------------------------------------------------------------------
_CATEGORY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("anthropic_", "default_model", "max_tokens"), "LLM"),
    (("dashscope_",), "LLM/DashScope"),
    (
        (
            "agent_name",
            "max_iterations",
            "force_tool_call",
            "tool_max_parallel",
            "allow_parallel",
            "selfcheck_",
        ),
        "Agent",
    ),
    (("thinking_",), "Agent/Thinking mode"),
    (("im_chain_push",), "IM/Chain-of-thought push"),
    (("progress_timeout", "hard_timeout"), "Agent/Timeouts"),
    (("log_",), "Logging"),
    (("whisper_",), "Speech recognition"),
    (("http_proxy", "https_proxy", "all_proxy", "force_ipv4"), "Proxy"),
    (("model_download_",), "Model download"),
    (("embedding_", "search_backend"), "Embedding/Memory search"),
    (("memory_",), "Memory"),
    (("github_",), "GitHub"),
    (("search_provider", "search_fallback_enabled", "brave_api_key", "tavily_api_key", "exa_api_key"), "Web Search"),
    (("telegram_",), "IM/Telegram"),
    (("feishu_",), "IM/Feishu"),
    (("wework_",), "IM/WeCom"),
    (("dingtalk_",), "IM/DingTalk"),
    (("onebot_",), "IM/OneBot"),
    (("qqbot_",), "IM/QQ"),
    (("wechat_",), "IM/WeChat"),
    (("session_",), "Session"),
    (("scheduler_",), "Scheduled tasks"),
    (("orchestration_",), "Multi-agent orchestration"),
    (("persona_",), "Persona"),
    (("proactive_",), "Proactive presence"),
    (("sticker_",), "Stickers"),
    (("desktop_notify_",), "Desktop notifications"),
    (("tracing_",), "Tracing"),
    (("evaluation_",), "Evaluation"),
    (("ui_",), "UI preferences"),
]


def _infer_category(field_name: str) -> str:
    """Infer the configuration category from the field name"""
    for patterns, category in _CATEGORY_RULES:
        for p in patterns:
            if field_name == p or field_name.startswith(p):
                return category
    return "Other"


def _get_field_category(field_name: str, field_info: Any) -> str:
    """Get the field category, preferring the json_schema_extra declaration"""
    extra = getattr(field_info, "json_schema_extra", None) or {}
    if isinstance(extra, dict) and "category" in extra:
        return extra["category"]
    return _infer_category(field_name)


def _is_sensitive(field_name: str) -> bool:
    return bool(_SENSITIVE_PATTERN.search(field_name))


def _needs_restart(field_name: str, field_info: Any) -> bool:
    extra = getattr(field_info, "json_schema_extra", None) or {}
    if isinstance(extra, dict) and extra.get("needs_restart"):
        return True
    return field_name in _RESTART_REQUIRED_FIELDS


def _mask_value(value: Any) -> str:
    """Mask sensitive values"""
    s = str(value)
    if len(s) > 6:
        return s[:4] + "***" + s[-2:]
    return "***"


def _unique_env_key(base: str, used: set[str]) -> str:
    """Return *base* if unused, otherwise append _2, _3, … until unique."""
    if not base or base not in used:
        return base
    for i in range(2, 100):
        candidate = f"{base}_{i}"
        if candidate not in used:
            return candidate
    return f"{base}_{int(__import__('time').time())}"


def _update_env_content(existing: str, entries: dict[str, str]) -> str:
    """Merge entries into existing .env content (preserves comments and order)"""
    lines = existing.splitlines()
    updated_keys: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in entries:
            value = entries[key]
            if value == "":
                updated_keys.add(key)
                continue
            new_lines.append(f"{key}={value}")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    for key, value in entries.items():
        if key not in updated_keys and value != "":
            new_lines.append(f"{key}={value}")

    return "\n".join(new_lines) + "\n"


def _check_cli_anything_path() -> str | None:
    """Return path of first cli-anything-* executable found, or None."""
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        try:
            if not os.path.isdir(d):
                continue
            for entry in os.listdir(d):
                if entry.lower().startswith("cli-anything-"):
                    return os.path.join(d, entry)
        except OSError:
            continue
    return None


class ConfigHandler:
    """System configuration handler"""

    TOOLS = ["system_config"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        action = params.get("action", "")
        try:
            if action == "discover":
                return self._discover(params)
            elif action == "get":
                return self._get_config(params)
            elif action == "set":
                return self._set_config(params)
            elif action == "add_endpoint":
                return self._add_endpoint(params)
            elif action == "remove_endpoint":
                return self._remove_endpoint(params)
            elif action == "test_endpoint":
                return await self._test_endpoint(params)
            elif action == "set_ui":
                return self._set_ui(params)
            elif action == "manage_provider":
                return self._manage_provider(params)
            elif action == "extensions":
                return self._extensions(params)
            else:
                return (
                    f"Unknown action: {action}. Supported: discover, get, set, "
                    "add_endpoint, remove_endpoint, test_endpoint, set_ui, "
                    "manage_provider, extensions"
                )
        except Exception as e:
            logger.error(f"[ConfigHandler] action={action} failed: {e}", exc_info=True)
            return f"Configuration operation failed: {type(e).__name__}: {e}"

    # ------------------------------------------------------------------
    # discover: Introspect Settings to dynamically discover configurable items
    # ------------------------------------------------------------------
    def _discover(self, params: dict) -> str:
        from ...config import Settings, settings

        category_filter = (params.get("category") or "").strip()

        grouped: dict[str, list[dict]] = {}
        for field_name, field_info in Settings.model_fields.items():
            if field_name in _READONLY_FIELDS:
                continue

            cat = _get_field_category(field_name, field_info)
            if category_filter and cat != category_filter:
                # Fuzzy match: user input "Agent" should also match "Agent/Thinking mode"
                if category_filter not in cat:
                    continue

            current_val = getattr(settings, field_name, None)
            default_val = field_info.default
            if hasattr(field_info, "default_factory") and field_info.default_factory:
                try:
                    default_val = field_info.default_factory()
                except Exception:
                    default_val = "(dynamic)"

            sensitive = _is_sensitive(field_name)
            display_current = (
                _mask_value(current_val) if sensitive and current_val else str(current_val)
            )
            display_default = str(default_val)

            annotation = field_info.annotation
            type_name = getattr(annotation, "__name__", str(annotation))

            entry = {
                "field": field_name,
                "env_name": field_name.upper(),
                "description": field_info.description or "",
                "type": type_name,
                "current": display_current,
                "default": display_default,
                "is_modified": current_val != default_val,
                "is_sensitive": sensitive,
                "needs_restart": _needs_restart(field_name, field_info),
            }

            grouped.setdefault(cat, []).append(entry)

        if not grouped:
            if category_filter:
                return f'No configuration items found for category "{category_filter}". Call action=discover without a category to view all categories.'
            return "No configurable items found."

        lines = [
            f"## Configurable items ({sum(len(v) for v in grouped.values())} total, {len(grouped)} categories)\n"
        ]
        for cat in sorted(grouped.keys()):
            items = grouped[cat]
            modified_count = sum(1 for it in items if it["is_modified"])
            lines.append(f"### {cat} ({len(items)} items, {modified_count} modified)")
            for it in items:
                mark = "**[modified]** " if it["is_modified"] else ""
                restart_mark = " ⚠️ restart required" if it["needs_restart"] else ""
                sensitive_mark = " 🔒" if it["is_sensitive"] else ""
                lines.append(
                    f"- `{it['env_name']}` ({it['type']}): {it['description']}"
                    f"{sensitive_mark}{restart_mark}"
                )
                lines.append(f"  current: {mark}{it['current']}  |  default: {it['default']}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # get: View current configuration
    # ------------------------------------------------------------------
    def _get_config(self, params: dict) -> str:
        from ...config import Settings, settings

        category_filter = (params.get("category") or "").strip()
        keys_filter = params.get("keys") or []

        parts: list[str] = []

        # If keys were specified, query them directly
        if keys_filter:
            parts.append("## Specified configuration items\n")
            for key in keys_filter:
                field_name = key.lower()
                if field_name not in Settings.model_fields:
                    parts.append(f"- `{key}`: ❌ does not exist")
                    continue
                val = getattr(settings, field_name, None)
                if _is_sensitive(field_name) and val:
                    val = _mask_value(val)
                field_info = Settings.model_fields[field_name]
                parts.append(f"- `{field_name.upper()}`: {val}  ({field_info.description or ''})")
            return "\n".join(parts)

        # Return configuration overview grouped by category
        grouped: dict[str, list[str]] = {}
        for field_name, field_info in Settings.model_fields.items():
            if field_name in _READONLY_FIELDS:
                continue
            cat = _get_field_category(field_name, field_info)
            if category_filter and category_filter not in cat:
                continue
            val = getattr(settings, field_name, None)
            if _is_sensitive(field_name) and val:
                val = _mask_value(val)
            grouped.setdefault(cat, []).append(f"- `{field_name.upper()}` = {val}")

        # Append LLM endpoint overview (when viewing the LLM category or no filter)
        if not category_filter or "LLM" in category_filter:
            ep_lines = self._format_endpoints_summary()
            if ep_lines:
                grouped.setdefault("LLM/Endpoints", []).extend(ep_lines)

        if not grouped:
            return "No matching configuration items found."

        parts.append(
            "## Current configuration" + (f" (category: {category_filter})" if category_filter else "") + "\n"
        )
        for cat in sorted(grouped.keys()):
            parts.append(f"### {cat}")
            parts.extend(grouped[cat])
            parts.append("")

        return "\n".join(parts)

    def _format_endpoints_summary(self) -> list[str]:
        """Format the LLM endpoint summary"""
        try:
            from ...llm.config import load_endpoints_config

            endpoints, compiler_eps, stt_eps, _ = load_endpoints_config()
        except Exception:
            return ["- ⚠️ Unable to read endpoint configuration"]

        lines = []
        for _i, ep in enumerate(endpoints, 1):
            key_info = ""
            if ep.api_key_env:
                has_key = bool(os.environ.get(ep.api_key_env))
                key_info = f" | Key: {'✅' if has_key else '❌'}{ep.api_key_env}"
            lines.append(
                f"- **{ep.name}** (P{ep.priority}): {ep.provider}/{ep.model}"
                f" | {ep.api_type}{key_info}"
            )

        if compiler_eps:
            lines.append(f"- Compiler endpoints: {len(compiler_eps)}")
        if stt_eps:
            lines.append(f"- STT endpoints: {len(stt_eps)}")
        if not endpoints:
            lines.append("- (no endpoints)")
        return lines

    # ------------------------------------------------------------------
    # set: Modify configuration
    # ------------------------------------------------------------------
    def _set_config(self, params: dict) -> str:
        from ...config import Settings, runtime_state, settings

        updates = params.get("updates")
        if not updates or not isinstance(updates, dict):
            return '❌ The updates parameter is missing or malformed; it should be a {"KEY": "value"} dict'

        # Project root directory
        project_root = Path(settings.project_root)
        env_path = project_root / ".env"

        changes: list[str] = []
        env_entries: dict[str, str] = {}
        restart_needed: list[str] = []
        errors: list[str] = []

        for env_key, new_value in updates.items():
            field_name = env_key.lower()

            # Blocklist check
            if field_name in _READONLY_FIELDS:
                errors.append(f"`{env_key}`: read-only field, cannot be modified")
                continue

            # Check whether the field exists
            if field_name not in Settings.model_fields:
                errors.append(f"`{env_key}`: unknown configuration item. Use action=discover to view configurable items")
                continue

            field_info = Settings.model_fields[field_name]

            # Type validation and conversion
            _, err = self._validate_value(field_name, field_info, new_value)
            if err:
                errors.append(f"`{env_key}`: {err}")
                continue

            old_value = getattr(settings, field_name, None)
            if _is_sensitive(field_name) and old_value:
                old_display = _mask_value(old_value)
            else:
                old_display = str(old_value)

            new_display = _mask_value(new_value) if _is_sensitive(field_name) else str(new_value)

            env_entries[env_key.upper()] = str(new_value)
            changes.append(f"- `{env_key.upper()}`: {old_display} → {new_display}")

            if _needs_restart(field_name, field_info):
                restart_needed.append(env_key.upper())

        if errors:
            error_lines = "\n".join(f"  {e}" for e in errors)
            if not changes:
                return f"❌ All changes were rejected:\n{error_lines}"

        # Write to .env
        if env_entries:
            existing = ""
            if env_path.exists():
                existing = env_path.read_text(encoding="utf-8", errors="replace")
            new_content = _update_env_content(existing, env_entries)
            env_path.write_text(new_content, encoding="utf-8")

            # Sync to os.environ
            for key, value in env_entries.items():
                if value:
                    os.environ[key] = value
                elif key in os.environ:
                    del os.environ[key]

            # Hot-reload settings (reload already skips _PERSISTABLE_KEYS fields)
            changed_fields = settings.reload()
            logger.info(
                f"[ConfigHandler] set: updated {len(env_entries)} entries, reloaded fields: {changed_fields}"
            )

            # Double-safety: restore runtime-persistable fields (in case an older reload or error path overwrote them)
            try:
                runtime_state.load()
            except Exception as e:
                logger.warning(f"[ConfigHandler] runtime_state.load failed: {e}")

            # Persist runtime_state (if persistable fields were changed)
            try:
                from ...config import _PERSISTABLE_KEYS

                if any(k.lower() in _PERSISTABLE_KEYS for k in env_entries):
                    runtime_state.save()
            except Exception as e:
                logger.warning(f"[ConfigHandler] runtime_state save failed: {e}")

            # Reset the router singleton when search provider config changes (hot reload)
            _SEARCH_FIELDS = {
                "search_provider", "search_fallback_enabled",
                "brave_api_key", "tavily_api_key", "exa_api_key",
            }
            if any(k.lower() in _SEARCH_FIELDS for k in env_entries):
                try:
                    from openakita.tools.handlers.search_providers import reset_router
                    reset_router()
                    logger.info("[ConfigHandler] Search provider router reset after config change")
                except Exception as e:
                    logger.warning(f"[ConfigHandler] search router reset failed: {e}")

        # Build response
        result_lines = ["✅ Configuration updated:\n"] + changes

        if errors:
            result_lines.append("\n⚠️ Some fields were rejected:")
            result_lines.extend(f"  {e}" for e in errors)

        if restart_needed:
            result_lines.append(f"\n⚠️ The following fields require a service restart to take effect: {', '.join(restart_needed)}")

        return "\n".join(result_lines)

    _INT_CONSTRAINTS: dict[str, tuple[int | None, int | None, str]] = {
        "max_iterations": (15, 10000, "Max iterations must be in 15~10000; recommended 100~300"),
        "progress_timeout_seconds": (60, None, "No-progress timeout must be at least 60 seconds"),
        "tool_max_parallel": (1, 32, "Parallel tool count must be in 1~32"),
    }

    def _check_int_constraints(self, field_name: str, value: int) -> str | None:
        spec = self._INT_CONSTRAINTS.get(field_name)
        if not spec:
            return None
        lo, hi, msg = spec
        if lo is not None and value < lo:
            return f"Value {value} is too small. {msg}"
        if hi is not None and value > hi:
            return f"Value {value} is too large. {msg}"
        return None

    def _validate_value(
        self, field_name: str, field_info: Any, value: Any
    ) -> tuple[Any, str | None]:
        """Validate the type and legality of a configuration value. Returns (validated_value, error_or_None)"""
        annotation = field_info.annotation

        # Handle str
        if annotation is str:
            return str(value), None

        # Handle int
        if annotation is int:
            try:
                v = int(value)
            except (ValueError, TypeError):
                return None, f"Expected integer, got: {value}"
            constraint_err = self._check_int_constraints(field_name, v)
            if constraint_err:
                return None, constraint_err
            return v, None

        # Handle bool
        if annotation is bool:
            if isinstance(value, bool):
                return value, None
            s = str(value).lower()
            if s in ("true", "1", "yes", "on"):
                return True, None
            elif s in ("false", "0", "no", "off"):
                return False, None
            return None, f"Expected boolean (true/false), got: {value}"

        # Handle list (e.g. thinking_keywords)
        if hasattr(annotation, "__origin__") and annotation.__origin__ is list:
            if isinstance(value, list):
                return value, None
            return None, f"Expected list, got: {type(value).__name__}"

        # Handle Path
        if annotation is Path:
            return None, "Path types cannot be modified via chat"

        return str(value), None

    # ------------------------------------------------------------------
    # add_endpoint: Add an LLM endpoint
    # ------------------------------------------------------------------
    def _add_endpoint(self, params: dict) -> str:
        endpoint_data = params.get("endpoint")
        if not endpoint_data or not isinstance(endpoint_data, dict):
            return "❌ Missing endpoint parameter"

        name = endpoint_data.get("name", "").strip()
        provider = endpoint_data.get("provider", "").strip()
        model = endpoint_data.get("model", "").strip()
        if not name or not provider or not model:
            return "❌ endpoint must include name, provider, and model"

        target = (params.get("target") or "main").strip()

        api_type = endpoint_data.get("api_type", "")
        base_url = endpoint_data.get("base_url", "")

        if not api_type or not base_url:
            defaults = self._get_provider_defaults(provider)
            if defaults:
                if not api_type:
                    api_type = defaults.get("api_type", "openai")
                if not base_url:
                    base_url = defaults.get("base_url", "")

        if not api_type:
            api_type = "openai"
        if not base_url:
            return f"❌ Cannot infer the API URL for {provider}; please provide base_url manually"

        api_key = endpoint_data.get("api_key", "").strip()

        endpoint_type_map = {"compiler": "compiler_endpoints", "stt": "stt_endpoints"}
        endpoint_type = endpoint_type_map.get(target, "endpoints")

        ep_dict = {
            "name": name,
            "provider": provider,
            "api_type": api_type,
            "base_url": base_url,
            "model": model,
            "priority": int(endpoint_data.get("priority", 10)),
            "max_tokens": int(endpoint_data.get("max_tokens", 0)),
            "context_window": int(endpoint_data.get("context_window", 200000)),
            "timeout": int(endpoint_data.get("timeout", 180)),
        }
        if endpoint_data.get("capabilities"):
            ep_dict["capabilities"] = endpoint_data["capabilities"]
        if endpoint_data.get("api_key_env"):
            ep_dict["api_key_env"] = endpoint_data["api_key_env"]

        from ...config import settings
        from ...llm.endpoint_manager import EndpointManager

        mgr = EndpointManager(Path(settings.project_root))
        try:
            result = mgr.save_endpoint(
                endpoint=ep_dict,
                api_key=api_key or None,
                endpoint_type=endpoint_type,
            )
        except ValueError as e:
            return f"❌ {e}"

        reload_info = self._reload_llm_client()

        api_key_env = result.get("api_key_env", "")
        key_info = f"API key stored in .env ({api_key_env})" if api_key_env else "No API key configured"
        return (
            f"✅ LLM endpoint added:\n"
            f"- Name: {name}\n"
            f"- Provider: {provider} | Protocol: {api_type}\n"
            f"- API URL: {base_url}\n"
            f"- Model: {model} | Priority: {ep_dict['priority']}\n"
            f"- {key_info}\n"
            f"- Target: {target}\n"
            f"- {reload_info}"
        )

    # ------------------------------------------------------------------
    # remove_endpoint: Delete an endpoint
    # ------------------------------------------------------------------
    def _remove_endpoint(self, params: dict) -> str:
        endpoint_name = (params.get("endpoint_name") or "").strip()
        if not endpoint_name:
            return "❌ Missing endpoint_name parameter"

        target = (params.get("target") or "main").strip()

        endpoint_type_map = {"compiler": "compiler_endpoints", "stt": "stt_endpoints"}
        endpoint_type = endpoint_type_map.get(target, "endpoints")

        from ...config import settings
        from ...llm.endpoint_manager import EndpointManager

        mgr = EndpointManager(Path(settings.project_root))
        removed = mgr.delete_endpoint(endpoint_name, endpoint_type=endpoint_type)

        if removed is None:
            all_eps = mgr.list_endpoints(endpoint_type)
            available = ", ".join(e.get("name", "") for e in all_eps) or "(none)"
            return f'❌ Endpoint "{endpoint_name}" not found. Current {target} endpoints: {available}'

        reload_info = self._reload_llm_client()
        return f'✅ Endpoint "{endpoint_name}" ({target}) removed. {reload_info}'

    # ------------------------------------------------------------------
    # test_endpoint: Test connectivity
    # ------------------------------------------------------------------
    async def _test_endpoint(self, params: dict) -> str:
        endpoint_name = (params.get("endpoint_name") or "").strip()
        if not endpoint_name:
            return "❌ Missing endpoint_name parameter"

        from ...llm.config import load_endpoints_config

        endpoints, compiler_eps, stt_eps, _ = load_endpoints_config()
        all_eps = endpoints + compiler_eps + stt_eps

        target_ep = None
        for ep in all_eps:
            if ep.name == endpoint_name:
                target_ep = ep
                break

        if not target_ep:
            available = ", ".join(ep.name for ep in all_eps) or "(none)"
            return f'❌ Endpoint "{endpoint_name}" not found. Available endpoints: {available}'

        api_key = target_ep.get_api_key()
        if not api_key:
            return (
                f'❌ Endpoint "{endpoint_name}" has no API key configured.\n'
                f"Please set the environment variable {target_ep.api_key_env or '(unspecified)'} or supply api_key in the endpoint configuration."
            )

        import httpx

        # Attempt a list-models request
        from openakita.llm.types import normalize_base_url

        headers = {"Authorization": f"Bearer {api_key}"}
        _base = normalize_base_url(target_ep.base_url)
        if target_ep.api_type == "anthropic":
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
            test_url = _base + "/v1/models"
        else:
            test_url = _base + "/models"

        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(test_url, headers=headers)
                elapsed_ms = int((time.time() - t0) * 1000)

                if resp.status_code < 400:
                    return (
                        f'✅ Endpoint "{endpoint_name}" is reachable\n'
                        f"- Status code: {resp.status_code}\n"
                        f"- Latency: {elapsed_ms}ms\n"
                        f"- Provider: {target_ep.provider} | Model: {target_ep.model}"
                    )
                else:
                    body_preview = (resp.text or "")[:300]
                    return (
                        f'⚠️ Endpoint "{endpoint_name}" returned an error\n'
                        f"- Status code: {resp.status_code}\n"
                        f"- Latency: {elapsed_ms}ms\n"
                        f"- Response: {body_preview}"
                    )
        except httpx.ConnectError as e:
            return f'❌ Endpoint "{endpoint_name}" connection failed: unable to reach {target_ep.base_url}\n{e}'
        except httpx.TimeoutException:
            return f'❌ Endpoint "{endpoint_name}" request timed out (15s)'
        except Exception as e:
            return f'❌ Endpoint "{endpoint_name}" test failed: {type(e).__name__}: {e}'

    # ------------------------------------------------------------------
    # set_ui: Set UI preferences
    # ------------------------------------------------------------------
    def _set_ui(self, params: dict) -> str:
        from ...config import runtime_state, settings

        theme = (params.get("theme") or "").strip()
        language = (params.get("language") or "").strip()

        if not theme and not language:
            return "❌ Please specify a theme or language parameter"

        changes: list[str] = []
        ui_pref: dict[str, str] = {}

        if theme:
            if theme not in ("light", "dark", "system"):
                return f"❌ theme only supports light/dark/system, got: {theme}"
            settings.ui_theme = theme
            ui_pref["theme"] = theme
            changes.append(f"- Theme: {theme}")

        if language:
            if language not in ("zh", "en"):
                return f"❌ language only supports zh/en, got: {language}"
            settings.ui_language = language
            ui_pref["language"] = language
            changes.append(f"- Language: {language}")

        runtime_state.save()

        result = {
            "ok": True,
            "message": "✅ UI preferences updated:\n" + "\n".join(changes),
            "ui_preference": ui_pref,
        }

        # Check current channel
        session = getattr(self.agent, "_current_session", None)
        channel = getattr(session, "channel", None) if session else None
        if channel and channel != "desktop":
            result["message"] += "\n\nNote: this setting only affects the desktop client (Desktop); the current channel is " + channel

        return json.dumps(result, ensure_ascii=False)

    # ------------------------------------------------------------------
    # manage_provider: Manage LLM providers
    # ------------------------------------------------------------------

    _PROVIDER_REQUIRED_FIELDS = ("slug", "name", "api_type", "default_base_url")
    _PROVIDER_VALID_API_TYPES = ("openai", "anthropic")
    _PROVIDER_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

    def _manage_provider(self, params: dict) -> str:
        operation = (params.get("operation") or "").strip()

        if operation == "list":
            return self._list_providers_info()
        elif operation == "add":
            return self._add_custom_provider(params.get("provider") or {})
        elif operation == "update":
            return self._update_custom_provider(params.get("provider") or {})
        elif operation == "remove":
            slug = (params.get("slug") or "").strip()
            return self._remove_custom_provider(slug)
        else:
            return (
                "❌ manage_provider requires the operation parameter.\n"
                "Supported: list (list all providers), add (add custom provider), "
                "update (modify custom provider), remove (delete custom provider)"
            )

    def _list_providers_info(self) -> str:
        from ...llm.registries import list_providers, load_custom_providers

        all_providers = list_providers()
        custom_slugs = {e.get("slug") for e in load_custom_providers()}

        lines = [f"## LLM provider list ({len(all_providers)} total)\n"]
        for p in all_providers:
            tag = " [custom]" if p.slug in custom_slugs else ""
            local_tag = " [local]" if p.is_local else ""
            lines.append(
                f"- **{p.name}**{tag}{local_tag}\n"
                f"  slug: `{p.slug}` | Protocol: {p.api_type} | URL: {p.default_base_url}"
            )
        lines.append(
            "\nCustom providers file: data/custom_providers.json\n"
            "Use operation=add to add a new provider, and operation=update to modify an existing one."
        )
        return "\n".join(lines)

    def _validate_provider_entry(self, entry: dict) -> str | None:
        """Validate a provider entry; returns an error message or None"""
        for field in self._PROVIDER_REQUIRED_FIELDS:
            if not (entry.get(field) or "").strip():
                return f"Missing required field: {field}"

        slug = entry["slug"].strip()
        if not self._PROVIDER_SLUG_PATTERN.match(slug):
            return (
                f"Invalid slug format: '{slug}' (only lowercase letters, digits, hyphens, and underscores are allowed, and it cannot start with a symbol)"
            )

        api_type = entry["api_type"].strip()
        if api_type not in self._PROVIDER_VALID_API_TYPES:
            return f"Invalid api_type: '{api_type}' (only openai or anthropic are allowed)"

        base_url = entry["default_base_url"].strip()
        if not base_url.startswith(("http://", "https://")):
            return "default_base_url must start with http:// or https://"

        return None

    def _add_custom_provider(self, provider_data: dict) -> str:
        if not provider_data or not isinstance(provider_data, dict):
            return "❌ Missing provider parameter (must include slug, name, api_type, default_base_url)"

        err = self._validate_provider_entry(provider_data)
        if err:
            return f"❌ {err}"

        from ...llm.registries import (
            list_providers,
            load_custom_providers,
            reload_registries,
            save_custom_providers,
        )

        slug = provider_data["slug"].strip()

        existing_slugs = {p.slug for p in list_providers()}
        if slug in existing_slugs:
            return (
                f"❌ slug '{slug}' already exists. To modify, use operation=update; "
                f"to override the default config of a built-in provider, also use operation=update."
            )

        entry = {
            "slug": slug,
            "name": provider_data["name"].strip(),
            "api_type": provider_data["api_type"].strip(),
            "default_base_url": provider_data["default_base_url"].strip(),
            "api_key_env_suggestion": (provider_data.get("api_key_env_suggestion") or "").strip(),
            "supports_model_list": provider_data.get("supports_model_list", True),
            "supports_capability_api": provider_data.get("supports_capability_api", False),
            "registry_class": provider_data.get("registry_class")
            or (
                "AnthropicRegistry"
                if provider_data["api_type"].strip() == "anthropic"
                else "OpenAIRegistry"
            ),
            "requires_api_key": provider_data.get("requires_api_key", True),
            "is_local": provider_data.get("is_local", False),
        }
        if provider_data.get("coding_plan_base_url"):
            entry["coding_plan_base_url"] = provider_data["coding_plan_base_url"].strip()
        if provider_data.get("coding_plan_api_type"):
            entry["coding_plan_api_type"] = provider_data["coding_plan_api_type"].strip()

        custom = load_custom_providers()
        custom.append(entry)
        save_custom_providers(custom)
        count = reload_registries()

        return (
            f"✅ Custom provider added:\n"
            f"- Name: {entry['name']}\n"
            f"- slug: {slug}\n"
            f"- Protocol: {entry['api_type']} | URL: {entry['default_base_url']}\n"
            f"- Total providers: {count}\n"
            f"- Saved to: data/custom_providers.json"
        )

    def _update_custom_provider(self, provider_data: dict) -> str:
        if not provider_data or not isinstance(provider_data, dict):
            return "❌ Missing provider parameter"

        slug = (provider_data.get("slug") or "").strip()
        if not slug:
            return "❌ Missing slug field, which is required to locate the provider to modify"

        from ...llm.registries import (
            load_custom_providers,
            reload_registries,
            save_custom_providers,
        )

        if "api_type" in provider_data:
            api_type = provider_data["api_type"].strip()
            if api_type not in self._PROVIDER_VALID_API_TYPES:
                return f"❌ Invalid api_type: '{api_type}'"

        if "default_base_url" in provider_data:
            url = provider_data["default_base_url"].strip()
            if not url.startswith(("http://", "https://")):
                return "❌ default_base_url must start with http:// or https://"

        custom = load_custom_providers()
        found = False
        for i, entry in enumerate(custom):
            if entry.get("slug") == slug:
                for k, v in provider_data.items():
                    if k == "slug":
                        continue
                    custom[i][k] = v.strip() if isinstance(v, str) else v
                found = True
                break

        if not found:
            new_entry = {"slug": slug}
            for k, v in provider_data.items():
                if k == "slug":
                    continue
                new_entry[k] = v.strip() if isinstance(v, str) else v
            if not new_entry.get("registry_class"):
                api_type = new_entry.get("api_type", "openai")
                new_entry["registry_class"] = (
                    "AnthropicRegistry" if api_type == "anthropic" else "OpenAIRegistry"
                )
            custom.append(new_entry)

        save_custom_providers(custom)
        count = reload_registries()

        action = "updated" if found else "added (overriding built-in config)"
        return (
            f"✅ Provider '{slug}' {action}:\n"
            f"- Updated fields: {', '.join(k for k in provider_data if k != 'slug')}\n"
            f"- Total providers: {count}"
        )

    def _remove_custom_provider(self, slug: str) -> str:
        if not slug:
            return "❌ Missing slug parameter"

        from ...llm.registries import (
            _BUILTIN_ENTRIES,
            load_custom_providers,
            reload_registries,
            save_custom_providers,
        )

        builtin_slugs = {e["slug"] for e in _BUILTIN_ENTRIES}
        if slug in builtin_slugs:
            custom = load_custom_providers()
            had_override = any(e.get("slug") == slug for e in custom)
            if had_override:
                custom = [e for e in custom if e.get("slug") != slug]
                save_custom_providers(custom)
                reload_registries()
                return f"✅ Removed the custom override for built-in provider '{slug}'; reverted to the built-in default configuration"
            return f"❌ '{slug}' is a built-in provider and cannot be deleted. To modify its configuration, use operation=update"

        custom = load_custom_providers()
        original_len = len(custom)
        custom = [e for e in custom if e.get("slug") != slug]

        if len(custom) == original_len:
            return f"❌ Custom provider '{slug}' not found"

        save_custom_providers(custom)
        count = reload_registries()
        return f"✅ Custom provider '{slug}' deleted. Total providers: {count}"

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    def _get_provider_defaults(self, provider_slug: str) -> dict | None:
        """Get default configuration from the provider registry"""
        try:
            from ...llm.registries import list_providers

            for p in list_providers():
                if p.slug == provider_slug:
                    return {
                        "api_type": p.api_type,
                        "base_url": p.default_base_url,
                        "api_key_env": p.api_key_env_suggestion,
                        "requires_api_key": p.requires_api_key,
                    }
        except Exception as e:
            logger.warning(f"[ConfigHandler] Failed to load provider registry: {e}")
        return None

    # ------------------------------------------------------------------
    # extensions: External extension module management
    # ------------------------------------------------------------------

    _EXTENSIONS = [
        {
            "id": "opencli",
            "name": "OpenCLI",
            "description": "Turn websites and Electron apps into CLI commands, reusing Chrome's login state",
            "category": "Web",
            "check": lambda: __import__("shutil").which("opencli"),
            "install": "npm install -g opencli",
            "upgrade": "npm update -g opencli",
            "setup": "opencli setup",
            "homepage": "https://github.com/anthropics/opencli",
            "license": "MIT",
            "thanks": "Anthropic / Jack Wener",
        },
        {
            "id": "cli-anything",
            "name": "CLI-Anything",
            "description": "Auto-generate CLI interfaces for desktop software (GIMP, Blender, LibreOffice, etc.)",
            "category": "Desktop",
            "check": lambda: _check_cli_anything_path(),
            "install": "pip install cli-anything-gimp  # Replace with the target app as needed",
            "upgrade": "pip install --upgrade cli-anything-<app>",
            "setup": None,
            "homepage": "https://github.com/HKUDS/CLI-Anything",
            "license": "MIT",
            "thanks": "HKU Data Science Lab (HKUDS)",
        },
    ]

    def _extensions(self, params: dict) -> str:
        operation = (params.get("operation") or "status").strip()

        if operation == "status":
            return self._ext_status()
        elif operation == "credits":
            return self._ext_credits()
        else:
            return (
                "❌ extensions supports the following operations:\n"
                "- `status`: View the status and install/upgrade commands for all external extension modules\n"
                "- `credits`: View acknowledgements"
            )

    def _ext_status(self) -> str:
        lines = ["## External extension modules\n"]
        lines.append(
            "The modules below are optional external tools. Once installed, OpenAkita detects and enables them automatically.\n"
            "No restart is required; they take effect in the next conversation.\n"
        )

        for ext in self._EXTENSIONS:
            path = ext["check"]()
            installed = path is not None
            icon = "✅" if installed else "⬜"
            lines.append(f"### {icon} {ext['name']} ({ext['category']})")
            lines.append(f"{ext['description']}")
            lines.append(
                f"- Status: {'**installed**' if installed else 'not installed'}"
                + (f" (`{path}`)" if installed else "")
            )
            lines.append(f"- Install: `{ext['install']}`")
            lines.append(f"- Upgrade: `{ext['upgrade']}`")
            if ext.get("setup"):
                lines.append(f"- First-time setup: `{ext['setup']}`")
            lines.append(f"- Homepage: {ext['homepage']}")
            lines.append("")

        lines.append("---")
        lines.append("*No OpenAkita configuration changes are needed after installation; the system detects PATH at startup.*")
        return "\n".join(lines)

    def _ext_credits(self) -> str:
        lines = ["## Acknowledgements — External extension modules\n"]
        lines.append("OpenAkita's tool-calling and browser-access capabilities benefit from the following open-source projects:\n")

        for ext in self._EXTENSIONS:
            lines.append(f"### {ext['name']}")
            lines.append(f"- {ext['description']}")
            lines.append(f"- Author: **{ext['thanks']}**")
            lines.append(f"- License: {ext['license']}")
            lines.append(f"- Project: {ext['homepage']}")
            lines.append("")

        lines.append(
            "Thanks to the contributors of these projects for enabling AI agents to interact more reliably with real-world websites and desktop software."
        )
        return "\n".join(lines)

    def _reload_llm_client(self) -> str:
        """Hot-reload the LLM client; returns a result description"""
        brain = getattr(self.agent, "brain", None)
        llm_client = getattr(brain, "_llm_client", None) if brain else None
        if llm_client is None:
            return "⚠️ LLM client not found; please restart the service manually"

        try:
            success = llm_client.reload()
            if success:
                count = len(llm_client.endpoints)
                return f"Hot-reloaded ({count} endpoints now active)"
            return "⚠️ Hot reload returned false"
        except Exception as e:
            return f"⚠️ Hot reload failed: {e}"


def create_handler(agent: "Agent"):
    """Create the configuration handler"""
    handler = ConfigHandler(agent)
    return handler.handle

"""Configuration lifecycle policy for runtime updates.

The configuration API must not infer runtime behaviour from broad environment
variable prefixes.  This module provides one authoritative policy keyed by
``Settings`` field names, with explicit exceptions for startup infrastructure
and legacy environment variables that are not represented by ``Settings``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class ConfigApplyMode(StrEnum):
    """When a persisted configuration change becomes effective."""

    IMMEDIATE = "immediate"
    NEXT_TASK = "next_task"
    COMPONENT_RELOAD = "component_reload"
    PROCESS_RESTART = "process_restart"


# These fields are consumed while process-level infrastructure is constructed.
# Mutating the Settings singleton cannot change the already-bound socket,
# storage root, logging handlers, or channel adapter topology.
_PROCESS_RESTART_FIELDS = frozenset(
    {
        "api_host",
        "api_port",
        "api_lan_mode",
        "api_token",
        "project_root",
        "database_path",
        "session_storage_path",
        "log_level",
        "log_dir",
        "log_file_prefix",
        "log_max_size_mb",
        "log_backup_count",
        "log_retention_days",
        "log_format",
        "log_to_console",
        "log_to_file",
        "runtime_v2_enabled",
        "orgs_v2_backend",
        "multi_agent_enabled",
    }
)

_CHANNEL_FIELDS = frozenset(
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
        "wework_ws_enabled",
        "wework_ws_bot_id",
        "wework_ws_secret",
        "wework_ws_thinking_indicator",
        "wework_ws_msg_item_images",
        "wework_ws_webhook_url",
        "dingtalk_enabled",
        "dingtalk_client_id",
        "dingtalk_client_secret",
        "onebot_enabled",
        "onebot_mode",
        "onebot_ws_url",
        "onebot_reverse_host",
        "onebot_reverse_port",
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
        "im_bots",
    }
)

# These values are presentation-only or are read directly at the point of use.
_IMMEDIATE_FIELDS = frozenset(
    {
        "ui_theme",
        "ui_language",
        "desktop_notify_enabled",
        "desktop_notify_sound",
        "web_search_provider",
        "bocha_api_key",
        "tavily_api_key",
        "jina_api_key",
        "keenable_api_key",
        "searxng_base_url",
    }
)

# Supported UI integrations that intentionally remain ordinary environment
# variables instead of Settings fields. Their clients are owned by an Agent,
# so invalidating the pool applies them at the next task boundary.
_NEXT_TASK_ENV_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "EXA_API_KEY",
        "QUERIT_API_KEY",
        "ZHIPU_SEARCH_API_KEY",
        "API_TOOLS_SCHEMA_BUDGET_TOKENS",
    }
)

_COMPONENT_ENV_KEYS = {
    "desktop": frozenset(
        {
            "DESKTOP_ENABLED",
            "DESKTOP_DEFAULT_MONITOR",
            "DESKTOP_COMPRESSION_QUALITY",
            "DESKTOP_MAX_WIDTH",
            "DESKTOP_MAX_HEIGHT",
            "DESKTOP_CACHE_TTL",
            "DESKTOP_UIA_TIMEOUT",
            "DESKTOP_UIA_RETRY_INTERVAL",
            "DESKTOP_UIA_MAX_RETRIES",
            "DESKTOP_VISION_ENABLED",
            "DESKTOP_VISION_MAX_RETRIES",
            "DESKTOP_VISION_TIMEOUT",
            "DESKTOP_CLICK_DELAY",
            "DESKTOP_TYPE_INTERVAL",
            "DESKTOP_MOVE_DURATION",
            "DESKTOP_FAILSAFE",
            "DESKTOP_PAUSE",
        }
    )
}

# These keys are deleted from legacy .env storage by the Setup Center and are
# persisted through their own runtime-aware APIs.
_IMMEDIATE_ENV_KEYS = frozenset(
    {
        "BACKUP_ENABLED",
        "BACKUP_PATH",
        "BACKUP_CRON",
        "BACKUP_MAX_BACKUPS",
        "BACKUP_INCLUDE_USERDATA",
        "BACKUP_INCLUDE_MEDIA",
    }
)


@dataclass(frozen=True)
class ConfigChangePlan:
    """Classified result for a set of env-style configuration keys."""

    modes: dict[str, ConfigApplyMode]

    @property
    def restart_required(self) -> bool:
        return ConfigApplyMode.PROCESS_RESTART in self.modes.values()

    @property
    def hot_reloadable(self) -> bool:
        return all(
            mode
            in {
                ConfigApplyMode.IMMEDIATE,
                ConfigApplyMode.NEXT_TASK,
                ConfigApplyMode.COMPONENT_RELOAD,
            }
            for mode in self.modes.values()
        )

    @property
    def apply_mode(self) -> ConfigApplyMode:
        if not self.modes:
            return ConfigApplyMode.IMMEDIATE
        precedence = (
            ConfigApplyMode.PROCESS_RESTART,
            ConfigApplyMode.COMPONENT_RELOAD,
            ConfigApplyMode.NEXT_TASK,
            ConfigApplyMode.IMMEDIATE,
        )
        return next(mode for mode in precedence if mode in self.modes.values())

    @property
    def next_task_keys(self) -> set[str]:
        return {key for key, mode in self.modes.items() if mode is ConfigApplyMode.NEXT_TASK}

    @property
    def component_reload_keys(self) -> set[str]:
        return {
            key for key, mode in self.modes.items() if mode is ConfigApplyMode.COMPONENT_RELOAD
        }


def _settings_field_for_env_key(key: str) -> str | None:
    from openakita.config import Settings

    field_name = key.lower()
    return field_name if field_name in Settings.model_fields else None


def classify_config_key(key: str) -> ConfigApplyMode:
    """Return the runtime lifecycle for one exact env-style key."""

    normalized = key.upper()
    field_name = _settings_field_for_env_key(normalized)
    if field_name in _PROCESS_RESTART_FIELDS or field_name in _CHANNEL_FIELDS:
        return ConfigApplyMode.PROCESS_RESTART
    if field_name in _IMMEDIATE_FIELDS:
        return ConfigApplyMode.IMMEDIATE
    if normalized in _IMMEDIATE_ENV_KEYS:
        return ConfigApplyMode.IMMEDIATE
    if any(normalized in keys for keys in _COMPONENT_ENV_KEYS.values()):
        return ConfigApplyMode.COMPONENT_RELOAD
    if field_name is not None or normalized in _NEXT_TASK_ENV_KEYS:
        return ConfigApplyMode.NEXT_TASK

    # Unknown environment variables may be consumed by import-time third-party
    # integrations. Conservatively require restart instead of claiming a hot
    # reload that the runtime cannot guarantee.
    return ConfigApplyMode.PROCESS_RESTART


def classify_config_changes(keys: Iterable[str]) -> ConfigChangePlan:
    """Build a stable per-key lifecycle plan for a configuration update."""

    modes = {key.upper(): classify_config_key(key) for key in sorted(set(keys))}
    return ConfigChangePlan(modes=modes)


def effective_config_values(keys: Iterable[str]) -> dict[str, str]:
    """Return effective runtime values for requested Settings-backed keys."""

    from openakita.config import settings

    dumped_settings = settings.model_dump(mode="json")
    values: dict[str, str] = {}
    for key in sorted(set(keys)):
        normalized = key.upper()
        field_name = _settings_field_for_env_key(normalized)
        if field_name is not None:
            value = dumped_settings[field_name]
            if isinstance(value, bool):
                values[normalized] = "true" if value else "false"
            elif isinstance(value, (dict, list)):
                values[normalized] = json.dumps(value, ensure_ascii=False)
            else:
                values[normalized] = "" if value is None else str(value)
        elif normalized in os.environ:
            values[normalized] = os.environ[normalized]
    return values


def reload_config_components(plan: ConfigChangePlan) -> dict[str, str]:
    """Reload affected component caches and report per-component status."""

    changed = plan.component_reload_keys
    results: dict[str, str] = {}
    for component, keys in _COMPONENT_ENV_KEYS.items():
        if changed.isdisjoint(keys):
            continue
        try:
            if component == "desktop":
                desktop_config = sys.modules.get("openakita.tools.desktop.config")
                if desktop_config is not None:
                    desktop_config.reset_config()
            results[component] = "reloaded"
        except Exception:
            logger.exception("Failed to reload configuration component: %s", component)
            results[component] = "failed"
    return results

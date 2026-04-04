"""Plugin state persistence — tracks enabled/disabled, active backends, errors."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PluginStateEntry:
    plugin_id: str
    enabled: bool = True
    granted_permissions: list[str] = field(default_factory=list)
    installed_at: float = 0.0
    disabled_reason: str = ""
    error_count: int = 0
    last_error: str = ""
    last_error_time: float = 0.0


_SCHEMA_VERSION = 2


@dataclass
class PluginState:
    """Persistent plugin state, stored in data/plugin_state.json."""

    schema_version: int = _SCHEMA_VERSION
    plugins: dict[str, PluginStateEntry] = field(default_factory=dict)
    active_backends: dict[str, str] = field(default_factory=dict)  # reserved for future memory/search backend switching

    def get_entry(self, plugin_id: str) -> PluginStateEntry | None:
        return self.plugins.get(plugin_id)

    def ensure_entry(self, plugin_id: str) -> PluginStateEntry:
        if plugin_id not in self.plugins:
            self.plugins[plugin_id] = PluginStateEntry(
                plugin_id=plugin_id, installed_at=time.time()
            )
        return self.plugins[plugin_id]

    def is_enabled(self, plugin_id: str) -> bool:
        entry = self.plugins.get(plugin_id)
        if entry is None:
            return True  # not tracked yet → default enabled
        return entry.enabled

    def enable(self, plugin_id: str) -> None:
        entry = self.ensure_entry(plugin_id)
        entry.enabled = True
        entry.disabled_reason = ""

    def disable(self, plugin_id: str, reason: str = "user") -> None:
        entry = self.ensure_entry(plugin_id)
        entry.enabled = False
        entry.disabled_reason = reason

    def record_error(self, plugin_id: str, error: str) -> None:
        entry = self.ensure_entry(plugin_id)
        entry.error_count += 1
        entry.last_error = error
        entry.last_error_time = time.time()

    def set_active_backend(self, slot: str, provider_id: str) -> None:
        self.active_backends[slot] = provider_id

    def get_active_backend(self, slot: str) -> str | None:
        return self.active_backends.get(slot)

    def remove_plugin(self, plugin_id: str) -> None:
        self.plugins.pop(plugin_id, None)
        self.active_backends = {
            k: v for k, v in self.active_backends.items() if v != plugin_id
        }

    def save(self, path: Path) -> None:
        data = {
            "schema_version": _SCHEMA_VERSION,
            "plugins": {
                pid: {
                    "enabled": e.enabled,
                    "granted_permissions": e.granted_permissions,
                    "installed_at": e.installed_at,
                    "disabled_reason": e.disabled_reason,
                    "error_count": e.error_count,
                    "last_error": e.last_error,
                    "last_error_time": e.last_error_time,
                }
                for pid, e in self.plugins.items()
            },
            "active_backends": self.active_backends,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(path)

    @classmethod
    def load(cls, path: Path) -> PluginState:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Corrupt plugin_state.json, starting fresh")
            return cls()

        state = cls()
        file_version = data.get("schema_version", 1)
        if file_version < _SCHEMA_VERSION:
            logger.info("Migrating plugin_state.json from v%d to v%d", file_version, _SCHEMA_VERSION)
        plugins_data = data.get("plugins", {})
        if not isinstance(plugins_data, dict):
            logger.warning("Corrupt plugin_state.json: 'plugins' is not a dict, starting fresh")
            return cls()
        for pid, pdata in plugins_data.items():
            state.plugins[pid] = PluginStateEntry(
                plugin_id=pid,
                enabled=pdata.get("enabled", True),
                granted_permissions=pdata.get("granted_permissions", []),
                installed_at=pdata.get("installed_at", 0),
                disabled_reason=pdata.get("disabled_reason", ""),
                error_count=pdata.get("error_count", 0),
                last_error=pdata.get("last_error", ""),
                last_error_time=pdata.get("last_error_time", 0),
            )
        state.active_backends = data.get("active_backends", {})
        return state

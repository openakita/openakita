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
    # Absolute path or URL the plugin was originally installed from.
    # When this points at a still-existing local directory we can re-sync
    # ``data/plugins/<id>`` from it on every reload — that is what makes
    # the "Reload" button actually pick up source-code edits without the
    # user having to remove + reinstall. URL/git sources are recorded for
    # traceability only and are NOT auto-resynced on reload.
    install_source: str = ""
    loaded: bool = False
    pending_update_revision: str = ""
    pending_update_at: float = 0.0
    pending_update_path: str = ""
    pending_update_source: str = ""
    reload_required: bool = False
    update_policy: str = "disk-only"


_SCHEMA_VERSION = 2


_VALID_DEV_MODES = ("off", "symlink")


@dataclass
class PluginState:
    """Persistent plugin state, stored in data/plugin_state.json."""

    schema_version: int = _SCHEMA_VERSION
    plugins: dict[str, PluginStateEntry] = field(default_factory=dict)
    active_backends: dict[str, str] = field(
        default_factory=dict
    )  # reserved for future memory/search backend switching
    # Developer mode for local-path installs:
    #   "off"     — copy plugin files (default, production behaviour)
    #   "symlink" — symlink the source dir so live edits hot-reload
    dev_mode: str = "off"

    def set_dev_mode(self, mode: str) -> None:
        if mode not in _VALID_DEV_MODES:
            raise ValueError(
                f"Invalid dev_mode {mode!r}; expected one of {_VALID_DEV_MODES}"
            )
        self.dev_mode = mode

    @property
    def dev_mode_enabled(self) -> bool:
        return self.dev_mode != "off"

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

    def mark_loaded(self, plugin_id: str) -> None:
        entry = self.ensure_entry(plugin_id)
        entry.loaded = True
        self.clear_pending_update(plugin_id)

    def mark_pending_update(
        self,
        plugin_id: str,
        revision: str,
        *,
        pending_path: str = "",
        source: str = "",
    ) -> None:
        entry = self.ensure_entry(plugin_id)
        entry.pending_update_revision = revision
        entry.pending_update_at = time.time()
        entry.pending_update_path = pending_path
        entry.pending_update_source = source
        entry.reload_required = True
        entry.update_policy = "disk-only"

    def clear_pending_update(self, plugin_id: str) -> None:
        entry = self.ensure_entry(plugin_id)
        entry.pending_update_revision = ""
        entry.pending_update_at = 0.0
        entry.pending_update_path = ""
        entry.pending_update_source = ""
        entry.reload_required = False
        entry.update_policy = "disk-only"

    def set_active_backend(self, slot: str, provider_id: str) -> None:
        self.active_backends[slot] = provider_id

    def get_active_backend(self, slot: str) -> str | None:
        return self.active_backends.get(slot)

    def remove_plugin(self, plugin_id: str) -> None:
        self.plugins.pop(plugin_id, None)
        self.active_backends = {k: v for k, v in self.active_backends.items() if v != plugin_id}

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
                    "install_source": e.install_source,
                    "loaded": e.loaded,
                    "pending_update_revision": e.pending_update_revision,
                    "pending_update_at": e.pending_update_at,
                    "pending_update_path": e.pending_update_path,
                    "pending_update_source": e.pending_update_source,
                    "reload_required": e.reload_required,
                    "update_policy": e.update_policy,
                }
                for pid, e in self.plugins.items()
            },
            "active_backends": self.active_backends,
            "dev_mode": self.dev_mode,
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
            logger.info(
                "Migrating plugin_state.json from v%d to v%d", file_version, _SCHEMA_VERSION
            )
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
                install_source=pdata.get("install_source", "") or "",
                loaded=bool(pdata.get("loaded", False)),
                pending_update_revision=pdata.get("pending_update_revision", "") or "",
                pending_update_at=pdata.get("pending_update_at", 0),
                pending_update_path=pdata.get("pending_update_path", "") or "",
                pending_update_source=pdata.get("pending_update_source", "") or "",
                reload_required=bool(pdata.get("reload_required", False)),
                update_policy=pdata.get("update_policy", "disk-only") or "disk-only",
            )
        state.active_backends = data.get("active_backends", {})
        loaded_dev_mode = data.get("dev_mode", "off")
        if loaded_dev_mode in _VALID_DEV_MODES:
            state.dev_mode = loaded_dev_mode
        else:
            logger.warning(
                "Unknown dev_mode %r in plugin_state.json; resetting to 'off'",
                loaded_dev_mode,
            )
            state.dev_mode = "off"
        return state


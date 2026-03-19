"""
EndpointManager: LLM 端点配置的唯一管理者。

所有对 .env 和 llm_endpoints.json 的写操作都必须经过这里。
提供原子写入、自动备份、线程锁、BOM 容错等保护机制。
"""

from __future__ import annotations

import hashlib
import json
import locale
import logging
import os
import shutil
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_ENDPOINT_LISTS = ("endpoints", "compiler_endpoints", "stt_endpoints")


def _strip_bom(raw: bytes) -> bytes:
    """Strip UTF-8 BOM if present."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:]
    return raw


def _read_text_robust(path: Path) -> str:
    """Read a text file with BOM stripping and encoding fallback."""
    if not path.exists():
        return ""
    raw = path.read_bytes()
    raw = _strip_bom(raw)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning(
            "Failed to decode %s as UTF-8, falling back to system encoding", path
        )
        try:
            return raw.decode(locale.getpreferredencoding(False), errors="replace")
        except Exception:
            return raw.decode("utf-8", errors="replace")


def _parse_env(content: str) -> dict[str, str]:
    """Parse .env content into key-value dict."""
    env: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            inner = value[1:-1]
            if "\\" in inner:
                inner = (
                    inner.replace("\\\\", "\x00")
                    .replace('\\"', '"')
                    .replace("\x00", "\\")
                )
            value = inner
        else:
            for sep in (" #", "\t#"):
                idx = value.find(sep)
                if idx != -1:
                    value = value[:idx].rstrip()
                    break
        env[key] = value
    return env


def _needs_quoting(value: str) -> bool:
    if not value:
        return False
    if value[0] in (" ", "\t") or value[-1] in (" ", "\t"):
        return True
    if value[0] in ('"', "'"):
        return True
    for ch in (" ", "#", '"', "'", "\\"):
        if ch in value:
            return True
    return False


def _quote_env_value(value: str) -> str:
    if not _needs_quoting(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _merge_env_content(
    existing: str,
    entries: dict[str, str],
    delete_keys: set[str] | None = None,
) -> str:
    """Merge entries into existing .env content (preserves comments, order)."""
    delete_keys = delete_keys or set()
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
        if key in delete_keys:
            updated_keys.add(key)
            continue
        if key in entries:
            value = entries[key]
            if value == "":
                new_lines.append(line)
            else:
                new_lines.append(f"{key}={_quote_env_value(value)}")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    for key, value in entries.items():
        if key not in updated_keys and value != "":
            new_lines.append(f"{key}={_quote_env_value(value)}")

    return "\n".join(new_lines) + "\n"


class EndpointManager:
    """LLM 端点配置的唯一管理者。

    所有对 .env 和 llm_endpoints.json 的写操作都必须经过这里。
    """

    def __init__(self, workspace_dir: Path):
        self._ws_dir = Path(workspace_dir)
        self._json_path = self._ws_dir / "data" / "llm_endpoints.json"
        self._env_path = self._ws_dir / ".env"
        self._lock = threading.Lock()

    @property
    def json_path(self) -> Path:
        return self._json_path

    @property
    def env_path(self) -> Path:
        return self._env_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_endpoint(
        self,
        endpoint: dict,
        api_key: str | None = None,
        endpoint_type: str = "endpoints",
        expected_version: str | None = None,
    ) -> dict:
        """Save or update an endpoint atomically.

        Writes api_key to .env first, then updates llm_endpoints.json.
        Returns the saved endpoint dict (with api_key_env populated).
        """
        if endpoint_type not in _ENDPOINT_LISTS:
            raise ValueError(f"Invalid endpoint_type: {endpoint_type}")

        name = endpoint.get("name", "").strip()
        if not name:
            raise ValueError("Endpoint must have a name")

        with self._lock:
            config, version = self._read_json_versioned()

            if expected_version and expected_version != version:
                raise ConflictError(
                    "配置已被其他会话修改，请刷新后重试",
                    current_version=version,
                )

            ep_list = config.get(endpoint_type, [])
            existing = next(
                (e for e in ep_list if e.get("name") == name), None
            )

            # Resolve api_key_env
            if api_key is not None:
                env_var = self._resolve_env_var(endpoint, existing, config)

                # If this env_var is shared with other endpoints and the key
                # value is DIFFERENT, allocate a new unique env_var for this
                # endpoint to avoid overwriting others.
                other_users = self._find_endpoints_using_env_var(
                    config, env_var, exclude_name=name
                )
                if other_users:
                    env = _parse_env(_read_text_robust(self._env_path))
                    old_val = env.get(env_var, "")
                    if old_val and old_val != api_key:
                        env_var = self._allocate_unique_env_var(
                            endpoint, config
                        )

                # Write .env first (prefer losing an orphan key over losing endpoint data)
                self._write_env_key(env_var, api_key)
                os.environ[env_var] = api_key
            else:
                env_var = (
                    existing.get("api_key_env", "")
                    if existing
                    else endpoint.get("api_key_env", "")
                )

            endpoint["api_key_env"] = env_var

            # Upsert into endpoint list
            if existing:
                idx = ep_list.index(existing)
                ep_list[idx] = {**existing, **endpoint}
            else:
                ep_list.append(endpoint)

            ep_list.sort(
                key=lambda e: (int(e.get("priority", 999)), e.get("name", ""))
            )
            config[endpoint_type] = ep_list
            self._write_json(config)

            return endpoint

    def delete_endpoint(
        self,
        name: str,
        endpoint_type: str = "endpoints",
        clean_env: bool = True,
    ) -> dict | None:
        """Delete an endpoint by name. Returns removed endpoint or None."""
        if endpoint_type not in _ENDPOINT_LISTS:
            raise ValueError(f"Invalid endpoint_type: {endpoint_type}")

        with self._lock:
            config, _ = self._read_json_versioned()
            ep_list = config.get(endpoint_type, [])

            removed = None
            new_list = []
            for ep in ep_list:
                if ep.get("name") == name:
                    removed = ep
                else:
                    new_list.append(ep)

            if removed is None:
                return None

            config[endpoint_type] = new_list

            # Clean up .env key if no other endpoint references it
            if clean_env:
                env_var = removed.get("api_key_env", "")
                if env_var:
                    still_used = self._find_endpoints_using_env_var(
                        config, env_var
                    )
                    if not still_used:
                        self._delete_env_key(env_var)
                        os.environ.pop(env_var, None)

            self._write_json(config)
            return removed

    def list_endpoints(
        self, endpoint_type: str = "endpoints"
    ) -> list[dict]:
        """Read endpoints from config file."""
        config = self._read_json()
        return config.get(endpoint_type, [])

    def get_all_config(self) -> dict:
        """Read the entire llm_endpoints.json content."""
        return self._read_json()

    def get_version(self) -> str:
        """Get the current config version (content hash)."""
        _, version = self._read_json_versioned()
        return version

    def get_endpoint_status(self) -> list[dict]:
        """Return key presence status for all endpoints."""
        config = self._read_json()
        env = _parse_env(_read_text_robust(self._env_path))
        result = []
        for list_key in _ENDPOINT_LISTS:
            for ep in config.get(list_key, []):
                env_var = ep.get("api_key_env", "")
                key_present = bool(env_var and env.get(env_var, "").strip())
                result.append({
                    "name": ep.get("name", ""),
                    "type": list_key,
                    "provider": ep.get("provider", ""),
                    "model": ep.get("model", ""),
                    "key_env": env_var,
                    "key_present": key_present,
                    "enabled": ep.get("enabled", True),
                })
        return result

    # ------------------------------------------------------------------
    # File I/O with atomic write + backup
    # ------------------------------------------------------------------

    def _read_json(self) -> dict:
        """Read llm_endpoints.json with robust error handling and .bak fallback."""
        try:
            content = _read_text_robust(self._json_path)
            if not content.strip():
                return self._empty_config()
            return json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            bak = self._json_path.with_suffix(".json.bak")
            if bak.exists():
                logger.warning(
                    "Primary config corrupted (%s), restoring from backup", e
                )
                try:
                    content = _read_text_robust(bak)
                    data = json.loads(content)
                    self._atomic_write(self._json_path, content)
                    return data
                except Exception:
                    pass
            logger.error("Cannot read llm_endpoints.json: %s", e)
            return self._empty_config()

    def _read_json_versioned(self) -> tuple[dict, str]:
        """Read JSON and return (data, version_hash)."""
        try:
            content = _read_text_robust(self._json_path)
            if not content.strip():
                return self._empty_config(), "empty"
            version = hashlib.md5(content.encode()).hexdigest()[:8]
            return json.loads(content), version
        except (json.JSONDecodeError, OSError):
            return self._read_json(), "error"

    def _write_json(self, data: dict) -> None:
        """Write llm_endpoints.json atomically with backup."""
        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        self._atomic_write(self._json_path, content)

    def _write_env_key(self, key: str, value: str) -> None:
        """Write a single key to .env (merge, not overwrite)."""
        existing = _read_text_robust(self._env_path)
        new_content = _merge_env_content(existing, {key: value})
        self._atomic_write(self._env_path, new_content)

    def _delete_env_key(self, key: str) -> None:
        """Remove a key from .env."""
        existing = _read_text_robust(self._env_path)
        new_content = _merge_env_content(existing, {}, delete_keys={key})
        self._atomic_write(self._env_path, new_content)

    def _atomic_write(self, path: Path, content: str, retries: int = 3) -> None:
        """Write via temp file + rename for atomicity, with backup."""
        path.parent.mkdir(parents=True, exist_ok=True)

        # Backup existing file
        if path.exists():
            bak = path.with_suffix(path.suffix + ".bak")
            try:
                shutil.copy2(path, bak)
            except OSError as e:
                logger.warning("Failed to create backup %s: %s", bak, e)

        # Atomic write: tmp → rename
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")

        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                tmp.replace(path)
                return
            except PermissionError as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(0.2 * (attempt + 1))

        # All retries failed — fall back to direct write
        logger.warning(
            "Atomic rename failed after %d retries (%s), falling back to direct write",
            retries,
            last_err,
        )
        path.write_text(content, encoding="utf-8")
        tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # env var naming
    # ------------------------------------------------------------------

    def _resolve_env_var(
        self, endpoint: dict, existing: dict | None, config: dict
    ) -> str:
        """Determine the env var name for an endpoint."""
        # If editing and endpoint already has an env var, keep it
        if existing and existing.get("api_key_env"):
            return existing["api_key_env"]
        # If the endpoint dict specifies one, use it
        if endpoint.get("api_key_env"):
            return endpoint["api_key_env"]
        # Otherwise allocate a new unique one
        return self._allocate_unique_env_var(endpoint, config)

    def _allocate_unique_env_var(self, endpoint: dict, config: dict) -> str:
        """Generate a unique env var name for a new endpoint."""
        used = self._collect_used_env_vars(config)

        provider = endpoint.get("provider", "custom").upper().replace("-", "_")
        base_name = f"{provider}_API_KEY"

        if base_name not in used:
            return base_name

        for i in range(2, 100):
            candidate = f"{base_name}_{i}"
            if candidate not in used:
                return candidate

        # Extremely unlikely fallback
        import uuid
        return f"{base_name}_{uuid.uuid4().hex[:6]}"

    def _collect_used_env_vars(self, config: dict) -> set[str]:
        """Collect all api_key_env names across all endpoint lists."""
        used: set[str] = set()
        for list_key in _ENDPOINT_LISTS:
            for ep in config.get(list_key, []):
                env_var = ep.get("api_key_env", "")
                if env_var:
                    used.add(env_var)
        return used

    def _find_endpoints_using_env_var(
        self, config: dict, env_var: str, exclude_name: str | None = None
    ) -> list[dict]:
        """Find all endpoints referencing a given env var."""
        result = []
        for list_key in _ENDPOINT_LISTS:
            for ep in config.get(list_key, []):
                if ep.get("api_key_env") == env_var:
                    if exclude_name and ep.get("name") == exclude_name:
                        continue
                    result.append(ep)
        return result

    @staticmethod
    def _empty_config() -> dict:
        return {
            "endpoints": [],
            "compiler_endpoints": [],
            "stt_endpoints": [],
            "settings": {},
        }


class ConflictError(Exception):
    """Raised when optimistic lock detects a concurrent modification."""

    def __init__(self, message: str, current_version: str = ""):
        super().__init__(message)
        self.current_version = current_version

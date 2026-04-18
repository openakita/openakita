"""
LLM endpoint configuration loading.

Supports loading endpoint configuration from JSON files.
"""

import json
import locale
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from ..utils.atomic_io import read_json_safe, safe_write
from .types import ConfigurationError, EndpointConfig

logger = logging.getLogger(__name__)


def _strip_bom(raw: bytes) -> bytes:
    """Strip UTF-8 BOM (EF BB BF) if present."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:]
    return raw


def _read_text_robust(path: Path) -> str:
    """Read a text file with BOM stripping and encoding fallback."""
    if not path.exists():
        return ""
    raw = _strip_bom(path.read_bytes())
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning(
            "Failed to decode %s as UTF-8, falling back to system encoding",
            path,
        )
        try:
            return raw.decode(locale.getpreferredencoding(False), errors="replace")
        except Exception:
            return raw.decode("utf-8", errors="replace")


def _parse_env_content(content: str) -> dict[str, str]:
    """Parse .env content into key-value pairs."""
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
                inner = inner.replace("\\\\", "\x00").replace('\\"', '"').replace("\x00", "\\")
            value = inner
        else:
            for sep in (" #", "\t#"):
                idx = value.find(sep)
                if idx != -1:
                    value = value[:idx].rstrip()
                    break
        env[key] = value
    return env


def _get_workspace_dir_from_config_path(config_path: Path) -> Path:
    """Infer workspace root from an endpoint config path."""
    config_path = Path(config_path)
    if config_path.parent.name == "data":
        return config_path.parent.parent
    return config_path.parent


def get_workspace_dir(config_path: Path | None = None) -> Path:
    """Return the workspace root associated with an endpoints config path."""
    resolved_path = Path(config_path) if config_path is not None else get_default_config_path()
    return _get_workspace_dir_from_config_path(resolved_path)


def get_workspace_env_path(config_path: Path | None = None) -> Path:
    """Return the .env path associated with an endpoints config path."""
    return get_workspace_dir(config_path) / ".env"


def read_workspace_env_values(config_path: Path | None = None) -> dict[str, str]:
    """Read the workspace .env as a plain dict without mutating os.environ."""
    env_path = get_workspace_env_path(config_path)
    if not env_path.exists():
        return {}
    return _parse_env_content(_read_text_robust(env_path))


def _safe_load_dotenv(env_path: Path) -> None:
    """Load a .env file with BOM handling, encoding fallback, and override.

    - Strips UTF-8 BOM before loading (Windows Notepad compatibility).
    - Tries UTF-8 first, falls back to platform default encoding.
    - Uses ``override=True`` so Python's own read always wins over any
      values that may have been pre-injected into ``os.environ``.
    """
    try:
        raw = env_path.read_bytes()
        stripped = _strip_bom(raw)
        if stripped != raw:
            logger.debug("Stripped UTF-8 BOM from %s", env_path)
            tmp = env_path.with_suffix(".env._bom_tmp")
            try:
                tmp.write_bytes(stripped)
                load_dotenv(tmp, override=True)
            finally:
                tmp.unlink(missing_ok=True)
        else:
            load_dotenv(env_path, override=True)
    except UnicodeDecodeError:
        logger.warning(
            "Failed to read %s as UTF-8; retrying with system encoding. "
            "Consider converting the file to UTF-8.",
            env_path,
        )
        try:
            load_dotenv(env_path, override=True, encoding=None)
        except Exception:
            logger.error("Could not load %s with any encoding, skipping.", env_path)
    except Exception as e:
        logger.error("Unexpected error loading %s: %s", env_path, e)


def ensure_env_loaded(config_path: Path | None = None) -> Path | None:
    """Load the workspace .env associated with the given config path."""
    env_path = get_workspace_env_path(config_path)
    if env_path.exists():
        _safe_load_dotenv(env_path)
        logger.info("Loaded .env from %s", env_path)
        return env_path
    logger.debug("No .env file found at %s", env_path)
    return None


_workspace_env_loaded: set[str] = set()


def _ensure_workspace_env_loaded(config_path: Path) -> None:
    """Load the .env file from the workspace that owns *config_path*.

    ``_load_env()`` runs at import-time from CWD, which works for CLI /
    development but can miss the workspace ``.env`` in packaged desktop
    builds (Tauri + PyInstaller) where CWD may differ from the workspace
    root.  This function is called from ``load_endpoints_config()`` — at
    that point we *know* the concrete config path, so we can derive the
    workspace root reliably: ``config_path`` is ``<workspace>/data/llm_endpoints.json``.
    """
    ws_root = config_path.parent.parent
    ws_key = str(ws_root)
    if ws_key in _workspace_env_loaded:
        return
    _workspace_env_loaded.add(ws_key)
    env_path = ws_root / ".env"
    if env_path.exists():
        _safe_load_dotenv(env_path)
        logger.info("Loaded workspace .env from %s", env_path)


def get_default_config_path() -> Path:
    """Return the default configuration file path.

    Search order:
    1. Environment variable LLM_ENDPOINTS_CONFIG
    2. data/llm_endpoints.json in CWD or its parents (up to 3 levels)
    3. data/llm_endpoints.json relative to the package file (up to 5 levels)
    4. Fallback: CWD/data/llm_endpoints.json (even if it does not exist)
    """
    # 1) Environment variable takes priority
    env_path = os.environ.get("LLM_ENDPOINTS_CONFIG")
    if env_path:
        return Path(env_path)

    # 2) Search upward from CWD (pip install scenario: openakita init creates data/ in CWD)
    cwd = Path.cwd()
    current = cwd
    for _ in range(3):
        config_path = current / "data" / "llm_endpoints.json"
        if config_path.exists():
            return config_path
        parent = current.parent
        if parent == current:
            break
        current = parent

    # 3) Search upward from the package file (development / editable install scenario)
    current = Path(__file__).parent
    for _ in range(5):
        config_path = current / "data" / "llm_endpoints.json"
        if config_path.exists():
            return config_path
        current = current.parent

    # 4) Fallback: return the default location under CWD (let the caller handle non-existence)
    return cwd / "data" / "llm_endpoints.json"


ensure_env_loaded()


def load_endpoints_config(
    config_path: Path | None = None,
) -> tuple[list[EndpointConfig], list[EndpointConfig], list[EndpointConfig], dict]:
    """
    Load endpoint configuration.

    Args:
        config_path: Configuration file path; defaults to get_default_config_path().

    Returns:
        (endpoints, compiler_endpoints, stt_endpoints, settings):
        Main endpoint list, Prompt Compiler endpoint list,
        speech-to-text endpoint list, and global settings.

    Raises:
        ConfigurationError: Configuration error.
    """
    if config_path is None:
        config_path = get_default_config_path()

    config_path = Path(config_path)
    env_values = read_workspace_env_values(config_path)

    _ensure_workspace_env_loaded(config_path)

    data = read_json_safe(config_path)
    if data is None:
        logger.warning(f"Config file not found: {config_path}, using empty config")
        return [], [], [], {}

    def _parse_endpoint_list(key: str) -> list[EndpointConfig]:
        result = []
        for ep_data in data.get(key, []):
            try:
                ep_payload = dict(ep_data)
                env_var = ep_payload.get("api_key_env")
                if not ep_payload.get("api_key") and env_var:
                    ep_payload["api_key"] = env_values.get(env_var) or os.environ.get(env_var)

                endpoint = EndpointConfig.from_dict(ep_payload)
                if not endpoint.enabled:
                    logger.info(f"Skipping disabled endpoint '{endpoint.name}'")
                    continue
                if endpoint.api_key_env and not endpoint.get_api_key():
                    logger.warning(
                        f"API key not found for endpoint '{endpoint.name}': "
                        f"env var '{endpoint.api_key_env}' is not set"
                    )
                result.append(endpoint)
            except Exception as e:
                logger.error(f"Failed to parse endpoint config ({key}): {e}")
                continue
        result.sort(key=lambda x: x.priority)
        return result

    # Parse main endpoints
    endpoints = _parse_endpoint_list("endpoints")
    if not endpoints:
        logger.warning("No valid endpoints found in config")

    # Parse Prompt Compiler endpoints
    compiler_endpoints = _parse_endpoint_list("compiler_endpoints")
    if compiler_endpoints:
        logger.info(f"Loaded {len(compiler_endpoints)} compiler endpoints")

    # Parse speech-to-text (STT) endpoints
    stt_endpoints = _parse_endpoint_list("stt_endpoints")
    if stt_endpoints:
        logger.info(f"Loaded {len(stt_endpoints)} STT endpoints")
    else:
        logger.debug("No STT endpoints configured")

    # Parse global settings
    settings = data.get("settings", {})

    logger.info(f"Loaded {len(endpoints)} endpoints from {config_path}")

    return endpoints, compiler_endpoints, stt_endpoints, settings


def save_endpoints_config(
    endpoints: list[EndpointConfig],
    settings: dict | None = None,
    config_path: Path | None = None,
    compiler_endpoints: list[EndpointConfig] | None = None,
    stt_endpoints: list[EndpointConfig] | None = None,
):
    """
    Save endpoint configuration.

    Args:
        endpoints: Main endpoint configuration list.
        settings: Global settings.
        config_path: Configuration file path.
        compiler_endpoints: Prompt Compiler endpoint list (optional).
        stt_endpoints: Speech-to-text endpoint list (optional).
    """
    if config_path is None:
        config_path = get_default_config_path()

    config_path = Path(config_path)

    data: dict = {
        "endpoints": [ep.to_dict() for ep in endpoints],
    }

    if compiler_endpoints:
        data["compiler_endpoints"] = [ep.to_dict() for ep in compiler_endpoints]

    if stt_endpoints:
        data["stt_endpoints"] = [ep.to_dict() for ep in stt_endpoints]

    data["settings"] = settings or {
        "retry_count": 2,
        "retry_delay_seconds": 2,
        "health_check_interval": 60,
        "fallback_on_error": True,
    }

    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    safe_write(config_path, content)

    logger.info(f"Saved {len(endpoints)} endpoints to {config_path}")


def create_default_config(config_path: Path | None = None):
    """
    Create a default configuration file.

    Args:
        config_path: Configuration file path.
    """
    default_endpoints = [
        EndpointConfig(
            name="claude-primary",
            provider="anthropic",
            api_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
            model="claude-sonnet-4-20250514",
            priority=1,
            max_tokens=0,
            timeout=180,
            capabilities=["text", "vision", "tools"],
        ),
        EndpointConfig(
            name="qwen-backup",
            provider="dashscope",
            api_type="openai",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="DASHSCOPE_API_KEY",
            model="qwen-plus",
            priority=2,
            max_tokens=0,
            timeout=180,
            capabilities=["text", "tools", "thinking"],
            extra_params={"enable_thinking": True},
        ),
    ]

    save_endpoints_config(default_endpoints, config_path=config_path)


def validate_config(config_path: Path | None = None) -> list[str]:
    """
    Validate the configuration file.

    Returns:
        A list of errors (empty list means no errors).
    """
    errors = []

    if config_path is not None:
        raw_path = Path(config_path)
        if raw_path.exists() and read_json_safe(raw_path) is None:
            return [f"Invalid JSON in config file: {raw_path}"]

    try:
        endpoints, compiler_endpoints, stt_endpoints, settings = load_endpoints_config(config_path)
    except ConfigurationError as e:
        return [str(e)]

    if not endpoints:
        errors.append("No endpoints configured")

    def _validate_endpoints(eps: list[EndpointConfig], label: str = "") -> None:
        prefix = f"[{label}] " if label else ""
        for ep in eps:
            # Check API key
            if ep.api_key_env and not ep.get_api_key():
                errors.append(
                    f"{prefix}Endpoint '{ep.name}': API key env var '{ep.api_key_env}' not set"
                )

            # Check API type
            if ep.api_type not in ("anthropic", "openai"):
                errors.append(f"{prefix}Endpoint '{ep.name}': Invalid api_type '{ep.api_type}'")

            # Check base_url
            if not ep.base_url.startswith(("http://", "https://")):
                errors.append(f"{prefix}Endpoint '{ep.name}': Invalid base_url '{ep.base_url}'")

    _validate_endpoints(endpoints)
    _validate_endpoints(compiler_endpoints, label="compiler")
    _validate_endpoints(stt_endpoints, label="stt")

    return errors

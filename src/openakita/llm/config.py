"""
LLM 端点配置加载

支持从 JSON 文件加载端点配置。
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


def _try_recover_env_from_backup(env_path: Path) -> bool:
    """If .env is missing or suspiciously small, try to restore from .env.bak.

    Returns True if a backup was restored.
    """
    import shutil

    bak = env_path.with_suffix(env_path.suffix + ".bak")
    if not bak.exists():
        return False

    env_missing = not env_path.exists()
    env_empty = False
    bak_bigger = False

    if not env_missing:
        try:
            env_size = env_path.stat().st_size
            bak_size = bak.stat().st_size
            env_empty = env_size < 10
            bak_bigger = bak_size > env_size + 20
        except OSError:
            pass

    if env_missing or (env_empty and bak.stat().st_size > 10):
        reason = "missing" if env_missing else "empty/truncated"
        logger.warning(
            "[env recovery] .env is %s, restoring from .env.bak (%s)",
            reason, bak,
        )
        try:
            shutil.copy2(bak, env_path)
            return True
        except OSError as e:
            logger.error("[env recovery] Failed to restore .env from backup: %s", e)

    if bak_bigger:
        logger.info(
            "[env recovery] .env.bak is significantly larger than .env — "
            "possible data loss. Backup preserved at %s",
            bak,
        )

    return False


def ensure_env_loaded(config_path: Path | None = None) -> Path | None:
    """Load the workspace .env associated with the given config path.

    Checks for corruption/truncation first and restores from .bak if needed.
    """
    env_path = get_workspace_env_path(config_path)

    _try_recover_env_from_backup(env_path)

    if env_path.exists():
        _safe_load_dotenv(env_path)
        logger.info("Loaded .env from %s", env_path)
        return env_path
    logger.debug("No .env file found at %s", env_path)
    return None


def get_default_config_path() -> Path:
    """获取默认配置文件路径

    搜索顺序：
    1. 环境变量 LLM_ENDPOINTS_CONFIG
    2. CWD 及其父级（最多 3 层）下的 data/llm_endpoints.json
    3. 包文件所在目录向上（最多 5 层）下的 data/llm_endpoints.json
    4. 兜底返回 CWD/data/llm_endpoints.json（即使不存在）
    """
    # 1) 环境变量优先
    env_path = os.environ.get("LLM_ENDPOINTS_CONFIG")
    if env_path:
        return Path(env_path)

    # 2) 从 CWD 向上搜索（pip install 场景：openakita init 在 CWD 创建 data/）
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

    # 3) 从包文件向上搜索（开发 / editable install 场景）
    current = Path(__file__).parent
    for _ in range(5):
        config_path = current / "data" / "llm_endpoints.json"
        if config_path.exists():
            return config_path
        current = current.parent

    # 4) 兜底：返回 CWD 下的默认位置（让调用方统一处理不存在的情况）
    return cwd / "data" / "llm_endpoints.json"


ensure_env_loaded()


def load_endpoints_config(
    config_path: Path | None = None,
) -> tuple[list[EndpointConfig], list[EndpointConfig], list[EndpointConfig], dict]:
    """
    加载端点配置

    Args:
        config_path: 配置文件路径，默认使用 get_default_config_path()

    Returns:
        (endpoints, compiler_endpoints, stt_endpoints, settings):
        主端点列表、Prompt Compiler 专用端点列表、语音识别端点列表、全局设置

    Raises:
        ConfigurationError: 配置错误
    """
    if config_path is None:
        config_path = get_default_config_path()

    config_path = Path(config_path)
    env_values = read_workspace_env_values(config_path)

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

    # 解析主端点
    endpoints = _parse_endpoint_list("endpoints")
    if not endpoints:
        logger.warning("No valid endpoints found in config")

    # 解析 Prompt Compiler 专用端点
    compiler_endpoints = _parse_endpoint_list("compiler_endpoints")
    if compiler_endpoints:
        logger.info(f"Loaded {len(compiler_endpoints)} compiler endpoints")

    # 解析语音识别（STT）端点
    stt_endpoints = _parse_endpoint_list("stt_endpoints")
    if stt_endpoints:
        logger.info(f"Loaded {len(stt_endpoints)} STT endpoints")
    else:
        logger.debug("No STT endpoints configured")

    # 解析全局设置
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
    保存端点配置（CLI / 离线场景专用，通过 safe_write 原子写入）

    注意：当后端 API 服务运行中时，所有端点写入应通过 EndpointManager（HTTP API）。
    此函数仅用于后端未运行的 CLI 交互 / 初始化场景。

    Args:
        endpoints: 主端点配置列表
        settings: 全局设置
        config_path: 配置文件路径
        compiler_endpoints: Prompt Compiler 专用端点列表（可选）
        stt_endpoints: 语音识别端点列表（可选）
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
    创建默认配置文件

    Args:
        config_path: 配置文件路径
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
    验证配置文件

    Returns:
        错误列表（空列表表示没有错误）
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
            # 检查 API Key
            if ep.api_key_env and not ep.get_api_key():
                errors.append(
                    f"{prefix}Endpoint '{ep.name}': API key env var '{ep.api_key_env}' not set"
                )

            # 检查 API 类型
            if ep.api_type not in ("anthropic", "openai"):
                errors.append(f"{prefix}Endpoint '{ep.name}': Invalid api_type '{ep.api_type}'")

            # 检查 base_url
            if not ep.base_url.startswith(("http://", "https://")):
                errors.append(f"{prefix}Endpoint '{ep.name}': Invalid base_url '{ep.base_url}'")

    _validate_endpoints(endpoints)
    _validate_endpoints(compiler_endpoints, label="compiler")
    _validate_endpoints(stt_endpoints, label="stt")

    return errors

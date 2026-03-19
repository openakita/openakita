"""
LLM 端点配置加载

支持从 JSON 文件加载端点配置。
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from .types import ConfigurationError, EndpointConfig

logger = logging.getLogger(__name__)


def _strip_bom(raw: bytes) -> bytes:
    """Strip UTF-8 BOM (EF BB BF) if present."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:]
    return raw


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


def _load_env():
    """Discover and load the nearest .env file.

    Search order: CWD (up to 3 levels) → package directory (up to 5 levels).
    """
    cwd = Path.cwd()
    current = cwd
    for _ in range(3):
        env_path = current / ".env"
        if env_path.exists():
            _safe_load_dotenv(env_path)
            logger.info("Loaded .env from %s", env_path)
            return
        parent = current.parent
        if parent == current:
            break
        current = parent

    current = Path(__file__).parent
    for _ in range(5):
        env_path = current / ".env"
        if env_path.exists():
            _safe_load_dotenv(env_path)
            logger.info("Loaded .env from %s", env_path)
            return
        current = current.parent

    logger.debug("No .env file found in search paths (CWD=%s)", cwd)


_load_env()


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

    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}, using empty config")
        return [], [], [], {}

    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigurationError(f"Invalid JSON in config file: {e}")
    except Exception as e:
        raise ConfigurationError(f"Failed to read config file: {e}")

    def _parse_endpoint_list(key: str) -> list[EndpointConfig]:
        result = []
        for ep_data in data.get(key, []):
            try:
                endpoint = EndpointConfig.from_dict(ep_data)
                if not endpoint.enabled:
                    logger.info(f"Skipping disabled endpoint '{endpoint.name}'")
                    continue
                if endpoint.api_key_env:
                    api_key = os.environ.get(endpoint.api_key_env)
                    if not api_key:
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
    保存端点配置

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
    config_path.parent.mkdir(parents=True, exist_ok=True)

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

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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
            if ep.api_key_env:
                api_key = os.environ.get(ep.api_key_env)
                if not api_key:
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

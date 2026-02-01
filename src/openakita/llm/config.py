"""
LLM 端点配置加载

支持从 JSON 文件加载端点配置。
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .types import EndpointConfig, ConfigurationError

logger = logging.getLogger(__name__)

# 确保 .env 文件被加载
def _load_env():
    """加载 .env 文件"""
    # 尝试从项目根目录加载
    current = Path(__file__).parent
    for _ in range(5):
        env_path = current / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            return
        current = current.parent

_load_env()


def get_default_config_path() -> Path:
    """获取默认配置文件路径"""
    # 优先使用环境变量
    env_path = os.environ.get("LLM_ENDPOINTS_CONFIG")
    if env_path:
        return Path(env_path)
    
    # 默认路径：项目根目录下的 data/llm_endpoints.json
    # 从当前文件向上查找
    current = Path(__file__).parent
    for _ in range(5):  # 最多向上 5 层
        config_path = current / "data" / "llm_endpoints.json"
        if config_path.exists():
            return config_path
        current = current.parent
    
    # 如果找不到，返回默认位置
    return Path(__file__).parent.parent.parent.parent / "data" / "llm_endpoints.json"


def load_endpoints_config(
    config_path: Optional[Path] = None,
) -> tuple[list[EndpointConfig], dict]:
    """
    加载端点配置
    
    Args:
        config_path: 配置文件路径，默认使用 get_default_config_path()
        
    Returns:
        (endpoints, settings): 端点配置列表和全局设置
        
    Raises:
        ConfigurationError: 配置错误
    """
    if config_path is None:
        config_path = get_default_config_path()
    
    config_path = Path(config_path)
    
    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}, using empty config")
        return [], {}
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigurationError(f"Invalid JSON in config file: {e}")
    except Exception as e:
        raise ConfigurationError(f"Failed to read config file: {e}")
    
    # 解析端点
    endpoints = []
    for ep_data in data.get("endpoints", []):
        try:
            endpoint = EndpointConfig.from_dict(ep_data)
            
            # 验证 API Key 环境变量存在
            api_key = os.environ.get(endpoint.api_key_env)
            if not api_key:
                logger.warning(
                    f"API key not found for endpoint '{endpoint.name}': "
                    f"env var '{endpoint.api_key_env}' is not set"
                )
            
            endpoints.append(endpoint)
        except Exception as e:
            logger.error(f"Failed to parse endpoint config: {e}")
            continue
    
    if not endpoints:
        logger.warning("No valid endpoints found in config")
    
    # 按优先级排序
    endpoints.sort(key=lambda x: x.priority)
    
    # 解析全局设置
    settings = data.get("settings", {})
    
    logger.info(f"Loaded {len(endpoints)} endpoints from {config_path}")
    
    return endpoints, settings


def save_endpoints_config(
    endpoints: list[EndpointConfig],
    settings: Optional[dict] = None,
    config_path: Optional[Path] = None,
):
    """
    保存端点配置
    
    Args:
        endpoints: 端点配置列表
        settings: 全局设置
        config_path: 配置文件路径
    """
    if config_path is None:
        config_path = get_default_config_path()
    
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    data = {
        "endpoints": [ep.to_dict() for ep in endpoints],
        "settings": settings or {
            "retry_count": 2,
            "retry_delay_seconds": 2,
            "health_check_interval": 60,
            "fallback_on_error": True,
        },
    }
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    logger.info(f"Saved {len(endpoints)} endpoints to {config_path}")


def create_default_config(config_path: Optional[Path] = None):
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
            max_tokens=8192,
            timeout=60,
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
            max_tokens=8192,
            timeout=60,
            capabilities=["text", "tools", "thinking"],
            extra_params={"enable_thinking": True},
        ),
    ]
    
    save_endpoints_config(default_endpoints, config_path=config_path)


def validate_config(config_path: Optional[Path] = None) -> list[str]:
    """
    验证配置文件
    
    Returns:
        错误列表（空列表表示没有错误）
    """
    errors = []
    
    try:
        endpoints, settings = load_endpoints_config(config_path)
    except ConfigurationError as e:
        return [str(e)]
    
    if not endpoints:
        errors.append("No endpoints configured")
    
    for ep in endpoints:
        # 检查 API Key
        api_key = os.environ.get(ep.api_key_env)
        if not api_key:
            errors.append(f"Endpoint '{ep.name}': API key env var '{ep.api_key_env}' not set")
        
        # 检查 API 类型
        if ep.api_type not in ("anthropic", "openai"):
            errors.append(f"Endpoint '{ep.name}': Invalid api_type '{ep.api_type}'")
        
        # 检查 base_url
        if not ep.base_url.startswith(("http://", "https://")):
            errors.append(f"Endpoint '{ep.name}': Invalid base_url '{ep.base_url}'")
    
    return errors

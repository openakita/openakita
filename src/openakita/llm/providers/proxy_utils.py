"""
代理和网络配置工具

从环境变量或配置中获取代理设置，以及 IPv4 强制配置。
"""

import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# 缓存：避免重复打印日志
_ipv4_logged = False
_transport_cache: Optional[httpx.AsyncHTTPTransport] = None


def get_proxy_config() -> Optional[str]:
    """获取代理配置
    
    优先级（从高到低）:
    1. ALL_PROXY 环境变量
    2. HTTPS_PROXY 环境变量
    3. HTTP_PROXY 环境变量
    4. 配置文件中的 all_proxy
    5. 配置文件中的 https_proxy
    6. 配置文件中的 http_proxy
    
    Returns:
        代理地址或 None
    """
    # 先检查环境变量
    for env_var in ['ALL_PROXY', 'all_proxy', 'HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy']:
        proxy = os.environ.get(env_var)
        if proxy:
            logger.debug(f"[Proxy] Using proxy from {env_var}: {proxy}")
            return proxy
    
    # 再检查配置文件
    try:
        from ...config import settings
        
        if settings.all_proxy:
            logger.debug(f"[Proxy] Using proxy from config all_proxy: {settings.all_proxy}")
            return settings.all_proxy
        if settings.https_proxy:
            logger.debug(f"[Proxy] Using proxy from config https_proxy: {settings.https_proxy}")
            return settings.https_proxy
        if settings.http_proxy:
            logger.debug(f"[Proxy] Using proxy from config http_proxy: {settings.http_proxy}")
            return settings.http_proxy
    except Exception as e:
        logger.debug(f"[Proxy] Failed to load config: {e}")
    
    return None


def is_ipv4_only() -> bool:
    """检查是否强制使用 IPv4
    
    通过环境变量 FORCE_IPV4=true 或配置文件 force_ipv4=true 启用
    """
    # 检查环境变量
    if os.environ.get('FORCE_IPV4', '').lower() in ('true', '1', 'yes'):
        return True
    
    # 检查配置文件
    try:
        from ...config import settings
        return getattr(settings, 'force_ipv4', False)
    except Exception:
        pass
    
    return False


def get_httpx_transport() -> Optional[httpx.AsyncHTTPTransport]:
    """获取 httpx transport（支持 IPv4-only 模式）
    
    当 FORCE_IPV4=true 时，创建强制使用 IPv4 的 transport。
    这对于某些 VPN（如 LetsTAP）不支持 IPv6 的情况很有用。
    
    Returns:
        httpx.AsyncHTTPTransport 或 None
    """
    global _ipv4_logged
    
    if is_ipv4_only():
        # 只在第一次打印日志
        if not _ipv4_logged:
            logger.info("[Network] IPv4-only mode enabled (FORCE_IPV4=true)")
            _ipv4_logged = True
        # local_address="0.0.0.0" 强制使用 IPv4
        return httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    return None


def get_httpx_proxy_mounts() -> Optional[dict]:
    """获取 httpx 代理配置
    
    Returns:
        httpx 代理 mounts 字典或 None
    """
    proxy = get_proxy_config()
    if proxy:
        return {
            "http://": proxy,
            "https://": proxy,
        }
    return None

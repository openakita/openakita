"""
核心模块 - 提供所有 API 集成的通用能力
"""
from .base import BaseAPIIntegration, APIConfig, APIResponse
from .exceptions import (
    APIIntegrationError,
    AuthenticationError,
    PermissionError,
    RateLimitError,
    NotFoundError,
    ValidationError,
    TimeoutError,
    ServiceUnavailableError,
    ConfigurationError
)
from .auth import CredentialManager, credential_manager
from .config import ConfigLoader, config

__all__ = [
    # 基类
    'BaseAPIIntegration',
    'APIConfig',
    'APIResponse',
    
    # 异常
    'APIIntegrationError',
    'AuthenticationError',
    'PermissionError',
    'RateLimitError',
    'NotFoundError',
    'ValidationError',
    'TimeoutError',
    'ServiceUnavailableError',
    'ConfigurationError',
    
    # 工具
    'CredentialManager',
    'credential_manager',
    'ConfigLoader',
    'config',
]

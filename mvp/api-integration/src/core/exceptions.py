"""
统一错误处理模块
"""
from typing import Optional, Dict, Any


class APIIntegrationError(Exception):
    """API 集成基础异常"""
    
    def __init__(
        self,
        message: str,
        error_code: str = "UNKNOWN_ERROR",
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "error_code": self.error_code,
            "status_code": self.status_code,
            "details": self.details
        }


class AuthenticationError(APIIntegrationError):
    """认证失败"""
    def __init__(self, message: str = "认证失败", **kwargs):
        super().__init__(message, error_code="AUTH_ERROR", status_code=401, **kwargs)


class PermissionError(APIIntegrationError):
    """权限不足"""
    def __init__(self, message: str = "权限不足", **kwargs):
        super().__init__(message, error_code="PERMISSION_ERROR", status_code=403, **kwargs)


class RateLimitError(APIIntegrationError):
    """请求频率超限"""
    def __init__(self, message: str = "请求频率超限", retry_after: Optional[int] = None, **kwargs):
        details = {"retry_after": retry_after} if retry_after else {}
        details.update(kwargs.get("details", {}))
        super().__init__(message, error_code="RATE_LIMIT_ERROR", status_code=429, details=details, **kwargs)


class NotFoundError(APIIntegrationError):
    """资源不存在"""
    def __init__(self, message: str = "资源不存在", **kwargs):
        super().__init__(message, error_code="NOT_FOUND_ERROR", status_code=404, **kwargs)


class ValidationError(APIIntegrationError):
    """参数验证失败"""
    def __init__(self, message: str = "参数验证失败", field_errors: Optional[Dict] = None, **kwargs):
        details = {"field_errors": field_errors} if field_errors else {}
        details.update(kwargs.get("details", {}))
        super().__init__(message, error_code="VALIDATION_ERROR", status_code=400, details=details, **kwargs)


class TimeoutError(APIIntegrationError):
    """请求超时"""
    def __init__(self, message: str = "请求超时", **kwargs):
        super().__init__(message, error_code="TIMEOUT_ERROR", status_code=504, **kwargs)


class ServiceUnavailableError(APIIntegrationError):
    """服务不可用"""
    def __init__(self, message: str = "服务暂时不可用", **kwargs):
        super().__init__(message, error_code="SERVICE_UNAVAILABLE", status_code=503, **kwargs)


class ConfigurationError(APIIntegrationError):
    """配置错误"""
    def __init__(self, message: str = "配置错误", **kwargs):
        super().__init__(message, error_code="CONFIG_ERROR", **kwargs)

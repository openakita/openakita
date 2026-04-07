"""API 适配器子包。"""

from openakita.integrations import APIError, AuthenticationError, BaseAPIAdapter, RateLimitError

__all__ = ["BaseAPIAdapter", "APIError", "AuthenticationError", "RateLimitError"]

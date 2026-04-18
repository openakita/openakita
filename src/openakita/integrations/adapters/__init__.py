"""API adapter sub-package."""

from openakita.integrations import APIError, AuthenticationError, BaseAPIAdapter, RateLimitError

__all__ = ["BaseAPIAdapter", "APIError", "AuthenticationError", "RateLimitError"]

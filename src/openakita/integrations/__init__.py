"""
Unified API integration framework.
Provides standardized API call interfaces, authentication management,
error handling, and monitoring.
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class APIError(Exception):
    """API call error."""

    def __init__(self, message: str, status_code: int | None = None, response: dict | None = None):
        self.message = message
        self.status_code = status_code
        self.response = response
        super().__init__(self.message)


class RateLimitError(APIError):
    """API rate-limit error."""

    def __init__(self, message: str, retry_after: int | None = None, **kwargs):
        self.retry_after = retry_after
        super().__init__(message, **kwargs)


class AuthenticationError(APIError):
    """Authentication failure error."""

    pass


class BaseAPIAdapter(ABC):
    """Base API adapter class."""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize the API adapter.

        Args:
            config: API configuration, including authentication credentials.
        """
        self.config = config
        self.name = self.__class__.__name__
        self._client = None

    @abstractmethod
    async def authenticate(self) -> bool:
        """Perform authentication; return True on success."""
        pass

    @abstractmethod
    async def call(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        """
        Call the API.

        Args:
            endpoint: API endpoint.
            method: HTTP method.
            **kwargs: Request parameters.

        Returns:
            API response data.
        """
        pass

    async def health_check(self) -> bool:
        """Health check."""
        try:
            await self.authenticate()
            return True
        except Exception as e:
            logger.error(f"{self.name} health check failed: {e}")
            return False

    def _log_request(self, endpoint: str, method: str, params: dict):
        """Log request details."""
        logger.debug(
            f"[{self.name}] {method} {endpoint} - Params: {json.dumps(params, ensure_ascii=False)}"
        )

    def _log_response(self, endpoint: str, status: int, duration: float):
        """Log response details."""
        logger.debug(f"[{self.name}] {endpoint} - Status: {status}, Duration: {duration:.2f}ms")

    def _handle_error(self, status_code: int, response: dict) -> APIError:
        """Handle error responses."""
        if status_code == 429:
            retry_after = response.get("retry_after") or response.get("headers", {}).get(
                "Retry-After"
            )
            return RateLimitError(
                "API rate limited", retry_after=retry_after, status_code=status_code, response=response
            )
        elif status_code in [401, 403]:
            return AuthenticationError(
                f"Authentication failed: {status_code}", status_code=status_code, response=response
            )
        else:
            return APIError(
                f"API call failed: {status_code}", status_code=status_code, response=response
            )


class APIGateway:
    """API gateway - centrally manages all API adapters."""

    def __init__(self):
        self.adapters: dict[str, BaseAPIAdapter] = {}
        self._metrics = {
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "avg_response_time": 0.0,
        }

    def register(self, name: str, adapter: BaseAPIAdapter):
        """Register an API adapter."""
        self.adapters[name] = adapter
        logger.info(f"Registered API adapter: {name}")

    def get(self, name: str) -> BaseAPIAdapter | None:
        """Get an API adapter by name."""
        return self.adapters.get(name)

    async def call(
        self, api_name: str, endpoint: str, method: str = "GET", **kwargs
    ) -> dict[str, Any]:
        """
        Call an API through the gateway.

        Args:
            api_name: API name.
            endpoint: API endpoint.
            method: HTTP method.
            **kwargs: Request parameters.

        Returns:
            API response data.
        """
        adapter = self.get(api_name)
        if not adapter:
            raise APIError(f"API adapter not found: {api_name}")

        start_time = datetime.now()
        self._metrics["total_calls"] += 1

        try:
            adapter._log_request(endpoint, method, kwargs)
            result = await adapter.call(endpoint, method, **kwargs)

            duration = (datetime.now() - start_time).total_seconds() * 1000
            adapter._log_response(endpoint, 200, duration)

            self._metrics["successful_calls"] += 1
            self._update_avg_response_time(duration)

            return result
        except APIError as e:
            self._metrics["failed_calls"] += 1
            logger.error(f"[{api_name}] API call failed: {e.message}")
            raise
        except Exception as e:
            self._metrics["failed_calls"] += 1
            logger.error(f"[{api_name}] Unknown error: {e}")
            raise APIError(str(e))

    def _update_avg_response_time(self, duration: float):
        """Update the average response time."""
        total = self._metrics["successful_calls"] + self._metrics["failed_calls"]
        if total > 0:
            self._metrics["avg_response_time"] = (
                self._metrics["avg_response_time"] * (total - 1) + duration
            ) / total

    def get_metrics(self) -> dict[str, Any]:
        """Get gateway metrics."""
        return self._metrics.copy()


# Global gateway instance
gateway = APIGateway()

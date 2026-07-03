"""Shared HTTP helpers for web search providers."""

from __future__ import annotations

from typing import Any

from ....llm.providers.proxy_utils import extract_connection_error, get_httpx_client_kwargs


def search_httpx_client_kwargs(*, timeout: float, target_url: str) -> dict[str, Any]:
    """Build an httpx client config using OpenAkita's proxy/NO_PROXY rules."""
    return get_httpx_client_kwargs(timeout=timeout, target_url=target_url)


def describe_httpx_failure(exc: BaseException) -> str:
    """Return the useful root-cause detail hidden inside httpx wrapper errors."""
    return extract_connection_error(exc)

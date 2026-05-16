"""Probe an LLM endpoint's actual model catalog.

Relay stations (oneapi / new-api / yunwu / private gateways) and
OpenAI-compatible aggregators almost always expose ``GET /v1/models``
(or a vendor-specific equivalent) which returns *exactly* the models
that gateway carries. The catalog is rarely the same as the official
provider's — that mismatch is the entire pain point this module exists
to solve.

This module is intentionally a small pure helper:

- :func:`probe_models` takes the bits it needs (``api_type``,
  ``base_url``, ``api_key``) and does ONE HTTP GET. No threading, no
  config-file IO, no LLM client construction.
- It returns a normalised ``list[str]`` of model ids that the UI / the
  ``LLMClient`` filter can match against ``EndpointConfig.model``.
- All error paths surface a typed :class:`ProbeError` so callers can
  distinguish "this provider has no public /models endpoint" (drop
  silently in batch jobs) from "the relay returned 401" (surface to UI).

The companion ``EndpointManager.sync_endpoint_models`` writes the
result back to ``llm_endpoints.json``.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from .types import normalize_base_url

logger = logging.getLogger(__name__)


# ─── Errors ──────────────────────────────────────────────────────────


class ProbeError(Exception):
    """Base error for endpoint model probing.

    ``user_message`` is what the UI should show to the user (Chinese);
    ``args[0]`` keeps the raw English/technical message for logs.
    """

    def __init__(self, message: str, *, user_message: str | None = None, status: int | None = None):
        super().__init__(message)
        self.user_message = user_message or message
        self.status = status


class ProbeUnsupported(ProbeError):
    """The endpoint's ``api_type`` has no public model-list route."""


class ProbeAuthError(ProbeError):
    """The relay rejected the API key (401 / 403)."""


class ProbeNetworkError(ProbeError):
    """Network / DNS / TLS failure reaching the relay."""


# ─── Endpoint URL resolution ─────────────────────────────────────────


def _models_url_for(api_type: str, base_url: str, provider: str = "") -> str | None:
    """Decide which URL to hit for a given (api_type, provider) pair.

    Returns ``None`` for provider/api_type combinations that have no
    documented public catalog endpoint; the caller raises
    :class:`ProbeUnsupported` in that case.

    The provider hint lets us pick the DashScope OpenAI-compat path
    (``/compatible-mode/v1/models``) over the bare ``/v1/models`` that
    OneAPI publishes — both endpoints exist but only the compat path
    on DashScope returns the catalog without an X-DashScope-* header.
    """
    base = normalize_base_url(base_url or "")
    if not base:
        return None
    provider_l = (provider or "").lower()
    api_l = (api_type or "").lower()

    # Aliyun Bailian / DashScope native — the official OpenAI-compat
    # entry is "/compatible-mode/v1/models" (see
    # plugins/happyhorse-video/happyhorse_dashscope_client.py:337).
    if provider_l.startswith("dashscope") and "dashscope.aliyuncs.com" in base:
        return f"{base.rstrip('/')}/compatible-mode/v1/models"

    if api_l in ("openai", "openai_responses"):
        return f"{base.rstrip('/')}/models"

    if api_l == "anthropic":
        # Anthropic's own API has no public /v1/models. Relays that
        # expose Claude through an OpenAI shim usually do, though, so
        # we still try /v1/models — but parse failure must downgrade
        # to ProbeUnsupported instead of looking like a bug.
        return f"{base.rstrip('/')}/models"

    return None


def _is_likely_html(body: str) -> bool:
    head = (body or "").lstrip()[:64].lower()
    return head.startswith(("<!doctype", "<html", "<head", "<body"))


def _parse_models_payload(payload: Any) -> list[str]:
    """Extract a flat ``list[str]`` of model ids from arbitrary shapes.

    Accepted shapes (any of):

    - ``{"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}`` (OpenAI)
    - ``{"data": ["gpt-4o", "gpt-4o-mini"]}`` (some relays)
    - ``{"models": [{"name": "claude-3.5"}]}`` (Anthropic-ish)
    - ``[{"id": "x"}, {"id": "y"}]`` (flat list at root)
    - ``[{"name": "x"}]`` / ``["x", "y"]`` (also flat)

    Items missing both ``id`` and ``name`` are skipped silently.
    Duplicates are removed but order is preserved (first wins).
    """
    items: list[Any] = []
    if isinstance(payload, dict):
        for key in ("data", "models", "list"):
            v = payload.get(key)
            if isinstance(v, list):
                items = v
                break
    elif isinstance(payload, list):
        items = payload

    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        if isinstance(it, str):
            name = it.strip()
        elif isinstance(it, dict):
            name = str(it.get("id") or it.get("name") or "").strip()
        else:
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


# ─── Public API ──────────────────────────────────────────────────────


def probe_models(
    *,
    api_type: str,
    base_url: str,
    api_key: str | None = None,
    provider: str = "",
    timeout: float = 15.0,
) -> list[str]:
    """Synchronously fetch the model catalog for one endpoint.

    Raises :class:`ProbeUnsupported`, :class:`ProbeAuthError`, or
    :class:`ProbeNetworkError` on the corresponding failure mode; the
    caller is expected to surface ``ProbeError.user_message`` to the
    UI directly and store nothing (so a stale catalog is not painted
    as "current").

    On success returns a list of model ids in the order the relay
    returned them. Empty list is a legitimate result (e.g. the relay
    explicitly says "no models"), it is NOT an error.
    """
    url = _models_url_for(api_type, base_url, provider)
    if not url:
        raise ProbeUnsupported(
            f"no /models route for api_type={api_type!r}",
            user_message="该 endpoint 协议不支持模型列表探测",
        )

    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        # OpenAI / DashScope-compat / most relays use Bearer; Anthropic
        # uses x-api-key. We add both so we don't have to special-case
        # niche relays that accept either — sending an extra header to
        # an endpoint that ignores it is harmless.
        headers["Authorization"] = f"Bearer {api_key}"
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"

    try:
        resp = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise ProbeNetworkError(
            f"timeout fetching {url}: {exc}",
            user_message=f"探测 {urlparse(url).netloc} 超时（>{timeout:.0f}s），请检查网络或代理",
        ) from exc
    except httpx.HTTPError as exc:
        raise ProbeNetworkError(
            f"network error fetching {url}: {exc}",
            user_message=f"无法访问 {urlparse(url).netloc}：{exc}",
        ) from exc

    body = resp.text or ""
    status = resp.status_code

    if status in (401, 403):
        raise ProbeAuthError(
            f"HTTP {status} from {url}: {body[:200]}",
            user_message=f"API Key 被中转站拒绝（HTTP {status}），请检查 Key / 计费是否正常",
            status=status,
        )
    if status == 404:
        raise ProbeUnsupported(
            f"HTTP 404 from {url}: endpoint has no /models route",
            user_message="该 endpoint 没有 /v1/models 路由，无法探测模型列表",
            status=status,
        )
    if status >= 400:
        raise ProbeError(
            f"HTTP {status} from {url}: {body[:200]}",
            user_message=f"探测失败 HTTP {status}：{body[:120]}",
            status=status,
        )

    # Some "free" or expired relays return an HTML 200 login page —
    # treat that as ProbeUnsupported instead of crashing JSON parse.
    if _is_likely_html(body):
        raise ProbeUnsupported(
            "endpoint returned an HTML page instead of JSON (login/captcha?)",
            user_message="endpoint 返回 HTML 页面（可能需要登录），不是模型列表",
            status=status,
        )

    try:
        payload = json.loads(body) if body else None
    except json.JSONDecodeError as exc:
        raise ProbeError(
            f"invalid JSON from {url}: {exc}; body={body[:200]}",
            user_message=f"endpoint 返回内容不是 JSON: {body[:100]}",
            status=status,
        ) from exc

    models = _parse_models_payload(payload)
    if not models and isinstance(payload, dict) and payload.get("error"):
        # Some relays return 200 + {"error": ...} for auth issues; bubble
        # it as ProbeError instead of a misleading "empty catalog".
        err_msg = str(payload["error"])[:200]
        raise ProbeError(
            f"relay returned error in 200 body: {err_msg}",
            user_message=f"endpoint 返回错误: {err_msg}",
            status=status,
        )
    return models


__all__ = [
    "ProbeAuthError",
    "ProbeError",
    "ProbeNetworkError",
    "ProbeUnsupported",
    "probe_models",
]

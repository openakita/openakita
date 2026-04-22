"""
OpenAI Provider

Supports calls using the OpenAI API format, including:
- OpenAI official API
- DashScope (Tongyi Qianwen)
- Kimi (Moonshot AI)
- OpenRouter
- SiliconFlow
- Yunwu API
- Other OpenAI-compatible APIs
"""

import json
import logging
from collections.abc import AsyncIterator
from json import JSONDecodeError

import httpx

from ..cache import build_cached_system_blocks
from ..converters.messages import convert_messages_to_openai
from ..converters.tools import (
    convert_tool_calls_from_openai,
    convert_tools_to_openai,
    has_text_tool_calls,
    parse_text_tool_calls,
)
from ..model_registry import get_model_capabilities
from ..types import (
    AuthenticationError,
    EndpointConfig,
    LLMError,
    LLMRequest,
    LLMResponse,
    RateLimitError,
    StopReason,
    TextBlock,
    ToolUseBlock,
    Usage,
    normalize_base_url,
)
from .base import LLMProvider
from .proxy_utils import build_httpx_timeout, get_httpx_transport, get_proxy_config

logger = logging.getLogger(__name__)


def _is_stream_only_error(error: str) -> bool:
    """Detect whether an error indicates the endpoint only supports streaming requests (stream-only relay)."""
    err_lower = error.lower()
    return (
        "stream must be set to true" in err_lower
        or "stream is required" in err_lower
        or ("text/event-stream" in err_lower and "invalid json" in err_lower)
    )


def _humanize_upstream_error(status: int, body: str) -> str:
    """把云端 LLM 的英文错误转成对小白用户更友好的中文摘要。

    原始 body 仍会通过 logger.error 留档以便排查；这里只控制传播给用户那条
    LLMError 的 message。完全找不到匹配时回退到一个通用 HTTP 提示。

    例外：如果是 stream-only relay（"stream must be set to true" 等），
    必须保留原文，以便 chat() 的 except 分支识别后自动切到流式重试。
    """
    if _is_stream_only_error(body or ""):
        return body or f"API error ({status})"
    body_l = (body or "").lower()
    if (
        "invalidparameter" in body_l
        and (
            "url" in body_l
            or "image" in body_l
            or "vision" in body_l
        )
    ):
        return "云端模型未能访问到您发送的图片（图片需可公网访问或采用内嵌方式），请稍后重试或更换更小的图片"
    if status == 401 or "authenticationerror" in body_l or "invalid api key" in body_l:
        return "API Key 无效或已过期，请到设置中心检查模型端点凭据"
    if status == 429 or "rate limit" in body_l:
        return "调用频率已超过上游限制，请稍后再试"
    if "insufficientquota" in body_l or "insufficient_quota" in body_l or "balance" in body_l:
        return "云端账户余额不足或额度已用尽，请充值后再继续使用"
    if status == 408 or "timeout" in body_l:
        return "云端响应超时，请稍后重试或换个模型"
    if status == 404 or "modelnotfound" in body_l or "model not found" in body_l:
        return "目标模型不存在或当前账号无权限调用该模型"
    if status >= 500:
        return f"云端服务暂时不可用 (HTTP {status})，请稍后重试"
    return f"云端模型调用失败 (HTTP {status})"


class _BearerAuth(httpx.Auth):
    """Bearer token auth that persists across cross-origin redirects.

    httpx strips the Authorization header on cross-origin redirects for security.
    Some OpenAI-compatible gateways (e.g., GitCode api-ai) internally redirect to
    a different host, causing the token to be lost and a 401 response.
    Using httpx's auth mechanism re-attaches credentials after every redirect.
    """

    def __init__(self, token: str):
        self.token = token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible API Provider"""

    def __init__(self, config: EndpointConfig):
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int | None = None  # Records the event loop ID used when the client was created
        self._stream_only: bool = config.stream_only

    @property
    def api_key(self) -> str:
        """Get the API Key"""
        return self.config.get_api_key() or ""

    @property
    def base_url(self) -> str:
        """Get the base URL, automatically stripping OpenAI-compatible endpoint path suffixes the user may have accidentally pasted."""
        return normalize_base_url(self.config.base_url)

    @property
    def _api_url(self) -> str:
        """Full API endpoint URL; subclasses can override to switch protocols (e.g. Responses API)."""
        return f"{self.base_url}/chat/completions"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client.

        Note: httpx.AsyncClient is bound to the event loop it was created on.
        If the event loop changes (e.g. a scheduled task creates a new loop), the client must be recreated.
        """
        import asyncio

        try:
            current_loop = asyncio.get_running_loop()
            current_loop_id = id(current_loop)
        except RuntimeError:
            current_loop_id = None

        # Check whether the client needs to be recreated
        need_recreate = (
            self._client is None
            or self._client.is_closed
            or self._client_loop_id != current_loop_id
        )

        if need_recreate:
            # Safely close the old client
            if self._client is not None and not self._client.is_closed:
                try:
                    await self._client.aclose()
                except Exception:
                    pass  # Ignore close errors

            # Get proxy and network configuration
            proxy = get_proxy_config()
            transport = get_httpx_transport()  # IPv4-only support
            is_local = self._is_local_endpoint()

            # Automatically extend read timeout for local endpoints (Ollama, etc.)
            # Local inference is bound by CPU/GPU resources, making inference much slower than cloud APIs.
            # The default read timeout can cause frequent timeouts that are misclassified as failures.
            timeout_value = self.config.timeout
            if is_local:
                base_timeout = build_httpx_timeout(timeout_value, default=60.0)
                current_read = (
                    base_timeout.read if isinstance(base_timeout, httpx.Timeout) else 60.0
                )
                if current_read < 300.0:
                    timeout_value = {"read": 300.0, "connect": 30.0, "write": 30.0, "pool": 30.0}
                    logger.info(
                        f"[OpenAI] Local endpoint '{self.name}': auto-increased read timeout "
                        f"from {current_read}s to 300s (local inference is slower)"
                    )

            # httpx strips Authorization on cross-origin redirects for security.
            # Some OpenAI-compatible gateways (e.g., GitCode api-ai) internally redirect
            # to a different host. Event hooks fire on EVERY request including redirects,
            # so we use one to re-attach the credential that _build_redirect_request strips.
            api_key_for_hook = (self.api_key or "").strip()
            if not api_key_for_hook and is_local:
                api_key_for_hook = "local"

            async def _ensure_auth_on_redirect(request: httpx.Request):
                if api_key_for_hook and "Authorization" not in request.headers:
                    request.headers["Authorization"] = f"Bearer {api_key_for_hook}"

            # trust_env=False: proxies are managed explicitly by get_proxy_config() (with reachability checks).
            # This avoids lingering system proxies on macOS/Windows (Clash/V2Ray, etc.) from routing
            # requests to nonexistent proxy ports and causing failures.
            client_kwargs = {
                "timeout": build_httpx_timeout(timeout_value, default=60.0),
                "follow_redirects": True,
                "trust_env": False,
                "event_hooks": {"request": [_ensure_auth_on_redirect]},
            }

            if proxy and not is_local:
                client_kwargs["proxy"] = proxy
                logger.debug(f"[OpenAI] Using proxy: {proxy}")

            if transport:
                client_kwargs["transport"] = transport

            self._client = httpx.AsyncClient(**client_kwargs)
            self._client_loop_id = current_loop_id

        return self._client

    def _estimate_request_timeout(self, body: dict) -> httpx.Timeout | None:
        """Dynamically compute the timeout based on request body size.

        For large-context scenarios (~>60K estimated tokens), the default read timeout
        may not be enough and must be scaled up to avoid futile retries caused by
        frequent ReadTimeouts.

        Returns:
            An httpx.Timeout or None (when no override is needed).
        """
        messages = body.get("messages", [])
        body_chars = sum(
            len(str(m.get("content", ""))) + len(str(m.get("tool_calls", ""))) for m in messages
        )
        tools = body.get("tools", [])
        if tools:
            body_chars += sum(len(str(t)) for t in tools)

        est_tokens = body_chars // 2  # Chinese is roughly 2 chars/token
        if est_tokens < 30_000:
            return None

        base_timeout = self.config.timeout or 180
        scale = min(est_tokens / 30_000, 3.0)  # Up to 3x
        new_read = base_timeout * scale
        new_read = min(new_read, 540.0)  # Cap at 9 minutes
        if new_read <= base_timeout * 1.1:
            return None

        logger.info(
            f"[OpenAI] '{self.name}': large context (~{est_tokens // 1000}k tokens est.), "
            f"scaling read timeout {base_timeout}s → {new_read:.0f}s"
        )
        return httpx.Timeout(
            connect=min(10.0, new_read),
            read=new_read,
            write=min(30.0, new_read),
            pool=min(30.0, new_read),
        )

    async def chat(self, request: LLMRequest) -> LLMResponse:
        """Send a chat request (with automatic detection of stream-only endpoints)."""
        await self.acquire_rate_limit()

        if self._stream_only:
            return await self._chat_via_stream(request)

        try:
            return await self._chat_non_stream(request)
        except (AuthenticationError, RateLimitError):
            raise
        except LLMError as e:
            if _is_stream_only_error(str(e)):
                logger.info(
                    f"[OpenAI] '{self.name}': detected stream-only endpoint, "
                    f"retrying with streaming transport"
                )
                self._stream_only = True
                return await self._chat_via_stream(request)
            raise

    async def _chat_non_stream(self, request: LLMRequest) -> LLMResponse:
        """Non-streaming request implementation (original path, logic unchanged). The caller must have already acquired the rate limit."""
        client = await self._get_client()

        body = self._build_request_body(request)

        logger.debug(f"OpenAI request to {self.base_url}: model={body.get('model')}")

        req_timeout = self._estimate_request_timeout(body)

        try:
            response = await client.post(
                self._api_url,
                headers=self._build_headers(),
                json=body,
                **({"timeout": req_timeout} if req_timeout else {}),
            )

            if response.status_code >= 400:
                body = (response.text or "")[:500]
                logger.error(
                    "[OpenAIProvider] upstream non-stream error status=%s body=%s",
                    response.status_code, body[:1000],
                )
                if response.status_code == 401:
                    raise AuthenticationError(
                        _humanize_upstream_error(401, body), status_code=401
                    )
                if response.status_code == 429:
                    raise RateLimitError(
                        _humanize_upstream_error(429, body), status_code=429
                    )
                raise LLMError(
                    _humanize_upstream_error(response.status_code, body),
                    status_code=response.status_code,
                )

            try:
                data = response.json()
            except JSONDecodeError:
                content_type = response.headers.get("content-type", "")
                body_preview = (response.text or "")[:500]
                raise LLMError(
                    "Invalid JSON response from OpenAI-compatible endpoint "
                    f"(status={response.status_code}, content-type={content_type}, "
                    f"body_preview={body_preview!r})"
                )

            # Some OpenAI-compatible APIs return errors inside an HTTP 200 response body (not via standard HTTP status codes)
            if "error" in data and data["error"]:
                err_obj = (
                    data["error"]
                    if isinstance(data["error"], dict)
                    else {"message": str(data["error"])}
                )
                err_msg = err_obj.get("message", str(err_obj))
                err_code = err_obj.get("code", "")
                logger.warning(
                    f"[OpenAI] '{self.name}': API returned 200 with error in body: "
                    f"code={err_code}, message={err_msg}"
                )
                raise LLMError(f"API error in response body: {err_msg}")

            # HTTP 200 but empty choices — anomalous behavior from some relay/compatible APIs
            choices = data.get("choices")
            if not choices:
                body_preview = json.dumps(data, ensure_ascii=False)[:500]
                logger.warning(
                    f"[OpenAI] '{self.name}': API returned 200 but choices is empty. "
                    f"Response preview: {body_preview}"
                )
                self.mark_unhealthy(
                    f"Empty choices in 200 response (model={data.get('model', '?')})",
                    is_local=self._is_local_endpoint(),
                )
                raise LLMError(
                    f"API returned empty response (no choices) from '{self.name}'. "
                    f"This usually indicates the model is unavailable, rate-limited, "
                    f"or the API key lacks permission. Response: {body_preview}"
                )

            self.mark_healthy()
            return self._parse_response(data)

        except httpx.TimeoutException as e:
            detail = f"{type(e).__name__}: {e}"
            self.mark_unhealthy(f"Timeout: {detail}", is_local=self._is_local_endpoint())
            raise LLMError(f"Request timeout: {detail}")
        except httpx.RequestError as e:
            detail = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}({repr(e)})"
            self.mark_unhealthy(f"Request error: {detail}", is_local=self._is_local_endpoint())
            raise LLMError(f"Request failed: {detail}")

    async def _iter_sse_events(self, body: dict) -> AsyncIterator[dict]:
        """SSE transport layer: sends a streaming request, parses SSE lines, yields converted events.

        ``body`` must already include ``"stream": True``. The caller must have already acquired the rate limit.
        Subclasses can override this to adapt to different SSE formats (e.g. named events in the Responses API).
        """
        client = await self._get_client()
        req_timeout = self._estimate_request_timeout(body)

        try:
            async with client.stream(
                "POST",
                self._api_url,
                headers=self._build_headers(),
                json=body,
                **({"timeout": req_timeout} if req_timeout else {}),
            ) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    error_text = error_body.decode(errors="replace")[:500]
                    logger.error(
                        "[OpenAIProvider] upstream stream error status=%s body=%s",
                        response.status_code, error_text,
                    )
                    if response.status_code == 401:
                        raise AuthenticationError(
                            _humanize_upstream_error(401, error_text),
                            status_code=401,
                        )
                    if response.status_code == 429:
                        raise RateLimitError(
                            _humanize_upstream_error(429, error_text),
                            status_code=429,
                        )
                    raise LLMError(
                        _humanize_upstream_error(response.status_code, error_text),
                        status_code=response.status_code,
                    )

                has_content = False
                first_line_raw = None
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    if first_line_raw is None:
                        first_line_raw = line

                    if line.startswith("data: "):
                        data = line[6:]
                        if data.strip() and data != "[DONE]":
                            try:
                                event = json.loads(data)
                                has_content = True
                                converted = self._convert_stream_event(event)
                                if isinstance(converted, list):
                                    for ev in converted:
                                        yield ev
                                else:
                                    yield converted
                            except json.JSONDecodeError:
                                continue
                    elif not has_content and not line.startswith(":"):
                        try:
                            err_data = json.loads(line)
                            if "error" in err_data:
                                err_obj = err_data["error"]
                                err_msg = (
                                    err_obj.get("message", str(err_obj))
                                    if isinstance(err_obj, dict)
                                    else str(err_obj)
                                )
                                raise LLMError(f"Stream error from '{self.name}': {err_msg}")
                        except json.JSONDecodeError:
                            if "error" in line.lower():
                                raise LLMError(f"Stream error from '{self.name}': {line[:500]}")

                if has_content:
                    self.mark_healthy()
                else:
                    preview = (first_line_raw or "")[:300]
                    logger.warning(
                        f"[OpenAI] '{self.name}': stream returned 200 but no content chunks. "
                        f"First line: {preview!r}"
                    )
                    self.mark_unhealthy(
                        f"Empty stream response (model={body.get('model', '?')})",
                        is_local=self._is_local_endpoint(),
                    )
                    raise LLMError(
                        f"Stream returned empty response from '{self.name}'. "
                        f"Model may be unavailable or rate-limited."
                    )

        except httpx.TimeoutException as e:
            detail = f"{type(e).__name__}: {e}"
            self.mark_unhealthy(f"Timeout: {detail}", is_local=self._is_local_endpoint())
            raise LLMError(f"Stream timeout: {detail}")
        except httpx.RequestError as e:
            detail = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}({repr(e)})"
            self.mark_unhealthy(
                f"Stream request error: {detail}", is_local=self._is_local_endpoint()
            )
            raise LLMError(f"Stream request failed: {detail}")

    async def _chat_via_stream(self, request: LLMRequest) -> LLMResponse:
        """Streaming-to-synchronous response adapter: collects streaming events and assembles them into an LLMResponse.

        Used for stream-only endpoints (e.g. Codex relay). The caller must have already acquired the rate limit.
        """
        body = self._build_request_body(request)
        body["stream"] = True

        text_parts: list[str] = []
        tool_calls: dict[str, dict] = {}
        current_tool_id: str | None = None
        stop_reason = StopReason.END_TURN
        response_model = self.config.model

        async for event in self._iter_sse_events(body):
            event_type = event.get("type")

            if event_type == "content_block_delta":
                delta = event.get("delta", {})
                delta_type = delta.get("type")

                if delta_type == "text":
                    text_parts.append(delta.get("text", ""))
                elif delta_type == "tool_use":
                    call_id = delta.get("id")
                    if call_id:
                        if call_id not in tool_calls:
                            tool_calls[call_id] = {
                                "name": delta.get("name") or "",
                                "arguments": "",
                            }
                        elif delta.get("name") and not tool_calls[call_id]["name"]:
                            tool_calls[call_id]["name"] = delta["name"]
                        current_tool_id = call_id
                    target_id = call_id or current_tool_id
                    if target_id and target_id in tool_calls:
                        tool_calls[target_id]["arguments"] += delta.get("arguments") or ""

            elif event_type == "message_stop":
                raw_reason = event.get("stop_reason", "stop")
                _stop_map = {
                    "stop": StopReason.END_TURN,
                    "length": StopReason.MAX_TOKENS,
                    "tool_calls": StopReason.TOOL_USE,
                    "function_call": StopReason.TOOL_USE,
                }
                stop_reason = _stop_map.get(raw_reason, StopReason.END_TURN)

            elif event_type == "error":
                raise LLMError(f"Stream error from '{self.name}': {event.get('error', 'unknown')}")

        content_blocks: list = []
        text = "".join(text_parts)
        if text:
            content_blocks.append(TextBlock(text=text))

        for call_id, tc in tool_calls.items():
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": tc["arguments"]}
            content_blocks.append(
                ToolUseBlock(
                    id=call_id,
                    name=tc["name"],
                    input=args,
                )
            )

        if tool_calls and stop_reason != StopReason.MAX_TOKENS:
            stop_reason = StopReason.TOOL_USE

        return LLMResponse(
            id="",
            content=content_blocks,
            stop_reason=stop_reason,
            usage=Usage(),
            model=response_model,
        )

    async def chat_stream(self, request: LLMRequest) -> AsyncIterator[dict]:
        """Streaming chat request."""
        await self.acquire_rate_limit()
        body = self._build_request_body(request)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        async for event in self._iter_sse_events(body):
            yield event

    def _is_local_endpoint(self) -> bool:
        """Check whether this is a local endpoint (Ollama, LM Studio, etc.)."""
        url = self.base_url.lower()
        return any(
            host in url
            for host in (
                "localhost",
                "127.0.0.1",
                "0.0.0.0",
                "[::1]",
            )
        )

    def _get_auth(self) -> _BearerAuth:
        """Get authentication info (via httpx's Auth mechanism to ensure credentials are not lost on redirects)."""
        api_key = (self.api_key or "").strip()
        if not api_key:
            if self._is_local_endpoint():
                api_key = "local"
            else:
                hint = ""
                if self.config.api_key_env:
                    hint = f" (env var {self.config.api_key_env} is not set)"
                raise AuthenticationError(
                    f"Missing API key for endpoint '{self.name}'{hint}. "
                    "Set the environment variable or configure api_key/api_key_env."
                )
        return _BearerAuth(api_key)

    def _build_headers(self) -> dict:
        """Build request headers (including Authorization, without relying on httpx's auth mechanism)."""
        api_key = (self.api_key or "").strip()
        if not api_key:
            if self._is_local_endpoint():
                api_key = "local"
            else:
                hint = ""
                if self.config.api_key_env:
                    hint = f" (env var {self.config.api_key_env} is not set)"
                raise AuthenticationError(
                    f"Missing API key for endpoint '{self.name}'{hint}. "
                    "Set the environment variable or configure api_key/api_key_env."
                )

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        if "openrouter" in self.base_url.lower():
            headers["HTTP-Referer"] = "https://github.com/openakita"
            headers["X-Title"] = "OpenAkita"

        return headers

    def _build_request_body(self, request: LLMRequest) -> dict:
        """Build the request body."""
        # Convert message format (pass provider so video and other multimedia content is handled correctly)
        thinking_enabled = request.enable_thinking and self.config.has_capability("thinking")

        # Thinking-only models (deepseek-reasoner, QwQ, etc.) cannot disable thinking.
        # Even if fallback downgraded enable_thinking to False,
        # we still must inject reasoning_content and keep thinking enabled, otherwise the API returns 400.
        is_always_thinking = False
        if not thinking_enabled and self.config.has_capability("thinking"):
            from ..capabilities import is_thinking_only

            is_always_thinking = is_thinking_only(
                self.config.model,
                provider_slug=self.config.provider,
            )
            if is_always_thinking:
                thinking_enabled = True

        messages = convert_messages_to_openai(
            request.messages,
            request.system,
            provider=self.config.provider,
            enable_thinking=thinking_enabled,
        )

        body = {
            "model": self.config.model,
            "messages": messages,
        }

        # max_tokens handling strategy:
        # Ideally, omitting max_tokens would let the API use the model's default cap, but in practice
        # some OpenAI-compatible APIs (e.g. NVIDIA NIM) default max_tokens to an extremely low value
        # (~200). With thinking enabled, the entire output budget is consumed by thinking content,
        # leaving no visible text in the response.
        # Therefore: if the caller passes max_tokens > 0, use it directly; otherwise use the endpoint
        # configuration value, falling back to 16384.
        #
        # Special case — OpenAI o1/o3/o4 reasoning models:
        # These models reject the max_tokens parameter and require max_completion_tokens.
        # Detection: model name contains "o1-"/"o3-"/"o4-" and provider is openai.
        _model_lower = self.config.model.lower()
        _is_openai_reasoning = self.config.provider == "openai" and any(
            tag in _model_lower for tag in ("o1-", "o3-", "o4-", "/o1", "/o3", "/o4")
        )
        _token_key = "max_completion_tokens" if _is_openai_reasoning else "max_tokens"

        _max_tokens = request.max_tokens
        if _max_tokens and _max_tokens > 0:
            body[_token_key] = _max_tokens
        else:
            _fallback = self.config.max_tokens or 16384
            body[_token_key] = _fallback

        # Tools
        if request.tools:
            body["tools"] = convert_tools_to_openai(request.tools)
            body["tool_choice"] = "auto"

        # Temperature
        if request.temperature != 1.0:
            body["temperature"] = request.temperature

        # Stop sequences
        if request.stop_sequences:
            body["stop"] = request.stop_sequences

        # Extra parameters (provider-specific)
        if self.config.extra_params:
            body.update(self.config.extra_params)
        if request.extra_params:
            body.update(request.extra_params)

        # ── Local endpoint detection ──
        # Local inference engines such as Ollama / LM Studio do not support the OpenAI-style
        # nested thinking: {"type": "enabled"} parameter, but Ollama 0.9+ supports
        # enable_thinking (bool) to control the thinking mode of dual-mode models (e.g. qwen3.5).
        is_local = self._is_local_endpoint()

        # DashScope thinking mode — must come after extra_params to override any enable_thinking set there
        if self.config.provider == "dashscope" and self.config.has_capability("thinking"):
            ds_thinking = bool(request.enable_thinking)
            if not ds_thinking and is_always_thinking:
                ds_thinking = True
            body["enable_thinking"] = ds_thinking
            if ds_thinking and request.thinking_depth:
                budget_map = {"low": 1024, "medium": 4096, "high": 16384}
                budget = budget_map.get(request.thinking_depth)
                if budget:
                    body["thinking_budget"] = budget
            elif not ds_thinking:
                body.pop("thinking_budget", None)

        # SiliconFlow thinking mode
        #
        # The SiliconFlow API has two classes of thinking models (see official docs):
        #
        # Class A - Dual-mode models (support toggling via enable_thinking):
        #   Qwen3 series, Hunyuan-A13B, GLM-4.6V/4.5V, DeepSeek-V3.1/V3.2 series
        #   → send enable_thinking (bool) + thinking_budget
        #
        # Class B - Always-thinking models (always think, do not accept enable_thinking):
        #   Kimi-K2-Thinking, DeepSeek-R1, QwQ-32B, GLM-Z1 series
        #   → only send thinking_budget to control depth; do not send enable_thinking
        #   → sending enable_thinking to these models causes a 400:
        #     "Value error, current model does not support parameter enable_thinking"
        #
        # Neither class supports the OpenAI-style thinking: {"type": "enabled"} + reasoning_effort
        elif self.config.provider in (
            "siliconflow",
            "siliconflow-intl",
        ) and self.config.has_capability("thinking"):
            from ..capabilities import is_thinking_only

            sf_thinking_only = is_thinking_only(
                self.config.model, provider_slug=self.config.provider
            )

            if sf_thinking_only:
                # Class B: always-thinking models — only thinking_budget is allowed to control depth.
                # Must strip any enable_thinking that may have leaked through extra_params.
                body.pop("enable_thinking", None)
                if request.thinking_depth:
                    budget_map = {"low": 1024, "medium": 4096, "high": 16384}
                    budget = budget_map.get(request.thinking_depth)
                    if budget:
                        body["thinking_budget"] = budget
            else:
                # Class A: dual-mode models — toggle via enable_thinking + thinking_budget
                body["enable_thinking"] = bool(request.enable_thinking)
                if request.enable_thinking:
                    if request.thinking_depth:
                        budget_map = {"low": 1024, "medium": 4096, "high": 16384}
                        budget = budget_map.get(request.thinking_depth)
                        if budget:
                            body["thinking_budget"] = budget
                else:
                    body.pop("thinking_budget", None)

            # Strip OpenAI-style parameters not applicable to SiliconFlow (may have been introduced via extra_params)
            body.pop("thinking", None)
            body.pop("reasoning_effort", None)

        # Local endpoint thinking mode (Ollama 0.9+, etc.)
        #
        # Ollama 0.9+'s OpenAI-compatible API supports enable_thinking (bool) to toggle
        # the thinking mode of dual-mode models (e.g. qwen3.5). Thinking-only models (e.g. qwen3)
        # emit their thinking content via <think> tags and need no API-level parameter control.
        # Does not use the OpenAI-style thinking: {"type": "enabled"} or reasoning_effort.
        elif is_local and self.config.has_capability("thinking"):
            if request.enable_thinking:
                body["enable_thinking"] = True

        # OpenRouter thinking mode
        #
        # OpenRouter uses its own reasoning API (incompatible with OpenAI thinking / DashScope enable_thinking):
        #   Request: reasoning: {"effort": "high"} or {"enabled": true}
        #   Response: message.reasoning (str) contains the reasoning trace
        # Docs: https://openrouter.ai/docs/use-cases/reasoning-tokens
        elif self.config.provider == "openrouter" and self.config.has_capability("thinking"):
            body.pop("enable_thinking", None)
            body.pop("thinking", None)
            body.pop("reasoning_effort", None)

            if request.enable_thinking or is_always_thinking:
                depth_map = {"low": "low", "medium": "medium", "high": "high"}
                effort = depth_map.get(request.thinking_depth or "medium", "medium")
                body["reasoning"] = {"effort": effort}
            else:
                body.pop("reasoning", None)

        # OpenAI-compatible endpoint thinking mode (Volcengine/DeepSeek/vLLM, etc.)
        #
        # Background:
        # - Native OpenAI o1/o3 series are inherently thinking models, so only reasoning_effort is needed to control depth.
        # - Other OpenAI-compatible endpoints (Volcengine/DeepSeek/vLLM, etc.) require explicitly passing
        #   thinking: {"type": "enabled"} to enable thinking mode; reasoning_effort is only an optional depth control.
        # - Sending only reasoning_effort without enabling thinking causes Volcengine and similar APIs to return 400:
        #   "Invalid combination of reasoning_effort and thinking type: medium + disabled"
        #
        # Excludes: DashScope, SiliconFlow, local endpoints, OpenRouter (each handled above)
        elif self.config.has_capability("thinking") and not is_local:
            body.pop("enable_thinking", None)

            if request.enable_thinking or is_always_thinking:
                if "thinking" not in body:
                    body["thinking"] = {"type": "enabled"}
                if request.thinking_depth:
                    depth_map = {"low": "low", "medium": "medium", "high": "high"}
                    effort = depth_map.get(request.thinking_depth)
                    if effort:
                        body["reasoning_effort"] = effort
            else:
                body.pop("reasoning_effort", None)
                if "thinking" in body:
                    body["thinking"] = {"type": "disabled"}

        # ── Local endpoint cleanup ──
        # Remove thinking-related parameters that may have leaked via extra_params and are unsupported by local engines.
        # enable_thinking (bool) is not on this list: Ollama 0.9+ supports it natively,
        # and other local engines (LM Studio / older Ollama) silently ignore unknown simple fields.
        if is_local:
            _stripped = [
                k for k in ("thinking", "thinking_budget", "reasoning_effort") if k in body
            ]
            for _key in _stripped:
                body.pop(_key, None)
            if _stripped:
                logger.debug(
                    f"[OpenAI] Local endpoint '{self.name}': stripped thinking params {_stripped}"
                )

        # ── Endpoint-level thinking parameter stripping ──
        # If the endpoint has previously returned 400 due to thinking/reasoning_effort,
        # the client's self-healing logic marks _thinking_params_unsupported on the provider.
        # This serves as a final safety net ensuring no thinking-related parameters are sent.
        if getattr(self, "_thinking_params_unsupported", False):
            for _tp in ("thinking", "reasoning_effort", "enable_thinking", "thinking_budget"):
                body.pop(_tp, None)

        # ── Request body sanity check ──
        # body.update() from extra_params is a blind overwrite and may replace carefully computed
        # parameters (such as max_tokens) with invalid values. Do a final validation before return
        # to ensure the outgoing request body is always valid.
        for _tk in ("max_tokens", "max_completion_tokens"):
            _tv = body.get(_tk)
            if _tv is not None and (not isinstance(_tv, int) or _tv <= 0):
                body.pop(_tk, None)

        # ── DashScope Explicit Prompt Cache ──
        # DashScope OpenAI 兼容模式支持 Anthropic 风格的 cache_control 字段，
        # 命中后输入 token 按 20% 计费、TTFT 显著降低。激活条件：
        #   1) provider == "dashscope"
        #   2) 模型在 model_registry 中标记 supports_cache=True
        #   3) system prompt 含 SYSTEM_PROMPT_DYNAMIC_BOUNDARY 切割标记
        # 仅切割 system 一处即可（DashScope cache 走 message content blocks，
        # tools 数组层面不接受 cache_control）。命中情况通过 _parse_response /
        # 流式 chunk_usage 中的 prompt_tokens_details.cached_tokens 统计。
        if self.config.provider == "dashscope":
            try:
                _caps = get_model_capabilities(self.config.model)
                if _caps.supports_cache and body.get("messages"):
                    _msgs = body["messages"]
                    if _msgs and _msgs[0].get("role") == "system":
                        _sys_content = _msgs[0].get("content")
                        if isinstance(_sys_content, str) and _sys_content:
                            _blocks = build_cached_system_blocks(_sys_content)
                            if _blocks:
                                _msgs[0] = {"role": "system", "content": _blocks}
            except Exception as _cache_err:
                logger.debug(
                    f"[CACHE] DashScope cache_control injection skipped: {_cache_err}"
                )

        return body

    def _parse_response(self, data: dict) -> LLMResponse:
        """Parse the response."""
        choices = data.get("choices", [])
        if not choices:
            return LLMResponse(
                id=data.get("id", ""),
                content=[],
                stop_reason=StopReason.END_TURN,
                usage=Usage(),
                model=data.get("model", self.config.model),
            )

        choice = choices[0]
        message = choice.get("message", {})
        content_blocks = []
        has_tool_calls = False

        # Text content — accept both string and array formats
        # Some OpenAI-compatible APIs (e.g. Google Gemini OpenAI-compat) return content as an array:
        #   [{"type": "text", "text": "..."}, ...]
        raw_content = message.get("content")
        if isinstance(raw_content, list):
            text_content = ""
            for part in raw_content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_content += part.get("text", "")
                elif isinstance(part, str):
                    text_content += part
            if not text_content and raw_content:
                logger.warning(
                    f"[PARSE] content is list but no text parts extracted: "
                    f"types={[p.get('type') if isinstance(p, dict) else type(p).__name__ for p in raw_content[:3]]}"
                )
        else:
            text_content = raw_content or ""

        # Native tool calls
        tool_calls = message.get("tool_calls", [])
        if tool_calls:
            converted = convert_tool_calls_from_openai(tool_calls)
            if converted:
                content_blocks.extend(converted)
                has_tool_calls = True
            logger.info(
                f"[TOOL_CALLS] Received {len(tool_calls)} native tool calls from {self.name}"
            )
            # Fault-tolerance log: tool_calls present but none converted (usually due to non-standard fields from compatibility gateways)
            if not converted:
                try:
                    first = tool_calls[0] if isinstance(tool_calls, list) and tool_calls else {}
                    func = (first.get("function") or {}) if isinstance(first, dict) else {}
                    logger.warning(
                        "[TOOL_CALLS] tool_calls present but none converted "
                        f"(first.type={getattr(first, 'get', lambda *_: None)('type') if isinstance(first, dict) else type(first)}, "
                        f"first.function.name={func.get('name') if isinstance(func, dict) else None}, "
                        f"first.function.arguments_type={type(func.get('arguments')).__name__ if isinstance(func, dict) else None})"
                    )
                except Exception:
                    pass

        # Text-format tool call parsing (fallback)
        # When the model does not support native tool calls, parse the <function_calls> format embedded in text.
        # Also check whether reasoning_content contains embedded tool calls.
        _tool_calls_from_reasoning = False
        combined_for_check = text_content
        # reasoning_content: DeepSeek/Kimi etc. use the reasoning_content field
        # reasoning: OpenRouter uses the reasoning field (a string, or an object containing content)
        reasoning_content = message.get("reasoning_content") or ""
        if not reasoning_content:
            _or_reasoning = message.get("reasoning")
            if isinstance(_or_reasoning, str) and _or_reasoning:
                reasoning_content = _or_reasoning
            elif isinstance(_or_reasoning, dict):
                reasoning_content = _or_reasoning.get("content", "") or ""
        if not has_tool_calls and not text_content and reasoning_content:
            if has_text_tool_calls(reasoning_content):
                combined_for_check = reasoning_content
                _tool_calls_from_reasoning = True
                logger.info(
                    f"[TEXT_TOOL_PARSE] Detected tool calls embedded in reasoning_content from {self.name}"
                )

        if not has_tool_calls and combined_for_check and has_text_tool_calls(combined_for_check):
            logger.info(f"[TEXT_TOOL_PARSE] Detected text-based tool calls from {self.name}")
            clean_text, text_tool_calls = parse_text_tool_calls(combined_for_check)

            if text_tool_calls:
                if _tool_calls_from_reasoning:
                    if clean_text.strip():
                        text_content = clean_text
                        logger.info(
                            f"[TEXT_TOOL_PARSE] Preserved {len(clean_text)} chars of clean_text "
                            f"from reasoning_content"
                        )
                else:
                    text_content = clean_text
                content_blocks.extend(text_tool_calls)
                has_tool_calls = True
                logger.info(
                    f"[TEXT_TOOL_PARSE] Extracted {len(text_tool_calls)} tool calls "
                    f"from {'reasoning_content' if _tool_calls_from_reasoning else 'text'}"
                )

        # Reasoning model fallback: content is empty but reasoning has content.
        # When a reasoning model is truncated by max_tokens, all output may live in the reasoning field
        # while content is empty. As a fallback, try to extract structured content from reasoning.
        if not text_content and not has_tool_calls and reasoning_content:
            import re

            yaml_match = re.search(
                r"```(?:yaml)?\s*\n(.+?)```",
                reasoning_content,
                re.DOTALL,
            )
            if yaml_match:
                text_content = yaml_match.group(1).strip()
                logger.warning(
                    f"[PARSE] content is empty but found structured data in reasoning "
                    f"({len(text_content)} chars extracted from {len(reasoning_content)} chars reasoning)"
                )
            else:
                logger.warning(
                    f"[PARSE] content is empty, reasoning has {len(reasoning_content)} chars "
                    f"but no extractable structured content. Response may be truncated "
                    f"(model exhausted max_tokens on reasoning before producing content)."
                )

        # Add text content
        if text_content:
            content_blocks.insert(0, TextBlock(text=text_content))

        # Parse stop reason
        finish_reason = choice.get("finish_reason", "stop")
        if has_tool_calls and finish_reason == "length":
            # finish_reason=length + tool_calls = output truncated, tool arguments may be incomplete
            stop_reason = StopReason.MAX_TOKENS
        elif has_tool_calls:
            stop_reason = StopReason.TOOL_USE
        else:
            stop_reason_map = {
                "stop": StopReason.END_TURN,
                "length": StopReason.MAX_TOKENS,
                "tool_calls": StopReason.TOOL_USE,
                "function_call": StopReason.TOOL_USE,
            }
            stop_reason = stop_reason_map.get(finish_reason, StopReason.END_TURN)

        # Parse usage statistics
        usage_data = data.get("usage", {})
        # OpenAI 兼容协议（DashScope/OpenAI/部分 OpenAI 兼容网关）通过
        # prompt_tokens_details.cached_tokens 暴露 prompt cache 命中数。
        # 部分模型（如 DashScope 新加坡区 / qwen3-vl-*）直接放在 usage.cached_tokens。
        _details = usage_data.get("prompt_tokens_details") or {}
        _cached = 0
        if isinstance(_details, dict):
            _cached = int(_details.get("cached_tokens") or 0)
        if not _cached:
            _cached = int(usage_data.get("cached_tokens") or 0)
        _cache_creation = 0
        if isinstance(_details, dict):
            _cache_creation = int(_details.get("cache_creation_input_tokens") or 0)
        usage = Usage(
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
            cache_read_input_tokens=_cached,
            cache_creation_input_tokens=_cache_creation,
        )

        return LLMResponse(
            id=data.get("id", ""),
            content=content_blocks,
            stop_reason=stop_reason,
            usage=usage,
            model=data.get("model", self.config.model),
            reasoning_content=reasoning_content,
        )

    def _convert_stream_event(self, event: dict) -> dict | list[dict]:
        """Convert a streaming event into the unified format.

        A single chunk may carry reasoning_content + content + finish_reason simultaneously
        (special behavior of models like DeepSeek), so returns either a dict or list[dict].
        """
        choices = event.get("choices", [])
        if not choices:
            usage = event.get("usage")
            if usage:
                _det = usage.get("prompt_tokens_details") or {}
                _cached = 0
                if isinstance(_det, dict):
                    _cached = int(_det.get("cached_tokens") or 0)
                if not _cached:
                    _cached = int(usage.get("cached_tokens") or 0)
                _create = 0
                if isinstance(_det, dict):
                    _create = int(_det.get("cache_creation_input_tokens") or 0)
                return {
                    "type": "message_delta",
                    "delta": {},
                    "usage": {
                        "input_tokens": usage.get("prompt_tokens", 0),
                        "output_tokens": usage.get("completion_tokens", 0),
                        "cache_read_input_tokens": _cached,
                        "cache_creation_input_tokens": _create,
                    },
                }
            return {"type": "ping"}

        choice = choices[0]
        delta = choice.get("delta", {})
        events: list[dict] = []

        # 1) Thinking: reasoning_content (DeepSeek R1, Qwen3) / reasoning (OpenRouter)
        reasoning = delta.get("reasoning_content") or ""
        if not reasoning:
            r = delta.get("reasoning")
            if isinstance(r, str) and r:
                reasoning = r
            elif isinstance(r, dict):
                reasoning = r.get("content", "") or ""
        if reasoning:
            events.append(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking", "text": reasoning},
                }
            )

        # 2) Text content
        if delta.get("content"):
            events.append(
                {
                    "type": "content_block_delta",
                    "delta": {"type": "text", "text": delta["content"]},
                }
            )

        # 3) Tool calls
        if "tool_calls" in delta:
            tool_calls = delta["tool_calls"]
            if tool_calls:
                tc = tool_calls[0]
                events.append(
                    {
                        "type": "content_block_delta",
                        "delta": {
                            "type": "tool_use",
                            "id": tc.get("id"),
                            "name": tc.get("function", {}).get("name"),
                            "arguments": tc.get("function", {}).get("arguments"),
                        },
                    }
                )

        # 4) Finish reason → message_stop
        if choice.get("finish_reason"):
            stop_evt = {
                "type": "message_stop",
                "stop_reason": choice["finish_reason"],
            }
            chunk_usage = event.get("usage")
            if chunk_usage:
                _det2 = chunk_usage.get("prompt_tokens_details") or {}
                _cached2 = 0
                if isinstance(_det2, dict):
                    _cached2 = int(_det2.get("cached_tokens") or 0)
                if not _cached2:
                    _cached2 = int(chunk_usage.get("cached_tokens") or 0)
                _create2 = 0
                if isinstance(_det2, dict):
                    _create2 = int(_det2.get("cache_creation_input_tokens") or 0)
                stop_evt["usage"] = {
                    "input_tokens": chunk_usage.get("prompt_tokens", 0),
                    "output_tokens": chunk_usage.get("completion_tokens", 0),
                    "cache_read_input_tokens": _cached2,
                    "cache_creation_input_tokens": _create2,
                }
            events.append(stop_evt)

        if not events:
            return {"type": "ping"}
        return events[0] if len(events) == 1 else events

    async def close(self):
        """Close the client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

"""
Anthropic Provider

Supports API calls to Claude model series.
Enhancements: SSE spec parsing, Prompt Cache support, streaming Usage completeness.
"""

import logging
from collections.abc import AsyncIterator

import httpx

from ..cache import (
    add_message_cache_breakpoints,
    add_tools_cache_control,
    sort_tools_for_cache_stability,
)
from ..converters.tools import (
    convert_tools_to_anthropic,
    has_text_tool_calls,
    parse_text_tool_calls,
)
from ..model_registry import get_model_capabilities, get_thinking_budget
from ..sse import parse_sse_stream
from ..types import (
    AuthenticationError,
    EndpointConfig,
    LLMError,
    LLMRequest,
    LLMResponse,
    RateLimitError,
    StopReason,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
)
from .base import LLMProvider
from .proxy_utils import build_httpx_timeout, get_httpx_transport, get_proxy_config

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic API Provider"""

    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, config: EndpointConfig):
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int | None = None  # Record event loop ID when client was created

    @property
    def api_key(self) -> str:
        """Get API Key"""
        return self.config.get_api_key() or ""

    @property
    def base_url(self) -> str:
        """Get base URL"""
        return self.config.base_url.rstrip("/")

    def _messages_url(self) -> str:
        """Build messages API URL, avoiding /v1 duplicate concatenation."""
        b = self.base_url
        return f"{b}/messages" if b.endswith("/v1") else f"{b}/v1/messages"

    def _is_local_endpoint(self) -> bool:
        """Check if the endpoint is local"""
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

    def _get_validated_api_key(self) -> str:
        """Get and validate API Key, raising a meaningful error early when empty rather than letting API return vague 401."""
        api_key = (self.api_key or "").strip()
        if not api_key:
            if self._is_local_endpoint():
                return "local"
            hint = ""
            if self.config.api_key_env:
                hint = f" (env var {self.config.api_key_env} is not set)"
            raise AuthenticationError(
                f"Missing API key for endpoint '{self.name}'{hint}. "
                "Set the environment variable or configure api_key/api_key_env."
            )
        return api_key

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client

        Note: httpx.AsyncClient is bound to the event loop at creation time.
        If the event loop changes (e.g., scheduled tasks create a new loop), the client must be recreated.
        """
        import asyncio

        try:
            current_loop = asyncio.get_running_loop()
            current_loop_id = id(current_loop)
        except RuntimeError:
            current_loop_id = None

        # Check if client needs to be recreated
        need_recreate = (
            self._client is None
            or self._client.is_closed
            or self._client_loop_id != current_loop_id
        )

        if need_recreate:
            # Safely close old client
            if self._client is not None and not self._client.is_closed:
                try:
                    await self._client.aclose()
                except Exception:
                    pass  # Ignore close errors

            # Get proxy and network configuration
            proxy = get_proxy_config()
            transport = get_httpx_transport()  # IPv4-only support

            is_local = self._is_local_endpoint()

            # httpx strips Authorization on cross-origin redirects for security.
            # Some Anthropic-compatible gateways (MiniMax, Volcengine Coding Plan, etc.)
            # may redirect across hosts. Re-attach credentials via event hook.
            api_key_for_hook = (self.api_key or "").strip()
            if not api_key_for_hook and is_local:
                api_key_for_hook = "local"

            async def _ensure_auth_on_redirect(request: httpx.Request):
                if api_key_for_hook and "Authorization" not in request.headers:
                    request.headers["Authorization"] = f"Bearer {api_key_for_hook}"
                    request.headers["x-api-key"] = api_key_for_hook

            # trust_env=False: proxies are explicitly managed by get_proxy_config() (with reachability checks).
            # Avoid routing requests to non-existent proxy ports due to leftover system proxies (Clash/V2Ray, etc.) on macOS/Windows.
            client_kwargs = {
                "timeout": build_httpx_timeout(self.config.timeout, default=60.0),
                "follow_redirects": True,
                "trust_env": False,
                "event_hooks": {"request": [_ensure_auth_on_redirect]},
            }

            if proxy and not is_local:
                client_kwargs["proxy"] = proxy
                logger.debug(f"[Anthropic] Using proxy: {proxy}")

            if transport:
                client_kwargs["transport"] = transport

            self._client = httpx.AsyncClient(**client_kwargs)
            self._client_loop_id = current_loop_id

        return self._client

    async def chat(self, request: LLMRequest) -> LLMResponse:
        """Send chat request"""
        self._get_validated_api_key()
        await self.acquire_rate_limit()
        client = await self._get_client()

        # Build request body
        body = self._build_request_body(request)

        # Send request
        try:
            response = await client.post(
                self._messages_url(),
                headers=self._build_headers(),
                json=body,
            )

            if response.status_code >= 400:
                body = (response.text or "")[:500]
                if response.status_code == 401:
                    raise AuthenticationError(f"Authentication failed: {body}", status_code=401)
                if response.status_code == 429:
                    raise RateLimitError(f"Rate limit exceeded: {body}", status_code=429)
                raise LLMError(
                    f"API error ({response.status_code}): {body}", status_code=response.status_code
                )

            data = response.json()
            self.mark_healthy()
            return self._parse_response(data)

        except httpx.TimeoutException as e:
            detail = f"{type(e).__name__}: {e}"
            self.mark_unhealthy(f"Timeout: {detail}")
            raise LLMError(f"Request timeout: {detail}")
        except httpx.RequestError as e:
            detail = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}({repr(e)})"
            self.mark_unhealthy(f"Request error: {detail}")
            raise LLMError(f"Request failed: {detail}")

    async def chat_stream(self, request: LLMRequest) -> AsyncIterator[dict]:
        """Streaming chat request.

        Enhancements: Uses SSE spec-compliant parser with finally resource cleanup.
        """
        self._get_validated_api_key()
        await self.acquire_rate_limit()
        client = await self._get_client()

        body = self._build_request_body(request)
        body["stream"] = True

        response = None
        try:
            response = await client.send(
                client.build_request(
                    "POST",
                    self._messages_url(),
                    headers=self._build_headers(),
                    json=body,
                ),
                stream=True,
            )

            if response.status_code >= 400:
                error_body = await response.aread()
                error_text = error_body.decode(errors="replace")[:500]
                if response.status_code == 401:
                    raise AuthenticationError(
                        f"Authentication failed: {error_text}", status_code=401
                    )
                if response.status_code == 429:
                    raise RateLimitError(f"Rate limit exceeded: {error_text}", status_code=429)
                raise LLMError(
                    f"API error ({response.status_code}): {error_text}",
                    status_code=response.status_code,
                )

            # Use SSE spec-compliant parser
            async for event in parse_sse_stream(response):
                yield event

            self.mark_healthy()

        except httpx.TimeoutException as e:
            detail = f"{type(e).__name__}: {e}"
            self.mark_unhealthy(f"Timeout: {detail}")
            raise LLMError(f"Stream timeout: {detail}")
        except httpx.RequestError as e:
            detail = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}({repr(e)})"
            self.mark_unhealthy(f"Stream request error: {detail}")
            raise LLMError(f"Stream request failed: {detail}")
        finally:
            if response is not None:
                await response.aclose()

    def _build_headers(self) -> dict:
        """Build request headers"""
        key = (self.api_key or "").strip()
        return {
            "Content-Type": "application/json",
            "x-api-key": key,
            # Some Anthropic-compatible gateways only recognize Bearer; keep x-api-key for compatibility with official Anthropic.
            "Authorization": f"Bearer {key}",
            "anthropic-version": self.ANTHROPIC_VERSION,
        }

    @staticmethod
    def _build_system_blocks(system: str) -> list[dict]:
        """Split system prompt into static + dynamic blocks for Anthropic prompt caching.

        Uses the '## Developer' section boundary as the split point.
        The static part (System section) gets cache_control to enable
        cross-turn prompt caching, reducing token costs significantly.
        """
        _BOUNDARY = "\n\n---\n\n## Developer"
        idx = system.find(_BOUNDARY)
        if idx == -1:
            return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        static_part = system[:idx]
        dynamic_part = system[idx:]
        blocks = [
            {"type": "text", "text": static_part, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic_part},
        ]
        return blocks

    def _build_request_body(self, request: LLMRequest) -> dict:
        """Build request body.

        Enhancements: Uses model registry to query capabilities, supports Prompt Cache.
        """
        thinking_enabled = request.enable_thinking and self.config.has_capability("thinking")
        messages = self._serialize_messages(request.messages, thinking_enabled)

        # Use model registry to query max_tokens, replacing hardcoded values
        caps = get_model_capabilities(self.config.model)
        max_tokens = request.max_tokens or self.config.max_tokens or caps.default_output_tokens

        body: dict = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        # System prompt: segmented caching (static part marked with cache_control)
        if request.system:
            if caps.supports_cache:
                body["system"] = self._build_system_blocks(request.system)
            else:
                body["system"] = request.system

        # Tools schema: sorted + cache marked
        if request.tools:
            tools = convert_tools_to_anthropic(request.tools)
            tools = sort_tools_for_cache_stability(tools)
            if caps.supports_cache:
                tools = add_tools_cache_control(tools)
            body["tools"] = tools

        if request.temperature != 1.0:
            body["temperature"] = request.temperature

        if request.stop_sequences:
            body["stop_sequences"] = request.stop_sequences

        # Extra parameters
        if self.config.extra_params:
            body.update(self.config.extra_params)
        if request.extra_params:
            body.update(request.extra_params)

        # Message cache breakpoints: add cache_control to last 1-2 messages
        if caps.supports_cache and messages:
            body["messages"] = add_message_cache_breakpoints(messages, max_breakpoints=2)

        # Anthropic extended thinking (Extended Thinking)
        if thinking_enabled:
            budget = get_thinking_budget(self.config.model, request.thinking_depth)
            if budget <= 0:
                budget = 8192
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }
            body.pop("temperature", None)
            current_max = body.get("max_tokens", 4096)
            if current_max < budget + 1024:
                body["max_tokens"] = budget + 4096

        return body

    @staticmethod
    def _serialize_messages(messages: list, thinking_enabled: bool) -> list[dict]:
        """Serialize message list ensuring compliance with thinking mode format.

        When thinking is enabled, some Anthropic-compatible proxies (e.g., YunWu AI forwarding Kimi/Qwen)
        require all assistant messages containing tool_use to include a thinking block, otherwise returning 400:
        "thinking is enabled but reasoning_content is missing in assistant tool call message"

        The conversation history may contain assistant messages without thinking blocks (e.g., generated by
        non-thinking endpoints before failover, or thinking enabled mid-conversation). A placeholder
        thinking block is inserted to satisfy API validation.
        """
        result = []
        for msg in messages:
            msg_dict = msg.to_dict() if hasattr(msg, "to_dict") else dict(msg)
            if not thinking_enabled or msg_dict.get("role") != "assistant":
                result.append(msg_dict)
                continue

            content = msg_dict.get("content")
            if not isinstance(content, list):
                result.append(msg_dict)
                continue

            has_tool_use = any(b.get("type") == "tool_use" for b in content)
            has_thinking = any(b.get("type") == "thinking" for b in content)

            if has_tool_use and not has_thinking:
                content.insert(0, {"type": "thinking", "thinking": "..."})
                msg_dict["content"] = content

            result.append(msg_dict)
        return result

    def _parse_response(self, data: dict) -> LLMResponse:
        """Parse response

        Supports MiniMax M2.1 Interleaved Thinking:
        - Parses thinking blocks and preserves them in content
        - Ensures continuity of thought chain across multiple tool calls

        Supports text-format tool calls (MiniMax compatible):
        - Detects and parses <minimax:tool_call> format
        - Converts to standard ToolUseBlock
        """
        content_blocks = []
        has_tool_calls = False
        text_content = ""  # Collect text content for detecting text-format tool calls
        thinking_content = ""  # Collect thinking content, detect embedded tool calls

        for block in data.get("content", []):
            block_type = block.get("type")

            if block_type == "thinking":
                # MiniMax M2.1 Interleaved Thinking support
                # Must preserve thinking blocks completely to maintain thought chain continuity
                raw_thinking = block.get("thinking", "")
                thinking_content += raw_thinking
                content_blocks.append(ThinkingBlock(thinking=raw_thinking))
            elif block_type == "text":
                text = block.get("text", "")
                text_content += text
                content_blocks.append(TextBlock(text=text))
            elif block_type == "tool_use":
                content_blocks.append(
                    ToolUseBlock(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        input=block.get("input", {}),
                    )
                )
                has_tool_calls = True

        # === Text-format tool call parsing (MiniMax compatible) ===
        # When the model returns text-format tool calls (e.g., <minimax:tool_call>), parse and convert
        # Also check if tool calls are embedded in thinking blocks (MiniMax-M2.5 known behavior)
        combined_text_for_tool_check = text_content
        if not has_tool_calls and not text_content and thinking_content:
            if has_text_tool_calls(thinking_content):
                combined_text_for_tool_check = thinking_content
                logger.info(
                    f"[TEXT_TOOL_PARSE] Detected tool calls embedded inside thinking block from {self.name}"
                )

        if (
            not has_tool_calls
            and combined_text_for_tool_check
            and has_text_tool_calls(combined_text_for_tool_check)
        ):
            logger.info(f"[TEXT_TOOL_PARSE] Detected text-based tool calls from {self.name}")
            clean_text, text_tool_calls = parse_text_tool_calls(combined_text_for_tool_check)

            if text_tool_calls:
                # Remove text blocks containing tool calls, replace with cleaned text
                content_blocks = [
                    b
                    for b in content_blocks
                    if not (isinstance(b, TextBlock) and has_text_tool_calls(b.text))
                ]

                # Add cleaned text (if any)
                if clean_text.strip():
                    content_blocks.append(TextBlock(text=clean_text.strip()))

                # Add parsed tool calls
                content_blocks.extend(text_tool_calls)
                has_tool_calls = True
                logger.info(
                    f"[TEXT_TOOL_PARSE] Extracted {len(text_tool_calls)} tool calls "
                    f"from {'thinking block' if combined_text_for_tool_check != text_content else 'text'}"
                )

        # Parse stop reason
        stop_reason_str = data.get("stop_reason", "end_turn")
        if has_tool_calls:
            stop_reason = StopReason.TOOL_USE
        else:
            stop_reason_map = {
                "end_turn": StopReason.END_TURN,
                "max_tokens": StopReason.MAX_TOKENS,
                "tool_use": StopReason.TOOL_USE,
                "stop_sequence": StopReason.STOP_SEQUENCE,
            }
            stop_reason = stop_reason_map.get(stop_reason_str, StopReason.END_TURN)

        # Parse usage statistics
        usage_data = data.get("usage", {})
        usage = Usage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=usage_data.get("cache_read_input_tokens", 0),
        )

        return LLMResponse(
            id=data.get("id", ""),
            content=content_blocks,
            stop_reason=stop_reason,
            usage=usage,
            model=data.get("model", self.config.model),
        )

    async def close(self):
        """Close client"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

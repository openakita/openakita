"""
Brain module - LLM interaction layer

Brain is a thin wrapper around LLMClient, providing a backward-compatible interface.
All actual LLM calls, capability routing, and failover are handled by LLMClient.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from anthropic.types import Message as AnthropicMessage
from anthropic.types import MessageParam, ToolParam
from anthropic.types import TextBlock as AnthropicTextBlock
from anthropic.types import ToolUseBlock as AnthropicToolUseBlock
from anthropic.types import Usage as AnthropicUsage

from ..config import settings
from ..llm.client import LLMClient
from ..llm.config import get_default_config_path, load_endpoints_config
from ..llm.types import (
    AudioBlock,
    AudioContent,
    DocumentBlock,
    DocumentContent,
    ImageBlock,
    ImageContent,
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    ThinkingBlock,
    Tool,
    ToolResultBlock,
    ToolUseBlock,
    VideoBlock,
    VideoContent,
)
from .token_tracking import (
    TokenTrackingContext,
    reset_tracking_context,
    set_tracking_context,
)
from .token_tracking import (
    record_usage as _record_token_usage,
)

logger = logging.getLogger(__name__)


@dataclass
class Response:
    """LLM response (backward compatible)"""

    content: str
    tool_calls: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)


@dataclass
class Context:
    """Conversation context"""

    messages: list[MessageParam] = field(default_factory=list)
    system: str = ""
    tools: list[ToolParam] = field(default_factory=list)


class Brain:
    """
    Agent brain - LLM interaction layer

    Brain is a thin wrapper around LLMClient:
    - Configuration loaded from llm_endpoints.json
    - Capability routing and failover handled by LLMClient
    - Provides a backward-compatible Anthropic Message format interface
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ):
        # Compiler circuit breaker — instance-level to avoid cross-instance state sharing
        self._compiler_fail_count: int = 0
        self._compiler_circuit_open: bool = False
        self._compiler_circuit_open_at: float = 0.0
        self._compiler_auth_failed: bool = False

        # max_tokens=0 means "use a reasonable default":
        # - For OpenAI-compatible APIs: use the endpoint config value or fall back to 16384
        #   (some APIs such as NVIDIA NIM default to very low values)
        # - For the Anthropic API: use the endpoint config value or fall back to 16384
        #   (this parameter is required by that API)
        self.max_tokens = max_tokens if max_tokens is not None else settings.max_tokens

        # Create LLMClient (unified entry point)
        config_path = get_default_config_path()
        if config_path.exists():
            self._llm_client = LLMClient(config_path=config_path)
            logger.info(f"Brain using LLMClient with config from {config_path}")
        else:
            # If there is no config file, create an empty client
            self._llm_client = LLMClient()
            logger.warning("No llm_endpoints.json found, LLMClient may not work")

        # Dedicated LLMClient for Prompt Compiler (independent of the main model, uses a fast small model)
        self._compiler_client: LLMClient | None = None
        self._init_compiler_client()

        # Compiler circuit breaker constants (instance-level, overridable by tests)
        self._COMPILER_FAIL_THRESHOLD: int = 5
        self._COMPILER_CIRCUIT_RESET_S: float = 300.0
        self._COMPILER_AUTH_CIRCUIT_RESET_S: float = 1800.0

        # Public attributes (obtained from LLMClient)
        self._update_public_attrs()

        # Thinking mode state
        self._thinking_enabled = True

        # Trace context for debug dump files (org_id, node_id, session_id, etc.)
        self._trace_context: dict[str, str] = {}

        # Per-session LLM call accumulator (reset via reset_usage_accumulator)
        self._acc_calls: int = 0
        self._acc_tokens_in: int = 0
        self._acc_tokens_out: int = 0

        # Startup info
        endpoints = self._llm_client.endpoints
        logger.info(f"Brain initialized with {len(endpoints)} endpoints via LLMClient")
        for ep in endpoints:
            logger.info(f"  - {ep.name}: {ep.model} (capabilities: {ep.capabilities})")

        # Show current endpoints
        if endpoints:
            # Get healthy endpoints
            healthy_eps = [p.name for p in self._llm_client.providers.values() if p.is_healthy]
            if healthy_eps:
                logger.info("  ╔══════════════════════════════════════════╗")
                logger.info(f"  ║  Available endpoints: {', '.join(healthy_eps):<20}║")
                logger.info("  ╚══════════════════════════════════════════╝")

    def _update_public_attrs(self) -> None:
        """Update public attributes (backward compatible)"""
        endpoints = self._llm_client.endpoints
        if endpoints:
            ep = endpoints[0]  # Use info from the first endpoint
            self.model = ep.model
            self.base_url = ep.base_url
            # API key is no longer exposed
        else:
            self.model = settings.default_model
            self.base_url = ""

    def set_trace_context(self, ctx: dict[str, str]) -> None:
        """Set trace context (org_id, node_id, session_id, etc.) for LLM debug dumps."""
        self._trace_context = dict(ctx)

    def _init_compiler_client(self) -> None:
        """Load the dedicated Prompt Compiler LLMClient from config"""
        try:
            _, compiler_eps, _, _ = load_endpoints_config()
            if compiler_eps:
                self._compiler_client = LLMClient(endpoints=compiler_eps)
                names = [ep.name for ep in compiler_eps]
                logger.info(f"Compiler LLMClient initialized with endpoints: {names}")
            else:
                logger.info("No compiler endpoints configured, will fall back to main model")
        except Exception as e:
            logger.warning(f"Failed to init compiler client: {e}")

    def _compiler_available(self) -> bool:
        """Check if the compiler client is usable (not circuit-broken)."""
        if not self._compiler_client:
            return False
        if not self._compiler_circuit_open:
            return True
        import time

        elapsed = time.monotonic() - self._compiler_circuit_open_at
        reset_s = (
            self._COMPILER_AUTH_CIRCUIT_RESET_S
            if self._compiler_auth_failed
            else self._COMPILER_CIRCUIT_RESET_S
        )
        if elapsed >= reset_s:
            self._compiler_circuit_open = False
            self._compiler_fail_count = 0
            self._compiler_auth_failed = False
            logger.info("[Brain] Compiler circuit breaker reset, will retry compiler endpoint")
            return True
        return False

    def _compiler_on_success(self) -> None:
        self._compiler_fail_count = 0
        if self._compiler_circuit_open:
            self._compiler_circuit_open = False
            logger.info("[Brain] Compiler circuit breaker closed (success)")

    def _compiler_on_failure(self, error_str: str = "") -> None:
        self._compiler_fail_count += 1

        _is_auth = (
            any(
                kw in error_str.lower()
                for kw in (
                    "invalid_api_key",
                    "authentication",
                    "unauthorized",
                    "401",
                    "api key",
                    "auth_failed",
                )
            )
            if error_str
            else False
        )

        if _is_auth:
            import time

            self._compiler_auth_failed = True
            self._compiler_circuit_open = True
            self._compiler_circuit_open_at = time.monotonic()
            logger.error(
                f"[Brain] Compiler circuit breaker OPEN (auth failure), "
                f"skipping compiler for {self._COMPILER_AUTH_CIRCUIT_RESET_S}s. "
                f"Fix the API key in settings to restore."
            )
            return

        if (
            not self._compiler_circuit_open
            and self._compiler_fail_count >= self._COMPILER_FAIL_THRESHOLD
        ):
            import time

            self._compiler_circuit_open = True
            self._compiler_circuit_open_at = time.monotonic()
            logger.warning(
                f"[Brain] Compiler circuit breaker OPEN after "
                f"{self._compiler_fail_count} consecutive failures, "
                f"skipping compiler for {self._COMPILER_CIRCUIT_RESET_S}s"
            )

    def reload_compiler_client(self) -> bool:
        """Hot-reload the compiler endpoint configuration.

        Returns:
            True if the reload succeeded, False if there was no change or it failed.
        """
        try:
            _, compiler_eps, _, _ = load_endpoints_config()
            if compiler_eps:
                self._compiler_client = LLMClient(endpoints=compiler_eps)
                names = [ep.name for ep in compiler_eps]
                logger.info(f"Compiler LLMClient reloaded with endpoints: {names}")
            else:
                self._compiler_client = None
                logger.info("Compiler endpoints cleared (none configured)")
            # Reset the circuit breaker after reload, so recovery works once the user fixes the API key
            self._compiler_circuit_open = False
            self._compiler_fail_count = 0
            self._compiler_auth_failed = False
            self._compiler_circuit_open_at = 0.0
            return True
        except Exception as e:
            logger.warning(f"Failed to reload compiler client: {e}")
            return False

    async def compiler_think(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 512,
    ) -> Response:
        """
        Dedicated LLM call for the Prompt Compiler.

        Call strategy:
        1. Prefer compiler_client (fast model, thinking mode forcibly disabled)
        2. If all compiler_client endpoints fail, fall back to the main model (thinking also disabled)

        Args:
            prompt: user message
            system: system prompt
            max_tokens: max output tokens (default 512, callers may raise as needed)

        Returns:
            Response object
        """
        messages = [Message(role="user", content=[TextBlock(text=prompt)])]

        _source = "compiler"
        if self._compiler_available():
            try:
                response = await self._compiler_client.chat(
                    messages=messages,
                    system=system,
                    enable_thinking=False,
                    max_tokens=max_tokens,
                )
                self._compiler_on_success()
                self._record_usage(response)
                result = self._llm_response_to_response(response)
                self._dump_llm_request(system, messages, [], caller="compiler_think")
                self._dump_llm_response(
                    response,
                    caller="compiler_think",
                    request_id=f"compiler_{_source}",
                )
                return result
            except Exception as e:
                self._compiler_on_failure(str(e))
                logger.warning(f"Compiler LLM failed, falling back to main model: {e}")

        # Fall back to the main model
        # The main model may be a reasoning model (e.g. mimo-v2-pro). Even with enable_thinking=False
        # it can produce thinking content in the reasoning field, consuming the max_tokens budget.
        # Bump max_tokens to ensure enough headroom remains for content after reasoning.
        _source = "main_fallback"
        _fallback_max = max(max_tokens * 4, 2048)
        if _fallback_max != max_tokens:
            logger.info(
                f"[compiler_think] Falling back to main model, "
                f"bumping max_tokens {max_tokens} → {_fallback_max}"
            )
        response = await self._llm_client.chat(
            messages=messages,
            system=system,
            enable_thinking=False,
            max_tokens=_fallback_max,
        )
        self._record_usage(response)
        req_id = self._dump_llm_request(system, messages, [], caller="compiler_think")
        self._dump_llm_response(
            response,
            caller=f"compiler_think({_source})",
            request_id=req_id,
        )
        return self._llm_response_to_response(response)

    async def think_lightweight(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 2048,
    ) -> Response:
        """
        Lightweight thinking: prefer the compiler endpoint.

        For simple LLM calls such as memory extraction or classification that do not
        need tools or context. Fully isolated from the main reasoning chain (no shared
        message history), using a separate LLM endpoint.

        Call strategy:
        1. Prefer _compiler_client (fast small model)
        2. Fall back to _llm_client if compiler_client is unavailable or fails

        Args:
            prompt: user message
            system: system prompt
            max_tokens: max output tokens

        Returns:
            Response object
        """
        messages = [Message(role="user", content=[TextBlock(text=prompt)])]
        sys_prompt = system or ""

        req_id = self._dump_llm_request(sys_prompt, messages, [], caller="think_lightweight")

        use_compiler = self._compiler_available()
        client = self._compiler_client if use_compiler else self._llm_client
        client_name = "compiler" if use_compiler else "main"

        try:
            response = await client.chat(
                messages=messages,
                system=sys_prompt,
                enable_thinking=False,
                max_tokens=max_tokens,
            )
            if use_compiler:
                self._compiler_on_success()
            logger.info(f"[LLM] think_lightweight completed via {client_name} endpoint")
        except Exception as e:
            if use_compiler:
                self._compiler_on_failure(str(e))
                logger.warning(
                    f"[LLM] think_lightweight: compiler failed ({e}), falling back to main"
                )
                response = await self._llm_client.chat(
                    messages=messages,
                    system=sys_prompt,
                    enable_thinking=False,
                    max_tokens=max_tokens,
                )
                client_name = "main_fallback"
            else:
                raise

        # Save response
        self._dump_llm_response(
            response, caller=f"think_lightweight_{client_name}", request_id=req_id
        )

        self._record_usage(response)
        return self._llm_response_to_response(response)

    def _llm_response_to_response(self, llm_response: LLMResponse) -> Response:
        """Convert an LLMResponse into a backward-compatible Response"""
        text_parts = []
        tool_calls = []
        for block in llm_response.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
        return Response(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=llm_response.stop_reason or "",
            usage={
                "input_tokens": llm_response.usage.input_tokens if llm_response.usage else 0,
                "output_tokens": llm_response.usage.output_tokens if llm_response.usage else 0,
            },
        )

    def set_thinking_mode(self, enabled: bool) -> None:
        """Set thinking mode"""
        self._thinking_enabled = enabled
        logger.info(f"Thinking mode {'enabled' if enabled else 'disabled'}")

    def is_thinking_enabled(self) -> bool:
        """Check whether thinking mode is enabled.

        First checks the global config (always/never), then whether the model's
        capabilities support thinking, and finally uses the runtime toggle.
        Models that do not support thinking always return False.
        """
        thinking_mode = settings.thinking_mode
        if thinking_mode == "always":
            from ..llm.model_registry import get_model_capabilities

            caps = get_model_capabilities(self.model)
            if not caps.supports_thinking:
                logger.debug(
                    f"[Brain] thinking_mode=always but model={self.model} "
                    f"does not support thinking, disabled"
                )
                return False
            return True
        if thinking_mode == "never":
            return False
        from ..llm.model_registry import get_model_capabilities

        caps = get_model_capabilities(self.model)
        if not caps.supports_thinking:
            return False
        return self._thinking_enabled

    def get_current_endpoint_info(self) -> dict:
        """Get current endpoint info"""
        providers = self._llm_client.providers
        for name, provider in providers.items():
            if provider.is_healthy:
                return {
                    "name": name,
                    "model": provider.model,
                    "healthy": True,
                }
        # No healthy endpoints
        endpoints = self._llm_client.endpoints
        if endpoints:
            return {
                "name": endpoints[0].name,
                "model": endpoints[0].model,
                "healthy": False,
            }
        return {"name": "none", "model": "none", "healthy": False}

    # ========================================================================
    # Core method: messages_create
    # ========================================================================

    def messages_create(
        self, use_thinking: bool = None, thinking_depth: str | None = None, **kwargs
    ) -> AnthropicMessage:
        """
        Call the LLM API (via LLMClient).

        This is the primary LLM entry point and automatically handles:
        - Capability routing (images/videos are routed to endpoints that support them)
        - Failover
        - Format conversion

        Args:
            use_thinking: whether to use thinking mode
            thinking_depth: thinking depth ('low'/'medium'/'high'/None)
            **kwargs: Anthropic-style parameters (messages, system, tools, max_tokens)

        Returns:
            Response in Anthropic Message format
        """
        if use_thinking is None:
            use_thinking = self.is_thinking_enabled()

        # Convert message format: Anthropic -> LLMClient
        llm_messages = self._convert_messages_to_llm(kwargs.get("messages", []))
        system = kwargs.get("system", "")
        llm_tools = self._convert_tools_to_llm(kwargs.get("tools", []))
        max_tokens = kwargs.get("max_tokens", self.max_tokens)

        # Debug output: save the full request to a file
        req_id = self._dump_llm_request(system, llm_messages, llm_tools, caller="messages_create")

        conversation_id = kwargs.get("conversation_id")

        # Call LLMClient
        try:
            response = asyncio.get_event_loop().run_until_complete(
                self._llm_client.chat(
                    messages=llm_messages,
                    system=system,
                    tools=llm_tools,
                    max_tokens=max_tokens,
                    enable_thinking=use_thinking,
                    thinking_depth=thinking_depth,
                    conversation_id=conversation_id,
                )
            )
        except RuntimeError:
            # No event loop; create a new one
            response = asyncio.run(
                self._llm_client.chat(
                    messages=llm_messages,
                    system=system,
                    tools=llm_tools,
                    max_tokens=max_tokens,
                    enable_thinking=use_thinking,
                    thinking_depth=thinking_depth,
                    conversation_id=conversation_id,
                )
            )

        # Save response to debug file
        self._dump_llm_response(response, caller="messages_create", request_id=req_id)

        # Record token usage
        self._record_usage(response)

        # Convert response: LLMClient -> Anthropic Message
        return self._convert_response_to_anthropic(response)

    async def messages_create_async(
        self,
        use_thinking: bool = None,
        thinking_depth: str | None = None,
        cancel_event: asyncio.Event | None = None,
        **kwargs,
    ) -> AnthropicMessage:
        """Async version of messages_create; awaits LLMClient.chat() directly.

        For code paths already inside an event loop (e.g. cancellation cleanup),
        avoiding the asyncio.to_thread + asyncio.run combo that creates a new
        event loop and leads to httpx connection pool contention.
        """
        if use_thinking is None:
            use_thinking = self.is_thinking_enabled()

        llm_messages = self._convert_messages_to_llm(kwargs.get("messages", []))
        system = kwargs.get("system", "")
        llm_tools = self._convert_tools_to_llm(kwargs.get("tools", []))
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        conversation_id = kwargs.get("conversation_id")

        logger.info(
            f"[Brain] messages_create_async called: msg_count={len(llm_messages)}, "
            f"max_tokens={max_tokens}, use_thinking={use_thinking}, "
            f"tools_count={len(llm_tools) if llm_tools else 0}, model_kwarg={kwargs.get('model', 'N/A')}"
        )

        req_id = self._dump_llm_request(
            system, llm_messages, llm_tools, caller="messages_create_async"
        )

        extra_params = kwargs.get("extra_params")

        try:
            response = await self._llm_client.chat(
                messages=llm_messages,
                system=system,
                tools=llm_tools,
                max_tokens=max_tokens,
                enable_thinking=use_thinking,
                thinking_depth=thinking_depth,
                conversation_id=conversation_id,
                cancel_event=cancel_event,
                extra_params=extra_params,
            )
            _choices = getattr(response, "choices", None) or []
            _content = getattr(response, "content", None) or []
            logger.info(
                f"[Brain] messages_create_async success: "
                f"choices={len(_choices)}, content_blocks={len(_content)}"
            )
        except Exception as e:
            logger.error(f"[Brain] messages_create_async FAILED: {type(e).__name__}: {e}")
            raise

        self._dump_llm_response(response, caller="messages_create_async", request_id=req_id)

        # Record token usage
        self._record_usage(response)

        return self._convert_response_to_anthropic(response)

    async def messages_create_stream(
        self,
        use_thinking: bool = None,
        thinking_depth: str | None = None,
        **kwargs,
    ):
        """Streaming version of messages_create; yields raw provider stream events (dict).

        Parameter handling matches messages_create_async, but calls
        LLMClient.chat_stream() and yields events one by one for StreamAccumulator
        to consume. After the stream ends, the caller is responsible for recording
        token usage from the usage info exposed by StreamAccumulator.
        """
        if use_thinking is None:
            use_thinking = self.is_thinking_enabled()

        llm_messages = self._convert_messages_to_llm(kwargs.get("messages", []))
        system = kwargs.get("system", "")
        llm_tools = self._convert_tools_to_llm(kwargs.get("tools", []))
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        conversation_id = kwargs.get("conversation_id")
        extra_params = kwargs.get("extra_params")

        logger.info(
            f"[Brain] messages_create_stream called: msg_count={len(llm_messages)}, "
            f"max_tokens={max_tokens}, use_thinking={use_thinking}, "
            f"tools_count={len(llm_tools) if llm_tools else 0}, model_kwarg={kwargs.get('model', 'N/A')}"
        )

        self._dump_llm_request(system, llm_messages, llm_tools, caller="messages_create_stream")

        _tt = set_tracking_context(
            TokenTrackingContext(
                session_id=kwargs.get("conversation_id", ""),
                operation_type="chat_react_iteration_stream",
                channel="api",
                iteration=kwargs.get("iteration", 0),
                agent_profile_id=kwargs.get("agent_profile_id", "default"),
            )
        )
        try:
            async for event in self._llm_client.chat_stream(
                messages=llm_messages,
                system=system,
                tools=llm_tools,
                max_tokens=max_tokens,
                enable_thinking=use_thinking,
                thinking_depth=thinking_depth,
                conversation_id=conversation_id,
                extra_params=extra_params,
            ):
                yield event
        finally:
            reset_tracking_context(_tt)

    # ========================================================================
    # Token usage recording
    # ========================================================================

    def _record_usage(self, response: LLMResponse) -> None:
        """Extract token usage from an LLMResponse and dispatch it to the tracking queue."""
        try:
            usage = response.usage
            if not usage:
                return

            self._acc_calls += 1
            self._acc_tokens_in += usage.input_tokens
            self._acc_tokens_out += usage.output_tokens

            ep_name = response.endpoint_name or self.get_current_endpoint_info().get("name", "")
            cost = 0.0
            for ep in self._llm_client.endpoints:
                if ep.name == ep_name:
                    cost = ep.calculate_cost(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cache_read_tokens=usage.cache_read_input_tokens,
                    )
                    break
            _record_token_usage(
                model=response.model or "",
                endpoint_name=ep_name,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_tokens=usage.cache_creation_input_tokens,
                cache_read_tokens=usage.cache_read_input_tokens,
                estimated_cost=cost,
            )
        except Exception as e:
            logger.debug(f"[Brain] _record_usage failed (non-fatal): {e}")

    def drain_usage_accumulator(self) -> dict:
        """Return accumulated LLM usage since last drain, then reset counters."""
        stats = {
            "calls": self._acc_calls,
            "tokens_in": self._acc_tokens_in,
            "tokens_out": self._acc_tokens_out,
        }
        self._acc_calls = 0
        self._acc_tokens_in = 0
        self._acc_tokens_out = 0
        return stats

    # ========================================================================
    # Format conversion methods
    # ========================================================================

    def _convert_messages_to_llm(self, messages: list[MessageParam]) -> list[Message]:
        """Convert an Anthropic MessageParam into an LLMClient Message.

        Supports MiniMax M2.1 Interleaved Thinking:
        - Parses and preserves thinking blocks
        - Keeps the chain of thought continuous across multi-turn tool calls

        Supports Kimi reasoning_content:
        - Extracts reasoning_content from the message dict
        - Passes it to the Message object to support model switching
        """
        result = []

        for msg in messages:
            role = msg.get("role", "user") if isinstance(msg, dict) else msg["role"]
            content = msg.get("content", "") if isinstance(msg, dict) else msg["content"]
            # Extract reasoning_content (for thinking-capable models such as Kimi)
            reasoning_content = msg.get("reasoning_content") if isinstance(msg, dict) else None

            if isinstance(content, str):
                result.append(
                    Message(role=role, content=content, reasoning_content=reasoning_content)
                )
            elif isinstance(content, list):
                # Complex content (multimodal, tool calls, etc.)
                blocks = []
                for part in content:
                    if isinstance(part, dict):
                        part_type = part.get("type", "")

                        if part_type == "text":
                            blocks.append(TextBlock(text=part.get("text", "")))

                        elif part_type == "thinking":
                            # MiniMax M2.1 Interleaved Thinking support
                            # The thinking block must be preserved in full to keep the chain of thought intact
                            blocks.append(ThinkingBlock(thinking=part.get("thinking", "")))

                        elif part_type == "tool_use":
                            blocks.append(
                                ToolUseBlock(
                                    id=part.get("id", ""),
                                    name=part.get("name", ""),
                                    input=part.get("input", {}),
                                )
                            )

                        elif part_type == "tool_result":
                            tool_content = part.get("content", "")
                            if isinstance(tool_content, list):
                                has_images = any(
                                    p.get("type") in ("image_url", "image")
                                    for p in tool_content
                                    if isinstance(p, dict)
                                )
                                if has_images:
                                    # Preserve multimodal content (text + images) so the LLM can see it
                                    tool_content = tool_content
                                else:
                                    texts = [
                                        p.get("text", "")
                                        for p in tool_content
                                        if isinstance(p, dict) and p.get("type") == "text"
                                    ]
                                    tool_content = "\n".join(texts)
                            blocks.append(
                                ToolResultBlock(
                                    tool_use_id=part.get("tool_use_id", ""),
                                    content=tool_content
                                    if isinstance(tool_content, list)
                                    else str(tool_content),
                                    is_error=part.get("is_error", False),
                                )
                            )

                        elif part_type == "image":
                            source = part.get("source", {})
                            if source.get("type") == "base64":
                                blocks.append(
                                    ImageBlock(
                                        image=ImageContent(
                                            media_type=source.get("media_type", "image/jpeg"),
                                            data=source.get("data", ""),
                                        )
                                    )
                                )

                        elif part_type == "video":
                            source = part.get("source", {})
                            if source.get("type") == "base64":
                                blocks.append(
                                    VideoBlock(
                                        video=VideoContent(
                                            media_type=source.get("media_type", "video/mp4"),
                                            data=source.get("data", ""),
                                        )
                                    )
                                )

                        elif part_type == "audio":
                            source = part.get("source", {})
                            if source.get("type") == "base64":
                                blocks.append(
                                    AudioBlock(
                                        audio=AudioContent(
                                            media_type=source.get("media_type", "audio/wav"),
                                            data=source.get("data", ""),
                                            format=source.get("format", "wav"),
                                        )
                                    )
                                )

                        elif part_type == "document":
                            source = part.get("source", {})
                            if source.get("type") == "base64":
                                blocks.append(
                                    DocumentBlock(
                                        document=DocumentContent(
                                            media_type=source.get("media_type", "application/pdf"),
                                            data=source.get("data", ""),
                                            filename=part.get("filename", ""),
                                        )
                                    )
                                )

                        # ── OpenAI format compatibility (Desktop Chat attachments, etc.) ──
                        elif part_type == "image_url":
                            image_url = part.get("image_url", {})
                            url = image_url.get("url", "")
                            if url:
                                import re as _re

                                m = _re.match(r"data:([^;]+);base64,(.+)", url)
                                if m:
                                    blocks.append(
                                        ImageBlock(
                                            image=ImageContent(
                                                media_type=m.group(1),
                                                data=m.group(2),
                                            )
                                        )
                                    )
                                else:
                                    # Remote URL — try to resolve via ImageContent.from_url
                                    img = ImageContent.from_url(url)
                                    if img:
                                        blocks.append(ImageBlock(image=img))

                        elif part_type == "video_url":
                            video_url = part.get("video_url", {})
                            url = video_url.get("url", "")
                            if url:
                                import re as _re

                                m = _re.match(r"data:([^;]+);base64,(.+)", url)
                                if m:
                                    blocks.append(
                                        VideoBlock(
                                            video=VideoContent(
                                                media_type=m.group(1),
                                                data=m.group(2),
                                            )
                                        )
                                    )
                                else:
                                    logger.warning(
                                        f"[Brain] video_url is not a data URL, "
                                        f"passing through as-is: {url[:80]}..."
                                    )
                                    vid = VideoContent.from_url(url)
                                    if vid:
                                        blocks.append(VideoBlock(video=vid))

                        elif part_type == "input_audio":
                            audio_data = part.get("input_audio", {})
                            data = audio_data.get("data", "")
                            fmt = audio_data.get("format", "wav")
                            if data:
                                mime_map = {
                                    "wav": "audio/wav",
                                    "mp3": "audio/mpeg",
                                    "pcm16": "audio/pcm",
                                }
                                media_type = mime_map.get(fmt, f"audio/{fmt}")
                                blocks.append(
                                    AudioBlock(
                                        audio=AudioContent(
                                            media_type=media_type,
                                            data=data,
                                            format=fmt,
                                        )
                                    )
                                )

                    elif isinstance(part, str):
                        blocks.append(TextBlock(text=part))

                if blocks:
                    result.append(
                        Message(role=role, content=blocks, reasoning_content=reasoning_content)
                    )
                else:
                    logger.debug(
                        f"[Brain] Skipping message with empty content blocks (role={role})"
                    )
            else:
                result.append(
                    Message(role=role, content=str(content), reasoning_content=reasoning_content)
                )

        return result

    def _convert_tools_to_llm(self, tools: list[ToolParam] | None) -> list[Tool] | None:
        """Convert tool definitions into LLMClient Tool, accepting both Anthropic and OpenAI formats.

        Supports defer_loading: tools marked _deferred=True are sent with only
        name + description, not input_schema, to reduce token usage. The model
        fetches the full schema on demand via tool_search.

        Supported formats:
        - Anthropic (internal): {"name": ..., "description": ..., "input_schema": {...}}
        - OpenAI:               {"type": "function", "function": {"name": ..., ...}}
        """
        if not tools:
            return None

        result: list[Tool] = []
        skipped = 0
        deferred = 0
        for tool in tools:
            name = tool.get("name", "")
            description = tool.get("detail") or tool.get("description", "")
            schema = tool.get("input_schema", {})
            is_deferred = tool.get("_deferred", False)

            if not name:
                func = tool.get("function")
                if isinstance(func, dict):
                    name = func.get("name", "")
                    description = description or func.get("description", "")
                    schema = schema or func.get("parameters", {})

            if not name:
                skipped += 1
                continue

            if is_deferred:
                short_desc = description.split("\n")[0][:200] if description else ""
                result.append(
                    Tool(
                        name=name,
                        description=(
                            f"[DEFERRED] {short_desc} — "
                            "Do NOT call this tool directly. "
                            'You must first call tool_search(query="...") to load '
                            "its full parameters, then call it in the NEXT turn."
                        ),
                        input_schema={"type": "object", "properties": {}},
                    )
                )
                deferred += 1
            else:
                result.append(
                    Tool(
                        name=name,
                        description=description,
                        input_schema=schema,
                    )
                )

        if skipped:
            logger.warning(
                "[Brain] _convert_tools_to_llm: skipped %d tool(s) with empty name "
                "(total=%d, valid=%d)",
                skipped,
                len(tools),
                len(result),
            )
        if deferred:
            logger.debug(
                "[Brain] defer_loading: %d/%d tools deferred (schema omitted)",
                deferred,
                len(result),
            )

        return result if result else None

    def _convert_response_to_anthropic(self, response: LLMResponse) -> AnthropicMessage:
        """Convert an LLMClient Response into an Anthropic Message.

        Supports MiniMax M2.1 Interleaved Thinking:
        - thinking blocks are converted into text wrapped in <thinking> tags
        - The Agent layer preserves the full content for message-history round-tripping
        """
        # Convert content blocks
        content_blocks = []
        thinking_texts = []

        for block in response.content:
            if isinstance(block, ThinkingBlock):
                # MiniMax M2.1 Interleaved Thinking support
                # Convert to text wrapped in <thinking> tags to match how other models are handled
                # When sending message history back to MiniMax it will be converted back into thinking blocks
                thinking_texts.append(f"<thinking>{block.thinking}</thinking>")
            elif isinstance(block, TextBlock):
                content_blocks.append(AnthropicTextBlock(type="text", text=block.text))
            elif isinstance(block, ToolUseBlock):
                content_blocks.append(
                    AnthropicToolUseBlock(
                        type="tool_use",
                        id=block.id,
                        name=block.name,
                        input=block.input,
                    )
                )

        # Preserve reasoning_content from OpenAI-compatible responses (DeepSeek / Kimi / Zhipu, etc.)
        # Wrap it in <thinking> tags and embed it in the text, following the same path as Anthropic ThinkingBlock.
        # On the next round, _extract_thinking_content() will extract the real reasoning text back into the API,
        # rather than using the placeholder "...".
        if response.reasoning_content and not thinking_texts:
            thinking_texts.append(f"<thinking>{response.reasoning_content}</thinking>")

        # If there is thinking content, prepend it to the text blocks
        if thinking_texts:
            thinking_content = "\n".join(thinking_texts)
            if content_blocks and hasattr(content_blocks[0], "text"):
                # Merge into the first text block
                content_blocks[0] = AnthropicTextBlock(
                    type="text", text=thinking_content + "\n" + content_blocks[0].text
                )
            else:
                # Insert a new text block
                content_blocks.insert(0, AnthropicTextBlock(type="text", text=thinking_content))

        # If there is no content, add an empty text block
        if not content_blocks:
            content_blocks.append(AnthropicTextBlock(type="text", text=""))

        # Convert stop_reason
        stop_reason_map = {
            StopReason.END_TURN: "end_turn",
            StopReason.MAX_TOKENS: "max_tokens",
            StopReason.TOOL_USE: "tool_use",
            StopReason.STOP_SEQUENCE: "stop_sequence",
        }
        stop_reason = stop_reason_map.get(response.stop_reason, "end_turn")

        return AnthropicMessage(
            id=response.id,
            type="message",
            role="assistant",
            content=content_blocks,
            model=response.model,
            stop_reason=stop_reason,
            stop_sequence=None,
            usage=AnthropicUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
        )

    # ========================================================================
    # High-level method: think (backward compatible)
    # ========================================================================

    async def think(
        self,
        prompt: str,
        context: Context | None = None,
        system: str | None = None,
        tools: list[ToolParam] | None = None,
        max_tokens: int | None = None,
        thinking_depth: str | None = None,
    ) -> Response:
        """
        Send a thinking request to the LLM (via LLMClient).

        Args:
            prompt: user input
            context: conversation context
            system: system prompt
            tools: list of available tools
            max_tokens: max output tokens (defaults to self.max_tokens when omitted)
            thinking_depth: thinking depth ('low'/'medium'/'high'/None)

        Returns:
            Response object
        """
        # Build the message list
        messages: list[MessageParam] = []
        if context and context.messages:
            messages.extend(context.messages)
        messages.append({"role": "user", "content": prompt})

        # Determine system prompt and tools
        sys_prompt = system or (context.system if context else "")
        tool_list = tools or (context.tools if context else [])

        # Convert to LLMClient format
        llm_messages = self._convert_messages_to_llm(messages)
        llm_tools = self._convert_tools_to_llm(tool_list) if tool_list else None

        # Logging
        logger.info(
            f"[LLM REQUEST] messages={len(llm_messages)}, tools={len(tool_list) if tool_list else 0}"
        )

        # Debug output: save the full request to a file
        req_id = self._dump_llm_request(
            sys_prompt, llm_messages, llm_tools, caller="_chat_with_llm_client"
        )

        # Call LLMClient
        response = await self._llm_client.chat(
            messages=llm_messages,
            system=sys_prompt,
            tools=llm_tools,
            max_tokens=max_tokens or self.max_tokens,
            enable_thinking=self.is_thinking_enabled(),
            thinking_depth=thinking_depth,
        )

        # Save the response to a debug file
        self._dump_llm_response(response, caller="_chat_with_llm_client", request_id=req_id)

        self._record_usage(response)

        # Convert response
        content = response.text
        tool_calls = [
            {
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            }
            for tc in response.tool_calls
        ]

        # Logging
        logger.info(f"[LLM RESPONSE] content_len={len(content)}, tool_calls={len(tool_calls)}")

        return Response(
            content=content,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason.value,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )

    # ========================================================================
    # Helper methods
    # ========================================================================

    def _dump_llm_request(
        self, system: str, messages: list, tools: list, caller: str = "unknown"
    ) -> str:
        """
        Save an LLM request to a debug file.

        Used to diagnose context issues by saving the full system prompt and messages to disk.

        Args:
            system: system prompt
            messages: message list (may be Message objects or dicts)
            tools: tool list
            caller: caller identifier

        Returns:
            request_id: request ID, used to correlate with the response file
        """
        try:
            debug_dir = settings.project_root / "data" / "llm_debug"
            debug_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            request_id = uuid.uuid4().hex[:8]
            debug_file = debug_dir / f"llm_request_{timestamp}_{request_id}.json"

            # ── 1. Serialize messages ──
            serializable_messages = []
            for msg in messages:
                if hasattr(msg, "to_dict"):
                    serializable_messages.append(msg.to_dict())
                elif hasattr(msg, "__dict__"):
                    serializable_messages.append(self._serialize_message(msg))
                elif isinstance(msg, dict):
                    serializable_messages.append(msg)
                else:
                    serializable_messages.append(str(msg))

            # ── 2. Serialize full tool definitions (identical to the tools parameter sent to the LLM API) ──
            full_tools = []
            for t in tools or []:
                if hasattr(t, "name"):
                    # Tool / NamedTuple / dataclass object
                    full_tools.append(
                        {
                            "name": t.name,
                            "description": getattr(t, "description", ""),
                            "input_schema": getattr(t, "input_schema", {}),
                        }
                    )
                elif isinstance(t, dict):
                    full_tools.append(
                        {
                            "name": t.get("name", ""),
                            "description": t.get("description", ""),
                            "input_schema": t.get("input_schema", {}),
                        }
                    )
                else:
                    full_tools.append({"raw": str(t)})

            # ── 3. Token estimation (mixed CJK/English aware: Chinese ~1.5 chars/token, English/JSON ~4 chars/token) ──
            from .context_manager import ContextManager as _CM

            _est = _CM.static_estimate_tokens
            system_length = len(system) if system else 0
            estimated_system_tokens = _est(system) if system else 0
            messages_text = json.dumps(serializable_messages, ensure_ascii=False)
            estimated_messages_tokens = _est(messages_text)
            tools_text = json.dumps(full_tools, ensure_ascii=False)
            estimated_tools_tokens = _est(tools_text)
            total_estimated_tokens = (
                estimated_system_tokens + estimated_messages_tokens + estimated_tools_tokens
            )

            # ── 4. Build the full debug payload (matches the structure of the request sent to the LLM) ──
            debug_data: dict[str, Any] = {
                "timestamp": datetime.now().isoformat(),
                "caller": caller,
                "llm_request": {
                    "system": system,
                    "messages": serializable_messages,
                    "tools": full_tools,
                },
                "stats": {
                    "system_prompt_length": system_length,
                    "system_prompt_tokens": estimated_system_tokens,
                    "messages_count": len(messages),
                    "messages_tokens": estimated_messages_tokens,
                    "tools_count": len(full_tools),
                    "tools_tokens": estimated_tools_tokens,
                    "total_estimated_tokens": total_estimated_tokens,
                },
            }
            if self._trace_context:
                debug_data["context"] = dict(self._trace_context)

            with open(debug_file, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, ensure_ascii=False, indent=2, default=str)

            # Log and warn when the token count is too large
            token_detail = f"system={estimated_system_tokens}, messages={estimated_messages_tokens}, tools={estimated_tools_tokens}"
            if total_estimated_tokens > 50000:
                logger.warning(
                    f"[LLM DEBUG] ⚠️ Very large context! Estimated {total_estimated_tokens} tokens ({token_detail})"
                )
            elif total_estimated_tokens > 30000:
                logger.warning(
                    f"[LLM DEBUG] Large context: {total_estimated_tokens} tokens ({token_detail})"
                )
            else:
                logger.info(
                    f"[LLM DEBUG] Request saved: {total_estimated_tokens} tokens ({token_detail})"
                )

            # Clean up debug files older than 3 days
            self._cleanup_old_debug_files(debug_dir, max_age_days=3)

            return request_id

        except Exception as e:
            logger.warning(f"[LLM DEBUG] Failed to save debug file: {e}")
            return uuid.uuid4().hex[:8]  # Even if saving fails, return an ID so the response can correlate

    def _dump_llm_response(self, response, caller: str = "unknown", request_id: str = "") -> None:
        """
        Save the LLM response to a debug file (symmetric with _dump_llm_request).

        Args:
            response: LLMResponse object
            caller: caller identifier
            request_id: corresponding request ID (used to correlate with the request file)
        """
        try:
            debug_dir = settings.project_root / "data" / "llm_debug"
            debug_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            debug_file = debug_dir / f"llm_response_{timestamp}_{request_id}.json"

            # Serialize content blocks
            content_blocks = self._serialize_response_content(response)

            debug_data: dict[str, Any] = {
                "timestamp": datetime.now().isoformat(),
                "caller": caller,
                "request_id": request_id,
                "llm_response": {
                    "model": getattr(response, "model", ""),
                    "stop_reason": str(getattr(response, "stop_reason", "")),
                    "usage": {
                        "input_tokens": getattr(response.usage, "input_tokens", 0)
                        if hasattr(response, "usage")
                        else 0,
                        "output_tokens": getattr(response.usage, "output_tokens", 0)
                        if hasattr(response, "usage")
                        else 0,
                    },
                    "content": content_blocks,
                },
            }
            if self._trace_context:
                debug_data["context"] = dict(self._trace_context)

            with open(debug_file, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, ensure_ascii=False, indent=2, default=str)

            # Summary log
            text_len = sum(
                len(b.get("text", "")) for b in content_blocks if b.get("type") == "text"
            )
            tool_count = sum(1 for b in content_blocks if b.get("type") == "tool_use")
            in_tokens = debug_data["llm_response"]["usage"]["input_tokens"]
            out_tokens = debug_data["llm_response"]["usage"]["output_tokens"]
            logger.info(
                f"[LLM DEBUG] Response saved: text_len={text_len}, tool_calls={tool_count}, "
                f"tokens_in={in_tokens}, tokens_out={out_tokens} (request_id={request_id})"
            )

        except Exception as e:
            logger.warning(f"[LLM DEBUG] Failed to save response debug file: {e}")

    def _serialize_response_content(self, response) -> list[dict]:
        """
        Serialize the content blocks of an LLM response; supports text/thinking/tool_use.

        Truncation rules:
        - text: preserved in full
        - thinking: truncated to 500 characters
        - tool_use: name/id preserved in full, input preserved in full (helps diagnose truncation issues)
        """
        blocks = []

        # LLMResponse object
        if hasattr(response, "text") and not hasattr(response, "content"):
            # Simple text response
            blocks.append({"type": "text", "text": response.text or ""})
            for tc in getattr(response, "tool_calls", []):
                input_str = (
                    json.dumps(tc.input, ensure_ascii=False, default=str)
                    if isinstance(tc.input, dict)
                    else str(tc.input)
                )
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": input_str,
                    }
                )
            return blocks

        # Anthropic Message format
        for block in getattr(response, "content", []):
            block_type = getattr(block, "type", None) or (
                block.get("type") if isinstance(block, dict) else None
            )
            if block_type == "text":
                text = (
                    getattr(block, "text", "")
                    if not isinstance(block, dict)
                    else block.get("text", "")
                )
                blocks.append({"type": "text", "text": text})
            elif block_type == "thinking":
                thinking = (
                    getattr(block, "thinking", "")
                    if not isinstance(block, dict)
                    else block.get("thinking", "")
                )
                blocks.append({"type": "thinking", "thinking": str(thinking)})
            elif block_type == "tool_use":
                if isinstance(block, dict):
                    name = block.get("name", "")
                    bid = block.get("id", "")
                    inp = block.get("input", {})
                else:
                    name = getattr(block, "name", "")
                    bid = getattr(block, "id", "")
                    inp = getattr(block, "input", {})
                input_str = (
                    json.dumps(inp, ensure_ascii=False, default=str)
                    if isinstance(inp, dict)
                    else str(inp)
                )
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": bid,
                        "name": name,
                        "input": input_str,
                    }
                )
            else:
                blocks.append({"type": str(block_type), "raw": str(block)})

        return blocks

    def _cleanup_old_debug_files(self, debug_dir: Path, max_age_days: int = 7) -> None:
        """Clean up debug files (request + response) older than the given number of days."""
        try:
            import os
            from datetime import timedelta

            cutoff_time = datetime.now() - timedelta(days=max_age_days)
            deleted_count = 0

            for pattern in ("llm_request_*.json", "llm_response_*.json"):
                for file in debug_dir.glob(pattern):
                    try:
                        mtime = datetime.fromtimestamp(os.path.getmtime(file))
                        if mtime < cutoff_time:
                            file.unlink()
                            deleted_count += 1
                    except Exception:
                        pass

            if deleted_count > 0:
                logger.debug(
                    f"[LLM DEBUG] Cleaned up {deleted_count} old debug files "
                    f"(older than {max_age_days} days)"
                )

        except Exception as e:
            logger.warning(f"[LLM DEBUG] Failed to cleanup old files: {e}")

    def _serialize_message(self, msg) -> dict:
        """Serialize a Message object into a dict"""
        result = {"role": getattr(msg, "role", "unknown")}

        content = getattr(msg, "content", None)
        if isinstance(content, str):
            result["content"] = content
        elif isinstance(content, list):
            result["content"] = []
            for block in content:
                if hasattr(block, "__dict__"):
                    block_dict = {"type": getattr(block, "type", "unknown")}
                    # Handle common block attributes
                    if hasattr(block, "text"):
                        block_dict["text"] = block.text
                    if hasattr(block, "id"):
                        block_dict["id"] = block.id
                    if hasattr(block, "name"):
                        block_dict["name"] = block.name
                    if hasattr(block, "input"):
                        block_dict["input"] = block.input
                    if hasattr(block, "content"):
                        block_dict["content"] = block.content
                    if hasattr(block, "thinking"):
                        block_dict["thinking"] = block.thinking
                    result["content"].append(block_dict)
                elif isinstance(block, dict):
                    result["content"].append(dict(block))
                else:
                    result["content"].append(str(block))
        else:
            result["content"] = str(content) if content else None

        # Add reasoning_content (if present)
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            result["reasoning_content"] = msg.reasoning_content

        return result

    async def health_check(self) -> dict[str, bool]:
        """Check health status of all endpoints"""
        return await self._llm_client.health_check()

    # ========================================================================
    # Dynamic model switching
    # ========================================================================

    def switch_model(
        self,
        endpoint_name: str,
        hours: float = 12,
        reason: str = "",
        conversation_id: str | None = None,
    ) -> tuple[bool, str]:
        """
        Temporarily switch to the specified model.

        Args:
            endpoint_name: endpoint name
            hours: validity duration in hours, default 12 hours
            reason: reason for the switch

        Returns:
            (success, message)
        """
        return self._llm_client.switch_model(
            endpoint_name, hours, reason, conversation_id=conversation_id
        )

    def get_fallback_model(self, conversation_id: str | None = None) -> str:
        """
        Get the name of the next-priority fallback model endpoint.

        Sorts endpoints by configured priority and returns the next healthy
        endpoint after the current one. Used by TaskMonitor for dynamic
        fallback model selection, replacing hard-coded values.

        Args:
            conversation_id: optional conversation ID

        Returns:
            next endpoint name, or an empty string if no fallback is available
        """
        next_ep = self._llm_client.get_next_endpoint(conversation_id)
        return next_ep or ""

    def restore_default_model(self, conversation_id: str | None = None) -> tuple[bool, str]:
        """
        Restore the default model (clear any temporary override).

        Returns:
            (success, message)
        """
        return self._llm_client.restore_default(conversation_id=conversation_id)

    def get_current_model_info(self, conversation_id: str | None = None) -> dict:
        """
        Get info about the currently used model.

        Args:
            conversation_id: conversation ID (when provided, checks for a per-conversation override)

        Returns:
            model info dict
        """
        model = self._llm_client.get_current_model(conversation_id=conversation_id)
        if not model:
            return {"error": "No model available"}

        return {
            "name": model.name,
            "model": model.model,
            "provider": model.provider,
            "is_healthy": model.is_healthy,
            "is_override": model.is_override,
            "capabilities": model.capabilities,
            "note": model.note,
        }

    def list_available_models(self) -> list[dict]:
        """
        List all available models.

        Returns:
            list of model info dicts
        """
        models = self._llm_client.list_available_models()
        return [
            {
                "name": m.name,
                "model": m.model,
                "provider": m.provider,
                "priority": m.priority,
                "is_healthy": m.is_healthy,
                "is_current": m.is_current,
                "is_override": m.is_override,
                "capabilities": m.capabilities,
                "note": m.note,
            }
            for m in models
        ]

    def get_override_status(self) -> dict | None:
        """
        Get the current override status.

        Returns:
            override status info, or None when there is no override
        """
        return self._llm_client.get_override_status()

    def update_model_priority(self, priority_order: list[str]) -> tuple[bool, str]:
        """
        Update the model priority order (persisted permanently).

        Args:
            priority_order: list of model names, ordered from highest to lowest priority

        Returns:
            (success, message)
        """
        return self._llm_client.update_priority(priority_order)

    async def plan(self, task: str, context: Context | None = None) -> str:
        """Generate an execution plan for a task"""
        prompt = f"""Please create a detailed execution plan for the following task:

Task: {task}

Requirements:
1. Break it down into concrete steps
2. Identify the tools and skills required
3. Consider possible failure cases and fallback options
4. Estimate the complexity of each step

Please output the plan in Markdown format."""

        response = await self.think(prompt, context)
        return response.content

    async def generate_code(
        self,
        description: str,
        language: str = "python",
        context: Context | None = None,
    ) -> str:
        """Generate code"""
        prompt = f"""Please generate {language} code for the following functionality:

{description}

Requirements:
1. The code should be complete and runnable
2. Include all necessary import statements
3. Add appropriate comments and docstrings
4. Follow {language} best practices
5. Include type hints if it is a class

Output only the code, no explanations."""

        response = await self.think(prompt, context)

        # Extract code block
        code = response.content
        if f"```{language}" in code:
            start = code.find(f"```{language}") + len(f"```{language}")
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()
        elif "```" in code:
            start = code.find("```") + 3
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()

        return code

    async def analyze_error(
        self,
        error: str,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Analyze an error and provide a solution"""
        prompt = f"""Please analyze the following error and provide a solution:

Error message:
{error}

{"Context:" + context if context else ""}

Please provide:
1. Root cause analysis
2. Possible solutions (sorted by priority)
3. How to avoid similar errors

Output as JSON:
{{
    "cause": "root cause",
    "solutions": ["solution 1", "solution 2"],
    "prevention": "prevention measures"
}}"""

        response = await self.think(prompt)

        import json

        try:
            content = response.content
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip()
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end].strip()

            return json.loads(content)
        except json.JSONDecodeError:
            return {
                "cause": "Unable to parse error analysis",
                "solutions": [response.content],
                "prevention": "",
            }

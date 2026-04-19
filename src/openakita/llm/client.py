"""
Unified LLM Client

Provides a unified LLM invocation interface, supporting:
- Multi-endpoint configuration
- Automatic failover
- Capability routing (automatically selects the right endpoint based on the request)
- Health checks
- Dynamic model switching (temporary/permanent)
- Message normalization pipeline
- Per-request observability (TTFT, stall detection, structured metrics)
- Exponential backoff retry + Retry-After + 429/529 differentiation
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..core.errors import UserCancelledError
from .config import get_default_config_path, load_endpoints_config
from .normalize import normalize_messages_for_api
from .providers.anthropic import AnthropicProvider
from .providers.base import LLMProvider
from .providers.openai import OpenAIProvider
from .providers.openai_responses import OpenAIResponsesProvider
from .retry import calculate_retry_delay
from .types import (
    AllEndpointsFailedError,
    AudioBlock,
    AuthenticationError,
    ContentBlock,
    DocumentBlock,
    EndpointConfig,
    ImageBlock,
    ImageContent,
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    TextBlock,
    ThinkingBlock,
    Tool,
    ToolResultBlock,
    ToolUseBlock,
    VideoBlock,
)

logger = logging.getLogger(__name__)


def _friendly_error_hint(failed_providers: list | None = None, last_error: str = "") -> str:
    """Generate user-friendly hint messages based on error categories of failed endpoints.

    Returns a user-facing hint helping the user understand the issue and take action.
    """
    from .error_types import FailoverReason

    hints: list[str] = []
    categories: set[str] = set()

    if failed_providers:
        for p in failed_providers:
            cat = getattr(p, "error_category", "")
            if cat:
                categories.add(cat)

    if FailoverReason.QUOTA in categories:
        hints.append("API quota exhausted. Please top up or upgrade the plan on the corresponding platform; service will auto-resume after top-up.")
    if FailoverReason.AUTH in categories:
        hints.append("API authentication failed. Please check whether the API key is correct and not expired.")
    if FailoverReason.TRANSIENT in categories:
        hints.append("Network timeout / connection failure detected. Please check your network connection and proxy settings.")
    if FailoverReason.STRUCTURAL in categories:
        hints.append("Request format error detected. This is usually a model compatibility issue; please try switching to another model.")

    if not hints:
        # Generic hint when the error cannot be categorized
        hints.append("Please check the API key, network connection, and account balance.")

    return " ".join(hints)


# ==================== Dynamic switching data structures ====================


@dataclass
class EndpointOverride:
    """Endpoint temporary override configuration"""

    endpoint_name: str  # Name of the endpoint being overridden to
    expires_at: datetime  # Expiration time
    created_at: datetime = field(default_factory=datetime.now)
    reason: str = ""  # Switch reason (optional)

    @property
    def is_expired(self) -> bool:
        """Check whether expired"""
        return datetime.now() >= self.expires_at

    @property
    def remaining_hours(self) -> float:
        """Remaining valid time (hours)"""
        if self.is_expired:
            return 0.0
        delta = self.expires_at - datetime.now()
        return delta.total_seconds() / 3600

    def to_dict(self) -> dict:
        """Convert to dict (for serialization)"""
        return {
            "endpoint_name": self.endpoint_name,
            "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EndpointOverride":
        """Create from dict (for deserialization)"""
        return cls(
            endpoint_name=data["endpoint_name"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            reason=data.get("reason", ""),
        )


@dataclass
class ModelInfo:
    """Model information (for list display)"""

    name: str  # Endpoint name
    model: str  # Model name
    provider: str  # Provider
    priority: int  # Priority
    is_healthy: bool  # Health status
    is_current: bool  # Whether currently in use
    is_override: bool  # Whether a temporary override
    capabilities: list[str]  # Supported capabilities
    note: str = ""  # Notes


class LLMClient:
    """Unified LLM client"""

    # Default temporary switch validity (hours)
    DEFAULT_OVERRIDE_HOURS = 12

    # Global LLM concurrency control: limits the number of in-flight requests to prevent concurrency storms from crushing the event loop
    DEFAULT_MAX_CONCURRENT = 20
    _global_semaphore: asyncio.Semaphore | None = None
    _global_semaphore_loop_id: int | None = None
    _global_semaphore_value: int = 0
    _global_inflight: int = 0  # Current in-flight request count (for monitoring)

    # Endpoints with auth failures are permanently skipped for the process lifetime (requires config change + restart or reload to recover)
    _auth_failed_endpoints: set[str] = set()
    _auth_logged_endpoints: set[str] = set()  # Log the warning only once

    @classmethod
    def _get_semaphore(cls, max_concurrent: int = 0) -> asyncio.Semaphore:
        """Get or create the global concurrency semaphore (bound to the current event loop)."""
        target = max_concurrent or cls.DEFAULT_MAX_CONCURRENT
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = None
        if (
            cls._global_semaphore is None
            or cls._global_semaphore_loop_id != loop_id
            or cls._global_semaphore_value != target
        ):
            cls._global_semaphore = asyncio.Semaphore(target)
            cls._global_semaphore_loop_id = loop_id
            cls._global_semaphore_value = target
            cls._global_inflight = 0
        return cls._global_semaphore

    @classmethod
    def get_concurrency_stats(cls) -> dict:
        """Return current concurrency stats (for use by health monitoring APIs)."""
        return {
            "inflight": cls._global_inflight,
            "max_concurrent": cls._global_semaphore_value or cls.DEFAULT_MAX_CONCURRENT,
        }

    def __init__(
        self,
        config_path: Path | None = None,
        endpoints: list[EndpointConfig] | None = None,
    ):
        """
        Initialize the LLM client

        Args:
            config_path: Config file path
            endpoints: Directly supplied endpoint configuration (takes precedence over config_path)
        """
        self._endpoints: list[EndpointConfig] = []
        self._providers: dict[str, LLMProvider] = {}
        self._settings: dict = {}
        self._config_path: Path | None = config_path

        # Dynamic switching state
        self._endpoint_override: EndpointOverride | None = None
        # per-conversation temporary override (for concurrency isolation)
        self._conversation_overrides: dict[str, EndpointOverride] = {}

        # Endpoint affinity: record the last successful endpoint name
        # When there's a tool context, prefer the last-successful endpoint (avoid failover then returning to a failing high-priority endpoint)
        self._last_success_endpoint: str | None = None
        self._endpoint_lock = asyncio.Lock()

        if endpoints:
            self._endpoints = sorted(endpoints, key=lambda x: x.priority)
        elif config_path or get_default_config_path().exists():
            self._config_path = config_path or get_default_config_path()
            self._endpoints, _, _, self._settings = load_endpoints_config(self._config_path)

        # Create Provider instances
        self._init_providers()

    def reload(self) -> bool:
        """Hot reload: re-read the config file and rebuild all Providers.

        Returns:
            True if reload succeeded, False if the config file is unavailable.
        """
        # The backend may start when the config file does not yet exist (e.g. auto-start), in which case _config_path is None.
        # The user later creates the config via the Setup Center and triggers a reload;
        # we must re-detect the default path here, otherwise reload would be permanently disabled.
        if not self._config_path:
            default = get_default_config_path()
            if default.exists():
                self._config_path = default
                logger.info(f"reload(): discovered config at {default}")
            else:
                logger.warning("reload() called but no config_path available")
                return False
        if not self._config_path.exists():
            logger.warning("reload() called but config file not found: %s", self._config_path)
            return False
        try:
            new_endpoints, _, _, new_settings = load_endpoints_config(self._config_path)
            self._endpoints = new_endpoints
            self._settings = new_settings
            self._providers.clear()
            self._init_providers()
            self._last_success_endpoint = None  # Reset endpoint affinity after reload
            LLMClient._auth_failed_endpoints.clear()  # Clear auth failure records after reload
            LLMClient._auth_logged_endpoints.clear()
            logger.info(
                f"LLMClient reloaded from {self._config_path}: "
                f"{len(self._endpoints)} endpoints, {len(self._providers)} providers"
            )
            return True
        except Exception as e:
            logger.error(f"LLMClient reload failed: {e}", exc_info=True)
            return False

    def _init_providers(self):
        """Initialize all Providers"""
        for ep in self._endpoints:
            provider = self._create_provider(ep)
            if provider:
                self._providers[ep.name] = provider

    async def startup_health_check(self) -> dict[str, str]:
        """Lightweight health check against all endpoints at startup.

        Sends a tiny request (1 token) to each endpoint to detect auth and network issues.
        Endpoints with auth failures are immediately added to _auth_failed_endpoints.

        Returns:
            {endpoint_name: "ok" | "auth_failed" | "error: ..."}
        """
        results: dict[str, str] = {}
        for name, provider in self._providers.items():
            try:
                request = LLMRequest(
                    messages=[Message(role="user", content="hi")],
                    system="Respond with 'ok'",
                    max_tokens=1,
                )
                await asyncio.wait_for(provider.chat(request), timeout=15.0)
                results[name] = "ok"
                logger.info(f"[HealthCheck] endpoint={name} status=ok")
            except AuthenticationError as e:
                LLMClient._auth_failed_endpoints.add(name)
                if name not in LLMClient._auth_logged_endpoints:
                    LLMClient._auth_logged_endpoints.add(name)
                    logger.error(
                        f"[HealthCheck] endpoint={name} auth_failed: {e}. "
                        f"Permanently disabled until config reload."
                    )
                results[name] = "auth_failed"
            except (asyncio.TimeoutError, TimeoutError):
                results[name] = "error: timeout (15s)"
                logger.warning(f"[HealthCheck] endpoint={name} timed out (15s)")
            except Exception as e:
                err_msg = str(e)[:200]
                results[name] = f"error: {err_msg}"
                logger.warning(f"[HealthCheck] endpoint={name} failed: {err_msg}")
        return results

    def _create_provider(self, config: EndpointConfig) -> LLMProvider | None:
        """Create a Provider from config - first check the plugin registry, then fall back to built-ins"""
        try:
            from ..plugins import PLUGIN_PROVIDER_MAP

            plugin_cls = PLUGIN_PROVIDER_MAP.get(config.api_type)
            if plugin_cls:
                try:
                    return plugin_cls(config)
                except Exception as e:
                    logger.error(
                        f"Plugin provider '{config.api_type}' failed to init: {e}, "
                        f"skipping endpoint '{config.name}'"
                    )
                    return None
        except ImportError:
            pass

        try:
            if config.api_type == "anthropic":
                return AnthropicProvider(config)
            elif config.api_type == "openai":
                return OpenAIProvider(config)
            elif config.api_type == "openai_responses":
                return OpenAIResponsesProvider(config)
            else:
                logger.warning(f"Unknown api_type '{config.api_type}' for endpoint '{config.name}'")
                return None
        except Exception as e:
            logger.error(f"Failed to create provider for '{config.name}': {e}")
            return None

    @property
    def endpoints(self) -> list[EndpointConfig]:
        """Get all endpoint configurations"""
        return self._endpoints

    @property
    def providers(self) -> dict[str, LLMProvider]:
        """Get all Providers"""
        return self._providers

    async def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[Tool] | None = None,
        max_tokens: int = 0,
        temperature: float = 1.0,
        enable_thinking: bool = False,
        thinking_depth: str | None = None,
        conversation_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        Unified chat interface

        Automatically handles:
        1. Inferring required capabilities from request content
        2. Filtering endpoints that support the required capabilities
        3. Trying calls in priority order
        4. Automatic failover

        Args:
            messages: Message list
            system: System prompt
            tools: Tool definition list
            max_tokens: Maximum output tokens
            temperature: Temperature
            enable_thinking: Whether to enable thinking mode
            thinking_depth: Thinking depth ('low'/'medium'/'high')
            **kwargs: Additional parameters

        Returns:
            Unified response format

        Raises:
            UnsupportedMediaError: Video content but no video-capable endpoint
            AllEndpointsFailedError: All endpoints failed
        """
        sem = self._get_semaphore(self._settings.get("max_concurrent", 0))
        async with sem:
            LLMClient._global_inflight += 1
            try:
                return await self._chat_impl(
                    messages=messages,
                    system=system,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    enable_thinking=enable_thinking,
                    thinking_depth=thinking_depth,
                    conversation_id=conversation_id,
                    cancel_event=cancel_event,
                    **kwargs,
                )
            finally:
                LLMClient._global_inflight -= 1

    async def _chat_impl(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[Tool] | None = None,
        max_tokens: int = 0,
        temperature: float = 1.0,
        enable_thinking: bool = False,
        thinking_depth: str | None = None,
        conversation_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
        **kwargs,
    ) -> LLMResponse:
        # Message normalization: unify format before sending
        normalized_msgs = self._normalize_messages(messages)

        request = LLMRequest(
            messages=normalized_msgs,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_thinking=enable_thinking,
            thinking_depth=thinking_depth,
            extra_params=kwargs.get("extra_params"),
        )

        # Infer required capabilities
        require_tools = bool(tools)
        require_vision = self._has_images(messages)
        require_video = self._has_videos(messages)
        require_audio = self._has_audio(messages)
        require_pdf = self._has_documents(messages)
        require_thinking = bool(enable_thinking)

        # Tool context detection: be more conservative about failover
        #
        # Key reasons:
        # - Tool-chain "continuity" is not just message format compatibility (OpenAI-compatible / Anthropic)
        # - It also includes model-specific chain-of-thought / metadata continuity (e.g. MiniMax M2.1 interleaved thinking)
        #   If this info is not fully preserved/returned, or if the model is switched mid-way, tool-call quality drops noticeably.
        #
        # So by default: whenever a tool context is detected, disable failover (stay on the same endpoint/model).
        # But allow "same-protocol failover" to be explicitly enabled via config (off by default).
        has_tool_context = self._has_tool_context(messages)
        allow_failover = not has_tool_context

        if has_tool_context:
            logger.debug(
                "[LLM] Tool context detected in messages; failover disabled by default "
                "(set settings.allow_failover_with_tool_context=true to override)."
            )

        # Filter endpoints supporting the required capabilities
        # When there's a tool context, pass the endpoint affinity: prefer the last-successful endpoint
        eligible = self._filter_eligible_endpoints(
            require_tools=require_tools,
            require_vision=require_vision,
            require_video=require_video,
            require_thinking=require_thinking,
            require_audio=require_audio,
            require_pdf=require_pdf,
            conversation_id=conversation_id,
            prefer_endpoint=self._last_success_endpoint if has_tool_context else None,
        )

        # Optional: enable failover under tool context (only when explicitly configured)
        if has_tool_context and eligible:
            if self._settings.get("allow_failover_with_tool_context", False):
                # By default, only allow switching within the same protocol; avoid anthropic/openai mixing and incompatible tool messages
                api_types = {p.config.api_type for p in eligible}
                if len(api_types) == 1:
                    allow_failover = True
                    logger.debug(
                        "[LLM] Tool context failover explicitly enabled; "
                        f"api_type={next(iter(api_types))}."
                    )
                else:
                    allow_failover = False
                    logger.debug(
                        "[LLM] Tool context failover requested but eligible endpoints have mixed "
                        f"api_types={sorted(api_types)}; failover remains disabled."
                    )

        if eligible:
            return await self._try_endpoints(
                eligible, request, allow_failover=allow_failover, cancel_event=cancel_event
            )

        # eligible is empty - use the shared fallback strategy
        providers = await self._resolve_providers_with_fallback(
            request=request,
            require_tools=require_tools,
            require_vision=require_vision,
            require_video=require_video,
            require_thinking=require_thinking,
            require_audio=require_audio,
            require_pdf=require_pdf,
            conversation_id=conversation_id,
            prefer_endpoint=self._last_success_endpoint if has_tool_context else None,
            cancel_event=cancel_event,
        )
        return await self._try_endpoints(
            providers, request, allow_failover=allow_failover, cancel_event=cancel_event
        )

    async def chat_stream(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[Tool] | None = None,
        max_tokens: int = 0,
        temperature: float = 1.0,
        enable_thinking: bool = False,
        thinking_depth: str | None = None,
        conversation_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
        **kwargs,
    ) -> AsyncIterator[dict]:
        """
        Streaming chat interface (with full fallback strategy)

        Shares fallback logic with chat(): thinking soft fallback, cooldown waiting, multi-endpoint rotation.
        Streaming-specific behavior: once events start being produced (yielded=True), mid-stream failures no longer switch endpoints
        (avoiding sending mixed partial responses to the client).

        Args:
            messages: Message list
            system: System prompt
            tools: Tool definition list
            max_tokens: Maximum output tokens
            temperature: Temperature
            enable_thinking: Whether to enable thinking mode
            thinking_depth: Thinking depth ('low'/'medium'/'high')
            conversation_id: Conversation ID
            cancel_event: Cancel event (matches chat() signature)
            **kwargs: Additional parameters

        Yields:
            Streaming events
        """
        sem = self._get_semaphore(self._settings.get("max_concurrent", 0))
        async with sem:
            LLMClient._global_inflight += 1
            try:
                async for event in self._chat_stream_impl(
                    messages=messages,
                    system=system,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    enable_thinking=enable_thinking,
                    thinking_depth=thinking_depth,
                    conversation_id=conversation_id,
                    cancel_event=cancel_event,
                    **kwargs,
                ):
                    yield event
            finally:
                LLMClient._global_inflight -= 1

    async def _chat_stream_impl(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[Tool] | None = None,
        max_tokens: int = 0,
        temperature: float = 1.0,
        enable_thinking: bool = False,
        thinking_depth: str | None = None,
        conversation_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
        **kwargs,
    ) -> AsyncIterator[dict]:
        """Internal implementation of chat_stream() (already runs under semaphore protection)."""
        normalized_msgs = self._normalize_messages(messages)

        request = LLMRequest(
            messages=normalized_msgs,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_thinking=enable_thinking,
            thinking_depth=thinking_depth,
            extra_params=kwargs.get("extra_params"),
        )

        require_tools = bool(tools)
        require_vision = self._has_images(messages)
        require_video = self._has_videos(messages)
        require_audio = self._has_audio(messages)
        require_pdf = self._has_documents(messages)
        require_thinking = bool(enable_thinking)

        eligible = self._filter_eligible_endpoints(
            require_tools=require_tools,
            require_vision=require_vision,
            require_video=require_video,
            require_thinking=require_thinking,
            require_audio=require_audio,
            require_pdf=require_pdf,
            conversation_id=conversation_id,
        )

        if not eligible:
            eligible = await self._resolve_providers_with_fallback(
                request=request,
                require_tools=require_tools,
                require_vision=require_vision,
                require_video=require_video,
                require_thinking=require_thinking,
                require_audio=require_audio,
                require_pdf=require_pdf,
                conversation_id=conversation_id,
                cancel_event=cancel_event,
            )

        _413_retried = False
        last_error: Exception | None = None
        for i, provider in enumerate(eligible):
            if cancel_event and cancel_event.is_set():
                raise UserCancelledError(reason="User requested stop", source="llm_stream")

            yielded = False
            try:
                logger.info(
                    f"[LLM-Stream] endpoint={provider.name} model={provider.model} "
                    f"action=stream_request"
                )
                async for event in provider.chat_stream(request):
                    if cancel_event and cancel_event.is_set():
                        raise UserCancelledError(
                            reason="User requested stop",
                            source="llm_stream_mid",
                        )
                    yielded = True
                    yield event
                async with self._endpoint_lock:
                    self._last_success_endpoint = provider.name
                return

            except (UserCancelledError, asyncio.CancelledError):
                raise

            except LLMError as e:
                last_error = e
                if yielded:
                    logger.error(
                        f"[LLM-Stream] endpoint={provider.name} mid-stream failure: {e}. "
                        f"Cannot failover (partial response already sent)."
                    )
                    raise

                sc = e.status_code

                # 413 auto-recovery: reduce max_tokens and retry same provider
                if sc == 413 and not _413_retried:
                    _413_retried = True
                    current = request.max_tokens or 16384
                    request.max_tokens = max(current // 2, 1024)
                    logger.info(
                        f"[LLM-Stream] endpoint={provider.name} status=413, "
                        f"reducing max_tokens {current} → {request.max_tokens}, "
                        f"retrying same endpoint"
                    )
                    try:
                        async for event in provider.chat_stream(request):
                            if cancel_event and cancel_event.is_set():
                                raise UserCancelledError(
                                    reason="User requested stop",
                                    source="llm_stream_413_retry",
                                )
                            yielded = True
                            yield event
                        async with self._endpoint_lock:
                            self._last_success_endpoint = provider.name
                        return
                    except (UserCancelledError, asyncio.CancelledError):
                        raise
                    except LLMError as retry_e:
                        last_error = retry_e
                        logger.warning(
                            f"[LLM-Stream] endpoint={provider.name} "
                            f"413 retry also failed: {retry_e}"
                        )

                # 429/529/503: backoff before trying next provider
                if sc in (429, 529, 503) and i < len(eligible) - 1:
                    delay = self._get_retry_delay(1, e)
                    logger.info(
                        f"[LLM-Stream] endpoint={provider.name} status={sc}, "
                        f"backoff {delay:.1f}s before next endpoint"
                    )
                    if cancel_event:
                        try:
                            await asyncio.wait_for(
                                cancel_event.wait(),
                                timeout=delay,
                            )
                            raise UserCancelledError(
                                reason="User requested stop",
                                source="llm_stream_backoff",
                            )
                        except (asyncio.TimeoutError, TimeoutError):
                            pass
                    else:
                        await asyncio.sleep(delay)
                else:
                    logger.warning(
                        f"[LLM-Stream] endpoint={provider.name} error={e}"
                        + (", trying next endpoint..." if i < len(eligible) - 1 else "")
                    )

            except Exception as e:
                last_error = e
                if yielded:
                    raise
                provider.mark_unhealthy(str(e))
                logger.warning(
                    f"[LLM-Stream] endpoint={provider.name} unexpected_error={e}"
                    + (", trying next endpoint..." if i < len(eligible) - 1 else ""),
                    exc_info=True,
                )

        hint = _friendly_error_hint(eligible)
        raise AllEndpointsFailedError(
            f"Stream: all {len(eligible)} endpoints failed. {hint} Last error: {last_error}"
        )

    # ==================== Shared fallback strategy ====================

    async def _resolve_providers_with_fallback(
        self,
        request: LLMRequest,
        require_tools: bool = False,
        require_vision: bool = False,
        require_video: bool = False,
        require_thinking: bool = False,
        require_audio: bool = False,
        require_pdf: bool = False,
        conversation_id: str | None = None,
        prefer_endpoint: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> list[LLMProvider]:
        """Shared tiered fallback strategy - used by both chat() and chat_stream()

        This method is called when _filter_eligible_endpoints() returns an empty list.
        It falls back step by step until an available endpoint is found:

        1. Thinking soft fallback: drop the thinking requirement, use a non-thinking endpoint
        2. Wait for cooldown recovery: wait for the shortest transient cooldown (up to 35s)
        3. Force retry: ignore cooldowns, force calls to endpoints matching base capabilities
        4. Final fallback: try all endpoints

        Side effects:
            - May modify request.enable_thinking = False (when thinking is dropped)

        Raises:
            UnsupportedMediaError: Requires video but no video-capable endpoint
            AllEndpointsFailedError: All endpoints have structural errors

        Returns:
            Priority-sorted endpoint list (contains at least one endpoint)
        """
        providers_sorted = sorted(self._providers.values(), key=lambda p: p.config.priority)

        # -- Fallback 1: thinking soft fallback --
        # thinking differs from tools/vision/video: the request still works without it
        # If thinking leaves no available endpoints, fall back to non-thinking mode
        if require_thinking:
            eligible_no_thinking = self._filter_eligible_endpoints(
                require_tools=require_tools,
                require_vision=require_vision,
                require_video=require_video,
                require_thinking=False,
                require_audio=require_audio,
                require_pdf=require_pdf,
                conversation_id=conversation_id,
                prefer_endpoint=prefer_endpoint,
            )
            if eligible_no_thinking:
                logger.info(
                    f"[LLM] No healthy thinking-capable endpoint. "
                    f"Falling back to non-thinking mode "
                    f"({len(eligible_no_thinking)} endpoints available)."
                )
                request.enable_thinking = False
                return eligible_no_thinking

        # -- Fallback 2+3+4: all endpoints are in cooldown --
        # Build the base capability match list (no thinking requirement, ignore health)
        base_capability_matched = [
            p
            for p in providers_sorted
            if (not require_tools or p.config.has_capability("tools"))
            and (not require_vision or p.config.has_capability("vision"))
            and (not require_video or p.config.has_capability("video"))
            and (not require_audio or p.config.has_capability("audio"))
            and (not require_pdf or p.config.has_capability("pdf"))
        ]

        # Multimodal soft fallback: don't hard-fail when video/audio/PDF endpoints don't match
        if not base_capability_matched:
            degraded = []
            if require_video:
                degraded.append("video")
                require_video = False
            if require_audio:
                degraded.append("audio")
                require_audio = False
            if require_pdf:
                degraded.append("pdf")
                require_pdf = False
            if degraded:
                logger.warning(
                    f"[LLM] No endpoint supports {'/'.join(degraded)}. "
                    "Content will be degraded (keyframes/text/STT)."
                )
                base_capability_matched = [
                    p
                    for p in providers_sorted
                    if (not require_tools or p.config.has_capability("tools"))
                    and (not require_vision or p.config.has_capability("vision"))
                ]

        # If thinking was degraded, update the request
        if require_thinking:
            request.enable_thinking = False
            logger.info("[LLM] All endpoints in cooldown. Disabling thinking for fallback attempt.")

        if base_capability_matched:
            unhealthy = [p for p in base_capability_matched if not p.is_healthy]
            unhealthy_count = len(unhealthy)

            if unhealthy_count > 0:
                # Group by error type
                structural = [p for p in unhealthy if p.error_category == "structural"]
                quota_or_auth = [p for p in unhealthy if p.error_category in ("quota", "auth")]
                non_structural = [p for p in unhealthy if p.error_category != "structural"]

                # -- Fallback 2: wait for transient cooldowns to recover --
                transient_like = [
                    p for p in non_structural if p.error_category not in ("quota", "auth")
                ]
                if transient_like:
                    min_transient_cd = min(p.cooldown_remaining for p in transient_like)
                    if 0 < min_transient_cd <= 35:
                        if cancel_event and cancel_event.is_set():
                            raise UserCancelledError(
                                reason="User requested stop", source="llm_cooldown_wait"
                            )
                        logger.info(
                            f"[LLM] All endpoints in cooldown. "
                            f"Waiting {min_transient_cd}s for transient recovery..."
                        )
                        wait_seconds = min(min_transient_cd + 1, 35)
                        if cancel_event:
                            try:
                                await asyncio.wait_for(
                                    cancel_event.wait(),
                                    timeout=wait_seconds,
                                )
                                raise UserCancelledError(
                                    reason="User requested stop",
                                    source="llm_cooldown_wait",
                                )
                            except (asyncio.TimeoutError, TimeoutError):
                                pass
                        else:
                            await asyncio.sleep(wait_seconds)
                        # Refilter after waiting
                        eligible = self._filter_eligible_endpoints(
                            require_tools=require_tools,
                            require_vision=require_vision,
                            require_video=require_video,
                            require_thinking=False,
                            require_audio=require_audio,
                            require_pdf=require_pdf,
                            conversation_id=conversation_id,
                            prefer_endpoint=prefer_endpoint,
                        )
                        if eligible:
                            logger.info(
                                f"[LLM] Recovery detected: "
                                f"{len(eligible)} endpoints available after wait"
                            )
                            return eligible

                # -- All structural errors (400 param errors, etc.); retry is pointless -> raise --
                if structural and len(structural) == unhealthy_count:
                    last_err = structural[0]._last_error or "unknown structural error"
                    min_cd = min(p.cooldown_remaining for p in structural)
                    hint = _friendly_error_hint(structural)
                    raise AllEndpointsFailedError(
                        f"All endpoints failed with structural errors "
                        f"(cooldown {min_cd}s). {hint} Last error: {last_err}",
                        is_structural=True,
                    )

                # -- All quota/auth errors; retry is pointless -> fail fast --
                if quota_or_auth and len(quota_or_auth) == unhealthy_count:
                    last_err = quota_or_auth[0]._last_error or "unknown auth/quota error"
                    categories = sorted({p.error_category for p in quota_or_auth})
                    hint = _friendly_error_hint(quota_or_auth)
                    raise AllEndpointsFailedError(
                        f"All endpoints failed with {'/'.join(categories)} errors. "
                        f"{hint} Last error: {last_err}"
                    )

            # -- Fallback 3: "last-resort bypass" - bypass cooldowns (aligned with Portkey) --
            # Portkey's core rule: when there are no healthy targets, bypass the circuit breaker and try all targets
            # Exclude endpoints with quota/auth errors (these cannot be retried meaningfully)
            retryable = [
                p
                for p in base_capability_matched
                if p.is_healthy or p.error_category not in ("quota", "auth")
            ]
            if retryable:
                logger.warning(
                    f"[LLM] No healthy endpoint available. "
                    f"Bypassing cooldowns for {len(retryable)} endpoints "
                    f"(last resort, Portkey-style)."
                )
                for p in retryable:
                    if not p.is_healthy:
                        p.reset_cooldown()
                return retryable

            # All endpoints are quota/auth -> raise directly; don't send back to _try_endpoints and waste API calls
            last_err = base_capability_matched[0]._last_error or "unknown error"
            categories = sorted({p.error_category for p in base_capability_matched})
            hint = _friendly_error_hint(base_capability_matched)
            raise AllEndpointsFailedError(
                f"All endpoints failed with {'/'.join(categories)} errors. "
                f"{hint} Last error: {last_err}"
            )

        # -- Fallback 4: final fallback - try all endpoints --
        logger.warning(
            f"[LLM] No endpoint matches required capabilities "
            f"(tools={require_tools}, vision={require_vision}, video={require_video}). "
            f"Trying all {len(providers_sorted)} endpoints as last resort."
        )
        return providers_sorted

    # ==================== Endpoint filtering ====================

    def _filter_eligible_endpoints(
        self,
        require_tools: bool = False,
        require_vision: bool = False,
        require_video: bool = False,
        require_thinking: bool = False,
        require_audio: bool = False,
        require_pdf: bool = False,
        conversation_id: str | None = None,
        prefer_endpoint: str | None = None,
    ) -> list[LLMProvider]:
        """Filter endpoints supporting the required capabilities

        Notes:
        - When enable_thinking=True, prefer/require endpoints with thinking capability (avoid capability/format degradation)
        - If there's a temporary override and that endpoint supports the required capabilities, prefer it
        - prefer_endpoint: endpoint affinity; with tool context, pass the last-successful endpoint name
          to promote it to the front of the queue (higher priority than the regular priority sort, but lower than override)
        """
        # Clean up expired overrides
        # 1) Clean up the current conversation's expired override
        if conversation_id:
            ov = self._conversation_overrides.get(conversation_id)
            if ov and ov.is_expired:
                self._conversation_overrides.pop(conversation_id, None)
        # 2) Clean up the global override
        if self._endpoint_override and self._endpoint_override.is_expired:
            logger.info("[LLM] Override expired, restoring default")
            self._endpoint_override = None
        # 3) Periodically clean up all expired conversation overrides (prevent memory leaks)
        #    Only triggered when the accumulation exceeds a threshold, to avoid iterating on every call
        if len(self._conversation_overrides) > 50:
            expired_keys = [k for k, v in self._conversation_overrides.items() if v.is_expired]
            for k in expired_keys:
                self._conversation_overrides.pop(k, None)
            if expired_keys:
                logger.debug(f"[LLM] Cleaned {len(expired_keys)} expired conversation overrides")

        eligible = []
        override_provider = None

        # If there's a temporary override, check the override endpoint (conversation > global)
        effective_override = None
        if conversation_id and conversation_id in self._conversation_overrides:
            effective_override = self._conversation_overrides.get(conversation_id)
        else:
            effective_override = self._endpoint_override

        if effective_override:
            override_name = effective_override.endpoint_name
            if override_name in self._providers:
                provider = self._providers[override_name]
                if provider.is_healthy:
                    override_provider = provider
                    logger.info(f"[LLM] Using user-selected endpoint: {override_name}")
                else:
                    cooldown = provider.cooldown_remaining
                    logger.warning(
                        f"[LLM] User-selected endpoint {override_name} is unhealthy "
                        f"(cooldown: {cooldown}s), falling back to other endpoints"
                    )

        for name, provider in self._providers.items():
            # Permanently skip endpoints with auth failures
            if name in LLMClient._auth_failed_endpoints:
                continue

            # Check health status (including cooldown)
            if not provider.is_healthy:
                cooldown = provider.cooldown_remaining
                if cooldown > 0:
                    logger.debug(f"[LLM] endpoint={name} skipped (cooldown: {cooldown}s remaining)")
                continue

            config = provider.config

            if require_tools and not config.has_capability("tools"):
                continue
            if require_vision and not config.has_capability("vision"):
                continue
            if require_video and not config.has_capability("video"):
                continue
            if require_thinking and not config.has_capability("thinking"):
                continue
            if require_audio and not config.has_capability("audio"):
                continue
            if require_pdf and not config.has_capability("pdf"):
                continue

            eligible.append(provider)

        # Sort by priority
        eligible.sort(key=lambda p: p.config.priority)

        # Endpoint affinity: with tool context, promote the last-successful endpoint to the front of the queue
        # This way, the next call after failover continues using the successful endpoint rather than returning to the high-priority failing endpoint
        if prefer_endpoint:
            prefer_provider = next((p for p in eligible if p.name == prefer_endpoint), None)
            if prefer_provider:
                eligible.remove(prefer_provider)
                eligible.insert(0, prefer_provider)
                logger.debug(
                    f"[LLM] Endpoint affinity: prefer {prefer_endpoint} "
                    f"(last successful endpoint with tool context)"
                )

        # If there's an effective override, put it at the front (override takes priority over affinity)
        if override_provider and override_provider in eligible:
            eligible.remove(override_provider)
            eligible.insert(0, override_provider)
        elif override_provider and override_provider not in eligible:
            # The user's explicitly selected endpoint was excluded by capability inference.
            # Only append it as a fallback when only the thinking capability is missing (thinking inference is the least reliable).
            # When hard capabilities like tools/vision are missing, don't append - this avoids every request failing then falling back, adding latency.
            missing = []
            cfg = override_provider.config
            if require_tools and not cfg.has_capability("tools"):
                missing.append("tools")
            if require_thinking and not cfg.has_capability("thinking"):
                missing.append("thinking")
            if require_vision and not cfg.has_capability("vision"):
                missing.append("vision")
            if require_video and not cfg.has_capability("video"):
                missing.append("video")
            if require_audio and not cfg.has_capability("audio"):
                missing.append("audio")
            if require_pdf and not cfg.has_capability("pdf"):
                missing.append("pdf")

            hard_missing = [m for m in missing if m != "thinking"]
            if not hard_missing:
                # Only missing thinking - append as fallback (at the end); doesn't affect normal endpoint priority
                eligible.append(override_provider)
                logger.info(
                    f"[LLM] User-selected endpoint {override_provider.name} "
                    f"lacks thinking capability; appended as non-thinking fallback"
                )
            elif not eligible:
                # No other available endpoints, must use this one
                eligible.append(override_provider)
                logger.warning(
                    f"[LLM] User-selected endpoint {override_provider.name} "
                    f"may lack capability: {', '.join(missing)}. "
                    f"No alternatives available, using it as last resort."
                )
            else:
                logger.warning(
                    f"[LLM] User-selected endpoint {override_provider.name} "
                    f"lacks hard capabilities: {', '.join(hard_missing)}. "
                    f"Skipping to avoid unnecessary API failures. "
                    f"Using {eligible[0].name} instead."
                )

        return eligible

    @staticmethod
    async def _race_with_cancel(
        awaitable,
        cancel_event: asyncio.Event,
    ) -> LLMResponse:
        """Race an awaitable against a cancellation event.

        Returns the awaitable's result if it completes first.
        Raises UserCancelledError if cancel_event fires first,
        after cleanly cancelling the in-flight task.
        """
        task = asyncio.ensure_future(awaitable)
        cancel_waiter = asyncio.ensure_future(cancel_event.wait())
        try:
            done, pending = await asyncio.wait(
                [task, cancel_waiter],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()
                try:
                    await p
                except (asyncio.CancelledError, Exception):
                    pass

            if task in done:
                return task.result()

            raise UserCancelledError(
                reason="User requested stop",
                source="llm_request_cancelled",
            )
        except BaseException:
            for t in (task, cancel_waiter):
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            raise

    async def _try_with_retry(
        self,
        operation,
        *,
        cancel_event: asyncio.Event | None = None,
        max_attempts: int = 3,
        request: LLMRequest | None = None,
        provider_name: str = "",
    ):
        """Unified retry wrapper, decisions based on structured HTTP status codes.

        - 413: automatically halve max_tokens and retry once
        - 429/529/503: exponential backoff + jitter (cancel-aware)
        - cancel_event: race against the cancel event
        - Errors without a status_code (timeout/connection): fall back to legacy string-matching retry logic

        Not handled (re-raised to caller):
        - AuthenticationError
        - Non-transient errors (structural, content-level, etc.)
        """
        from .retry import should_retry as _legacy_should_retry

        _413_retried = False
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            if cancel_event and cancel_event.is_set():
                raise UserCancelledError(reason="User requested stop", source="llm_retry")

            try:
                if cancel_event:
                    return await self._race_with_cancel(operation(), cancel_event)
                return await operation()

            except (UserCancelledError, asyncio.CancelledError):
                raise
            except AuthenticationError:
                raise

            except LLMError as e:
                last_error = e
                sc = e.status_code

                # 413 Payload Too Large -> auto-reduce max_tokens by 50%, only once
                if sc == 413 and request and not _413_retried:
                    _413_retried = True
                    current = request.max_tokens or 16384
                    request.max_tokens = max(current // 2, 1024)
                    logger.info(
                        f"[LLM] endpoint={provider_name} status=413, "
                        f"reducing max_tokens {current} → {request.max_tokens}"
                    )
                    continue

                # Determine retryability (prefer status_code, fall back to string matching)
                if sc is not None:
                    is_retryable = sc in (429, 529, 503)
                else:
                    is_retryable = _legacy_should_retry(e, attempt, max_attempts)

                if is_retryable and attempt < max_attempts:
                    delay = self._get_retry_delay(attempt, e)
                    logger.info(
                        f"[LLM] endpoint={provider_name} "
                        f"{'status=' + str(sc) if sc else 'transient'} "
                        f"retry {attempt}/{max_attempts} after {delay:.1f}s"
                    )
                    if cancel_event:
                        try:
                            await asyncio.wait_for(
                                cancel_event.wait(),
                                timeout=delay,
                            )
                            raise UserCancelledError(
                                reason="User requested stop",
                                source="llm_retry_backoff",
                            )
                        except (asyncio.TimeoutError, TimeoutError):
                            pass
                    else:
                        await asyncio.sleep(delay)
                    continue

                raise

        if last_error:
            raise last_error

    async def _try_endpoints(
        self,
        providers: list[LLMProvider],
        request: LLMRequest,
        allow_failover: bool = True,
        cancel_event: asyncio.Event | None = None,
    ) -> LLMResponse:
        """Try multiple endpoints, with per-endpoint retry via _try_with_retry.

        Configurable strategy:
        - retry_same_endpoint_first: if True, retry on the current endpoint first even when alternatives exist
        - retry_count: number of retries

        Args:
            providers: Endpoint list (priority sorted)
            request: LLM request
            allow_failover: Controls endpoint switching strategy
                - True: no tool context, fast switch (each endpoint tried only once)
                - False: tool context, retry current endpoint multiple times before switching

        Default strategy: when alternatives exist, fast-switch without retrying the same endpoint (improves response time)
        Tool context: each endpoint is retried retry_count times before switching (preserves continuity)
        All endpoints are tried in priority order regardless of allow_failover
        """
        from .providers.base import COOLDOWN_GLOBAL_FAILURE

        errors: list[str] = []
        failed_providers: list[LLMProvider] = []
        for p in providers:
            p._content_error = False
        retry_count = self._settings.get("retry_count", 2)
        retry_same_first = self._settings.get("retry_same_endpoint_first", False)

        has_fallback = len(providers) > 1
        if retry_same_first or not allow_failover:
            max_attempts = retry_count + 1
        else:
            max_attempts = 1 if (has_fallback and allow_failover) else (retry_count + 1)

        for i, provider in enumerate(providers):
            if cancel_event and cancel_event.is_set():
                raise UserCancelledError(reason="User requested stop", source="llm_try_endpoints")

            _thinking_downgraded = False
            if request.enable_thinking and not provider.config.has_capability("thinking"):
                request.enable_thinking = False
                _thinking_downgraded = True
                logger.info(
                    f"[LLM] endpoint={provider.name} thinking soft-disabled "
                    f"(endpoint lacks thinking capability)"
                )

            try:
                tools_count = len(request.tools) if request.tools else 0
                logger.info(
                    f"[LLM] endpoint={provider.name} model={provider.model} "
                    f"action=request tools={tools_count}"
                )

                response = await self._try_with_retry(
                    lambda p=provider: p.chat(request),
                    cancel_event=cancel_event,
                    max_attempts=max_attempts,
                    request=request,
                    provider_name=provider.name,
                )

                provider.record_success()
                logger.info(
                    f"[LLM] endpoint={provider.name} model={provider.model} "
                    f"action=response tokens_in={response.usage.input_tokens} "
                    f"tokens_out={response.usage.output_tokens}"
                )
                async with self._endpoint_lock:
                    self._last_success_endpoint = provider.name
                response.endpoint_name = provider.name
                return response

            except (UserCancelledError, asyncio.CancelledError):
                raise

            except AuthenticationError as e:
                error_str = str(e)
                from .providers.base import LLMProvider as _BaseProvider

                error_cat = _BaseProvider._classify_error(error_str)
                if error_cat == "quota":
                    logger.error(f"[LLM] endpoint={provider.name} quota_exhausted={e}")
                    provider.mark_unhealthy(error_str, category="quota")
                else:
                    LLMClient._auth_failed_endpoints.add(provider.name)
                    provider.mark_unhealthy(error_str, category="auth")
                    if provider.name not in LLMClient._auth_logged_endpoints:
                        LLMClient._auth_logged_endpoints.add(provider.name)
                        logger.error(
                            f"[LLM] endpoint={provider.name} permanently disabled "
                            f"(auth failure). Fix the API key in settings and reload/restart."
                        )
                errors.append(f"{provider.name}: {e}")
                failed_providers.append(provider)

            except LLMError as e:
                error_str = str(e)
                logger.warning(f"[LLM] endpoint={provider.name} action=error error={e}")
                errors.append(f"{provider.name}: {e}")

                from .providers.base import LLMProvider as _BaseProvider

                auto_category = _BaseProvider._classify_error(error_str)

                if auto_category == "quota":
                    logger.error(
                        f"[LLM] endpoint={provider.name} quota exhausted detected in LLMError, "
                        f"skipping. Error: {error_str[:200]}"
                    )
                    provider.mark_unhealthy(error_str, category="quota")
                    failed_providers.append(provider)

                elif self._try_self_heal(e, request, provider):
                    # Self-healing modified request; retry with healed params
                    try:
                        response = await self._try_with_retry(
                            lambda p=provider: p.chat(request),
                            cancel_event=cancel_event,
                            max_attempts=max_attempts,
                            request=request,
                            provider_name=provider.name,
                        )
                        provider.record_success()
                        logger.info(
                            f"[LLM] endpoint={provider.name} model={provider.model} "
                            f"action=response (healed) tokens_in={response.usage.input_tokens} "
                            f"tokens_out={response.usage.output_tokens}"
                        )
                        async with self._endpoint_lock:
                            self._last_success_endpoint = provider.name
                        response.endpoint_name = provider.name
                        return response
                    except (UserCancelledError, asyncio.CancelledError):
                        raise
                    except Exception as heal_err:
                        logger.warning(
                            f"[LLM] endpoint={provider.name} self-heal retry failed: {heal_err}"
                        )
                        provider.mark_unhealthy(str(heal_err))
                        failed_providers.append(provider)

                else:
                    _err_lower = error_str.lower()
                    non_retryable_patterns = [
                        "invalid_request_error",
                        "invalid_parameter",
                        "messages with role",
                        "must be a response to a preceeding message",
                        "does not support",
                        "not supported",
                        "reasoning_content is missing",
                        "missing reasoning_content",
                        "missing 'reasoning_content'",
                        "data_inspection_failed",
                        "inappropriate content",
                        "(413)",
                        "payload too large",
                        "request entity too large",
                        "larger than allowed",
                    ]
                    is_non_retryable = any(p in _err_lower for p in non_retryable_patterns)

                    if is_non_retryable:
                        _content_error_patterns = [
                            "exceeded limit",
                            "max bytes",
                            "payload too large",
                            "request entity too large",
                            "content too large",
                            "larger than allowed",
                            "(413)",
                            "context length",
                            "too many tokens",
                            "string too long",
                            "data_inspection",
                            "inappropriate content",
                        ]
                        if any(p in _err_lower for p in _content_error_patterns):
                            logger.error(
                                f"[LLM] endpoint={provider.name} content-level error "
                                f"(NOT cooling down endpoint): {error_str[:200]}"
                            )
                            provider._content_error = True
                        else:
                            logger.error(
                                f"[LLM] endpoint={provider.name} non-retryable structural error: "
                                f"{error_str[:200]}"
                            )
                            provider.mark_unhealthy(error_str, category="structural")
                        failed_providers.append(provider)
                    else:
                        provider.mark_unhealthy(error_str)
                        failed_providers.append(provider)
                        logger.warning(
                            f"[LLM] endpoint={provider.name} "
                            f"cooldown={provider.cooldown_remaining}s "
                            f"(category={provider.error_category})"
                        )

            except Exception as e:
                logger.error(
                    f"[LLM] endpoint={provider.name} unexpected_error={e}",
                    exc_info=True,
                )
                provider.mark_unhealthy(str(e))
                errors.append(f"{provider.name}: {e}")
                failed_providers.append(provider)
                logger.warning(
                    f"[LLM] endpoint={provider.name} "
                    f"cooldown={provider.cooldown_remaining}s "
                    f"(category={provider.error_category})"
                )

            finally:
                if _thinking_downgraded:
                    request.enable_thinking = True

            if i < len(providers) - 1:
                next_provider = providers[i + 1]
                logger.warning(
                    f"[LLM] endpoint={provider.name} action=failover target={next_provider.name}"
                    + (" (tool_context, retried same endpoint first)" if not allow_failover else "")
                )

        # -- Global failure detection --
        if len(failed_providers) >= 2:
            transient_count = sum(1 for fp in failed_providers if fp.error_category == "transient")
            if transient_count >= len(failed_providers) * 0.5:
                shortened = 0
                for fp in failed_providers:
                    if fp.error_category == "transient" and not fp.is_extended_cooldown:
                        fp.shorten_cooldown(COOLDOWN_GLOBAL_FAILURE)
                        shortened += 1
                if shortened:
                    logger.warning(
                        f"[LLM] Global failure detected: {len(failed_providers)} endpoints failed "
                        f"({transient_count} transient). Likely network issue on host. "
                        f"Shortened {shortened} endpoint cooldowns to {COOLDOWN_GLOBAL_FAILURE}s "
                        f"(skipped {transient_count - shortened} with progressive backoff)."
                    )

        if not allow_failover:
            logger.warning(
                "[LLM] Tool context detected. All endpoints exhausted (each retried before failover). "
                "Upper layer (Agent/TaskMonitor) may restart with a different strategy."
            )

        hint = _friendly_error_hint(failed_providers)
        has_content_error = any(getattr(fp, "_content_error", False) for fp in failed_providers)
        all_structural = has_content_error or all(
            fp.error_category == "structural" for fp in failed_providers
        )
        raise AllEndpointsFailedError(
            f"All endpoints failed: {'; '.join(errors)}\n{hint}",
            is_structural=all_structural,
        )

    def _try_self_heal(self, error: LLMError, request: LLMRequest, provider) -> bool:
        """Attempt to self-heal request parameters based on the error message.

        Mutates request attributes in place; returns True if fixed and should be retried.
        Each self-heal type triggers only once (using flags on the request to prevent loops).
        """
        error_str = str(error).lower()

        # -- Self-heal 1: missing reasoning_content --
        _reasoning_patterns = [
            "reasoning_content is missing",
            "missing reasoning_content",
            "missing `reasoning_content`",
            "missing 'reasoning_content'",
            "thinking is enabled but reasoning_content is missing",
        ]
        if any(p in error_str for p in _reasoning_patterns):
            if not getattr(request, "_reasoning_healed", False):
                request._reasoning_healed = True  # type: ignore[attr-defined]
                request.enable_thinking = True
                logger.info(
                    f"[LLM] endpoint={provider.name} reasoning_content error, "
                    f"self-healing: enable_thinking=True"
                )
                return True

        # -- Self-heal 2: endpoint rejects thinking / reasoning_effort parameter --
        _reject_patterns = [
            "extra_forbidden",
            "extra inputs are not permitted",
            "unsupported parameter",
        ]
        if any(p in error_str for p in _reject_patterns) and (
            "thinking" in error_str or "reasoning_effort" in error_str
        ):
            if not getattr(request, "_thinking_stripped", False):
                request._thinking_stripped = True  # type: ignore[attr-defined]
                request.enable_thinking = False
                request.thinking_depth = None
                provider._thinking_params_unsupported = True  # type: ignore[attr-defined]
                logger.info(
                    f"[LLM] endpoint={provider.name} rejected thinking params, "
                    f"self-healing: disabling thinking mode"
                )
                return True

        return False

    def _normalize_messages(self, messages: list[Message]) -> list[Message]:
        """Message normalization pipeline: unify format before sending.

        Converts internal messages to dicts for normalization, then converts them back to Message objects.
        """
        try:
            msg_dicts = [m.to_dict() for m in messages]
            normalized = normalize_messages_for_api(msg_dicts)
            return [self._dict_to_message(m) for m in normalized]
        except Exception as e:
            logger.debug("Message normalization skipped: %s", e)
            return messages

    @staticmethod
    def _dict_to_message(m: dict) -> Message:
        """Convert a normalized dict back to a Message with proper ContentBlock types."""
        content = m["content"]
        if isinstance(content, str):
            return Message(role=m["role"], content=content)

        rebuilt: list = []
        for block in content:
            if isinstance(block, ContentBlock):
                rebuilt.append(block)
                continue
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                rebuilt.append(TextBlock(text=block.get("text", "")))
            elif btype == "tool_use":
                rebuilt.append(
                    ToolUseBlock(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        input=block.get("input", {}),
                    )
                )
            elif btype == "tool_result":
                rebuilt.append(
                    ToolResultBlock(
                        tool_use_id=block.get("tool_use_id", ""),
                        content=block.get("content", ""),
                        is_error=block.get("is_error", False),
                    )
                )
            elif btype == "image":
                source = block.get("source", {})
                rebuilt.append(
                    ImageBlock(
                        image=ImageContent(
                            media_type=source.get("media_type", "image/png"),
                            data=source.get("data", ""),
                        )
                    )
                )
            elif btype == "thinking":
                rebuilt.append(ThinkingBlock(thinking=block.get("thinking", "")))
            else:
                rebuilt.append(TextBlock(text=str(block)))

        return Message(role=m["role"], content=rebuilt if rebuilt else content)

    def _get_retry_delay(self, attempt: int, error: Exception | None = None) -> float:
        """Calculate retry delay (seconds). Uses exponential backoff + jitter."""
        retry_after = None
        if error:
            retry_after = getattr(error, "retry_after_seconds", None)
        delay_ms = calculate_retry_delay(attempt, retry_after)
        return delay_ms / 1000

    def _has_images(self, messages: list[Message]) -> bool:
        """Check whether any message contains images"""
        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ImageBlock):
                        return True
        return False

    def _has_videos(self, messages: list[Message]) -> bool:
        """Check whether any message contains video"""
        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, VideoBlock):
                        return True
        return False

    def _has_audio(self, messages: list[Message]) -> bool:
        """Check whether any message contains audio"""
        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, AudioBlock):
                        return True
        return False

    def _has_documents(self, messages: list[Message]) -> bool:
        """Check whether any message contains documents (PDF, etc.)"""
        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, DocumentBlock):
                        return True
        return False

    def has_any_endpoint_with_capability(self, capability: str) -> bool:
        """Check whether any endpoint supports the given capability (for Agent queries)"""
        return any(p.config.has_capability(capability) for p in self._providers.values())

    def _has_tool_context(self, messages: list[Message]) -> bool:
        """Check whether any message contains tool-call context (tool_use or tool_result)

        Used to decide whether failover is allowed:
        - No tool context: can safely fail over to another endpoint
        - Has tool context: disallow failover, since tool-call formats may be incompatible across models

        Returns:
            True if tool context is present, meaning failover should be disabled
        """
        from .types import ToolResultBlock, ToolUseBlock

        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, (ToolUseBlock, ToolResultBlock)):
                        return True
                    # Support dict format (some post-conversion messages may be dicts)
                    if isinstance(block, dict):
                        block_type = block.get("type", "")
                        if block_type in ("tool_use", "tool_result"):
                            return True
        return False

    def reset_endpoint_cooldown(self, endpoint_name: str) -> bool:
        """Reset cooldown for the given endpoint

        Used before model switching to ensure the target endpoint is available. Does not reset the consecutive failure count
        (reset_cooldown preserves _consecutive_cooldowns; if the endpoint still has problems, backoff continues to grow on the next failure).

        Returns:
            True if reset succeeded, False if the endpoint does not exist
        """
        provider = self._providers.get(endpoint_name)
        if not provider:
            return False
        if not provider.is_healthy:
            logger.info(
                f"[LLM] endpoint={endpoint_name} cooldown force-reset for model switch "
                f"(was category={provider.error_category}, "
                f"remaining={provider.cooldown_remaining}s)"
            )
            provider.reset_cooldown()
        return True

    def reset_all_cooldowns(self, *, include_structural: bool = False, force_all: bool = False):
        """Reset endpoint cooldowns

        Args:
            include_structural: Also reset cooldowns for structural errors.
            force_all: Unconditionally reset all endpoint cooldowns (used when the user actively retries).
        """
        reset_count = 0
        for name, provider in self._providers.items():
            if not provider.is_healthy:
                cat = provider.error_category
                if force_all or cat == "transient" or (include_structural and cat == "structural"):
                    provider.reset_cooldown()
                    reset_count += 1
                    logger.info(
                        f"[LLM] endpoint={name} cooldown reset (category={cat}, force_all={force_all})"
                    )
        if reset_count:
            logger.info(f"[LLM] Reset cooldowns for {reset_count} endpoints")
        return reset_count

    async def health_check(self) -> dict[str, bool]:
        """
        Check the health status of all endpoints

        Returns:
            {endpoint_name: is_healthy}
        """
        results = {}

        tasks = [(name, provider.health_check()) for name, provider in self._providers.items()]

        for name, task in tasks:
            try:
                results[name] = await task
            except Exception as e:
                logger.error(f"Health check failed for {name}: {e}")
                results[name] = False

        return results

    def get_provider(self, name: str) -> LLMProvider | None:
        """Get the Provider with the given name"""
        return self._providers.get(name)

    def add_endpoint(self, config: EndpointConfig):
        """Dynamically add an endpoint"""
        provider = self._create_provider(config)
        if provider:
            self._endpoints.append(config)
            self._endpoints.sort(key=lambda x: x.priority)
            self._providers[config.name] = provider

    def remove_endpoint(self, name: str):
        """Dynamically remove an endpoint"""
        if name in self._providers:
            del self._providers[name]
        self._endpoints = [ep for ep in self._endpoints if ep.name != name]

    # ==================== Dynamic model switching ====================

    def switch_model(
        self,
        endpoint_name: str,
        hours: float = DEFAULT_OVERRIDE_HOURS,
        reason: str = "",
        conversation_id: str | None = None,
    ) -> tuple[bool, str]:
        """
        Temporarily switch to the specified model

        Args:
            endpoint_name: Endpoint name
            hours: Validity time (hours), defaults to 12
            reason: Switch reason

        Returns:
            (success, message)
        """
        # Check whether the endpoint exists
        if endpoint_name not in self._providers:
            available = list(self._providers.keys())
            return False, f"Endpoint '{endpoint_name}' does not exist. Available endpoints: {', '.join(available)}"

        # switch_model is an explicit intent declaration (user-selected model / system failover),
        # and must not be blocked by cooldown. If the endpoint actually has issues, _try_endpoints
        # at request time will mark_unhealthy and trigger failover — that's the correct health-aware layer.
        provider = self._providers[endpoint_name]
        if not provider.is_healthy:
            logger.info(
                f"[LLM] endpoint={endpoint_name} cooldown reset for switch_model "
                f"(was category={provider.error_category}, "
                f"remaining={provider.cooldown_remaining}s, reason={reason!r})"
            )
            provider.reset_cooldown()

        # Create the override configuration
        expires_at = datetime.now() + timedelta(hours=hours)
        override = EndpointOverride(
            endpoint_name=endpoint_name,
            expires_at=expires_at,
            reason=reason,
        )
        if conversation_id:
            self._conversation_overrides[conversation_id] = override
        else:
            self._endpoint_override = override

        model = provider.config.model
        expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"[LLM] Model switched to {endpoint_name} ({model}), expires at {expires_str}")

        return True, f"Switched to model: {model}\nValid until: {expires_str}"

    def restore_default(self, conversation_id: str | None = None) -> tuple[bool, str]:
        """
        Restore default model (clear temporary override)

        Returns:
            (success, message)
        """
        if conversation_id:
            if conversation_id not in self._conversation_overrides:
                return False, "No temporary switch for the current session; already using the default model"
            self._conversation_overrides.pop(conversation_id, None)
        else:
            if not self._endpoint_override:
                return False, "No temporary switch currently active; already using the default model"
            self._endpoint_override = None

        # Get the current default model
        default = self.get_current_model()
        default_model = default.model if default else "unknown"

        logger.info(f"[LLM] Restored to default model: {default_model}")
        return True, f"Restored to default model: {default_model}"

    def get_current_model(self, conversation_id: str | None = None) -> ModelInfo | None:
        """
        Get information about the currently used model

        Args:
            conversation_id: Conversation ID (checks the per-conversation override when provided)

        Returns:
            Current model info, or None if no model is available
        """
        # Check for and clean up expired overrides
        if self._endpoint_override and self._endpoint_override.is_expired:
            logger.info("[LLM] Override expired, restoring default")
            self._endpoint_override = None

        # Determine the effective override (conversation > global)
        effective_override = None
        if conversation_id and conversation_id in self._conversation_overrides:
            ov = self._conversation_overrides[conversation_id]
            if ov and not ov.is_expired:
                effective_override = ov
            else:
                self._conversation_overrides.pop(conversation_id, None)
        if not effective_override and self._endpoint_override:
            effective_override = self._endpoint_override

        # If an override is effective, return the overridden endpoint
        if effective_override:
            name = effective_override.endpoint_name
            if name in self._providers:
                provider = self._providers[name]
                config = provider.config
                return ModelInfo(
                    name=name,
                    model=config.model,
                    provider=config.provider,
                    priority=config.priority,
                    is_healthy=provider.is_healthy,
                    is_current=True,
                    is_override=True,
                    capabilities=config.capabilities,
                    note=config.note,
                )

        # Otherwise, return the highest-priority healthy endpoint
        for provider in sorted(self._providers.values(), key=lambda p: p.config.priority):
            if provider.is_healthy:
                config = provider.config
                return ModelInfo(
                    name=config.name,
                    model=config.model,
                    provider=config.provider,
                    priority=config.priority,
                    is_healthy=True,
                    is_current=True,
                    is_override=False,
                    capabilities=config.capabilities,
                    note=config.note,
                )

        return None

    def get_next_endpoint(self, conversation_id: str | None = None) -> str | None:
        """
        Get the next-priority healthy endpoint name (for fallback)

        Logic: find the current effective endpoint, sort by priority, and return the first healthy endpoint after it.
        If the current endpoint is already the lowest priority or there are no available endpoints, return None.

        Args:
            conversation_id: Optional conversation ID (used to identify per-conversation override)

        Returns:
            Next endpoint name, or None
        """
        current = self.get_current_model()
        if not current:
            return None

        sorted_providers = sorted(
            (p for p in self._providers.values() if p.is_healthy),
            key=lambda p: p.config.priority,
        )

        found_current = False
        for p in sorted_providers:
            if p.config.name == current.name:
                found_current = True
                continue
            if found_current:
                return p.config.name

        return None

    def list_available_models(self) -> list[ModelInfo]:
        """
        List all available models

        Returns:
            List of model info (priority sorted)
        """
        # Check for and clean up expired overrides
        if self._endpoint_override and self._endpoint_override.is_expired:
            self._endpoint_override = None

        current_name = None
        if self._endpoint_override:
            current_name = self._endpoint_override.endpoint_name

        models = []
        for provider in sorted(self._providers.values(), key=lambda p: p.config.priority):
            config = provider.config
            is_current = False
            is_override = False

            if current_name:
                is_current = config.name == current_name
                is_override = is_current
            elif provider.is_healthy and not models:
                # The first healthy endpoint is the current default
                is_current = True

            models.append(
                ModelInfo(
                    name=config.name,
                    model=config.model,
                    provider=config.provider,
                    priority=config.priority,
                    is_healthy=provider.is_healthy,
                    is_current=is_current,
                    is_override=is_override,
                    capabilities=config.capabilities,
                    note=config.note,
                )
            )

        return models

    def get_override_status(self) -> dict | None:
        """
        Get the current override status

        Returns:
            Override status info, or None if no override is active
        """
        if not self._endpoint_override:
            return None

        if self._endpoint_override.is_expired:
            self._endpoint_override = None
            return None

        return {
            "endpoint_name": self._endpoint_override.endpoint_name,
            "remaining_hours": round(self._endpoint_override.remaining_hours, 2),
            "expires_at": self._endpoint_override.expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": self._endpoint_override.reason,
        }

    def update_priority(self, priority_order: list[str]) -> tuple[bool, str]:
        """
        Update endpoint priority order

        Args:
            priority_order: List of endpoint names sorted from highest to lowest priority

        Returns:
            (success, message)
        """
        # Verify all endpoints exist
        unknown = [name for name in priority_order if name not in self._providers]
        if unknown:
            return False, f"Unknown endpoints: {', '.join(unknown)}"

        # Update priorities
        for i, name in enumerate(priority_order):
            for ep in self._endpoints:
                if ep.name == name:
                    ep.priority = i
                    break

        # Re-sort
        self._endpoints.sort(key=lambda x: x.priority)

        # Save to config file
        if self._config_path and self._config_path.exists():
            try:
                self._save_config()
                logger.info(f"[LLM] Priority updated and saved: {priority_order}")
                return True, f"Priority updated and saved: {' > '.join(priority_order)}"
            except Exception as e:
                logger.error(f"[LLM] Failed to save config: {e}")
                return True, f"Priority updated (in memory), but failed to save config file: {e}"

        return True, f"Priority updated: {' > '.join(priority_order)}"

    def _save_config(self):
        """Save configuration to file"""
        if not self._config_path:
            return

        from ..utils.atomic_io import read_json_safe, safe_json_write

        config_data = read_json_safe(self._config_path)
        if config_data is None:
            logger.warning("Cannot save config: no existing config to update")
            return

        name_to_priority = {ep.name: ep.priority for ep in self._endpoints}
        for ep_data in config_data.get("endpoints", []):
            name = ep_data.get("name")
            if name in name_to_priority:
                ep_data["priority"] = name_to_priority[name]

        safe_json_write(self._config_path, config_data)

    async def close(self):
        """Close all Providers"""
        for provider in self._providers.values():
            if hasattr(provider, "close"):
                await provider.close()


# Global singleton
_default_client: LLMClient | None = None


def get_default_client() -> LLMClient:
    """Get the default client instance"""
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


def set_default_client(client: LLMClient):
    """Set the default client instance"""
    global _default_client
    _default_client = client


async def chat(
    messages: list[Message],
    system: str = "",
    tools: list[Tool] | None = None,
    **kwargs,
) -> LLMResponse:
    """Convenience function: chat using the default client"""
    client = get_default_client()
    return await client.chat(messages, system=system, tools=tools, **kwargs)

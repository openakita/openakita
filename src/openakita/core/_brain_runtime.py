"""
Brain 模块 - LLM 交互层

Brain 是 LLMClient 的薄包装，提供向后兼容的接口。
所有实际的 LLM 调用、能力分流、故障切换都由 LLMClient 处理。
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

from ..config import settings
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
    TextBlock,
    ThinkingBlock,
    Tool,
    ToolResultBlock,
    ToolUseBlock,
    VideoBlock,
    VideoContent,
)
from ..runtime.llm import (
    CompilerCircuitBreaker,
    EndpointFailoverView,
    response_to_anthropic_message,
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


def _sanitize_compiler_error(error: str, max_chars: int = 300) -> str:
    """Return a single-line, secret-redacted compiler failure summary for the UI."""
    from ..utils.redaction import redact_text

    sanitized = redact_text(str(error or ""))
    return " ".join(sanitized.split())[:max_chars]


def _classify_compiler_access_error(error: str) -> str:
    text = str(error or "").lower()
    if any(
        marker in text for marker in ("401", "403", "unauthorized", "authentication", "api key")
    ):
        return "authentication_failed"
    if any(marker in text for marker in ("429", "rate limit", "rate_limit", "quota")):
        return "rate_limited"
    if any(marker in text for marker in ("timeout", "timed out", "connection", "network", "dns")):
        return "network_unreachable"
    if any(
        marker in text for marker in ("model_not_found", "model not found", "unknown model", "404")
    ):
        return "model_unavailable"
    return "all_endpoints_failed"


def _compiler_configuration_fallback_reason() -> tuple[str, str]:
    """Explain why no compiler client could be constructed from saved configuration."""
    config_path = get_default_config_path()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        configured = data.get("compiler_endpoints", [])
        if not isinstance(configured, list) or not configured:
            return "not_configured", "没有配置提示词编译模型"
        enabled = [row for row in configured if isinstance(row, dict) and row.get("enabled", True)]
        if not enabled:
            names = ", ".join(
                _sanitize_compiler_error(str(row.get("name") or row.get("model") or "未命名"), 60)
                for row in configured
                if isinstance(row, dict)
            )
            detail = "已配置的提示词编译模型全部被禁用"
            return "all_disabled", f"{detail}：{names}" if names else detail
        names = ", ".join(
            _sanitize_compiler_error(str(row.get("name") or row.get("model") or "未命名"), 60)
            for row in enabled
        )
        detail = "已启用的提示词编译模型配置无效，未能创建访问客户端"
        return "invalid_configuration", f"{detail}：{names}" if names else detail
    except Exception as exc:
        return "invalid_configuration", _sanitize_compiler_error(str(exc))


@dataclass
class Response:
    """LLM 响应（向后兼容）"""

    content: str
    tool_calls: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)
    compiler_source: str = ""
    compiler_fallback_reason: str = ""
    compiler_fallback_detail: str = ""


@dataclass
class Context:
    """对话上下文"""

    messages: list[MessageParam] = field(default_factory=list)
    system: str = ""
    tools: list[ToolParam] = field(default_factory=list)


class Brain:
    """
    Agent 大脑 - LLM 交互层

    Brain 是 LLMClient 的薄包装：
    - 配置从 llm_endpoints.json 加载
    - 能力分流、故障切换由 LLMClient 处理
    - 提供向后兼容的 Anthropic Message 格式接口
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ):
        from ..llm.client import (
            LLMClient,  # local import: break core/agent/llm cycle (P-RC-11 P11.2)
        )

        # Compiler circuit breaker (P-RC-4 extraction).
        self._compiler_breaker = CompilerCircuitBreaker()
        self._compiler_last_error = ""

        # max_tokens=0 表示"使用合理默认值"：
        # - 对 OpenAI 兼容 API：使用端点配置值或兜底 16384（部分 API 如 NVIDIA NIM 默认极低）
        # - 对 Anthropic API：使用端点配置值或兜底 16384（该 API 强制要求此参数）
        self.max_tokens = max_tokens if max_tokens is not None else settings.max_tokens

        # 创建 LLMClient（统一入口）
        config_path = get_default_config_path()
        if config_path.exists():
            self._llm_client = LLMClient(config_path=config_path)
            logger.info(f"Brain using LLMClient with config from {config_path}")
        else:
            # 如果没有配置文件，创建空客户端
            self._llm_client = LLMClient()
            logger.warning("No llm_endpoints.json found, LLMClient may not work")

        # Failover/endpoint view (P-RC-4 extraction). Borrows the
        # client; the nine Brain wrappers below delegate here so the
        # agent rewrite can compose the same surface freshly.
        self._failover_view = EndpointFailoverView(self._llm_client)

        # Prompt Compiler 专用 LLMClient（独立于主模型，使用快速小模型）
        self._compiler_client: LLMClient | None = None
        self._init_compiler_client()

        # 公开属性（从 LMClient 获取）
        self._update_public_attrs()

        # Thinking 模式状态
        self._thinking_enabled = True

        # Trace context for debug dump files (org_id, node_id, session_id, etc.)
        self._trace_context: dict[str, str] = {}

        # Per-session LLM call accumulator (reset via reset_usage_accumulator)
        self._acc_calls: int = 0
        self._acc_tokens_in: int = 0
        self._acc_tokens_out: int = 0

        # 启动信息
        endpoints = self._llm_client.endpoints
        logger.info(f"Brain initialized with {len(endpoints)} endpoints via LLMClient")
        for ep in endpoints:
            logger.info(f"  - {ep.name}: {ep.model} (capabilities: {ep.capabilities})")

        # 显示当前端点
        if endpoints:
            # 获取健康的端点
            healthy_eps = [p.name for p in self._llm_client.providers.values() if p.is_healthy]
            if healthy_eps:
                logger.info("  ╔══════════════════════════════════════════╗")
                logger.info(f"  ║  可用端点: {', '.join(healthy_eps):<30}║")
                logger.info("  ╚══════════════════════════════════════════╝")

    def _update_public_attrs(self) -> None:
        """更新公开属性（向后兼容）"""
        endpoints = self._llm_client.endpoints
        if endpoints:
            ep = endpoints[0]  # 使用第一个端点的信息
            self.model = ep.model
            self.base_url = ep.base_url
            # API key 不再暴露
        else:
            self.model = settings.default_model
            self.base_url = ""

    def set_trace_context(self, ctx: dict[str, str]) -> None:
        """Set trace context (org_id, node_id, session_id, etc.) for LLM debug dumps."""
        self._trace_context = dict(ctx)

    def _init_compiler_client(self) -> None:
        """从配置加载 Prompt Compiler 专属 LLMClient"""
        from ..llm.client import (
            LLMClient,  # local import: break core/agent/llm cycle (P-RC-11 P11.2)
        )

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
        """True when the compiler client exists and the breaker is closed."""
        if not self._compiler_client:
            return False
        return self._compiler_breaker.is_available()

    def _compiler_on_success(self) -> None:
        """Record a compiler success on the breaker."""
        self._compiler_last_error = ""
        self._compiler_breaker.on_success()

    def _compiler_on_failure(self, error_str: str = "") -> None:
        """Record a compiler failure on the breaker."""
        self._compiler_last_error = _sanitize_compiler_error(error_str)
        self._compiler_breaker.on_failure(error_str)

    def reload_compiler_client(self) -> bool:
        """热重载编译端点配置。

        Returns:
            True 表示成功重载，False 表示无变化或失败。
        """
        from ..llm.client import (
            LLMClient,  # local import: break core/agent/llm cycle (P-RC-11 P11.2)
        )

        try:
            _, compiler_eps, _, _ = load_endpoints_config()
            if compiler_eps:
                self._compiler_client = LLMClient(endpoints=compiler_eps)
                names = [ep.name for ep in compiler_eps]
                logger.info(f"Compiler LLMClient reloaded with endpoints: {names}")
            else:
                self._compiler_client = None
                logger.info("Compiler endpoints cleared (none configured)")
            # Reset breaker on config reload so a fixed API key recovers.
            self._compiler_breaker.force_reset()
            self._compiler_last_error = ""
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
        Prompt Compiler 专用 LLM 调用。

        调用策略：
        1. 优先用 compiler_client（快速模型，强制禁用思考模式）
        2. compiler_client 全部端点失败时，回退到主模型（同样禁用思考）

        Args:
            prompt: 用户消息
            system: 系统提示词
            max_tokens: 最大输出 token（默认 512，调用方可按需调大）

        Returns:
            Response 对象
        """
        messages = [Message(role="user", content=[TextBlock(text=prompt)])]

        _source = "compiler"
        _fallback_reason = ""
        _fallback_detail = ""
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
                result.compiler_source = _source
                self._dump_llm_request(system, messages, [], caller="compiler_think")
                self._dump_llm_response(
                    response,
                    caller="compiler_think",
                    request_id=f"compiler_{_source}",
                )
                return result
            except Exception as e:
                self._compiler_on_failure(str(e))
                _fallback_reason = _classify_compiler_access_error(str(e))
                _fallback_detail = self._compiler_last_error
                logger.warning(f"Compiler LLM failed, falling back to main model: {e}")
        elif self._compiler_client is None:
            _fallback_reason, _fallback_detail = _compiler_configuration_fallback_reason()
        else:
            _fallback_reason = (
                "authentication_failed" if self._compiler_breaker.auth_failed else "circuit_open"
            )
            _fallback_detail = self._compiler_last_error

        # 回退到主模型
        # 主模型可能是 reasoning 模型（如 mimo-v2-pro），即使 enable_thinking=False
        # 也会在 reasoning 字段产出思考内容，占用 max_tokens 预算。
        # 需要增大 max_tokens 确保 reasoning 之后仍有余量产出 content。
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
        result = self._llm_response_to_response(response)
        result.compiler_source = _source
        result.compiler_fallback_reason = _fallback_reason or "all_endpoints_failed"
        result.compiler_fallback_detail = _fallback_detail
        return result

    async def think_lightweight(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 2048,
    ) -> Response:
        """
        轻量级思考：优先使用 compiler 端点。

        适用于记忆提取、分类判断等不需要工具/上下文的简单 LLM 调用。
        与主推理链完全隔离（不共享消息历史），使用独立的 LLM 端点。

        调用策略:
        1. 优先用 _compiler_client（快速小模型）
        2. compiler_client 不可用或失败时，回退到 _llm_client

        Args:
            prompt: 用户消息
            system: 系统提示词
            max_tokens: 最大输出 token

        Returns:
            Response 对象
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

        # 保存响应
        self._dump_llm_response(
            response, caller=f"think_lightweight_{client_name}", request_id=req_id
        )

        self._record_usage(response)
        return self._llm_response_to_response(response)

    def _llm_response_to_response(self, llm_response: LLMResponse) -> Response:
        """将 LLMResponse 转换为向后兼容的 Response"""
        text_parts = []
        tool_calls = []
        for block in llm_response.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                d: dict = {
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
                if block.provider_extra:
                    d["provider_extra"] = block.provider_extra
                tool_calls.append(d)
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
        """设置 thinking 模式"""
        self._thinking_enabled = enabled
        logger.info(f"Thinking mode {'enabled' if enabled else 'disabled'}")

    def is_thinking_enabled(self) -> bool:
        """检查 thinking 模式是否启用

        先检查全局配置 (always/never)，再检查模型能力是否支持 thinking，
        最后使用运行时开关。不支持 thinking 的模型始终返回 False。
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
        """获取当前端点信息"""
        providers = self._llm_client.providers
        for name, provider in providers.items():
            if provider.is_healthy:
                return {
                    "name": name,
                    "model": provider.model,
                    "healthy": True,
                }
        # 没有健康的端点
        endpoints = self._llm_client.endpoints
        if endpoints:
            return {
                "name": endpoints[0].name,
                "model": endpoints[0].model,
                "healthy": False,
            }
        return {"name": "none", "model": "none", "healthy": False}

        # ========================================================================
        # 核心方法：messages_create
        # ========================================================================
        """Return current-endpoint info dict; delegates to EndpointFailoverView."""
        return self._failover_view.current_endpoint_info()

    def messages_create(
        self, use_thinking: bool = None, thinking_depth: str | None = None, **kwargs
    ) -> AnthropicMessage:
        """
        调用 LLM API（通过 LLMClient）

        这是主要的 LLM 调用入口，自动处理：
        - 能力分流（图片/视频自动选择支持的端点）
        - 故障切换
        - 格式转换

        Args:
            use_thinking: 是否使用 thinking 模式
            thinking_depth: 思考深度 ('low'/'medium'/'high'/'max'/None)
            **kwargs: Anthropic 格式参数 (messages, system, tools, max_tokens)

        Returns:
            Anthropic Message 格式响应
        """
        if use_thinking is None:
            use_thinking = self.is_thinking_enabled()

        # 转换消息格式: Anthropic -> LLMClient
        llm_messages = self._convert_messages_to_llm(kwargs.get("messages", []))
        system = kwargs.get("system", "")
        llm_tools = self._convert_tools_to_llm(kwargs.get("tools", []))
        max_tokens = kwargs.get("max_tokens", self.max_tokens)

        # 调试输出：保存完整请求到文件
        req_id = self._dump_llm_request(system, llm_messages, llm_tools, caller="messages_create")

        conversation_id = kwargs.get("conversation_id")

        # 调用 LLMClient
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
            # 没有事件循环，创建新的
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

        # 保存响应到调试文件
        self._dump_llm_response(response, caller="messages_create", request_id=req_id)

        # 记录 token 用量
        self._record_usage(response)

        # 转换响应: LLMClient -> Anthropic Message
        return self._convert_response_to_anthropic(response)

    async def messages_create_async(
        self,
        use_thinking: bool = None,
        thinking_depth: str | None = None,
        cancel_event: asyncio.Event | None = None,
        **kwargs,
    ) -> AnthropicMessage:
        """异步版本的 messages_create，直接 await LLMClient.chat()。

        用于已处在事件循环中的场景（如取消收尾），避免 asyncio.to_thread + asyncio.run
        创建新事件循环导致 httpx 连接池竞争。
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
            _out_tokens = response.usage.output_tokens if hasattr(response, "usage") else 0
            _reasoning = bool(getattr(response, "reasoning_content", None))
            _ = _choices  # reserved for upcoming choice-level diagnostics
            logger.info(
                f"[Brain] messages_create_async success: "
                f"content_blocks={len(_content)}, tokens_out={_out_tokens}, "
                f"has_reasoning={_reasoning}, endpoint={getattr(response, 'endpoint_name', '?')}"
            )
            if not _content and _out_tokens > 0:
                logger.warning(
                    f"[Brain] ⚠️ EMPTY CONTENT with {_out_tokens} output tokens! "
                    f"enable_thinking={use_thinking}, model={getattr(response, 'model', '?')}, "
                    f"reasoning_content_len={len(response.reasoning_content) if response.reasoning_content else 0}, "
                    f"stop_reason={getattr(response, 'stop_reason', '?')}"
                )
        except Exception as e:
            logger.error(f"[Brain] messages_create_async FAILED: {type(e).__name__}: {e}")
            raise

        self._dump_llm_response(response, caller="messages_create_async", request_id=req_id)

        # 记录 token 用量
        self._record_usage(response)

        return self._convert_response_to_anthropic(response)

    async def messages_create_stream(
        self,
        use_thinking: bool = None,
        thinking_depth: str | None = None,
        **kwargs,
    ):
        """流式版本的 messages_create，yield Provider 原始流事件 (dict)。

        参数准备与 messages_create_async 一致，但调用 LLMClient.chat_stream()
        逐事件 yield，供 StreamAccumulator 消费。Token 用量由调用方在流结束后
        通过 StreamAccumulator 获取的 usage 信息自行记录。
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
    # Token 用量记录
    # ========================================================================

    def _record_usage(self, response: LLMResponse) -> None:
        """从 LLMResponse 提取 token 用量并投递到追踪队列。"""
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
    # 格式转换方法
    # ========================================================================

    def _convert_messages_to_llm(self, messages: list[MessageParam]) -> list[Message]:
        """将 Anthropic MessageParam 转换为 LLMClient Message

        支持 MiniMax M2.1 的 Interleaved Thinking：
        - 解析并保留 thinking 块
        - 确保多轮工具调用时思维链的连续性

        支持 Kimi reasoning_content：
        - 从消息字典中提取 reasoning_content
        - 传递给 Message 对象以支持模型切换
        """
        result = []

        for msg in messages:
            role = msg.get("role", "user") if isinstance(msg, dict) else msg["role"]
            content = msg.get("content", "") if isinstance(msg, dict) else msg["content"]
            # 提取 reasoning_content（用于 Kimi 等支持思考的模型）
            reasoning_content = msg.get("reasoning_content") if isinstance(msg, dict) else None

            if isinstance(content, str):
                result.append(
                    Message(role=role, content=content, reasoning_content=reasoning_content)
                )
            elif isinstance(content, list):
                # 复杂内容（多模态、工具调用等）
                blocks = []
                for part in content:
                    if isinstance(part, dict):
                        part_type = part.get("type", "")

                        if part_type == "text":
                            blocks.append(TextBlock(text=part.get("text", "")))

                        elif part_type == "thinking":
                            # MiniMax M2.1 Interleaved Thinking 支持
                            # 必须完整保留 thinking 块以保持思维链连续性
                            blocks.append(ThinkingBlock(thinking=part.get("thinking", "")))

                        elif part_type == "tool_use":
                            blocks.append(
                                ToolUseBlock(
                                    id=part.get("id", ""),
                                    name=part.get("name", ""),
                                    input=part.get("input", {}),
                                    provider_extra=part.get("provider_extra"),
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
                                    # 保留多模态内容（文本+图片），让 LLM 能看到
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

                        # ── OpenAI 格式兼容（Desktop Chat 附件等场景） ──
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
                                    # 远程 URL — 尝试通过 ImageContent.from_url 解析
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
        """Convert the progressively disclosed Agent tool set to provider schemas.

        The Agent is the single owner of schema selection. Tools marked
        ``_deferred=True`` remain discoverable through the textual catalog and
        are omitted here; every other valid tool is forwarded unchanged.
        """
        if not tools:
            return None

        result: list[Tool] = []
        skipped = 0
        deferred = 0
        duplicate_names: list[str] = []
        seen_names: set[str] = set()

        for tool in tools:
            name = tool.get("name", "")
            description = tool.get("detail") or tool.get("description", "")
            schema = tool.get("input_schema", {})

            if not name:
                func = tool.get("function")
                if isinstance(func, dict):
                    name = func.get("name", "")
                    description = description or func.get("description", "")
                    schema = schema or func.get("parameters", {})

            if not name:
                skipped += 1
                continue
            if name in seen_names:
                duplicate_names.append(name)
                continue
            seen_names.add(name)

            if tool.get("_deferred", False):
                deferred += 1
                continue

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
        if duplicate_names:
            logger.warning(
                "[Brain] _convert_tools_to_llm: removed %d duplicate tool definition(s): %s",
                len(duplicate_names),
                sorted(set(duplicate_names)),
            )
        if deferred:
            logger.info(
                "[Brain] progressive schemas: deferred=%d direct=%d total=%d",
                deferred,
                len(result),
                len(tools),
            )

        return result if result else None

    def _convert_response_to_anthropic(self, response: LLMResponse) -> AnthropicMessage:
        """Project an LLMResponse to an AnthropicMessage; delegates to runtime/llm/multimodal."""
        return response_to_anthropic_message(response)

    async def think(
        self,
        prompt: str,
        context: Context | None = None,
        system: str | None = None,
        tools: list[ToolParam] | None = None,
        max_tokens: int | None = None,
        thinking_depth: str | None = None,
        enable_thinking: bool | None = None,
    ) -> Response:
        """
        发送思考请求到 LLM（通过 LLMClient）

        Args:
            prompt: 用户输入
            context: 对话上下文
            system: 系统提示词
            tools: 可用工具列表
            max_tokens: 最大输出 token（不传则使用 self.max_tokens）
            thinking_depth: 思考深度 ('low'/'medium'/'high'/'max'/None)
            enable_thinking: 是否启用思考；None=沿用全局开关；True/False=本次显式覆盖
                （辅助任务如记忆抽取/总结请显式传 False，避免 thinking 浪费）

        Returns:
            Response 对象
        """
        # 构建消息列表
        messages: list[MessageParam] = []
        if context and context.messages:
            messages.extend(context.messages)
        messages.append({"role": "user", "content": prompt})

        # 确定系统提示词和工具
        sys_prompt = system or (context.system if context else "")
        tool_list = tools or (context.tools if context else [])

        # 转换为 LLMClient 格式
        llm_messages = self._convert_messages_to_llm(messages)
        llm_tools = self._convert_tools_to_llm(tool_list) if tool_list else None

        # 日志
        logger.info(
            f"[LLM REQUEST] messages={len(llm_messages)}, tools={len(tool_list) if tool_list else 0}"
        )

        # 调试输出：保存完整请求到文件
        req_id = self._dump_llm_request(
            sys_prompt, llm_messages, llm_tools, caller="_chat_with_llm_client"
        )

        # 思考开关：调用方可显式覆盖；否则沿用全局
        _thinking_flag = (
            self.is_thinking_enabled() if enable_thinking is None else bool(enable_thinking)
        )

        # 调用 LLMClient
        response = await self._llm_client.chat(
            messages=llm_messages,
            system=sys_prompt,
            tools=llm_tools,
            max_tokens=max_tokens or self.max_tokens,
            enable_thinking=_thinking_flag,
            thinking_depth=thinking_depth,
        )

        # 保存响应到调试文件
        self._dump_llm_response(response, caller="_chat_with_llm_client", request_id=req_id)

        self._record_usage(response)

        # 转换响应
        content = response.text
        tool_calls = [
            {
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            }
            for tc in response.tool_calls
        ]

        # 日志
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
    # 辅助方法
    # ========================================================================

    def _dump_llm_request(
        self, system: str, messages: list, tools: list, caller: str = "unknown"
    ) -> str:
        """
        保存 LLM 请求到调试文件

        用于诊断上下文问题，将完整的 system prompt 和 messages 保存到文件

        Args:
            system: 系统提示词
            messages: 消息列表（可能是 Message 对象或字典）
            tools: 工具列表
            caller: 调用方标识

        Returns:
            request_id: 请求 ID，用于关联对应的 response 文件
        """
        try:
            if not getattr(settings, "llm_debug_enabled", True):
                return uuid.uuid4().hex[:8]
            debug_dir = settings.project_root / "data" / "llm_debug"
            debug_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            request_id = uuid.uuid4().hex[:8]
            debug_file = debug_dir / f"llm_request_{timestamp}_{request_id}.json"

            # ── 1. 序列化 messages ──
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

            # ── 2. 序列化完整工具定义（和发给 LLM API 的 tools 参数一模一样）──
            full_tools = []
            for t in tools or []:
                if hasattr(t, "name"):
                    # Tool / NamedTuple / dataclass 对象
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

            # ── 3. Token 估算（中英混合感知：中文 ~1.5字符/token，英文/JSON ~4字符/token）──
            from ._context_runtime import ContextManager as _CM

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

            # ── 4. 构建完整 debug 数据（和发给 LLM 的请求结构一致）──
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

            # 记录日志并在 token 数量过大时发出警告
            token_detail = f"system={estimated_system_tokens}, messages={estimated_messages_tokens}, tools={estimated_tools_tokens}"
            if total_estimated_tokens > 50000:
                logger.debug(
                    f"[LLM DEBUG] ⚠️ Very large context! Estimated {total_estimated_tokens} tokens ({token_detail})"
                )
            elif total_estimated_tokens > 30000:
                logger.debug(
                    f"[LLM DEBUG] Large context: {total_estimated_tokens} tokens ({token_detail})"
                )
            else:
                logger.debug(
                    f"[LLM DEBUG] Request saved: {total_estimated_tokens} tokens ({token_detail})"
                )

            self._cleanup_old_debug_files(
                debug_dir,
                max_age_days=getattr(settings, "llm_debug_retention_days", 3),
                max_size_mb=getattr(settings, "llm_debug_max_size_mb", 512),
            )

            return request_id

        except Exception as e:
            logger.warning(f"[LLM DEBUG] Failed to save debug file: {e}")
            return uuid.uuid4().hex[:8]  # 即使保存失败也返回一个 ID 供 response 关联

    def _dump_llm_response(self, response, caller: str = "unknown", request_id: str = "") -> None:
        """
        保存 LLM 响应到调试文件（与 _dump_llm_request 对称）

        Args:
            response: LLMResponse 对象
            caller: 调用方标识
            request_id: 对应的请求 ID（用于关联 request 文件）
        """
        try:
            if not getattr(settings, "llm_debug_enabled", True):
                return
            debug_dir = settings.project_root / "data" / "llm_debug"
            debug_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            debug_file = debug_dir / f"llm_response_{timestamp}_{request_id}.json"

            # 序列化 content blocks
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
            # 原始响应诊断（CONTENT LOST 时由 provider 附加）
            _raw_diag = getattr(response, "_raw_diagnostic", None)
            if _raw_diag:
                debug_data["raw_diagnostic"] = _raw_diag

            with open(debug_file, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, ensure_ascii=False, indent=2, default=str)

            # 摘要日志
            text_len = sum(
                len(b.get("text", "")) for b in content_blocks if b.get("type") == "text"
            )
            tool_count = sum(1 for b in content_blocks if b.get("type") == "tool_use")
            in_tokens = debug_data["llm_response"]["usage"]["input_tokens"]
            out_tokens = debug_data["llm_response"]["usage"]["output_tokens"]
            logger.debug(
                f"[LLM DEBUG] Response saved: text_len={text_len}, tool_calls={tool_count}, "
                f"tokens_in={in_tokens}, tokens_out={out_tokens} (request_id={request_id})"
            )

        except Exception as e:
            logger.warning(f"[LLM DEBUG] Failed to save response debug file: {e}")

    def _serialize_response_content(self, response) -> list[dict]:
        """
        序列化 LLM 响应的 content blocks，支持 text/thinking/tool_use。

        Truncation 规则:
        - text: 保留完整
        - thinking: truncate 到 500 字符
        - tool_use: name/id 完整保留，input 完整保留（便于诊断截断问题）
        """
        blocks = []

        # LLMResponse 对象
        if hasattr(response, "text") and not hasattr(response, "content"):
            # 简单 text 响应
            blocks.append({"type": "text", "text": response.text or ""})
            for tc in getattr(response, "tool_calls", []):
                input_str = (
                    json.dumps(tc.input, ensure_ascii=False, default=str)
                    if isinstance(tc.input, dict)
                    else str(tc.input)
                )
                d: dict = {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": input_str,
                }
                if getattr(tc, "provider_extra", None):
                    d["provider_extra"] = tc.provider_extra
                blocks.append(d)
            return blocks

        # Anthropic Message 格式
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
                d2: dict = {
                    "type": "tool_use",
                    "id": bid,
                    "name": name,
                    "input": input_str,
                }
                extra = (
                    block.get("provider_extra")
                    if isinstance(block, dict)
                    else getattr(block, "provider_extra", None)
                )
                if extra:
                    d2["provider_extra"] = extra
                blocks.append(d2)
            else:
                blocks.append({"type": str(block_type), "raw": str(block)})

        return blocks

    def _cleanup_old_debug_files(
        self,
        debug_dir: Path,
        max_age_days: int = 7,
        max_size_mb: int = 512,
    ) -> None:
        """清理超过指定天数或目录体积上限的调试文件（request + response）。"""
        try:
            from datetime import timedelta

            cutoff_time = datetime.now() - timedelta(days=max_age_days)
            deleted_count = 0
            remaining: list[tuple[float, int, Path]] = []

            for pattern in ("llm_request_*.json", "llm_response_*.json"):
                for file in debug_dir.glob(pattern):
                    try:
                        stat = file.stat()
                        mtime = datetime.fromtimestamp(stat.st_mtime)
                        if mtime < cutoff_time:
                            file.unlink()
                            deleted_count += 1
                        else:
                            remaining.append((stat.st_mtime, stat.st_size, file))
                    except Exception:
                        pass

            if max_size_mb > 0:
                max_bytes = max_size_mb * 1024 * 1024
                total_bytes = sum(size for _, size, _ in remaining)
                for _, size, file in sorted(remaining, key=lambda item: item[0]):
                    if total_bytes <= max_bytes:
                        break
                    try:
                        file.unlink()
                        total_bytes -= size
                        deleted_count += 1
                    except Exception:
                        pass

            if deleted_count > 0:
                logger.debug(
                    f"[LLM DEBUG] Cleaned up {deleted_count} old debug files "
                    f"(retention={max_age_days} days, max_size={max_size_mb} MB)"
                )

        except Exception as e:
            logger.warning(f"[LLM DEBUG] Failed to cleanup old files: {e}")

    def _serialize_message(self, msg) -> dict:
        """将 Message 对象序列化为字典"""
        result = {"role": getattr(msg, "role", "unknown")}

        content = getattr(msg, "content", None)
        if isinstance(content, str):
            result["content"] = content
        elif isinstance(content, list):
            result["content"] = []
            for block in content:
                if hasattr(block, "__dict__"):
                    block_dict = {"type": getattr(block, "type", "unknown")}
                    # 处理常见的 block 属性
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

        # 添加 reasoning_content（如果有）
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            result["reasoning_content"] = msg.reasoning_content

        return result

    async def health_check(self) -> dict[str, bool]:
        """检查所有端点健康状态"""
        return await self._llm_client.health_check()

        # ========================================================================
        # 动态模型切换
        # ========================================================================
        """Health-probe every endpoint; delegates to EndpointFailoverView."""
        return await self._failover_view.health_check()

    def switch_model(
        self,
        endpoint_name: str,
        hours: float = 12,
        reason: str = "",
        conversation_id: str | None = None,
        policy: str = "prefer",
    ) -> tuple[bool, str]:
        """Temporarily switch to a given endpoint; delegates to EndpointFailoverView."""
        return self._failover_view.switch_model(
            endpoint_name,
            hours,
            reason,
            conversation_id=conversation_id,
            policy=policy,
        )

    def get_fallback_model(self, conversation_id: str | None = None) -> str:
        """Return next-priority fallback endpoint name (empty when none)."""
        return self._failover_view.next_fallback_model(conversation_id)

    def restore_default_model(self, conversation_id: str | None = None) -> tuple[bool, str]:
        """Drop the manual override and revert to default priority."""
        return self._failover_view.restore_default(conversation_id=conversation_id)

    def get_current_model_info(self, conversation_id: str | None = None) -> dict:
        """Render current ModelInfo as dict; delegates to EndpointFailoverView."""
        return self._failover_view.current_model_info(conversation_id=conversation_id)

    def list_available_models(self) -> list[dict]:
        """List every available ModelInfo as dicts."""
        return self._failover_view.list_models()

    def get_override_status(self) -> dict | None:
        """Return current override status, or None."""
        return self._failover_view.override_status()

    def update_model_priority(self, priority_order: list[str]) -> tuple[bool, str]:
        """Update model priority order and persist."""
        return self._failover_view.update_priority(priority_order)

    async def plan(self, task: str, context: Context | None = None) -> str:
        """为任务生成执行计划"""
        prompt = f"""请为以下任务制定详细的执行计划:

任务: {task}

要求:
1. 分解为具体的步骤
2. 识别需要的工具和技能
3. 考虑可能的失败情况和备选方案
4. 估计每个步骤的复杂度

请以 Markdown 格式输出计划。"""

        response = await self.think(prompt, context)
        return response.content

    async def generate_code(
        self,
        description: str,
        language: str = "python",
        context: Context | None = None,
    ) -> str:
        """生成代码"""
        prompt = f"""请生成以下功能的 {language} 代码:

{description}

要求:
1. 代码应该完整、可运行
2. 包含必要的导入语句
3. 添加适当的注释和 docstring
4. 遵循 {language} 的最佳实践
5. 如果是类，包含类型提示

只输出代码，不要解释。"""

        response = await self.think(prompt, context)

        # 提取代码块
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
        """分析错误并提供解决方案"""
        prompt = f"""请分析以下错误并提供解决方案:

错误信息:
{error}

{"上下文:" + context if context else ""}

请提供:
1. 错误原因分析
2. 可能的解决方案（按优先级排序）
3. 如何避免类似错误

以 JSON 格式输出:
{{
    "cause": "错误原因",
    "solutions": ["解决方案1", "解决方案2"],
    "prevention": "预防措施"
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

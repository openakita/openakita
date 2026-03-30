"""
OpenAI Provider

支持 OpenAI API 格式的调用，包括：
- OpenAI 官方 API
- DashScope（通义千问）
- Kimi（Moonshot AI）
- OpenRouter
- 硅基流动
- 云雾 API
- 其他 OpenAI 兼容 API
"""

import json
import logging
from collections.abc import AsyncIterator
from json import JSONDecodeError

import httpx

from ..converters.messages import convert_messages_to_openai
from ..converters.tools import (
    convert_tool_calls_from_openai,
    convert_tools_to_openai,
    has_text_tool_calls,
    parse_text_tool_calls,
)
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
    """检测错误是否表明端点仅支持流式请求（stream-only relay/中转站）。"""
    err_lower = error.lower()
    return (
        "stream must be set to true" in err_lower
        or "stream is required" in err_lower
        or ("text/event-stream" in err_lower and "invalid json" in err_lower)
    )


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
    """OpenAI 兼容 API Provider"""

    def __init__(self, config: EndpointConfig):
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int | None = None  # 记录创建客户端时的事件循环 ID
        self._stream_only: bool = config.stream_only

    @property
    def api_key(self) -> str:
        """获取 API Key"""
        return self.config.get_api_key() or ""

    @property
    def base_url(self) -> str:
        """获取 base URL，自动剥离用户误粘贴的 OpenAI 兼容端点路径后缀。"""
        return normalize_base_url(self.config.base_url)

    @property
    def _api_url(self) -> str:
        """完整 API 端点 URL，子类可覆写以切换协议（如 Responses API）。"""
        return f"{self.base_url}/chat/completions"

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端

        注意：httpx.AsyncClient 绑定到创建时的事件循环。
        如果事件循环变化（如定时任务创建新循环），需要重新创建客户端。
        """
        import asyncio

        try:
            current_loop = asyncio.get_running_loop()
            current_loop_id = id(current_loop)
        except RuntimeError:
            current_loop_id = None

        # 检查是否需要重新创建客户端
        need_recreate = (
            self._client is None
            or self._client.is_closed
            or self._client_loop_id != current_loop_id
        )

        if need_recreate:
            # 安全关闭旧客户端
            if self._client is not None and not self._client.is_closed:
                try:
                    await self._client.aclose()
                except Exception:
                    pass  # 忽略关闭错误

            # 获取代理和网络配置
            proxy = get_proxy_config()
            transport = get_httpx_transport()  # IPv4-only 支持
            is_local = self._is_local_endpoint()

            # 本地端点（Ollama 等）自动放大 read timeout
            # 本地推理受 CPU/GPU 资源限制，推理时间远大于云端 API
            # 默认 read timeout 可能导致频繁超时被误判为故障
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

            # trust_env=False: 代理由 get_proxy_config() 显式管理（含可达性验证）。
            # 避免 macOS/Windows 残留系统代理（Clash/V2Ray 等）导致请求被路由到
            # 不存在的代理端口而失败。
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
        """根据请求体大小动态计算超时

        大上下文（>60K tokens 估算）场景下，默认 read timeout 可能不够，
        需按比例放大以避免频繁 ReadTimeout 导致的无效重试。

        Returns:
            httpx.Timeout 或 None（不需要覆盖时）
        """
        messages = body.get("messages", [])
        body_chars = sum(
            len(str(m.get("content", ""))) + len(str(m.get("tool_calls", "")))
            for m in messages
        )
        tools = body.get("tools", [])
        if tools:
            body_chars += sum(len(str(t)) for t in tools)

        est_tokens = body_chars // 2  # 中文约 2 字符/token
        if est_tokens < 60_000:
            return None

        base_timeout = self.config.timeout or 180
        scale = min(est_tokens / 60_000, 3.0)  # 最多 3 倍
        new_read = base_timeout * scale
        new_read = min(new_read, 540.0)  # 上限 9 分钟
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
        """发送聊天请求（支持 stream-only 端点自动检测）"""
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
        """非流式请求实现（原始路径，逻辑完全不变）。调用方须已获取 rate limit。"""
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
                if response.status_code == 401:
                    raise AuthenticationError(f"Authentication failed: {body}")
                if response.status_code == 429:
                    raise RateLimitError(f"Rate limit exceeded: {body}")
                raise LLMError(f"API error ({response.status_code}): {body}")

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

            # 某些 OpenAI 兼容 API 在 HTTP 200 响应体内返回错误（不走标准 HTTP 状态码）
            if "error" in data and data["error"]:
                err_obj = data["error"] if isinstance(data["error"], dict) else {"message": str(data["error"])}
                err_msg = err_obj.get("message", str(err_obj))
                err_code = err_obj.get("code", "")
                logger.warning(
                    f"[OpenAI] '{self.name}': API returned 200 with error in body: "
                    f"code={err_code}, message={err_msg}"
                )
                raise LLMError(f"API error in response body: {err_msg}")

            # HTTP 200 但 choices 为空 —— 某些中转/兼容 API 的异常行为
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
        """SSE 传输层：发送流式请求，解析 SSE 行，yield 转换后的事件。

        body 须已包含 "stream": True。调用方须已获取 rate limit。
        子类可覆写以适配不同 SSE 格式（如 Responses API 的 named events）。
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
                    if response.status_code == 401:
                        raise AuthenticationError(
                            f"Authentication failed: {error_text}"
                        )
                    if response.status_code == 429:
                        raise RateLimitError(
                            f"Rate limit exceeded: {error_text}"
                        )
                    raise LLMError(
                        f"API error ({response.status_code}): {error_text}"
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
                                yield self._convert_stream_event(event)
                            except json.JSONDecodeError:
                                continue
                    elif not has_content and not line.startswith(":"):
                        try:
                            err_data = json.loads(line)
                            if "error" in err_data:
                                err_obj = err_data["error"]
                                err_msg = err_obj.get("message", str(err_obj)) if isinstance(err_obj, dict) else str(err_obj)
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
            self.mark_unhealthy(f"Stream request error: {detail}", is_local=self._is_local_endpoint())
            raise LLMError(f"Stream request failed: {detail}")

    async def _chat_via_stream(self, request: LLMRequest) -> LLMResponse:
        """流式传输 → 同步响应适配器：收集流式事件，组装为 LLMResponse。

        用于 stream-only 端点（如 Codex relay）。调用方须已获取 rate limit。
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
                raise LLMError(
                    f"Stream error from '{self.name}': {event.get('error', 'unknown')}"
                )

        content_blocks: list = []
        text = "".join(text_parts)
        has_native_tool_calls = bool(tool_calls)

        # 文本格式工具调用提取（与 _parse_response 对齐）
        # stream-only 端点的模型可能在文本中输出 [TOOL_CALL]/dot-style 等格式
        if text and not has_native_tool_calls:
            if has_text_tool_calls(text):
                logger.info(
                    f"[TEXT_TOOL_PARSE] Detected text-based tool calls "
                    f"from stream response ({self.name})"
                )
                clean_text, text_tool_calls = parse_text_tool_calls(text)
                if text_tool_calls:
                    text = clean_text
                    content_blocks.extend(text_tool_calls)
                    logger.info(
                        f"[TEXT_TOOL_PARSE] Extracted {len(text_tool_calls)} "
                        f"tool calls from stream text"
                    )

        if text:
            content_blocks.append(TextBlock(text=text))

        for call_id, tc in tool_calls.items():
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": tc["arguments"]}
            content_blocks.append(ToolUseBlock(
                id=call_id,
                name=tc["name"],
                input=args,
            ))

        has_any_tool_calls = has_native_tool_calls or any(
            isinstance(b, ToolUseBlock) for b in content_blocks
        )
        if has_any_tool_calls and stop_reason != StopReason.MAX_TOKENS:
            stop_reason = StopReason.TOOL_USE

        return LLMResponse(
            id="",
            content=content_blocks,
            stop_reason=stop_reason,
            usage=Usage(),
            model=response_model,
        )

    async def chat_stream(self, request: LLMRequest) -> AsyncIterator[dict]:
        """流式聊天请求"""
        await self.acquire_rate_limit()
        body = self._build_request_body(request)
        body["stream"] = True
        async for event in self._iter_sse_events(body):
            yield event

    def _is_local_endpoint(self) -> bool:
        """检查是否为本地端点（Ollama/LM Studio 等）"""
        url = self.base_url.lower()
        return any(host in url for host in (
            "localhost", "127.0.0.1", "0.0.0.0", "[::1]",
        ))

    def _get_auth(self) -> _BearerAuth:
        """获取认证信息（通过 httpx Auth 机制，确保重定向时不丢失凭据）"""
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
        """构建请求头（含 Authorization，不依赖 httpx auth 机制）"""
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
        """构建请求体"""
        # 转换消息格式（传递 provider 以正确处理视频等多媒体内容）
        thinking_enabled = request.enable_thinking and self.config.has_capability("thinking")

        # thinking-only 模型（deepseek-reasoner、QwQ 等）无法关闭思考，
        # 即使 fallback 降级了 enable_thinking=False，
        # 仍必须注入 reasoning_content 并保持 thinking 启用，否则 API 返回 400
        is_always_thinking = False
        if not thinking_enabled and self.config.has_capability("thinking"):
            from ..capabilities import is_thinking_only
            is_always_thinking = is_thinking_only(
                self.config.model, provider_slug=self.config.provider,
            )
            if is_always_thinking:
                thinking_enabled = True

        messages = convert_messages_to_openai(
            request.messages, request.system,
            provider=self.config.provider,
            enable_thinking=thinking_enabled,
        )

        body = {
            "model": self.config.model,
            "messages": messages,
        }

        # max_tokens 处理策略：
        # 理想情况下不传 max_tokens 可让 API 使用模型默认上限，但实际上部分 OpenAI 兼容
        # API（如 NVIDIA NIM）默认 max_tokens 极低（~200），开启 thinking 后所有输出预算
        # 被思考内容耗尽，导致无可见文本返回。
        # 因此：调用方传了 max_tokens > 0 时直接使用，否则用端点配置值或兜底 16384。
        #
        # 特殊情况 — OpenAI o1/o3/o4 推理模型：
        # 这些模型拒绝 max_tokens 参数，要求使用 max_completion_tokens。
        # 检测方式：模型名含 "o1-"/"o3-"/"o4-" 且 provider 为 openai。
        _model_lower = self.config.model.lower()
        _is_openai_reasoning = (
            self.config.provider == "openai"
            and any(tag in _model_lower for tag in ("o1-", "o3-", "o4-", "/o1", "/o3", "/o4"))
        )
        _token_key = "max_completion_tokens" if _is_openai_reasoning else "max_tokens"

        _max_tokens = request.max_tokens
        if _max_tokens and _max_tokens > 0:
            body[_token_key] = _max_tokens
        else:
            _fallback = self.config.max_tokens or 16384
            body[_token_key] = _fallback

        # 工具
        if request.tools:
            body["tools"] = convert_tools_to_openai(request.tools)
            body["tool_choice"] = "auto"

        # 温度
        if request.temperature != 1.0:
            body["temperature"] = request.temperature

        # 停止序列
        if request.stop_sequences:
            body["stop"] = request.stop_sequences

        # 额外参数（服务商特定）
        if self.config.extra_params:
            body.update(self.config.extra_params)
        if request.extra_params:
            body.update(request.extra_params)

        # ── 本地端点检测 ──
        # Ollama / LM Studio 等本地推理引擎不支持 OpenAI 风格的
        # thinking: {"type": "enabled"} 嵌套参数，但 Ollama 0.9+ 支持
        # enable_thinking (bool) 来控制双模模型（如 qwen3.5）的思考模式。
        is_local = self._is_local_endpoint()

        # DashScope 思考模式 — 必须在 extra_params 之后，以覆盖其中的 enable_thinking
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

        # SiliconFlow 思考模式
        #
        # SiliconFlow API 有两类思考模型（参考官方文档）：
        #
        # A 类 - 双模模型（支持 enable_thinking 切换）：
        #   Qwen3 系列, Hunyuan-A13B, GLM-4.6V/4.5V, DeepSeek-V3.1/V3.2 系列
        #   → 发送 enable_thinking (bool) + thinking_budget
        #
        # B 类 - 天然思考模型（始终思考，不接受 enable_thinking）：
        #   Kimi-K2-Thinking, DeepSeek-R1, QwQ-32B, GLM-Z1 系列
        #   → 只发送 thinking_budget 控制深度，不发送 enable_thinking
        #   → 向这些模型发送 enable_thinking 会导致 400:
        #     "Value error, current model does not support parameter enable_thinking"
        #
        # 两类模型都不支持 OpenAI 风格的 thinking: {"type": "enabled"} + reasoning_effort
        elif self.config.provider in ("siliconflow", "siliconflow-intl") and self.config.has_capability("thinking"):
            from ..capabilities import is_thinking_only
            sf_thinking_only = is_thinking_only(self.config.model, provider_slug=self.config.provider)

            if sf_thinking_only:
                # B 类：天然思考模型 — 只允许 thinking_budget 控制深度
                # 必须清理 extra_params 可能泄漏的 enable_thinking
                body.pop("enable_thinking", None)
                if request.thinking_depth:
                    budget_map = {"low": 1024, "medium": 4096, "high": 16384}
                    budget = budget_map.get(request.thinking_depth)
                    if budget:
                        body["thinking_budget"] = budget
            else:
                # A 类：双模模型 — enable_thinking 切换 + thinking_budget
                body["enable_thinking"] = bool(request.enable_thinking)
                if request.enable_thinking:
                    if request.thinking_depth:
                        budget_map = {"low": 1024, "medium": 4096, "high": 16384}
                        budget = budget_map.get(request.thinking_depth)
                        if budget:
                            body["thinking_budget"] = budget
                else:
                    body.pop("thinking_budget", None)

            # 清理不适用于 SiliconFlow 的 OpenAI 风格参数（可能由 extra_params 引入）
            body.pop("thinking", None)
            body.pop("reasoning_effort", None)

        # 本地端点思考模式（Ollama 0.9+ 等）
        #
        # Ollama 0.9+ 的 OpenAI 兼容 API 支持 enable_thinking (bool) 来切换
        # 双模模型（如 qwen3.5）的思考模式。Thinking-only 模型（如 qwen3）
        # 通过 <think> 标签自行输出思考内容，无需 API 参数控制。
        # 不使用 OpenAI 风格的 thinking: {"type": "enabled"} 或 reasoning_effort。
        elif is_local and self.config.has_capability("thinking"):
            if request.enable_thinking:
                body["enable_thinking"] = True

        # OpenAI 兼容端点思考模式（火山引擎/DeepSeek/vLLM/OpenRouter 等）
        #
        # 背景：
        # - 原生 OpenAI o1/o3 系列天然就是思考模型，只需 reasoning_effort 控制深度
        # - 但其他 OpenAI-compatible 端点（火山引擎/DeepSeek/vLLM 等）需要显式传
        #   thinking: {"type": "enabled"} 来启用思考模式，reasoning_effort 只是可选的深度控制
        # - 如果只传 reasoning_effort 而不启用 thinking，火山引擎等 API 会返回 400:
        #   "Invalid combination of reasoning_effort and thinking type: medium + disabled"
        #
        # 排除: DashScope（上面已处理）、SiliconFlow（上面已处理）、本地端点（上面已处理）
        elif (
            self.config.has_capability("thinking")
            and not is_local
        ):
            # 清理 DashScope 风格参数（可能由 extra_params 泄漏）
            # 此分支使用 OpenAI 风格 thinking: {"type": "enabled"}，不使用 enable_thinking
            body.pop("enable_thinking", None)

            if request.enable_thinking or is_always_thinking:
                # 显式启用思考（DeepSeek/vLLM/火山引擎等 OpenAI-compatible 标准）
                # 对于原生 OpenAI o1/o3 模型，此参数会被忽略（它们天然就是思考模型）
                # thinking-only 模型在 fallback 降级后也必须保持启用
                if "thinking" not in body:
                    body["thinking"] = {"type": "enabled"}
                # 思考深度控制（可选）
                if request.thinking_depth:
                    depth_map = {"low": "low", "medium": "medium", "high": "high"}
                    effort = depth_map.get(request.thinking_depth)
                    if effort:
                        body["reasoning_effort"] = effort
            else:
                # 显式关闭思考（避免 extra_params 中的残留设置）
                body.pop("reasoning_effort", None)
                if "thinking" in body:
                    body["thinking"] = {"type": "disabled"}

        # ── 本地端点清理 ──
        # 移除可能通过 extra_params 泄漏的、本地引擎不支持的思考参数。
        # enable_thinking (bool) 不在此列：Ollama 0.9+ 原生支持，
        # 其他本地引擎（LM Studio / 旧版 Ollama）对未知简单字段静默忽略。
        if is_local:
            _stripped = [k for k in ("thinking", "thinking_budget", "reasoning_effort") if k in body]
            for _key in _stripped:
                body.pop(_key, None)
            if _stripped:
                logger.debug(
                    f"[OpenAI] Local endpoint '{self.name}': stripped thinking params {_stripped}"
                )

        # ── 端点级 thinking 参数剥离 ──
        # 若端点曾因 thinking/reasoning_effort 返回 400，
        # 客户端自愈逻辑已在 provider 上标记 _thinking_params_unsupported，
        # 此处作为最终安全网，确保不再发送任何 thinking 相关参数。
        if getattr(self, '_thinking_params_unsupported', False):
            for _tp in ("thinking", "reasoning_effort", "enable_thinking", "thinking_budget"):
                body.pop(_tp, None)

        # ── 请求体卫生检查 ──
        # extra_params 的 body.update() 是盲覆盖，可能将精心计算的参数（如 max_tokens）
        # 替换为无效值。在 return 前做最终校验，确保发出的请求体始终合法。
        for _tk in ("max_tokens", "max_completion_tokens"):
            _tv = body.get(_tk)
            if _tv is not None and (not isinstance(_tv, int) or _tv <= 0):
                body.pop(_tk, None)

        return body

    def _parse_response(self, data: dict) -> LLMResponse:
        """解析响应"""
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

        # 文本内容 — 兼容 string 和 array 两种格式
        # 部分 OpenAI 兼容 API (如 Google Gemini OpenAI-compat) 返回 content 为数组:
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

        # 原生工具调用
        tool_calls = message.get("tool_calls", [])
        if tool_calls:
            converted = convert_tool_calls_from_openai(tool_calls)
            if converted:
                content_blocks.extend(converted)
                has_tool_calls = True
            logger.info(
                f"[TOOL_CALLS] Received {len(tool_calls)} native tool calls from {self.name}"
            )
            # 容错日志：有 tool_calls 但未能转换（通常是兼容网关字段不规范）
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

        # 文本格式工具调用解析（降级方案）
        # 当模型不支持原生工具调用时，解析文本中的 <function_calls> 格式
        # 同时检查 reasoning_content 中是否嵌入了工具调用
        _tool_calls_from_reasoning = False
        combined_for_check = text_content
        reasoning_content = message.get("reasoning_content") or ""
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

        # 添加文本内容
        if text_content:
            content_blocks.insert(0, TextBlock(text=text_content))

        # 解析停止原因
        finish_reason = choice.get("finish_reason", "stop")
        if has_tool_calls and finish_reason == "length":
            # finish_reason=length + tool_calls = 输出被截断，工具参数可能不完整
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

        # 解析使用统计
        usage_data = data.get("usage", {})
        usage = Usage(
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
        )

        return LLMResponse(
            id=data.get("id", ""),
            content=content_blocks,
            stop_reason=stop_reason,
            usage=usage,
            model=data.get("model", self.config.model),
            reasoning_content=reasoning_content,
        )

    def _convert_stream_event(self, event: dict) -> dict:
        """转换流式事件为统一格式"""
        choices = event.get("choices", [])
        if not choices:
            return {"type": "ping"}

        choice = choices[0]
        delta = choice.get("delta", {})

        result = {"type": "content_block_delta"}

        if "content" in delta:
            result["delta"] = {"type": "text", "text": delta["content"]}
        elif "tool_calls" in delta:
            tool_calls = delta["tool_calls"]
            if tool_calls:
                tc = tool_calls[0]
                result["delta"] = {
                    "type": "tool_use",
                    "id": tc.get("id"),
                    "name": tc.get("function", {}).get("name"),
                    "arguments": tc.get("function", {}).get("arguments"),
                }

        if choice.get("finish_reason"):
            result["type"] = "message_stop"
            result["stop_reason"] = choice["finish_reason"]

        return result

    async def close(self):
        """关闭客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

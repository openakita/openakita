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
    Usage,
)
from .base import LLMProvider
from .proxy_utils import build_httpx_timeout, get_httpx_transport, get_proxy_config

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """OpenAI 兼容 API Provider"""

    def __init__(self, config: EndpointConfig):
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int | None = None  # 记录创建客户端时的事件循环 ID

    @property
    def api_key(self) -> str:
        """获取 API Key"""
        return self.config.get_api_key() or ""

    @property
    def base_url(self) -> str:
        """获取 base URL"""
        return self.config.base_url.rstrip("/")

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

            # 本地端点（Ollama 等）自动放大 read timeout
            # 本地推理受 CPU/GPU 资源限制，推理时间远大于云端 API
            # 默认 read timeout 可能导致频繁超时被误判为故障
            timeout_value = self.config.timeout
            if self._is_local_endpoint():
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

            client_kwargs = {
                "timeout": build_httpx_timeout(timeout_value, default=60.0),
                "follow_redirects": True,
            }

            if proxy:
                client_kwargs["proxy"] = proxy
                logger.debug(f"[OpenAI] Using proxy: {proxy}")

            if transport:
                client_kwargs["transport"] = transport

            self._client = httpx.AsyncClient(**client_kwargs)
            self._client_loop_id = current_loop_id

        return self._client

    async def chat(self, request: LLMRequest) -> LLMResponse:
        """发送聊天请求"""
        client = await self._get_client()

        # 构建请求体
        body = self._build_request_body(request)

        logger.debug(f"OpenAI request to {self.base_url}: model={body.get('model')}")

        # 发送请求
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._build_headers(),
                json=body,
            )

            if response.status_code == 401:
                raise AuthenticationError(f"Authentication failed: {response.text}")
            if response.status_code == 429:
                raise RateLimitError(f"Rate limit exceeded: {response.text}")
            if response.status_code >= 400:
                raise LLMError(f"API error ({response.status_code}): {response.text}")

            try:
                data = response.json()
            except JSONDecodeError:
                # 某些 OpenAI 兼容网关可能返回 200 但 body 为空/非 JSON（例如 HTML 错误页）
                # 这里把关键信息打进错误文本，便于排障（base_url 是否需要 /v1 等）
                content_type = response.headers.get("content-type", "")
                body_preview = (response.text or "")[:500]
                raise LLMError(
                    "Invalid JSON response from OpenAI-compatible endpoint "
                    f"(status={response.status_code}, content-type={content_type}, "
                    f"body_preview={body_preview!r})"
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

    async def chat_stream(self, request: LLMRequest) -> AsyncIterator[dict]:
        """流式聊天请求"""
        client = await self._get_client()

        body = self._build_request_body(request)
        body["stream"] = True

        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._build_headers(),
                json=body,
            ) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    raise LLMError(f"API error ({response.status_code}): {error_body.decode()}")

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data.strip() and data != "[DONE]":
                            try:
                                event = json.loads(data)
                                yield self._convert_stream_event(event)
                            except json.JSONDecodeError:
                                continue

                self.mark_healthy()

        except httpx.TimeoutException as e:
            detail = f"{type(e).__name__}: {e}"
            self.mark_unhealthy(f"Timeout: {detail}", is_local=self._is_local_endpoint())
            raise LLMError(f"Stream timeout: {detail}")
        except httpx.RequestError as e:
            detail = f"{type(e).__name__}: {e}" if str(e) else f"{type(e).__name__}({repr(e)})"
            self.mark_unhealthy(f"Stream request error: {detail}", is_local=self._is_local_endpoint())
            raise LLMError(f"Stream request failed: {detail}")

    def _is_local_endpoint(self) -> bool:
        """检查是否为本地端点（Ollama/LM Studio 等）"""
        url = self.base_url.lower()
        return any(host in url for host in (
            "localhost", "127.0.0.1", "0.0.0.0", "[::1]",
        ))

    def _build_headers(self) -> dict:
        """构建请求头"""
        # 避免 Authorization: Bearer <empty> 导致 httpx 报 Illegal header value
        api_key = (self.api_key or "").strip()
        if not api_key:
            # 本地服务（Ollama/LM Studio 等）不需要真实 API Key
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

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        # OpenRouter 需要额外的头
        if "openrouter" in self.base_url.lower():
            headers["HTTP-Referer"] = "https://github.com/openakita"
            headers["X-Title"] = "OpenAkita"

        return headers

    def _build_request_body(self, request: LLMRequest) -> dict:
        """构建请求体"""
        # 转换消息格式（传递 provider 以正确处理视频等多媒体内容）
        thinking_enabled = request.enable_thinking and self.config.has_capability("thinking")
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
        # OpenAI 兼容 API 中 max_tokens 是可选参数。
        # 不传时，API 会使用模型的默认最大输出上限，LLM 可以生成到自然结束。
        # 这对 Agent 场景非常重要——工具调用（如 write_file）可能产生极长的 JSON 参数，
        # 人为限制 max_tokens 会导致 JSON 被截断、工具调用失败。
        # 因此：仅当调用方显式传了 max_tokens 且 > 0 时才发送，否则不传（让 API 用模型默认值）。
        _max_tokens = request.max_tokens
        if _max_tokens and _max_tokens > 0:
            body["max_tokens"] = _max_tokens

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
        # Ollama / LM Studio 等本地推理引擎的 OpenAI 兼容 API 不支持
        # thinking: {"type": "enabled"} 格式的思考参数。
        # 本地模型的思考能力通过模型自身机制实现（如 qwen3 的 <think> 标签），
        # 无需也不能通过 API 参数控制。
        is_local = self._is_local_endpoint()

        # DashScope 思考模式 — 必须在 extra_params 之后，以覆盖其中的 enable_thinking
        if self.config.provider == "dashscope" and self.config.has_capability("thinking"):
            body["enable_thinking"] = bool(request.enable_thinking)
            if request.enable_thinking and request.thinking_depth:
                # 映射 thinking_depth 到 DashScope thinking_budget
                budget_map = {"low": 1024, "medium": 4096, "high": 16384}
                budget = budget_map.get(request.thinking_depth)
                if budget:
                    body["thinking_budget"] = budget
            elif not request.enable_thinking:
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
            is_always_thinking = is_thinking_only(self.config.model, provider_slug=self.config.provider)

            if is_always_thinking:
                # B 类：天然思考模型 — 只允许 thinking_budget 控制深度
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

        # OpenAI 兼容端点思考模式（火山引擎/DeepSeek/vLLM/OpenRouter 等）
        #
        # 背景：
        # - 原生 OpenAI o1/o3 系列天然就是思考模型，只需 reasoning_effort 控制深度
        # - 但其他 OpenAI-compatible 端点（火山引擎/DeepSeek/vLLM 等）需要显式传
        #   thinking: {"type": "enabled"} 来启用思考模式，reasoning_effort 只是可选的深度控制
        # - 如果只传 reasoning_effort 而不启用 thinking，火山引擎等 API 会返回 400:
        #   "Invalid combination of reasoning_effort and thinking type: medium + disabled"
        #
        # 排除: DashScope（上面已处理）、SiliconFlow（上面已处理）、本地端点
        elif (
            self.config.has_capability("thinking")
            and not is_local
        ):
            if request.enable_thinking:
                # 显式启用思考（DeepSeek/vLLM/火山引擎等 OpenAI-compatible 标准）
                # 对于原生 OpenAI o1/o3 模型，此参数会被忽略（它们天然就是思考模型）
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
        # 移除可能通过 extra_params 泄漏到请求体中的思考相关参数，
        # 避免 Ollama / LM Studio 返回 400 错误
        if is_local:
            _stripped = [k for k in ("thinking", "enable_thinking", "thinking_budget", "reasoning_effort") if k in body]
            for _key in _stripped:
                body.pop(_key, None)
            if _stripped:
                logger.debug(
                    f"[OpenAI] Local endpoint '{self.name}': stripped thinking params {_stripped} "
                    f"(local models use native thinking mechanism, not API params)"
                )

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

        # 文本内容
        text_content = message.get("content") or ""

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
        if not has_tool_calls and text_content and has_text_tool_calls(text_content):
            logger.info(f"[TEXT_TOOL_PARSE] Detected text-based tool calls from {self.name}")
            clean_text, text_tool_calls = parse_text_tool_calls(text_content)

            if text_tool_calls:
                # 更新文本内容（移除工具调用部分）
                text_content = clean_text
                content_blocks.extend(text_tool_calls)
                has_tool_calls = True
                logger.info(
                    f"[TEXT_TOOL_PARSE] Extracted {len(text_tool_calls)} tool calls from text"
                )

        # 添加文本内容
        if text_content:
            content_blocks.insert(0, TextBlock(text=text_content))

        # 保存思考内容（Kimi/DashScope 等模型返回）
        reasoning_content = message.get("reasoning_content")

        # 解析停止原因
        finish_reason = choice.get("finish_reason", "stop")
        if has_tool_calls:
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

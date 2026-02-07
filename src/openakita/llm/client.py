"""
LLM 统一客户端

提供统一的 LLM 调用接口，支持：
- 多端点配置
- 自动故障切换
- 能力分流（根据请求自动选择合适的端点）
- 健康检查
- 动态模型切换（临时/永久）
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .config import get_default_config_path, load_endpoints_config
from .providers.anthropic import AnthropicProvider
from .providers.base import LLMProvider
from .providers.openai import OpenAIProvider
from .types import (
    AllEndpointsFailedError,
    AuthenticationError,
    EndpointConfig,
    ImageBlock,
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    Tool,
    UnsupportedMediaError,
    VideoBlock,
)

logger = logging.getLogger(__name__)


# ==================== 动态切换相关数据结构 ====================


@dataclass
class EndpointOverride:
    """端点临时覆盖配置"""

    endpoint_name: str  # 覆盖到的端点名称
    expires_at: datetime  # 过期时间
    created_at: datetime = field(default_factory=datetime.now)
    reason: str = ""  # 切换原因（可选）

    @property
    def is_expired(self) -> bool:
        """检查是否已过期"""
        return datetime.now() >= self.expires_at

    @property
    def remaining_hours(self) -> float:
        """剩余有效时间（小时）"""
        if self.is_expired:
            return 0.0
        delta = self.expires_at - datetime.now()
        return delta.total_seconds() / 3600

    def to_dict(self) -> dict:
        """转换为字典（用于序列化）"""
        return {
            "endpoint_name": self.endpoint_name,
            "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EndpointOverride":
        """从字典创建（用于反序列化）"""
        return cls(
            endpoint_name=data["endpoint_name"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            reason=data.get("reason", ""),
        )


@dataclass
class ModelInfo:
    """模型信息（用于列表展示）"""

    name: str  # 端点名称
    model: str  # 模型名称
    provider: str  # 提供商
    priority: int  # 优先级
    is_healthy: bool  # 健康状态
    is_current: bool  # 是否当前使用
    is_override: bool  # 是否临时覆盖
    capabilities: list[str]  # 支持的能力
    note: str = ""  # 备注


class LLMClient:
    """统一 LLM 客户端"""

    # 默认临时切换有效期（小时）
    DEFAULT_OVERRIDE_HOURS = 12

    def __init__(
        self,
        config_path: Path | None = None,
        endpoints: list[EndpointConfig] | None = None,
    ):
        """
        初始化 LLM 客户端

        Args:
            config_path: 配置文件路径
            endpoints: 直接传入端点配置（优先于 config_path）
        """
        self._endpoints: list[EndpointConfig] = []
        self._providers: dict[str, LLMProvider] = {}
        self._settings: dict = {}
        self._config_path: Path | None = config_path

        # 动态切换相关
        self._endpoint_override: EndpointOverride | None = None
        # per-conversation 临时覆盖（用于并发隔离）
        self._conversation_overrides: dict[str, EndpointOverride] = {}

        if endpoints:
            self._endpoints = sorted(endpoints, key=lambda x: x.priority)
        elif config_path or get_default_config_path().exists():
            self._config_path = config_path or get_default_config_path()
            self._endpoints, self._settings = load_endpoints_config(config_path)

        # 创建 Provider 实例
        self._init_providers()

    def _init_providers(self):
        """初始化所有 Provider"""
        for ep in self._endpoints:
            provider = self._create_provider(ep)
            if provider:
                self._providers[ep.name] = provider

    def _create_provider(self, config: EndpointConfig) -> LLMProvider | None:
        """根据配置创建 Provider"""
        try:
            if config.api_type == "anthropic":
                return AnthropicProvider(config)
            elif config.api_type == "openai":
                return OpenAIProvider(config)
            else:
                logger.warning(f"Unknown api_type '{config.api_type}' for endpoint '{config.name}'")
                return None
        except Exception as e:
            logger.error(f"Failed to create provider for '{config.name}': {e}")
            return None

    @property
    def endpoints(self) -> list[EndpointConfig]:
        """获取所有端点配置"""
        return self._endpoints

    @property
    def providers(self) -> dict[str, LLMProvider]:
        """获取所有 Provider"""
        return self._providers

    async def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[Tool] | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        enable_thinking: bool = False,
        conversation_id: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        统一聊天接口

        自动处理：
        1. 根据请求内容推断所需能力
        2. 筛选支持所需能力的端点
        3. 按优先级尝试调用
        4. 自动故障切换

        Args:
            messages: 消息列表
            system: 系统提示
            tools: 工具定义列表
            max_tokens: 最大输出 token
            temperature: 温度
            enable_thinking: 是否启用思考模式
            **kwargs: 额外参数

        Returns:
            统一响应格式

        Raises:
            UnsupportedMediaError: 视频内容但没有支持视频的端点
            AllEndpointsFailedError: 所有端点都失败
        """
        request = LLMRequest(
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_thinking=enable_thinking,
            extra_params=kwargs.get("extra_params"),
        )

        # 推断所需能力
        require_tools = bool(tools)
        require_vision = self._has_images(messages)
        require_video = self._has_videos(messages)
        require_thinking = bool(enable_thinking)

        # 检测工具上下文：有工具历史时禁止 failover
        # 原因：不同模型对工具调用格式可能不兼容，切换后可能无法正确处理
        has_tool_context = self._has_tool_context(messages)
        allow_failover = not has_tool_context

        if has_tool_context:
            logger.debug(
                "[LLM] Tool context detected in messages, failover disabled. "
                "Will retry same endpoint on failure."
            )

        # 筛选支持所需能力的端点
        eligible = self._filter_eligible_endpoints(
            require_tools=require_tools,
            require_vision=require_vision,
            require_video=require_video,
            require_thinking=require_thinking,
            conversation_id=conversation_id,
        )

        if eligible:
            return await self._try_endpoints(eligible, request, allow_failover=allow_failover)
        # eligible 为空时，可能原因包括：
        # - 配置里确实没有满足能力的端点
        # - 端点存在但都处于冷静期/不健康（被筛掉）
        providers_sorted = sorted(self._providers.values(), key=lambda p: p.config.priority)
        capability_matched = [
            p
            for p in providers_sorted
            if (not require_tools or p.config.has_capability("tools"))
            and (not require_vision or p.config.has_capability("vision"))
            and (not require_video or p.config.has_capability("video"))
            and (not require_thinking or p.config.has_capability("thinking"))
        ]

        if require_video and not capability_matched:
            # 视频能力是硬需求：如果配置里没有视频端点，明确报错
            raise UnsupportedMediaError(
                "No endpoint supports video. Configure a video-capable endpoint (e.g., kimi-k2.5)."
            )

        if capability_matched:
            # 有能力匹配的端点，但都不健康/冷静期，日志要避免误导
            logger.warning(
                "No healthy endpoint meets required capabilities: "
                f"tools={require_tools}, vision={require_vision}, video={require_video}, "
                f"thinking={require_thinking}. Trying capability-matched endpoints anyway."
            )
            return await self._try_endpoints(
                capability_matched, request, allow_failover=allow_failover
            )

        # 配置里确实没有能力匹配：警告后用首选端点尝试（尽量不中断）
        logger.warning(
            f"No endpoint supports required capabilities: "
            f"tools={require_tools}, vision={require_vision}, "
            f"video={require_video}, thinking={require_thinking}. Falling back to primary endpoint."
        )
        return await self._try_endpoints(providers_sorted, request, allow_failover=allow_failover)

    async def chat_stream(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[Tool] | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        **kwargs,
    ) -> AsyncIterator[dict]:
        """
        流式聊天接口

        Args:
            messages: 消息列表
            system: 系统提示
            tools: 工具定义列表
            max_tokens: 最大输出 token
            temperature: 温度
            **kwargs: 额外参数

        Yields:
            流式事件
        """
        request = LLMRequest(
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_params=kwargs.get("extra_params"),
        )

        # 推断所需能力
        require_tools = bool(tools)
        require_vision = self._has_images(messages)
        require_video = self._has_videos(messages)

        eligible = self._filter_eligible_endpoints(
            require_tools=require_tools,
            require_vision=require_vision,
            require_video=require_video,
        )

        if not eligible:
            providers_sorted = sorted(self._providers.values(), key=lambda p: p.config.priority)
            capability_matched = [
                p
                for p in providers_sorted
                if (not require_tools or p.config.has_capability("tools"))
                and (not require_vision or p.config.has_capability("vision"))
                and (not require_video or p.config.has_capability("video"))
            ]

            if require_video and not capability_matched:
                raise UnsupportedMediaError("No endpoint supports video")
            eligible = capability_matched if capability_matched else providers_sorted

        # 流式只尝试第一个端点
        provider = eligible[0]
        async for event in provider.chat_stream(request):
            yield event

    def _filter_eligible_endpoints(
        self,
        require_tools: bool = False,
        require_vision: bool = False,
        require_video: bool = False,
        require_thinking: bool = False,
        conversation_id: str | None = None,
    ) -> list[LLMProvider]:
        """筛选支持所需能力的端点

        注意：
        - enable_thinking=True 时，优先/要求端点具备 thinking 能力（避免能力/格式退化）
        - 如果有临时覆盖且覆盖端点支持所需能力，优先使用覆盖端点
        """
        # 清理过期的 override（conversation 优先）
        if conversation_id:
            ov = self._conversation_overrides.get(conversation_id)
            if ov and ov.is_expired:
                self._conversation_overrides.pop(conversation_id, None)
        if self._endpoint_override and self._endpoint_override.is_expired:
            logger.info("[LLM] Override expired, restoring default")
            self._endpoint_override = None

        eligible = []
        override_provider = None

        # 如果有临时覆盖，检查覆盖端点（conversation > global）
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
                    config = provider.config
                    # 检查能力是否满足
                    tools_ok = not require_tools or config.has_capability("tools")
                    vision_ok = not require_vision or config.has_capability("vision")
                    video_ok = not require_video or config.has_capability("video")
                    thinking_ok = (not require_thinking) or config.has_capability("thinking")

                    if tools_ok and vision_ok and video_ok and thinking_ok:
                        override_provider = provider
                        logger.debug(f"[LLM] Using override endpoint: {override_name}")
                    else:
                        logger.warning(
                            f"[LLM] Override endpoint {override_name} doesn't support "
                            f"required capabilities, falling back to default selection"
                        )

        for name, provider in self._providers.items():
            # 检查健康状态（包括冷静期）
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

            eligible.append(provider)

        # 按优先级排序
        eligible.sort(key=lambda p: p.config.priority)

        # 如果有有效的 override，将其放到最前面
        if override_provider and override_provider in eligible:
            eligible.remove(override_provider)
            eligible.insert(0, override_provider)

        return eligible

    async def _try_endpoints(
        self,
        providers: list[LLMProvider],
        request: LLMRequest,
        allow_failover: bool = True,
    ) -> LLMResponse:
        """尝试多个端点

        策略可配置：
        - retry_same_endpoint_first: True 时，即使有备选也先在当前端点重试
        - retry_count: 重试次数
        - retry_delay_seconds: 重试间隔

        Args:
            providers: 端点列表（按优先级排序）
            request: LLM 请求
            allow_failover: 是否允许切换到其他端点
                - True: 无工具上下文，可以安全切换（默认）
                - False: 有工具上下文，禁止切换，失败后直接抛异常让上层处理

        默认策略：有备选端点时快速切换，不重试同一个端点（提高响应速度）
        """
        errors = []
        retry_count = self._settings.get("retry_count", 2)
        retry_delay = self._settings.get("retry_delay_seconds", 2)
        retry_same_first = self._settings.get("retry_same_endpoint_first", False)

        # 有备选时默认快速切换（除非配置了先重试或禁止 failover）
        has_fallback = len(providers) > 1 and allow_failover
        if retry_same_first or not allow_failover:
            # 先重试当前端点（有工具上下文时强制此模式）
            max_attempts = retry_count + 1
        else:
            # 有备选时每个端点只尝试一次，无备选时重试多次
            max_attempts = 1 if has_fallback else (retry_count + 1)

        # 如果禁止 failover，只尝试第一个端点
        providers_to_try = providers if allow_failover else providers[:1]

        for i, provider in enumerate(providers_to_try):
            for attempt in range(max_attempts):
                try:
                    tools_count = len(request.tools) if request.tools else 0
                    logger.info(
                        f"[LLM] endpoint={provider.name} model={provider.model} "
                        f"action=request tools={tools_count}"
                    )

                    response = await provider.chat(request)

                    logger.info(
                        f"[LLM] endpoint={provider.name} model={provider.model} "
                        f"action=response tokens_out={response.usage.output_tokens}"
                    )

                    return response

                except AuthenticationError as e:
                    # 认证错误：设置冷静期，直接切换
                    logger.error(f"[LLM] endpoint={provider.name} auth_error={e}")
                    provider.mark_unhealthy(str(e))
                    errors.append(f"{provider.name}: {e}")
                    logger.warning(f"[LLM] endpoint={provider.name} cooldown=180s (auth error)")
                    break

                except LLMError as e:
                    logger.warning(f"[LLM] endpoint={provider.name} action=error error={e}")
                    errors.append(f"{provider.name}: {e}")

                    # 无备选时才重试（或禁止 failover 时）
                    if (not has_fallback or not allow_failover) and attempt < max_attempts - 1:
                        logger.info(
                            f"[LLM] endpoint={provider.name} retry={attempt + 1}/{max_attempts - 1}"
                            + (" (tool_context, no_failover)" if not allow_failover else "")
                        )
                        await asyncio.sleep(retry_delay)
                    else:
                        # 最后一次尝试也失败，设置冷静期
                        provider.mark_unhealthy(str(e))
                        logger.warning(f"[LLM] endpoint={provider.name} cooldown=180s")

                except Exception as e:
                    logger.error(f"[LLM] endpoint={provider.name} unexpected_error={e}")
                    provider.mark_unhealthy(str(e))
                    errors.append(f"{provider.name}: {e}")
                    logger.warning(
                        f"[LLM] endpoint={provider.name} cooldown=180s (unexpected error)"
                    )
                    break

            # 切换到下一个端点（如果允许且有下一个）
            if allow_failover and i < len(providers_to_try) - 1:
                next_provider = providers_to_try[i + 1]
                logger.warning(
                    f"[LLM] endpoint={provider.name} action=failover target={next_provider.name}"
                )

        # 如果禁止 failover，给出明确的日志
        if not allow_failover:
            logger.warning(
                "[LLM] Tool context detected, failover disabled. "
                "Let upper layer (Agent/TaskMonitor) handle retry/switch."
            )

        raise AllEndpointsFailedError(f"All endpoints failed: {'; '.join(errors)}")

    def _has_images(self, messages: list[Message]) -> bool:
        """检查消息中是否包含图片"""
        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ImageBlock):
                        return True
        return False

    def _has_videos(self, messages: list[Message]) -> bool:
        """检查消息中是否包含视频"""
        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, VideoBlock):
                        return True
        return False

    def _has_tool_context(self, messages: list[Message]) -> bool:
        """检查消息中是否包含工具调用上下文（tool_use 或 tool_result）

        用于判断是否允许 failover：
        - 无工具上下文：可以安全 failover 到其他端点
        - 有工具上下文：禁止 failover，因为不同模型对工具调用格式可能不兼容

        Returns:
            True 表示包含工具上下文，应禁止 failover
        """
        from .types import ToolResultBlock, ToolUseBlock

        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, (ToolUseBlock, ToolResultBlock)):
                        return True
                    # 兼容字典格式（某些转换后的消息可能是字典）
                    if isinstance(block, dict):
                        block_type = block.get("type", "")
                        if block_type in ("tool_use", "tool_result"):
                            return True
        return False

    async def health_check(self) -> dict[str, bool]:
        """
        检查所有端点健康状态

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
        """获取指定名称的 Provider"""
        return self._providers.get(name)

    def add_endpoint(self, config: EndpointConfig):
        """动态添加端点"""
        provider = self._create_provider(config)
        if provider:
            self._endpoints.append(config)
            self._endpoints.sort(key=lambda x: x.priority)
            self._providers[config.name] = provider

    def remove_endpoint(self, name: str):
        """动态移除端点"""
        if name in self._providers:
            del self._providers[name]
        self._endpoints = [ep for ep in self._endpoints if ep.name != name]

    # ==================== 动态模型切换 ====================

    def switch_model(
        self,
        endpoint_name: str,
        hours: float = DEFAULT_OVERRIDE_HOURS,
        reason: str = "",
        conversation_id: str | None = None,
    ) -> tuple[bool, str]:
        """
        临时切换到指定模型

        Args:
            endpoint_name: 端点名称
            hours: 有效时间（小时），默认 12 小时
            reason: 切换原因

        Returns:
            (成功, 消息)
        """
        # 检查端点是否存在
        if endpoint_name not in self._providers:
            available = list(self._providers.keys())
            return False, f"端点 '{endpoint_name}' 不存在。可用端点: {', '.join(available)}"

        # 检查端点是否健康
        provider = self._providers[endpoint_name]
        if not provider.is_healthy:
            cooldown = provider.cooldown_remaining
            return False, f"端点 '{endpoint_name}' 当前不可用（冷静期剩余 {cooldown:.0f} 秒）"

        # 创建覆盖配置
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

        return True, f"已切换到模型: {model}\n有效期至: {expires_str}"

    def restore_default(self, conversation_id: str | None = None) -> tuple[bool, str]:
        """
        恢复默认模型（清除临时覆盖）

        Returns:
            (成功, 消息)
        """
        if conversation_id:
            if conversation_id not in self._conversation_overrides:
                return False, "当前会话没有临时切换，已在使用默认模型"
            self._conversation_overrides.pop(conversation_id, None)
        else:
            if not self._endpoint_override:
                return False, "当前没有临时切换，已在使用默认模型"
            self._endpoint_override = None

        # 获取当前默认模型
        default = self.get_current_model()
        default_model = default.model if default else "未知"

        logger.info(f"[LLM] Restored to default model: {default_model}")
        return True, f"已恢复默认模型: {default_model}"

    def get_current_model(self) -> ModelInfo | None:
        """
        获取当前使用的模型信息

        Returns:
            当前模型信息，无可用模型时返回 None
        """
        # 检查并清理过期的 override
        if self._endpoint_override and self._endpoint_override.is_expired:
            logger.info("[LLM] Override expired, restoring default")
            self._endpoint_override = None

        # 如果有临时覆盖，返回覆盖的端点
        if self._endpoint_override:
            name = self._endpoint_override.endpoint_name
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

        # 否则返回优先级最高的健康端点
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

    def list_available_models(self) -> list[ModelInfo]:
        """
        列出所有可用模型

        Returns:
            模型信息列表（按优先级排序）
        """
        # 检查并清理过期的 override
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
                # 第一个健康的端点是当前默认
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
        获取当前覆盖状态

        Returns:
            覆盖状态信息，无覆盖时返回 None
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
        更新端点优先级顺序

        Args:
            priority_order: 端点名称列表，按优先级从高到低排序

        Returns:
            (成功, 消息)
        """
        # 验证所有端点都存在
        unknown = [name for name in priority_order if name not in self._providers]
        if unknown:
            return False, f"未知端点: {', '.join(unknown)}"

        # 更新优先级
        for i, name in enumerate(priority_order):
            for ep in self._endpoints:
                if ep.name == name:
                    ep.priority = i
                    break

        # 重新排序
        self._endpoints.sort(key=lambda x: x.priority)

        # 保存到配置文件
        if self._config_path and self._config_path.exists():
            try:
                self._save_config()
                logger.info(f"[LLM] Priority updated and saved: {priority_order}")
                return True, f"优先级已更新并保存: {' > '.join(priority_order)}"
            except Exception as e:
                logger.error(f"[LLM] Failed to save config: {e}")
                return True, f"优先级已更新（内存），但保存配置文件失败: {e}"

        return True, f"优先级已更新: {' > '.join(priority_order)}"

    def _save_config(self):
        """保存配置到文件"""
        if not self._config_path:
            return

        # 读取原配置
        with open(self._config_path, encoding="utf-8") as f:
            config_data = json.load(f)

        # 更新端点优先级
        name_to_priority = {ep.name: ep.priority for ep in self._endpoints}
        for ep_data in config_data.get("endpoints", []):
            name = ep_data.get("name")
            if name in name_to_priority:
                ep_data["priority"] = name_to_priority[name]

        # 写回文件
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)

    async def close(self):
        """关闭所有 Provider"""
        for provider in self._providers.values():
            if hasattr(provider, "close"):
                await provider.close()


# 全局单例
_default_client: LLMClient | None = None


def get_default_client() -> LLMClient:
    """获取默认客户端实例"""
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


def set_default_client(client: LLMClient):
    """设置默认客户端实例"""
    global _default_client
    _default_client = client


async def chat(
    messages: list[Message],
    system: str = "",
    tools: list[Tool] | None = None,
    **kwargs,
) -> LLMResponse:
    """便捷函数：使用默认客户端聊天"""
    client = get_default_client()
    return await client.chat(messages, system=system, tools=tools, **kwargs)

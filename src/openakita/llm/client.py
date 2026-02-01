"""
LLM 统一客户端

提供统一的 LLM 调用接口，支持：
- 多端点配置
- 自动故障切换
- 能力分流（根据请求自动选择合适的端点）
- 健康检查
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Union, AsyncIterator

from .types import (
    LLMRequest,
    LLMResponse,
    EndpointConfig,
    Message,
    Tool,
    ContentBlock,
    TextBlock,
    ImageBlock,
    VideoBlock,
    LLMError,
    UnsupportedMediaError,
    AllEndpointsFailedError,
    AuthenticationError,
)
from .config import load_endpoints_config, get_default_config_path
from .providers.base import LLMProvider
from .providers.anthropic import AnthropicProvider
from .providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)


class LLMClient:
    """统一 LLM 客户端"""
    
    def __init__(
        self,
        config_path: Optional[Path] = None,
        endpoints: Optional[list[EndpointConfig]] = None,
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
        
        if endpoints:
            self._endpoints = sorted(endpoints, key=lambda x: x.priority)
        elif config_path or get_default_config_path().exists():
            self._endpoints, self._settings = load_endpoints_config(config_path)
        
        # 创建 Provider 实例
        self._init_providers()
    
    def _init_providers(self):
        """初始化所有 Provider"""
        for ep in self._endpoints:
            provider = self._create_provider(ep)
            if provider:
                self._providers[ep.name] = provider
    
    def _create_provider(self, config: EndpointConfig) -> Optional[LLMProvider]:
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
        tools: Optional[list[Tool]] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        enable_thinking: bool = False,
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
        
        # 推断所需能力（thinking 只是传输参数，不作为筛选标准）
        require_tools = bool(tools)
        require_vision = self._has_images(messages)
        require_video = self._has_videos(messages)
        
        # 筛选支持所需能力的端点
        eligible = self._filter_eligible_endpoints(
            require_tools=require_tools,
            require_vision=require_vision,
            require_video=require_video,
        )
        
        if eligible:
            return await self._try_endpoints(eligible, request)
        elif require_video:
            # 视频能力是硬需求，无法降级
            raise UnsupportedMediaError(
                "No endpoint supports video. Configure a video-capable endpoint (e.g., kimi-k2.5)."
            )
        else:
            # 其他能力无匹配：警告后用首选端点尝试
            logger.warning(
                f"No endpoint supports required capabilities: "
                f"tools={require_tools}, vision={require_vision}, "
                f"video={require_video}. Falling back to primary endpoint."
            )
            return await self._try_endpoints(list(self._providers.values()), request)
    
    async def chat_stream(
        self,
        messages: list[Message],
        system: str = "",
        tools: Optional[list[Tool]] = None,
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
            if require_video:
                raise UnsupportedMediaError("No endpoint supports video")
            eligible = list(self._providers.values())
        
        # 流式只尝试第一个端点
        provider = eligible[0]
        async for event in provider.chat_stream(request):
            yield event
    
    def _filter_eligible_endpoints(
        self,
        require_tools: bool = False,
        require_vision: bool = False,
        require_video: bool = False,
    ) -> list[LLMProvider]:
        """筛选支持所需能力的端点
        
        注意：thinking 不作为筛选标准，只是传输参数
        """
        eligible = []
        
        for name, provider in self._providers.items():
            # 检查健康状态（包括冷静期）
            if not provider.is_healthy:
                cooldown = provider.cooldown_remaining
                if cooldown > 0:
                    logger.debug(
                        f"[LLM] endpoint={name} skipped (cooldown: {cooldown}s remaining)"
                    )
                continue
            
            config = provider.config
            
            if require_tools and not config.has_capability("tools"):
                continue
            if require_vision and not config.has_capability("vision"):
                continue
            if require_video and not config.has_capability("video"):
                continue
            
            eligible.append(provider)
        
        # 按优先级排序
        eligible.sort(key=lambda p: p.config.priority)
        
        return eligible
    
    async def _try_endpoints(
        self,
        providers: list[LLMProvider],
        request: LLMRequest,
    ) -> LLMResponse:
        """尝试多个端点
        
        策略：有备选端点时，一次失败就切换，不重试同一个端点
        只有单一端点时才重试多次
        """
        errors = []
        retry_count = self._settings.get("retry_count", 2)
        retry_delay = self._settings.get("retry_delay_seconds", 2)
        
        # 有备选时每个端点只尝试一次，无备选时重试多次
        has_fallback = len(providers) > 1
        max_attempts = 1 if has_fallback else (retry_count + 1)
        
        for i, provider in enumerate(providers):
            for attempt in range(max_attempts):
                try:
                    logger.info(
                        f"[LLM] endpoint={provider.name} model={provider.model} "
                        f"action=request"
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
                    logger.warning(
                        f"[LLM] endpoint={provider.name} action=error error={e}"
                    )
                    errors.append(f"{provider.name}: {e}")
                    
                    # 无备选时才重试
                    if not has_fallback and attempt < max_attempts - 1:
                        await asyncio.sleep(retry_delay)
                    else:
                        # 最后一次尝试也失败，设置冷静期
                        provider.mark_unhealthy(str(e))
                        logger.warning(f"[LLM] endpoint={provider.name} cooldown=180s")
                    
                except Exception as e:
                    logger.error(f"[LLM] endpoint={provider.name} unexpected_error={e}")
                    provider.mark_unhealthy(str(e))
                    errors.append(f"{provider.name}: {e}")
                    logger.warning(f"[LLM] endpoint={provider.name} cooldown=180s (unexpected error)")
                    break
            
            # 切换到下一个端点
            if i < len(providers) - 1:
                next_provider = providers[i + 1]
                logger.warning(
                    f"[LLM] endpoint={provider.name} action=failover "
                    f"target={next_provider.name}"
                )
        
        raise AllEndpointsFailedError(
            f"All endpoints failed: {'; '.join(errors)}"
        )
    
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
    
    async def health_check(self) -> dict[str, bool]:
        """
        检查所有端点健康状态
        
        Returns:
            {endpoint_name: is_healthy}
        """
        results = {}
        
        tasks = [
            (name, provider.health_check())
            for name, provider in self._providers.items()
        ]
        
        for name, task in tasks:
            try:
                results[name] = await task
            except Exception as e:
                logger.error(f"Health check failed for {name}: {e}")
                results[name] = False
        
        return results
    
    def get_provider(self, name: str) -> Optional[LLMProvider]:
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
    
    async def close(self):
        """关闭所有 Provider"""
        for provider in self._providers.values():
            if hasattr(provider, "close"):
                await provider.close()


# 全局单例
_default_client: Optional[LLMClient] = None


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
    tools: Optional[list[Tool]] = None,
    **kwargs,
) -> LLMResponse:
    """便捷函数：使用默认客户端聊天"""
    client = get_default_client()
    return await client.chat(messages, system=system, tools=tools, **kwargs)

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
    AudioBlock,
    AuthenticationError,
    DocumentBlock,
    EndpointConfig,
    ImageBlock,
    LLMError,
    LLMRequest,
    LLMResponse,
    Message,
    Tool,
    VideoBlock,
)

logger = logging.getLogger(__name__)


def _friendly_error_hint(failed_providers: list | None = None, last_error: str = "") -> str:
    """根据失败端点的错误分类生成用户友好的提示信息。

    返回一段面向用户的中文提示，帮助用户理解问题并采取行动。
    """
    hints: list[str] = []
    categories: set[str] = set()

    if failed_providers:
        for p in failed_providers:
            cat = getattr(p, "error_category", "")
            if cat:
                categories.add(cat)

    # 根据错误类型给出具体建议
    if "quota" in categories:
        hints.append("💳 检测到 API 配额耗尽，请前往对应平台充值或升级套餐，充值后会自动恢复。")
    if "auth" in categories:
        hints.append("🔑 检测到 API 认证失败，请检查 API Key 是否正确、是否过期。")
    if "transient" in categories:
        hints.append("🌐 检测到网络超时/连接失败，请检查网络连接和代理设置。")
    if "structural" in categories:
        hints.append("⚙️ 检测到请求格式错误，这通常是模型兼容性问题，请尝试切换其他模型。")

    if not hints:
        # 无法分类时的通用提示
        hints.append("请检查 API Key、网络连接和账户余额。")

    return " ".join(hints)


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

        # 端点亲和性：记录上一次成功的端点名称
        # 有工具上下文时，优先使用上次成功的端点（避免 failover 后又回到高优先级的故障端点）
        self._last_success_endpoint: str | None = None

        if endpoints:
            self._endpoints = sorted(endpoints, key=lambda x: x.priority)
        elif config_path or get_default_config_path().exists():
            self._config_path = config_path or get_default_config_path()
            self._endpoints, _, _, self._settings = load_endpoints_config(config_path)

        # 创建 Provider 实例
        self._init_providers()

    def reload(self) -> bool:
        """热重载：重新读取配置文件并重建所有 Provider。

        Returns:
            True 表示成功重载，False 表示配置文件不可用。
        """
        if not self._config_path or not self._config_path.exists():
            logger.warning("reload() called but no config_path available")
            return False
        try:
            # Re-read .env so newly written API keys are available in os.environ
            from dotenv import load_dotenv as _reload_dotenv

            env_path = self._config_path.parent.parent / ".env"
            if env_path.exists():
                _reload_dotenv(env_path, override=True)

            new_endpoints, _, _, new_settings = load_endpoints_config(self._config_path)
            self._endpoints = new_endpoints
            self._settings = new_settings
            self._providers.clear()
            self._init_providers()
            self._last_success_endpoint = None  # 重载后重置端点亲和性
            logger.info(
                f"LLMClient reloaded from {self._config_path}: "
                f"{len(self._endpoints)} endpoints, {len(self._providers)} providers"
            )
            return True
        except Exception as e:
            logger.error(f"LLMClient reload failed: {e}", exc_info=True)
            return False

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
        max_tokens: int = 0,
        temperature: float = 1.0,
        enable_thinking: bool = False,
        thinking_depth: str | None = None,
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
            thinking_depth: 思考深度 ('low'/'medium'/'high')
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
            thinking_depth=thinking_depth,
            extra_params=kwargs.get("extra_params"),
        )

        # 推断所需能力
        require_tools = bool(tools)
        require_vision = self._has_images(messages)
        require_video = self._has_videos(messages)
        require_audio = self._has_audio(messages)
        require_pdf = self._has_documents(messages)
        require_thinking = bool(enable_thinking)

        # 检测工具上下文：对 failover 需要更保守
        #
        # 关键原因：
        # - 工具链的“连续性”不仅是消息格式兼容（OpenAI-compatible / Anthropic）
        # - 还包含模型特定的思维链/元数据连续性（例如 MiniMax M2.1 的 interleaved thinking）
        #   这类信息若未完整保留/回传，或中途切换到另一模型，工具调用质量会明显下降
        #
        # 因此默认：只要检测到工具上下文，就禁用 failover（保持同一端点/同一模型）
        # 但允许通过配置显式开启“同协议内 failover”（默认不开启）。
        has_tool_context = self._has_tool_context(messages)
        allow_failover = not has_tool_context

        if has_tool_context:
            logger.debug(
                "[LLM] Tool context detected in messages; failover disabled by default "
                "(set settings.allow_failover_with_tool_context=true to override)."
            )

        # 筛选支持所需能力的端点
        # 有工具上下文时传入端点亲和性：优先使用上次成功的端点
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

        # 可选：工具上下文下启用 failover（显式配置才开启）
        if has_tool_context and eligible:
            if self._settings.get("allow_failover_with_tool_context", False):
                # 默认只允许同协议内切换；避免 anthropic/openai 混用导致 tool message 不兼容
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
            return await self._try_endpoints(eligible, request, allow_failover=allow_failover)

        # eligible 为空 — 使用公共降级策略
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
        )
        return await self._try_endpoints(providers, request, allow_failover=allow_failover)

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
        **kwargs,
    ) -> AsyncIterator[dict]:
        """
        流式聊天接口（带完整降级策略）

        与 chat() 共用降级逻辑：thinking 软降级、冷静期等待、多端点轮询。
        流式特殊处理：一旦开始产出事件（yielded=True），中途失败不再切换端点
        （避免向客户端发送混合的部分响应）。

        Args:
            messages: 消息列表
            system: 系统提示
            tools: 工具定义列表
            max_tokens: 最大输出 token
            temperature: 温度
            enable_thinking: 是否启用思考模式
            thinking_depth: 思考深度 ('low'/'medium'/'high')
            conversation_id: 对话 ID
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
            enable_thinking=enable_thinking,
            thinking_depth=thinking_depth,
            extra_params=kwargs.get("extra_params"),
        )

        # 推断所需能力
        require_tools = bool(tools)
        require_vision = self._has_images(messages)
        require_video = self._has_videos(messages)
        require_audio = self._has_audio(messages)
        require_pdf = self._has_documents(messages)
        require_thinking = bool(enable_thinking)

        # 使用公共降级策略解析端点列表
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
            )

        # 多端点轮询：依次尝试每个端点
        # 流式特殊处理：一旦有事件产出就不再切换（避免混合响应）
        last_error: Exception | None = None
        for i, provider in enumerate(eligible):
            yielded = False
            try:
                logger.info(
                    f"[LLM-Stream] endpoint={provider.name} model={provider.model} "
                    f"action=stream_request"
                )
                async for event in provider.chat_stream(request):
                    yielded = True
                    yield event
                # 流完成：provider 内部已调用 mark_healthy()
                self._last_success_endpoint = provider.name
                return
            except LLMError as e:
                last_error = e
                if yielded:
                    # 已产出部分事件，不能切换端点（客户端会收到混合响应）
                    logger.error(
                        f"[LLM-Stream] endpoint={provider.name} mid-stream failure: {e}. "
                        f"Cannot failover (partial response already sent)."
                    )
                    raise
                # 未产出任何事件 → 安全切换到下一个端点
                # provider 内部已调用 mark_unhealthy()
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

    # ==================== 公共降级策略 ====================

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
    ) -> list[LLMProvider]:
        """公共分层降级策略 — 供 chat() 和 chat_stream() 复用

        当 _filter_eligible_endpoints() 返回空列表时调用此方法，
        按以下顺序逐级降级，直到找到可用端点：

        1. thinking 软降级：放弃 thinking 要求，用非 thinking 端点
        2. 等待冷静期恢复：等最短的瞬时冷静期（最多等 35s）
        3. 强制重试：忽略冷静期，强制调用匹配基础能力的端点
        4. 最终兜底：所有端点都试一遍

        副作用：
            - 可能修改 request.enable_thinking = False（thinking 降级时）

        Raises:
            UnsupportedMediaError: 需要视频但无视频能力端点
            AllEndpointsFailedError: 所有端点均为结构性错误

        Returns:
            按优先级排序的端点列表（至少包含一个端点）
        """
        providers_sorted = sorted(self._providers.values(), key=lambda p: p.config.priority)

        # ── 降级 1: thinking 软降级 ──
        # thinking 不同于 tools/vision/video：没有它请求仍能正常工作
        # 如果因为 thinking 要求导致无可用端点，降级到无 thinking 模式
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

        # ── 降级 2+3+4: 所有端点都在冷静期 ──
        # 构建基础能力匹配列表（不含 thinking 要求，忽略健康状态）
        base_capability_matched = [
            p
            for p in providers_sorted
            if (not require_tools or p.config.has_capability("tools"))
            and (not require_vision or p.config.has_capability("vision"))
            and (not require_video or p.config.has_capability("video"))
            and (not require_audio or p.config.has_capability("audio"))
            and (not require_pdf or p.config.has_capability("pdf"))
        ]

        # 多模态软降级: 视频/音频/PDF 端点不匹配时不硬失败
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

        # 如果降级了 thinking，更新 request
        if require_thinking:
            request.enable_thinking = False
            logger.info("[LLM] All endpoints in cooldown. Disabling thinking for fallback attempt.")

        if base_capability_matched:
            unhealthy = [p for p in base_capability_matched if not p.is_healthy]
            unhealthy_count = len(unhealthy)

            if unhealthy_count > 0:
                # 按错误类型分组
                structural = [p for p in unhealthy if p.error_category == "structural"]
                quota_or_auth = [
                    p for p in unhealthy
                    if p.error_category in ("quota", "auth")
                ]
                non_structural = [p for p in unhealthy if p.error_category != "structural"]

                # ── 降级 2: 等待瞬时冷静期恢复 ──
                transient_like = [
                    p for p in non_structural
                    if p.error_category not in ("quota", "auth")
                ]
                if transient_like:
                    min_transient_cd = min(p.cooldown_remaining for p in transient_like)
                    if 0 < min_transient_cd <= 35:
                        logger.info(
                            f"[LLM] All endpoints in cooldown. "
                            f"Waiting {min_transient_cd}s for transient recovery..."
                        )
                        await asyncio.sleep(min(min_transient_cd + 1, 35))
                        # 等待后重新筛选
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

                # ── 全部是结构性错误（400 参数错误等），重试无意义 → 报错 ──
                if structural and len(structural) == unhealthy_count:
                    last_err = structural[0]._last_error or "unknown structural error"
                    min_cd = min(p.cooldown_remaining for p in structural)
                    hint = _friendly_error_hint(structural)
                    raise AllEndpointsFailedError(
                        f"All endpoints failed with structural errors "
                        f"(cooldown {min_cd}s). {hint} Last error: {last_err}",
                        is_structural=True,
                    )

                # ── 全部是配额/认证错误，重试无意义 → 快速报错 ──
                if quota_or_auth and len(quota_or_auth) == unhealthy_count:
                    last_err = quota_or_auth[0]._last_error or "unknown auth/quota error"
                    categories = sorted({p.error_category for p in quota_or_auth})
                    hint = _friendly_error_hint(quota_or_auth)
                    raise AllEndpointsFailedError(
                        f"All endpoints failed with {'/'.join(categories)} errors. "
                        f"{hint} Last error: {last_err}"
                    )

            # ── 降级 3: "最后防线旁路" — 绕过冷静期（对齐 Portkey） ──
            # Portkey 核心规则：当没有健康目标时，绕过 circuit breaker 尝试所有目标
            # 排除 quota/auth 错误的端点（这类错误重试无意义）
            retryable = [
                p for p in base_capability_matched
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

            # 如果所有端点都是 quota/auth，仍然返回它们（让 _try_endpoints 决定最终错误）
            logger.warning(
                f"[LLM] All {len(base_capability_matched)} endpoints have "
                f"non-retryable errors. Returning for final error handling."
            )
            return base_capability_matched

        # ── 降级 4: 最终兜底 — 尝试所有端点 ──
        logger.warning(
            f"[LLM] No endpoint matches required capabilities "
            f"(tools={require_tools}, vision={require_vision}, video={require_video}). "
            f"Trying all {len(providers_sorted)} endpoints as last resort."
        )
        return providers_sorted

    # ==================== 端点筛选 ====================

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
        """筛选支持所需能力的端点

        注意：
        - enable_thinking=True 时，优先/要求端点具备 thinking 能力（避免能力/格式退化）
        - 如果有临时覆盖且覆盖端点支持所需能力，优先使用覆盖端点
        - prefer_endpoint: 端点亲和性，有工具上下文时传入上次成功的端点名称，
          将其提升到队列前端（优先于 priority 排序，但低于 override）
        """
        # 清理过期的 override
        # 1) 清理当前 conversation 的过期 override
        if conversation_id:
            ov = self._conversation_overrides.get(conversation_id)
            if ov and ov.is_expired:
                self._conversation_overrides.pop(conversation_id, None)
        # 2) 清理全局 override
        if self._endpoint_override and self._endpoint_override.is_expired:
            logger.info("[LLM] Override expired, restoring default")
            self._endpoint_override = None
        # 3) 定期清理所有过期的 conversation overrides（防止内存泄漏）
        #    仅当积累超过阈值时触发，避免每次调用都遍历
        if len(self._conversation_overrides) > 50:
            expired_keys = [
                k for k, v in self._conversation_overrides.items() if v.is_expired
            ]
            for k in expired_keys:
                self._conversation_overrides.pop(k, None)
            if expired_keys:
                logger.debug(
                    f"[LLM] Cleaned {len(expired_keys)} expired conversation overrides"
                )

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
                    audio_ok = not require_audio or config.has_capability("audio")
                    pdf_ok = not require_pdf or config.has_capability("pdf")

                    if tools_ok and vision_ok and video_ok and thinking_ok and audio_ok and pdf_ok:
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
            if require_audio and not config.has_capability("audio"):
                continue
            if require_pdf and not config.has_capability("pdf"):
                continue

            eligible.append(provider)

        # 按优先级排序
        eligible.sort(key=lambda p: p.config.priority)

        # 端点亲和性：有工具上下文时，将上次成功的端点提升到队列前端
        # 这样 failover 后的下一次调用会继续使用成功的端点，而不是回到高优先级的故障端点
        if prefer_endpoint:
            prefer_provider = next(
                (p for p in eligible if p.name == prefer_endpoint), None
            )
            if prefer_provider:
                eligible.remove(prefer_provider)
                eligible.insert(0, prefer_provider)
                logger.debug(
                    f"[LLM] Endpoint affinity: prefer {prefer_endpoint} "
                    f"(last successful endpoint with tool context)"
                )

        # 如果有有效的 override，将其放到最前面（override 优先于亲和性）
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
            allow_failover: 控制端点切换策略
                - True: 无工具上下文，快速切换（每个端点只试 1 次）
                - False: 有工具上下文，先重试当前端点多次再切到下一个

        默认策略：有备选端点时快速切换，不重试同一个端点（提高响应速度）
        工具上下文：每个端点重试 retry_count 次后才切到下一个（保持连续性）
        所有端点都按优先级依次尝试，无论 allow_failover 值
        """
        from .providers.base import COOLDOWN_GLOBAL_FAILURE

        errors = []
        failed_providers: list[LLMProvider] = []  # 跟踪本次调用中失败的端点
        for p in providers:
            p._content_error = False
        retry_count = self._settings.get("retry_count", 2)
        retry_delay = self._settings.get("retry_delay_seconds", 2)
        retry_same_first = self._settings.get("retry_same_endpoint_first", False)

        # 有备选时默认快速切换（除非配置了先重试或禁止 failover）
        has_fallback = len(providers) > 1
        if retry_same_first or not allow_failover:
            # 先重试当前端点（有工具上下文时强制此模式：多次重试后再切换）
            max_attempts = retry_count + 1
        else:
            # 有备选时每个端点只尝试一次，无备选时重试多次
            max_attempts = 1 if (has_fallback and allow_failover) else (retry_count + 1)

        # 始终尝试所有端点（工具上下文时每个端点多次重试后再切到下一个）
        providers_to_try = providers

        for i, provider in enumerate(providers_to_try):
            for attempt in range(max_attempts):
                try:
                    tools_count = len(request.tools) if request.tools else 0
                    logger.info(
                        f"[LLM] endpoint={provider.name} model={provider.model} "
                        f"action=request tools={tools_count}"
                    )

                    response = await provider.chat(request)

                    # 成功：重置连续失败计数
                    provider.record_success()

                    logger.info(
                        f"[LLM] endpoint={provider.name} model={provider.model} "
                        f"action=response tokens_in={response.usage.input_tokens} tokens_out={response.usage.output_tokens}"
                    )

                    # 注意：这里不缩短其他失败端点的冷静期。
                    # A 失败、B 成功 ≠ 全局网络波动；A 的远程服务可能仍然有问题。
                    # 全局网络波动判定见本方法末尾：所有端点都失败时才触发。

                    # 端点亲和性：记录本次成功的端点，供后续有工具上下文的调用优先使用
                    self._last_success_endpoint = provider.name

                    return response

                except AuthenticationError as e:
                    # 认证/配额错误：长冷静期，直接切换（不重试当前端点）
                    error_str = str(e)
                    # 区分配额耗尽和真正的认证错误
                    from .providers.base import LLMProvider as _BaseProvider
                    error_cat = _BaseProvider._classify_error(error_str)
                    if error_cat == "quota":
                        logger.error(f"[LLM] endpoint={provider.name} quota_exhausted={e}")
                        provider.mark_unhealthy(error_str, category="quota")
                    else:
                        logger.error(f"[LLM] endpoint={provider.name} auth_error={e}")
                        provider.mark_unhealthy(error_str, category="auth")
                    errors.append(f"{provider.name}: {e}")
                    failed_providers.append(provider)
                    logger.warning(
                        f"[LLM] endpoint={provider.name} cooldown={provider.cooldown_remaining}s "
                        f"(category={provider.error_category})"
                    )
                    break

                except LLMError as e:
                    error_str = str(e)
                    logger.warning(f"[LLM] endpoint={provider.name} action=error error={e}")
                    errors.append(f"{provider.name}: {e}")

                    # 自动分类错误
                    from .providers.base import LLMProvider as _BaseProvider
                    auto_category = _BaseProvider._classify_error(error_str)

                    # 配额耗尽：不可恢复，立即跳过此端点（与 auth 同等处理）
                    if auto_category == "quota":
                        logger.error(
                            f"[LLM] endpoint={provider.name} quota exhausted detected in LLMError, "
                            f"skipping remaining retries. Error: {error_str[:200]}"
                        )
                        provider.mark_unhealthy(error_str, category="quota")
                        failed_providers.append(provider)
                        break

                    # ── 自愈: reasoning_content / thinking 兼容性错误 ──
                    # 当 enable_thinking=False 的请求发送到 thinking-only 端点时，
                    # API 会返回 reasoning_content 缺失错误。
                    # 无需维护 thinking-only 模型列表，运行时检测并自动修正。
                    _reasoning_err_patterns = [
                        "reasoning_content is missing",
                        "missing reasoning_content",
                        "missing `reasoning_content`",
                        "missing 'reasoning_content'",
                        "thinking is enabled but reasoning_content is missing",
                    ]
                    _is_reasoning_err = any(
                        p in error_str.lower() for p in _reasoning_err_patterns
                    )
                    if _is_reasoning_err and not getattr(request, '_reasoning_healed', False):
                        request._reasoning_healed = True  # type: ignore[attr-defined]
                        request.enable_thinking = True
                        logger.info(
                            f"[LLM] endpoint={provider.name} reasoning_content error detected, "
                            f"self-healing: enable_thinking=True, retrying"
                        )
                        await asyncio.sleep(0.5)
                        continue  # 用修正后的参数重试当前端点

                    # 检测不可重试的结构性错误（重试不会修复，浪费配额）
                    non_retryable_patterns = [
                        "invalid_request_error",
                        "invalid_parameter",
                        "messages with role",
                        "must be a response to a preceeding message",
                        "does not support",  # Ollama: "model does not support thinking" 等
                        "not supported",     # 通用的"不支持"格式
                        "reasoning_content is missing",  # 自愈失败后仍作为结构性错误
                        "missing reasoning_content",
                        "missing 'reasoning_content'",
                    ]
                    is_non_retryable = any(
                        pattern in error_str.lower() for pattern in non_retryable_patterns
                    )

                    if is_non_retryable:
                        # 区分内容级错误 vs 端点级错误：
                        # 内容级错误（请求 payload 有问题）不应给端点加冷却，
                        # 否则会殃及其他正常会话。
                        _content_error_patterns = [
                            "exceeded limit",
                            "max bytes",
                            "payload too large",
                            "request entity too large",
                            "content too large",
                            "maximum context length",
                            "too many tokens",
                            "string too long",
                        ]
                        _is_content_error = any(
                            p in error_str.lower() for p in _content_error_patterns
                        )

                        if _is_content_error:
                            logger.error(
                                f"[LLM] endpoint={provider.name} content-level error detected "
                                f"(NOT cooling down endpoint): {error_str[:200]}"
                            )
                            errors.append(f"{provider.name}: {error_str}")
                            failed_providers.append(provider)
                            provider._content_error = True
                            break

                        logger.error(
                            f"[LLM] endpoint={provider.name} non-retryable structural error detected, "
                            f"skipping remaining retries. Error: {error_str[:200]}"
                        )
                        provider.mark_unhealthy(error_str, category="structural")
                        failed_providers.append(provider)
                        break

                    # 重试当前端点：
                    # - 工具上下文/retry_same_first 时每个端点重试多次再切
                    # - 无备选端点时也重试多次
                    should_retry = attempt < max_attempts - 1
                    if should_retry:
                        logger.info(
                            f"[LLM] endpoint={provider.name} retry={attempt + 1}/{max_attempts - 1}"
                            + (" (tool_context)" if not allow_failover else "")
                        )
                        await asyncio.sleep(retry_delay)
                    else:
                        # 当前端点重试全部失败，设置冷静期后切到下一个端点
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
                    break

            # 切换到下一个端点
            if i < len(providers_to_try) - 1:
                next_provider = providers_to_try[i + 1]
                logger.warning(
                    f"[LLM] endpoint={provider.name} action=failover target={next_provider.name}"
                    + (" (tool_context, retried same endpoint first)" if not allow_failover else "")
                )

        # ── 全局故障检测 ──
        # 所有端点在同一次请求中连续失败，且主要是瞬时错误（超时/连接）
        # → 很可能是主机网络波动，而非端点本身异常
        # → 缩短冷静期，让系统尽快恢复
        if len(failed_providers) >= 2:
            transient_count = sum(
                1 for fp in failed_providers if fp.error_category == "transient"
            )
            if transient_count >= len(failed_providers) * 0.5:
                # 多数为瞬时错误 → 判定为全局网络故障
                logger.warning(
                    f"[LLM] Global failure detected: {len(failed_providers)} endpoints failed "
                    f"({transient_count} transient). Likely network issue on host. "
                    f"Shortening cooldowns to {COOLDOWN_GLOBAL_FAILURE}s for quick recovery."
                )
                for fp in failed_providers:
                    if fp.error_category == "transient":
                        fp.shorten_cooldown(COOLDOWN_GLOBAL_FAILURE)

        # 工具上下文下所有端点都失败
        if not allow_failover:
            logger.warning(
                "[LLM] Tool context detected. All endpoints exhausted (each retried before failover). "
                "Upper layer (Agent/TaskMonitor) may restart with a different strategy."
            )

        hint = _friendly_error_hint(failed_providers)
        has_content_error = any(getattr(fp, '_content_error', False) for fp in failed_providers)
        all_structural = has_content_error or all(
            fp.error_category == "structural" for fp in failed_providers
        )
        raise AllEndpointsFailedError(
            f"All endpoints failed: {'; '.join(errors)}\n{hint}",
            is_structural=all_structural,
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

    def _has_audio(self, messages: list[Message]) -> bool:
        """检查消息中是否包含音频"""
        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, AudioBlock):
                        return True
        return False

    def _has_documents(self, messages: list[Message]) -> bool:
        """检查消息中是否包含文档（PDF 等）"""
        for msg in messages:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, DocumentBlock):
                        return True
        return False

    def has_any_endpoint_with_capability(self, capability: str) -> bool:
        """检查是否有任何端点支持指定能力（供 Agent 查询）"""
        return any(p.config.has_capability(capability) for p in self._providers.values())

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

    def reset_all_cooldowns(self, *, include_structural: bool = False, force_all: bool = False):
        """重置端点冷静期

        Args:
            include_structural: 同时重置结构性错误的冷静期。
            force_all: 无条件重置所有端点冷静期（用户主动重试时使用）。
        """
        reset_count = 0
        for name, provider in self._providers.items():
            if not provider.is_healthy:
                cat = provider.error_category
                if force_all or cat == "transient" or (include_structural and cat == "structural"):
                    provider.reset_cooldown()
                    reset_count += 1
                    logger.info(f"[LLM] endpoint={name} cooldown reset (category={cat}, force_all={force_all})")
        if reset_count:
            logger.info(f"[LLM] Reset cooldowns for {reset_count} endpoints")
        return reset_count

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

    def get_next_endpoint(self, conversation_id: str | None = None) -> str | None:
        """
        获取下一优先级的健康端点名称（用于 fallback）

        逻辑：找到当前生效端点，按 priority 排序后返回它之后的第一个健康端点。
        如果当前端点已是最低优先级或无可用端点，返回 None。

        Args:
            conversation_id: 可选的会话 ID（用于识别 per-conversation override）

        Returns:
            下一个端点名称，或 None
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

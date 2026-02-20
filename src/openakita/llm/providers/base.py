"""
LLM Provider 基类

定义所有 Provider 必须实现的接口。
"""

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from ..types import EndpointConfig, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

# 冷静期时长（秒）- 按错误类型区分
# 设计原则：冷却只防止秒级连续轰炸，不阻塞其他会话；
# 重试上限由上层（TaskMonitor / ReasoningEngine）控制，
# 超过 3 次即终止并告知用户，用户重新发送即可重试。
COOLDOWN_AUTH = 60         # 认证错误: 1 分钟（需要人工干预，但不宜锁太久）
COOLDOWN_QUOTA = 20        # 配额耗尽: 20 秒
COOLDOWN_STRUCTURAL = 10   # 结构性错误: 10 秒（上层会快速识别处理）
COOLDOWN_TRANSIENT = 5     # 瞬时错误: 5 秒（超时/连接失败，很可能快速恢复）
COOLDOWN_DEFAULT = 30      # 默认: 30 秒
COOLDOWN_GLOBAL_FAILURE = 3  # 全局故障（所有端点同时失败）: 3 秒

# 渐进式冷静期退避 —— 连续失败时按次数递增，上限 1 分钟
COOLDOWN_ESCALATION_STEPS = [5, 10, 20, 60]  # 5s -> 10s -> 20s -> 60s(上限)

# 向后兼容（旧代码引用）
COOLDOWN_EXTENDED = COOLDOWN_ESCALATION_STEPS[-1]  # 300s，旧的 3600 已废弃
CONSECUTIVE_FAILURE_THRESHOLD = 3  # 保留常量以向后兼容，但不再触发 1h 冷静期
COOLDOWN_SECONDS = COOLDOWN_DEFAULT


class LLMProvider(ABC):
    """LLM Provider 基类"""

    def __init__(self, config: EndpointConfig):
        self.config = config
        self._healthy = True
        self._last_error: str | None = None
        self._cooldown_until: float = 0  # 冷静期结束时间戳
        self._error_category: str = ""   # 错误分类
        self._consecutive_cooldowns: int = 0  # 连续进入冷静期次数（无成功请求间隔）
        self._is_extended_cooldown: bool = False  # 是否处于升级冷静期

    @property
    def name(self) -> str:
        """Provider 名称"""
        return self.config.name

    @property
    def model(self) -> str:
        """模型名称"""
        return self.config.model

    @property
    def is_healthy(self) -> bool:
        """是否健康

        检查：
        1. 是否被标记为不健康
        2. 是否在冷静期内
        """
        # 冷静期结束后自动恢复健康
        if self._cooldown_until > 0 and time.time() >= self._cooldown_until:
            self._healthy = True
            self._cooldown_until = 0
            self._last_error = None
            self._error_category = ""
            if self._is_extended_cooldown:
                self._is_extended_cooldown = False
                # 渐进退避冷静期结束后重置连续计数，给端点重新证明自己的机会
                self._consecutive_cooldowns = 0
                logger.info(f"[LLM] endpoint={self.name} progressive cooldown expired, reset to healthy")

        return self._healthy

    @property
    def last_error(self) -> str | None:
        """最后一次错误"""
        return self._last_error

    @property
    def error_category(self) -> str:
        """错误分类: auth / quota / structural / transient / unknown"""
        return self._error_category

    @property
    def cooldown_remaining(self) -> int:
        """冷静期剩余秒数"""
        if self._cooldown_until <= 0:
            return 0
        remaining = self._cooldown_until - time.time()
        return max(0, int(remaining))

    @property
    def consecutive_cooldowns(self) -> int:
        """连续进入冷静期的次数"""
        return self._consecutive_cooldowns

    @property
    def is_extended_cooldown(self) -> bool:
        """是否处于渐进升级冷静期"""
        return self._is_extended_cooldown

    def mark_unhealthy(self, error: str, category: str = "", is_local: bool = False):
        """标记为不健康，进入冷静期

        Args:
            error: 错误信息
            category: 错误分类，影响冷静期时长
                - "auth": 认证错误 (60s)
                - "quota": 配额耗尽 (20s)
                - "structural": 结构性/格式错误 (10s)
                - "transient": 超时/连接错误 (5s)
                - "": 默认 (30s)
            is_local: 是否为本地端点（Ollama 等），本地端点 transient
                错误不参与渐进升级（超时是资源不足，非远程故障）

        渐进式冷静期退避：
            连续非结构性错误进入冷静期（中间没有成功请求），冷静期从
            COOLDOWN_ESCALATION_STEPS 按次数递增，上限 5 分钟。
            - 结构性错误不计入连续次数（重试不会改变结果）
            - 本地端点 transient 错误不触发渐进升级（超时是正常行为）
        """
        was_already_unhealthy = not self._healthy
        self._healthy = False
        self._last_error = error
        self._error_category = category or self._classify_error(error)

        # 累计连续冷静期次数
        # - 只在从健康 → 不健康时递增（同一轮重试中多次 mark_unhealthy 不重复计数）
        # - 结构性错误不累计：每次重试结果相同
        # - 本地端点 transient 错误不累计：超时是资源不足，惩罚无意义
        skip_escalation = (
            self._error_category == "structural"
            or (is_local and self._error_category == "transient")
        )
        if not skip_escalation and not was_already_unhealthy:
            self._consecutive_cooldowns += 1

        # 渐进式退避：按连续失败次数从 COOLDOWN_ESCALATION_STEPS 取冷静期
        # 本地端点 transient 错误固定 30s，不参与渐进升级
        if self._error_category == "quota":
            cooldown = COOLDOWN_QUOTA
        elif self._error_category == "auth":
            cooldown = COOLDOWN_AUTH
        elif self._error_category == "structural":
            cooldown = COOLDOWN_STRUCTURAL
        elif self._error_category == "transient":
            if is_local:
                # 本地端点超时固定短冷静期，不升级
                cooldown = COOLDOWN_TRANSIENT
            elif self._consecutive_cooldowns >= 2:
                # 远程端点连续失败 → 渐进退避
                step_idx = min(
                    self._consecutive_cooldowns - 1,
                    len(COOLDOWN_ESCALATION_STEPS) - 1,
                )
                cooldown = COOLDOWN_ESCALATION_STEPS[step_idx]
                self._is_extended_cooldown = True
                logger.warning(
                    f"[LLM] endpoint={self.name} progressive cooldown "
                    f"step {step_idx + 1}/{len(COOLDOWN_ESCALATION_STEPS)} "
                    f"({cooldown}s) after {self._consecutive_cooldowns} "
                    f"consecutive failures"
                )
            else:
                cooldown = COOLDOWN_TRANSIENT
        else:
            # unknown 类型：同样应用渐进退避
            if self._consecutive_cooldowns >= 2:
                step_idx = min(
                    self._consecutive_cooldowns - 1,
                    len(COOLDOWN_ESCALATION_STEPS) - 1,
                )
                cooldown = COOLDOWN_ESCALATION_STEPS[step_idx]
                self._is_extended_cooldown = True
            else:
                cooldown = COOLDOWN_DEFAULT

        self._cooldown_until = time.time() + cooldown

    def mark_healthy(self):
        """标记为健康，清除冷静期和连续失败计数"""
        self._healthy = True
        self._last_error = None
        self._cooldown_until = 0
        self._error_category = ""
        self._consecutive_cooldowns = 0
        self._is_extended_cooldown = False

    def record_success(self):
        """记录一次成功请求，重置连续失败计数并恢复健康状态

        在 _try_endpoints 中成功响应后调用。
        如果端点之前处于冷静期（包括扩展冷静期），成功请求证明端点已恢复，
        应完全清除冷静期状态，而不是让它继续被视为不健康。
        """
        was_unhealthy = not self._healthy or self._cooldown_until > 0
        if was_unhealthy or self._consecutive_cooldowns > 0:
            logger.debug(
                f"[LLM] endpoint={self.name} success, "
                f"reset consecutive cooldowns ({self._consecutive_cooldowns} → 0)"
                + (", clearing cooldown (endpoint proved functional)" if was_unhealthy else "")
            )
        self._consecutive_cooldowns = 0
        self._is_extended_cooldown = False
        # 成功请求证明端点可用，清除冷静期（包括扩展冷静期）
        if was_unhealthy:
            self._healthy = True
            self._cooldown_until = 0
            self._last_error = None
            self._error_category = ""

    def reset_cooldown(self):
        """重置冷静期，允许端点立即被重新尝试

        用于全局故障恢复 / "最后防线旁路" 场景：所有端点同时失败后，
        绕过冷静期让所有端点都可被重新尝试（对齐 Portkey 设计）。

        注意：不重置连续失败计数，因为全局故障重置不代表端点真正恢复。
        如果端点确实有问题，下次请求会再次 mark_unhealthy。
        """
        if self._cooldown_until > 0 or self._is_extended_cooldown or not self._healthy:
            self._cooldown_until = 0
            self._is_extended_cooldown = False
            self._healthy = True
            self._last_error = None
            self._error_category = ""

    def shorten_cooldown(self, seconds: int):
        """缩短冷静期到指定秒数（如果当前冷静期更长的话）

        Args:
            seconds: 新的冷静期秒数（从现在开始计算）

        注意：渐进退避冷静期也可以被缩短（旧版 1h 升级冷静期不可被缩短，
        但新版上限 5min 无此限制）。
        """
        new_until = time.time() + seconds
        if self._cooldown_until > new_until:
            self._cooldown_until = new_until

    @staticmethod
    def _classify_error(error: str) -> str:
        """根据错误信息自动分类

        分类优先级：quota > auth > structural > transient > unknown
        quota 必须在 auth 之前检测，因为 403 配额耗尽也包含 "403" 关键字。
        """
        err_lower = error.lower()

        # 配额耗尽类（必须在 auth 之前，因为也是 403 状态码）
        if any(kw in err_lower for kw in [
            "allocationquota", "freetieronly", "insufficient_quota",
            "quota_exceeded", "billing", "free tier",
            "free_tier", "quota", "exceeded your current",
        ]):
            return "quota"

        # 认证类
        if any(kw in err_lower for kw in [
            "auth", "401", "403", "api_key", "invalid key", "permission",
        ]):
            return "auth"

        # 结构性/格式类
        if any(kw in err_lower for kw in [
            "invalid_request", "invalid_parameter", "messages with role",
            "must be a response", "does not support", "not supported",
            "400",
        ]):
            return "structural"

        # 瞬时类（网络/超时）
        if any(kw in err_lower for kw in [
            "timeout", "timed out", "connect", "connection",
            "network", "unreachable", "reset", "eof", "broken pipe",
            "502", "503", "504", "529",
        ]):
            return "transient"

        return "unknown"

    @abstractmethod
    async def chat(self, request: LLMRequest) -> LLMResponse:
        """
        发送聊天请求

        Args:
            request: 统一请求格式

        Returns:
            统一响应格式
        """
        pass

    @abstractmethod
    async def chat_stream(self, request: LLMRequest) -> AsyncIterator[dict]:
        """
        流式聊天请求

        Args:
            request: 统一请求格式

        Yields:
            流式事件
        """
        pass

    async def health_check(self, dry_run: bool = False) -> bool:
        """
        健康检查

        默认实现：发送一个简单请求测试连接

        Args:
            dry_run: 如果为 True，只测试连通性，不修改 provider 的健康/冷静期状态。
                     适用于桌面端手动检测，避免干扰正在进行的 Agent 调用。
        """
        try:
            from ..types import Message

            request = LLMRequest(
                messages=[Message(role="user", content="Hi")],
                max_tokens=10,
            )
            await self.chat(request)
            if not dry_run:
                self.mark_healthy()
            return True
        except Exception as e:
            if dry_run:
                # dry_run 模式：不修改状态，抛出异常让调用方获取错误详情
                raise
            else:
                # 正常模式：标记不健康，返回 False（保持原始行为）
                self.mark_unhealthy(str(e))
                return False

    @property
    def supports_tools(self) -> bool:
        """是否支持工具调用"""
        return self.config.has_capability("tools")

    @property
    def supports_vision(self) -> bool:
        """是否支持图片"""
        return self.config.has_capability("vision")

    @property
    def supports_video(self) -> bool:
        """是否支持视频"""
        return self.config.has_capability("video")

    @property
    def supports_thinking(self) -> bool:
        """是否支持思考模式"""
        return self.config.has_capability("thinking")

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} model={self.model}>"

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
COOLDOWN_AUTH = 300       # 认证错误: 5 分钟（持久性问题，短时间内不会自愈）
COOLDOWN_STRUCTURAL = 120  # 结构性错误: 2 分钟（消息格式问题，需上层修复）
COOLDOWN_TRANSIENT = 30    # 瞬时错误: 30 秒（超时/连接失败，很可能快速恢复）
COOLDOWN_DEFAULT = 180     # 默认: 3 分钟（向后兼容）
COOLDOWN_GLOBAL_FAILURE = 10  # 全局故障（所有端点同时失败）: 10 秒
COOLDOWN_EXTENDED = 3600   # 升级冷静期: 1 小时（连续多次失败后触发）

# 连续冷静期升级阈值
CONSECUTIVE_FAILURE_THRESHOLD = 3  # 连续进入 N 次冷静期后升级到 COOLDOWN_EXTENDED

# 向后兼容
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
                # 升级冷静期结束后重置连续计数，给端点一次重新证明自己的机会
                self._consecutive_cooldowns = 0
                logger.info(f"[LLM] endpoint={self.name} extended cooldown expired, reset to healthy")

        return self._healthy

    @property
    def last_error(self) -> str | None:
        """最后一次错误"""
        return self._last_error

    @property
    def error_category(self) -> str:
        """错误分类: auth / structural / transient / unknown"""
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
        """是否处于升级冷静期（1小时）"""
        return self._is_extended_cooldown

    def mark_unhealthy(self, error: str, category: str = ""):
        """标记为不健康，进入冷静期

        Args:
            error: 错误信息
            category: 错误分类，影响冷静期时长
                - "auth": 认证错误 (300s)
                - "structural": 结构性/格式错误 (120s)
                - "transient": 超时/连接错误 (30s)
                - "": 默认 (180s)

        连续冷静期升级：
            连续 N 次进入冷静期（中间没有成功请求）→ 升级到 1 小时。
            全局故障（shorten_cooldown）产生的短冷静期不计入连续次数。
        """
        self._healthy = False
        self._last_error = error
        self._error_category = category or self._classify_error(error)

        # 累计连续冷静期次数
        self._consecutive_cooldowns += 1

        # 连续 N 次失败 → 升级到 1 小时冷静期
        if self._consecutive_cooldowns >= CONSECUTIVE_FAILURE_THRESHOLD:
            cooldown = COOLDOWN_EXTENDED
            self._is_extended_cooldown = True
            logger.warning(
                f"[LLM] endpoint={self.name} escalated to extended cooldown "
                f"({COOLDOWN_EXTENDED}s = 1h) after {self._consecutive_cooldowns} "
                f"consecutive failures"
            )
        elif self._error_category == "auth":
            cooldown = COOLDOWN_AUTH
        elif self._error_category == "structural":
            cooldown = COOLDOWN_STRUCTURAL
        elif self._error_category == "transient":
            cooldown = COOLDOWN_TRANSIENT
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
        """重置冷静期（不改变健康标记，仅允许立即重新尝试）

        用于全局故障恢复场景：所有端点同时失败后，
        网络恢复时需要立即重试而非等待冷静期。

        注意：不重置连续失败计数，因为全局故障重置不代表端点真正恢复。
        """
        if self._cooldown_until > 0:
            self._cooldown_until = 0
            # 不清除 _healthy=False，让下次 is_healthy 检查时自然恢复

    def shorten_cooldown(self, seconds: int):
        """缩短冷静期到指定秒数（如果当前冷静期更长的话）

        Args:
            seconds: 新的冷静期秒数（从现在开始计算）

        注意：升级冷静期（1小时）不会被缩短，除非显式调用 reset_cooldown()。
        """
        if self._is_extended_cooldown:
            logger.debug(
                f"[LLM] endpoint={self.name} shorten_cooldown skipped "
                f"(in extended cooldown, {self.cooldown_remaining}s remaining)"
            )
            return
        new_until = time.time() + seconds
        if self._cooldown_until > new_until:
            self._cooldown_until = new_until

    def get_cooldown_state(self) -> dict | None:
        """导出冷静期状态（用于持久化）

        仅导出升级冷静期的状态，普通冷静期不需要持久化（重启后自然清零）。

        Returns:
            状态字典，或 None（无需持久化）
        """
        if not self._is_extended_cooldown or self._cooldown_until <= 0:
            return None
        return {
            "cooldown_until": self._cooldown_until,
            "consecutive_cooldowns": self._consecutive_cooldowns,
            "last_error": self._last_error or "",
            "error_category": self._error_category,
        }

    def restore_cooldown_state(self, state: dict):
        """从持久化状态恢复升级冷静期

        用于进程重启后恢复之前的升级冷静期状态，
        防止通过重启绕过 1 小时冷静期。

        Args:
            state: get_cooldown_state() 返回的字典
        """
        cooldown_until = state.get("cooldown_until", 0)
        if cooldown_until <= time.time():
            # 冷静期已过期，无需恢复
            return

        self._healthy = False
        self._cooldown_until = cooldown_until
        self._consecutive_cooldowns = state.get("consecutive_cooldowns", CONSECUTIVE_FAILURE_THRESHOLD)
        self._last_error = state.get("last_error", "restored from persistent state")
        self._error_category = state.get("error_category", "unknown")
        self._is_extended_cooldown = True
        logger.info(
            f"[LLM] endpoint={self.name} restored extended cooldown from saved state "
            f"({self.cooldown_remaining}s remaining)"
        )

    @staticmethod
    def _classify_error(error: str) -> str:
        """根据错误信息自动分类"""
        err_lower = error.lower()

        # 认证类
        if any(kw in err_lower for kw in [
            "auth", "401", "403", "api_key", "invalid key", "permission",
        ]):
            return "auth"

        # 结构性/格式类
        if any(kw in err_lower for kw in [
            "invalid_request", "invalid_parameter", "messages with role",
            "must be a response", "400",
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

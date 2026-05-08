"""
LLM Provider 基类

定义所有 Provider 必须实现的接口。
"""

import asyncio
import hashlib
import logging
import re
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import AsyncIterator

from ..types import EndpointConfig, LLMRequest, LLMResponse, normalize_base_url

logger = logging.getLogger(__name__)


class RPMRateLimiter:
    """滑动窗口 RPM (Requests Per Minute) 限流器。

    采用 60 秒滑动窗口 + asyncio.Lock 保证并发安全。
    当请求速率超过限制时，自动等待直到窗口内有空余配额。
    """

    __slots__ = ("_rpm", "_window", "_timestamps", "_lock", "_lock_loop_id", "_blocked_until")

    def __init__(self, rpm: int):
        self._rpm = rpm
        self._window = 60.0
        self._timestamps: deque[float] = deque()
        self._lock: asyncio.Lock | None = None
        self._lock_loop_id: int | None = None
        self._blocked_until: float = 0.0

    def configure(self, rpm: int) -> None:
        """更新 RPM 配置，保留已有窗口状态以避免 reload 瞬间绕过限流。"""
        self._rpm = max(0, int(rpm or 0))

    def penalize(self, seconds: float, endpoint_name: str = "") -> None:
        """记录上游 429 后的短暂退避。

        这不是常规限流额度，只在服务商已经明确返回 rate limit 后生效，
        用来阻止多 Agent/多 LLMClient 实例继续立刻打同一个上游端点。
        """
        if seconds <= 0:
            return
        seconds = min(max(seconds, 1.0), 300.0)
        until = time.monotonic() + seconds
        if until > self._blocked_until:
            self._blocked_until = until
            tag = f" endpoint={endpoint_name}" if endpoint_name else ""
            logger.warning(f"[RPM]{tag} upstream rate limit backoff {seconds:.1f}s")

    def _get_lock(self) -> asyncio.Lock:
        """获取或创建 asyncio.Lock（绑定到当前事件循环）。"""
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = None
        if self._lock is None or self._lock_loop_id != loop_id:
            self._lock = asyncio.Lock()
            self._lock_loop_id = loop_id
        return self._lock

    async def acquire(self, endpoint_name: str = "") -> None:
        """获取一个请求配额，必要时等待。"""
        lock = self._get_lock()
        while True:
            async with lock:
                now = time.monotonic()
                if self._blocked_until > now:
                    wait_time = self._blocked_until - now
                elif self._rpm <= 0:
                    return
                else:
                    wait_time = 0

                if wait_time <= 0:
                    while self._timestamps and self._timestamps[0] <= now - self._window:
                        self._timestamps.popleft()

                    if len(self._timestamps) < self._rpm:
                        self._timestamps.append(now)
                        return

                    oldest = self._timestamps[0]
                    wait_time = oldest + self._window - now

            tag = f" endpoint={endpoint_name}" if endpoint_name else ""
            if self._blocked_until > time.monotonic():
                logger.info(f"[RPM]{tag} upstream backoff active, waiting {wait_time:.1f}s")
            else:
                logger.info(
                    f"[RPM]{tag} rate limit reached ({self._rpm} rpm), waiting {wait_time:.1f}s"
                )
            await asyncio.sleep(max(wait_time, 0.1))


# 冷静期时长（秒）- 按错误类型区分
COOLDOWN_AUTH = 60  # 认证错误: 1 分钟（需要人工干预，但不宜锁太久）
COOLDOWN_QUOTA = 300  # 配额耗尽: 5 分钟（配额恢复通常需要数小时）
COOLDOWN_STRUCTURAL = 10  # 结构性错误: 10 秒（上层会快速识别处理）
COOLDOWN_TRANSIENT = 5  # 瞬时错误: 5 秒（超时/连接失败，很可能快速恢复）
COOLDOWN_DEFAULT = 30  # 默认: 30 秒
COOLDOWN_GLOBAL_FAILURE = 10  # 全局故障（所有端点同时失败）: 10 秒

# 渐进式冷静期退避 —— 连续失败时按次数递增
COOLDOWN_ESCALATION_STEPS = [5, 10, 20, 60]  # 5s -> 10s -> 20s -> 60s(上限)
# quota/auth 专用退避 —— 首次 5 分钟，连续失败升级到 30 分钟
COOLDOWN_QUOTA_ESCALATION = [300, 600, 1200, 1800]  # 5m -> 10m -> 20m -> 30m

# 向后兼容（旧代码引用）
COOLDOWN_EXTENDED = COOLDOWN_ESCALATION_STEPS[-1]
CONSECUTIVE_FAILURE_THRESHOLD = 3  # 保留常量以向后兼容，但不再触发 1h 冷静期
COOLDOWN_SECONDS = COOLDOWN_DEFAULT


class LLMProvider(ABC):
    """LLM Provider 基类"""

    _shared_rate_limiters: dict[str, RPMRateLimiter] = {}

    def __init__(self, config: EndpointConfig):
        self.config = config
        self._healthy = True
        self._last_error: str | None = None
        self._cooldown_until: float = 0  # 冷静期结束时间戳
        self._error_category: str = ""  # 错误分类
        self._consecutive_cooldowns: int = 0  # 连续进入冷静期次数（无成功请求间隔）
        self._is_extended_cooldown: bool = False  # 是否处于升级冷静期
        _rpm = config.rpm_limit if isinstance(config.rpm_limit, int) else 0
        self._rate_limiter: RPMRateLimiter = self._get_shared_rate_limiter(_rpm)

    def _get_shared_rate_limiter(self, rpm: int) -> RPMRateLimiter:
        """按真实上游身份共享限速器，避免多 Agent/多客户端实例绕过 RPM。"""
        key = self._rate_limit_key()
        limiter = self._shared_rate_limiters.get(key)
        if limiter is None:
            limiter = RPMRateLimiter(max(0, int(rpm or 0)))
            self._shared_rate_limiters[key] = limiter
        else:
            limiter.configure(max(0, int(rpm or 0)))
        return limiter

    def _rate_limit_key(self) -> str:
        api_key = self.config.get_api_key() or ""
        key_fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16] if api_key else ""
        base_url = normalize_base_url(self.config.base_url or "")
        return "|".join(
            [
                self.config.api_type or "",
                base_url,
                self.config.model or "",
                self.config.api_key_env or "",
                key_fingerprint,
            ]
        )

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
                # 不重置 _consecutive_cooldowns：只有 record_success() 才重置。
                # 这样持续失败的端点在冷却到期后如果再次失败，会直接进入
                # 上一次的退避阶级（而非从 0 重新开始），避免 5s→10s→reset 循环。
                logger.info(
                    f"[LLM] endpoint={self.name} progressive cooldown expired, "
                    f"reset to healthy (consecutive_cooldowns={self._consecutive_cooldowns} preserved)"
                )

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
            is_local: 是否为本地端点（Ollama 等），影响 transient 错误的退避策略

        渐进式冷静期退避：
            连续非结构性错误进入冷静期（中间没有成功请求），冷静期从
            COOLDOWN_ESCALATION_STEPS 按次数递增，上限 60 秒。
            - 结构性错误不计入连续次数（重试不会改变结果）
            - 本地端点 Timeout 不触发渐进升级（超时是推理资源不足）
            - 本地端点 ConnectError 正常参与渐进升级（服务未启动，持续重试无意义）
        """
        was_already_unhealthy = not self._healthy
        self._healthy = False
        self._last_error = error
        self._error_category = category or self._classify_error(error)

        # 累计连续冷静期次数
        # - 只在从健康 → 不健康时递增（同一轮重试中多次 mark_unhealthy 不重复计数）
        # - 结构性错误不累计：每次重试结果相同
        # - 本地端点 Timeout 不累计：超时是推理资源不足，惩罚无意义
        #   但 ConnectError（服务未启动）应参与渐进退避，否则 5s 冷却循环浪费资源
        skip_escalation = self._error_category == "structural" or (
            is_local and self._error_category == "transient" and self._is_timeout_error(error)
        )
        if not skip_escalation and not was_already_unhealthy:
            self._consecutive_cooldowns += 1

        # 渐进式退避：按连续失败次数从对应退避序列取冷静期
        if self._error_category == "quota":
            step_idx = min(
                max(self._consecutive_cooldowns - 1, 0),
                len(COOLDOWN_QUOTA_ESCALATION) - 1,
            )
            cooldown = max(COOLDOWN_QUOTA, COOLDOWN_QUOTA_ESCALATION[step_idx])
            if self._consecutive_cooldowns >= 2:
                self._is_extended_cooldown = True
                logger.warning(
                    f"[LLM] endpoint={self.name} quota progressive cooldown "
                    f"step {step_idx + 1}/{len(COOLDOWN_QUOTA_ESCALATION)} "
                    f"({cooldown}s) after {self._consecutive_cooldowns} "
                    f"consecutive failures"
                )
        elif self._error_category == "auth":
            cooldown = COOLDOWN_AUTH
        elif self._error_category == "structural":
            cooldown = COOLDOWN_STRUCTURAL
        elif self._error_category == "transient":
            _local_timeout = is_local and self._is_timeout_error(error)
            if _local_timeout:
                # 本地端点超时固定短冷静期，不升级（服务在跑但慢）
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

    async def acquire_rate_limit(self):
        """获取 RPM 限流配额，必要时等待。无限流配置时立即返回。"""
        await self._rate_limiter.acquire(endpoint_name=self.name)

    def report_upstream_rate_limit(self, error: str) -> None:
        """上游明确返回 429 后，给同一上游身份设置短暂共享退避。"""
        retry_after = self._parse_retry_after_seconds(error)
        self._rate_limiter.penalize(retry_after or 60.0, endpoint_name=self.name)

    @staticmethod
    def _parse_retry_after_seconds(error: str) -> float | None:
        """从常见 429 文本中提取 retry-after/reset 秒数，缺失时由调用方兜底。"""
        err = error or ""
        patterns = [
            r"retry-after[\"'\s:=]+(\d+(?:\.\d+)?)",
            r"retry_after[\"'\s:=]+(\d+(?:\.\d+)?)",
            r"retry in\s+(\d+(?:\.\d+)?)\s*s",
            r"try again in\s+(\d+(?:\.\d+)?)\s*s",
        ]
        for pattern in patterns:
            match = re.search(pattern, err, flags=re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    return None
        return None

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

        注意：如果缩短了渐进退避冷静期，必须同步清除 _is_extended_cooldown，
        否则缩短后的冷静期到期时 is_healthy 会误以为完整退避已完成，
        错误重置 _consecutive_cooldowns，导致渐进退避永远无法升级。
        """
        new_until = time.time() + seconds
        if self._cooldown_until > new_until:
            if self._is_extended_cooldown:
                self._is_extended_cooldown = False
            self._cooldown_until = new_until

    @staticmethod
    def _is_timeout_error(error: str) -> bool:
        """判断是否为超时错误（区别于连接拒绝等其他 transient 错误）"""
        err = error.lower()
        return any(kw in err for kw in ["timeout", "timed out"])

    @staticmethod
    def _classify_error(error: str) -> str:
        """根据错误信息自动分类。

        分类优先级: content_safety > quota > auth > rate_limit(→transient) > structural > transient > unknown
        content_safety 必须最优先检测，否则 "data_inspection_failed" 中的
        "(400)" 或其他子串会被错误分到 STRUCTURAL，导致换端点重试浪费配额。
        quota 必须在 auth 之前检测，因为 403 配额耗尽也包含 "403" 关键字。
        rate_limit 必须在 structural 之前检测，因为某些提供商 429 响应体含 "invalid_request"。

        返回 ``FailoverReason`` 枚举成员 (StrEnum, 与字符串互兼容)。
        """
        from ..error_types import FailoverReason

        err_lower = error.lower()

        if any(
            kw in err_lower
            for kw in [
                "data_inspection",
                "datainspectionfailed",
                "inappropriate content",
                "content_filter",
            ]
        ):
            return FailoverReason.CONTENT_SAFETY

        if any(
            kw in err_lower
            for kw in [
                "allocationquota",
                "freetieronly",
                "insufficientquota",
                "insufficient_quota",
                "insufficient balance",
                "balance insufficient",
                "account balance",
                "quota_exceeded",
                "quota exceeded",
                "payment required",
                "billing",
                "free tier",
                "free_tier",
                "quota",
                "tokens.total",
                "business.total",
                "exceeded your current",
                "api error (402)",
                "http 402",
                "(402)",
                "余额不足",
                "额度不足",
                "额度已用尽",
                "账户余额",
                "请充值",
            ]
        ):
            return FailoverReason.QUOTA

        if any(
            kw in err_lower
            for kw in [
                "auth",
                "appidnoautherror",
                "noauth",
                "unauthorized",
                "401",
                "403",
                "api_key",
                "invalid key",
                "permission",
            ]
        ):
            return FailoverReason.AUTH

        # 限速类（必须在 structural 之前，某些提供商 429 响应体含 "invalid_request"）#324
        if any(
            kw in err_lower
            for kw in [
                "rate limit",
                "rate_limit",
                "too many requests",
                "rate reaches maximum",
                "request rate",
                "(429)",
            ]
        ):
            return FailoverReason.TRANSIENT

        # 结构性/格式类
        # 注意: 用 "(400)" 而非 "400"，避免匹配 HTML 中的 CSS 类名等内容
        if any(
            kw in err_lower
            for kw in [
                "invalid_request",
                "invalid_parameter",
                "messages with role",
                "must be a response",
                "does not support",
                "not supported",
                "(400)",
                "(413)",
                "payload too large",
                "request entity too large",
                "larger than allowed",
                "exceed_context_size",
                "exceeds the available context",
                "maximum context length",
                "prompt is too long",
                "too many tokens",
            ]
        ):
            return FailoverReason.STRUCTURAL

        if any(
            kw in err_lower
            for kw in [
                "timeout",
                "timed out",
                "connect",
                "connection",
                "network",
                "unreachable",
                "reset",
                "eof",
                "broken pipe",
                "502",
                "503",
                "504",
                "529",
            ]
        ):
            return FailoverReason.TRANSIENT

        return FailoverReason.UNKNOWN

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

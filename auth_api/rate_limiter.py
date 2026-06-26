"""
速率限制中间件
防止暴力破解：登录接口限制 5 次/分钟
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict
import time


class RateLimiter:
    """简单的内存速率限制器"""

    def __init__(self):
        # 存储每个 IP 的请求记录：{ip: [timestamp1, timestamp2, ...]}
        self.requests = defaultdict(list)
        self.cleanup_interval = 300  # 5 分钟清理一次过期记录

    def is_allowed(self, client_ip: str, max_requests: int = 5, window_seconds: int = 60) -> bool:
        """
        检查请求是否允许
        :param client_ip: 客户端 IP
        :param max_requests: 时间窗口内最大请求数
        :param window_seconds: 时间窗口（秒）
        :return: 是否允许
        """
        now = time.time()
        window_start = now - window_seconds

        # 清理过期请求记录
        self.requests[client_ip] = [ts for ts in self.requests[client_ip] if ts > window_start]

        # 检查是否超出限制
        if len(self.requests[client_ip]) >= max_requests:
            return False

        # 记录当前请求
        self.requests[client_ip].append(now)
        return True

    def get_retry_after(self, client_ip: str, window_seconds: int = 60) -> int:
        """获取重试等待时间（秒）"""
        if not self.requests[client_ip]:
            return 0

        oldest_request = min(self.requests[client_ip])
        retry_after = int(oldest_request + window_seconds - time.time())
        return max(0, retry_after)


# 全局速率限制器实例
login_rate_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """速率限制中间件"""

    async def dispatch(self, request: Request, call_next):
        # 仅对登录接口进行速率限制
        if request.url.path == "/api/auth/login" and request.method == "POST":
            client_ip = self.get_client_ip(request)

            if not login_rate_limiter.is_allowed(client_ip, max_requests=5, window_seconds=60):
                retry_after = login_rate_limiter.get_retry_after(client_ip)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "请求过于频繁，请稍后再试", "retry_after": retry_after},
                    headers={"Retry-After": str(retry_after)},
                )

        response = await call_next(request)
        return response

    def get_client_ip(self, request: Request) -> str:
        """获取客户端真实 IP"""
        # 检查代理头
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        # 直接连接
        if request.client:
            return request.client.host

        return "unknown"

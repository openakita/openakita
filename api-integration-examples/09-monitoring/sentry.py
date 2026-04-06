"""
API 集成示例 9: Sentry 错误监控
"""
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

class SentryMonitor:
    def __init__(self, dsn, environment="development"):
        self.dsn = dsn
        self.environment = environment
    
    def init(self):
        """初始化 Sentry"""
        sentry_sdk.init(
            dsn=self.dsn,
            environment=self.environment,
            traces_sample_rate=1.0,
            integrations=[LoggingIntegration()]
        )
    
    def capture_exception(self, exception):
        """捕获异常"""
        return sentry_sdk.capture_exception(exception)
    
    def capture_message(self, message, level="info"):
        """捕获消息"""
        return sentry_sdk.capture_message(message, level=level)
    
    def set_user(self, user_id, email=None, username=None):
        """设置用户信息"""
        sentry_sdk.set_user({
            "id": user_id,
            "email": email,
            "username": username
        })
    
    def set_tag(self, key, value):
        """设置标签"""
        sentry_sdk.set_tag(key, value)
    
    def add_breadcrumb(self, message, category="default", level="info"):
        """添加面包屑"""
        sentry_sdk.add_breadcrumb(message=message, category=category, level=level)

# 使用示例
if __name__ == "__main__":
    # 初始化
    # sentry = SentryMonitor("https://xxx@xxx.ingest.sentry.io/xxx")
    # sentry.init()
    
    # 捕获异常
    try:
        1 / 0
    except Exception as e:
        # sentry.capture_exception(e)
        pass
    
    print("Sentry 监控示例已就绪")

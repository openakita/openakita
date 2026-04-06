# Sentry 监控日志 API 集成示例
# 适用于 MVP 错误追踪、性能监控、日志聚合

import os
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from typing import Optional, Dict, Any


class SentryClient:
    """Sentry 监控客户端封装"""
    
    def __init__(self, dsn: str = None, environment: str = "development"):
        """
        初始化 Sentry
        
        Args:
            dsn: Sentry DSN（从环境变量读取）
            environment: 环境（development, staging, production）
        """
        self.dsn = dsn or os.getenv("SENTRY_DSN")
        self.environment = environment
        
        if not self.dsn:
            print("Warning: SENTRY_DSN not configured, monitoring disabled")
            return
        
        self._init_sentry()
    
    def _init_sentry(self):
        """初始化 Sentry SDK"""
        sentry_sdk.init(
            dsn=self.dsn,
            environment=self.environment,
            traces_sample_rate=1.0 if self.environment == "development" else 0.1,
            profiles_sample_rate=1.0 if self.environment == "development" else 0.1,
            integrations=[
                FastApiIntegration(),
                RedisIntegration(),
                SqlalchemyIntegration(),
            ],
            # 错误采样配置
            send_default_pii=True,
            # 性能监控
            enable_tracing=True,
        )
    
    def capture_exception(self, exception: Exception, context: Dict = None) -> dict:
        """
        捕获异常
        
        Args:
            exception: 异常对象
            context: 附加上下文
        
        Returns:
            事件 ID
        """
        try:
            if context:
                sentry_sdk.set_context("custom", context)
            
            event_id = sentry_sdk.capture_exception(exception)
            return {"success": True, "event_id": event_id}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def capture_message(self, message: str, level: str = "info", context: Dict = None) -> dict:
        """
        捕获消息
        
        Args:
            message: 消息内容
            level: 日志级别（debug, info, warning, error）
            context: 附加上下文
        
        Returns:
            事件 ID
        """
        try:
            if context:
                sentry_sdk.set_context("custom", context)
            
            sentry_sdk.set_level(level)
            event_id = sentry_sdk.capture_message(message)
            return {"success": True, "event_id": event_id}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def set_user(self, user_id: str, email: str = None, username: str = None) -> dict:
        """
        设置用户上下文
        
        Args:
            user_id: 用户 ID
            email: 邮箱
            username: 用户名
        
        Returns:
            设置结果
        """
        try:
            sentry_sdk.set_user({
                "id": user_id,
                "email": email,
                "username": username
            })
            return {"success": True, "user_id": user_id}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def set_tag(self, key: str, value: str) -> dict:
        """
        设置标签
        
        Args:
            key: 标签键
            value: 标签值
        
        Returns:
            设置结果
        """
        try:
            sentry_sdk.set_tag(key, value)
            return {"success": True, "key": key, "value": value}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def add_breadcrumb(self, message: str, category: str = "default", level: str = "info") -> dict:
        """
        添加面包屑（用于追踪错误发生前的操作）
        
        Args:
            message: 消息
            category: 类别
            level: 级别
        
        Returns:
            添加结果
        """
        try:
            sentry_sdk.add_breadcrumb(
                message=message,
                category=category,
                level=level
            )
            return {"success": True, "message": message}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def start_transaction(self, name: str, op: str = "function") -> any:
        """
        开始事务（性能监控）
        
        Args:
            name: 事务名称
            op: 操作类型
        
        Returns:
            事务对象
        """
        transaction = sentry_sdk.start_transaction(name=name, op=op)
        transaction.__enter__()
        return transaction
    
    def end_transaction(self, transaction: any, status: str = "ok") -> dict:
        """
        结束事务
        
        Args:
            transaction: 事务对象
            status: 状态
        
        Returns:
            结束结果
        """
        try:
            transaction.set_status(status)
            transaction.__exit__(None, None, None)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ============== FastAPI 集成示例 ==============

def setup_sentry_fastapi(app):
    """
    在 FastAPI 应用中设置 Sentry
    
    Args:
        app: FastAPI 应用实例
    """
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN"),
        environment=os.getenv("ENVIRONMENT", "development"),
        integrations=[
            FastApiIntegration(
                app=app,
                transaction_style="endpoint"
            ),
        ],
        traces_sample_rate=1.0,
    )


# ============== 装饰器示例 ==============

def with_sentry_monitoring(func_name: str = None):
    """
    函数监控装饰器
    
    Args:
        func_name: 函数名称
    
    Returns:
        装饰器
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            name = func_name or func.__name__
            client = SentryClient()
            
            # 添加面包屑
            client.add_breadcrumb(f"Starting {name}", category="function")
            
            # 开始事务
            transaction = client.start_transaction(name=name, op="function")
            
            try:
                result = func(*args, **kwargs)
                client.end_transaction(transaction, status="ok")
                return result
            except Exception as e:
                client.capture_exception(e, context={"function": name, "args": str(args)})
                client.end_transaction(transaction, status="internal_error")
                raise
        
        return wrapper
    return decorator


# ============== 使用示例 ==============

if __name__ == "__main__":
    # 1. 初始化 Sentry
    client = SentryClient(environment="development")
    
    # 2. 设置用户上下文
    client.set_user(
        user_id="user_123",
        email="user@example.com",
        username="testuser"
    )
    
    # 3. 添加标签
    client.set_tag("version", "1.0.0")
    client.set_tag("environment", "development")
    
    # 4. 添加面包屑
    client.add_breadcrumb("User clicked button", category="ui", level="info")
    client.add_breadcrumb("API request started", category="http", level="info")
    
    # 5. 捕获消息
    client.capture_message("Application started", level="info")
    
    # 6. 捕获异常
    try:
        # 模拟错误
        raise ValueError("Test error")
    except Exception as e:
        result = client.capture_exception(
            e,
            context={"module": "main", "function": "test"}
        )
        print(f"异常上报：{result}")
    
    # 7. 性能监控
    transaction = client.start_transaction(name="process_data", op="task")
    try:
        # 模拟处理
        import time
        time.sleep(1)
        client.end_transaction(transaction, status="ok")
    except Exception as e:
        client.end_transaction(transaction, status="error")
        client.capture_exception(e)

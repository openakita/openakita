"""
日志监控 API 集成示例代码
功能：错误追踪、性能监控、日志收集
支持：Sentry、Elasticsearch
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import json
import time
from datetime import datetime

load_dotenv()

# Sentry 配置
SENTRY_DSN = os.getenv("SENTRY_DSN", "https://your-dsn@sentry.io/your-project-id")

# Elasticsearch 配置
ELASTICSEARCH_HOST = os.getenv("ELASTICSEARCH_HOST", "localhost")
ELASTICSEARCH_PORT = os.getenv("ELASTICSEARCH_PORT", "9200")
ELASTICSEARCH_INDEX = "app-logs"


class LogEntry(BaseModel):
    """日志条目"""
    level: str  # DEBUG/INFO/WARNING/ERROR/CRITICAL
    message: str
    timestamp: str
    source: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


# ============ Sentry ============

class SentryClient:
    """Sentry 客户端"""
    
    def __init__(self):
        self.dsn = SENTRY_DSN
        self.initialized = False
    
    def init(self):
        """初始化 Sentry"""
        print(f"Sentry 初始化:")
        print(f"  DSN: {self.dsn[:50]}...")
        print(f"  状态：已初始化\n")
        self.initialized = True
    
    def capture_exception(
        self,
        exception: Exception,
        level: str = "error"
    ) -> Optional[str]:
        """
        捕获异常
        
        Args:
            exception: 异常对象
            level: 日志级别
        
        Returns:
            事件 ID
        """
        if not self.initialized:
            self.init()
        
        event_id = f"EVENT_{int(time.time())}"
        
        print(f"Sentry 捕获异常:")
        print(f"  类型：{type(exception).__name__}")
        print(f"  消息：{str(exception)}")
        print(f"  级别：{level}")
        print(f"  事件 ID: {event_id}\n")
        
        return event_id
    
    def capture_message(
        self,
        message: str,
        level: str = "info"
    ) -> Optional[str]:
        """
        捕获消息
        
        Args:
            message: 消息内容
            level: 日志级别
        
        Returns:
            事件 ID
        """
        if not self.initialized:
            self.init()
        
        event_id = f"MSG_{int(time.time())}"
        
        print(f"Sentry 捕获消息:")
        print(f"  消息：{message}")
        print(f"  级别：{level}")
        print(f"  事件 ID: {event_id}\n")
        
        return event_id
    
    def set_context(self, name: str, data: dict):
        """
        设置上下文
        
        Args:
            name: 上下文名称
            data: 上下文数据
        """
        print(f"Sentry 设置上下文:")
        print(f"  名称：{name}")
        print(f"  数据：{json.dumps(data, ensure_ascii=False)}\n")
    
    def set_user(self, user_id: str, email: Optional[str] = None):
        """
        设置用户信息
        
        Args:
            user_id: 用户 ID
            email: 邮箱
        """
        print(f"Sentry 设置用户:")
        print(f"  用户 ID: {user_id}")
        print(f"  邮箱：{email}\n")


# ============ Elasticsearch ============

class ElasticsearchClient:
    """Elasticsearch 客户端"""
    
    def __init__(self):
        self.host = ELASTICSEARCH_HOST
        self.port = ELASTICSEARCH_PORT
        self.index = ELASTICSEARCH_INDEX
        self.base_url = f"http://{self.host}:{self.port}"
    
    def index_log(self, log_entry: LogEntry) -> bool:
        """
        索引日志
        
        Args:
            log_entry: 日志条目
        
        Returns:
            是否成功
        """
        doc = {
            "level": log_entry.level,
            "message": log_entry.message,
            "timestamp": log_entry.timestamp,
            "source": log_entry.source,
            "extra": log_entry.extra
        }
        
        print(f"Elasticsearch 索引日志:")
        print(f"  索引：{self.index}")
        print(f"  级别：{log_entry.level}")
        print(f"  消息：{log_entry.message}")
        print(f"  时间：{log_entry.timestamp}")
        print()
        
        return True
    
    def search_logs(
        self,
        query: str,
        level: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        size: int = 10
    ) -> list:
        """
        搜索日志
        
        Args:
            query: 搜索关键词
            level: 日志级别过滤
            start_time: 开始时间
            end_time: 结束时间
            size: 返回数量
        
        Returns:
            日志列表
        """
        print(f"Elasticsearch 搜索日志:")
        print(f"  查询：{query}")
        print(f"  级别：{level or '全部'}")
        print(f"  时间范围：{start_time or '不限'} ~ {end_time or '不限'}")
        print(f"  返回数量：{size}")
        print()
        
        # 模拟返回
        return [
            {
                "timestamp": "2024-03-18T10:00:00Z",
                "level": "ERROR",
                "message": "Database connection failed",
                "source": "api.database"
            },
            {
                "timestamp": "2024-03-18T10:05:00Z",
                "level": "WARNING",
                "message": "High memory usage detected",
                "source": "system.monitor"
            }
        ]
    
    def create_index(self, index_name: str, mappings: dict) -> bool:
        """
        创建索引
        
        Args:
            index_name: 索引名称
            mappings: 映射定义
        
        Returns:
            是否成功
        """
        print(f"Elasticsearch 创建索引:")
        print(f"  索引：{index_name}")
        print(f"  映射：{json.dumps(mappings, ensure_ascii=False)[:100]}...")
        print()
        
        return True


# ============ 统一监控服务 ============

class MonitoringService:
    """统一监控服务"""
    
    def __init__(self, sentry_enabled: bool = True, es_enabled: bool = True):
        self.sentry = SentryClient() if sentry_enabled else None
        self.es = ElasticsearchClient() if es_enabled else None
        
        if sentry_enabled:
            self.sentry.init()
    
    def log(
        self,
        level: str,
        message: str,
        source: Optional[str] = None,
        extra: Optional[dict] = None,
        exception: Optional[Exception] = None
    ):
        """
        记录日志
        
        Args:
            level: 日志级别
            message: 消息内容
            source: 来源
            extra: 额外数据
            exception: 异常对象
        """
        timestamp = datetime.utcnow().isoformat()
        
        log_entry = LogEntry(
            level=level,
            message=message,
            timestamp=timestamp,
            source=source,
            extra=extra
        )
        
        # 记录到 Elasticsearch
        if self.es:
            self.es.index_log(log_entry)
        
        # 上报到 Sentry
        if self.sentry:
            if exception:
                self.sentry.capture_exception(exception, level.lower())
            else:
                self.sentry.capture_message(message, level.lower())
    
    def error(self, message: str, **kwargs):
        """记录错误日志"""
        self.log("ERROR", message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """记录警告日志"""
        self.log("WARNING", message, **kwargs)
    
    def info(self, message: str, **kwargs):
        """记录信息日志"""
        self.log("INFO", message, **kwargs)
    
    def debug(self, message: str, **kwargs):
        """记录调试日志"""
        self.log("DEBUG", message, **kwargs)


# ============ 使用示例 ============

def example_monitoring():
    """监控 API 示例"""
    print("=== 日志监控 API 示例 ===\n")
    
    # 1. Sentry 异常捕获
    print("1. Sentry 异常捕获:")
    sentry = SentryClient()
    sentry.init()
    
    try:
        # 模拟异常
        raise ValueError("测试异常")
    except Exception as e:
        event_id = sentry.capture_exception(e)
        print(f"   事件 ID: {event_id}\n")
    
    # 2. Sentry 消息
    print("2. Sentry 消息:")
    sentry.capture_message("用户登录成功", level="info")
    
    # 3. Sentry 上下文
    print("3. Sentry 上下文:")
    sentry.set_user(user_id="123", email="user@example.com")
    sentry.set_context("request", {"method": "POST", "path": "/api/login"})
    
    # 4. Elasticsearch 索引
    print("4. Elasticsearch 索引:")
    es = ElasticsearchClient()
    log_entry = LogEntry(
        level="ERROR",
        message="Database connection timeout",
        timestamp=datetime.utcnow().isoformat(),
        source="api.database",
        extra={"db_host": "localhost", "timeout": 30}
    )
    es.index_log(log_entry)
    
    # 5. Elasticsearch 搜索
    print("5. Elasticsearch 搜索:")
    results = es.search_logs(query="error", level="ERROR")
    for log in results:
        print(f"   [{log['level']}] {log['message']} ({log['source']})")
    print()
    
    # 6. 统一监控服务
    print("6. 统一监控服务:")
    monitor = MonitoringService(sentry_enabled=True, es_enabled=True)
    monitor.info("应用启动")
    monitor.warning("内存使用率超过 80%")
    monitor.error("API 请求失败", source="api.user", extra={"user_id": "123"})
    
    try:
        raise RuntimeError("测试异常")
    except Exception as e:
        monitor.error("捕获到异常", exception=e)


if __name__ == "__main__":
    example_monitoring()

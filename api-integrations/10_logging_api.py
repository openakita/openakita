"""
日志监控 API 集成示例
支持结构化日志、Prometheus 指标、Sentry 错误追踪
"""

import logging
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any, Callable
from functools import wraps
from contextlib import contextmanager


class StructuredLogger:
    """结构化日志 API"""
    
    def __init__(self, name: str, level: int = logging.INFO, 
                 json_format: bool = True, output_file: str = None):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.json_format = json_format
        
        # 创建处理器
        handlers = []
        
        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        handlers.append(console_handler)
        
        # 文件处理器（可选）
        if output_file:
            file_handler = logging.FileHandler(output_file, encoding='utf-8')
            file_handler.setLevel(level)
            handlers.append(file_handler)
        
        # 设置格式
        for handler in handlers:
            if json_format:
                handler.setFormatter(JsonFormatter())
            else:
                handler.setFormatter(logging.Formatter(
                    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
                ))
            self.logger.addHandler(handler)
        
        print(f"✓ 日志系统已初始化：{name}")
    
    def info(self, message: str, **kwargs):
        """记录 INFO 级别日志"""
        self._log(logging.INFO, message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """记录 WARNING 级别日志"""
        self._log(logging.WARNING, message, **kwargs)
    
    def error(self, message: str, exc_info: bool = False, **kwargs):
        """记录 ERROR 级别日志"""
        self._log(logging.ERROR, message, exc_info=exc_info, **kwargs)
    
    def debug(self, message: str, **kwargs):
        """记录 DEBUG 级别日志"""
        self._log(logging.DEBUG, message, **kwargs)
    
    def _log(self, level: int, message: str, **kwargs):
        """内部日志记录方法"""
        extra = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': logging.getLevelName(level),
            'message': message
        }
        extra.update(kwargs)
        
        self.logger.log(level, json.dumps(extra, ensure_ascii=False), extra=extra)


class JsonFormatter(logging.Formatter):
    """JSON 格式日志 formatter"""
    
    def format(self, record):
        log_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # 添加额外字段
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'created', 'filename', 'funcName',
                          'levelname', 'levelno', 'lineno', 'module', 'msecs',
                          'pathname', 'process', 'processName', 'relativeCreated',
                          'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName']:
                log_data[key] = value
        
        return json.dumps(log_data, ensure_ascii=False)


class PerformanceMonitor:
    """性能监控 API"""
    
    def __init__(self, logger: StructuredLogger = None):
        self.logger = logger or StructuredLogger("performance")
        self.metrics = {}
    
    @contextmanager
    def track_time(self, operation: str):
        """
        追踪代码执行时间（上下文管理器）
        
        使用示例:
        with monitor.track_time("数据库查询"):
            # 执行数据库操作
        """
        start_time = time.time()
        try:
            yield
        finally:
            elapsed = time.time() - start_time
            self.logger.info(
                f"操作完成：{operation}",
                operation=operation,
                duration_ms=round(elapsed * 1000, 2)
            )
    
    def track_function(self, func: Callable) -> Callable:
        """
        追踪函数执行时间（装饰器）
        
        使用示例:
        @monitor.track_function
        def slow_function():
            time.sleep(1)
        """
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time
                self.logger.info(
                    f"函数执行完成：{func.__name__}",
                    function=func.__name__,
                    duration_ms=round(elapsed * 1000, 2),
                    success=True
                )
                return result
            except Exception as e:
                elapsed = time.time() - start_time
                self.logger.error(
                    f"函数执行失败：{func.__name__}",
                    function=func.__name__,
                    duration_ms=round(elapsed * 1000, 2),
                    success=False,
                    error=str(e),
                    exc_info=True
                )
                raise
        
        return wrapper
    
    def record_metric(self, name: str, value: float, labels: Dict = None):
        """
        记录指标数据
        
        Args:
            name: 指标名称
            value: 指标值
            labels: 标签字典
        """
        metric = {
            'name': name,
            'value': value,
            'timestamp': datetime.utcnow().isoformat(),
            'labels': labels or {}
        }
        
        if name not in self.metrics:
            self.metrics[name] = []
        
        self.metrics[name].append(metric)
        
        # 保留最近 1000 条记录
        if len(self.metrics[name]) > 1000:
            self.metrics[name] = self.metrics[name][-1000:]
        
        self.logger.debug(f"指标记录：{name}={value}", metric=metric)


class SentryErrorTracker:
    """Sentry 错误追踪 API"""
    
    def __init__(self, dsn: str, environment: str = 'production'):
        self.dsn = dsn
        self.environment = environment
        self.client = None
    
    def init(self):
        """初始化 Sentry"""
        try:
            import sentry_sdk
            from sentry_sdk.integrations.logging import LoggingIntegration
            
            sentry_logging = LoggingIntegration(
                level=logging.INFO,
                event_level=logging.ERROR
            )
            
            sentry_sdk.init(
                dsn=self.dsn,
                integrations=[sentry_logging],
                environment=self.environment,
                traces_sample_rate=1.0
            )
            
            self.client = sentry_sdk
            print(f"✓ Sentry 已初始化（环境：{self.environment}）")
            
        except ImportError:
            print("✗ 未安装 sentry-sdk，请运行：pip install sentry-sdk")
        except Exception as e:
            print(f"✗ Sentry 初始化失败：{e}")
    
    def capture_exception(self, exception: Exception, **kwargs):
        """捕获异常"""
        if self.client:
            with self.client.push_scope() as scope:
                for key, value in kwargs.items():
                    scope.set_extra(key, value)
                self.client.capture_exception(exception)
                print(f"✓ 异常已上报到 Sentry")
    
    def capture_message(self, message: str, level: str = 'info', **kwargs):
        """捕获消息"""
        if self.client:
            with self.client.push_scope() as scope:
                for key, value in kwargs.items():
                    scope.set_extra(key, value)
                self.client.capture_message(message, level=level)
                print(f"✓ 消息已上报到 Sentry")
    
    def set_user(self, user_id: str, username: str = None, email: str = None):
        """设置用户上下文"""
        if self.client:
            user_data = {'id': user_id}
            if username:
                user_data['username'] = username
            if email:
                user_data['email'] = email
            self.client.set_user(user_data)
    
    def set_tag(self, key: str, value: str):
        """设置标签"""
        if self.client:
            self.client.set_tag(key, value)


class PrometheusMetrics:
    """Prometheus 指标暴露 API"""
    
    def __init__(self, port: int = 8000):
        self.port = port
        self.registry = None
    
    def init(self):
        """初始化 Prometheus"""
        try:
            from prometheus_client import start_http_server, Counter, Histogram, Gauge
            
            self.registry = {
                'request_counter': Counter(
                    'app_requests_total',
                    'Total requests',
                    ['method', 'endpoint', 'status']
                ),
                'request_duration': Histogram(
                    'app_request_duration_seconds',
                    'Request duration',
                    ['endpoint']
                ),
                'active_connections': Gauge(
                    'app_active_connections',
                    'Active connections'
                )
            }
            
            start_http_server(self.port)
            print(f"✓ Prometheus 指标已暴露：http://localhost:{self.port}/metrics")
            
        except ImportError:
            print("✗ 未安装 prometheus-client，请运行：pip install prometheus-client")
        except Exception as e:
            print(f"✗ Prometheus 初始化失败：{e}")
    
    def inc_request(self, method: str, endpoint: str, status: int):
        """增加请求计数"""
        if self.registry:
            self.registry['request_counter'].labels(
                method=method,
                endpoint=endpoint,
                status=status
            ).inc()
    
    def observe_duration(self, endpoint: str, duration: float):
        """记录请求耗时"""
        if self.registry:
            self.registry['request_duration'].labels(
                endpoint=endpoint
            ).observe(duration)
    
    def set_active_connections(self, count: int):
        """设置活跃连接数"""
        if self.registry:
            self.registry['active_connections'].set(count)


# 使用示例
if __name__ == "__main__":
    # 结构化日志
    logger = StructuredLogger("myapp", output_file="app.log")
    logger.info("应用启动", version="1.0.0", environment="development")
    logger.warning("内存使用率超过 80%", memory_usage=85)
    logger.error("数据库连接失败", database="postgres", exc_info=True)
    
    # 性能监控
    monitor = PerformanceMonitor(logger)
    
    @monitor.track_function
    def process_data():
        time.sleep(0.5)
        return "done"
    
    process_data()
    
    with monitor.track_time("数据导出"):
        time.sleep(0.3)
    
    # 错误追踪（需要配置 DSN）
    # sentry = SentryErrorTracker(dsn="https://xxx@sentry.io/xxx")
    # sentry.init()
    # sentry.set_user(user_id="123", username="zacon")
    # 
    # try:
    #     raise ValueError("测试错误")
    # except Exception as e:
    #     sentry.capture_exception(e, custom_field="value")
    
    # Prometheus 指标（需要安装 prometheus-client）
    # prom = PrometheusMetrics(port=8000)
    # prom.init()
    # prom.inc_request("GET", "/api/users", 200)
    # prom.observe_duration("/api/users", 0.15)

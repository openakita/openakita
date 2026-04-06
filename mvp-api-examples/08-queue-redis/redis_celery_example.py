# Redis/Celery 消息队列 API 集成示例
# 适用于 MVP 异步任务、消息队列、缓存

import os
import json
import redis
from typing import Any, Optional
from celery import Celery
from celery.schedules import crontab


# ============== Redis 客户端 ==============

class RedisClient:
    """Redis 客户端封装（缓存 + 简单队列）"""
    
    def __init__(self):
        self.host = os.getenv("REDIS_HOST", "localhost")
        self.port = int(os.getenv("REDIS_PORT", 6379))
        self.db = int(os.getenv("REDIS_DB", 0))
        self.password = os.getenv("REDIS_PASSWORD", None)
        
        self.client = redis.Redis(
            host=self.host,
            port=self.port,
            db=self.db,
            password=self.password,
            decode_responses=True
        )
    
    def set(self, key: str, value: Any, expire: int = None) -> dict:
        """
        设置键值
        
        Args:
            key: 键
            value: 值
            expire: 过期时间（秒）
        
        Returns:
            设置结果
        """
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            
            if expire:
                self.client.setex(key, expire, value)
            else:
                self.client.set(key, value)
            return {"success": True, "key": key}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get(self, key: str) -> dict:
        """
        获取值
        
        Args:
            key: 键
        
        Returns:
            值
        """
        try:
            value = self.client.get(key)
            if value is None:
                return {"success": False, "error": "Key not found"}
            
            # 尝试解析 JSON
            try:
                value = json.loads(value)
            except:
                pass
            
            return {"success": True, "value": value}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def delete(self, *keys) -> dict:
        """
        删除键
        
        Args:
            *keys: 键列表
        
        Returns:
            删除结果
        """
        try:
            count = self.client.delete(*keys)
            return {"success": True, "deleted_count": count}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def exists(self, key: str) -> dict:
        """
        检查键是否存在
        
        Args:
            key: 键
        
        Returns:
            是否存在
        """
        try:
            exists = self.client.exists(key)
            return {"success": True, "exists": bool(exists)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def enqueue(self, queue_name: str, item: Any) -> dict:
        """
        入队（简单队列）
        
        Args:
            queue_name: 队列名称
            item: 项目
        
        Returns:
            入队结果
        """
        try:
            if isinstance(item, (dict, list)):
                item = json.dumps(item)
            self.client.lpush(queue_name, item)
            return {"success": True, "queue": queue_name}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def dequeue(self, queue_name: str, timeout: int = 0) -> dict:
        """
        出队
        
        Args:
            queue_name: 队列名称
            timeout: 超时时间（0 为不等待）
        
        Returns:
            出队结果
        """
        try:
            if timeout > 0:
                result = self.client.brpop(queue_name, timeout=timeout)
                if result:
                    item = result[1]
                else:
                    return {"success": False, "error": "Timeout"}
            else:
                item = self.client.rpop(queue_name)
            
            if item is None:
                return {"success": False, "error": "Queue empty"}
            
            # 尝试解析 JSON
            try:
                item = json.loads(item)
            except:
                pass
            
            return {"success": True, "item": item}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def publish(self, channel: str, message: Any) -> dict:
        """
        发布消息（Pub/Sub）
        
        Args:
            channel: 频道
            message: 消息
        
        Returns:
            发布结果
        """
        try:
            if isinstance(message, (dict, list)):
                message = json.dumps(message)
            count = self.client.publish(channel, message)
            return {"success": True, "subscribers_count": count}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ============== Celery 配置 ==============

# Celery 配置
celery_app = Celery(
    "mvp_tasks",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
)

# 配置定时任务
celery_app.conf.beat_schedule = {
    "cleanup-every-hour": {
        "task": "tasks.cleanup_expired_data",
        "schedule": crontab(minute=0, hour="*"),
    },
    "send-daily-report": {
        "task": "tasks.send_daily_report",
        "schedule": crontab(minute=0, hour=9),
    },
}


# ============== Celery 任务示例 ==============

@celery_app.task(bind=True, max_retries=3)
def send_email_task(self, to_email: str, subject: str, content: str) -> dict:
    """
    异步发送邮件任务
    
    Args:
        to_email: 收件人
        subject: 主题
        content: 内容
    
    Returns:
        发送结果
    """
    try:
        # 这里调用实际的邮件发送 API
        # from sendgrid_example import SendGridClient
        # client = SendGridClient()
        # result = client.send_email(to_email, subject, content)
        
        # 模拟发送
        print(f"Sending email to {to_email}: {subject}")
        return {"success": True, "message_id": "mock_123"}
    except Exception as e:
        # 重试逻辑
        raise self.retry(exc=e, countdown=60)


@celery_app.task(bind=True, max_retries=3)
def process_data_task(self, data_id: str) -> dict:
    """
    异步数据处理任务
    
    Args:
        data_id: 数据 ID
    
    Returns:
        处理结果
    """
    try:
        print(f"Processing data: {data_id}")
        # 模拟处理
        import time
        time.sleep(2)
        return {"success": True, "data_id": data_id, "status": "processed"}
    except Exception as e:
        raise self.retry(exc=e, countdown=30)


@celery_app.task
def cleanup_expired_data() -> dict:
    """
    清理过期数据（定时任务）
    
    Returns:
        清理结果
    """
    try:
        print("Cleaning up expired data...")
        # 实际清理逻辑
        return {"success": True, "cleaned_count": 100}
    except Exception as e:
        return {"success": False, "error": str(e)}


@celery_app.task
def send_daily_report() -> dict:
    """
    发送日报（定时任务）
    
    Returns:
        发送结果
    """
    try:
        print("Sending daily report...")
        # 实际发送逻辑
        return {"success": True, "recipients": 10}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============== 使用示例 ==============

if __name__ == "__main__":
    # 1. Redis 缓存操作
    redis_client = RedisClient()
    
    # 设置缓存
    result = redis_client.set("user:1001", {"name": "Test", "email": "test@example.com"}, expire=3600)
    print(f"设置缓存：{result}")
    
    # 获取缓存
    result = redis_client.get("user:1001")
    print(f"获取缓存：{result}")
    
    # 2. Redis 队列操作
    result = redis_client.enqueue("task_queue", {"task": "send_email", "to": "user@example.com"})
    print(f"入队：{result}")
    
    result = redis_client.dequeue("task_queue")
    print(f"出队：{result}")
    
    # 3. Redis 发布订阅
    result = redis_client.publish("notifications", {"type": "new_user", "user_id": 1001})
    print(f"发布消息：{result}")
    
    # 4. Celery 异步任务
    # 启动 Celery Worker: celery -A redis_celery_example worker -l info
    # 启动 Celery Beat: celery -A redis_celery_example beat -l info
    
    # 触发异步任务
    # task = send_email_task.delay("user@example.com", "Welcome", "Hello!")
    # print(f"任务 ID: {task.id}")
    
    # 获取任务结果
    # result = task.get()
    # print(f"任务结果：{result}")

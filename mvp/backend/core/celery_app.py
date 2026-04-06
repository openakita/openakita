"""Celery 配置"""
import os
from celery import Celery
from celery.schedules import crontab

# Celery 配置
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")

# 创建 Celery 应用
celery_app = Celery(
    "mvp_backend",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["mvp.backend.tasks.workflow_tasks"]
)

# Celery 配置
celery_app.conf.update(
    # 任务序列化
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    
    # 时区
    timezone="Asia/Shanghai",
    enable_utc=True,
    
    # 任务追踪
    task_track_started=True,
    task_time_limit=300,  # 5 分钟超时
    task_soft_time_limit=240,
    
    # 重试配置
    task_autoretry_for=(Exception,),
    task_max_retries=3,
    task_default_retry_delay=60,
    
    # 结果过期（1 小时）
    result_expires=3600,
    
    # Worker 配置
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    
    # 定时任务
    beat_schedule={
        "cleanup-expired-tokens": {
            "task": "mvp.backend.tasks.cleanup.cleanup_expired_tokens",
            "schedule": crontab(hour=3, minute=0),  # 每天 3:00
        },
        "cleanup-old-logs": {
            "task": "mvp.backend.tasks.cleanup.cleanup_old_logs",
            "schedule": crontab(hour=4, minute=0),  # 每天 4:00
        },
    },
)

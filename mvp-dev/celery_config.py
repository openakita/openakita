"""
Celery 配置 - MVP 开发环境
支持任务优先级、失败重试、超时控制
"""
from celery import Celery
from celery.schedules import crontab
import os

# Celery 应用配置
celery_app = Celery(
    'mvp_tasks',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/1',
    include=['mvp_tasks.tasks']
)

# 任务配置
celery_app.conf.update(
    # 任务序列化
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='Asia/Shanghai',
    enable_utc=True,
    
    # 任务追踪
    task_track_started=True,
    task_time_limit=300,  # 5 分钟超时
    task_soft_time_limit=240,
    
    # 失败重试配置
    task_autoretry_for=(Exception,),
    task_retry_backoff=True,
    task_retry_backoff_max=600,
    task_max_retries=3,
    
    # 结果过期时间（秒）
    result_expires=3600,
    
    # Worker 配置
    worker_prefetch_multiplier=1,
    worker_concurrency=4,
    
    # 任务优先级队列
    task_create_missing_queues=True,
    task_queues={
        'high_priority': {
            'exchange': 'default',
            'routing_key': 'high',
        },
        'default': {
            'exchange': 'default',
            'routing_key': 'default',
        },
        'low_priority': {
            'exchange': 'default',
            'routing_key': 'low',
        },
    },
    task_default_queue='default',
    task_default_exchange='default',
    task_default_routing_key='default',
    
    # 定时任务（示例）
    beat_schedule={
        'cleanup-old-tasks': {
            'task': 'mvp_tasks.tasks.cleanup_old_results',
            'schedule': crontab(hour=3, minute=0),  # 每天凌晨 3 点
        },
    },
)

# MLflow 配置
MLFLOW_TRACKING_URI = os.getenv('MLFLOW_TRACKING_URI', 'http://localhost:5000')

# Qdrant 配置
QDRANT_HOST = os.getenv('QDRANT_HOST', 'localhost')
QDRANT_PORT = int(os.getenv('QDRANT_PORT', 6333))
QDRANT_COLLECTION = os.getenv('QDRANT_COLLECTION', 'mvp_vectors')

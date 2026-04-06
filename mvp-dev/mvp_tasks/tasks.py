"""
Celery 任务示例 - MVP 开发环境
包含 LLM 推理任务、API 调用任务等示例
"""
from celery import group
from .celery_config import celery_app, MLFLOW_TRACKING_URI
import mlflow
import time
import logging

logger = logging.getLogger(__name__)

# 配置 MLflow
mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def llm_inference_task(self, prompt: str, model: str = "gpt-3.5-turbo"):
    """
    LLM 推理任务（异步）
    
    Args:
        prompt: 用户输入
        model: 模型名称
    
    Returns:
        dict: 推理结果
    """
    task_id = self.request.id
    start_time = time.time()
    
    try:
        # 开始 MLflow 实验记录
        with mlflow.start_run(run_name=f"llm_inference_{task_id}"):
            mlflow.log_param("model", model)
            mlflow.log_param("prompt_length", len(prompt))
            
            # 模拟 LLM 推理（实际应调用 API）
            logger.info(f"Starting LLM inference with model: {model}")
            time.sleep(2)  # 模拟推理延迟
            
            # 模拟结果
            result = {
                "status": "success",
                "response": f"这是 {model} 对 '{prompt[:50]}...' 的响应",
                "tokens_used": 150,
                "latency_ms": int((time.time() - start_time) * 1000)
            }
            
            # 记录指标
            mlflow.log_metric("latency_ms", result["latency_ms"])
            mlflow.log_metric("tokens_used", result["tokens_used"])
            
            logger.info(f"Task {task_id} completed in {result['latency_ms']}ms")
            return result
            
    except Exception as e:
        logger.error(f"Task {task_id} failed: {str(e)}")
        mlflow.log_param("error", str(e))
        # 触发重试
        raise self.retry(exc=e, countdown=60)


@celery_app.task(bind=True, priority=9)
def api_integration_test(self, api_name: str, endpoint: str):
    """
    API 集成测试任务（高优先级）
    
    Args:
        api_name: API 名称
        endpoint: API 端点
    
    Returns:
        dict: 测试结果
    """
    import httpx
    
    task_id = self.request.id
    logger.info(f"Testing API: {api_name} at {endpoint}")
    
    try:
        start_time = time.time()
        
        # 模拟 API 调用
        # 实际应使用 httpx.AsyncClient() 调用真实 API
        time.sleep(1)
        
        result = {
            "api_name": api_name,
            "endpoint": endpoint,
            "status": "success",
            "response_time_ms": int((time.time() - start_time) * 1000),
            "tested_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        logger.info(f"API test {task_id} completed: {result}")
        return result
        
    except Exception as e:
        logger.error(f"API test {task_id} failed: {str(e)}")
        return {
            "api_name": api_name,
            "status": "failed",
            "error": str(e)
        }


@celery_app.task
def cleanup_old_results(days: int = 7):
    """
    清理旧任务结果（定时任务）
    
    Args:
        days: 保留天数
    """
    logger.info(f"Cleaning up task results older than {days} days")
    # 实际实现应清理 Redis/数据库中的旧数据
    return {"status": "cleaned", "days": days}


@celery_app.task
def batch_api_tests(api_list: list):
    """
    批量 API 测试（并行执行）
    
    Args:
        api_list: API 列表 [{"name": "...", "endpoint": "..."}]
    
    Returns:
        list: 测试结果
    """
    # 使用 group 并行执行多个 API 测试
    jobs = group(
        api_integration_test.s(api["name"], api["endpoint"])
        for api in api_list
    )
    results = jobs.apply_async()
    return [r.get() for r in results]

"""
Celery 异步任务集成示例
用于 MVP 后台任务处理、定时任务等场景
"""
from celery import Celery, Task
from celery.schedules import crontab
from typing import Any, Dict, Optional
import time


# ============== Celery 配置 ==============

def create_celery_app(broker_url: str = "redis://localhost:6379/0",
                      result_backend: str = "redis://localhost:6379/1") -> Celery:
    """
    创建 Celery 应用
    
    Args:
        broker_url: 消息代理 URL
        result_backend: 结果后端 URL
    
    Returns:
        Celery 应用实例
    """
    app = Celery(
        "mvp_tasks",
        broker=broker_url,
        backend=result_backend
    )
    
    # 配置
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Shanghai",
        enable_utc=True,
        task_track_started=True,
        task_time_limit=300,  # 任务超时 5 分钟
        task_soft_time_limit=240,  # 软超时 4 分钟
        worker_prefetch_multiplier=1,  # 每次只取一个任务
        result_expires=3600,  # 结果过期时间 1 小时
    )
    
    return app


# 创建 Celery 应用
celery_app = create_celery_app()


# ============== 任务定义 ==============

@celery_app.task(bind=True, max_retries=3)
def send_email_task(self, to_email: str, subject: str, body: str) -> Dict:
    """
    发送邮件任务（异步）
    
    Args:
        to_email: 收件人邮箱
        subject: 邮件主题
        body: 邮件内容
    
    Returns:
        任务结果
    """
    try:
        print(f"[任务] 开始发送邮件给 {to_email}")
        
        # 模拟邮件发送（实际项目中调用 SMTP 客户端）
        time.sleep(2)  # 模拟网络延迟
        
        print(f"[任务] 邮件发送成功：{to_email}")
        
        return {
            "success": True,
            "task": "send_email",
            "to_email": to_email,
            "subject": subject
        }
        
    except Exception as e:
        # 重试逻辑
        try:
            raise self.retry(exc=e, countdown=60)  # 60 秒后重试
        except self.MaxRetriesExceededError:
            return {
                "success": False,
                "error": f"邮件发送失败（已重试 3 次）：{str(e)}"
            }


@celery_app.task(bind=True)
def send_sms_task(self, phone_number: str, message: str) -> Dict:
    """
    发送短信任务（异步）
    """
    try:
        print(f"[任务] 开始发送短信给 {phone_number}")
        
        # 模拟短信发送
        time.sleep(1)
        
        return {
            "success": True,
            "task": "send_sms",
            "phone_number": phone_number
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


@celery_app.task(bind=True)
def process_workflow_task(self, workflow_id: str, steps: list) -> Dict:
    """
    处理工作流任务（异步）
    
    Args:
        workflow_id: 工作流 ID
        steps: 处理步骤列表
    """
    try:
        print(f"[任务] 开始处理工作流 {workflow_id}")
        
        # 更新任务状态
        self.update_state(state="PROCESSING", meta={"workflow_id": workflow_id, "current_step": 0})
        
        results = []
        for i, step in enumerate(steps):
            print(f"[任务] 执行步骤 {i+1}/{len(steps)}: {step}")
            
            # 模拟步骤执行
            time.sleep(1)
            
            # 更新进度
            self.update_state(
                state="PROCESSING", 
                meta={"workflow_id": workflow_id, "current_step": i+1, "total_steps": len(steps)}
            )
            
            results.append({"step": step, "status": "completed"})
        
        return {
            "success": True,
            "task": "process_workflow",
            "workflow_id": workflow_id,
            "steps_result": results
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


@celery_app.task
def cleanup_temp_files_task() -> Dict:
    """
    清理临时文件任务（定时任务）
    """
    try:
        print("[任务] 开始清理临时文件")
        
        # 模拟清理操作
        time.sleep(1)
        
        return {
            "success": True,
            "task": "cleanup_temp_files",
            "deleted_count": 10
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


@celery_app.task
def generate_report_task(report_type: str, params: Dict) -> Dict:
    """
    生成报告任务（耗时任务）
    
    Args:
        report_type: 报告类型
        params: 报告参数
    """
    try:
        print(f"[任务] 开始生成 {report_type} 报告")
        
        # 模拟报告生成
        time.sleep(5)
        
        return {
            "success": True,
            "task": "generate_report",
            "report_type": report_type,
            "report_url": f"/reports/{report_type}_{int(time.time())}.pdf"
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============== 定时任务配置 ==============

# 定时任务调度器配置
celery_app.conf.beat_schedule = {
    # 每天凌晨 2 点清理临时文件
    "cleanup-temp-files-daily": {
        "task": "mvp_tasks.cleanup_temp_files_task",
        "schedule": crontab(hour=2, minute=0),
    },
    
    # 每小时检查一次过期会话
    "check-expired-sessions": {
        "task": "mvp_tasks.cleanup_temp_files_task",
        "schedule": crontab(minute=0),  # 每小时
    },
    
    # 每 5 分钟发送一次系统健康检查
    "health-check": {
        "task": "mvp_tasks.cleanup_temp_files_task",
        "schedule": 300.0,  # 300 秒
    },
}


# ============== 任务工具函数 ==============

def get_task_status(task_id: str) -> Dict:
    """
    获取任务状态
    
    Args:
        task_id: 任务 ID
    
    Returns:
        任务状态信息
    """
    result = celery_app.AsyncResult(task_id)
    
    return {
        "task_id": task_id,
        "state": result.state,
        "info": result.info,
        "ready": result.ready(),
        "successful": result.successful() if result.ready() else None
    }


def revoke_task(task_id: str, terminate: bool = False) -> Dict:
    """
    撤销任务
    
    Args:
        task_id: 任务 ID
        terminate: 是否终止正在运行的任务
    """
    celery_app.control.revoke(task_id, terminate=terminate)
    
    return {
        "success": True,
        "task_id": task_id,
        "terminated": terminate
    }


# ============== 使用示例 ==============

if __name__ == "__main__":
    print("=== Celery 异步任务示例 ===")
    
    # 示例 1: 异步发送邮件
    print("\n1. 异步发送邮件")
    task = send_email_task.delay(
        to_email="user@example.com",
        subject="测试邮件",
        body="这是一封测试邮件"
    )
    print(f"任务 ID: {task.id}")
    
    # 示例 2: 异步发送短信
    print("\n2. 异步发送短信")
    task = send_sms_task.delay(
        phone_number="13800138000",
        message="测试短信"
    )
    print(f"任务 ID: {task.id}")
    
    # 示例 3: 处理工作流
    print("\n3. 处理工作流")
    task = process_workflow_task.delay(
        workflow_id="workflow_123",
        steps=["验证", "处理", "通知"]
    )
    print(f"任务 ID: {task.id}")
    
    # 示例 4: 获取任务状态
    print("\n4. 获取任务状态")
    status = get_task_status(task.id)
    print(f"状态：{status}")
    
    # 示例 5: 生成报告（耗时任务）
    print("\n5. 生成报告")
    task = generate_report_task.delay(
        report_type="monthly_sales",
        params={"month": "2026-03", "department": "all"}
    )
    print(f"任务 ID: {task.id}")

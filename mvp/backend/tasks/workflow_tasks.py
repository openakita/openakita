"""Celery 任务定义"""
from ..core.celery_app import celery_app
from sqlalchemy.orm import Session
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3)
def execute_workflow(self, instance_id: int):
    """执行工作流（异步任务）"""
    from ..database import SessionLocal
    from ..models import Workflow, WorkflowInstance, WorkflowLog
    
    db = SessionLocal()
    try:
        # 获取实例
        instance = db.query(WorkflowInstance).filter(
            WorkflowInstance.id == instance_id
        ).first()
        
        if not instance:
            logger.error(f"Instance {instance_id} not found")
            return
        
        # 获取工作流定义
        workflow = db.query(Workflow).filter(
            Workflow.id == instance.workflow_id
        ).first()
        
        if not workflow:
            logger.error(f"Workflow {instance.workflow_id} not found")
            instance.status = "failed"
            instance.error_message = "Workflow not found"
            instance.completed_at = datetime.utcnow()
            db.commit()
            return
        
        # 更新实例状态
        instance.status = "running"
        instance.started_at = datetime.utcnow()
        db.commit()
        
        # 记录开始日志
        log = WorkflowLog(
            instance_id=instance_id,
            action="start",
            message="Workflow execution started",
        )
        db.add(log)
        db.commit()
        
        # 执行节点（简化版：按顺序执行所有节点）
        nodes = workflow.nodes
        for node in nodes:
            node_id = node.get("id")
            node_type = node.get("type")
            
            # 跳过开始和结束节点
            if node_type in ["start", "end"]:
                continue
            
            # 记录节点开始
            node_log = WorkflowLog(
                instance_id=instance_id,
                node_id=node_id,
                node_type=node_type,
                action="start",
                message=f"Executing node: {node.get('label', node_id)}",
            )
            db.add(node_log)
            db.commit()
            
            try:
                # 执行节点（这里简化处理，实际需要根据节点类型调用不同执行器）
                start_time = datetime.utcnow()
                
                # TODO: 根据节点类型执行不同逻辑
                # if node_type == "action":
                #     result = execute_action_node(node, instance.input_data)
                # elif node_type == "condition":
                #     result = evaluate_condition(node, instance.input_data)
                
                # 模拟执行
                import time
                time.sleep(0.1)
                
                end_time = datetime.utcnow()
                duration_ms = (end_time - start_time).total_seconds() * 1000
                
                # 记录节点完成
                node_complete_log = WorkflowLog(
                    instance_id=instance_id,
                    node_id=node_id,
                    node_type=node_type,
                    action="complete",
                    message=f"Node completed",
                    duration_ms=duration_ms,
                )
                db.add(node_complete_log)
                db.commit()
                
            except Exception as e:
                logger.error(f"Node {node_id} failed: {str(e)}")
                
                # 记录节点错误
                error_log = WorkflowLog(
                    instance_id=instance_id,
                    node_id=node_id,
                    node_type=node_type,
                    action="error",
                    message=str(e),
                )
                db.add(error_log)
                db.commit()
                
                # 重试逻辑
                if self.request.retries < self.max_retries:
                    raise self.retry(exc=e, countdown=60)
                else:
                    # 失败
                    instance.status = "failed"
                    instance.error_message = f"Node {node_id} failed: {str(e)}"
                    instance.completed_at = datetime.utcnow()
                    db.commit()
                    return
        
        # 所有节点执行完成
        instance.status = "completed"
        instance.completed_at = datetime.utcnow()
        instance.output_data = {"result": "success"}  # TODO: 实际输出数据
        
        # 记录完成日志
        complete_log = WorkflowLog(
            instance_id=instance_id,
            action="complete",
            message="Workflow execution completed successfully",
        )
        db.add(complete_log)
        db.commit()
        
        logger.info(f"Workflow instance {instance_id} completed")
        
    except Exception as e:
        logger.error(f"Workflow execution failed: {str(e)}")
        if instance:
            instance.status = "failed"
            instance.error_message = str(e)
            instance.completed_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


@celery_app.task
def cleanup_expired_tokens():
    """清理过期的刷新令牌（定时任务）"""
    from ..database import SessionLocal
    from ..models import RefreshToken
    from datetime import datetime
    
    db = SessionLocal()
    try:
        # 删除过期令牌
        expired_count = db.query(RefreshToken).filter(
            RefreshToken.expires_at < datetime.utcnow()
        ).delete()
        
        db.commit()
        logger.info(f"Cleaned up {expired_count} expired tokens")
        
    except Exception as e:
        logger.error(f"Cleanup failed: {str(e)}")
        db.rollback()
    finally:
        db.close()


@celery_app.task
def cleanup_old_logs(days: int = 30):
    """清理旧日志（定时任务）"""
    from ..database import SessionLocal
    from ..models import WorkflowLog
    from datetime import datetime, timedelta
    
    db = SessionLocal()
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        # 删除旧日志
        deleted_count = db.query(WorkflowLog).filter(
            WorkflowLog.created_at < cutoff_date
        ).delete()
        
        db.commit()
        logger.info(f"Cleaned up {deleted_count} old logs")
        
    except Exception as e:
        logger.error(f"Log cleanup failed: {str(e)}")
        db.rollback()
    finally:
        db.close()

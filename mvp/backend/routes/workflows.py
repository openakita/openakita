"""工作流管理路由"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Annotated
from datetime import datetime

from ..database import get_db
from ..models import User, Workflow, WorkflowInstance
from ..schemas.workflow import (
    WorkflowCreate,
    WorkflowUpdate,
    WorkflowResponse,
    WorkflowRunRequest,
    WorkflowRunResponse,
    WorkflowInstanceResponse,
)
from ..routes.auth import get_current_user
from ..tasks.workflow_tasks import execute_workflow

router = APIRouter(prefix="/workflows", tags=["工作流"])


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
def create_workflow(
    workflow_data: WorkflowCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """创建工作流"""
    # 验证工作流结构
    if not workflow_data.nodes or not workflow_data.edges:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workflow must have at least one node and one edge"
        )
    
    # 检查工作流是否有开始和结束节点
    node_types = [node.type for node in workflow_data.nodes]
    if "start" not in node_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workflow must have a start node"
        )
    if "end" not in node_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workflow must have an end node"
        )
    
    # 创建工作流
    db_workflow = Workflow(
        name=workflow_data.name,
        description=workflow_data.description,
        nodes=[node.model_dump() for node in workflow_data.nodes],
        edges=[edge.model_dump() for edge in workflow_data.edges],
        created_by=current_user.id,
    )
    
    db.add(db_workflow)
    db.commit()
    db.refresh(db_workflow)
    
    return db_workflow


@router.get("", response_model=List[WorkflowResponse])
def list_workflows(
    skip: int = 0,
    limit: int = 100,
    include_templates: bool = False,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """获取工作流列表"""
    query = db.query(Workflow).filter(
        Workflow.created_by == current_user.id,
        Workflow.is_active == True
    )
    
    if not include_templates:
        query = query.filter(Workflow.is_template == False)
    
    workflows = query.offset(skip).limit(limit).all()
    return workflows


@router.get("/{workflow_id}", response_model=WorkflowResponse)
def get_workflow(
    workflow_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """获取工作流详情"""
    workflow = db.query(Workflow).filter(
        Workflow.id == workflow_id,
        Workflow.created_by == current_user.id
    ).first()
    
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found"
        )
    
    return workflow


@router.put("/{workflow_id}", response_model=WorkflowResponse)
def update_workflow(
    workflow_id: int,
    workflow_data: WorkflowUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """更新工作流"""
    workflow = db.query(Workflow).filter(
        Workflow.id == workflow_id,
        Workflow.created_by == current_user.id
    ).first()
    
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found"
        )
    
    # 更新字段
    update_data = workflow_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(workflow, field, value)
    
    # 更新版本号
    workflow.version += 1
    workflow.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(workflow)
    
    return workflow


@router.delete("/{workflow_id}")
def delete_workflow(
    workflow_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """删除工作流（软删除）"""
    workflow = db.query(Workflow).filter(
        Workflow.id == workflow_id,
        Workflow.created_by == current_user.id
    ).first()
    
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found"
        )
    
    workflow.is_active = False
    workflow.updated_at = datetime.utcnow()
    db.commit()
    
    return {"message": "Workflow deleted successfully"}


@router.post("/{workflow_id}/run", response_model=WorkflowRunResponse)
def run_workflow(
    workflow_id: int,
    run_data: WorkflowRunRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """运行工作流（异步执行）"""
    # 验证工作流存在
    workflow = db.query(Workflow).filter(
        Workflow.id == workflow_id,
        Workflow.created_by == current_user.id
    ).first()
    
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found"
        )
    
    if not workflow.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workflow is inactive"
        )
    
    # 创建执行实例
    instance = WorkflowInstance(
        workflow_id=workflow_id,
        status="pending",
        input_data=run_data.input_data,
    )
    
    db.add(instance)
    db.commit()
    db.refresh(instance)
    
    # 异步执行工作流
    execute_workflow.delay(instance.id)
    
    return WorkflowRunResponse(
        instance_id=instance.id,
        status=instance.status,
        created_at=instance.created_at,
        message="Workflow execution started"
    )


@router.get("/{workflow_id}/instances", response_model=List[WorkflowInstanceResponse])
def list_workflow_instances(
    workflow_id: int,
    skip: int = 0,
    limit: int = 100,
    status_filter: str = None,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """获取工作流执行实例列表"""
    # 验证工作流归属
    workflow = db.query(Workflow).filter(
        Workflow.id == workflow_id,
        Workflow.created_by == current_user.id
    ).first()
    
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found"
        )
    
    # 查询实例
    query = db.query(WorkflowInstance).filter(
        WorkflowInstance.workflow_id == workflow_id
    )
    
    if status_filter:
        query = query.filter(WorkflowInstance.status == status_filter)
    
    instances = query.order_by(WorkflowInstance.created_at.desc()).offset(skip).limit(limit).all()
    return instances


@router.get("/instances/{instance_id}", response_model=WorkflowInstanceResponse)
def get_workflow_instance(
    instance_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    """获取工作流实例详情"""
    instance = db.query(WorkflowInstance).filter(
        WorkflowInstance.id == instance_id
    ).first()
    
    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instance not found"
        )
    
    # 验证工作流归属
    workflow = db.query(Workflow).filter(
        Workflow.id == instance.workflow_id,
        Workflow.created_by == current_user.id
    ).first()
    
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    return instance

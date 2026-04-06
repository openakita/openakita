"""工作流相关 Pydantic 模式"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class NodeConfig(BaseModel):
    """节点配置"""
    type: str = Field(..., description="节点类型：start/end/condition/action/loop")
    label: str = Field(..., description="节点标签")
    config: Dict[str, Any] = Field(default_factory=dict, description="节点配置参数")
    position: Dict[str, float] = Field(default_factory=dict, description="画布位置")


class EdgeConfig(BaseModel):
    """连接线配置"""
    source: str = Field(..., description="源节点 ID")
    target: str = Field(..., description="目标节点 ID")
    label: Optional[str] = Field(None, description="连接线标签")
    condition: Optional[Dict[str, Any]] = Field(None, description="条件表达式")


class WorkflowCreate(BaseModel):
    """工作流创建模式"""
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    nodes: List[NodeConfig] = Field(..., description="节点列表")
    edges: List[EdgeConfig] = Field(..., description="连接线列表")
    template_id: Optional[int] = Field(None, description="模板 ID（如基于模板创建）")


class WorkflowUpdate(BaseModel):
    """工作流更新模式"""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    nodes: Optional[List[NodeConfig]] = None
    edges: Optional[List[EdgeConfig]] = None
    is_active: Optional[bool] = None


class WorkflowResponse(BaseModel):
    """工作流响应模式"""
    id: int
    name: str
    description: Optional[str]
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    is_active: bool
    is_template: bool
    created_by: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class WorkflowRunRequest(BaseModel):
    """工作流运行请求"""
    workflow_id: int
    input_data: Optional[Dict[str, Any]] = Field(default_factory=dict)


class WorkflowRunResponse(BaseModel):
    """工作流运行响应"""
    instance_id: int
    status: str  # pending/running/completed/failed
    created_at: datetime
    message: Optional[str] = None


class WorkflowInstanceResponse(BaseModel):
    """工作流实例响应"""
    id: int
    workflow_id: int
    status: str
    input_data: Optional[Dict[str, Any]]
    output_data: Optional[Dict[str, Any]]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True

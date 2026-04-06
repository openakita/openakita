"""权限相关 Pydantic 模式"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class RoleBase(BaseModel):
    """角色基础模式"""
    name: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=200)


class RoleCreate(RoleBase):
    """角色创建模式"""
    permissions: list[int] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    """角色更新模式"""
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=200)
    permissions: Optional[list[int]] = None


class RoleResponse(RoleBase):
    """角色响应模式"""
    id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class PermissionBase(BaseModel):
    """权限基础模式"""
    name: str = Field(..., min_length=1, max_length=100)
    code: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=200)


class PermissionResponse(PermissionBase):
    """权限响应模式"""
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

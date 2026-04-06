"""数据库模型定义"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, 
    String, Text, Float, JSON, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


# ──────────────────────────────────────────────────────
# 用户与认证模型
# ──────────────────────────────────────────────────────

class User(Base):
    """用户表"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(50), unique=True, index=True, nullable=False)
    full_name = Column(String(100), nullable=True)
    hashed_password = Column(String(255), nullable=False)
    
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)  # 邮箱验证
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    
    # 关联
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")
    workflows = relationship("Workflow", back_populates="creator", foreign_keys="Workflow.created_by")
    
    __table_args__ = (
        Index('idx_user_email_active', 'email', 'is_active'),
    )


class RefreshToken(Base):
    """刷新令牌表（支持 rotating 机制）"""
    __tablename__ = "refresh_tokens"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(255), unique=True, index=True, nullable=False)
    
    is_revoked = Column(Boolean, default=False, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revoke_reason = Column(String(100), nullable=True)  # revoked/used/expires
    
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_ip = Column(String(45), nullable=True)  # IPv6 最大长度
    
    # 关联
    user = relationship("User", back_populates="refresh_tokens")
    
    __table_args__ = (
        Index('idx_token_user_active', 'user_id', 'is_revoked'),
    )


# ──────────────────────────────────────────────────────
# RBAC 权限模型
# ──────────────────────────────────────────────────────

class Role(Base):
    """角色表"""
    __tablename__ = "roles"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, index=True, nullable=False)
    description = Column(String(200), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # 关联
    permissions = relationship("RolePermission", back_populates="role", cascade="all, delete-orphan")
    users = relationship("UserRole", back_populates="role", cascade="all, delete-orphan")


class Permission(Base):
    """权限表"""
    __tablename__ = "permissions"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(100), unique=True, index=True, nullable=False)  # 如：workflow.create
    description = Column(String(200), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # 关联
    roles = relationship("RolePermission", back_populates="permission", cascade="all, delete-orphan")


class RolePermission(Base):
    """角色 - 权限关联表（多对多）"""
    __tablename__ = "role_permissions"
    
    id = Column(Integer, primary_key=True, index=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    permission_id = Column(Integer, ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # 关联
    role = relationship("Role", back_populates="permissions")
    permission = relationship("Permission", back_populates="roles")
    
    __table_args__ = (
        UniqueConstraint('role_id', 'permission_id', name='uq_role_permission'),
    )


class UserRole(Base):
    """用户 - 角色关联表（多对多）"""
    __tablename__ = "user_roles"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # 关联
    user = relationship("User", back_populates="roles")
    role = relationship("Role", back_populates="users")
    
    __table_args__ = (
        UniqueConstraint('user_id', 'role_id', name='uq_user_role'),
    )


# ──────────────────────────────────────────────────────
# 工作流模型
# ──────────────────────────────────────────────────────

class Workflow(Base):
    """工作流定义表"""
    __tablename__ = "workflows"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    
    # 工作流结构（JSON 存储节点和边）
    nodes = Column(JSON, nullable=False, default=list)  # [{id, type, label, config, position}]
    edges = Column(JSON, nullable=False, default=list)  # [{id, source, target, label, condition}]
    
    is_active = Column(Boolean, default=True, nullable=False)
    is_template = Column(Boolean, default=False, nullable=False)  # 是否为模板
    version = Column(Integer, default=1, nullable=False)  # 版本号
    
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # 关联
    creator = relationship("User", foreign_keys=[created_by])
    instances = relationship("WorkflowInstance", back_populates="workflow", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_workflow_creator_active', 'created_by', 'is_active'),
    )


class WorkflowInstance(Base):
    """工作流执行实例表"""
    __tablename__ = "workflow_instances"
    
    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False)
    
    status = Column(String(20), nullable=False, default="pending")  # pending/running/completed/failed/cancelled
    input_data = Column(JSON, nullable=True)  # 输入数据
    output_data = Column(JSON, nullable=True)  # 输出数据
    
    current_node_id = Column(String(100), nullable=True)  # 当前执行节点 ID
    error_message = Column(Text, nullable=True)
    
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # 关联
    workflow = relationship("Workflow", back_populates="instances")
    logs = relationship("WorkflowLog", back_populates="instance", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_instance_workflow_status', 'workflow_id', 'status'),
        Index('idx_instance_created', 'created_at'),
    )


class WorkflowLog(Base):
    """工作流执行日志表"""
    __tablename__ = "workflow_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    instance_id = Column(Integer, ForeignKey("workflow_instances.id", ondelete="CASCADE"), nullable=False)
    
    node_id = Column(String(100), nullable=True)  # 节点 ID
    node_type = Column(String(50), nullable=True)  # 节点类型
    action = Column(String(50), nullable=False)  # start/complete/error/retry
    
    message = Column(Text, nullable=True)
    data = Column(JSON, nullable=True)  # 详细数据
    duration_ms = Column(Float, nullable=True)  # 执行耗时（毫秒）
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # 关联
    instance = relationship("WorkflowInstance", back_populates="logs")
    
    __table_args__ = (
        Index('idx_log_instance_created', 'instance_id', 'created_at'),
    )


# ──────────────────────────────────────────────────────
# API 集成模型
# ──────────────────────────────────────────────────────

class ApiIntegration(Base):
    """API 集成配置表"""
    __tablename__ = "api_integrations"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)  # 如：DingTalk, WeCom
    provider = Column(String(50), nullable=False)  # 提供商
    
    config = Column(JSON, nullable=False)  # 配置信息（加密存储）
    is_active = Column(Boolean, default=True, nullable=False)
    
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_integration_provider', 'provider', 'is_active'),
    )


class ApiCredential(Base):
    """API 凭证表（敏感信息加密）"""
    __tablename__ = "api_credentials"
    
    id = Column(Integer, primary_key=True, index=True)
    integration_id = Column(Integer, ForeignKey("api_integrations.id", ondelete="CASCADE"), nullable=False)
    
    credential_type = Column(String(50), nullable=False)  # access_token/refresh_token/api_key
    credential_value = Column(Text, nullable=False)  # 加密存储
    
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_credential_integration', 'integration_id', 'credential_type'),
    )

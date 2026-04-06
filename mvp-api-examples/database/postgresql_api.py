# 数据库 API 示例（PostgreSQL + SQLAlchemy）
# 用于 MVP 数据持久化

from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from typing import List, Optional

# 数据库连接配置
DATABASE_URL = "postgresql://user:password@localhost:5432/mvp_db"

# 创建引擎
engine = create_engine(DATABASE_URL, echo=True)

# 创建 Session
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 基类
Base = declarative_base()

# 用户模型
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<User(username={self.username})>"

# 工作流实例模型
class WorkflowInstance(Base):
    __tablename__ = "workflow_instances"
    
    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(String, nullable=False, index=True)
    status = Column(String, default='pending')
    data = Column(String)  # JSON 字符串
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# 创建数据库表
def init_db():
    Base.metadata.create_all(bind=engine)

# 数据库操作类
class DatabaseAPI:
    def __init__(self):
        self.db = SessionLocal()
    
    def close(self):
        self.db.close()
    
    # 用户操作
    def create_user(self, username: str, email: str, password_hash: str) -> User:
        user = User(username=username, email=email, password_hash=password_hash)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user
    
    def get_user_by_id(self, user_id: int) -> Optional[User]:
        return self.db.query(User).filter(User.id == user_id).first()
    
    def get_user_by_username(self, username: str) -> Optional[User]:
        return self.db.query(User).filter(User.username == username).first()
    
    # 工作流实例操作
    def create_workflow_instance(self, workflow_id: str, data: str) -> WorkflowInstance:
        instance = WorkflowInstance(workflow_id=workflow_id, data=data)
        self.db.add(instance)
        self.db.commit()
        self.db.refresh(instance)
        return instance
    
    def update_workflow_status(self, instance_id: int, status: str) -> WorkflowInstance:
        instance = self.db.query(WorkflowInstance).filter(
            WorkflowInstance.id == instance_id
        ).first()
        
        if instance:
            instance.status = status
            instance.version += 1
            self.db.commit()
            self.db.refresh(instance)
        
        return instance
    
    def get_workflow_instance(self, instance_id: int) -> Optional[WorkflowInstance]:
        return self.db.query(WorkflowInstance).filter(
            WorkflowInstance.id == instance_id
        ).first()

# 使用示例
if __name__ == '__main__':
    # 初始化数据库
    init_db()
    
    # 创建数据库 API 实例
    db = DatabaseAPI()
    
    try:
        # 创建用户
        user = db.create_user(
            username="test_user",
            email="test@example.com",
            password_hash="hashed_password"
        )
        print(f"Created user: {user}")
        
        # 查询用户
        found_user = db.get_user_by_username("test_user")
        print(f"Found user: {found_user}")
        
        # 创建工作流实例
        workflow = db.create_workflow_instance(
            workflow_id="workflow_001",
            data='{"step": 1}'
        )
        print(f"Created workflow: {workflow}")
        
        # 更新工作流状态
        updated = db.update_workflow_status(workflow.id, "running")
        print(f"Updated workflow: {updated}")
    
    finally:
        db.close()

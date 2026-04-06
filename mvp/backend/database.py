"""数据库配置"""
import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager

# 数据库 URL（从环境变量读取）
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/openakita_mvp"
)

# 创建引擎
engine = create_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,  # 连接前验证
    pool_recycle=3600,   # 1 小时回收
    echo=os.getenv("DB_ECHO", "false").lower() == "true"  # SQL 日志
)

# 会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 基础模型类
Base = declarative_base()


def get_db():
    """获取数据库会话（依赖注入用）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context():
    """数据库会话上下文管理器"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    """初始化数据库（创建所有表）"""
    from .models import Base  # 导入所有模型
    Base.metadata.create_all(bind=engine)


def drop_db():
    """删除所有表（仅开发环境使用）"""
    from .models import Base
    Base.metadata.drop_all(bind=engine)

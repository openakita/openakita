from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 数据库配置
SQLALCHEMY_DATABASE_URL = "sqlite:///./demo.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """依赖注入：获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

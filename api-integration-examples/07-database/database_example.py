"""
API 集成示例 07: 数据库 (MySQL/PostgreSQL)
========================================
功能：连接池、CRUD 操作、事务管理
依赖：pip install sqlalchemy pymysql psycopg2-binary
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from contextlib import contextmanager
from pydantic import BaseModel

# ==================== SQLAlchemy 通用封装 ====================

class DatabaseConfig:
    """数据库配置"""
    # MySQL
    MYSQL_URL = "mysql+pymysql://user:password@localhost:3306/mydb"
    # PostgreSQL
    POSTGRES_URL = "postgresql+psycopg2://user:password@localhost:5432/mydb"
    # 连接池配置
    POOL_SIZE = 10
    MAX_OVERFLOW = 20
    POOL_TIMEOUT = 30
    POOL_RECYCLE = 3600

class Database:
    """数据库连接管理"""
    
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.engine = None
        self.SessionLocal = None
        self._init_engine()
    
    def _init_engine(self):
        """初始化数据库引擎"""
        # 实际调用
        # from sqlalchemy import create_engine
        # from sqlalchemy.orm import sessionmaker
        # self.engine = create_engine(
        #     self.db_url,
        #     pool_size=DatabaseConfig.POOL_SIZE,
        #     max_overflow=DatabaseConfig.MAX_OVERFLOW,
        #     pool_timeout=DatabaseConfig.POOL_TIMEOUT,
        #     pool_recycle=DatabaseConfig.POOL_RECYCLE
        # )
        # self.SessionLocal = sessionmaker(
        #     autocommit=False,
        #     autoflush=False,
        #     bind=self.engine
        # )
        pass
    
    @contextmanager
    def get_session(self):
        """获取数据库会话（上下文管理器）"""
        # session = self.SessionLocal()
        # try:
        #     yield session
        #     session.commit()
        # except Exception:
        #     session.rollback()
        #     raise
        # finally:
        #     session.close()
        yield MockSession()
    
    def execute_query(self, query: str, params: Dict = None) -> List[Dict]:
        """
        执行查询 SQL
        
        Args:
            query: SQL 查询语句
            params: 查询参数
            
        Returns:
            查询结果
        """
        # with self.get_session() as session:
        #     result = session.execute(text(query), params or {})
        #     return [dict(row) for row in result]
        return [{"id": 1, "name": "test", "created_at": datetime.now()}]
    
    def execute_write(self, query: str, params: Dict = None) -> Dict:
        """
        执行写入 SQL（INSERT/UPDATE/DELETE）
        
        Args:
            query: SQL 语句
            params: 参数
            
        Returns:
            执行结果
        """
        # with self.get_session() as session:
        #     result = session.execute(text(query), params or {})
        #     session.commit()
        #     return {"affected_rows": result.rowcount}
        return {"affected_rows": 1, "success": True}

class MockSession:
    """模拟 Session 用于示例"""
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass
    def execute(self, *args, **kwargs): return []
    def add(self, obj): pass
    def query(self, *args): return self
    def filter(self, *args): return self
    def first(self): return None
    def all(self): return []

# ==================== MySQL 专用封装 ====================

class MySQLDatabase(Database):
    """MySQL 数据库"""
    
    def __init__(self, host: str, port: int, user: str, 
                 password: str, database: str):
        db_url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
        super().__init__(db_url)
    
    def get_tables(self) -> List[str]:
        """获取所有表名"""
        query = "SHOW TABLES"
        result = self.execute_query(query)
        return [list(row.values())[0] for row in result]
    
    def get_table_schema(self, table_name: str) -> List[Dict]:
        """获取表结构"""
        query = f"DESCRIBE {table_name}"
        return self.execute_query(query)

# ==================== PostgreSQL 专用封装 ====================

class PostgresDatabase(Database):
    """PostgreSQL 数据库"""
    
    def __init__(self, host: str, port: int, user: str,
                 password: str, database: str):
        db_url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"
        super().__init__(db_url)
    
    def get_tables(self) -> List[str]:
        """获取所有表名"""
        query = """
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public'
        """
        result = self.execute_query(query)
        return [row["table_name"] for row in result]
    
    def get_table_schema(self, table_name: str) -> List[Dict]:
        """获取表结构"""
        query = """
            SELECT column_name, data_type, is_nullable 
            FROM information_schema.columns 
            WHERE table_name = :table_name
        """
        return self.execute_query(query, {"table_name": table_name})

# ==================== ORM 模型示例 ====================

from sqlalchemy import Column, Integer, String, DateTime, create_engine
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class User(Base):
    """用户模型"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True)
    email = Column(String(100), unique=True, index=True)
    password_hash = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

class UserRepository:
    """用户数据访问层"""
    
    def __init__(self, db: Database):
        self.db = db
    
    def create(self, username: str, email: str, 
               password_hash: str) -> Dict:
        """创建用户"""
        # 实际调用
        # with self.db.get_session() as session:
        #     user = User(
        #         username=username,
        #         email=email,
        #         password_hash=password_hash
        #     )
        #     session.add(user)
        #     session.commit()
        #     session.refresh(user)
        #     return {"id": user.id, "username": user.username}
        
        return {
            "success": True,
            "id": 1,
            "username": username,
            "email": email
        }
    
    def get_by_id(self, user_id: int) -> Optional[Dict]:
        """根据 ID 获取用户"""
        # with self.db.get_session() as session:
        #     user = session.query(User).filter(User.id == user_id).first()
        #     if user:
        #         return {
        #             "id": user.id,
        #             "username": user.username,
        #             "email": user.email
        #         }
        #     return None
        
        return {"id": user_id, "username": "testuser", "email": "test@example.com"}
    
    def get_by_username(self, username: str) -> Optional[Dict]:
        """根据用户名获取用户"""
        # with self.db.get_session() as session:
        #     user = session.query(User).filter(User.username == username).first()
        #     return {"id": user.id, "username": user.username} if user else None
        
        return {"id": 1, "username": username}
    
    def update(self, user_id: int, **kwargs) -> Dict:
        """更新用户"""
        # with self.db.get_session() as session:
        #     user = session.query(User).filter(User.id == user_id).first()
        #     if user:
        #         for key, value in kwargs.items():
        #             setattr(user, key, value)
        #         session.commit()
        #         return {"success": True}
        #     return {"success": False, "error": "User not found"}
        
        return {"success": True, "updated": kwargs}
    
    def delete(self, user_id: int) -> Dict:
        """删除用户"""
        # with self.db.get_session() as session:
        #     user = session.query(User).filter(User.id == user_id).first()
        #     if user:
        #         session.delete(user)
        #         session.commit()
        #         return {"success": True}
        #     return {"success": False, "error": "User not found"}
        
        return {"success": True, "deleted_id": user_id}

# ==================== 事务管理示例 ====================

class TransactionManager:
    """事务管理器"""
    
    def __init__(self, db: Database):
        self.db = db
    
    def transfer_money(self, from_user_id: int, to_user_id: int, 
                      amount: float) -> Dict:
        """
        转账操作（事务示例）
        
        Args:
            from_user_id: 转出用户 ID
            to_user_id: 转入用户 ID
            amount: 金额
            
        Returns:
            交易结果
        """
        # 实际调用
        # with self.db.get_session() as session:
        #     try:
        #         # 检查余额
        #         from_user = session.query(User).filter(
        #             User.id == from_user_id
        #         ).with_for_update().first()
        #         
        #         if from_user.balance < amount:
        #             return {"success": False, "error": "Insufficient balance"}
        #         
        #         # 扣款
        #         from_user.balance -= amount
        #         
        #         # 入账
        #         to_user = session.query(User).filter(
        #             User.id == to_user_id
        #         ).with_for_update().first()
        #         to_user.balance += amount
        #         
        #         # 记录交易
        #         transaction = Transaction(
        #             from_user_id=from_user_id,
        #             to_user_id=to_user_id,
        #             amount=amount
        #         )
        #         session.add(transaction)
        #         
        #         session.commit()
        #         return {"success": True, "transaction_id": transaction.id}
        #     except Exception as e:
        #         session.rollback()
        #         return {"success": False, "error": str(e)}
        
        return {
            "success": True,
            "transaction_id": 12345,
            "from": from_user_id,
            "to": to_user_id,
            "amount": amount
        }

# ==================== 使用示例 ====================

if __name__ == "__main__":
    # MySQL 示例
    mysql_db = MySQLDatabase(
        host="localhost",
        port=3306,
        user="root",
        password="password",
        database="mydb"
    )
    
    # PostgreSQL 示例
    postgres_db = PostgresDatabase(
        host="localhost",
        port=5432,
        user="postgres",
        password="password",
        database="mydb"
    )
    
    # CRUD 操作示例
    user_repo = UserRepository(mysql_db)
    
    # 创建用户
    result = user_repo.create("testuser", "test@example.com", "hashed_password")
    print(f"创建用户：{result}")
    
    # 查询用户
    user = user_repo.get_by_username("testuser")
    print(f"查询用户：{user}")
    
    # 更新用户
    result = user_repo.update(1, email="new@example.com")
    print(f"更新用户：{result}")
    
    # 事务示例
    tx_manager = TransactionManager(mysql_db)
    result = tx_manager.transfer_money(1, 2, 100.0)
    print(f"转账结果：{result}")

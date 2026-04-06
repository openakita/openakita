"""
数据库 API 集成示例代码
功能：缓存、消息队列、文档存储、数据查询
支持：Redis、MongoDB
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import json
import time
from datetime import datetime

load_dotenv()

# Redis 配置
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# MongoDB 配置
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "your-db-name")


# ============ Redis ============

class RedisClient:
    """Redis 客户端"""
    
    def __init__(self):
        self.host = REDIS_HOST
        self.port = REDIS_PORT
        self.password = REDIS_PASSWORD
        self.db = REDIS_DB
        self.connected = False
    
    def connect(self):
        """连接 Redis"""
        print(f"Redis 连接:")
        print(f"  主机：{self.host}:{self.port}")
        print(f"  数据库：{self.db}")
        print(f"  状态：已连接\n")
        self.connected = True
    
    def set(self, key: str, value: str, expire: Optional[int] = None) -> bool:
        """
        设置键值
        
        Args:
            key: 键
            value: 值
            expire: 过期时间（秒）
        
        Returns:
            是否成功
        """
        if not self.connected:
            self.connect()
        
        print(f"Redis SET:")
        print(f"  键：{key}")
        print(f"  值：{value[:50]}...")
        if expire:
            print(f"  过期：{expire}秒")
        print()
        
        return True
    
    def get(self, key: str) -> Optional[str]:
        """
        获取值
        
        Args:
            key: 键
        
        Returns:
            值
        """
        if not self.connected:
            self.connect()
        
        print(f"Redis GET:")
        print(f"  键：{key}")
        print()
        
        # 模拟返回值
        return f"value_{key}"
    
    def delete(self, *keys: str) -> int:
        """
        删除键
        
        Args:
            keys: 键列表
        
        Returns:
            删除数量
        """
        print(f"Redis DELETE:")
        print(f"  键：{keys}")
        print()
        
        return len(keys)
    
    def expire(self, key: str, seconds: int) -> bool:
        """
        设置过期时间
        
        Args:
            key: 键
            seconds: 秒数
        
        Returns:
            是否成功
        """
        print(f"Redis EXPIRE:")
        print(f"  键：{key}")
        print(f"  时间：{seconds}秒")
        print()
        
        return True
    
    def incr(self, key: str, amount: int = 1) -> int:
        """
        自增
        
        Args:
            key: 键
            amount: 增量
        
        Returns:
            新值
        """
        print(f"Redis INCR:")
        print(f"  键：{key}")
        print(f"  增量：{amount}")
        print()
        
        return 100  # 模拟返回值
    
    def hset(self, name: str, key: str, value: str) -> int:
        """
        设置哈希字段
        
        Args:
            name: 哈希名
            key: 字段名
            value: 字段值
        
        Returns:
            是否新字段
        """
        print(f"Redis HSET:")
        print(f"  哈希：{name}")
        print(f"  字段：{key}")
        print(f"  值：{value}")
        print()
        
        return 1
    
    def hgetall(self, name: str) -> Dict[str, str]:
        """
        获取所有哈希字段
        
        Args:
            name: 哈希名
        
        Returns:
            字段字典
        """
        print(f"Redis HGETALL:")
        print(f"  哈希：{name}")
        print()
        
        return {"field1": "value1", "field2": "value2"}
    
    def lpush(self, name: str, *values: str) -> int:
        """
        列表左推入
        
        Args:
            name: 列表名
            values: 值列表
        
        Returns:
            列表长度
        """
        print(f"Redis LPUSH:")
        print(f"  列表：{name}")
        print(f"  值：{values}")
        print()
        
        return len(values)
    
    def rpop(self, name: str) -> Optional[str]:
        """
        列表右弹出
        
        Args:
            name: 列表名
        
        Returns:
            值
        """
        print(f"Redis RPOP:")
        print(f"  列表：{name}")
        print()
        
        return "item_1"
    
    def publish(self, channel: str, message: str) -> int:
        """
        发布消息
        
        Args:
            channel: 频道
            message: 消息
        
        Returns:
            订阅者数量
        """
        print(f"Redis PUBLISH:")
        print(f"  频道：{channel}")
        print(f"  消息：{message}")
        print()
        
        return 5  # 模拟订阅者数量


# ============ MongoDB ============

class MongoClient:
    """MongoDB 客户端"""
    
    def __init__(self):
        self.uri = MONGODB_URI
        self.db_name = MONGODB_DB_NAME
        self.connected = False
    
    def connect(self):
        """连接 MongoDB"""
        print(f"MongoDB 连接:")
        print(f"  URI: {self.uri[:50]}...")
        print(f"  数据库：{self.db_name}")
        print(f"  状态：已连接\n")
        self.connected = True
    
    def insert_one(self, collection: str, document: dict) -> str:
        """
        插入单个文档
        
        Args:
            collection: 集合名
            document: 文档
        
        Returns:
            文档 ID
        """
        if not self.connected:
            self.connect()
        
        doc_id = f"OID_{int(time.time())}"
        
        print(f"MongoDB INSERT_ONE:")
        print(f"  集合：{collection}")
        print(f"  文档：{json.dumps(document, ensure_ascii=False)[:100]}...")
        print(f"  ID: {doc_id}")
        print()
        
        return doc_id
    
    def insert_many(self, collection: str, documents: List[dict]) -> List[str]:
        """
        批量插入
        
        Args:
            collection: 集合名
            documents: 文档列表
        
        Returns:
            文档 ID 列表
        """
        print(f"MongoDB INSERT_MANY:")
        print(f"  集合：{collection}")
        print(f"  数量：{len(documents)}")
        print()
        
        return [f"OID_{i}" for i in range(len(documents))]
    
    def find_one(
        self,
        collection: str,
        filter: dict
    ) -> Optional[dict]:
        """
        查询单个文档
        
        Args:
            collection: 集合名
            filter: 查询条件
        
        Returns:
            文档
        """
        print(f"MongoDB FIND_ONE:")
        print(f"  集合：{collection}")
        print(f"  条件：{json.dumps(filter, ensure_ascii=False)}")
        print()
        
        # 模拟返回
        return {
            "_id": "OID_123",
            "name": "张三",
            "email": "zhangsan@example.com"
        }
    
    def find(
        self,
        collection: str,
        filter: dict,
        limit: int = 10,
        skip: int = 0
    ) -> List[dict]:
        """
        查询多个文档
        
        Args:
            collection: 集合名
            filter: 查询条件
            limit: 返回数量
            skip: 跳过数量
        
        Returns:
            文档列表
        """
        print(f"MongoDB FIND:")
        print(f"  集合：{collection}")
        print(f"  条件：{json.dumps(filter, ensure_ascii=False)}")
        print(f"  限制：{limit}")
        print(f"  跳过：{skip}")
        print()
        
        # 模拟返回
        return [
            {"_id": f"OID_{i}", "name": f"用户{i}", "email": f"user{i}@example.com"}
            for i in range(limit)
        ]
    
    def update_one(
        self,
        collection: str,
        filter: dict,
        update: dict
    ) -> int:
        """
        更新单个文档
        
        Args:
            collection: 集合名
            filter: 查询条件
            update: 更新内容
        
        Returns:
            修改数量
        """
        print(f"MongoDB UPDATE_ONE:")
        print(f"  集合：{collection}")
        print(f"  条件：{json.dumps(filter, ensure_ascii=False)}")
        print(f"  更新：{json.dumps(update, ensure_ascii=False)}")
        print()
        
        return 1
    
    def delete_one(self, collection: str, filter: dict) -> int:
        """
        删除单个文档
        
        Args:
            collection: 集合名
            filter: 查询条件
        
        Returns:
            删除数量
        """
        print(f"MongoDB DELETE_ONE:")
        print(f"  集合：{collection}")
        print(f"  条件：{json.dumps(filter, ensure_ascii=False)}")
        print()
        
        return 1
    
    def create_index(
        self,
        collection: str,
        keys: List[tuple],
        unique: bool = False
    ) -> str:
        """
        创建索引
        
        Args:
            collection: 集合名
            keys: 索引键
            unique: 是否唯一
        
        Returns:
            索引名
        """
        index_name = f"idx_{keys[0][0]}"
        
        print(f"MongoDB CREATE_INDEX:")
        print(f"  集合：{collection}")
        print(f"  键：{keys}")
        print(f"  唯一：{unique}")
        print(f"  索引名：{index_name}")
        print()
        
        return index_name


# ============ 使用示例 ============

def example_database():
    """数据库 API 示例"""
    print("=== 数据库 API 示例 ===\n")
    
    # 1. Redis 基础操作
    print("1. Redis 基础操作:")
    redis = RedisClient()
    redis.connect()
    
    redis.set("user:1001:name", "张三", expire=3600)
    name = redis.get("user:1001:name")
    print(f"   获取结果：{name}\n")
    
    # 2. Redis 哈希
    print("2. Redis 哈希:")
    redis.hset("user:1001", "name", "张三")
    redis.hset("user:1001", "email", "zhangsan@example.com")
    user_data = redis.hgetall("user:1001")
    print(f"   用户数据：{user_data}\n")
    
    # 3. Redis 列表（消息队列）
    print("3. Redis 列表（消息队列）:")
    redis.lpush("queue:tasks", "task_1", "task_2", "task_3")
    task = redis.rpop("queue:tasks")
    print(f"   弹出任务：{task}\n")
    
    # 4. Redis 发布订阅
    print("4. Redis 发布订阅:")
    subscribers = redis.publish("channel:notifications", "新消息通知")
    print(f"   订阅者数量：{subscribers}\n")
    
    # 5. MongoDB 插入
    print("5. MongoDB 插入:")
    mongo = MongoClient()
    mongo.connect()
    
    doc_id = mongo.insert_one("users", {
        "name": "李四",
        "email": "lisi@example.com",
        "created_at": datetime.utcnow().isoformat()
    })
    print(f"   插入 ID: {doc_id}\n")
    
    # 6. MongoDB 批量插入
    print("6. MongoDB 批量插入:")
    docs = [
        {"name": f"用户{i}", "email": f"user{i}@example.com"}
        for i in range(5)
    ]
    ids = mongo.insert_many("users", docs)
    print(f"   插入数量：{len(ids)}\n")
    
    # 7. MongoDB 查询
    print("7. MongoDB 查询:")
    user = mongo.find_one("users", {"email": "lisi@example.com"})
    print(f"   查询结果：{user}\n")
    
    users = mongo.find("users", {}, limit=3)
    print(f"   用户列表:")
    for u in users:
        print(f"     - {u['name']} ({u['email']})")
    print()
    
    # 8. MongoDB 更新
    print("8. MongoDB 更新:")
    modified = mongo.update_one(
        "users",
        {"email": "lisi@example.com"},
        {"$set": {"phone": "13800138000"}}
    )
    print(f"   修改数量：{modified}\n")
    
    # 9. MongoDB 索引
    print("9. MongoDB 索引:")
    index_name = mongo.create_index("users", [("email", 1)], unique=True)
    print(f"   索引名：{index_name}\n")


if __name__ == "__main__":
    example_database()

"""
Redis 缓存集成示例
用于 MVP 会话管理、数据缓存等场景
"""
import redis
import json
from typing import Any, Optional, Dict, List
from datetime import timedelta


class RedisCacheClient:
    """
    Redis 缓存客户端
    
    使用场景:
    - 用户会话存储
    - 数据缓存
    - 分布式锁
    - 计数器
    - 消息队列
    """
    
    def __init__(self, host: str = "localhost", port: int = 6379,
                 db: int = 0, password: Optional[str] = None,
                 decode_responses: bool = True):
        """
        初始化 Redis 客户端
        
        Args:
            host: Redis 服务器地址
            port: Redis 端口
            db: 数据库编号
            password: 密码（可选）
            decode_responses: 是否自动解码响应
        """
        self.client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=decode_responses
        )
    
    def set(self, key: str, value: Any, 
            expire_seconds: Optional[int] = None) -> Dict:
        """
        设置缓存
        
        Args:
            key: 缓存键
            value: 缓存值（自动 JSON 序列化）
            expire_seconds: 过期时间（秒）
        
        Returns:
            设置结果
        """
        try:
            # JSON 序列化
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            
            if expire_seconds:
                self.client.setex(key, expire_seconds, value)
            else:
                self.client.set(key, value)
            
            return {"success": True, "key": key}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get(self, key: str) -> Dict:
        """
        获取缓存
        
        Args:
            key: 缓存键
        
        Returns:
            缓存值（自动 JSON 反序列化）
        """
        try:
            value = self.client.get(key)
            
            if value is None:
                return {"success": False, "error": "Key not found", "value": None}
            
            # 尝试 JSON 反序列化
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass  # 非 JSON 格式，返回原始字符串
            
            return {"success": True, "value": value}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def delete(self, key: str) -> Dict:
        """删除缓存"""
        try:
            self.client.delete(key)
            return {"success": True, "key": key}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def exists(self, key: str) -> Dict:
        """检查键是否存在"""
        try:
            exists = self.client.exists(key)
            return {"success": True, "exists": bool(exists)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def expire(self, key: str, seconds: int) -> Dict:
        """设置过期时间"""
        try:
            self.client.expire(key, seconds)
            return {"success": True, "key": key, "expire_seconds": seconds}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def incr(self, key: str, amount: int = 1) -> Dict:
        """
        自增计数器
        
        Args:
            key: 计数器键
            amount: 增量
        
        Returns:
            自增后的值
        """
        try:
            value = self.client.incr(key, amount)
            return {"success": True, "key": key, "value": value}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def decr(self, key: str, amount: int = 1) -> Dict:
        """自减计数器"""
        try:
            value = self.client.decr(key, amount)
            return {"success": True, "key": key, "value": value}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # ============== 哈希操作 ==============
    
    def hset(self, name: str, key: str, value: Any) -> Dict:
        """设置哈希字段"""
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            self.client.hset(name, key, value)
            return {"success": True, "hash": name, "field": key}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def hget(self, name: str, key: str) -> Dict:
        """获取哈希字段"""
        try:
            value = self.client.hget(name, key)
            if value is None:
                return {"success": False, "error": "Field not found"}
            
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
            
            return {"success": True, "value": value}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def hgetall(self, name: str) -> Dict:
        """获取所有哈希字段"""
        try:
            data = self.client.hgetall(name)
            return {"success": True, "data": data}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # ============== 列表操作 ==============
    
    def lpush(self, name: str, *values: Any) -> Dict:
        """列表左推入"""
        try:
            values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in values]
            length = self.client.lpush(name, *values)
            return {"success": True, "list": name, "length": length}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def rpop(self, name: str) -> Dict:
        """列表右弹出"""
        try:
            value = self.client.rpop(name)
            if value is None:
                return {"success": False, "error": "List is empty"}
            
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
            
            return {"success": True, "value": value}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # ============== 会话管理 ==============
    
    def create_session(self, user_id: str, session_data: Dict,
                       expire_seconds: int = 3600) -> Dict:
        """
        创建用户会话
        
        Args:
            user_id: 用户 ID
            session_data: 会话数据
            expire_seconds: 会话过期时间
        
        Returns:
            会话 ID
        """
        import uuid
        session_id = str(uuid.uuid4())
        key = f"session:{session_id}"
        
        session_data["user_id"] = user_id
        
        result = self.set(key, session_data, expire_seconds)
        
        if result["success"]:
            return {"success": True, "session_id": session_id, "key": key}
        return result
    
    def get_session(self, session_id: str) -> Dict:
        """获取会话"""
        return self.get(f"session:{session_id}")
    
    def delete_session(self, session_id: str) -> Dict:
        """删除会话"""
        return self.delete(f"session:{session_id}")
    
    # ============== 分布式锁 ==============
    
    def acquire_lock(self, lock_name: str, timeout: int = 10) -> Dict:
        """
        获取分布式锁
        
        Args:
            lock_name: 锁名称
            timeout: 锁超时时间（秒）
        
        Returns:
            锁标识
        """
        import uuid
        lock_id = str(uuid.uuid4())
        key = f"lock:{lock_name}"
        
        # 尝试设置锁（NX 表示仅当键不存在时设置）
        acquired = self.client.set(key, lock_id, nx=True, ex=timeout)
        
        if acquired:
            return {"success": True, "lock_id": lock_id, "key": key}
        return {"success": False, "error": "Lock already acquired"}
    
    def release_lock(self, lock_name: str, lock_id: str) -> Dict:
        """
        释放分布式锁
        
        Args:
            lock_name: 锁名称
            lock_id: 锁标识（用于验证）
        """
        key = f"lock:{lock_name}"
        
        # 验证锁标识后删除
        current_lock_id = self.client.get(key)
        if current_lock_id == lock_id:
            self.client.delete(key)
            return {"success": True, "lock_name": lock_name}
        
        return {"success": False, "error": "Lock ID mismatch"}


# ============== 使用示例 ==============
if __name__ == "__main__":
    print("=== Redis 缓存示例 ===")
    
    # 初始化客户端
    cache = RedisCacheClient(host="localhost", port=6379)
    
    # 示例 1: 设置/获取缓存
    print("\n1. 设置/获取缓存")
    cache.set("user:1001", {"name": "张三", "email": "test@example.com"}, expire_seconds=3600)
    result = cache.get("user:1001")
    print(f"结果：{result}")
    
    # 示例 2: 计数器
    print("\n2. 计数器")
    cache.incr("page:view:1001")
    cache.incr("page:view:1001")
    result = cache.get("page:view:1001")
    print(f"页面浏览量：{result}")
    
    # 示例 3: 会话管理
    print("\n3. 会话管理")
    result = cache.create_session("user_1001", {"role": "admin"}, expire_seconds=3600)
    print(f"创建会话：{result}")
    
    # 示例 4: 分布式锁
    print("\n4. 分布式锁")
    result = cache.acquire_lock("order:12345", timeout=10)
    print(f"获取锁：{result}")

"""
API 集成示例 10: Redis 和 MongoDB
"""
import redis
from pymongo import MongoClient

class RedisClient:
    """Redis 客户端"""
    def __init__(self, host='localhost', port=6379, db=0, password=None):
        self.client = redis.Redis(host=host, port=port, db=db, password=password, decode_responses=True)
    
    def set(self, key, value, expire=None):
        """设置键值"""
        if expire:
            return self.client.setex(key, expire, value)
        return self.client.set(key, value)
    
    def get(self, key):
        """获取值"""
        return self.client.get(key)
    
    def delete(self, key):
        """删除键"""
        return self.client.delete(key)
    
    def incr(self, key):
        """自增"""
        return self.client.incr(key)
    
    def expire(self, key, seconds):
        """设置过期时间"""
        return self.client.expire(key, seconds)
    
    def hset(self, name, key, value):
        """设置哈希"""
        return self.client.hset(name, key, value)
    
    def hget(self, name, key):
        """获取哈希"""
        return self.client.hget(name, key)


class MongoDBClient:
    """MongoDB 客户端"""
    def __init__(self, uri='mongodb://localhost:27017', db_name='mvp_db'):
        self.client = MongoClient(uri)
        self.db = self.client[db_name]
    
    def insert_one(self, collection, document):
        """插入文档"""
        result = self.db[collection].insert_one(document)
        return result.inserted_id
    
    def find_one(self, collection, query):
        """查询单个文档"""
        return self.db[collection].find_one(query)
    
    def find_many(self, collection, query, limit=10):
        """查询多个文档"""
        return list(self.db[collection].find(query).limit(limit))
    
    def update_one(self, collection, query, update):
        """更新文档"""
        result = self.db[collection].update_one(query, {"$set": update})
        return result.modified_count
    
    def delete_one(self, collection, query):
        """删除文档"""
        result = self.db[collection].delete_one(query)
        return result.deleted_count
    
    def count(self, collection, query=None):
        """计数"""
        return self.db[collection].count_documents(query or {})

# 使用示例
if __name__ == "__main__":
    # Redis
    # redis_client = RedisClient()
    # redis_client.set("user:1", "John", expire=3600)
    # print(redis_client.get("user:1"))
    
    # MongoDB
    # mongo = MongoDBClient()
    # mongo.insert_one("users", {"name": "John", "age": 30})
    # user = mongo.find_one("users", {"name": "John"})
    
    print("Redis/MongoDB 示例已就绪")

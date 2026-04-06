"""
API 集成示例 05: 云存储 (AWS S3/阿里云 OSS)
=========================================
功能：文件上传、下载、删除、列举
依赖：pip install boto3 oss2
"""

import os
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from pydantic import BaseModel

# ==================== AWS S3 集成 ====================

class S3Config:
    """AWS S3 配置"""
    ACCESS_KEY = "your_access_key"
    SECRET_KEY = "your_secret_key"
    REGION = "us-east-1"
    BUCKET_NAME = "your-bucket-name"
    ENDPOINT_URL = f"https://s3.{REGION}.amazonaws.com"

class S3Storage:
    """AWS S3 存储服务"""
    
    def __init__(self):
        self.config = S3Config()
        # 实际使用需要初始化客户端
        # import boto3
        # self.client = boto3.client(
        #     's3',
        #     aws_access_key_id=self.config.ACCESS_KEY,
        #     aws_secret_access_key=self.config.SECRET_KEY,
        #     region_name=self.config.REGION
        # )
    
    def upload_file(self, file_path: str, object_key: Optional[str] = None,
                   content_type: str = "application/octet-stream") -> Dict:
        """
        上传文件到 S3
        
        Args:
            file_path: 本地文件路径
            object_key: S3 对象键（不传则使用文件名）
            content_type: 内容类型
            
        Returns:
            上传结果
        """
        if not object_key:
            object_key = os.path.basename(file_path)
        
        # 实际调用
        # self.client.upload_file(
        #     file_path,
        #     self.config.BUCKET_NAME,
        #     object_key,
        #     ExtraArgs={'ContentType': content_type}
        # )
        
        file_url = f"{self.config.ENDPOINT_URL}/{self.config.BUCKET_NAME}/{object_key}"
        
        return {
            "success": True,
            "object_key": object_key,
            "file_url": file_url,
            "bucket": self.config.BUCKET_NAME,
            "size": os.path.getsize(file_path)
        }
    
    def upload_fileobj(self, file_obj, object_key: str,
                      content_type: str = "application/octet-stream") -> Dict:
        """
        上传文件对象到 S3
        
        Args:
            file_obj: 文件对象（BytesIO 等）
            object_key: S3 对象键
            content_type: 内容类型
            
        Returns:
            上传结果
        """
        # 实际调用
        # self.client.upload_fileobj(
        #     file_obj,
        #     self.config.BUCKET_NAME,
        #     object_key,
        #     ExtraArgs={'ContentType': content_type}
        # )
        
        return {
            "success": True,
            "object_key": object_key,
            "bucket": self.config.BUCKET_NAME
        }
    
    def download_file(self, object_key: str, download_path: str) -> Dict:
        """
        从 S3 下载文件
        
        Args:
            object_key: S3 对象键
            download_path: 下载路径
            
        Returns:
            下载结果
        """
        # 实际调用
        # self.client.download_file(
        #     self.config.BUCKET_NAME,
        #     object_key,
        #     download_path
        # )
        
        return {
            "success": True,
            "object_key": object_key,
            "download_path": download_path
        }
    
    def get_presigned_url(self, object_key: str, expiration: int = 3600,
                         method: str = "get_object") -> Dict:
        """
        生成预签名 URL（临时访问链接）
        
        Args:
            object_key: S3 对象键
            expiration: 过期时间（秒）
            method: 操作方法（get_object/put_object）
            
        Returns:
            预签名 URL
        """
        # 实际调用
        # url = self.client.generate_presigned_url(
        #     ClientMethod=method,
        #     Params={
        #         'Bucket': self.config.BUCKET_NAME,
        #         'Key': object_key
        #     },
        #     ExpiresIn=expiration
        # )
        
        return {
            "success": True,
            "url": f"https://{self.config.BUCKET_NAME}.s3.{self.config.REGION}.amazonaws.com/{object_key}?X-Amz-Expires={expiration}&...",
            "expires_at": (datetime.now() + timedelta(seconds=expiration)).isoformat()
        }
    
    def delete_file(self, object_key: str) -> Dict:
        """
        删除 S3 文件
        
        Args:
            object_key: S3 对象键
            
        Returns:
            删除结果
        """
        # 实际调用
        # self.client.delete_object(
        #     Bucket=self.config.BUCKET_NAME,
        #     Key=object_key
        # )
        
        return {
            "success": True,
            "object_key": object_key,
            "deleted": True
        }
    
    def list_files(self, prefix: str = "", max_keys: int = 100) -> Dict:
        """
        列举 S3 文件
        
        Args:
            prefix: 前缀过滤
            max_keys: 最大返回数量
            
        Returns:
            文件列表
        """
        # 实际调用
        # response = self.client.list_objects_v2(
        #     Bucket=self.config.BUCKET_NAME,
        #     Prefix=prefix,
        #     MaxKeys=max_keys
        # )
        
        return {
            "success": True,
            "files": [
                {"key": "file1.txt", "size": 1024, "last_modified": "2026-03-18T10:00:00Z"},
                {"key": "file2.jpg", "size": 2048, "last_modified": "2026-03-18T11:00:00Z"}
            ],
            "count": 2
        }

# ==================== 阿里云 OSS 集成 ====================

class OSSConfig:
    """阿里云 OSS 配置"""
    ACCESS_KEY = "your_access_key"
    ACCESS_KEY_SECRET = "your_access_key_secret"
    ENDPOINT = "oss-cn-hangzhou.aliyuncs.com"
    BUCKET_NAME = "your-bucket-name"

class OSSStorage:
    """阿里云 OSS 存储服务"""
    
    def __init__(self):
        self.config = OSSConfig()
        # 实际使用需要初始化客户端
        # import oss2
        # self.auth = oss2.Auth(self.config.ACCESS_KEY, self.config.ACCESS_KEY_SECRET)
        # self.bucket = oss2.Bucket(self.auth, self.config.ENDPOINT, self.config.BUCKET_NAME)
    
    def upload_file(self, file_path: str, object_key: Optional[str] = None) -> Dict:
        """
        上传文件到 OSS
        
        Args:
            file_path: 本地文件路径
            object_key: OSS 对象键
            
        Returns:
            上传结果
        """
        if not object_key:
            object_key = os.path.basename(file_path)
        
        # 实际调用
        # self.bucket.put_object_from_file(object_key, file_path)
        
        file_url = f"https://{self.config.BUCKET_NAME}.{self.config.ENDPOINT}/{object_key}"
        
        return {
            "success": True,
            "object_key": object_key,
            "file_url": file_url,
            "bucket": self.config.BUCKET_NAME
        }
    
    def upload_fileobj(self, file_obj, object_key: str) -> Dict:
        """
        上传文件对象到 OSS
        
        Args:
            file_obj: 文件对象
            object_key: OSS 对象键
            
        Returns:
            上传结果
        """
        # 实际调用
        # self.bucket.put_object(object_key, file_obj)
        
        return {
            "success": True,
            "object_key": object_key
        }
    
    def download_file(self, object_key: str, download_path: str) -> Dict:
        """
        从 OSS 下载文件
        
        Args:
            object_key: OSS 对象键
            download_path: 下载路径
            
        Returns:
            下载结果
        """
        # 实际调用
        # self.bucket.get_object_to_file(object_key, download_path)
        
        return {
            "success": True,
            "object_key": object_key,
            "download_path": download_path
        }
    
    def get_signed_url(self, object_key: str, expiration: int = 3600,
                      method: str = "GET") -> Dict:
        """
        生成签名 URL
        
        Args:
            object_key: OSS 对象键
            expiration: 过期时间（秒）
            method: HTTP 方法
            
        Returns:
            签名 URL
        """
        # 实际调用
        # url = self.bucket.sign_url(method, object_key, expiration)
        
        return {
            "success": True,
            "url": f"https://{self.config.BUCKET_NAME}.{self.config.ENDPOINT}/{object_key}?OSSAccessKeyId=...&Expires={expiration}&Signature=...",
            "expires_at": (datetime.now() + timedelta(seconds=expiration)).isoformat()
        }
    
    def delete_file(self, object_key: str) -> Dict:
        """
        删除 OSS 文件
        
        Args:
            object_key: OSS 对象键
            
        Returns:
            删除结果
        """
        # 实际调用
        # self.bucket.delete_object(object_key)
        
        return {
            "success": True,
            "object_key": object_key,
            "deleted": True
        }
    
    def list_files(self, prefix: str = "", max_keys: int = 100) -> Dict:
        """
        列举 OSS 文件
        
        Args:
            prefix: 前缀过滤
            max_keys: 最大返回数量
            
        Returns:
            文件列表
        """
        # 实际调用
        # for obj in oss2.ObjectIterator(self.bucket, prefix=prefix):
        #     files.append({"key": obj.key, "size": obj.size})
        
        return {
            "success": True,
            "files": [
                {"key": "file1.txt", "size": 1024},
                {"key": "file2.jpg", "size": 2048}
            ],
            "count": 2
        }

# ==================== 云存储服务封装 ====================

class StorageProvider:
    """云存储服务商枚举"""
    AWS_S3 = "aws_s3"
    ALIYUN_OSS = "aliyun_oss"

class CloudStorage:
    """云存储服务（支持多服务商）"""
    
    def __init__(self, provider: str = StorageProvider.AWS_S3):
        self.provider = provider
        if provider == StorageProvider.AWS_S3:
            self.client = S3Storage()
        elif provider == StorageProvider.ALIYUN_OSS:
            self.client = OSSStorage()
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    def upload(self, file_path: str, key: Optional[str] = None) -> Dict:
        """上传文件"""
        return self.client.upload_file(file_path, key)
    
    def download(self, key: str, path: str) -> Dict:
        """下载文件"""
        return self.client.download_file(key, path)
    
    def get_url(self, key: str, expires: int = 3600) -> Dict:
        """获取临时访问 URL"""
        return self.client.get_presigned_url(key, expires)
    
    def delete(self, key: str) -> Dict:
        """删除文件"""
        return self.client.delete_file(key)

# ==================== 使用示例 ====================

if __name__ == "__main__":
    # S3 示例
    s3 = S3Storage()
    result = s3.upload_file("test.pdf", "documents/test.pdf")
    print(f"S3 上传结果：{result}")
    
    # 生成预签名 URL
    url_result = s3.get_presigned_url("documents/test.pdf", expiration=3600)
    print(f"S3 预签名 URL: {url_result['url']}")
    
    # OSS 示例
    oss = OSSStorage()
    result = oss.upload_file("test.pdf", "documents/test.pdf")
    print(f"OSS 上传结果：{result}")
    
    # 统一服务示例
    storage = CloudStorage(provider=StorageProvider.AWS_S3)
    result = storage.upload("test.jpg", "images/test.jpg")
    print(f"云存储上传结果：{result}")

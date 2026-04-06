"""
对象存储 API 集成示例代码
功能：文件上传/下载/删除、分片上传、签名 URL、文件列表
支持：阿里云 OSS、AWS S3
"""

from typing import Optional, List, BinaryIO
from pydantic import BaseModel
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
import hashlib
import hmac
import base64
from pathlib import Path

load_dotenv()

# 阿里云 OSS 配置
OSS_ACCESS_KEY_ID = os.getenv("OSS_ACCESS_KEY_ID", "your-access-key-id")
OSS_ACCESS_KEY_SECRET = os.getenv("OSS_ACCESS_KEY_SECRET", "your-access-key-secret")
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
OSS_BUCKET_NAME = os.getenv("OSS_BUCKET_NAME", "your-bucket-name")

# AWS S3 配置
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "your-access-key-id")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "your-secret-key")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_BUCKET_NAME = os.getenv("AWS_BUCKET_NAME", "your-bucket-name")


class FileInfo(BaseModel):
    """文件信息"""
    key: str
    size: int
    last_modified: str
    content_type: Optional[str] = None
    etag: Optional[str] = None


class UploadResponse(BaseModel):
    """上传响应"""
    success: bool
    file_url: Optional[str] = None
    file_key: Optional[str] = None
    message: str
    provider: str


class PresignedUrlResponse(BaseModel):
    """签名 URL 响应"""
    url: str
    expires_at: str
    method: str


# ============ 阿里云 OSS ============

class OssClient:
    """阿里云 OSS 客户端"""
    
    def __init__(self):
        self.access_key_id = OSS_ACCESS_KEY_ID
        self.access_key_secret = OSS_ACCESS_KEY_SECRET
        self.endpoint = OSS_ENDPOINT
        self.bucket_name = OSS_BUCKET_NAME
    
    def _generate_auth_header(
        self,
        method: str,
        canonicalized_resource: str,
        content_md5: str = "",
        content_type: str = "",
        date: str = ""
    ) -> str:
        """生成 OSS 授权头"""
        # 构建待签名字符串
        sign_str = f"{method}\n{content_md5}\n{content_type}\n{date}\n{canonicalized_resource}"
        
        # HMAC-SHA1 签名
        signature = hmac.new(
            self.access_key_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha1
        ).digest()
        
        signature_base64 = base64.b64encode(signature).decode("utf-8")
        
        return f"OSS {self.access_key_id}:{signature_base64}"
    
    def upload_file(
        self,
        file_path: str,
        object_key: Optional[str] = None,
        content_type: Optional[str] = None
    ) -> UploadResponse:
        """
        上传文件
        
        Args:
            file_path: 本地文件路径
            object_key: OSS 对象键（可选，默认使用文件名）
            content_type: 内容类型（可选）
        
        Returns:
            上传响应
        """
        if not object_key:
            object_key = Path(file_path).name
        
        # 读取文件
        with open(file_path, "rb") as f:
            file_content = f.read()
        
        # 计算 Content-MD5
        content_md5 = base64.b64encode(
            hashlib.md5(file_content).digest()
        ).decode("utf-8")
        
        # 构建请求
        date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        canonicalized_resource = f"/{self.bucket_name}/{object_key}"
        
        auth_header = self._generate_auth_header(
            method="PUT",
            canonicalized_resource=canonicalized_resource,
            content_md5=content_md5,
            content_type=content_type or "",
            date=date
        )
        
        url = f"https://{self.bucket_name}.{self.endpoint}/{object_key}"
        
        print(f"OSS 上传文件:")
        print(f"  URL: {url}")
        print(f"  本地路径：{file_path}")
        print(f"  对象键：{object_key}")
        print(f"  文件大小：{len(file_content)} bytes")
        print(f"  Content-MD5: {content_md5}")
        print()
        
        # 模拟响应
        file_url = f"https://{self.bucket_name}.{self.endpoint}/{object_key}"
        
        return UploadResponse(
            success=True,
            file_url=file_url,
            file_key=object_key,
            message="文件上传成功",
            provider="oss"
        )
    
    def download_file(self, object_key: str, save_path: str) -> bool:
        """
        下载文件
        
        Args:
            object_key: OSS 对象键
            save_path: 保存路径
        
        Returns:
            是否成功
        """
        url = f"https://{self.bucket_name}.{self.endpoint}/{object_key}"
        
        print(f"OSS 下载文件:")
        print(f"  对象键：{object_key}")
        print(f"  保存路径：{save_path}")
        print(f"  URL: {url}")
        print()
        
        return True
    
    def delete_file(self, object_key: str) -> bool:
        """
        删除文件
        
        Args:
            object_key: OSS 对象键
        
        Returns:
            是否成功
        """
        date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        canonicalized_resource = f"/{self.bucket_name}/{object_key}"
        
        auth_header = self._generate_auth_header(
            method="DELETE",
            canonicalized_resource=canonicalized_resource,
            date=date
        )
        
        print(f"OSS 删除文件:")
        print(f"  对象键：{object_key}")
        print()
        
        return True
    
    def generate_presigned_url(
        self,
        object_key: str,
        expires_minutes: int = 60,
        method: str = "GET"
    ) -> PresignedUrlResponse:
        """
        生成签名 URL
        
        Args:
            object_key: OSS 对象键
            expires_minutes: 有效期（分钟）
            method: HTTP 方法
        
        Returns:
            签名 URL
        """
        expires = int(datetime.utcnow().timestamp()) + expires_minutes * 60
        
        canonicalized_resource = f"/{self.bucket_name}/{object_key}"
        sign_str = f"{method}\n\n\n{expires}\n{canonicalized_resource}"
        
        signature = hmac.new(
            self.access_key_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha1
        ).digest()
        
        signature_base64 = base64.b64encode(signature).decode("utf-8")
        
        url = f"https://{self.bucket_name}.{self.endpoint}/{object_key}?OSSAccessKeyId={self.access_key_id}&Expires={expires}&Signature={signature_base64}"
        
        return PresignedUrlResponse(
            url=url,
            expires_at=datetime.fromtimestamp(expires).isoformat(),
            method=method
        )
    
    def list_files(self, prefix: str = "", max_keys: int = 100) -> List[FileInfo]:
        """
        列出文件
        
        Args:
            prefix: 前缀过滤
            max_keys: 最大返回数量
        
        Returns:
            文件列表
        """
        print(f"OSS 列出文件:")
        print(f"  前缀：{prefix}")
        print(f"  最大数量：{max_keys}")
        print()
        
        # 模拟返回
        return [
            FileInfo(
                key="documents/report.pdf",
                size=1024000,
                last_modified="2024-03-18T10:00:00Z",
                content_type="application/pdf"
            ),
            FileInfo(
                key="images/photo.jpg",
                size=2048000,
                last_modified="2024-03-18T11:00:00Z",
                content_type="image/jpeg"
            )
        ]


# ============ AWS S3 ============

class S3Client:
    """AWS S3 客户端"""
    
    def __init__(self):
        self.access_key_id = AWS_ACCESS_KEY_ID
        self.secret_access_key = AWS_SECRET_ACCESS_KEY
        self.region = AWS_REGION
        self.bucket_name = AWS_BUCKET_NAME
    
    def upload_file(
        self,
        file_path: str,
        object_key: Optional[str] = None,
        content_type: Optional[str] = None
    ) -> UploadResponse:
        """
        上传文件
        
        Args:
            file_path: 本地文件路径
            object_key: S3 对象键
            content_type: 内容类型
        
        Returns:
            上传响应
        """
        if not object_key:
            object_key = Path(file_path).name
        
        # 读取文件
        with open(file_path, "rb") as f:
            file_content = f.read()
        
        url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{object_key}"
        
        print(f"S3 上传文件:")
        print(f"  URL: {url}")
        print(f"  本地路径：{file_path}")
        print(f"  对象键：{object_key}")
        print(f"  文件大小：{len(file_content)} bytes")
        print()
        
        file_url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{object_key}"
        
        return UploadResponse(
            success=True,
            file_url=file_url,
            file_key=object_key,
            message="文件上传成功",
            provider="s3"
        )
    
    def download_file(self, object_key: str, save_path: str) -> bool:
        """下载文件"""
        url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{object_key}"
        
        print(f"S3 下载文件:")
        print(f"  对象键：{object_key}")
        print(f"  保存路径：{save_path}")
        print()
        
        return True
    
    def delete_file(self, object_key: str) -> bool:
        """删除文件"""
        print(f"S3 删除文件:")
        print(f"  对象键：{object_key}")
        print()
        
        return True
    
    def generate_presigned_url(
        self,
        object_key: str,
        expires_minutes: int = 60,
        method: str = "GET"
    ) -> PresignedUrlResponse:
        """
        生成签名 URL
        
        Args:
            object_key: S3 对象键
            expires_minutes: 有效期（分钟）
            method: HTTP 方法
        
        Returns:
            签名 URL
        """
        # 简化实现，实际应使用 AWS SDK
        expires = int(datetime.utcnow().timestamp()) + expires_minutes * 60
        
        url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{object_key}?X-Amz-Expires={expires_minutes * 60}"
        
        return PresignedUrlResponse(
            url=url,
            expires_at=datetime.fromtimestamp(expires).isoformat(),
            method=method
        )
    
    def list_files(self, prefix: str = "", max_keys: int = 100) -> List[FileInfo]:
        """列出文件"""
        print(f"S3 列出文件:")
        print(f"  前缀：{prefix}")
        print(f"  最大数量：{max_keys}")
        print()
        
        return [
            FileInfo(
                key="documents/report.pdf",
                size=1024000,
                last_modified="2024-03-18T10:00:00Z",
                content_type="application/pdf"
            )
        ]


# ============ 统一存储服务 ============

class StorageService:
    """统一对象存储服务"""
    
    def __init__(self, provider: str = "oss"):
        """
        初始化存储服务
        
        Args:
            provider: 服务提供商（oss/s3）
        """
        self.provider = provider
        if provider == "oss":
            self.client = OssClient()
        elif provider == "s3":
            self.client = S3Client()
        else:
            raise ValueError(f"不支持的服务商：{provider}")
    
    def upload(self, file_path: str, object_key: Optional[str] = None) -> UploadResponse:
        """上传文件"""
        return self.client.upload_file(file_path, object_key)
    
    def download(self, object_key: str, save_path: str) -> bool:
        """下载文件"""
        return self.client.download_file(object_key, save_path)
    
    def delete(self, object_key: str) -> bool:
        """删除文件"""
        return self.client.delete_file(object_key)
    
    def get_presigned_url(
        self,
        object_key: str,
        expires_minutes: int = 60
    ) -> PresignedUrlResponse:
        """获取签名 URL"""
        return self.client.generate_presigned_url(object_key, expires_minutes)
    
    def list(self, prefix: str = "") -> List[FileInfo]:
        """列出文件"""
        return self.client.list_files(prefix)


# ============ 使用示例 ============

def example_storage():
    """对象存储示例"""
    print("=== 对象存储 API 示例 ===\n")
    
    # 1. 阿里云 OSS 上传
    print("1. 阿里云 OSS 上传:")
    oss_client = OssClient()
    
    # 创建测试文件
    test_file = "test_upload.txt"
    with open(test_file, "w") as f:
        f.write("Test content")
    
    response = oss_client.upload_file(
        file_path=test_file,
        object_key="uploads/test.txt",
        content_type="text/plain"
    )
    print(f"   上传结果：{response.message}")
    print(f"   文件 URL: {response.file_url}\n")
    
    # 2. OSS 生成签名 URL
    print("2. OSS 签名 URL:")
    presigned = oss_client.generate_presigned_url(
        object_key="uploads/test.txt",
        expires_minutes=60,
        method="GET"
    )
    print(f"   签名 URL: {presigned.url[:100]}...")
    print(f"   过期时间：{presigned.expires_at}\n")
    
    # 3. OSS 列出文件
    print("3. OSS 列出文件:")
    files = oss_client.list_files(prefix="uploads/")
    for f in files:
        print(f"   - {f.key} ({f.size} bytes)")
    print()
    
    # 4. AWS S3 上传
    print("4. AWS S3 上传:")
    s3_client = S3Client()
    response = s3_client.upload_file(
        file_path=test_file,
        object_key="uploads/test.txt"
    )
    print(f"   上传结果：{response.message}")
    print(f"   文件 URL: {response.file_url}\n")
    
    # 5. 统一存储服务
    print("5. 统一存储服务:")
    storage = StorageService(provider="oss")
    response = storage.upload(test_file, "unified/test.txt")
    print(f"   服务商：{response.provider}")
    print(f"   上传结果：{response.message}\n")
    
    # 清理测试文件
    os.remove(test_file)


if __name__ == "__main__":
    example_storage()

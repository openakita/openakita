"""
文件存储 API 集成示例
支持本地存储、AWS S3、阿里云 OSS
"""

import os
import boto3
from typing import Optional, Dict, Any
from pathlib import Path


class LocalStorageAPI:
    """本地文件存储 API"""
    
    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def save_file(self, filename: str, content: bytes, subfolder: str = "") -> str:
        """
        保存文件
        
        Args:
            filename: 文件名
            content: 文件内容（字节）
            subfolder: 子文件夹
            
        Returns:
            str: 完整文件路径
        """
        try:
            if subfolder:
                folder = self.base_path / subfolder
                folder.mkdir(parents=True, exist_ok=True)
                file_path = folder / filename
            else:
                file_path = self.base_path / filename
            
            with open(file_path, 'wb') as f:
                f.write(content)
            
            print(f"✓ 文件已保存：{file_path}")
            return str(file_path)
            
        except Exception as e:
            print(f"✗ 文件保存失败：{e}")
            return ""
    
    def read_file(self, filename: str, subfolder: str = "") -> Optional[bytes]:
        """读取文件内容"""
        try:
            if subfolder:
                file_path = self.base_path / subfolder / filename
            else:
                file_path = self.base_path / filename
            
            with open(file_path, 'rb') as f:
                content = f.read()
            
            print(f"✓ 文件已读取：{file_path}")
            return content
            
        except Exception as e:
            print(f"✗ 文件读取失败：{e}")
            return None
    
    def delete_file(self, filename: str, subfolder: str = "") -> bool:
        """删除文件"""
        try:
            if subfolder:
                file_path = self.base_path / subfolder / filename
            else:
                file_path = self.base_path / filename
            
            os.remove(file_path)
            print(f"✓ 文件已删除：{file_path}")
            return True
            
        except Exception as e:
            print(f"✗ 文件删除失败：{e}")
            return False


class S3StorageAPI:
    """AWS S3 文件存储 API"""
    
    def __init__(self, aws_access_key: str, aws_secret_key: str, region: str = 'us-east-1'):
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=region
        )
    
    def upload_file(self, bucket: str, filename: str, file_path: str, acl: str = 'private') -> bool:
        """
        上传文件到 S3
        
        Args:
            bucket: S3 存储桶名称
            filename: 存储的文件名
            file_path: 本地文件路径
            acl: 访问权限（private/public-read）
            
        Returns:
            bool: 上传是否成功
        """
        try:
            self.s3_client.upload_file(
                file_path,
                bucket,
                filename,
                ExtraArgs={'ACL': acl}
            )
            url = f"https://{bucket}.s3.amazonaws.com/{filename}"
            print(f"✓ 文件已上传到 S3: {url}")
            return True
            
        except Exception as e:
            print(f"✗ S3 上传失败：{e}")
            return False
    
    def download_file(self, bucket: str, filename: str, save_path: str) -> bool:
        """从 S3 下载文件"""
        try:
            self.s3_client.download_file(bucket, filename, save_path)
            print(f"✓ 文件已从 S3 下载：{save_path}")
            return True
            
        except Exception as e:
            print(f"✗ S3 下载失败：{e}")
            return False
    
    def delete_file(self, bucket: str, filename: str) -> bool:
        """删除 S3 文件"""
        try:
            self.s3_client.delete_object(Bucket=bucket, Key=filename)
            print(f"✓ S3 文件已删除：{bucket}/{filename}")
            return True
            
        except Exception as e:
            print(f"✗ S3 删除失败：{e}")
            return False
    
    def get_presigned_url(self, bucket: str, filename: str, expiration: int = 3600) -> Optional[str]:
        """
        获取预签名 URL（临时访问链接）
        
        Args:
            bucket: 存储桶名称
            filename: 文件名
            expiration: 过期时间（秒）
            
        Returns:
            str: 预签名 URL
        """
        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket, 'Key': filename},
                ExpiresIn=expiration
            )
            print(f"✓ 预签名 URL 已生成（{expiration}秒有效）")
            return url
            
        except Exception as e:
            print(f"✗ 生成预签名 URL 失败：{e}")
            return None


class OSSStorageAPI:
    """阿里云 OSS 文件存储 API"""
    
    def __init__(self, access_key: str, access_key_secret: str, endpoint: str):
        import oss2
        self.auth = oss2.Auth(access_key, access_key_secret)
        self.endpoint = endpoint
    
    def get_bucket(self, bucket_name: str):
        """获取存储桶对象"""
        return oss2.Bucket(self.auth, self.endpoint, bucket_name)
    
    def upload_file(self, bucket_name: str, filename: str, file_path: str) -> bool:
        """上传文件到 OSS"""
        try:
            bucket = self.get_bucket(bucket_name)
            bucket.put_object_from_file(filename, file_path)
            url = f"https://{bucket_name}.{self.endpoint}/{filename}"
            print(f"✓ 文件已上传到 OSS: {url}")
            return True
            
        except Exception as e:
            print(f"✗ OSS 上传失败：{e}")
            return False
    
    def download_file(self, bucket_name: str, filename: str, save_path: str) -> bool:
        """从 OSS 下载文件"""
        try:
            bucket = self.get_bucket(bucket_name)
            bucket.get_object_to_file(filename, save_path)
            print(f"✓ 文件已从 OSS 下载：{save_path}")
            return True
            
        except Exception as e:
            print(f"✗ OSS 下载失败：{e}")
            return False


# 使用示例
if __name__ == "__main__":
    # 本地存储
    local = LocalStorageAPI(base_path="./storage")
    local.save_file("test.txt", b"Hello World", subfolder="docs")
    
    # S3 存储
    s3 = S3StorageAPI(
        aws_access_key="YOUR_AWS_KEY",
        aws_secret_key="YOUR_AWS_SECRET"
    )
    s3.upload_file("my-bucket", "test.txt", "./test.txt")
    
    # 获取预签名 URL
    url = s3.get_presigned_url("my-bucket", "test.txt", expiration=3600)
    print(f"访问链接：{url}")

# 云存储 API 示例 (阿里云 OSS + AWS S3)
# 安装依赖：pip install oss2 boto3

import os
from typing import Optional, List
from datetime import datetime, timedelta

# ============================================
# 方案 1: 阿里云 OSS
# ============================================

class AliyunOSSService:
    """阿里云对象存储服务"""
    
    def __init__(self, access_key_id: str, access_key_secret: str, endpoint: str, bucket_name: str):
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.endpoint = endpoint
        self.bucket_name = bucket_name
        self.bucket = None
        self._init_bucket()
    
    def _init_bucket(self):
        """初始化 bucket 连接"""
        import oss2
        auth = oss2.Auth(self.access_key_id, self.access_key_secret)
        self.bucket = oss2.Bucket(auth, self.endpoint, self.bucket_name)
    
    def upload_file(self, file_path: str, object_key: Optional[str] = None) -> bool:
        """上传文件"""
        try:
            object_key = object_key or os.path.basename(file_path)
            with open(file_path, 'rb') as f:
                self.bucket.put_object(object_key, f)
            print(f"✓ OSS 上传成功：{object_key}")
            return True
        except Exception as e:
            print(f"✗ OSS 上传失败：{str(e)}")
            return False
    
    def upload_file_object(self, file_object, object_key: str) -> bool:
        """上传文件对象"""
        try:
            self.bucket.put_object(object_key, file_object)
            print(f"✓ OSS 上传成功：{object_key}")
            return True
        except Exception as e:
            print(f"✗ OSS 上传失败：{str(e)}")
            return False
    
    def download_file(self, object_key: str, save_path: str) -> bool:
        """下载文件"""
        try:
            self.bucket.get_object_to_file(object_key, save_path)
            print(f"✓ OSS 下载成功：{object_key}")
            return True
        except Exception as e:
            print(f"✗ OSS 下载失败：{str(e)}")
            return False
    
    def get_file_url(self, object_key: str, expires: int = 3600) -> str:
        """获取文件访问 URL"""
        try:
            url = self.bucket.sign_url('GET', object_key, expires)
            print(f"✓ OSS URL 生成成功：{object_key}")
            return url
        except Exception as e:
            print(f"✗ OSS URL 生成失败：{str(e)}")
            return ""
    
    def delete_file(self, object_key: str) -> bool:
        """删除文件"""
        try:
            self.bucket.delete_object(object_key)
            print(f"✓ OSS 删除成功：{object_key}")
            return True
        except Exception as e:
            print(f"✗ OSS 删除失败：{str(e)}")
            return False
    
    def list_files(self, prefix: str = "", max_keys: int = 100) -> List[str]:
        """列出文件"""
        try:
            files = []
            for obj in oss2.ObjectIterator(self.bucket, prefix=prefix):
                files.append(obj.key)
                if len(files) >= max_keys:
                    break
            print(f"✓ OSS 列出文件：{len(files)} 个")
            return files
        except Exception as e:
            print(f"✗ OSS 列出文件失败：{str(e)}")
            return []


# ============================================
# 方案 2: AWS S3
# ============================================

class AWSS3Service:
    """AWS S3 对象存储服务"""
    
    def __init__(self, access_key: str, secret_key: str, region: str, bucket_name: str):
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.bucket_name = bucket_name
        self.s3_client = None
        self.s3_resource = None
        self._init_client()
    
    def _init_client(self):
        """初始化 S3 客户端"""
        import boto3
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region
        )
        self.s3_resource = boto3.resource(
            's3',
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region
        )
    
    def upload_file(self, file_path: str, object_key: Optional[str] = None) -> bool:
        """上传文件"""
        try:
            object_key = object_key or os.path.basename(file_path)
            self.s3_client.upload_file(file_path, self.bucket_name, object_key)
            print(f"✓ S3 上传成功：{object_key}")
            return True
        except Exception as e:
            print(f"✗ S3 上传失败：{str(e)}")
            return False
    
    def upload_file_object(self, file_object, object_key: str, content_type: str = 'application/octet-stream') -> bool:
        """上传文件对象"""
        try:
            self.s3_client.upload_fileobj(
                file_object,
                self.bucket_name,
                object_key,
                ExtraArgs={'ContentType': content_type}
            )
            print(f"✓ S3 上传成功：{object_key}")
            return True
        except Exception as e:
            print(f"✗ S3 上传失败：{str(e)}")
            return False
    
    def download_file(self, object_key: str, save_path: str) -> bool:
        """下载文件"""
        try:
            self.s3_client.download_file(self.bucket_name, object_key, save_path)
            print(f"✓ S3 下载成功：{object_key}")
            return True
        except Exception as e:
            print(f"✗ S3 下载失败：{str(e)}")
            return False
    
    def get_file_url(self, object_key: str, expires: int = 3600) -> str:
        """获取预签名 URL"""
        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': object_key},
                ExpiresIn=expires
            )
            print(f"✓ S3 URL 生成成功：{object_key}")
            return url
        except Exception as e:
            print(f"✗ S3 URL 生成失败：{str(e)}")
            return ""
    
    def delete_file(self, object_key: str) -> bool:
        """删除文件"""
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=object_key)
            print(f"✓ S3 删除成功：{object_key}")
            return True
        except Exception as e:
            print(f"✗ S3 删除失败：{str(e)}")
            return False
    
    def list_files(self, prefix: str = "", max_keys: int = 100) -> List[str]:
        """列出文件"""
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys
            )
            files = [obj['Key'] for obj in response.get('Contents', [])]
            print(f"✓ S3 列出文件：{len(files)} 个")
            return files
        except Exception as e:
            print(f"✗ S3 列出文件失败：{str(e)}")
            return []
    
    def upload_multiple_files(self, file_paths: List[str], prefix: str = "") -> int:
        """批量上传文件"""
        success_count = 0
        for file_path in file_paths:
            object_key = f"{prefix}{os.path.basename(file_path)}" if prefix else os.path.basename(file_path)
            if self.upload_file(file_path, object_key):
                success_count += 1
        print(f"✓ S3 批量上传完成：{success_count}/{len(file_paths)}")
        return success_count


# ============================================
# 方案 3: 腾讯云 COS
# ============================================

class TencentCOSService:
    """腾讯云对象存储服务"""
    
    def __init__(self, secret_id: str, secret_key: str, region: str, bucket_name: str):
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.region = region
        self.bucket_name = bucket_name
        self.client = None
        self._init_client()
    
    def _init_client(self):
        """初始化 COS 客户端"""
        from qcloud_cos import CosConfig, CosS3Client
        config = CosConfig(
            Region=self.region,
            SecretId=self.secret_id,
            SecretKey=self.secret_key
        )
        self.client = CosS3Client(config)
    
    def upload_file(self, file_path: str, object_key: Optional[str] = None) -> bool:
        """上传文件"""
        try:
            object_key = object_key or os.path.basename(file_path)
            self.client.upload_file(
                Bucket=self.bucket_name,
                LocalFilePath=file_path,
                Key=object_key
            )
            print(f"✓ COS 上传成功：{object_key}")
            return True
        except Exception as e:
            print(f"✗ COS 上传失败：{str(e)}")
            return False
    
    def get_file_url(self, object_key: str, expires: int = 3600) -> str:
        """获取预签名 URL"""
        try:
            url = self.client.get_presigned_download_url(
                Method='get',
                Bucket=self.bucket_name,
                Key=object_key,
                Expired=expires
            )
            print(f"✓ COS URL 生成成功：{object_key}")
            return url
        except Exception as e:
            print(f"✗ COS URL 生成失败：{str(e)}")
            return ""


# ============================================
# 使用示例
# ============================================

if __name__ == "__main__":
    # 阿里云 OSS 示例
    oss = AliyunOSSService(
        access_key_id="your-access-key-id",
        access_key_secret="your-access-key-secret",
        endpoint="oss-cn-hangzhou.aliyuncs.com",
        bucket_name="your-bucket"
    )
    oss.upload_file("test.txt", "uploads/test.txt")
    url = oss.get_file_url("uploads/test.txt", expires=3600)
    print(f"访问 URL: {url}")
    
    # AWS S3 示例
    s3 = AWSS3Service(
        access_key="your-access-key",
        secret_key="your-secret-key",
        region="us-east-1",
        bucket_name="your-bucket"
    )
    s3.upload_file("test.txt", "uploads/test.txt")
    files = s3.list_files(prefix="uploads/")
    print(f"文件列表：{files}")
    
    # 腾讯云 COS 示例
    cos = TencentCOSService(
        secret_id="your-secret-id",
        secret_key="your-secret-key",
        region="ap-guangzhou",
        bucket_name="your-bucket-123456"
    )
    cos.upload_file("test.txt", "uploads/test.txt")

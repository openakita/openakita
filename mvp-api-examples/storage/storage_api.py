# 文件存储 API 示例（AWS S3/阿里云 OSS）
# 用于 MVP 文件上传下载

import os
from typing import Optional, List
from datetime import timedelta

class StorageClient:
    """云存储客户端"""
    
    def __init__(self, provider: str = 's3'):
        self.provider = provider
        
        if provider == 's3':
            import boto3
            self.client = boto3.client(
                's3',
                aws_access_key_id=os.getenv('AWS_ACCESS_KEY', 'your-key'),
                aws_secret_access_key=os.getenv('AWS_SECRET_KEY', 'your-secret'),
                region_name=os.getenv('AWS_REGION', 'us-east-1')
            )
            self.bucket = os.getenv('AWS_S3_BUCKET', 'your-bucket')
        elif provider == 'oss':
            import oss2
            auth = oss2.Auth(
                os.getenv('OSS_ACCESS_KEY', 'your-key'),
                os.getenv('OSS_SECRET_KEY', 'your-secret')
            )
            self.bucket = oss2.Bucket(
                auth,
                os.getenv('OSS_ENDPOINT', 'oss-cn-hangzhou.aliyuncs.com'),
                os.getenv('OSS_BUCKET', 'your-bucket')
            )
    
    def upload_file(self, file_path: str, object_name: str) -> bool:
        """上传文件"""
        if self.provider == 's3':
            return self._upload_s3(file_path, object_name)
        elif self.provider == 'oss':
            return self._upload_oss(file_path, object_name)
    
    def _upload_s3(self, file_path: str, object_name: str) -> bool:
        """S3 上传"""
        try:
            self.client.upload_file(file_path, self.bucket, object_name)
            return True
        except Exception as e:
            print(f"S3 Upload Error: {e}")
            return False
    
    def _upload_oss(self, file_path: str, object_name: str) -> bool:
        """OSS 上传"""
        try:
            self.bucket.put_object_from_file(object_name, file_path)
            return True
        except Exception as e:
            print(f"OSS Upload Error: {e}")
            return False
    
    def download_file(self, object_name: str, file_path: str) -> bool:
        """下载文件"""
        if self.provider == 's3':
            return self._download_s3(object_name, file_path)
        elif self.provider == 'oss':
            return self._download_oss(object_name, file_path)
    
    def _download_s3(self, object_name: str, file_path: str) -> bool:
        """S3 下载"""
        try:
            self.client.download_file(self.bucket, object_name, file_path)
            return True
        except Exception as e:
            print(f"S3 Download Error: {e}")
            return False
    
    def _download_oss(self, object_name: str, file_path: str) -> bool:
        """OSS 下载"""
        try:
            self.bucket.get_object_to_file(object_name, file_path)
            return True
        except Exception as e:
            print(f"OSS Download Error: {e}")
            return False
    
    def get_presigned_url(self, object_name: str, expires_in: int = 3600) -> Optional[str]:
        """生成预签名 URL（临时访问链接）"""
        if self.provider == 's3':
            try:
                url = self.client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': self.bucket, 'Key': object_name},
                    ExpiresIn=expires_in
                )
                return url
            except Exception as e:
                print(f"S3 URL Error: {e}")
                return None
        elif self.provider == 'oss':
            # OSS 签名 URL
            from oss2 import make_url
            url = self.bucket.sign_url('GET', object_name, expires_in)
            return url
    
    def delete_file(self, object_name: str) -> bool:
        """删除文件"""
        if self.provider == 's3':
            try:
                self.client.delete_object(Bucket=self.bucket, Key=object_name)
                return True
            except Exception as e:
                print(f"S3 Delete Error: {e}")
                return False
        elif self.provider == 'oss':
            try:
                self.bucket.delete_object(object_name)
                return True
            except Exception as e:
                print(f"OSS Delete Error: {e}")
                return False
    
    def list_files(self, prefix: str = '') -> List[str]:
        """列出文件"""
        if self.provider == 's3':
            try:
                response = self.client.list_objects_v2(
                    Bucket=self.bucket,
                    Prefix=prefix
                )
                return [obj['Key'] for obj in response.get('Contents', [])]
            except Exception as e:
                print(f"S3 List Error: {e}")
                return []
        elif self.provider == 'oss':
            try:
                return [obj.key for obj in self.bucket.list_objects(prefix=prefix)]
            except Exception as e:
                print(f"OSS List Error: {e}")
                return []

# 使用示例
if __name__ == '__main__':
    # 初始化客户端
    storage = StorageClient(provider='s3')
    
    # 上传文件
    success = storage.upload_file('local_file.txt', 'uploads/local_file.txt')
    print(f"Upload: {success}")
    
    # 获取临时访问链接
    url = storage.get_presigned_url('uploads/local_file.txt', expires_in=3600)
    print(f"URL: {url}")
    
    # 列出文件
    files = storage.list_files('uploads/')
    print(f"Files: {files}")

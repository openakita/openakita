# AWS S3 文件存储 API 集成示例
# 适用于 MVP 文件上传、下载、管理

import os
import boto3
from botocore.exceptions import ClientError
from typing import Optional
import uuid


class S3Client:
    """AWS S3 客户端封装"""
    
    def __init__(self):
        self.access_key = os.getenv("AWS_ACCESS_KEY_ID", "your-access-key")
        self.secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "your-secret-key")
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.bucket_name = os.getenv("AWS_S3_BUCKET", "your-bucket-name")
        
        self.s3_client = boto3.client(
            "s3",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region
        )
        self.s3_resource = boto3.resource(
            "s3",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region
        )
    
    def upload_file(self, file_path: str, object_name: str = None, acl: str = "private") -> dict:
        """
        上传文件
        
        Args:
            file_path: 本地文件路径
            object_name: S3 对象名称（可选，默认使用文件名）
            acl: 访问控制（private, public-read, public-read-write）
        
        Returns:
            上传结果
        """
        if object_name is None:
            object_name = os.path.basename(file_path)
        
        try:
            self.s3_client.upload_file(
                file_path,
                self.bucket_name,
                object_name,
                ExtraArgs={"ACL": acl}
            )
            
            # 获取文件 URL
            if acl == "private":
                url = self.get_presigned_url(object_name)
            else:
                url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{object_name}"
            
            return {
                "success": True,
                "object_name": object_name,
                "url": url,
                "bucket": self.bucket_name
            }
        except ClientError as e:
            return {"success": False, "error": str(e)}
    
    def upload_file_obj(self, file_obj, object_name: str, content_type: str = None, acl: str = "private") -> dict:
        """
        上传文件对象（适用于内存中的文件）
        
        Args:
            file_obj: 文件对象（BytesIO 等）
            object_name: S3 对象名称
            content_type: 内容类型
            acl: 访问控制
        
        Returns:
            上传结果
        """
        try:
            extra_args = {"ACL": acl}
            if content_type:
                extra_args["ContentType"] = content_type
            
            self.s3_client.upload_fileobj(
                file_obj,
                self.bucket_name,
                object_name,
                ExtraArgs=extra_args
            )
            
            return {
                "success": True,
                "object_name": object_name,
                "url": self.get_presigned_url(object_name) if acl == "private" else f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{object_name}"
            }
        except ClientError as e:
            return {"success": False, "error": str(e)}
    
    def download_file(self, object_name: str, file_path: str) -> dict:
        """
        下载文件
        
        Args:
            object_name: S3 对象名称
            file_path: 本地保存路径
        
        Returns:
            下载结果
        """
        try:
            self.s3_client.download_file(
                self.bucket_name,
                object_name,
                file_path
            )
            return {"success": True, "file_path": file_path}
        except ClientError as e:
            return {"success": False, "error": str(e)}
    
    def get_presigned_url(self, object_name: str, expiration: int = 3600) -> dict:
        """
        生成预签名 URL（临时访问链接）
        
        Args:
            object_name: S3 对象名称
            expiration: 过期时间（秒）
        
        Returns:
            预签名 URL
        """
        try:
            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": object_name},
                ExpiresIn=expiration
            )
            return {"success": True, "url": url, "expires_in": expiration}
        except ClientError as e:
            return {"success": False, "error": str(e)}
    
    def delete_file(self, object_name: str) -> dict:
        """
        删除文件
        
        Args:
            object_name: S3 对象名称
        
        Returns:
            删除结果
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=object_name
            )
            return {"success": True, "object_name": object_name}
        except ClientError as e:
            return {"success": False, "error": str(e)}
    
    def list_files(self, prefix: str = "", max_keys: int = 100) -> dict:
        """
        列出文件
        
        Args:
            prefix: 前缀过滤
            max_keys: 最大返回数量
        
        Returns:
            文件列表
        """
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys
            )
            
            files = []
            if "Contents" in response:
                for obj in response["Contents"]:
                    files.append({
                        "key": obj["Key"],
                        "size": obj["Size"],
                        "last_modified": str(obj["LastModified"])
                    })
            
            return {"success": True, "files": files, "count": len(files)}
        except ClientError as e:
            return {"success": False, "error": str(e)}
    
    def get_file_metadata(self, object_name: str) -> dict:
        """
        获取文件元数据
        
        Args:
            object_name: S3 对象名称
        
        Returns:
            文件元数据
        """
        try:
            response = self.s3_client.head_object(
                Bucket=self.bucket_name,
                Key=object_name
            )
            return {
                "success": True,
                "metadata": {
                    "content_type": response.get("ContentType"),
                    "content_length": response.get("ContentLength"),
                    "last_modified": str(response.get("LastModified")),
                    "etag": response.get("ETag")
                }
            }
        except ClientError as e:
            return {"success": False, "error": str(e)}
    
    def upload_avatar(self, user_id: str, file_obj, content_type: str) -> dict:
        """
        上传用户头像（专用方法）
        
        Args:
            user_id: 用户 ID
            file_obj: 文件对象
            content_type: 内容类型
        
        Returns:
            上传结果
        """
        object_name = f"avatars/{user_id}/{uuid.uuid4().hex}.jpg"
        return self.upload_file_obj(file_obj, object_name, content_type, acl="public-read")


# 使用示例
if __name__ == "__main__":
    client = S3Client()
    
    # 1. 上传文件
    result = client.upload_file(
        file_path="local_file.txt",
        object_name="uploads/test.txt",
        acl="private"
    )
    print(f"上传结果：{result}")
    
    # 2. 获取预签名 URL
    if result["success"]:
        url_result = client.get_presigned_url(
            object_name="uploads/test.txt",
            expiration=3600
        )
        print(f"访问 URL: {url_result}")
    
    # 3. 列出文件
    list_result = client.list_files(prefix="uploads/")
    print(f"文件列表：{list_result}")
    
    # 4. 删除文件
    if result["success"]:
        delete_result = client.delete_file("uploads/test.txt")
        print(f"删除结果：{delete_result}")

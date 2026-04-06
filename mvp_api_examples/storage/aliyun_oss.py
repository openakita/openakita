"""
阿里云 OSS 对象存储集成示例
用于 MVP 文件上传、图片存储等场景
"""
import oss2
import os
from typing import Dict, List, Optional, BinaryIO
from datetime import datetime, timedelta


class AliyunOSSClient:
    """
    阿里云 OSS 对象存储客户端
    
    使用场景:
    - 用户头像/图片上传
    - 文件存储与下载
    - 静态资源托管
    - 备份文件存储
    """
    
    def __init__(self, access_key_id: str, access_key_secret: str,
                 endpoint: str, bucket_name: str):
        """
        初始化阿里云 OSS 客户端
        
        Args:
            access_key_id: AccessKey ID
            access_key_secret: AccessKey Secret
            endpoint: OSS Endpoint（如：oss-cn-hangzhou.aliyuncs.com）
            bucket_name: Bucket 名称
        """
        self.bucket_name = bucket_name
        
        # 认证
        auth = oss2.Auth(access_key_id, access_key_secret)
        
        # 初始化 Bucket
        self.bucket = oss2.Bucket(auth, endpoint, bucket_name)
    
    def upload_file(self, file_path: str, object_key: str, 
                    content_type: Optional[str] = None) -> Dict:
        """
        上传文件
        
        Args:
            file_path: 本地文件路径
            object_key: OSS 中的对象键（路径）
            content_type: 文件类型（可选，自动检测）
        
        Returns:
            上传结果字典
        """
        try:
            self.bucket.put_object_from_file(object_key, file_path)
            file_url = self.get_file_url(object_key)
            
            return {
                "success": True,
                "object_key": object_key,
                "file_url": file_url,
                "bucket": self.bucket_name
            }
        except Exception as e:
            return {"success": False, "error": f"上传失败：{str(e)}"}
    
    def upload_file_obj(self, file_obj: BinaryIO, object_key: str,
                       content_type: Optional[str] = None) -> Dict:
        """
        上传文件对象（用于处理上传的文件流）
        """
        try:
            headers = {"Content-Type": content_type} if content_type else None
            self.bucket.put_object(object_key, file_obj, headers=headers)
            file_url = self.get_file_url(object_key)
            
            return {
                "success": True,
                "object_key": object_key,
                "file_url": file_url
            }
        except Exception as e:
            return {"success": False, "error": f"上传失败：{str(e)}"}
    
    def download_file(self, object_key: str, save_path: str) -> Dict:
        """下载文件"""
        try:
            self.bucket.get_object_to_file(object_key, save_path)
            return {"success": True, "object_key": object_key, "save_path": save_path}
        except Exception as e:
            return {"success": False, "error": f"下载失败：{str(e)}"}
    
    def get_file_url(self, object_key: str, expires: int = 3600) -> str:
        """获取带签名的文件 URL"""
        return self.bucket.sign_url("GET", object_key, expires)
    
    def delete_file(self, object_key: str) -> Dict:
        """删除文件"""
        try:
            self.bucket.delete_object(object_key)
            return {"success": True, "object_key": object_key}
        except Exception as e:
            return {"success": False, "error": f"删除失败：{str(e)}"}
    
    def list_files(self, prefix: str = "", max_keys: int = 100) -> Dict:
        """列出文件"""
        try:
            result = []
            for obj in oss2.ObjectIterator(self.bucket, prefix=prefix, max_keys=max_keys):
                result.append({"key": obj.key, "size": obj.size, "last_modified": obj.last_modified.isoformat()})
            return {"success": True, "count": len(result), "files": result}
        except Exception as e:
            return {"success": False, "error": f"列出文件失败：{str(e)}"}


# ============== 使用示例 ==============
if __name__ == "__main__":
    print("=== 阿里云 OSS 示例 ===")
    # 配置
    client = AliyunOSSClient(
        access_key_id="your-access-key-id",
        access_key_secret="your-access-key-secret",
        endpoint="oss-cn-hangzhou.aliyuncs.com",
        bucket_name="mvp-platform"
    )
    
    # 上传示例
    # result = client.upload_file("local.txt", "uploads/test.txt")
    # print(result)

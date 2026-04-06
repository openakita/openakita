"""
API 集成示例 05: 阿里云 OSS 对象存储

功能：
- 上传文件
- 下载文件
- 删除文件
- 生成签名 URL
- 列举文件

依赖：
pip install oss2

文档：
https://help.aliyun.com/product/31815.html
"""

import oss2
from pathlib import Path
import time

# ============ 配置区域 ============
OSS_ENDPOINT = "oss-cn-hangzhou.aliyuncs.com"
OSS_BUCKET = "your-bucket-name"
OSS_ACCESS_KEY_ID = "your_access_key_id"
OSS_ACCESS_KEY_SECRET = "your_access_key_secret"
OSS_CDN_DOMAIN = "https://cdn.example.com"  # 可选 CDN 域名
# =================================


class OSSClient:
    """阿里云 OSS 客户端"""
    
    def __init__(self, endpoint, bucket, access_key_id, access_key_secret):
        self.auth = oss2.Auth(access_key_id, access_key_secret)
        self.bucket = oss2.Bucket(self.auth, endpoint, bucket)
        self.cdn_domain = OSS_CDN_DOMAIN
    
    def upload_file(self, local_file_path, object_key=None, content_type=None):
        """
        上传文件
        
        Args:
            local_file_path: 本地文件路径
            object_key: OSS 存储路径 (默认使用文件名)
            content_type: 文件类型
        
        Returns:
            str: 文件访问 URL
        """
        if object_key is None:
            object_key = Path(local_file_path).name
        
        try:
            # 上传文件
            with open(local_file_path, 'rb') as f:
                self.bucket.put_object(object_key, f, headers={'Content-Type': content_type})
            
            # 生成访问 URL
            url = self.get_file_url(object_key)
            print(f"✅ 文件已上传：{url}")
            return url
        except Exception as e:
            print(f"❌ 上传失败：{e}")
            return None
    
    def upload_file_with_progress(self, local_file_path, object_key=None, callback=None):
        """
        上传文件 (带进度回调)
        
        Args:
            local_file_path: 本地文件路径
            object_key: OSS 存储路径
            callback: 进度回调函数 (bytes_transferred, total_size)
        
        Returns:
            str: 文件访问 URL
        """
        if object_key is None:
            object_key = Path(local_file_path).name
        
        class ProgressCallback:
            def __init__(self, total_size, callback):
                self.total_size = total_size
                self.bytes_transferred = 0
                self.callback = callback
            
            def __call__(self, bytes_transferred):
                self.bytes_transferred += bytes_transferred
                if self.callback:
                    self.callback(self.bytes_transferred, self.total_size)
        
        try:
            total_size = Path(local_file_path).stat().st_size
            progress = ProgressCallback(total_size, callback)
            
            self.bucket.put_object_from_file(
                object_key,
                local_file_path,
                progress_callback=progress
            )
            
            url = self.get_file_url(object_key)
            print(f"✅ 文件已上传：{url}")
            return url
        except Exception as e:
            print(f"❌ 上传失败：{e}")
            return None
    
    def download_file(self, object_key, local_file_path):
        """
        下载文件
        
        Args:
            object_key: OSS 文件路径
            local_file_path: 本地保存路径
        
        Returns:
            bool: 是否成功
        """
        try:
            # 确保目录存在
            Path(local_file_path).parent.mkdir(parents=True, exist_ok=True)
            
            self.bucket.get_object_to_file(object_key, local_file_path)
            print(f"✅ 文件已下载：{local_file_path}")
            return True
        except Exception as e:
            print(f"❌ 下载失败：{e}")
            return False
    
    def delete_file(self, object_key):
        """
        删除文件
        
        Args:
            object_key: OSS 文件路径
        
        Returns:
            bool: 是否成功
        """
        try:
            self.bucket.delete_object(object_key)
            print(f"✅ 文件已删除：{object_key}")
            return True
        except Exception as e:
            print(f"❌ 删除失败：{e}")
            return False
    
    def get_file_url(self, object_key, expires=3600, internal=False):
        """
        生成文件访问 URL
        
        Args:
            object_key: OSS 文件路径
            expires: URL 有效期 (秒)
            internal: 是否内网访问
        
        Returns:
            str: 访问 URL
        """
        if internal:
            # 内网访问
            return f"https://{self.bucket.bucket_name}.{self.bucket.endpoint.replace('http://', '').replace('https://',')}/{object_key}"
        else:
            # 公网访问 (带签名)
            url = self.bucket.sign_url('GET', object_key, expires)
            
            # 如果配置了 CDN，使用 CDN 域名
            if self.cdn_domain and expires == 0:
                url = f"{self.cdn_domain}/{object_key}"
            
            return url
    
    def list_files(self, prefix='', max_keys=100):
        """
        列举文件
        
        Args:
            prefix: 路径前缀
            max_keys: 最大数量
        
        Returns:
            list: 文件信息列表
        """
        try:
            files = []
            for obj in oss2.ObjectIterator(self.bucket, prefix=prefix, max_keys=max_keys):
                files.append({
                    'key': obj.key,
                    'size': obj.size,
                    'last_modified': obj.last_modified,
                    'url': self.get_file_url(obj.key)
                })
            return files
        except Exception as e:
            print(f"❌ 列举失败：{e}")
            return []


# ============ 使用示例 ============
if __name__ == "__main__":
    print("=" * 50)
    print("阿里云 OSS 对象存储 API 集成示例")
    print("=" * 50)
    
    oss_client = OSSClient(
        OSS_ENDPOINT, OSS_BUCKET,
        OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET
    )
    
    # 1. 上传文件
    print("\n1️⃣  上传文件")
    # url = oss_client.upload_file("local_file.pdf", "uploads/file.pdf")
    print("⚠️  实际环境调用 upload_file 上传")
    
    # 2. 上传文件 (带进度)
    print("\n2️⃣  上传文件 (带进度)")
    # def progress_callback(bytes_transferred, total_size):
    #     percent = (bytes_transferred / total_size) * 100
    #     print(f"上传进度：{percent:.2f}%")
    # 
    # url = oss_client.upload_file_with_progress(
    #     "large_file.zip",
    #     "uploads/large_file.zip",
    #     progress_callback
    # )
    print("⚠️  实际环境调用 upload_file_with_progress 上传")
    
    # 3. 下载文件
    print("\n3️⃣  下载文件")
    # oss_client.download_file("uploads/file.pdf", "downloads/file.pdf")
    print("⚠️  实际环境调用 download_file 下载")
    
    # 4. 生成签名 URL
    print("\n4️⃣  生成签名 URL")
    # url = oss_client.get_file_url("uploads/file.pdf", expires=3600)
    print(f"签名 URL 示例：https://bucket.oss-region.aliyuncs.com/file.pdf?OSSAccessKeyId=xxx&Expires=xxx&Signature=xxx")
    
    # 5. 列举文件
    print("\n5️⃣  列举文件")
    # files = oss_client.list_files(prefix="uploads/", max_keys=10)
    # for f in files:
    #     print(f"  - {f['key']} ({f['size']} bytes)")
    print("⚠️  实际环境调用 list_files 列举")
    
    # 6. 删除文件
    print("\n6️⃣  删除文件")
    # oss_client.delete_file("uploads/file.pdf")
    print("⚠️  实际环境调用 delete_file 删除")
    
    print("\n" + "=" * 50)
    print("关键要点:")
    print("1. 大文件上传建议使用分片上传 (multipart)")
    print("2. 敏感文件应使用签名 URL (设置过期时间)")
    print("3. 可配置 CDN 加速访问")
    print("4. 注意存储类型选择 (标准/低频/归档)")
    print("=" * 50)

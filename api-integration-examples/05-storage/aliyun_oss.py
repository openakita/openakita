"""
API 集成示例 5: 阿里云 OSS 对象存储
"""
import oss2
import os

class OSSClient:
    def __init__(self, endpoint, access_key, access_secret, bucket_name):
        self.auth = oss2.Auth(access_key, access_secret)
        self.bucket = oss2.Bucket(self.auth, endpoint, bucket_name)
    
    def upload_file(self, local_path, object_key):
        """上传文件"""
        result = self.bucket.put_object_from_file(object_key, local_path)
        return result.status == 200
    
    def upload_content(self, content, object_key):
        """上传内容"""
        result = self.bucket.put_object(object_key, content)
        return result.status == 200
    
    def download_file(self, object_key, local_path):
        """下载文件"""
        self.bucket.get_object_to_file(object_key, local_path)
        return os.path.exists(local_path)
    
    def get_url(self, object_key, expires=3600):
        """获取签名 URL"""
        return self.bucket.sign_url('GET', object_key, expires)
    
    def delete_file(self, object_key):
        """删除文件"""
        result = self.bucket.delete_object(object_key)
        return result.status == 204
    
    def list_files(self, prefix=""):
        """列出文件"""
        files = []
        for obj in oss2.ObjectIterator(self.bucket, prefix=prefix):
            files.append({
                "key": obj.key,
                "size": obj.size,
                "last_modified": obj.last_modified
            })
        return files

# 使用示例
if __name__ == "__main__":
    oss = OSSClient(
        "oss-cn-hangzhou.aliyuncs.com",
        "access_key",
        "access_secret",
        "bucket_name"
    )
    # oss.upload_file("local.txt", "remote.txt")
    # url = oss.get_url("remote.txt")
    print("阿里云 OSS 示例已就绪")

"""
7. OSS 存储 API - 阿里云对象存储
支持文件上传、下载、删除、列表等操作
"""

import os
from typing import List, Optional, BinaryIO
from adapters.base import BaseAPIAdapter, APIResponse, APIStatus

try:
    import oss2
except ImportError:
    oss2 = None


class AliyunOSSAdapter(BaseAPIAdapter):
    """阿里云 OSS 适配器"""
    
    def __init__(self, config: dict):
        """
        配置参数:
        - access_key_id: AccessKey ID
        - access_key_secret: AccessKey Secret
        - endpoint: OSS _endpoint (如 oss-cn-hangzhou.aliyuncs.com)
        - bucket_name: 存储空间名称
        """
        super().__init__(config)
        self.bucket = None
    
    def connect(self) -> bool:
        try:
            if oss2 is None:
                print("错误：请先安装 oss2 库 (pip install oss2)")
                return False
            
            assert self.config.get('access_key_id')
            assert self.config.get('access_key_secret')
            assert self.config.get('endpoint')
            assert self.config.get('bucket_name')
            
            auth = oss2.Auth(
                self.config['access_key_id'],
                self.config['access_key_secret']
            )
            self.bucket = oss2.Bucket(
                auth,
                self.config['endpoint'],
                self.config['bucket_name']
            )
            self._initialized = True
            return True
        except Exception as e:
            print(f"连接失败：{e}")
            return False
    
    def disconnect(self) -> None:
        self.bucket = None
        self._initialized = False
    
    def execute(self, action: str, params: dict) -> APIResponse:
        if action == "upload":
            return self.upload_file(params)
        elif action == "download":
            return self.download_file(params)
        elif action == "delete":
            return self.delete_file(params)
        elif action == "list":
            return self.list_files(params)
        elif action == "get_url":
            return self.get_file_url(params)
        else:
            return APIResponse(
                status=APIStatus.FAILED,
                error=f"未知操作：{action}"
            )
    
    def upload_file(self, params: dict) -> APIResponse:
        """
        上传文件
        
        参数:
        - object_key: OSS 对象键 (路径)
        - file_path: 本地文件路径
        - file_content: 文件内容 (bytes 或 str，与 file_path 二选一)
        - content_type: 内容类型 (可选)
        """
        try:
            object_key = params['object_key']
            
            if 'file_content' in params:
                content = params['file_content']
                if isinstance(content, str):
                    content = content.encode('utf-8')
                result = self.bucket.put_object(object_key, content)
            elif 'file_path' in params:
                result = self.bucket.put_object_from_file(object_key, params['file_path'])
            else:
                return APIResponse(
                    status=APIStatus.FAILED,
                    error="必须提供 file_content 或 file_path"
                )
            
            if result.status == 200:
                return APIResponse(
                    status=APIStatus.SUCCESS,
                    data={'object_key': object_key, 'etag': result.etag},
                    status_code=200
                )
            else:
                return APIResponse(
                    status=APIStatus.FAILED,
                    error=f"上传失败，状态码：{result.status}"
                )
        except Exception as e:
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def download_file(self, params: dict) -> APIResponse:
        """
        下载文件
        
        参数:
        - object_key: OSS 对象键
        - file_path: 保存路径 (可选，不提供则返回内容)
        """
        try:
            object_key = params['object_key']
            
            if 'file_path' in params:
                self.bucket.get_object_to_file(object_key, params['file_path'])
                return APIResponse(
                    status=APIStatus.SUCCESS,
                    data={'file_path': params['file_path']},
                    status_code=200
                )
            else:
                result = self.bucket.get_object(object_key)
                content = result.read()
                return APIResponse(
                    status=APIStatus.SUCCESS,
                    data={'content': content, 'content_length': result.content_length},
                    status_code=200
                )
        except Exception as e:
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def delete_file(self, params: dict) -> APIResponse:
        """
        删除文件
        
        参数:
        - object_key: OSS 对象键
        """
        try:
            result = self.bucket.delete_object(params['object_key'])
            if result.status == 204:
                return APIResponse(
                    status=APIStatus.SUCCESS,
                    data={'deleted': params['object_key']},
                    status_code=204
                )
            else:
                return APIResponse(
                    status=APIStatus.FAILED,
                    error=f"删除失败，状态码：{result.status}"
                )
        except Exception as e:
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def list_files(self, params: dict) -> APIResponse:
        """
        列出文件
        
        参数:
        - prefix: 前缀 (可选)
        - marker: 分页标记 (可选)
        - max_keys: 最大数量 (可选，默认 100)
        """
        try:
            result = self.bucket.list_objects(
                prefix=params.get('prefix', ''),
                marker=params.get('marker', ''),
                max_keys=params.get('max_keys', 100)
            )
            
            files = []
            for obj in result.object_list:
                files.append({
                    'key': obj.key,
                    'size': obj.size,
                    'last_modified': obj.last_modified,
                    'etag': obj.etag
                })
            
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={
                    'files': files,
                    'next_marker': result.next_marker,
                    'is_truncated': result.is_truncated
                },
                status_code=200
            )
        except Exception as e:
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def get_file_url(self, params: dict) -> APIResponse:
        """
        获取文件访问 URL
        
        参数:
        - object_key: OSS 对象键
        - expires: 过期时间 (秒，默认 3600)
        """
        try:
            url = self.bucket.sign_url(
                'GET',
                params['object_key'],
                params.get('expires', 3600)
            )
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={'url': url},
                status_code=200
            )
        except Exception as e:
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )


# ============ 使用示例 ============
if __name__ == "__main__":
    config = {
        'access_key_id': 'YOUR_ACCESS_KEY_ID',
        'access_key_secret': 'YOUR_ACCESS_KEY_SECRET',
        'endpoint': 'oss-cn-hangzhou.aliyuncs.com',
        'bucket_name': 'your-bucket-name'
    }
    
    oss = AliyunOSSAdapter(config)
    
    if oss.connect():
        print("✅ OSS 连接成功")
        
        # 上传文件
        response = oss.execute('upload', {
            'object_key': 'test/hello.txt',
            'file_content': 'Hello, OSS!'
        })
        
        if response.is_success():
            print(f"✅ 上传成功：{response.data}")
        else:
            print(f"❌ 上传失败：{response.error}")
    else:
        print("❌ OSS 连接失败")

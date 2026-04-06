"""
对象存储 API - 阿里云 OSS
支持文件上传、下载、删除操作
"""

from typing import Dict, Any, Optional
import logging
from .base import BaseAPI, APIResponse, APIMode
import time
import os

logger = logging.getLogger(__name__)


class StorageAPI(BaseAPI):
    """阿里云 OSS 对象存储 API"""
    
    def __init__(self, mode: APIMode = APIMode.MOCK):
        super().__init__(mode)
        self.mock_files = {}
    
    def _call_mock(self, **kwargs) -> APIResponse:
        """Mock 模式：模拟 OSS 操作"""
        action = kwargs.get('action', 'upload')
        
        try:
            if action == 'upload':
                file_path = kwargs.get('file_path', '')
                object_key = kwargs.get('object_key', f'file_{int(time.time())}')
                
                if not os.path.exists(file_path):
                    return APIResponse(
                        success=False,
                        data=None,
                        error=f"文件不存在：{file_path}",
                        status_code=404
                    )
                
                file_size = os.path.getsize(file_path)
                self.mock_files[object_key] = {
                    'key': object_key,
                    'size': file_size,
                    'uploaded_at': time.strftime('%Y-%m-%d %H:%M:%S')
                }
                
                return APIResponse(
                    success=True,
                    data={
                        'object_key': object_key,
                        'url': f'https://mock.oss.com/{object_key}',
                        'size': file_size
                    }
                )
            
            elif action == 'download':
                object_key = kwargs.get('object_key', '')
                if object_key not in self.mock_files:
                    return APIResponse(
                        success=False,
                        data=None,
                        error=f"文件不存在：{object_key}",
                        status_code=404
                    )
                return APIResponse(
                    success=True,
                    data={
                        'object_key': object_key,
                        'download_url': f'https://mock.oss.com/download/{object_key}'
                    }
                )
            
            elif action == 'delete':
                object_key = kwargs.get('object_key', '')
                if object_key in self.mock_files:
                    del self.mock_files[object_key]
                    return APIResponse(success=True, data={'deleted': object_key})
                return APIResponse(
                    success=False,
                    data=None,
                    error=f"文件不存在：{object_key}",
                    status_code=404
                )
            
            elif action == 'list':
                prefix = kwargs.get('prefix', '')
                files = [f for k, f in self.mock_files.items() if k.startswith(prefix)]
                return APIResponse(success=True, data={'files': files, 'total': len(files)})
            
            else:
                return APIResponse(success=False, data=None, error=f"未知操作：{action}", status_code=400)
                
        except Exception as e:
            return APIResponse(success=False, data=None, error=str(e), status_code=500)
    
    def _call_real(self, **kwargs) -> APIResponse:
        """真实 API 调用 - 使用 oss2 库"""
        try:
            import oss2
            
            action = kwargs.get('action', 'upload')
            auth = oss2.Auth(
                self._config.get('ALIYUN_ACCESS_KEY_ID'),
                self._config.get('ALIYUN_ACCESS_KEY_SECRET')
            )
            bucket = oss2.Bucket(
                auth,
                self._config.get('ALIYUN_OSS_ENDPOINT'),
                self._config.get('ALIYUN_OSS_BUCKET_NAME')
            )
            
            if action == 'upload':
                file_path = kwargs.get('file_path', '')
                object_key = kwargs.get('object_key', os.path.basename(file_path))
                bucket.put_object_from_file(object_key, file_path)
                return APIResponse(
                    success=True,
                    data={
                        'object_key': object_key,
                        'url': f'https://{self._config.get("ALIYUN_OSS_BUCKET_NAME")}.{self._config.get("ALIYUN_OSS_ENDPOINT")}/{object_key}'
                    }
                )
            
            elif action == 'download':
                object_key = kwargs.get('object_key', '')
                download_path = kwargs.get('download_path', f'downloaded_{object_key}')
                bucket.get_object_to_file(object_key, download_path)
                return APIResponse(success=True, data={'download_path': download_path})
            
            elif action == 'delete':
                object_key = kwargs.get('object_key', '')
                bucket.delete_object(object_key)
                return APIResponse(success=True, data={'deleted': object_key})
            
            else:
                return APIResponse(success=False, data=None, error=f"不支持的操作：{action}", status_code=400)
                
        except Exception as e:
            return APIResponse(success=False, data=None, error=str(e), status_code=500)
    
    def upload(self, file_path: str, object_key: Optional[str] = None) -> APIResponse:
        """上传文件"""
        return self.call(action='upload', file_path=file_path, object_key=object_key)
    
    def download(self, object_key: str, download_path: Optional[str] = None) -> APIResponse:
        """下载文件"""
        return self.call(action='download', object_key=object_key, download_path=download_path)
    
    def delete(self, object_key: str) -> APIResponse:
        """删除文件"""
        return self.call(action='delete', object_key=object_key)
    
    def list_files(self, prefix: str = '') -> APIResponse:
        """列出文件"""
        return self.call(action='list', prefix=prefix)


def test_storage_api():
    """对象存储 API 测试"""
    print("=" * 50)
    print("对象存储 API 测试")
    print("=" * 50)
    
    api = StorageAPI(mode=APIMode.MOCK)
    
    # 创建测试文件
    test_file = 'test_upload.txt'
    with open(test_file, 'w') as f:
        f.write('测试内容')
    
    print("\n[测试 1] 上传文件")
    result = api.upload(test_file, 'test/file.txt')
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    if result.success:
        print(f"文件 URL: {result.data.get('url')}")
    
    print("\n[测试 2] 列出文件")
    result = api.list_files()
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    if result.success:
        print(f"文件数：{result.data.get('total', 0)}")
    
    print("\n[测试 3] 删除文件")
    result = api.delete('test/file.txt')
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    
    # 清理测试文件
    if os.path.exists(test_file):
        os.remove(test_file)
    
    print("\n" + "=" * 50)


if __name__ == "__main__":
    test_storage_api()

"""
6. HTTP 请求 API - 通用 HTTP 调用
支持 GET/POST/PUT/DELETE 等方法，统一接口规范
"""

import requests
from typing import Dict, Any, Optional
from adapters.base import BaseAPIAdapter, APIResponse, APIStatus


class HTTPClientAdapter(BaseAPIAdapter):
    """通用 HTTP 客户端适配器"""
    
    def __init__(self, config: dict):
        """
        配置参数:
        - base_url: 基础 URL
        - headers: 默认请求头
        - timeout: 超时时间 (秒)
        - auth: 认证信息 (可选)
        """
        super().__init__(config)
        self.base_url = config.get('base_url', '')
        self.default_headers = config.get('headers', {})
        self.timeout = config.get('timeout', 30)
        self.auth = config.get('auth')
    
    def connect(self) -> bool:
        """验证连接（简单检查配置）"""
        try:
            assert self.base_url
            self._initialized = True
            return True
        except Exception as e:
            print(f"连接失败：{e}")
            return False
    
    def disconnect(self) -> None:
        self._initialized = False
    
    def execute(self, action: str, params: dict) -> APIResponse:
        """
        执行 HTTP 请求
        
        action: get/post/put/delete/patch
        params:
        - url: 请求 URL (相对路径或完整 URL)
        - headers: 请求头 (可选)
        - params: 查询参数 (可选)
        - json: JSON 数据 (可选)
        - data: 表单数据 (可选)
        - files: 文件 (可选)
        """
        method = action.upper()
        if method not in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']:
            return APIResponse(
                status=APIStatus.FAILED,
                error=f"不支持的 HTTP 方法：{method}"
            )
        
        try:
            url = params.get('url', '')
            if not url.startswith('http'):
                url = self.base_url.rstrip('/') + '/' + url.lstrip('/')
            
            # 合并请求头
            headers = {**self.default_headers, **params.get('headers', {})}
            
            # 发送请求
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params.get('params'),
                json=params.get('json'),
                data=params.get('data'),
                files=params.get('files'),
                auth=self.auth,
                timeout=self.timeout
            )
            
            # 尝试解析 JSON
            try:
                data = response.json()
            except:
                data = response.text
            
            if 200 <= response.status_code < 300:
                return APIResponse(
                    status=APIStatus.SUCCESS,
                    data=data,
                    status_code=response.status_code
                )
            else:
                return APIResponse(
                    status=APIStatus.FAILED,
                    error=data if isinstance(data, str) else str(data),
                    status_code=response.status_code
                )
        except requests.exceptions.Timeout:
            return APIResponse(
                status=APIStatus.TIMEOUT,
                error="请求超时"
            )
        except Exception as e:
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def get(self, url: str, params: dict = None) -> APIResponse:
        """GET 请求快捷方法"""
        return self.execute('get', {'url': url, 'params': params})
    
    def post(self, url: str, json: dict = None, data: dict = None) -> APIResponse:
        """POST 请求快捷方法"""
        return self.execute('post', {'url': url, 'json': json, 'data': data})
    
    def put(self, url: str, json: dict = None) -> APIResponse:
        """PUT 请求快捷方法"""
        return self.execute('put', {'url': url, 'json': json})
    
    def delete(self, url: str) -> APIResponse:
        """DELETE 请求快捷方法"""
        return self.execute('delete', {'url': url})


# ============ 使用示例 ============
if __name__ == "__main__":
    # 配置 GitHub API
    config = {
        'base_url': 'https://api.github.com',
        'headers': {
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'API-Integration-Test'
        },
        'timeout': 30
    }
    
    http_client = HTTPClientAdapter(config)
    
    if http_client.connect():
        print("✅ HTTP 客户端连接成功")
        
        # GET 请求
        response = http_client.get('/users/octocat')
        if response.is_success():
            print(f"✅ 获取用户信息成功：{response.data.get('login')}")
        else:
            print(f"❌ 请求失败：{response.error}")
        
        # POST 请求示例
        # response = http_client.post('/repos', json={
        #     'name': 'test-repo',
        #     'description': '测试仓库'
        # })
    else:
        print("❌ HTTP 客户端连接失败")

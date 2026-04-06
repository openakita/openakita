"""
HTTP 请求 API - 通用 Webhook
支持 GET/POST/PUT/DELETE 等 HTTP 方法
"""

from typing import Dict, Any, Optional
import logging
from .base import BaseAPI, APIResponse, APIMode
import time
import json

logger = logging.getLogger(__name__)


class HTTPAPI(BaseAPI):
    """通用 HTTP 请求 API"""
    
    def __init__(self, mode: APIMode = APIMode.MOCK):
        super().__init__(mode)
    
    def _call_mock(self, **kwargs) -> APIResponse:
        """Mock 模式：模拟 HTTP 请求"""
        method = kwargs.get('method', 'GET').upper()
        url = kwargs.get('url', '')
        
        try:
            logger.info(f"[MOCK] {method} {url}")
            
            # 模拟不同响应
            if 'error' in url:
                return APIResponse(
                    success=False,
                    data=None,
                    error="模拟错误响应",
                    status_code=500
                )
            
            if 'timeout' in url:
                time.sleep(5)  # 模拟超时
            
            mock_response = {
                'status': 'success',
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'request': {
                    'method': method,
                    'url': url,
                    'headers': kwargs.get('headers', {}),
                    'body': kwargs.get('json', kwargs.get('data'))
                }
            }
            
            return APIResponse(
                success=True,
                data=mock_response,
                status_code=200
            )
                
        except Exception as e:
            return APIResponse(success=False, data=None, error=str(e), status_code=500)
    
    def _call_real(self, **kwargs) -> APIResponse:
        """真实 API 调用 - 使用 requests 库"""
        try:
            import requests
            
            method = kwargs.get('method', 'GET').upper()
            url = kwargs.get('url', '')
            headers = kwargs.get('headers', {})
            timeout = kwargs.get('timeout', 30)
            
            if method == 'GET':
                response = requests.get(url, headers=headers, params=kwargs.get('params'), timeout=timeout)
            elif method == 'POST':
                response = requests.post(
                    url,
                    headers=headers,
                    json=kwargs.get('json'),
                    data=kwargs.get('data'),
                    timeout=timeout
                )
            elif method == 'PUT':
                response = requests.put(
                    url,
                    headers=headers,
                    json=kwargs.get('json'),
                    data=kwargs.get('data'),
                    timeout=timeout
                )
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=timeout)
            else:
                return APIResponse(
                    success=False,
                    data=None,
                    error=f"不支持的 HTTP 方法：{method}",
                    status_code=400
                )
            
            try:
                response_data = response.json()
            except:
                response_data = {'text': response.text}
            
            return APIResponse(
                success=200 <= response.status_code < 300,
                data=response_data,
                status_code=response.status_code
            )
                
        except Exception as e:
            return APIResponse(success=False, data=None, error=str(e), status_code=500)
    
    def get(self, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None) -> APIResponse:
        """GET 请求"""
        return self.call(method='GET', url=url, params=params, headers=headers)
    
    def post(self, url: str, json: Optional[Dict] = None, data: Optional[Dict] = None, headers: Optional[Dict] = None) -> APIResponse:
        """POST 请求"""
        return self.call(method='POST', url=url, json=json, data=data, headers=headers)
    
    def put(self, url: str, json: Optional[Dict] = None, data: Optional[Dict] = None, headers: Optional[Dict] = None) -> APIResponse:
        """PUT 请求"""
        return self.call(method='PUT', url=url, json=json, data=data, headers=headers)
    
    def delete(self, url: str, headers: Optional[Dict] = None) -> APIResponse:
        """DELETE 请求"""
        return self.call(method='DELETE', url=url, headers=headers)


def test_http_api():
    """HTTP API 测试"""
    print("=" * 50)
    print("HTTP API 测试")
    print("=" * 50)
    
    api = HTTPAPI(mode=APIMode.MOCK)
    
    print("\n[测试 1] GET 请求")
    result = api.get('https://api.example.com/users')
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    if result.success:
        print(f"响应：{json.dumps(result.data, indent=2)[:200]}...")
    
    print("\n[测试 2] POST 请求")
    result = api.post(
        'https://api.example.com/users',
        json={'name': '张三', 'email': 'test@example.com'}
    )
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    
    print("\n[测试 3] 错误处理")
    result = api.get('https://api.example.com/error')
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    print(f"错误：{result.error}")
    
    print("\n" + "=" * 50)


if __name__ == "__main__":
    test_http_api()

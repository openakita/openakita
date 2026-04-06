"""
HTTP Webhook API 集成示例
支持发送和接收 Webhook 请求
"""

import requests
import json
from typing import Dict, Any, Optional
from flask import Flask, request, jsonify


class WebhookClient:
    """Webhook 客户端 - 发送 HTTP 请求"""
    
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
    
    def send_post(self, url: str, data: Dict[str, Any], headers: Optional[Dict] = None) -> Dict:
        """
        发送 POST 请求
        
        Args:
            url: 目标 URL
            data: 请求数据
            headers: 自定义请求头
            
        Returns:
            dict: 响应结果
        """
        try:
            default_headers = {"Content-Type": "application/json"}
            if headers:
                default_headers.update(headers)
            
            response = self.session.post(
                url, 
                json=data, 
                headers=default_headers, 
                timeout=self.timeout
            )
            
            result = {
                "success": response.status_code == 200,
                "status_code": response.status_code,
                "data": response.json() if response.content else None
            }
            
            print(f"✓ POST {url} - Status: {response.status_code}")
            return result
            
        except requests.exceptions.Timeout:
            print(f"✗ 请求超时：{url}")
            return {"success": False, "error": "timeout"}
        except Exception as e:
            print(f"✗ 请求失败：{e}")
            return {"success": False, "error": str(e)}
    
    def send_get(self, url: str, params: Optional[Dict] = None) -> Dict:
        """
        发送 GET 请求
        
        Args:
            url: 目标 URL
            params: 查询参数
            
        Returns:
            dict: 响应结果
        """
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            
            result = {
                "success": response.status_code == 200,
                "status_code": response.status_code,
                "data": response.json() if response.content else None
            }
            
            print(f"✓ GET {url} - Status: {response.status_code}")
            return result
            
        except Exception as e:
            print(f"✗ 请求失败：{e}")
            return {"success": False, "error": str(e)}


class WebhookServer:
    """Webhook 服务端 - 接收 HTTP 请求"""
    
    def __init__(self, name: str = "webhook_server"):
        self.app = Flask(name)
        self.webhooks = {}
        self._setup_routes()
    
    def _setup_routes(self):
        """设置默认路由"""
        @self.app.route('/webhook', methods=['POST'])
        def receive_webhook():
            data = request.json
            print(f"✓ 收到 Webhook: {json.dumps(data, indent=2)}")
            return jsonify({"status": "received", "data": data}), 200
        
        @self.app.route('/webhook/<webhook_id>', methods=['POST'])
        def receive_named_webhook(webhook_id: str):
            data = request.json
            print(f"✓ 收到 Webhook[{webhook_id}]: {json.dumps(data, indent=2)}")
            
            # 触发注册的回调函数
            if webhook_id in self.webhooks:
                try:
                    self.webhooks[webhook_id](data)
                except Exception as e:
                    print(f"✗ 回调执行失败：{e}")
            
            return jsonify({"status": "received", "webhook_id": webhook_id}), 200
    
    def register_webhook(self, webhook_id: str, callback):
        """
        注册 Webhook 回调函数
        
        Args:
            webhook_id: Webhook 标识
            callback: 回调函数
        """
        self.webhooks[webhook_id] = callback
        print(f"✓ Webhook 已注册：{webhook_id}")
    
    def run(self, host: str = '0.0.0.0', port: int = 5000, debug: bool = False):
        """启动 Webhook 服务器"""
        print(f"🚀 Webhook 服务器启动：http://{host}:{port}")
        self.app.run(host=host, port=port, debug=debug)


# 使用示例
if __name__ == "__main__":
    # 客户端示例
    client = WebhookClient()
    
    # 发送 POST 请求
    result = client.send_post(
        url="https://httpbin.org/post",
        data={"message": "Hello Webhook", "timestamp": "2026-03-14"}
    )
    print(f"响应：{result}")
    
    # 服务端示例（取消注释运行）
    # server = WebhookServer()
    # 
    # def my_callback(data):
    #     print(f"处理数据：{data}")
    # 
    # server.register_webhook("order_created", my_callback)
    # server.run(port=5000)

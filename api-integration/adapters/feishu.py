"""
5. 飞书 API - 开放平台
支持发送消息、操作多维表格、日历等
"""

import requests
import json
from typing import List, Optional, Dict, Any
from adapters.base import BaseAPIAdapter, APIResponse, APIStatus


class FeishuAdapter(BaseAPIAdapter):
    """飞书开放平台适配器"""
    
    def __init__(self, config: dict):
        """
        配置参数:
        - app_id: 应用 App ID
        - app_secret: 应用 Secret
        """
        super().__init__(config)
        self.host = "https://open.feishu.cn"
        self._tenant_access_token = None
    
    def connect(self) -> bool:
        try:
            assert self.config.get('app_id')
            assert self.config.get('app_secret')
            self._tenant_access_token = self._get_tenant_token()
            self._initialized = self._tenant_access_token is not None
            return self._initialized
        except Exception as e:
            print(f"连接失败：{e}")
            return False
    
    def disconnect(self) -> None:
        self._tenant_access_token = None
        self._initialized = False
    
    def _get_tenant_token(self) -> Optional[str]:
        """获取 tenant_access_token"""
        url = f"{self.host}/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.config['app_id'],
            "app_secret": self.config['app_secret']
        }
        response = requests.post(url, json=payload, timeout=30)
        result = response.json()
        if result.get('code') == 0:
            return result.get('tenant_access_token')
        else:
            print(f"获取 token 失败：{result.get('msg')}")
            return None
    
    def _refresh_token_if_needed(self):
        if not self._tenant_access_token:
            self._tenant_access_token = self._get_tenant_token()
    
    def execute(self, action: str, params: dict) -> APIResponse:
        if action == "send_message":
            return self.send_message(params)
        elif action == "get_table_data":
            return self.get_table_data(params)
        elif action == "create_table_record":
            return self.create_table_record(params)
        else:
            return APIResponse(
                status=APIStatus.FAILED,
                error=f"未知操作：{action}"
            )
    
    def send_message(self, params: dict) -> APIResponse:
        """
        发送消息
        
        参数:
        - receive_id: 接收者 ID
        - msg_type: 消息类型 (text/post/image/file)
        - content: 消息内容
        - open_id: 使用 open_id (默认)
        - user_id: 或使用 user_id
        - chat_id: 或使用 chat_id (群聊)
        """
        try:
            self._refresh_token_if_needed()
            
            # 确定接收者类型
            if params.get('chat_id'):
                receive_id = params['chat_id']
                receive_id_type = 'chat_id'
            elif params.get('open_id'):
                receive_id = params['open_id']
                receive_id_type = 'open_id'
            else:
                receive_id = params.get('user_id', '')
                receive_id_type = 'user_id'
            
            url = f"{self.host}/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
            headers = {
                'Authorization': f'Bearer {self._tenant_access_token}',
                'Content-Type': 'application/json'
            }
            payload = {
                "receive_id": receive_id,
                "msg_type": params.get('msg_type', 'text'),
                "content": json.dumps(params['content']) if isinstance(params['content'], dict) else params['content']
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            result = response.json()
            
            if result.get('code') == 0:
                return APIResponse(
                    status=APIStatus.SUCCESS,
                    data=result,
                    status_code=200
                )
            else:
                return APIResponse(
                    status=APIStatus.FAILED,
                    error=result.get('msg', '发送失败'),
                    status_code=200
                )
        except Exception as e:
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def get_table_data(self, params: dict) -> APIResponse:
        """
        获取多维表格数据
        
        参数:
        - app_token: 应用 Token
        - table_id: 表格 ID
        - page_size: 每页数量
        - page_token: 分页令牌
        """
        try:
            self._refresh_token_if_needed()
            
            app_token = params['app_token']
            table_id = params['table_id']
            url = f"{self.host}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            
            headers = {
                'Authorization': f'Bearer {self._tenant_access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            result = response.json()
            
            if result.get('code') == 0:
                return APIResponse(
                    status=APIStatus.SUCCESS,
                    data=result.get('data', {}),
                    status_code=200
                )
            else:
                return APIResponse(
                    status=APIStatus.FAILED,
                    error=result.get('msg', '获取失败'),
                    status_code=200
                )
        except Exception as e:
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def create_table_record(self, params: dict) -> APIResponse:
        """
        创建多维表格记录
        
        参数:
        - app_token: 应用 Token
        - table_id: 表格 ID
        - fields: 字段数据 (dict)
        """
        try:
            self._refresh_token_if_needed()
            
            app_token = params['app_token']
            table_id = params['table_id']
            url = f"{self.host}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            
            headers = {
                'Authorization': f'Bearer {self._tenant_access_token}',
                'Content-Type': 'application/json'
            }
            payload = {
                "fields": params['fields']
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            result = response.json()
            
            if result.get('code') == 0:
                return APIResponse(
                    status=APIStatus.SUCCESS,
                    data=result.get('data', {}),
                    status_code=200
                )
            else:
                return APIResponse(
                    status=APIStatus.FAILED,
                    error=result.get('msg', '创建失败'),
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
        'app_id': 'cli_a1b2c3d4e5f6',
        'app_secret': 'YOUR_APP_SECRET'
    }
    
    feishu = FeishuAdapter(config)
    
    if feishu.connect():
        print("✅ 飞书连接成功")
        
        # 发送文本消息
        response = feishu.execute('send_message', {
            'user_id': 'ou_a1b2c3d4e5f6',
            'msg_type': 'text',
            'content': {'text': '【系统通知】您的任务已分配'}
        })
        
        if response.is_success():
            print(f"✅ 消息发送成功")
        else:
            print(f"❌ 消息发送失败：{response.error}")
    else:
        print("❌ 飞书连接失败")

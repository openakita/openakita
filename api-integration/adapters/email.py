"""
1. 邮件发送 API - 阿里云邮件推送
支持发送邮件、模板邮件、批量发送
"""

import requests
import hashlib
import hmac
import base64
import urllib.parse
from datetime import datetime
from typing import List, Optional
from adapters.base import BaseAPIAdapter, APIResponse, APIStatus


class AliyunEmailAdapter(BaseAPIAdapter):
    """阿里云邮件推送适配器"""
    
    def __init__(self, config: dict):
        """
        配置参数:
        - access_key_id: 阿里云 AccessKey ID
        - access_key_secret: 阿里云 AccessKey Secret
        - account_name: 发件人地址
        - region: 区域 (如 cn-hangzhou)
        """
        super().__init__(config)
        self.endpoint = f"https://dm.{config.get('region', 'cn-hangzhou')}.aliyuncs.com"
    
    def connect(self) -> bool:
        """验证连接"""
        try:
            # 简单验证配置
            assert self.config.get('access_key_id')
            assert self.config.get('access_key_secret')
            assert self.config.get('account_name')
            self._initialized = True
            return True
        except Exception as e:
            print(f"连接失败：{e}")
            return False
    
    def disconnect(self) -> None:
        """断开连接（无状态服务，无需操作）"""
        self._initialized = False
    
    def execute(self, action: str, params: dict) -> APIResponse:
        """执行 API 调用"""
        if action == "send":
            return self.send_email(params)
        elif action == "send_batch":
            return self.send_batch_email(params)
        else:
            return APIResponse(
                status=APIStatus.FAILED,
                error=f"未知操作：{action}"
            )
    
    def _sign_request(self, params: dict) -> dict:
        """生成签名"""
        params['AccessKeyId'] = self.config['access_key_id']
        params['Format'] = 'JSON'
        params['SignatureMethod'] = 'HMAC-SHA1'
        params['SignatureVersion'] = '1.0'
        params['Timestamp'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        params['Version'] = '2015-11-23'
        params['SignatureNonce'] = datetime.utcnow().isoformat()
        
        # 生成签名字符串
        sorted_params = sorted(params.items())
        canonicalized_query_string = '&'.join(
            f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted_params
        )
        
        string_to_sign = f"GET&%2F&{urllib.parse.quote(canonicalized_query_string, safe='')}"
        
        # 计算签名
        secret = self.config['access_key_secret'] + '&'
        signature = hmac.new(
            secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha1
        ).digest()
        
        params['Signature'] = base64.b64encode(signature).decode('utf-8')
        return params
    
    def send_email(self, params: dict) -> APIResponse:
        """
        发送单封邮件
        
        参数:
        - to_address: 收件人地址
        - subject: 邮件主题
        - body: 邮件内容
        - html: 是否为 HTML 格式 (默认 True)
        """
        try:
            query_params = {
                'Action': 'SingleSendMail',
                'AccountName': self.config['account_name'],
                'ReplyToAddress': 'false',
                'AddressType': '1',  # 1 为触发邮件
                'ToAddress': params['to_address'],
                'FromAlias': params.get('from_alias', '系统通知'),
                'Subject': params['subject'],
                'HtmlBody': params['body'] if params.get('html', True) else '',
                'TextBody': '' if params.get('html', True) else params['body']
            }
            
            signed_params = self._sign_request(query_params)
            response = requests.get(self.endpoint, params=signed_params, timeout=30)
            
            if response.status_code == 200:
                return APIResponse(
                    status=APIStatus.SUCCESS,
                    data=response.json(),
                    status_code=200
                )
            else:
                return APIResponse(
                    status=APIStatus.FAILED,
                    error=response.text,
                    status_code=response.status_code
                )
        except Exception as e:
            return APIResponse(
                status=APIStatus.FAILED,
                error=str(e)
            )
    
    def send_batch_email(self, params: dict) -> APIResponse:
        """
        批量发送邮件
        
        参数:
        - recipients: 收件人列表
        - subject: 邮件主题
        - body: 邮件内容
        """
        results = []
        for recipient in params.get('recipients', []):
            result = self.send_email({
                'to_address': recipient,
                'subject': params['subject'],
                'body': params['body'],
                'html': params.get('html', True)
            })
            results.append({'recipient': recipient, 'result': result})
        
        return APIResponse(
            status=APIStatus.SUCCESS,
            data={'results': results}
        )


# ============ 使用示例 ============
if __name__ == "__main__":
    # 配置
    config = {
        'access_key_id': 'YOUR_ACCESS_KEY_ID',
        'access_key_secret': 'YOUR_ACCESS_KEY_SECRET',
        'account_name': 'noreply@yourdomain.com',
        'region': 'cn-hangzhou'
    }
    
    # 初始化适配器
    email_adapter = AliyunEmailAdapter(config)
    
    # 连接验证
    if email_adapter.connect():
        print("✅ 邮件服务连接成功")
        
        # 发送单封邮件
        response = email_adapter.execute('send', {
            'to_address': 'user@example.com',
            'subject': '测试邮件',
            'body': '<h1>您好</h1><p>这是一封测试邮件</p>',
            'html': True
        })
        
        if response.is_success():
            print(f"✅ 邮件发送成功：{response.data}")
        else:
            print(f"❌ 邮件发送失败：{response.error}")
    else:
        print("❌ 邮件服务连接失败")

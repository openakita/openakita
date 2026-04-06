"""
2. 短信通知 API - 阿里云短信服务
支持发送短信、查询发送状态、模板管理
"""

import requests
import hashlib
import hmac
import base64
import urllib.parse
from datetime import datetime
from typing import List
from adapters.base import BaseAPIAdapter, APIResponse, APIStatus


class AliyunSMSAdapter(BaseAPIAdapter):
    """阿里云短信适配器"""
    
    def __init__(self, config: dict):
        """
        配置参数:
        - access_key_id: 阿里云 AccessKey ID
        - access_key_secret: 阿里云 AccessKey Secret
        - sign_name: 短信签名
        - template_code: 模板 CODE
        - region: 区域
        """
        super().__init__(config)
        self.endpoint = "https://dysmsapi.aliyuncs.com"
    
    def connect(self) -> bool:
        try:
            assert self.config.get('access_key_id')
            assert self.config.get('access_key_secret')
            assert self.config.get('sign_name')
            self._initialized = True
            return True
        except Exception as e:
            print(f"连接失败：{e}")
            return False
    
    def disconnect(self) -> None:
        self._initialized = False
    
    def execute(self, action: str, params: dict) -> APIResponse:
        if action == "send":
            return self.send_sms(params)
        elif action == "send_batch":
            return self.send_batch_sms(params)
        else:
            return APIResponse(
                status=APIStatus.FAILED,
                error=f"未知操作：{action}"
            )
    
    def _sign_request(self, params: dict) -> dict:
        params['AccessKeyId'] = self.config['access_key_id']
        params['Format'] = 'JSON'
        params['SignatureMethod'] = 'HMAC-SHA1'
        params['SignatureVersion'] = '1.0'
        params['Timestamp'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        params['Version'] = '2017-05-25'
        params['SignatureNonce'] = datetime.utcnow().isoformat()
        
        sorted_params = sorted(params.items())
        canonicalized_query_string = '&'.join(
            f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted_params
        )
        
        string_to_sign = f"GET&%2F&{urllib.parse.quote(canonicalized_query_string, safe='')}"
        
        secret = self.config['access_key_secret'] + '&'
        signature = hmac.new(
            secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha1
        ).digest()
        
        params['Signature'] = base64.b64encode(signature).decode('utf-8')
        return params
    
    def send_sms(self, params: dict) -> APIResponse:
        """
        发送单条短信
        
        参数:
        - phone_numbers: 手机号
        - template_param: 模板参数 (dict)
        """
        try:
            query_params = {
                'Action': 'SendSms',
                'PhoneNumbers': params['phone_numbers'],
                'SignName': self.config['sign_name'],
                'TemplateCode': self.config['template_code'],
                'TemplateParam': str(params.get('template_param', {})).replace("'", '"')
            }
            
            signed_params = self._sign_request(query_params)
            response = requests.get(self.endpoint, params=signed_params, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('Code') == 'OK':
                    return APIResponse(
                        status=APIStatus.SUCCESS,
                        data=result,
                        status_code=200
                    )
                else:
                    return APIResponse(
                        status=APIStatus.FAILED,
                        error=result.get('Message', '发送失败'),
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
    
    def send_batch_sms(self, params: dict) -> APIResponse:
        """
        批量发送短信
        
        参数:
        - recipients: 手机号列表
        - template_param: 模板参数
        """
        results = []
        for phone in params.get('recipients', []):
            result = self.send_sms({
                'phone_numbers': phone,
                'template_param': params.get('template_param', {})
            })
            results.append({'phone': phone, 'result': result})
        
        return APIResponse(
            status=APIStatus.SUCCESS,
            data={'results': results}
        )


# ============ 使用示例 ============
if __name__ == "__main__":
    config = {
        'access_key_id': 'YOUR_ACCESS_KEY_ID',
        'access_key_secret': 'YOUR_ACCESS_KEY_SECRET',
        'sign_name': '您的签名',
        'template_code': 'SMS_123456789',
        'region': 'cn-hangzhou'
    }
    
    sms_adapter = AliyunSMSAdapter(config)
    
    if sms_adapter.connect():
        print("✅ 短信服务连接成功")
        
        response = sms_adapter.execute('send', {
            'phone_numbers': '13800138000',
            'template_param': {'code': '123456', 'product': '测试产品'}
        })
        
        if response.is_success():
            print(f"✅ 短信发送成功：{response.data}")
        else:
            print(f"❌ 短信发送失败：{response.error}")
    else:
        print("❌ 短信服务连接失败")

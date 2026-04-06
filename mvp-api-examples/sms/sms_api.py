# 短信服务 API 示例（Twilio/阿里云）
# 用于 MVP 短信验证码/通知

import os
from typing import Optional

class SMSClient:
    """短信客户端"""
    
    def __init__(self, provider: str = 'twilio'):
        self.provider = provider
        
        if provider == 'twilio':
            self.account_sid = os.getenv('TWILIO_ACCOUNT_SID', 'your-account-sid')
            self.auth_token = os.getenv('TWILIO_AUTH_TOKEN', 'your-auth-token')
            self.from_number = os.getenv('TWILIO_FROM_NUMBER', '+1234567890')
        elif provider == 'aliyun':
            self.access_key = os.getenv('ALIYUN_ACCESS_KEY', 'your-access-key')
            self.access_secret = os.getenv('ALIYUN_ACCESS_SECRET', 'your-access-secret')
            self.sign_name = os.getenv('ALIYUN_SIGN_NAME', '您的签名')
            self.template_code = os.getenv('ALIYUN_TEMPLATE_CODE', 'SMS_000000000')
    
    def send_sms(self, to_number: str, message: str) -> bool:
        """发送短信"""
        if self.provider == 'twilio':
            return self._send_twilio(to_number, message)
        elif provider == 'aliyun':
            return self._send_aliyun(to_number, message)
    
    def _send_twilio(self, to_number: str, message: str) -> bool:
        """Twilio API 发送"""
        from twilio.rest import Client
        
        try:
            client = Client(self.account_sid, self.auth_token)
            message = client.messages.create(
                body=message,
                from_=self.from_number,
                to=to_number
            )
            return message.sid is not None
        except Exception as e:
            print(f"Twilio Error: {e}")
            return False
    
    def _send_aliyun(self, to_number: str, message: str) -> bool:
        """阿里云短信 API 发送"""
        import requests
        
        # 阿里云短信 API 实现（简化版）
        # 实际使用需要签名和参数计算
        url = "http://dysmsapi.aliyuncs.com/"
        
        params = {
            'Action': 'SendSms',
            'PhoneNumbers': to_number,
            'SignName': self.sign_name,
            'TemplateCode': self.template_code,
            'TemplateParam': f'{{"code":"{message}"}}'
        }
        
        # TODO: 添加签名计算
        response = requests.get(url, params=params)
        return response.status_code == 200
    
    def send_verification_code(self, to_number: str, code: str) -> bool:
        """发送验证码短信"""
        message = f"您的验证码是：{code}，10 分钟内有效。"
        return self.send_sms(to_number, message)
    
    def send_notification(self, to_number: str, content: str) -> bool:
        """发送通知短信"""
        return self.send_sms(to_number, content)

# 使用示例
if __name__ == '__main__':
    # 初始化客户端
    sms_client = SMSClient(provider='twilio')
    
    # 发送验证码
    success = sms_client.send_verification_code(
        to_number="+8613800138000",
        code="123456"
    )
    
    print(f"SMS sent: {success}")

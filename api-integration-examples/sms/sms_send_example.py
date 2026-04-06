# 短信通知 API 示例 (阿里云 + Twilio)
# 安装依赖：pip install aliyun-python-sdk-core twilio

import json
import requests
from typing import Optional
from datetime import datetime

# ============================================
# 方案 1: 阿里云短信服务
# ============================================

class AliyunSmsService:
    """阿里云短信服务"""
    
    def __init__(self, access_key_id: str, access_key_secret: str, sign_name: str):
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.sign_name = sign_name
        self.endpoint = "dysmsapi.aliyuncs.com"
        self.version = "2017-05-25"
    
    def send_sms(self, phone_number: str, template_code: str, template_params: dict) -> bool:
        """发送短信"""
        import hashlib
        import hmac
        import urllib.parse
        import time
        
        # 构建请求参数
        params = {
            "Action": "SendSms",
            "Version": self.version,
            "AccessKeyId": self.access_key_id,
            "Format": "JSON",
            "SignatureMethod": "HMAC-SHA1",
            "Timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "SignatureVersion": "1.0",
            "SignatureNonce": str(time.time()),
            "PhoneNumbers": phone_number,
            "SignName": self.sign_name,
            "TemplateCode": template_code,
            "TemplateParam": json.dumps(template_params)
        }
        
        # 生成签名
        sorted_params = sorted(params.items())
        canonicalized_query_string = '&'.join(
            [f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted_params]
        )
        string_to_sign = f"GET&%2F&{urllib.parse.quote(canonicalized_query_string, safe='')}"
        
        h = hmac.new(
            (self.access_key_secret + "&").encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha1
        )
        signature = urllib.parse.quote(base64.b64encode(h.digest()).decode('utf-8'))
        
        # 发送请求
        url = f"https://{self.endpoint}/?{canonicalized_query_string}&Signature={signature}"
        
        try:
            response = requests.get(url, timeout=30)
            result = response.json()
            
            if result.get("Code") == "OK":
                print(f"✓ 阿里云短信发送成功：{phone_number}")
                return True
            else:
                print(f"✗ 阿里云短信发送失败：{result.get('Message')}")
                return False
                
        except Exception as e:
            print(f"✗ 阿里云短信请求异常：{str(e)}")
            return False
    
    def send_verification_code(self, phone_number: str, code: str) -> bool:
        """发送验证码"""
        return self.send_sms(
            phone_number=phone_number,
            template_code="SMS_123456789",  # 替换为你的模板代码
            template_params={"code": code}
        )


# ============================================
# 方案 2: Twilio 短信服务 (国际短信)
# ============================================

class TwilioSmsService:
    """Twilio 短信服务"""
    
    def __init__(self, account_sid: str, auth_token: str, from_number: str):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.base_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    
    def send_sms(self, to_number: str, body: str) -> bool:
        """发送短信"""
        import base64
        
        auth_string = f"{self.account_sid}:{self.auth_token}"
        auth_header = "Basic " + base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
        
        data = {
            "From": self.from_number,
            "To": to_number,
            "Body": body
        }
        
        try:
            response = requests.post(
                self.base_url,
                data=data,
                headers={"Authorization": auth_header},
                timeout=30
            )
            
            if response.status_code in [200, 201]:
                result = response.json()
                print(f"✓ Twilio 短信发送成功：{to_number}, SID: {result.get('sid')}")
                return True
            else:
                print(f"✗ Twilio 短信发送失败：{response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"✗ Twilio 请求异常：{str(e)}")
            return False
    
    def send_verification_code(self, to_number: str, code: str) -> bool:
        """发送验证码"""
        body = f"Your verification code is: {code}"
        return self.send_sms(to_number, body)


# ============================================
# 方案 3: 腾讯云短信服务
# ============================================

class TencentSmsService:
    """腾讯云短信服务"""
    
    def __init__(self, secret_id: str, secret_key: str, app_id: str, sign_name: str):
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.app_id = app_id
        self.sign_name = sign_name
        self.endpoint = "sms.tencentcloudapi.com"
    
    def send_sms(self, phone_number: str, template_id: str, template_params: list) -> bool:
        """发送短信"""
        import hashlib
        import hmac
        import time
        
        # 腾讯云 API 3.0 签名方法
        payload = json.dumps({
            "PhoneNumberSet": [phone_number],
            "TemplateId": template_id,
            "TemplateParamSet": template_params,
            "SmsSdkAppId": self.app_id,
            "SignName": self.sign_name
        })
        
        headers = {
            "Content-Type": "application/json",
            "X-TC-Action": "SendSms",
            "X-TC-Version": "2021-01-11",
            "X-TC-Timestamp": str(int(time.time())),
            "X-TC-Region": "ap-guangzhou"
        }
        
        # 简化版请求 (实际生产环境需要完整签名)
        try:
            response = requests.post(
                f"https://{self.endpoint}",
                headers=headers,
                data=payload,
                timeout=30
            )
            
            result = response.json()
            if result.get("Response", {}).get("Error") is None:
                print(f"✓ 腾讯云短信发送成功：{phone_number}")
                return True
            else:
                print(f"✗ 腾讯云短信发送失败：{result}")
                return False
                
        except Exception as e:
            print(f"✗ 腾讯云短信请求异常：{str(e)}")
            return False


# ============================================
# 使用示例
# ============================================

if __name__ == "__main__":
    # 阿里云示例
    aliyun_sms = AliyunSmsService(
        access_key_id="your-access-key-id",
        access_key_secret="your-access-key-secret",
        sign_name="你的签名"
    )
    aliyun_sms.send_verification_code("+8613800138000", "123456")
    
    # Twilio 示例
    twilio_sms = TwilioSmsService(
        account_sid="your-account-sid",
        auth_token="your-auth-token",
        from_number="+1234567890"
    )
    twilio_sms.send_verification_code("+8613800138000", "123456")
    
    # 腾讯云示例
    tencent_sms = TencentSmsService(
        secret_id="your-secret-id",
        secret_key="your-secret-key",
        app_id="1400000000",
        sign_name="你的签名"
    )
    tencent_sms.send_sms("+8613800138000", "123456", ["123456"])

"""
API 集成示例 3: 阿里云短信
"""
import requests
import hashlib
import hmac
import datetime
import uuid

class AliyunSMS:
    def __init__(self, access_key, access_secret):
        self.access_key = access_key
        self.access_secret = access_secret
        self.endpoint = "http://dysmsapi.aliyuncs.com"
    
    def send_sms(self, phone_numbers, sign_name, template_code, template_param=None):
        """发送短信"""
        params = {
            "Action": "SendSms",
            "Version": "2017-05-25",
            "AccessKeyId": self.access_key,
            "Format": "JSON",
            "SignName": sign_name,
            "PhoneNumbers": phone_numbers,
            "TemplateCode": template_code,
            "TemplateParam": template_param or "{}",
            "Timestamp": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4())
        }
        
        # 生成签名
        signature = self._generate_signature(params)
        params["Signature"] = signature
        
        response = requests.get(self.endpoint, params=params)
        return response.json()
    
    def _generate_signature(self, params):
        """生成签名 (简化版)"""
        sorted_params = sorted(params.items())
        query_string = "&".join(f"{k}={self._percent_encode(v)}" for k, v in sorted_params)
        string_to_sign = f"GET&%2F&{self._percent_encode(query_string)}"
        
        key = f"{self.access_secret}&"
        signature = hmac.new(key.encode(), string_to_sign.encode(), hashlib.sha1).digest()
        
        import base64
        return base64.b64encode(signature).decode()
    
    def _percent_encode(self, value):
        """URL 编码"""
        from urllib.parse import quote
        return quote(str(value), safe='')

# 使用示例
if __name__ == "__main__":
    sms = AliyunSMS("access_key", "access_secret")
    # result = sms.send_sms("13800138000", "公司名", "SMS_123456", '{"code":"1234"}')
    print("阿里云短信示例已就绪")

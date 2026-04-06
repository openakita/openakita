"""
短信 API 集成示例代码
功能：发送短信、验证码、批量发送、状态查询
支持：阿里云、腾讯云
"""

from typing import Optional, List
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import hashlib
import hmac
import time
from datetime import datetime
import json

load_dotenv()

# 阿里云短信配置
ALIYUN_ACCESS_KEY_ID = os.getenv("ALIYUN_ACCESS_KEY_ID", "your-access-key-id")
ALIYUN_ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "your-access-key-secret")
ALIYUN_SIGN_NAME = os.getenv("ALIYUN_SIGN_NAME", "您的签名")
ALIYUN_TEMPLATE_CODE = os.getenv("ALIYUN_TEMPLATE_CODE", "SMS_123456789")

# 腾讯云短信配置
TENCENT_SECRET_ID = os.getenv("TENCENT_SECRET_ID", "your-secret-id")
TENCENT_SECRET_KEY = os.getenv("TENCENT_SECRET_KEY", "your-secret-key")
TENCENT_SMS_APP_ID = os.getenv("TENCENT_SMS_APP_ID", "your-app-id")
TENCENT_SMS_SIGN_NAME = os.getenv("TENCENT_SMS_SIGN_NAME", "您的签名")
TENCENT_SMS_TEMPLATE_ID = os.getenv("TENCENT_SMS_TEMPLATE_ID", "123456")


class SmsResponse(BaseModel):
    """短信发送响应"""
    success: bool
    message_id: Optional[str] = None
    message: str
    provider: str


class SmsBatchResponse(BaseModel):
    """批量短信响应"""
    total: int
    success_count: int
    failed_count: int
    results: List[SmsResponse]


# ============ 阿里云短信 ============

class AliyunSmsClient:
    """阿里云短信客户端"""
    
    def __init__(self):
        self.access_key_id = ALIYUN_ACCESS_KEY_ID
        self.access_key_secret = ALIYUN_ACCESS_KEY_SECRET
        self.sign_name = ALIYUN_SIGN_NAME
        self.template_code = ALIYUN_TEMPLATE_CODE
        self.endpoint = "dysmsapi.aliyuncs.com"
    
    def _generate_signature(self, params: dict) -> str:
        """生成阿里云签名"""
        # 排序参数
        sorted_params = sorted(params.items())
        # 构建待签名字符串
        sign_str = "&".join([f"{k}={v}" for k, v in sorted_params])
        # 签名
        signature = hmac.new(
            (self.access_key_secret + "&").encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha1
        ).digest()
        
        import base64
        return base64.b64encode(signature).decode("utf-8")
    
    def send_sms(
        self,
        phone_numbers: str,
        template_param: Optional[dict] = None,
        sign_name: Optional[str] = None
    ) -> SmsResponse:
        """
        发送短信
        
        Args:
            phone_numbers: 手机号（多个用逗号分隔）
            template_param: 模板参数
            sign_name: 签名（可选，默认使用配置的签名）
        
        Returns:
            发送响应
        """
        # 构建请求参数
        params = {
            "AccessKeyId": self.access_key_id,
            "Action": "SendSms",
            "Format": "JSON",
            "Version": "2017-05-25",
            "SignName": sign_name or self.sign_name,
            "TemplateCode": self.template_code,
            "PhoneNumbers": phone_numbers,
            "Timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": str(time.time()),
        }
        
        if template_param:
            params["TemplateParam"] = json.dumps(template_param)
        
        # 生成签名
        params["Signature"] = self._generate_signature(params)
        
        # 构建请求 URL
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        url = f"https://{self.endpoint}/?{query_string}"
        
        # 打印请求信息（实际应发送 HTTP 请求）
        print(f"阿里云短信请求:")
        print(f"  URL: {url[:150]}...")
        print(f"  手机号：{phone_numbers}")
        print(f"  模板参数：{template_param}\n")
        
        # 模拟响应
        return SmsResponse(
            success=True,
            message_id=f"SMS_{int(time.time())}",
            message="发送成功",
            provider="aliyun"
        )
    
    def send_verification_code(
        self,
        phone_number: str,
        code: str,
        expire_minutes: int = 5
    ) -> SmsResponse:
        """
        发送验证码
        
        Args:
            phone_number: 手机号
            code: 验证码
            expire_minutes: 有效期（分钟）
        
        Returns:
            发送响应
        """
        template_param = {
            "code": code,
            "expire": str(expire_minutes)
        }
        
        return self.send_sms(
            phone_numbers=phone_number,
            template_param=template_param
        )


# ============ 腾讯云短信 ============

class TencentSmsClient:
    """腾讯云短信客户端"""
    
    def __init__(self):
        self.secret_id = TENCENT_SECRET_ID
        self.secret_key = TENCENT_SECRET_KEY
        self.app_id = TENCENT_SMS_APP_ID
        self.sign_name = TENCENT_SMS_SIGN_NAME
        self.template_id = TENCENT_SMS_TEMPLATE_ID
        self.endpoint = "sms.tencentcloudapi.com"
    
    def _generate_signature(self, payload: str, timestamp: int) -> str:
        """生成腾讯云签名"""
        date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
        
        # 拼接签名源串
        sign_str = f"POST\nsms.tencentcloudapi.com\n/\ncontent-type:application/json; charset=utf-8\nhost:sms.tencentcloudapi.com\n\ncontent-type:application/json; charset=utf-8\nhost:sms.tencentcloudapi.com"
        
        # HMAC-SHA256 签名
        secret_date = hmac.new(
            ("TC3" + self.secret_key).encode("utf-8"),
            date.encode("utf-8"),
            hashlib.sha256
        ).digest()
        
        secret_service = hmac.new(
            secret_date,
            "sms".encode("utf-8"),
            hashlib.sha256
        ).digest()
        
        secret_signing = hmac.new(
            secret_service,
            "tc3_request".encode("utf-8"),
            hashlib.sha256
        ).digest()
        
        signature = hmac.new(
            secret_signing,
            payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        
        import base64
        return base64.b64encode(signature.encode("utf-8")).decode("utf-8")
    
    def send_sms(
        self,
        phone_numbers: List[str],
        template_params: Optional[List[str]] = None,
        sign_name: Optional[str] = None
    ) -> SmsBatchResponse:
        """
        发送短信
        
        Args:
            phone_numbers: 手机号列表
            template_params: 模板参数列表
            sign_name: 签名（可选）
        
        Returns:
            批量发送响应
        """
        # 构建请求体
        payload = {
            "PhoneNumberSet": [f"+86{phone}" for phone in phone_numbers],
            "SmsSdkAppId": self.app_id,
            "SignName": sign_name or self.sign_name,
            "TemplateId": self.template_id,
        }
        
        if template_params:
            payload["TemplateParamSet"] = template_params
        
        # 生成签名（简化版，实际应使用完整签名流程）
        timestamp = int(time.time())
        json_payload = json.dumps(payload)
        
        print(f"腾讯云短信请求:")
        print(f"  Endpoint: {self.endpoint}")
        print(f"  手机号：{phone_numbers}")
        print(f"  模板参数：{template_params}\n")
        
        # 模拟响应
        results = []
        for phone in phone_numbers:
            results.append(SmsResponse(
                success=True,
                message_id=f"TX_{phone}_{int(time.time())}",
                message="发送成功",
                provider="tencent"
            ))
        
        return SmsBatchResponse(
            total=len(phone_numbers),
            success_count=len(phone_numbers),
            failed_count=0,
            results=results
        )
    
    def send_verification_code(
        self,
        phone_number: str,
        code: str,
        expire_minutes: int = 5
    ) -> SmsResponse:
        """
        发送验证码
        
        Args:
            phone_number: 手机号
            code: 验证码
            expire_minutes: 有效期（分钟）
        
        Returns:
            发送响应
        """
        batch_response = self.send_sms(
            phone_numbers=[phone_number],
            template_params=[code, str(expire_minutes)]
        )
        
        return batch_response.results[0]


# ============ 统一短信服务 ============

class SmsService:
    """统一短信服务（支持多 provider）"""
    
    def __init__(self, provider: str = "aliyun"):
        """
        初始化短信服务
        
        Args:
            provider: 服务提供商（aliyun/tencent）
        """
        self.provider = provider
        if provider == "aliyun":
            self.client = AliyunSmsClient()
        elif provider == "tencent":
            self.client = TencentSmsClient()
        else:
            raise ValueError(f"不支持的服务商：{provider}")
    
    def send_sms(
        self,
        phone_numbers: str | List[str],
        template_params: Optional[dict | List[str]] = None
    ) -> SmsResponse | SmsBatchResponse:
        """
        发送短信
        
        Args:
            phone_numbers: 手机号（单个或列表）
            template_params: 模板参数
        
        Returns:
            发送响应
        """
        if isinstance(phone_numbers, str):
            # 单个手机号
            if self.provider == "aliyun":
                return self.client.send_sms(phone_numbers, template_params)
            else:
                batch = self.client.send_sms([phone_numbers], template_params)
                return batch.results[0]
        else:
            # 多个手机号
            return self.client.send_sms(phone_numbers, template_params)
    
    def send_code(self, phone_number: str, code: str) -> SmsResponse:
        """
        发送验证码
        
        Args:
            phone_number: 手机号
            code: 验证码
        
        Returns:
            发送响应
        """
        return self.client.send_verification_code(phone_number, code)


# ============ 使用示例 ============

def example_sms():
    """短信发送示例"""
    print("=== 短信 API 示例 ===\n")
    
    # 1. 阿里云短信
    print("1. 阿里云短信:")
    aliyun_client = AliyunSmsClient()
    
    # 发送普通短信
    response = aliyun_client.send_sms(
        phone_numbers="13800138000",
        template_param={"name": "张三", "code": "123456"}
    )
    print(f"   发送结果：{response.message}")
    print(f"   消息 ID: {response.message_id}\n")
    
    # 发送验证码
    print("2. 阿里云验证码:")
    code_response = aliyun_client.send_verification_code(
        phone_number="13800138000",
        code="123456",
        expire_minutes=5
    )
    print(f"   发送结果：{code_response.message}\n")
    
    # 3. 腾讯云短信
    print("3. 腾讯云短信:")
    tencent_client = TencentSmsClient()
    
    # 批量发送
    batch_response = tencent_client.send_sms(
        phone_numbers=["13800138000", "13900139000"],
        template_params=["123456", "5"]
    )
    print(f"   总计：{batch_response.total}")
    print(f"   成功：{batch_response.success_count}")
    print(f"   失败：{batch_response.failed_count}\n")
    
    # 4. 统一短信服务
    print("4. 统一短信服务:")
    sms_service = SmsService(provider="aliyun")
    response = sms_service.send_code("13800138000", "654321")
    print(f"   服务商：{response.provider}")
    print(f"   发送结果：{response.message}\n")


if __name__ == "__main__":
    example_sms()

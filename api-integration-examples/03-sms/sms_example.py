"""
API 集成示例 03: 短信服务 (阿里云/腾讯云)
========================================
功能：实现短信发送、验证码功能
依赖：pip install aliyun-python-sdk-core aliyun-python-sdk-dysmsapi
"""

import random
import string
from datetime import datetime, timedelta
from typing import Optional, Dict
from pydantic import BaseModel

# ==================== 阿里云短信集成 ====================

class AliyunSMSConfig:
    """阿里云短信配置"""
    ACCESS_KEY_ID = "your_access_key_id"
    ACCESS_KEY_SECRET = "your_access_key_secret"
    SIGN_NAME = "你的签名"
    TEMPLATE_CODE = "SMS_123456789"  # 验证码模板
    REGION_ID = "cn-hangzhou"

class AliyunSMS:
    """阿里云短信服务"""
    
    def __init__(self):
        self.config = AliyunSMSConfig()
        # 实际使用需要初始化客户端
        # from aliyunsdkcore.client import AcsClient
        # self.client = AcsClient(
        #     self.config.ACCESS_KEY_ID,
        #     self.config.ACCESS_KEY_SECRET,
        #     self.config.REGION_ID
        # )
    
    def send_verification_code(self, phone_number: str, code: Optional[str] = None) -> Dict:
        """
        发送验证码短信
        
        Args:
            phone_number: 手机号
            code: 验证码（不传则自动生成）
            
        Returns:
            发送结果
        """
        # 自动生成 6 位数字验证码
        if not code:
            code = ''.join(random.choices(string.digits, k=6))
        
        # 构建请求参数
        params = {
            "PhoneNumbers": phone_number,
            "SignName": self.config.SIGN_NAME,
            "TemplateCode": self.config.TEMPLATE_CODE,
            "TemplateParam": f'{{"code":"{code}"}}'
        }
        
        # 实际调用
        # from aliyunsdkdysmsapi.request.v20170525 import SendSmsRequest
        # request = SendSmsRequest.SendSmsRequest()
        # for k, v in params.items():
        #     request.add_query_param(k, v)
        # response = self.client.do_action_with_exception(request)
        
        # 模拟响应
        return {
            "success": True,
            "code": code,  # 实际生产中不要返回验证码
            "message": "发送成功",
            "biz_id": "1234567890^1234567890",
            "phone_number": phone_number,
            "expire_at": (datetime.now() + timedelta(minutes=5)).isoformat()
        }
    
    def send_template_sms(self, phone_number: str, template_code: str, 
                          template_params: Dict) -> Dict:
        """
        发送模板短信（通知类）
        
        Args:
            phone_number: 手机号
            template_code: 模板 CODE
            template_params: 模板参数
            
        Returns:
            发送结果
        """
        params = {
            "PhoneNumbers": phone_number,
            "SignName": self.config.SIGN_NAME,
            "TemplateCode": template_code,
            "TemplateParam": str(template_params)
        }
        
        # 实际调用阿里云 API
        return {
            "success": True,
            "message": "发送成功",
            "biz_id": "1234567890^1234567890"
        }
    
    def query_send_status(self, biz_id: str, phone_number: str) -> Dict:
        """
        查询短信发送状态
        
        Args:
            biz_id: 发送回执 ID
            phone_number: 手机号
            
        Returns:
            发送状态
        """
        # 实际调用 QuerySendDetails API
        return {
            "status": "DELIVERED",
            "send_time": "2026-03-18 10:30:00",
            "receive_time": "2026-03-18 10:30:05"
        }

# ==================== 腾讯云短信集成 ====================

class TencentSMSConfig:
    """腾讯云短信配置"""
    SECRET_ID = "your_secret_id"
    SECRET_KEY = "your_secret_key"
    SDK_APP_ID = "1400000000"
    SIGN_NAME = "你的签名"
    TEMPLATE_ID = "100000"
    REGION = "ap-guangzhou"

class TencentSMS:
    """腾讯云短信服务"""
    
    def __init__(self):
        self.config = TencentSMSConfig()
        # 实际使用需要初始化客户端
        # from qcloudsms_py import SmsSingleSender
    
    def send_verification_code(self, phone_number: str, 
                               template_id: Optional[str] = None) -> Dict:
        """
        发送验证码短信
        
        Args:
            phone_number: 手机号
            template_id: 模板 ID（不传则使用默认）
            
        Returns:
            发送结果
        """
        # 自动生成验证码
        code = ''.join(random.choices(string.digits, k=6))
        params = [code, "5"]  # 验证码和有效期（分钟）
        
        # 实际调用
        # from qcloudsms_py import SmsSingleSender
        # ssender = SmsSingleSender(self.config.SDK_APP_ID, self.config.APP_KEY)
        # result = ssender.send_with_param(
        #     "86", phone_number, template_id or self.config.TEMPLATE_ID,
        #     params, sign=self.config.SIGN_NAME
        # )
        
        return {
            "success": True,
            "code": code,
            "message": "发送成功",
            "phone_number": phone_number,
            "expire_at": (datetime.now() + timedelta(minutes=5)).isoformat()
        }
    
    def batch_send(self, phone_numbers: list, template_id: str, 
                   params: list) -> Dict:
        """
        群发短信
        
        Args:
            phone_numbers: 手机号列表
            template_id: 模板 ID
            params: 模板参数
            
        Returns:
            发送结果
        """
        results = []
        for phone in phone_numbers:
            result = self.send_verification_code(phone, template_id)
            results.append({
                "phone": phone,
                "success": result["success"]
            })
        
        return {
            "total": len(phone_numbers),
            "success_count": sum(1 for r in results if r["success"]),
            "results": results
        }

# ==================== 短信服务封装（支持多服务商） ====================

class SMSProvider:
    """短信服务商枚举"""
    ALIYUN = "aliyun"
    TENCENT = "tencent"

class SMSService:
    """短信服务（支持多服务商切换）"""
    
    def __init__(self, provider: str = SMSProvider.ALIYUN):
        self.provider = provider
        if provider == SMSProvider.ALIYUN:
            self.client = AliyunSMS()
        elif provider == SMSProvider.TENCENT:
            self.client = TencentSMS()
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    def send_code(self, phone: str) -> Dict:
        """发送验证码"""
        return self.client.send_verification_code(phone)
    
    def verify_code(self, phone: str, code: str, stored_code: str) -> bool:
        """
        验证验证码
        
        Args:
            phone: 手机号
            code: 用户输入的验证码
            stored_code: 存储的验证码
            
        Returns:
            验证是否通过
        """
        return code == stored_code

# ==================== 使用示例 ====================

if __name__ == "__main__":
    # 阿里云短信示例
    aliyun_sms = AliyunSMS()
    result = aliyun_sms.send_verification_code("13800138000")
    print(f"阿里云短信发送结果：{result}")
    
    # 腾讯云短信示例
    tencent_sms = TencentSMS()
    result = tencent_sms.send_verification_code("13800138000")
    print(f"腾讯云短信发送结果：{result}")
    
    # 统一服务示例
    sms_service = SMSService(provider=SMSProvider.ALIYUN)
    result = sms_service.send_code("13800138000")
    print(f"统一短信服务结果：{result}")

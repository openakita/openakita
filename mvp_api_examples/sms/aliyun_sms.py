"""
阿里云短信服务集成示例
用于 MVP 验证码发送、通知短信等场景
"""
import hashlib
import hmac
import base64
import uuid
import datetime
from typing import Dict, List, Optional
from urllib.parse import quote
import requests


class AliyunSMSClient:
    """
    阿里云短信服务客户端
    
    使用场景:
    - 用户注册/登录验证码
    - 密码重置验证码
    - 订单通知短信
    - 系统告警通知
    """
    
    def __init__(self, access_key_id: str, access_key_secret: str, 
                 sign_name: str):
        """
        初始化阿里云短信客户端
        
        Args:
            access_key_id: AccessKey ID
            access_key_secret: AccessKey Secret
            sign_name: 短信签名（需在阿里云控制台申请）
        """
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.sign_name = sign_name
        self.endpoint = "dysmsapi.aliyuncs.com"
        self.version = "2017-05-25"
    
    def _generate_signature(self, params: Dict) -> str:
        """
        生成请求签名
        
        Args:
            params: 请求参数（不含 Signature）
        
        Returns:
            签名字符串
        """
        # 按参数名排序
        sorted_params = sorted(params.items())
        
        # 构造待签名字符串
        canonicalized_query_string = "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}" 
            for k, v in sorted_params
        )
        
        # 构造待签名字符串
        string_to_sign = f"GET&{quote('/', safe='')}&{quote(canonicalized_query_string, safe='')}"
        
        # 计算签名
        hmac_code = hmac.new(
            f"{self.access_key_secret}&".encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha1
        ).digest()
        
        signature = base64.b64encode(hmac_code).decode("utf-8")
        return signature
    
    def _build_request_url(self, params: Dict) -> str:
        """
        构建完整的请求 URL
        
        Args:
            params: 请求参数
        
        Returns:
            完整的请求 URL
        """
        # 添加公共参数
        params["AccessKeyId"] = self.access_key_id
        params["Action"] = "SendSms"
        params["Format"] = "JSON"
        params["Version"] = self.version
        params["Timestamp"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        params["SignatureMethod"] = "HMAC-SHA1"
        params["SignatureVersion"] = "1.0"
        params["SignatureNonce"] = str(uuid.uuid4())
        
        # 生成签名
        signature = self._generate_signature(params)
        params["Signature"] = signature
        
        # 构建 URL
        query_string = "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}" 
            for k, v in sorted(params.items())
        )
        
        url = f"https://{self.endpoint}/?{query_string}"
        return url
    
    def send_sms(self, phone_numbers: List[str], template_code: str, 
                 template_params: Dict = None) -> Dict:
        """
        发送短信
        
        Args:
            phone_numbers: 手机号列表（国内）
            template_code: 短信模板 CODE（需在阿里云控制台申请）
            template_params: 模板参数（字典）
        
        Returns:
            API 响应字典
        """
        params = {
            "PhoneNumbers": ",".join(phone_numbers),
            "SignName": self.sign_name,
            "TemplateCode": template_code,
        }
        
        if template_params:
            import json
            params["TemplateParam"] = json.dumps(template_params, ensure_ascii=False)
        
        url = self._build_request_url(params)
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {
                "Code": "RequestError",
                "Message": f"请求失败：{str(e)}"
            }
    
    def send_verification_code(self, phone_number: str, code: str,
                               template_code: str = "SMS_123456789") -> Dict:
        """
        发送验证码短信
        
        Args:
            phone_number: 手机号
            code: 验证码
            template_code: 验证码模板 CODE
        
        Returns:
            API 响应字典
        """
        return self.send_sms(
            phone_numbers=[phone_number],
            template_code=template_code,
            template_params={"code": code}
        )
    
    def send_order_notification(self, phone_number: str, order_id: str,
                               order_status: str, 
                               template_code: str = "SMS_987654321") -> Dict:
        """
        发送订单通知短信
        
        Args:
            phone_number: 手机号
            order_id: 订单号
            order_status: 订单状态
            template_code: 通知模板 CODE
        
        Returns:
            API 响应字典
        """
        return self.send_sms(
            phone_numbers=[phone_number],
            template_code=template_code,
            template_params={
                "order_id": order_id,
                "order_status": order_status
            }
        )
    
    def send_batch_sms(self, phone_numbers: List[str], template_code: str,
                       template_params_list: List[Dict] = None) -> Dict:
        """
        批量发送短信
        
        Args:
            phone_numbers: 手机号列表
            template_code: 短信模板 CODE
            template_params_list: 模板参数列表（与手机号一一对应）
        
        Returns:
            API 响应字典
        """
        if template_params_list and len(template_params_list) != len(phone_numbers):
            return {
                "Code": "InvalidParams",
                "Message": "模板参数数量与手机号数量不匹配"
            }
        
        # 批量发送（阿里云支持一次最多 1000 个）
        results = []
        for i, phone in enumerate(phone_numbers):
            params = template_params_list[i] if template_params_list else None
            result = self.send_sms(
                phone_numbers=[phone],
                template_code=template_code,
                template_params=params
            )
            results.append({"phone": phone, "result": result})
        
        return {
            "Code": "OK",
            "Message": f"批量发送完成，共 {len(phone_numbers)} 条",
            "details": results
        }


# ============== 使用示例 ==============

def example_usage():
    """使用示例"""
    
    # 配置（从环境变量或配置文件中获取）
    ACCESS_KEY_ID = "your-access-key-id"
    ACCESS_KEY_SECRET = "your-access-key-secret"
    SIGN_NAME = "MVP 平台"
    
    # 初始化客户端
    client = AliyunSMSClient(ACCESS_KEY_ID, ACCESS_KEY_SECRET, SIGN_NAME)
    
    # 示例 1: 发送验证码
    print("=== 发送验证码 ===")
    result = client.send_verification_code(
        phone_number="13800138000",
        code="123456",
        template_code="SMS_123456789"  # 替换为实际模板 CODE
    )
    print(f"结果：{result}")
    
    # 示例 2: 发送订单通知
    print("\n=== 发送订单通知 ===")
    result = client.send_order_notification(
        phone_number="13800138000",
        order_id="ORDER_123456",
        order_status="已发货",
        template_code="SMS_987654321"  # 替换为实际模板 CODE
    )
    print(f"结果：{result}")
    
    # 示例 3: 批量发送短信
    print("\n=== 批量发送短信 ===")
    result = client.send_batch_sms(
        phone_numbers=["13800138000", "13900139000"],
        template_code="SMS_123456789",
        template_params_list=[
            {"code": "111111"},
            {"code": "222222"}
        ]
    )
    print(f"结果：{result}")


if __name__ == "__main__":
    example_usage()

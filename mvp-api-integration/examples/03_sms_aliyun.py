"""
API 集成示例 03: 阿里云短信服务

功能：
- 发送验证码短信
- 发送通知短信
- 批量发送短信
- 查询发送状态

依赖：
pip install aliyun-python-sdk-core aliyun-python-sdk-dysmsapi

文档：
https://help.aliyun.com/product/44282.html
"""

from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest
import json

# ============ 配置区域 ============
ACCESS_KEY_ID = "your_access_key_id"
ACCESS_KEY_SECRET = "your_access_key_secret"
REGION_ID = "cn-hangzhou"
SIGN_NAME = "你的签名名称"  # 需在阿里云控制台备案
TEMPLATE_CODE = "SMS_123456789"  # 验证码模板 ID
NOTIFY_TEMPLATE_CODE = "SMS_987654321"  # 通知模板 ID
# =================================


class AliyunSMSClient:
    """阿里云短信客户端"""
    
    def __init__(self, access_key_id, access_key_secret, region_id=REGION_ID):
        self.client = AcsClient(access_key_id, access_key_secret, region_id)
    
    def send_verification_code(self, phone_number, code, template_code=TEMPLATE_CODE):
        """
        发送验证码短信
        
        Args:
            phone_number: 手机号
            code: 验证码
            template_code: 模板 ID
        
        Returns:
            dict: 发送结果
        """
        request = CommonRequest()
        request.set_method('POST')
        request.set_domain('dysmsapi.aliyuncs.com')
        request.set_version('2017-05-25')
        request.set_action_name('SendSms')
        
        request.add_query_param('RegionId', self.client.region_id)
        request.add_query_param('PhoneNumbers', phone_number)
        request.add_query_param('SignName', SIGN_NAME)
        request.add_query_param('TemplateCode', template_code)
        request.add_query_param('TemplateParam', json.dumps({'code': code}))
        
        response = self.client.do_action_with_exception(request)
        return json.loads(response)
    
    def send_notification(self, phone_number, params, template_code=NOTIFY_TEMPLATE_CODE):
        """
        发送通知短信
        
        Args:
            phone_number: 手机号
            params: 模板参数 dict
            template_code: 模板 ID
        
        Returns:
            dict: 发送结果
        """
        request = CommonRequest()
        request.set_method('POST')
        request.set_domain('dysmsapi.aliyuncs.com')
        request.set_version('2017-05-25')
        request.set_action_name('SendSms')
        
        request.add_query_param('RegionId', self.client.region_id)
        request.add_query_param('PhoneNumbers', phone_number)
        request.add_query_param('SignName', SIGN_NAME)
        request.add_query_param('TemplateCode', template_code)
        request.add_query_param('TemplateParam', json.dumps(params))
        
        response = self.client.do_action_with_exception(request)
        return json.loads(response)
    
    def batch_send(self, phone_numbers, template_code, template_params):
        """
        批量发送短信
        
        Args:
            phone_numbers: 手机号列表
            template_code: 模板 ID
            template_params: 模板参数列表 (与手机号一一对应)
        
        Returns:
            dict: 发送结果
        """
        results = []
        for phone, params in zip(phone_numbers, template_params):
            result = self.send_notification(phone, params, template_code)
            results.append({
                'phone': phone,
                'result': result
            })
        return results


# ============ 使用示例 ============
if __name__ == "__main__":
    print("=" * 50)
    print("阿里云短信 API 集成示例")
    print("=" * 50)
    
    sms_client = AliyunSMSClient(ACCESS_KEY_ID, ACCESS_KEY_SECRET)
    
    # 1. 发送验证码
    print("\n1️⃣  发送验证码短信")
    phone = "13800138000"
    code = "123456"
    # result = sms_client.send_verification_code(phone, code)
    print(f"手机号：{phone}")
    print(f"验证码：{code}")
    print("⚠️  实际环境调用 send_verification_code 发送")
    
    # 2. 发送通知短信
    print("\n2️⃣  发送通知短信")
    # result = sms_client.send_notification(
    #     phone,
    #     {'order_no': 'ORDER123', 'amount': '99.00'}
    # )
    print("通知内容：订单 ORDER123 支付成功，金额 99.00 元")
    print("⚠️  实际环境调用 send_notification 发送")
    
    # 3. 批量发送
    print("\n3️⃣  批量发送示例")
    # phones = ["13800138000", "13900139000"]
    # params = [
    #     {'name': '张三', 'code': 'A001'},
    #     {'name': '李四', 'code': 'A002'}
    # ]
    # results = sms_client.batch_send(phones, TEMPLATE_CODE, params)
    print("⚠️  实际环境调用 batch_send 批量发送")
    
    print("\n" + "=" * 50)
    print("关键要点:")
    print("1. 签名和模板需提前在阿里云备案")
    print("2. 单个手机号每天发送有限制 (默认 10 条)")
    print("3. 验证码短信需设置有效期 (通常 5 分钟)")
    print("4. 建议添加发送频率限制 (防刷)")
    print("=" * 50)

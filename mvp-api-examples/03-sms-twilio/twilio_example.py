# Twilio 短信通知 API 集成示例
# 适用于 MVP 短信验证码、通知提醒

import os
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException


class TwilioClient:
    """Twilio 短信客户端封装"""
    
    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID", "your-account-sid")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN", "your-auth-token")
        self.from_number = os.getenv("TWILIO_FROM_NUMBER", "+1234567890")
        self.client = Client(self.account_sid, self.auth_token)
    
    def send_sms(self, to_number: str, body: str) -> dict:
        """
        发送短信
        
        Args:
            to_number: 收件人手机号（带国家码，如 +8613800138000）
            body: 短信内容
        
        Returns:
            发送结果
        """
        try:
            message = self.client.messages.create(
                body=body,
                from_=self.from_number,
                to=to_number
            )
            return {
                "success": True,
                "message_sid": message.sid,
                "status": message.status
            }
        except TwilioRestException as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def send_verification_code(self, to_number: str, code: str, expire_minutes: int = 5) -> dict:
        """
        发送验证码短信
        
        Args:
            to_number: 收件人手机号
            code: 验证码
            expire_minutes: 有效期（分钟）
        
        Returns:
            发送结果
        """
        body = f"【Your App】您的验证码是 {code}，{expire_minutes}分钟内有效。请勿泄露给他人。"
        return self.send_sms(to_number, body)
    
    def send_order_notification(self, to_number: str, order_info: dict) -> dict:
        """
        发送订单通知短信
        
        Args:
            to_number: 收件人手机号
            order_info: 订单信息 {order_id, amount, status}
        
        Returns:
            发送结果
        """
        order_id = order_info.get("order_id", "N/A")
        amount = order_info.get("amount", 0)
        status = order_info.get("status", "未知")
        
        body = f"【Your App】订单 {order_id} 已{status}，金额：¥{amount}。详情：https://yourapp.com/orders/{order_id}"
        return self.send_sms(to_number, body)
    
    def send_reminder(self, to_number: str, reminder_info: dict) -> dict:
        """
        发送提醒短信
        
        Args:
            to_number: 收件人手机号
            reminder_info: 提醒信息 {title, time, location}
        
        Returns:
            发送结果
        """
        title = reminder_info.get("title", "提醒")
        time = reminder_info.get("time", "待定")
        location = reminder_info.get("location", "")
        
        body = f"【Your App】提醒：{title}\n时间：{time}"
        if location:
            body += f"\n地点：{location}"
        
        return self.send_sms(to_number, body)
    
    def get_message_status(self, message_sid: str) -> dict:
        """
        查询短信发送状态
        
        Args:
            message_sid: 消息 SID
        
        Returns:
            消息状态
        """
        try:
            message = self.client.messages(message_sid).fetch()
            return {
                "success": True,
                "status": message.status,
                "date_sent": str(message.date_sent),
                "error_code": message.error_code
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def send_bulk_sms(self, recipients: list, body: str) -> dict:
        """
        批量发送短信
        
        Args:
            recipients: 收件人列表 ['+8613800138000', ...]
            body: 短信内容
        
        Returns:
            发送结果统计
        """
        results = {"success": 0, "failed": 0, "errors": []}
        
        for number in recipients:
            result = self.send_sms(number, body)
            if result["success"]:
                results["success"] += 1
            else:
                results["failed"] += 1
                results["errors"].append({
                    "number": number,
                    "error": result["error"]
                })
        
        results["total"] = len(recipients)
        return results


# 使用示例
if __name__ == "__main__":
    client = TwilioClient()
    
    # 1. 发送普通短信
    result = client.send_sms(
        to_number="+8613800138000",
        body="这是一条测试短信"
    )
    print(f"发送结果：{result}")
    
    # 2. 发送验证码
    result = client.send_verification_code(
        to_number="+8613800138000",
        code="123456"
    )
    print(f"验证码短信：{result}")
    
    # 3. 发送订单通知
    result = client.send_order_notification(
        to_number="+8613800138000",
        order_info={
            "order_id": "ORD20260317001",
            "amount": 199.00,
            "status": "已发货"
        }
    )
    print(f"订单通知：{result}")
    
    # 4. 查询状态
    if result["success"]:
        status = client.get_message_status(result["message_sid"])
        print(f"消息状态：{status}")

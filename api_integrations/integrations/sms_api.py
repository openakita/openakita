"""
SMS API 集成 - Twilio
"""
from typing import List, Dict, Any, Optional
from .base_client import BaseAPIClient, APIError


class TwilioClient(BaseAPIClient):
    """Twilio SMS API 客户端"""
    
    def __init__(self, account_sid: str, auth_token: str, from_number: str):
        super().__init__(
            base_url=f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}",
            api_key=auth_token
        )
        self.account_sid = account_sid
        self.from_number = from_number
    
    def _get_headers(self) -> Dict[str, str]:
        import base64
        auth_string = f"{self.account_sid}:{self.api_key}"
        auth_bytes = base64.b64encode(auth_string.encode()).decode()
        return {
            "Authorization": f"Basic {auth_bytes}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
    
    async def send_sms(
        self,
        to: str,
        body: str,
        from_number: Optional[str] = None
    ) -> Dict[str, Any]:
        """发送短信"""
        data = {
            "To": to,
            "From": from_number or self.from_number,
            "Body": body
        }
        
        return await self.post("/Messages.json", data=data)
    
    async def send_bulk_sms(
        self,
        recipients: List[str],
        body: str,
        from_number: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """批量发送短信"""
        results = []
        for to in recipients:
            try:
                result = await self.send_sms(to, body, from_number)
                results.append({"success": True, "to": to, "data": result})
            except APIError as e:
                results.append({"success": False, "to": to, "error": str(e)})
        
        return results
    
    async def get_message_status(self, message_sid: str) -> Dict[str, Any]:
        """获取短信状态"""
        return await self.get(f"/Messages/{message_sid}.json")
    
    async def list_messages(self, limit: int = 10) -> List[Dict[str, Any]]:
        """列出短信"""
        response = await self.get(f"/Messages.json?PageSize={limit}")
        return response.get("messages", [])
    
    async def test_connection(self) -> bool:
        """测试连接"""
        try:
            await self.get("")
            return True
        except APIError:
            return False


# 使用示例
async def example_twilio():
    """Twilio 使用示例"""
    from config import APIConfig
    
    async with TwilioClient(
        APIConfig.TWILIO_ACCOUNT_SID,
        APIConfig.TWILIO_AUTH_TOKEN,
        APIConfig.TWILIO_PHONE_NUMBER
    ) as client:
        # 发送单条短信
        message = await client.send_sms(
            to="+8613800138000",
            body="【MVP】您的验证码是：123456"
        )
        
        # 批量发送
        results = await client.send_bulk_sms(
            recipients=["+8613800138000", "+8613900139000"],
            body="【MVP】系统通知：功能已上线"
        )
        
        # 查询状态
        status = await client.get_message_status(message["sid"])

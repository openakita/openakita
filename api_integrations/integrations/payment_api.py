"""
支付 API 集成 - Stripe
"""
from typing import Dict, Any, Optional, List
from .base_client import BaseAPIClient, APIError


class StripeClient(BaseAPIClient):
    """Stripe 支付 API 客户端"""
    
    def __init__(self, api_key: str):
        super().__init__(
            base_url="https://api.stripe.com/v1",
            api_key=api_key
        )
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
    
    async def create_payment_intent(
        self,
        amount: int,
        currency: str = "usd",
        customer_id: Optional[str] = None,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """创建支付意图"""
        data = {
            "amount": amount,  # 最小单位（美分）
            "currency": currency,
        }
        if customer_id:
            data["customer"] = customer_id
        if description:
            data["description"] = description
        
        return await self.post("/payment_intents", data=data)
    
    async def create_customer(
        self,
        email: str,
        name: Optional[str] = None,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """创建客户"""
        data = {"email": email}
        if name:
            data["name"] = name
        if description:
            data["description"] = description
        
        return await self.post("/customers", data=data)
    
    async def get_customer(self, customer_id: str) -> Dict[str, Any]:
        """获取客户信息"""
        return await self.get(f"/customers/{customer_id}")
    
    async def create_product(
        self,
        name: str,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """创建产品"""
        data = {"name": name}
        if description:
            data["description"] = description
        
        return await self.post("/products", data=data)
    
    async def create_price(
        self,
        product_id: str,
        unit_amount: int,
        currency: str = "usd"
    ) -> Dict[str, Any]:
        """创建价格"""
        data = {
            "product": product_id,
            "unit_amount": unit_amount,
            "currency": currency,
        }
        
        return await self.post("/prices", data=data)
    
    async def create_checkout_session(
        self,
        line_items: List[Dict[str, Any]],
        success_url: str,
        cancel_url: str,
        mode: str = "payment"
    ) -> Dict[str, Any]:
        """创建结账会话"""
        data = {
            "line_items": line_items,
            "mode": mode,
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        
        return await self.post("/checkout/sessions", data=data)
    
    async def get_payment_intent(self, intent_id: str) -> Dict[str, Any]:
        """获取支付意图"""
        return await self.get(f"/payment_intents/{intent_id}")
    
    async def test_connection(self) -> bool:
        """测试连接"""
        try:
            await self.get("/account")
            return True
        except APIError:
            return False


# 使用示例
async def example_stripe():
    """Stripe 使用示例"""
    from config import APIConfig
    
    async with StripeClient(APIConfig.STRIPE_API_KEY) as client:
        # 创建客户
        customer = await client.create_customer(
            email="customer@example.com",
            name="张三"
        )
        
        # 创建产品
        product = await client.create_product(
            name="MVP 专业版",
            description="月度订阅"
        )
        
        # 创建价格（99 美元）
        price = await client.create_price(
            product_id=product["id"],
            unit_amount=9900,
            currency="usd"
        )
        
        # 创建支付意图
        payment = await client.create_payment_intent(
            amount=9900,
            currency="usd",
            customer_id=customer["id"],
            description="MVP 专业版订阅"
        )

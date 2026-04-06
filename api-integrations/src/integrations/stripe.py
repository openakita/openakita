"""
Stripe API 客户端
支持支付处理、客户管理、订阅管理、退款等功能
文档：https://stripe.com/docs/api
"""
from typing import List, Optional, Dict, Any
from .base import BaseAPIClient, APIError
import structlog

logger = structlog.get_logger()


class StripeClient(BaseAPIClient):
    """Stripe API 客户端"""
    
    def __init__(self, api_key: str, base_url: str = "https://api.stripe.com/v1"):
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            timeout=30
        )
        self.api_key = api_key
    
    def _get_auth_header(self) -> str:
        """Stripe 使用 Bearer Token 认证"""
        return f"Bearer {self.api_key}"
    
    def _get_default_headers(self) -> Dict[str, str]:
        """Stripe 需要额外的 Stripe-Version 头"""
        headers = super()._get_default_headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Stripe-Version"] = "2023-10-16"
        return headers
    
    async def test_auth(self) -> bool:
        """测试认证是否有效"""
        try:
            response = await self.get("/account")
            return "id" in response
        except APIError:
            return False
    
    async def create_customer(
        self,
        email: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        创建客户
        
        Args:
            email: 邮箱
            name: 姓名
            description: 描述
            metadata: 元数据
            
        Returns:
            创建的客户信息
        """
        data = {}
        if email:
            data["email"] = email
        if name:
            data["name"] = name
        if description:
            data["description"] = description
        if metadata:
            for key, value in metadata.items():
                data[f"metadata[{key}]"] = value
        
        response = await self.post("/customers", json_data=data)
        
        logger.info("stripe_customer_created", customer_id=response.get("id"))
        return response
    
    async def get_customer(self, customer_id: str) -> Dict[str, Any]:
        """获取客户信息"""
        response = await self.get(f"/customers/{customer_id}")
        return response
    
    async def update_customer(
        self,
        customer_id: str,
        email: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """更新客户信息"""
        data = {}
        if email:
            data["email"] = email
        if name:
            data["name"] = name
        if description:
            data["description"] = description
        if metadata:
            for key, value in metadata.items():
                data[f"metadata[{key}]"] = value
        
        response = await self.post(f"/customers/{customer_id}", json_data=data)
        return response
    
    async def create_payment_intent(
        self,
        amount: int,
        currency: str = "usd",
        customer_id: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        创建支付意图
        
        Args:
            amount: 金额（最小货币单位，如美分）
            currency: 货币代码
            customer_id: 客户 ID
            description: 描述
            metadata: 元数据
            
        Returns:
            支付意图信息
        """
        data = {
            "amount": amount,
            "currency": currency,
        }
        
        if customer_id:
            data["customer"] = customer_id
        if description:
            data["description"] = description
        if metadata:
            for key, value in metadata.items():
                data[f"metadata[{key}]"] = value
        
        response = await self.post("/payment_intents", json_data=data)
        
        logger.info("stripe_payment_intent_created", payment_intent_id=response.get("id"), amount=amount)
        return response
    
    async def get_payment_intent(self, payment_intent_id: str) -> Dict[str, Any]:
        """获取支付意图详情"""
        response = await self.get(f"/payment_intents/{payment_intent_id}")
        return response
    
    async def confirm_payment_intent(
        self,
        payment_intent_id: str,
        payment_method_id: str
    ) -> Dict[str, Any]:
        """确认支付意图"""
        data = {
            "payment_method": payment_method_id
        }
        response = await self.post(f"/payment_intents/{payment_intent_id}/confirm", json_data=data)
        return response
    
    async def create_refund(
        self,
        payment_intent_id: str,
        amount: Optional[int] = None,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        创建退款
        
        Args:
            payment_intent_id: 支付意图 ID
            amount: 退款金额（不填则全额退款）
            reason: 退款原因
            
        Returns:
            退款信息
        """
        data = {
            "payment_intent": payment_intent_id
        }
        
        if amount:
            data["amount"] = amount
        if reason:
            data["reason"] = reason
        
        response = await self.post("/refunds", json_data=data)
        
        logger.info("stripe_refund_created", refund_id=response.get("id"), payment_intent=payment_intent_id)
        return response
    
    async def create_product(
        self,
        name: str,
        description: Optional[str] = None,
        active: bool = True,
        metadata: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        创建产品
        
        Args:
            name: 产品名称
            description: 描述
            active: 是否激活
            metadata: 元数据
            
        Returns:
            产品信息
        """
        data = {
            "name": name,
            "active": active
        }
        
        if description:
            data["description"] = description
        if metadata:
            for key, value in metadata.items():
                data[f"metadata[{key}]"] = value
        
        response = await self.post("/products", json_data=data)
        
        logger.info("stripe_product_created", product_id=response.get("id"))
        return response
    
    async def create_price(
        self,
        product_id: str,
        unit_amount: int,
        currency: str = "usd",
        recurring: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        创建价格
        
        Args:
            product_id: 产品 ID
            unit_amount: 单价（最小货币单位）
            currency: 货币代码
            recurring: 订阅周期配置 {"interval": "month/year"}
            
        Returns:
            价格信息
        """
        data = {
            "product": product_id,
            "unit_amount": unit_amount,
            "currency": currency,
        }
        
        if recurring:
            data["recurring"] = recurring
        
        response = await self.post("/prices", json_data=data)
        
        logger.info("stripe_price_created", price_id=response.get("id"))
        return response
    
    async def create_subscription(
        self,
        customer_id: str,
        items: List[Dict[str, str]],
        trial_period_days: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        创建订阅
        
        Args:
            customer_id: 客户 ID
            items: 订阅项列表 [{"price": "price_xxx"}]
            trial_period_days: 试用期天数
            
        Returns:
            订阅信息
        """
        data = {
            "customer": customer_id,
            "items": items
        }
        
        if trial_period_days:
            data["trial_period_days"] = trial_period_days
        
        response = await self.post("/subscriptions", json_data=data)
        
        logger.info("stripe_subscription_created", subscription_id=response.get("id"))
        return response
    
    async def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """获取订阅详情"""
        response = await self.get(f"/subscriptions/{subscription_id}")
        return response
    
    async def cancel_subscription(
        self,
        subscription_id: str,
        at_period_end: bool = False
    ) -> Dict[str, Any]:
        """
        取消订阅
        
        Args:
            subscription_id: 订阅 ID
            at_period_end: 是否在周期结束时取消
            
        Returns:
            取消后的订阅信息
        """
        data = {
            "at_period_end": at_period_end
        }
        response = await self.delete(f"/subscriptions/{subscription_id}", json_data=data)
        return response
    
    async def list_charges(
        self,
        customer_id: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """列出支付记录"""
        params = {"limit": limit}
        if customer_id:
            params["customer"] = customer_id
        
        response = await self.get("/charges", params=params)
        return response.get("data", [])


# 使用示例
async def example_usage():
    """Stripe API 使用示例"""
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    api_key = os.getenv("STRIPE_SECRET_KEY")
    if not api_key:
        print("❌ 请设置 STRIPE_SECRET_KEY 环境变量")
        return
    
    # 使用测试密钥
    if not api_key.startswith("sk_test_"):
        print("⚠️  建议使用测试密钥 (sk_test_)")
    
    async with StripeClient(api_key) as client:
        # 测试认证
        is_valid = await client.test_auth()
        print(f"✅ 认证有效：{is_valid}")
        
        # 创建客户
        customer = await client.create_customer(
            email="test@example.com",
            name="测试用户"
        )
        print(f"👤 创建客户：{customer.get('id')}")
        
        # 创建支付意图
        payment_intent = await client.create_payment_intent(
            amount=1000,  # $10.00
            currency="usd",
            customer_id=customer.get("id"),
            description="测试支付"
        )
        print(f"💳 创建支付意图：{payment_intent.get('id')}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(example_usage())

# Stripe 支付 API 集成示例
# 适用于 MVP 订阅支付、一次性支付

import os
import stripe
from typing import Optional


class StripeClient:
    """Stripe 支付客户端封装"""
    
    def __init__(self):
        self.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_xxx")
        self.webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_xxx")
        self.success_url = os.getenv("STRIPE_SUCCESS_URL", "https://yourapp.com/success")
        self.cancel_url = os.getenv("STRIPE_CANCEL_URL", "https://yourapp.com/cancel")
        stripe.api_key = self.api_key
    
    def create_payment_intent(self, amount: int, currency: str = "cny", customer_id: str = None) -> dict:
        """
        创建支付意图（一次性支付）
        
        Args:
            amount: 金额（最小单位，如分）
            currency: 货币代码（cny, usd）
            customer_id: 客户 ID（可选）
        
        Returns:
            支付意图信息
        """
        try:
            params = {
                "amount": amount,
                "currency": currency,
                "payment_method_types": ["card", "alipay", "wechat_pay"],
            }
            if customer_id:
                params["customer"] = customer_id
            
            intent = stripe.PaymentIntent.create(**params)
            return {
                "success": True,
                "client_secret": intent.client_secret,
                "payment_intent_id": intent.id,
                "amount": intent.amount,
                "currency": intent.currency
            }
        except stripe.error.StripeError as e:
            return {"success": False, "error": str(e)}
    
    def create_checkout_session(self, price_id: str, customer_id: str = None, metadata: dict = None) -> dict:
        """
        创建结账会话（推荐用于电商）
        
        Args:
            price_id: 价格 ID（在 Stripe Dashboard 创建）
            customer_id: 客户 ID（可选）
            metadata: 元数据（可选）
        
        Returns:
            结账会话信息
        """
        try:
            params = {
                "payment_method_types": ["card", "alipay", "wechat_pay"],
                "line_items": [{"price": price_id, "quantity": 1}],
                "mode": "payment",
                "success_url": self.success_url + "?session_id={CHECKOUT_SESSION_ID}",
                "cancel_url": self.cancel_url,
            }
            if customer_id:
                params["customer"] = customer_id
            if metadata:
                params["metadata"] = metadata
            
            session = stripe.checkout.Session.create(**params)
            return {
                "success": True,
                "session_id": session.id,
                "url": session.url,
                "amount_total": session.amount_total
            }
        except stripe.error.StripeError as e:
            return {"success": False, "error": str(e)}
    
    def create_subscription(self, customer_id: str, price_id: str, trial_days: int = 0) -> dict:
        """
        创建订阅（周期性扣款）
        
        Args:
            customer_id: 客户 ID
            price_id: 价格 ID
            trial_days: 试用天数
        
        Returns:
            订阅信息
        """
        try:
            subscription = stripe.Subscription.create(
                customer=customer_id,
                items=[{"price": price_id}],
                trial_period_days=trial_days if trial_days > 0 else None,
                payment_behavior="default_incomplete",
                payment_settings={"save_default_payment_method": "on_subscription"},
                expand=["latest_invoice.payment_intent"]
            )
            return {
                "success": True,
                "subscription_id": subscription.id,
                "status": subscription.status,
                "current_period_start": subscription.current_period_start,
                "current_period_end": subscription.current_period_end,
                "client_secret": subscription.latest_invoice.payment_intent.client_secret if subscription.latest_invoice else None
            }
        except stripe.error.StripeError as e:
            return {"success": False, "error": str(e)}
    
    def create_customer(self, email: str, name: str = None, metadata: dict = None) -> dict:
        """
        创建客户
        
        Args:
            email: 邮箱
            name: 姓名
            metadata: 元数据
        
        Returns:
            客户信息
        """
        try:
            params = {"email": email}
            if name:
                params["name"] = name
            if metadata:
                params["metadata"] = metadata
            
            customer = stripe.Customer.create(**params)
            return {
                "success": True,
                "customer_id": customer.id,
                "email": customer.email
            }
        except stripe.error.StripeError as e:
            return {"success": False, "error": str(e)}
    
    def refund_payment(self, payment_intent_id: str, amount: int = None, reason: str = None) -> dict:
        """
        退款
        
        Args:
            payment_intent_id: 支付意图 ID
            amount: 退款金额（可选，默认全额）
            reason: 退款原因
        
        Returns:
            退款信息
        """
        try:
            params = {"payment_intent": payment_intent_id}
            if amount:
                params["amount"] = amount
            if reason:
                params["reason"] = reason
            
            refund = stripe.Refund.create(**params)
            return {
                "success": True,
                "refund_id": refund.id,
                "status": refund.status,
                "amount": refund.amount
            }
        except stripe.error.StripeError as e:
            return {"success": False, "error": str(e)}
    
    def handle_webhook(self, payload: str, sig_header: str) -> dict:
        """
        处理 Webhook 事件
        
        Args:
            payload: Webhook 负载
            sig_header: 签名头
        
        Returns:
            事件信息
        """
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, self.webhook_secret
            )
            
            # 处理不同类型的事件
            event_type = event["type"]
            event_data = event["data"]["object"]
            
            if event_type == "payment_intent.succeeded":
                # 支付成功
                return {"event": "payment_success", "data": event_data}
            elif event_type == "payment_intent.payment_failed":
                # 支付失败
                return {"event": "payment_failed", "data": event_data}
            elif event_type == "customer.subscription.created":
                # 订阅创建
                return {"event": "subscription_created", "data": event_data}
            elif event_type == "customer.subscription.updated":
                # 订阅更新
                return {"event": "subscription_updated", "data": event_data}
            elif event_type == "customer.subscription.deleted":
                # 订阅取消
                return {"event": "subscription_deleted", "data": event_data}
            else:
                return {"event": "unhandled", "type": event_type}
                
        except stripe.error.SignatureVerificationError:
            return {"success": False, "error": "Invalid signature"}
        except Exception as e:
            return {"success": False, "error": str(e)}


# 使用示例
if __name__ == "__main__":
    client = StripeClient()
    
    # 1. 创建客户
    customer_result = client.create_customer(
        email="customer@example.com",
        name="Test Customer",
        metadata={"user_id": "12345"}
    )
    print(f"创建客户：{customer_result}")
    
    # 2. 创建支付意图
    if customer_result["success"]:
        payment_result = client.create_payment_intent(
            amount=9900,  # 99 元
            currency="cny",
            customer_id=customer_result["customer_id"]
        )
        print(f"创建支付：{payment_result}")
    
    # 3. 创建订阅
    # subscription_result = client.create_subscription(
    #     customer_id=customer_result["customer_id"],
    #     price_id="price_xxx",
    #     trial_days=7
    # )
    # print(f"创建订阅：{subscription_result}")

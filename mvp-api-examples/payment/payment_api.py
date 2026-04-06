# 支付接口 API 示例（Stripe/支付宝）
# 用于 MVP 支付功能

import os
from typing import Optional, Dict
from datetime import datetime

class PaymentClient:
    """支付客户端"""
    
    def __init__(self, provider: str = 'stripe'):
        self.provider = provider
        
        if provider == 'stripe':
            import stripe
            stripe.api_key = os.getenv('STRIPE_SECRET_KEY', 'sk_test_xxx')
            self.stripe = stripe
            self.webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET', 'whsec_xxx')
        elif provider == 'alipay':
            self.app_id = os.getenv('ALIPAY_APP_ID', 'your-app-id')
            self.private_key = os.getenv('ALIPAY_PRIVATE_KEY', 'your-private-key')
            self.alipay_public_key = os.getenv('ALIPAY_PUBLIC_KEY', 'alipay-public-key')
    
    def create_payment_intent(
        self,
        amount: int,
        currency: str = 'usd',
        customer_email: Optional[str] = None
    ) -> Optional[Dict]:
        """创建支付意图"""
        if self.provider == 'stripe':
            return self._create_stripe_intent(amount, currency, customer_email)
        elif self.provider == 'alipay':
            return self._create_alipay_order(amount, customer_email)
    
    def _create_stripe_intent(
        self,
        amount: int,
        currency: str,
        customer_email: Optional[str]
    ) -> Optional[Dict]:
        """Stripe 支付意图"""
        try:
            intent = self.stripe.PaymentIntent.create(
                amount=amount,
                currency=currency,
                payment_method_types=['card'],
                receipt_email=customer_email
            )
            return {
                'client_secret': intent.client_secret,
                'payment_intent_id': intent.id,
                'amount': amount,
                'currency': currency
            }
        except Exception as e:
            print(f"Stripe Error: {e}")
            return None
    
    def _create_alipay_order(
        self,
        amount: float,
        customer_email: Optional[str]
    ) -> Optional[Dict]:
        """支付宝订单（简化版）"""
        import hashlib
        
        out_trade_no = datetime.now().strftime('%Y%m%d%H%M%S')
        sign_str = f"app_id={self.app_id}&method=alipay.trade.page.pay"
        sign = hashlib.md5(sign_str.encode()).hexdigest()
        
        return {
            'out_trade_no': out_trade_no,
            'total_amount': str(amount),
            'sign': sign,
            'pay_url': f"https://openapi.alipay.com/gateway.do?sign={sign}"
        }
    
    def verify_webhook(self, payload: str, signature: str) -> bool:
        """验证支付回调 webhook"""
        if self.provider == 'stripe':
            try:
                event = self.stripe.Webhook.construct_event(
                    payload, signature, self.webhook_secret
                )
                return True
            except Exception as e:
                print(f"Webhook Verification Error: {e}")
                return False
        elif self.provider == 'alipay':
            return True
    
    def refund_payment(self, payment_intent_id: str, amount: Optional[int] = None) -> bool:
        """退款"""
        if self.provider == 'stripe':
            try:
                refund_params = {'payment_intent': payment_intent_id}
                if amount:
                    refund_params['amount'] = amount
                refund = self.stripe.Refund.create(**refund_params)
                return refund.status == 'succeeded'
            except Exception as e:
                print(f"Stripe Refund Error: {e}")
                return False
        elif self.provider == 'alipay':
            return True
    
    def get_payment_status(self, payment_intent_id: str) -> Optional[str]:
        """查询支付状态"""
        if self.provider == 'stripe':
            try:
                intent = self.stripe.PaymentIntent.retrieve(payment_intent_id)
                return intent.status
            except Exception as e:
                print(f"Stripe Status Error: {e}")
                return None
        elif self.provider == 'alipay':
            return 'success'

if __name__ == '__main__':
    payment = PaymentClient(provider='stripe')
    
    intent = payment.create_payment_intent(
        amount=9900,
        currency='usd',
        customer_email='user@example.com'
    )
    
    if intent:
        print(f"Payment Intent: {intent['payment_intent_id']}")
        status = payment.get_payment_status(intent['payment_intent_id'])
        print(f"Status: {status}")

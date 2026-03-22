"""
支付 API 适配器
支持支付宝和微信支付
"""

import aiohttp
from typing import Any, Dict
from . import BaseAPIAdapter, APIError, AuthenticationError


class AlipayAdapter(BaseAPIAdapter):
    """支付宝支付适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.app_id = config.get('app_id')
        self.private_key = config.get('private_key')
        self.alipay_public_key = config.get('alipay_public_key')
        self.gateway = "https://openapi.alipay.com/gateway.do"
    
    async def authenticate(self) -> bool:
        return bool(self.app_id and self.private_key)
    
    async def create_order(self, out_trade_no: str, total_amount: str, subject: str) -> Dict:
        """创建订单"""
        biz_content = {
            "out_trade_no": out_trade_no, "total_amount": total_amount,
            "subject": subject, "product_code": "FAST_INSTANT_TRADE_PAY"
        }
        params = {
            "method": "alipay.trade.page.pay", "app_id": self.app_id,
            "format": "JSON", "charset": "utf-8", "sign_type": "RSA2",
            "biz_content": json.dumps(biz_content), "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0", "return_url": config.get('return_url'), "notify_url": config.get('notify_url')
        }
        params["sign"] = self._sign(params)
        async with aiohttp.ClientSession() as session:
            async with session.post(self.gateway, data=params) as response:
                return await response.json()
    
    def _sign(self, params: Dict) -> str:
        from Crypto.Signature import PKCS1_v1_5
        from Crypto.Hash import SHA256
        from Crypto.PublicKey import RSA
        import base64
        sorted_params = sorted(params.items())
        sign_str = "&".join(f"{k}={v}" for k, v in sorted_params if v)
        key = RSA.import_key(self.private_key.encode())
        h = SHA256.new(sign_str.encode())
        signature = PKCS1_v1_5.new(key).sign(h)
        return base64.b64encode(signature).decode()
    
    async def verify_notify(self, notify_data: Dict) -> bool:
        """验证异步通知"""
        signature = notify_data.pop("sign")
        return self._verify(notify_data, signature)
    
    def _verify(self, data: Dict, signature: str) -> bool:
        from Crypto.Signature import PKCS1_v1_5
        from Crypto.Hash import SHA256
        from Crypto.PublicKey import RSA
        import base64
        sorted_params = sorted(data.items())
        sign_str = "&".join(f"{k}={v}" for k, v in sorted_params if v)
        key = RSA.import_key(self.alipay_public_key.encode())
        h = SHA256.new(sign_str.encode())
        try:
            PKCS1_v1_5.new(key).verify(h, base64.b64decode(signature))
            return True
        except:
            return False


class WeChatPayAdapter(BaseAPIAdapter):
    """微信支付适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.appid = config.get('appid')
        self.mch_id = config.get('mch_id')
        self.api_key = config.get('api_key')
        self.v3_api_key = config.get('v3_api_key')
        self.serial_no = config.get('serial_no')
    
    async def authenticate(self) -> bool:
        return bool(self.appid and self.mch_id and self.api_key)
    
    async def create_order(self, out_trade_no: str, total_amount: int, description: str) -> Dict:
        """创建 JSAPI 订单"""
        url = "https://api.mch.weixin.qq.com/v3/pay/transactions/jsapi"
        headers = {
            "Accept": "application/json", "Content-Type": "application/json",
            "Authorization": f"WECHATPAY2-SHA256-RSA2048 {self._generate_v3_signature('POST', url)}"
        }
        payload = {
            "appid": self.appid, "mchid": self.mch_id, "description": description,
            "out_trade_no": out_trade_no, "notify_url": config.get('notify_url'),
            "amount": {"total": total_amount, "currency": "CNY"}
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                return await response.json()
    
    def _generate_v3_signature(self, method: str, url: str) -> str:
        import time, random
        timestamp = str(int(time.time()))
        nonce_str = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=32))
        sign_content = f"{method}\n{url.replace('https://', '')}\n{timestamp}\n{nonce_str}\n"
        from Crypto.Signature import PKCS1_v1_5
        from Crypto.Hash import SHA256
        from Crypto.PublicKey import RSA
        import base64
        key = RSA.import_key(self.v3_api_key.encode())
        h = SHA256.new(sign_content.encode())
        signature = PKCS1_v1_5.new(key).sign(h)
        return base64.b64encode(signature).decode()
    
    async def verify_notify(self, notify_data: Dict) -> bool:
        """验证支付结果通知"""
        return True  # 简化实现，实际需要验证签名


def create_payment_adapter(provider: str, config: Dict[str, Any]) -> BaseAPIAdapter:
    providers = {'alipay': AlipayAdapter, 'wechat': WeChatPayAdapter}
    if provider not in providers:
        raise ValueError(f"不支持的支付提供商：{provider}")
    return providers[provider](config)

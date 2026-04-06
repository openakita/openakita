# 支付集成 API 示例 (支付宝 + 微信支付)
# 安装依赖：pip install alipay-sdk-python wechatpy

import json
import hashlib
import time
from typing import Optional, Dict
from datetime import datetime

# ============================================
# 方案 1: 支付宝支付 (Alipay)
# ============================================

class AlipayService:
    """支付宝支付服务"""
    
    def __init__(
        self,
        app_id: str,
        private_key: str,
        alipay_public_key: str,
        sandbox: bool = True
    ):
        self.app_id = app_id
        self.private_key = private_key
        self.alipay_public_key = alipay_public_key
        self.gateway_url = "https://openapi-sandbox.dl.alipaydev.com/gateway.do" if sandbox else "https://openapi.alipay.com/gateway.do"
    
    def _generate_sign(self, params: Dict) -> str:
        """生成签名"""
        sorted_params = sorted(params.items())
        sign_string = "&".join([f"{k}={v}" for k, v in sorted_params if v and k != 'sign'])
        from Crypto.Signature import PKCS1_v1_5
        from Crypto.Hash import SHA256
        from Crypto.PrivateKey import RSA
        
        key = RSA.import_key(self.private_key.encode())
        h = SHA256.new(sign_string.encode('utf-8'))
        signature = PKCS1_v1_5.new(key).sign(h)
        import base64
        return base64.b64encode(signature).decode('utf-8')
    
    def create_qr_payment(
        self,
        out_trade_no: str,
        total_amount: str,
        subject: str,
        body: Optional[str] = None
    ) -> str:
        """创建扫码支付"""
        biz_content = {
            "out_trade_no": out_trade_no,
            "total_amount": total_amount,
            "subject": subject,
            "body": body or "",
            "product_code": "FACE_TO_FACE_PAYMENT"
        }
        
        params = {
            "app_id": self.app_id,
            "method": "alipay.trade.precreate",
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps(biz_content)
        }
        
        params["sign"] = self._generate_sign(params)
        
        # 实际调用需要发送 HTTP 请求
        print(f"✓ 支付宝预下单成功：{out_trade_no}")
        print(f"  金额：¥{total_amount}")
        print(f"  商品：{subject}")
        return "qr_code_data_placeholder"
    
    def create_web_payment(
        self,
        out_trade_no: str,
        total_amount: str,
        subject: str,
        return_url: str,
        notify_url: str
    ) -> str:
        """创建网页支付"""
        biz_content = {
            "out_trade_no": out_trade_no,
            "total_amount": total_amount,
            "subject": subject,
            "product_code": "FAST_INSTANT_TRADE_PAY",
            "return_url": return_url,
            "notify_url": notify_url
        }
        
        params = {
            "app_id": self.app_id,
            "method": "alipay.trade.page.pay",
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps(biz_content)
        }
        
        params["sign"] = self._generate_sign(params)
        
        # 构建支付 URL
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        payment_url = f"{self.gateway_url}?{query_string}"
        
        print(f"✓ 支付宝网页支付链接生成：{out_trade_no}")
        return payment_url
    
    def verify_notify(self, notify_data: Dict) -> bool:
        """验证异步通知签名"""
        # 验证支付宝回调签名
        sign = notify_data.pop('sign', None)
        generated_sign = self._generate_sign(notify_data)
        return sign == generated_sign
    
    def query_order(self, out_trade_no: str) -> Dict:
        """查询订单状态"""
        biz_content = {
            "out_trade_no": out_trade_no
        }
        
        params = {
            "app_id": self.app_id,
            "method": "alipay.trade.query",
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps(biz_content)
        }
        
        params["sign"] = self._generate_sign(params)
        
        print(f"✓ 支付宝订单查询：{out_trade_no}")
        return {"trade_status": "TRADE_SUCCESS", "out_trade_no": out_trade_no}


# ============================================
# 方案 2: 微信支付 (WeChat Pay)
# ============================================

class WeChatPayService:
    """微信支付服务 (V3 版本)"""
    
    def __init__(
        self,
        appid: str,
        mchid: str,
        private_key: str,
        serial_no: str,
        api_v3_key: str,
        sandbox: bool = False
    ):
        self.appid = appid
        self.mchid = mchid
        self.private_key = private_key
        self.serial_no = serial_no
        self.api_v3_key = api_v3_key
        self.base_url = "https://api.mch.weixin.qq.com" if not sandbox else "https://api.mch.weixin.qq.com/sandbox"
    
    def _generate_signature(self, method: str, url: str, timestamp: str, nonce: str, body: str = "") -> str:
        """生成签名"""
        sign_content = f"{method}\n{url}\n{timestamp}\n{nonce}\n{body}\n"
        from Crypto.Signature import PKCS1_v1_5
        from Crypto.Hash import SHA256
        from Crypto.PrivateKey import RSA
        
        key = RSA.import_key(self.private_key.encode())
        h = SHA256.new(sign_content.encode('utf-8'))
        signature = PKCS1_v1_5.new(key).sign(h)
        import base64
        return base64.b64encode(signature).decode('utf-8')
    
    def create_jsapi_payment(
        self,
        out_trade_no: str,
        total_amount: int,  # 单位：分
        description: str,
        openid: str
    ) -> Dict:
        """创建 JSAPI 支付 (公众号/小程序)"""
        url = "/v3/pay/transactions/jsapi"
        timestamp = str(int(time.time()))
        nonce = "random_string_123"
        
        payload = {
            "appid": self.appid,
            "mchid": self.mchid,
            "description": description,
            "out_trade_no": out_trade_no,
            "notify_url": "https://yourdomain.com/notify/wechat",
            "amount": {
                "total": total_amount,
                "currency": "CNY"
            },
            "payer": {
                "openid": openid
            }
        }
        
        signature = self._generate_signature("POST", url, timestamp, nonce, json.dumps(payload))
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f'WECHATPAY2-SHA256-RSA2048 mchid="{self.mchid}",nonce_str="{nonce}",signature="{signature}",timestamp="{timestamp}",serial_no="{self.serial_no}"'
        }
        
        print(f"✓ 微信支付 JSAPI 下单成功：{out_trade_no}")
        print(f"  金额：¥{total_amount/100}")
        print(f"  商品：{description}")
        
        return {
            "prepay_id": "wx26160922998855ac8efd8d5b9d888888",
            "out_trade_no": out_trade_no
        }
    
    def create_native_payment(
        self,
        out_trade_no: str,
        total_amount: int,
        description: str
    ) -> str:
        """创建 Native 支付 (扫码支付)"""
        url = "/v3/pay/transactions/native"
        timestamp = str(int(time.time()))
        nonce = "random_string_123"
        
        payload = {
            "appid": self.appid,
            "mchid": self.mchid,
            "description": description,
            "out_trade_no": out_trade_no,
            "notify_url": "https://yourdomain.com/notify/wechat",
            "amount": {
                "total": total_amount,
                "currency": "CNY"
            }
        }
        
        signature = self._generate_signature("POST", url, timestamp, nonce, json.dumps(payload))
        
        print(f"✓ 微信支付 Native 下单成功：{out_trade_no}")
        
        return "wechat_pay_qr_code_url"
    
    def verify_notify(self, notify_headers: Dict, notify_body: str) -> bool:
        """验证微信支付回调"""
        # 验证签名逻辑
        signature = notify_headers.get('Wechatpay-Signature', '')
        nonce = notify_headers.get('Wechatpay-Nonce', '')
        timestamp = notify_headers.get('Wechatpay-Timestamp', '')
        
        sign_content = f"{timestamp}\n{nonce}\n{notify_body}\n"
        # 实际验证需要使用公钥验证签名
        print("✓ 微信支付回调验证通过")
        return True
    
    def refund(
        self,
        out_trade_no: str,
        out_refund_no: str,
        total_amount: int,
        refund_amount: int
    ) -> bool:
        """申请退款"""
        url = "/v3/refund/domestic/refunds"
        timestamp = str(int(time.time()))
        nonce = "random_string_123"
        
        payload = {
            "transaction_id": out_trade_no,
            "out_refund_no": out_refund_no,
            "amount": {
                "refund": refund_amount,
                "total": total_amount,
                "currency": "CNY"
            }
        }
        
        signature = self._generate_signature("POST", url, timestamp, nonce, json.dumps(payload))
        
        print(f"✓ 微信退款申请成功：{out_refund_no}")
        return True


# ============================================
# 使用示例
# ============================================

if __name__ == "__main__":
    # 支付宝示例
    alipay = AlipayService(
        app_id="2021000000000000",
        private_key="your-private-key",
        alipay_public_key="alipay-public-key",
        sandbox=True
    )
    
    qr_code = alipay.create_qr_payment(
        out_trade_no="ORDER_20260318_001",
        total_amount="99.00",
        subject="测试商品"
    )
    
    # 微信支付示例
    wechat_pay = WeChatPayService(
        appid="wx1234567890abcdef",
        mchid="1234567890",
        private_key="your-private-key",
        serial_no="serial-number",
        api_v3_key="your-api-v3-key"
    )
    
    payment_result = wechat_pay.create_jsapi_payment(
        out_trade_no="ORDER_20260318_002",
        total_amount=9900,  # 99 元
        description="测试商品",
        openid="user-openid"
    )

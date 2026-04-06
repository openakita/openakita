"""
API 集成示例 02: 支付接口 (支付宝/微信支付)
==========================================
功能：实现支付宝和微信支付的集成示例
依赖：pip install alipay-sdk-python wechatpy
"""

from datetime import datetime
from typing import Optional, Dict
from pydantic import BaseModel
import hashlib
import json

# ==================== 支付宝集成 ====================

class AlipayConfig:
    """支付宝配置"""
    APP_ID = "your_app_id"
    PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
your_private_key_here
-----END RSA PRIVATE KEY-----"""
    ALIPAY_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
alipay_public_key_here
-----END PUBLIC KEY-----"""
    SIGN_TYPE = "RSA2"
    CHARSET = "utf-8"
    GATEWAY_URL = "https://openapi.alipay.com/gateway.do"
    NOTIFY_URL = "https://yourdomain.com/api/payment/alipay/notify"
    RETURN_URL = "https://yourdomain.com/api/payment/alipay/return"

class AlipayPayment:
    """支付宝支付服务"""
    
    def __init__(self):
        self.config = AlipayConfig()
    
    def create_order(self, out_trade_no: str, total_amount: float, subject: str) -> Dict:
        """
        创建支付宝订单（手机网站支付）
        
        Args:
            out_trade_no: 商户订单号
            total_amount: 订单金额
            subject: 订单标题
            
        Returns:
            支付表单 HTML 或支付链接
        """
        # 构建请求参数
        biz_content = {
            "out_trade_no": out_trade_no,
            "total_amount": str(total_amount),
            "subject": subject,
            "product_code": "QUICK_WAP_WAY",
            "timeout_express": "30m"
        }
        
        # 构建公共参数
        public_params = {
            "app_id": self.config.APP_ID,
            "method": "alipay.trade.wap.pay",
            "charset": self.config.CHARSET,
            "sign_type": self.config.SIGN_TYPE,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps(biz_content, ensure_ascii=False),
            "notify_url": self.config.NOTIFY_URL,
            "return_url": self.config.RETURN_URL,
        }
        
        # 生成签名
        sign = self._generate_signature(public_params)
        public_params["sign"] = sign
        
        # 构建支付 URL
        query_string = "&".join([f"{k}={v}" for k, v in public_params.items()])
        pay_url = f"{self.config.GATEWAY_URL}?{query_string}"
        
        return {
            "pay_url": pay_url,
            "out_trade_no": out_trade_no,
            "total_amount": total_amount
        }
    
    def verify_notify(self, notify_data: Dict) -> bool:
        """
        验证异步通知签名
        
        Args:
            notify_data: 支付宝异步通知数据
            
        Returns:
            验证是否通过
        """
        sign = notify_data.pop("sign", None)
        if not sign:
            return False
        
        # 验证签名（实际实现需要使用 RSA2 验证）
        # 这里简化处理
        return True
    
    def _generate_signature(self, params: Dict) -> str:
        """生成 RSA2 签名"""
        # 实际实现需要使用 RSA 私钥签名
        # 这里简化处理
        sign_str = "&".join([f"{k}={params[k]}" for k in sorted(params.keys())])
        signature = hashlib.sha256((sign_str + self.config.PRIVATE_KEY).encode()).hexdigest()
        return signature[:64]

# ==================== 微信支付集成 ====================

class WechatPayConfig:
    """微信支付配置"""
    APPID = "your_appid"
    MCHID = "your_mchid"
    API_KEY = "your_api_key"
    NOTIFY_URL = "https://yourdomain.com/api/payment/wechat/notify"
    API_V3_KEY = "your_api_v3_key"
    MCH_CERT_SERIAL_NO = "your_cert_serial_no"
    PRIVATE_KEY_PATH = "apiclient_key.pem"
    CERTIFICATE_PATH = "apiclient_cert.pem"

class WechatPayment:
    """微信支付服务"""
    
    def __init__(self):
        self.config = WechatPayConfig()
    
    def create_jsapi_order(self, out_trade_no: str, total_amount: int, description: str, openid: str) -> Dict:
        """
        创建 JSAPI 支付订单（公众号支付）
        
        Args:
            out_trade_no: 商户订单号
            total_amount: 订单金额（单位：分）
            description: 商品描述
            openid: 用户 openid
            
        Returns:
            预支付交易单信息
        """
        # 构建请求参数（微信支付 V3 接口）
        request_body = {
            "appid": self.config.APPID,
            "mchid": self.config.MCHID,
            "description": description,
            "out_trade_no": out_trade_no,
            "notify_url": self.config.NOTIFY_URL,
            "amount": {
                "total": total_amount,
                "currency": "CNY"
            },
            "payer": {
                "openid": openid
            }
        }
        
        # 实际实现需要调用微信支付 V3 API
        # POST https://api.mch.weixin.qq.com/v3/pay/transactions/jsapi
        
        return {
            "prepay_id": "wx26160921123456789abcdef0123456789",
            "package": "prepay_id=wx26160921123456789abcdef0123456789",
            "timeStamp": str(int(datetime.now().timestamp())),
            "nonceStr": "5K8264ILTKCH16CQ2502SI8ZNMTM67VS",
            "signType": "RSA",
            "paySign": "oR9d8PuhnIc+YZ8cBHFCwfgpaK9gd7vaRvkYD7rthRAZ1xNnN..."
        }
    
    def create_native_order(self, out_trade_no: str, total_amount: int, description: str) -> Dict:
        """
        创建 Native 支付订单（扫码支付）
        
        Args:
            out_trade_no: 商户订单号
            total_amount: 订单金额（单位：分）
            description: 商品描述
            
        Returns:
            二维码链接
        """
        request_body = {
            "appid": self.config.APPID,
            "mchid": self.config.MCHID,
            "description": description,
            "out_trade_no": out_trade_no,
            "notify_url": self.config.NOTIFY_URL,
            "amount": {
                "total": total_amount,
                "currency": "CNY"
            }
        }
        
        # 实际实现需要调用微信支付 V3 API
        # POST https://api.mch.weixin.qq.com/v3/pay/transactions/native
        
        return {
            "code_url": "weixin://wxpay/bizpayurl?pr=xxxxx",
            "out_trade_no": out_trade_no
        }
    
    def verify_notify(self, notify_data: Dict) -> bool:
        """验证微信支付异步通知"""
        # 实际实现需要验证签名和解密数据
        return True
    
    def refund(self, out_trade_no: str, out_refund_no: str, total_amount: int, refund_amount: int) -> Dict:
        """
        申请退款
        
        Args:
            out_trade_no: 原订单号
            out_refund_no: 退款单号
            total_amount: 原订单金额（分）
            refund_amount: 退款金额（分）
            
        Returns:
            退款结果
        """
        request_body = {
            "out_trade_no": out_trade_no,
            "out_refund_no": out_refund_no,
            "amount": {
                "refund": refund_amount,
                "total": total_amount,
                "currency": "CNY"
            }
        }
        
        # POST https://api.mch.weixin.qq.com/v3/refund/domestic/refunds
        
        return {
            "refund_id": "50000000012021080112345678901",
            "status": "SUCCESS"
        }

# ==================== 使用示例 ====================

if __name__ == "__main__":
    # 支付宝示例
    alipay = AlipayPayment()
    order = alipay.create_order(
        out_trade_no="ORDER_20260318_001",
        total_amount=99.00,
        subject="测试商品"
    )
    print(f"支付宝支付链接：{order['pay_url']}")
    
    # 微信支付示例
    wechat = WechatPayment()
    wx_order = wechat.create_jsapi_order(
        out_trade_no="ORDER_20260318_002",
        total_amount=9900,  # 99 元 = 9900 分
        description="测试商品",
        openid="oUpF8uMuAJO_M2pxb1Q9zNjWeS6o"
    )
    print(f"微信支付预支付 ID: {wx_order['prepay_id']}")

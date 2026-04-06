"""
微信支付 V3 集成示例代码
功能：JSAPI 支付、Native 支付、支付回调、退款
"""

from typing import Optional
from pydantic import BaseModel
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
import hashlib
import hmac
import base64
import json
from Crypto.PublicKey import RSA
from Crypto.Signature import PKCS1_v1_5
from Crypto.Hash import SHA256
import time

load_dotenv()

# 微信支付 V3 配置
WECHAT_APPID = os.getenv("WECHAT_APPID", "your-appid")
WECHAT_MCHID = os.getenv("WECHAT_MCHID", "your-mchid")
WECHAT_API_KEY = os.getenv("WECHAT_API_KEY", "your-api-key")
WECHAT_API_SECRET = os.getenv("WECHAT_API_SECRET", "your-api-secret")
WECHAT_CERT_PATH = os.getenv("WECHAT_CERT_PATH", "./certs/apiclient_cert.pem")
WECHAT_KEY_PATH = os.getenv("WECHAT_KEY_PATH", "./certs/apiclient_key.pem")
WECHAT_SERIAL_NO = os.getenv("WECHAT_SERIAL_NO", "your-serial-no")

# 网关地址
WECHAT_BASE_URL = "https://api.mch.weixin.qq.com"
NOTIFY_URL = "http://yourdomain.com/payment/wechat/notify"


class WechatPaymentOrder(BaseModel):
    """微信支付订单"""
    out_trade_no: str  # 商户订单号
    amount: float  # 订单金额（元）
    description: str  # 商品描述
    attach: Optional[str] = None  # 附加数据


class WechatPaymentResponse(BaseModel):
    """微信支付响应"""
    success: bool
    out_trade_no: str
    prepay_id: Optional[str] = None  # 预支付交易会话标识
    code_url: Optional[str] = None  # Native 支付二维码链接
    pay_params: Optional[dict] = None  # 前端支付参数
    message: str


def generate_nonce_str() -> str:
    """生成随机字符串"""
    import random
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(random.choice(chars) for _ in range(32))


def load_mch_private_key(key_path: str) -> RSA.RsaKey:
    """加载商户私钥"""
    with open(key_path, "r") as f:
        key_str = f.read()
    return RSA.import_key(key_str)


def load_platform_cert(cert_path: str) -> str:
    """加载平台证书"""
    with open(cert_path, "r") as f:
        return f.read()


def create_sign(message: str, private_key: RSA.RsaKey) -> str:
    """
    创建签名
    
    Args:
        message: 待签名消息
        private_key: 商户私钥
    
    Returns:
        Base64 编码的签名
    """
    h = SHA256.new(message.encode("utf-8"))
    signer = PKCS1_v1_5.new(private_key)
    signature = signer.sign(h)
    return base64.b64encode(signature).decode("utf-8")


def create_authorization(
    method: str,
    url: str,
    timestamp: int,
    nonce_str: str,
    body: str,
    private_key: RSA.RsaKey,
    serial_no: str
) -> str:
    """
    创建 Authorization 头
    
    Args:
        method: HTTP 方法
        url: 请求 URL（不含域名）
        timestamp: 时间戳
        nonce_str: 随机字符串
        body: 请求体
        private_key: 商户私钥
        serial_no: 商户证书序列号
    
    Returns:
        Authorization 头值
    """
    # 构建签名消息
    sign_message = f"{method}\n{url}\n{timestamp}\n{nonce_str}\n{body}\n"
    
    # 创建签名
    signature = create_sign(sign_message, private_key)
    
    # 构建 Authorization
    auth = f'WECHATPAY2-SHA256-RSA2048 mchid="{WECHAT_MCHID}",nonce_str="{nonce_str}",signature="{signature}",timestamp="{timestamp}",serial_no="{serial_no}"'
    
    return auth


def create_jsapi_order(order: WechatPaymentOrder, openid: str) -> WechatPaymentResponse:
    """
    创建 JSAPI 支付订单（公众号/小程序）
    
    Args:
        order: 支付订单
        openid: 用户 openid
    
    Returns:
        支付响应
    """
    private_key = load_mch_private_key(WECHAT_KEY_PATH)
    
    # 构建请求体
    body = {
        "appid": WECHAT_APPID,
        "mchid": WECHAT_MCHID,
        "description": order.description,
        "out_trade_no": order.out_trade_no,
        "notify_url": NOTIFY_URL,
        "amount": {
            "total": int(order.amount * 100),  # 转换为分
            "currency": "CNY"
        },
        "payer": {
            "openid": openid
        }
    }
    
    if order.attach:
        body["attach"] = order.attach
    
    # 生成请求参数
    timestamp = int(time.time())
    nonce_str = generate_nonce_str()
    url = "/v3/pay/transactions/jsapi"
    
    # 创建 Authorization
    authorization = create_authorization(
        method="POST",
        url=url,
        timestamp=timestamp,
        nonce_str=nonce_str,
        body=json.dumps(body),
        private_key=private_key,
        serial_no=WECHAT_SERIAL_NO
    )
    
    # 打印请求信息（实际应发送 HTTP 请求）
    print(f"JSAPI 支付请求:")
    print(f"  URL: {WECHAT_BASE_URL}{url}")
    print(f"  Authorization: {authorization[:100]}...")
    print(f"  Body: {json.dumps(body)}\n")
    
    # 模拟响应
    prepay_id = "wx2012345678901234567890abcdef"
    
    # 生成前端支付参数
    pay_params = generate_jsapi_pay_params(prepay_id)
    
    return WechatPaymentResponse(
        success=True,
        out_trade_no=order.out_trade_no,
        prepay_id=prepay_id,
        pay_params=pay_params,
        message="JSAPI 支付订单创建成功"
    )


def create_native_order(order: WechatPaymentOrder) -> WechatPaymentResponse:
    """
    创建 Native 支付订单（扫码支付）
    
    Args:
        order: 支付订单
    
    Returns:
        支付响应
    """
    private_key = load_mch_private_key(WECHAT_KEY_PATH)
    
    # 构建请求体
    body = {
        "appid": WECHAT_APPID,
        "mchid": WECHAT_MCHID,
        "description": order.description,
        "out_trade_no": order.out_trade_no,
        "notify_url": NOTIFY_URL,
        "amount": {
            "total": int(order.amount * 100),
            "currency": "CNY"
        }
    }
    
    if order.attach:
        body["attach"] = order.attach
    
    # 生成请求参数
    timestamp = int(time.time())
    nonce_str = generate_nonce_str()
    url = "/v3/pay/transactions/native"
    
    # 创建 Authorization
    authorization = create_authorization(
        method="POST",
        url=url,
        timestamp=timestamp,
        nonce_str=nonce_str,
        body=json.dumps(body),
        private_key=private_key,
        serial_no=WECHAT_SERIAL_NO
    )
    
    # 打印请求信息
    print(f"Native 支付请求:")
    print(f"  URL: {WECHAT_BASE_URL}{url}")
    print(f"  Body: {json.dumps(body)}\n")
    
    # 模拟响应
    code_url = "weixin://wxpay/bizpayurl?pr=1234567890"
    
    return WechatPaymentResponse(
        success=True,
        out_trade_no=order.out_trade_no,
        code_url=code_url,
        message="Native 支付订单创建成功"
    )


def generate_jsapi_pay_params(prepay_id: str) -> dict:
    """
    生成 JSAPI 支付前端参数
    
    Args:
        prepay_id: 预支付交易会话标识
    
    Returns:
        前端支付参数
    """
    private_key = load_mch_private_key(WECHAT_KEY_PATH)
    
    timestamp = str(int(time.time()))
    nonce_str = generate_nonce_str()
    package = f"prepay_id={prepay_id}"
    
    # 构建签名消息
    sign_message = f"{WECHAT_APPID}\n{timestamp}\n{nonce_str}\n{package}\n"
    
    # 创建签名
    pay_sign = create_sign(sign_message, private_key)
    
    return {
        "appId": WECHAT_APPID,
        "timeStamp": timestamp,
        "nonceStr": nonce_str,
        "package": package,
        "signType": "RSA",
        "paySign": pay_sign
    }


def verify_notify(notify_headers: dict, notify_body: str) -> bool:
    """
    验证支付回调通知
    
    Args:
        notify_headers: 回调请求头
        notify_body: 回调请求体
    
    Returns:
        验证是否成功
    """
    # 获取回调头信息
    timestamp = notify_headers.get("Wechatpay-Timestamp", "")
    nonce = notify_headers.get("Wechatpay-Nonce", "")
    signature = notify_headers.get("Wechatpay-Signature", "")
    serial_no = notify_headers.get("Wechatpay-Serial", "")
    
    # 构建签名消息
    sign_message = f"{timestamp}\n{nonce}\n{notify_body}\n"
    
    # 加载平台证书验证签名
    # 实际应从证书管理获取对应序列号的证书
    print(f"验证回调签名:")
    print(f"  Timestamp: {timestamp}")
    print(f"  Nonce: {nonce}")
    print(f"  Serial: {serial_no}")
    print(f"  Signature: {signature[:50]}...")
    
    # 这里应该使用平台公钥验证签名
    # 示例中仅打印信息
    return True


def refund(out_trade_no: str, amount: float, reason: str = "") -> WechatPaymentResponse:
    """
    退款
    
    Args:
        out_trade_no: 商户订单号
        amount: 退款金额
        reason: 退款原因
    
    Returns:
        退款响应
    """
    private_key = load_mch_private_key(WECHAT_KEY_PATH)
    
    # 构建请求体
    body = {
        "out_trade_no": out_trade_no,
        "out_refund_no": f"REFUND_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "amount": {
            "refund": int(amount * 100),
            "total": int(amount * 100),
            "currency": "CNY"
        }
    }
    
    if reason:
        body["reason"] = reason
    
    # 生成请求参数
    timestamp = int(time.time())
    nonce_str = generate_nonce_str()
    url = "/v3/refund/domestic/refunds"
    
    # 创建 Authorization
    authorization = create_authorization(
        method="POST",
        url=url,
        timestamp=timestamp,
        nonce_str=nonce_str,
        body=json.dumps(body),
        private_key=private_key,
        serial_no=WECHAT_SERIAL_NO
    )
    
    print(f"退款请求:")
    print(f"  URL: {WECHAT_BASE_URL}{url}")
    print(f"  Body: {json.dumps(body)}\n")
    
    return WechatPaymentResponse(
        success=True,
        out_trade_no=out_trade_no,
        message="退款请求已发送"
    )


# ============ 使用示例 ============

def example_wechat_payment():
    """微信支付流程示例"""
    print("=== 微信支付 V3 示例 ===\n")
    
    # 1. 创建订单
    order = WechatPaymentOrder(
        out_trade_no=f"WX_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        amount=99.99,
        description="测试商品",
        attach="附加数据"
    )
    
    print(f"1. 创建订单:")
    print(f"   订单号：{order.out_trade_no}")
    print(f"   金额：¥{order.amount}")
    print(f"   描述：{order.description}\n")
    
    # 2. JSAPI 支付（公众号/小程序）
    print("2. JSAPI 支付（公众号/小程序）:")
    openid = "oUpF8uMuAJO_M2pxb1Q9zNjWeS6o"
    jsapi_response = create_jsapi_order(order, openid)
    if jsapi_response.success:
        print(f"   预支付 ID: {jsapi_response.prepay_id}")
        print(f"   前端支付参数：{jsapi_response.pay_params}\n")
    
    # 3. Native 支付（扫码支付）
    print("3. Native 支付（扫码支付）:")
    native_response = create_native_order(order)
    if native_response.success:
        print(f"   二维码链接：{native_response.code_url}")
        print(f"   （前端可生成二维码展示给用户）\n")
    
    # 4. 退款示例
    print("4. 退款示例:")
    refund_response = refund(order.out_trade_no, 99.99, "用户申请退款")
    print(f"   退款结果：{refund_response.message}\n")


if __name__ == "__main__":
    example_wechat_payment()

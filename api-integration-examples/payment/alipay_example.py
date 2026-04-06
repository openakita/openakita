"""
支付宝支付集成示例代码
功能：手机网站支付、电脑网站支付、支付回调、退款
"""

from typing import Optional
from pydantic import BaseModel
from dotenv import load_dotenv
import os
from datetime import datetime
import hashlib
from Crypto.PublicKey import RSA
from Crypto.Signature import PKCS1_v1_5
from Crypto.Hash import SHA256
import base64
import json

load_dotenv()

# 支付宝配置
ALIPAY_APP_ID = os.getenv("ALIPAY_APP_ID", "your-app-id")
ALIPAY_PRIVATE_KEY = os.getenv("ALIPAY_PRIVATE_KEY", "your-private-key")
ALIPAY_ALIPAY_PUBLIC_KEY = os.getenv("ALIPAY_ALIPAY_PUBLIC_KEY", "alipay-public-key")
ALIPAY_SANDBOX = os.getenv("ALIPAY_SANDBOX", "True").lower() == "true"

# 网关地址
ALIPAY_GATEWAY = "https://openapi-sandbox.dl.alipaydev.com/gateway.do" if ALIPAY_SANDBOX else "https://openapi.alipay.com/gateway.do"
RETURN_URL = "http://yourdomain.com/payment/return"
NOTIFY_URL = "http://yourdomain.com/payment/notify"


class PaymentOrder(BaseModel):
    """支付订单"""
    out_trade_no: str  # 商户订单号
    total_amount: float  # 订单金额
    subject: str  # 订单标题
    body: Optional[str] = None  # 订单描述
    product_code: str = "FAST_INSTANT_TRADE_PAY"  # 销售产品码


class PaymentResponse(BaseModel):
    """支付响应"""
    success: bool
    out_trade_no: str
    trade_no: Optional[str] = None  # 支付宝交易号
    pay_url: Optional[str] = None  # 支付页面 URL
    message: str


class RefundRequest(BaseModel):
    """退款请求"""
    trade_no: Optional[str] = None  # 支付宝交易号
    out_trade_no: Optional[str] = None  # 商户订单号
    refund_amount: float  # 退款金额
    refund_reason: Optional[str] = None  # 退款原因


def load_private_key(key_str: str) -> RSA.RsaKey:
    """加载私钥"""
    return RSA.import_key(key_str)


def load_public_key(key_str: str) -> RSA.RsaKey:
    """加载公钥"""
    return RSA.import_key(key_str)


def sign(data: str, private_key: RSA.RsaKey) -> str:
    """
    使用 RSA 私钥签名
    
    Args:
        data: 待签名字符串
        private_key: RSA 私钥
    
    Returns:
        Base64 编码的签名
    """
    h = SHA256.new(data.encode("utf-8"))
    signer = PKCS1_v1_5.new(private_key)
    signature = signer.sign(h)
    return base64.b64encode(signature).decode("utf-8")


def verify(data: str, signature: str, public_key: RSA.RsaKey) -> bool:
    """
    使用 RSA 公钥验证签名
    
    Args:
        data: 待验证字符串
        signature: Base64 编码的签名
        public_key: RSA 公钥
    
    Returns:
        验证是否成功
    """
    try:
        h = SHA256.new(data.encode("utf-8"))
        verifier = PKCS1_v1_5.new(public_key)
        return verifier.verify(h, base64.b64decode(signature))
    except Exception:
        return False


def build_common_params(method: str) -> dict:
    """
    构建公共请求参数
    
    Args:
        method: API 方法名
    
    Returns:
        公共参数字典
    """
    params = {
        "app_id": ALIPAY_APP_ID,
        "method": method,
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
    }
    return params


def generate_sign(params: dict, private_key: RSA.RsaKey) -> str:
    """
    生成签名
    
    Args:
        params: 请求参数（不含 sign）
        private_key: RSA 私钥
    
    Returns:
        签名
    """
    # 排序参数
    sorted_params = sorted(params.items())
    # 构建待签名字符串
    sign_str = "&".join([f"{k}={v}" for k, v in sorted_params if v and k != "sign"])
    # 签名
    return sign(sign_str, private_key)


def create_payment_url(order: PaymentOrder) -> str:
    """
    创建手机网站支付 URL
    
    Args:
        order: 支付订单
    
    Returns:
        支付页面 URL
    """
    private_key = load_private_key(ALIPAY_PRIVATE_KEY)
    
    # 构建业务参数
    biz_content = {
        "out_trade_no": order.out_trade_no,
        "total_amount": str(order.total_amount),
        "subject": order.subject,
        "product_code": "QUICK_WAP_WAY",  # 手机网站支付
        "return_url": RETURN_URL,
        "notify_url": NOTIFY_URL,
    }
    
    if order.body:
        biz_content["body"] = order.body
    
    # 构建请求参数
    params = build_common_params("alipay.trade.wap.pay")
    params["biz_content"] = json.dumps(biz_content)
    params["sign"] = generate_sign(params, private_key)
    
    # 构建支付 URL
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    pay_url = f"{ALIPAY_GATEWAY}?{query_string}"
    
    return pay_url


def create_pc_payment_url(order: PaymentOrder) -> str:
    """
    创建电脑网站支付 URL（页面跳转）
    
    Args:
        order: 支付订单
    
    Returns:
        支付页面 URL
    """
    private_key = load_private_key(ALIPAY_PRIVATE_KEY)
    
    # 构建业务参数
    biz_content = {
        "out_trade_no": order.out_trade_no,
        "total_amount": str(order.total_amount),
        "subject": order.subject,
        "product_code": "FAST_INSTANT_TRADE_PAY",
        "return_url": RETURN_URL,
        "notify_url": NOTIFY_URL,
    }
    
    if order.body:
        biz_content["body"] = order.body
    
    # 构建请求参数
    params = build_common_params("alipay.trade.page.pay")
    params["biz_content"] = json.dumps(biz_content)
    params["sign"] = generate_sign(params, private_key)
    
    # 构建支付 URL
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    pay_url = f"{ALIPAY_GATEWAY}?{query_string}"
    
    return pay_url


def verify_notify(notify_params: dict) -> bool:
    """
    验证支付回调通知
    
    Args:
        notify_params: 回调参数
    
    Returns:
        验证是否成功
    """
    # 提取签名
    signature = notify_params.pop("sign", "")
    sign_type = notify_params.pop("sign_type", "RSA2")
    
    if sign_type != "RSA2":
        print(f"不支持的签名类型：{sign_type}")
        return False
    
    # 加载公钥
    public_key = load_public_key(ALIPAY_ALIPAY_PUBLIC_KEY)
    
    # 构建待验证字符串
    sorted_params = sorted(notify_params.items())
    sign_str = "&".join([f"{k}={v}" for k, v in sorted_params if v])
    
    # 验证签名
    return verify(sign_str, signature, public_key)


def refund(refund_req: RefundRequest) -> PaymentResponse:
    """
    退款
    
    Args:
        refund_req: 退款请求
    
    Returns:
        退款响应
    """
    private_key = load_private_key(ALIPAY_PRIVATE_KEY)
    
    # 构建业务参数
    biz_content = {
        "refund_amount": str(refund_req.refund_amount),
    }
    
    if refund_req.trade_no:
        biz_content["trade_no"] = refund_req.trade_no
    elif refund_req.out_trade_no:
        biz_content["out_trade_no"] = refund_req.out_trade_no
    else:
        return PaymentResponse(
            success=False,
            out_trade_no="",
            message="必须提供 trade_no 或 out_trade_no"
        )
    
    if refund_req.refund_reason:
        biz_content["refund_reason"] = refund_req.refund_reason
    
    # 构建请求参数
    params = build_common_params("alipay.trade.refund")
    params["biz_content"] = json.dumps(biz_content)
    params["sign"] = generate_sign(params, private_key)
    
    # 发送请求（实际应使用 httpx 发送 HTTP 请求）
    print(f"退款请求参数：{params}")
    print("（实际调用需要发送 HTTP 请求到支付宝网关）")
    
    return PaymentResponse(
        success=True,
        out_trade_no=refund_req.out_trade_no or "",
        message="退款请求已发送"
    )


# ============ 使用示例 ============

def example_payment():
    """支付流程示例"""
    print("=== 支付宝支付示例 ===\n")
    
    # 1. 创建订单
    order = PaymentOrder(
        out_trade_no=f"ORDER_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        total_amount=99.99,
        subject="测试商品",
        body="这是一个测试订单"
    )
    
    print(f"1. 创建订单:")
    print(f"   订单号：{order.out_trade_no}")
    print(f"   金额：¥{order.total_amount}")
    print(f"   标题：{order.subject}\n")
    
    # 2. 生成手机网站支付 URL
    pay_url = create_payment_url(order)
    print(f"2. 手机网站支付 URL:")
    print(f"   {pay_url[:100]}...\n")
    
    # 3. 生成电脑网站支付 URL
    pc_pay_url = create_pc_payment_url(order)
    print(f"3. 电脑网站支付 URL:")
    print(f"   {pc_pay_url[:100]}...\n")
    
    # 4. 模拟回调验证
    print("4. 支付回调验证:")
    mock_notify_params = {
        "out_trade_no": order.out_trade_no,
        "trade_no": "202403181234567890",
        "total_amount": "99.99",
        "trade_status": "TRADE_SUCCESS",
        "sign": "mock-signature",
        "sign_type": "RSA2"
    }
    # is_valid = verify_notify(mock_notify_params)
    print(f"   （实际回调需要验证签名）\n")
    
    # 5. 退款示例
    print("5. 退款示例:")
    refund_req = RefundRequest(
        out_trade_no=order.out_trade_no,
        refund_amount=99.99,
        refund_reason="用户申请退款"
    )
    refund_response = refund(refund_req)
    print(f"   退款结果：{refund_response.message}\n")


if __name__ == "__main__":
    example_payment()

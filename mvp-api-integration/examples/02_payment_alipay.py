"""
API 集成示例 02: 支付宝支付接口

功能：
- 创建支付订单
- 查询订单状态
- 处理支付回调
- 退款操作

依赖：
pip install requests cryptography

文档：
https://opendocs.alipay.com/open/270
"""

import requests
import hashlib
from urllib.parse import quote
import json
from datetime import datetime

# ============ 配置区域 ============
ALIPAY_APP_ID = "your_app_id"
ALIPAY_PRIVATE_KEY = "your_private_key"
ALIPAY_PUBLIC_KEY = "alipay_public_key"
ALIPAY_GATEWAY = "https://openapi.alipay.com/gateway.do"
NOTIFY_URL = "https://your-domain.com/api/payment/alipay/notify"
RETURN_URL = "https://your-domain.com/payment/success"
# =================================


class AlipayClient:
    """支付宝 API 客户端"""
    
    def __init__(self, app_id, private_key, public_key, gateway=ALIPAY_GATEWAY):
        self.app_id = app_id
        self.private_key = private_key
        self.public_key = public_key
        self.gateway = gateway
    
    def _generate_sign(self, params):
        """生成 RSA 签名"""
        # 排序参数
        sorted_params = sorted(params.items())
        # 拼接字符串
        sign_str = "&".join([f"{k}={v}" for k, v in sorted_params if v and k != 'sign'])
        # RSA 签名 (简化示例，实际需要使用私钥加密)
        sign = hashlib.md5(sign_str.encode()).hexdigest()
        return sign
    
    def _build_common_params(self, method):
        """构建公共参数"""
        return {
            'app_id': self.app_id,
            'method': method,
            'format': 'JSON',
            'charset': 'utf-8',
            'sign_type': 'RSA2',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'version': '1.0'
        }
    
    def create_order(self, out_trade_no, total_amount, subject, body=None):
        """
        创建支付订单 (手机网站支付)
        
        Args:
            out_trade_no: 商户订单号
            total_amount: 订单金额 (元)
            subject: 订单标题
            body: 订单描述
        
        Returns:
            str: 支付链接
        """
        method = 'alipay.trade.wap.pay'
        
        # 业务参数
        biz_content = {
            'out_trade_no': out_trade_no,
            'total_amount': str(total_amount),
            'subject': subject,
            'product_code': 'QUICK_WAP_WAY',
            'notify_url': NOTIFY_URL,
            'return_url': RETURN_URL
        }
        
        if body:
            biz_content['body'] = body
        
        # 构建请求参数
        params = self._build_common_params(method)
        params['biz_content'] = json.dumps(biz_content, ensure_ascii=False)
        
        # 生成签名
        params['sign'] = self._generate_sign(params)
        
        # 构建支付链接
        query_string = "&".join([f"{k}={quote(str(v))}" for k, v in params.items()])
        pay_url = f"{self.gateway}?{query_string}"
        
        return pay_url
    
    def query_order(self, out_trade_no=None, trade_no=None):
        """
        查询订单状态
        
        Args:
            out_trade_no: 商户订单号
            trade_no: 支付宝交易号
        
        Returns:
            dict: 订单信息
        """
        method = 'alipay.trade.query'
        
        biz_content = {}
        if out_trade_no:
            biz_content['out_trade_no'] = out_trade_no
        if trade_no:
            biz_content['trade_no'] = trade_no
        
        params = self._build_common_params(method)
        params['biz_content'] = json.dumps(biz_content, ensure_ascii=False)
        params['sign'] = self._generate_sign(params)
        
        response = requests.post(self.gateway, data=params)
        return response.json()
    
    def refund(self, out_trade_no, refund_amount, refund_reason=None):
        """
        退款操作
        
        Args:
            out_trade_no: 原订单商户订单号
            refund_amount: 退款金额
            refund_reason: 退款原因
        
        Returns:
            dict: 退款结果
        """
        method = 'alipay.trade.refund'
        
        biz_content = {
            'out_trade_no': out_trade_no,
            'refund_amount': str(refund_amount),
            'refund_reason': refund_reason or '用户申请退款'
        }
        
        params = self._build_common_params(method)
        params['biz_content'] = json.dumps(biz_content, ensure_ascii=False)
        params['sign'] = self._generate_sign(params)
        
        response = requests.post(self.gateway, data=params)
        return response.json()
    
    def verify_notify(self, notify_data):
        """
        验证支付回调通知
        
        Args:
            notify_data: 回调参数 (POST data)
        
        Returns:
            bool: 验证是否通过
        """
        # 实际实现需要验证支付宝的签名
        # 这里简化处理
        sign = notify_data.pop('sign', None)
        if not sign:
            return False
        
        # 验证签名 (简化示例)
        # 实际需要使用支付宝公钥验证
        return True


# ============ Flask 集成示例 ============
def alipay_payment_routes():
    """Flask 路由示例"""
    from flask import Blueprint, request, jsonify, redirect
    
    bp = Blueprint('alipay', __name__)
    alipay = AlipayClient(ALIPAY_APP_ID, ALIPAY_PRIVATE_KEY, ALIPAY_PUBLIC_KEY)
    
    @bp.route('/create-order', methods=['POST'])
    def create_order():
        """创建支付订单"""
        data = request.json
        out_trade_no = f"ORDER_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        pay_url = alipay.create_order(
            out_trade_no=out_trade_no,
            total_amount=data['amount'],
            subject=data['subject'],
            body=data.get('description')
        )
        
        return jsonify({
            'success': True,
            'order_no': out_trade_no,
            'pay_url': pay_url
        })
    
    @bp.route('/notify', methods=['POST'])
    def notify():
        """支付回调通知"""
        notify_data = request.form.to_dict()
        
        # 验证签名
        if alipay.verify_notify(notify_data):
            trade_status = notify_data.get('trade_status')
            out_trade_no = notify_data.get('out_trade_no')
            
            if trade_status == 'TRADE_SUCCESS':
                # 更新订单状态为已支付
                print(f"✅ 订单 {out_trade_no} 支付成功")
                return 'success'
        
        return 'fail'
    
    @bp.route('/success', methods=['GET'])
    def success():
        """支付成功返回页面"""
        return redirect('/payment/success-page')
    
    return bp


# ============ 使用示例 ============
if __name__ == "__main__":
    print("=" * 50)
    print("支付宝支付 API 集成示例")
    print("=" * 50)
    
    alipay = AlipayClient(ALIPAY_APP_ID, ALIPAY_PRIVATE_KEY, ALIPAY_PUBLIC_KEY)
    
    # 1. 创建支付订单
    print("\n1️⃣  创建支付订单")
    order_no = f"ORDER_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    pay_url = alipay.create_order(
        out_trade_no=order_no,
        total_amount=99.00,
        subject="测试商品 - VIP 会员",
        body="这是一个测试订单"
    )
    print(f"订单号：{order_no}")
    print(f"支付链接：{pay_url[:80]}...")
    
    # 2. 查询订单状态
    print("\n2️⃣  查询订单状态")
    # order_info = alipay.query_order(out_trade_no=order_no)
    print("⚠️  实际环境调用 query_order 查询")
    
    # 3. 退款操作
    print("\n3️⃣  退款操作示例")
    # refund_result = alipay.refund(order_no, 99.00, "用户申请退款")
    print("⚠️  实际环境调用 refund 进行退款")
    
    print("\n" + "=" * 50)
    print("关键要点:")
    print("1. 使用 RSA2 签名确保安全性")
    print("2. 必须配置 notify_url 接收异步通知")
    print("3. 回调通知必须验证签名")
    print("4. 订单号必须全局唯一")
    print("=" * 50)

"""
API 集成示例 2: 支付宝支付
"""
import requests
import hashlib
from urllib.parse import quote

class AlipayClient:
    def __init__(self, app_id, private_key):
        self.app_id = app_id
        self.private_key = private_key
        self.gateway = "https://openapi.alipaydev.com/gateway.do"  # 沙箱环境
    
    def create_order(self, out_trade_no, total_amount, subject):
        """创建订单"""
        params = {
            "app_id": self.app_id,
            "method": "alipay.trade.page.pay",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps({
                "out_trade_no": out_trade_no,
                "total_amount": total_amount,
                "subject": subject
            })
        }
        # 生成签名 (简化版，实际需完整 RSA 签名)
        sign = self._generate_sign(params)
        params["sign"] = sign
        return f"{self.gateway}?{urlencode(params)}"
    
    def verify_callback(self, params):
        """验证回调"""
        sign = params.pop("sign", "")
        return self._verify_sign(params, sign)
    
    def _generate_sign(self, params):
        """生成签名 (简化)"""
        return "mock_sign"
    
    def _verify_sign(self, params, sign):
        """验证签名 (简化)"""
        return True

# 使用示例
if __name__ == "__main__":
    alipay = AlipayClient("app_id", "private_key")
    pay_url = alipay.create_order("ORDER_001", 99.99, "测试商品")
    print(f"支付 URL: {pay_url}")

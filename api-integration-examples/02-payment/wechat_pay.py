"""
API 集成示例 2: 微信支付
"""
import requests
import hashlib
import xml.etree.ElementTree as ET

class WechatPayClient:
    def __init__(self, app_id, mch_id, api_key):
        self.app_id = app_id
        self.mch_id = mch_id
        self.api_key = api_key
        self.unified_order_url = "https://api.mch.weixin.qq.com/pay/unifiedorder"
    
    def create_order(self, out_trade_no, total_fee, body, openid):
        """创建统一下单"""
        params = {
            "appid": self.app_id,
            "mch_id": self.mch_id,
            "nonce_str": self._generate_nonce(),
            "body": body,
            "out_trade_no": out_trade_no,
            "total_fee": int(total_fee * 100),  # 单位：分
            "spbill_create_ip": "127.0.0.1",
            "notify_url": "http://your-domain.com/wechat/callback",
            "trade_type": "JSAPI",
            "openid": openid
        }
        params["sign"] = self._generate_sign(params)
        
        xml_data = self._dict_to_xml(params)
        response = requests.post(self.unified_order_url, data=xml_data.encode('utf-8'))
        return self._parse_xml(response.content)
    
    def _generate_nonce(self):
        import random
        return ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=32))
    
    def _generate_sign(self, params):
        """生成签名"""
        sorted_params = sorted(params.items())
        sign_str = "&".join(f"{k}={v}" for k, v in sorted_params)
        sign_str += f"&key={self.api_key}"
        return hashlib.md5(sign_str.encode('utf-8')).hexdigest().upper()
    
    def _dict_to_xml(self, data):
        """字典转 XML"""
        xml = "<xml>"
        for k, v in data.items():
            xml += f"<{k}>{v}</{k}>"
        xml += "</xml>"
        return xml
    
    def _parse_xml(self, xml_data):
        """解析 XML 响应"""
        root = ET.fromstring(xml_data)
        return {child.tag: child.text for child in root}

# 使用示例
if __name__ == "__main__":
    wechat = WechatPayClient("app_id", "mch_id", "api_key")
    # order = wechat.create_order("ORDER_001", 99.99, "测试商品", "openid_xxx")
    print("微信支付示例已就绪")

"""
微信支付集成示例
用于 MVP 支付功能
注意：此为示例代码，实际使用需要商户资质和正式配置
"""
import hashlib
import hmac
import time
import uuid
import requests
import xml.etree.ElementTree as ET
from typing import Dict, Optional
from urllib.parse import quote


class WeChatPayClient:
    """
    微信支付客户端（V2 版本示例）
    
    使用场景:
    - APP 支付
    - 公众号支付
    - 小程序支付
    - H5 支付
    - 扫码支付
    
    注意：实际使用需要:
    1. 微信开放平台商户资质
    2. 商户号 (mch_id)
    3. API 密钥 (key)
    4. AppID
    """
    
    def __init__(self, app_id: str, mch_id: str, 
                 api_key: str, notify_url: str,
                 sandbox: bool = True):
        """
        初始化微信支付客户端
        
        Args:
            app_id: 公众号/小程序/APP 的 AppID
            mch_id: 商户号
            api_key: API 密钥（商户平台设置）
            notify_url: 支付结果异步通知地址
            sandbox: 是否使用沙箱环境
        """
        self.app_id = app_id
        self.mch_id = mch_id
        self.api_key = api_key
        self.notify_url = notify_url
        self.sandbox = sandbox
        
        # API 地址
        self.unified_order_url = "https://api.mch.weixin.qq.com/sandboxnew/pay/unifiedorder" if sandbox \
            else "https://api.mch.weixin.qq.com/pay/unifiedorder"
        self.order_query_url = "https://api.mch.weixin.qq.com/sandboxnew/pay/orderquery" if sandbox \
            else "https://api.mch.weixin.qq.com/pay/orderquery"
        self.refund_url = "https://api.mch.weixin.qq.com/sandboxnew/secapi/pay/refund" if sandbox \
            else "https://api.mch.weixin.qq.com/secapi/pay/refund"
    
    def _generate_sign(self, params: Dict) -> str:
        """
        生成签名
        
        Args:
            params: 参数字典
        
        Returns:
            MD5 签名（大写）
        """
        # 按参数名 ASCII 码排序
        sorted_params = sorted(params.items(), key=lambda x: x[0])
        
        # 拼接字符串
        string_list = []
        for key, value in sorted_params:
            if value and key != "sign":
                string_list.append(f"{key}={value}")
        
        string_sign_temp = "&".join(string_list) + f"&key={self.api_key}"
        
        # MD5 签名
        sign = hashlib.md5(string_sign_temp.encode("utf-8")).hexdigest().upper()
        return sign
    
    def _dict_to_xml(self, params: Dict) -> str:
        """字典转 XML"""
        xml = ["<xml>"]
        for key, value in params.items():
            if isinstance(value, int):
                xml.append(f"<{key}>{value}</{key}>")
            else:
                xml.append(f"<{key}><![CDATA[{value}]]></{key}>")
        xml.append("</xml>")
        return "".join(xml)
    
    def _xml_to_dict(self, xml_str: str) -> Dict:
        """XML 转字典"""
        root = ET.fromstring(xml_str)
        return {child.tag: child.text for child in root}
    
    def unified_order(self, out_trade_no: str, total_fee: int, 
                      trade_type: str, openid: Optional[str] = None,
                      body: str = "商品描述",
                      spbill_create_ip: str = "127.0.0.1") -> Dict:
        """
        统一下单
        
        Args:
            out_trade_no: 商户订单号
            total_fee: 订单金额（单位：分）
            trade_type: 交易类型（JSAPI, NATIVE, APP, MWEB）
            openid: 用户标识（JSAPI 必填）
            body: 商品描述
            spbill_create_ip: 终端 IP
        
        Returns:
            下单结果
        """
        # 构建请求参数
        params = {
            "appid": self.app_id,
            "mch_id": self.mch_id,
            "nonce_str": uuid.uuid4().hex,
            "body": body,
            "out_trade_no": out_trade_no,
            "total_fee": total_fee,
            "spbill_create_ip": spbill_create_ip,
            "notify_url": self.notify_url,
            "trade_type": trade_type,
        }
        
        if openid and trade_type == "JSAPI":
            params["openid"] = openid
        
        # 生成签名
        params["sign"] = self._generate_sign(params)
        
        # 转换为 XML
        xml_data = self._dict_to_xml(params)
        
        try:
            # 发送请求
            response = requests.post(
                self.unified_order_url,
                data=xml_data.encode("utf-8"),
                headers={"Content-Type": "text/xml"},
                timeout=10
            )
            response.raise_for_status()
            
            # 解析响应
            result = self._xml_to_dict(response.text)
            
            # 验证签名
            if result.get("return_code") == "SUCCESS":
                if result.get("result_code") == "SUCCESS":
                    return {
                        "success": True,
                        "prepay_id": result.get("prepay_id"),
                        "trade_type": result.get("trade_type"),
                        "code_url": result.get("code_url"),  # 扫码支付 URL
                        "result": result
                    }
                else:
                    return {
                        "success": False,
                        "error": result.get("err_code_des", "下单失败"),
                        "result": result
                    }
            else:
                return {
                    "success": False,
                    "error": result.get("return_msg", "通信失败"),
                    "result": result
                }
                
        except Exception as e:
            return {"success": False, "error": f"请求失败：{str(e)}"}
    
    def order_query(self, out_trade_no: Optional[str] = None,
                    transaction_id: Optional[str] = None) -> Dict:
        """
        查询订单
        
        Args:
            out_trade_no: 商户订单号
            transaction_id: 微信支付订单号
        
        Returns:
            订单查询结果
        """
        params = {
            "appid": self.app_id,
            "mch_id": self.mch_id,
            "nonce_str": uuid.uuid4().hex,
        }
        
        if out_trade_no:
            params["out_trade_no"] = out_trade_no
        elif transaction_id:
            params["transaction_id"] = transaction_id
        else:
            return {"success": False, "error": "必须提供订单号"}
        
        # 生成签名
        params["sign"] = self._generate_sign(params)
        
        # 转换为 XML
        xml_data = self._dict_to_xml(params)
        
        try:
            response = requests.post(
                self.order_query_url,
                data=xml_data.encode("utf-8"),
                headers={"Content-Type": "text/xml"},
                timeout=10
            )
            response.raise_for_status()
            
            result = self._xml_to_dict(response.text)
            
            if result.get("return_code") == "SUCCESS":
                if result.get("result_code") == "SUCCESS":
                    return {
                        "success": True,
                        "trade_state": result.get("trade_state"),
                        "total_fee": result.get("total_fee"),
                        "result": result
                    }
                else:
                    return {"success": False, "error": "订单不存在"}
            else:
                return {"success": False, "error": result.get("return_msg")}
                
        except Exception as e:
            return {"success": False, "error": f"查询失败：{str(e)}"}
    
    def get_jsapi_params(self, prepay_id: str) -> Dict:
        """
        获取 JSAPI 支付参数（用于前端调起支付）
        
        Args:
            prepay_id: 统一下单返回的 prepay_id
        
        Returns:
            前端支付参数
        """
        params = {
            "appId": self.app_id,
            "timeStamp": str(int(time.time())),
            "nonceStr": uuid.uuid4().hex,
            "package": f"prepay_id={prepay_id}",
            "signType": "MD5"
        }
        
        # 生成签名
        params["paySign"] = self._generate_sign(params)
        
        return params
    
    def get_app_params(self, prepay_id: str) -> Dict:
        """
        获取 APP 支付参数
        
        Args:
            prepay_id: 统一下单返回的 prepay_id
        
        Returns:
            APP 支付参数
        """
        params = {
            "appid": self.app_id,
            "partnerid": self.mch_id,
            "prepayid": prepay_id,
            "package": "Sign=WXPay",
            "noncestr": uuid.uuid4().hex,
            "timestamp": str(int(time.time()))
        }
        
        # 生成签名
        params["sign"] = self._generate_sign(params)
        
        return params


# ============== 使用示例 ==============

if __name__ == "__main__":
    print("=== 微信支付示例 ===")
    print("注意：此为示例代码，需要正式商户资质才能使用")
    
    # 配置（沙箱环境）
    client = WeChatPayClient(
        app_id="wx8888888888888888",
        mch_id="1234567890",
        api_key="your-api-key",
        notify_url="https://your-domain.com/api/pay/notify",
        sandbox=True
    )
    
    # 示例 1: 统一下单（扫码支付）
    print("\n1. 统一下单（扫码支付）")
    result = client.unified_order(
        out_trade_no=f"ORDER_{int(time.time())}",
        total_fee=1,  # 1 分钱测试
        trade_type="NATIVE",
        body="测试商品"
    )
    print(f"结果：{result}")
    
    # 示例 2: 查询订单
    print("\n2. 查询订单")
    result = client.order_query(out_trade_no="ORDER_123456")
    print(f"结果：{result}")
    
    # 示例 3: 获取 JSAPI 支付参数
    print("\n3. 获取 JSAPI 支付参数")
    params = client.get_jsapi_params("wx2012345678901234567890")
    print(f"参数：{params}")

"""
企业微信消息推送集成示例
用于 MVP 工作流通知和消息推送
"""
import requests
import hmac
import hashlib
import base64
import time
import json
from typing import Optional, List, Dict


class WeChatWorkBot:
    """
    企业微信机器人消息推送客户端
    
    使用场景:
    - 工作流状态变更通知
    - 系统告警推送
    - 定时任务执行结果通知
    """
    
    def __init__(self, webhook_url: str, secret: Optional[str] = None):
        """
        初始化企业微信机器人
        
        Args:
            webhook_url: 机器人 webhook 地址
            secret: 加签密钥（可选，用于消息签名验证）
        """
        self.webhook_url = webhook_url
        self.secret = secret
    
    def _generate_sign(self, timestamp: str) -> str:
        """
        生成消息签名（如果配置了 secret）
        
        Args:
            timestamp: 当前时间戳（字符串）
        
        Returns:
            签名后的字符串（base64+url encode）
        """
        if not self.secret:
            return ""
        
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256
        ).digest()
        
        sign = base64.b64encode(hmac_code).decode("utf-8")
        return sign
    
    def send_text(self, content: str, mentioned_list: List[str] = None, 
                  mentioned_mobile_list: List[str] = None) -> Dict:
        """
        发送文本消息
        
        Args:
            content: 消息内容
            mentioned_list: 要@的用户 ID 列表（["user1", "user2", "@all"]）
            mentioned_mobile_list: 要@的手机号列表
        
        Returns:
            API 响应字典
        """
        payload = {
            "msgtype": "text",
            "text": {
                "content": content,
                "mentioned_list": mentioned_list or [],
                "mentioned_mobile_list": mentioned_mobile_list or []
            }
        }
        return self._send(payload)
    
    def send_markdown(self, content: str) -> Dict:
        """
        发送 Markdown 消息
        
        Args:
            content: Markdown 格式内容
        
        Returns:
            API 响应字典
        """
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": content
            }
        }
        return self._send(payload)
    
    def send_news(self, articles: List[Dict]) -> Dict:
        """
        发送图文消息
        
        Args:
            articles: 图文列表，每项包含 title, description, url, picurl
        
        Returns:
            API 响应字典
        """
        payload = {
            "msgtype": "news",
            "news": {
                "articles": articles
            }
        }
        return self._send(payload)
    
    def send_template_card(self, card_type: str, source: Dict, 
                          main_title: Dict, emphasis_content: Dict = None,
                          quote_area: Dict = None, sub_title_text: str = None,
                          actions: List[Dict] = None) -> Dict:
        """
        发送模板卡片消息
        
        Args:
            card_type: 卡片类型（text_notice, news_notice, button_interaction 等）
            source: 卡片来源信息
            main_title: 卡片主标题
            emphasis_content: 重点内容（可选）
            quote_area: 引用区域（可选）
            sub_title_text: 副标题（可选）
            actions: 操作按钮列表（可选）
        
        Returns:
            API 响应字典
        """
        card_data = {
            "card_type": card_type,
            "source": source,
            "main_title": main_title
        }
        
        if emphasis_content:
            card_data["emphasis_content"] = emphasis_content
        if quote_area:
            card_data["quote_area"] = quote_area
        if sub_title_text:
            card_data["sub_title_text"] = sub_title_text
        if actions:
            card_data["actions"] = actions
        
        payload = {
            "msgtype": "template_card",
            "template_card": card_data
        }
        return self._send(payload)
    
    def _send(self, payload: Dict) -> Dict:
        """
        发送消息到企业微信
        
        Args:
            payload: 消息体
        
        Returns:
            API 响应字典
        """
        # 添加签名参数（如果配置了 secret）
        params = {}
        if self.secret:
            timestamp = str(int(time.time()))
            sign = self._generate_sign(timestamp)
            params["timestamp"] = timestamp
            params["sign"] = sign
        
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                params=params if params else None,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"errcode": -1, "errmsg": f"请求失败：{str(e)}"}


# ============== 使用示例 ==============

def example_usage():
    """使用示例"""
    
    # 配置（从环境变量或配置文件中获取）
    WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY"
    SECRET = "YOUR_SECRET"  # 可选
    
    # 初始化机器人
    bot = WeChatWorkBot(WEBHOOK_URL, SECRET)
    
    # 示例 1: 发送文本消息
    print("=== 发送文本消息 ===")
    result = bot.send_text(
        content="🔔 工作流通知：订单 #12345 已处理完成",
        mentioned_list=["@all"]  # @所有人
    )
    print(f"响应：{result}")
    
    # 示例 2: 发送 Markdown 消息
    print("\n=== 发送 Markdown 消息 ===")
    markdown_content = """## 工作流执行报告

**流程名称**: 订单处理流程
**执行时间**: 2026-03-17 21:00:00
**执行状态**: <font color="info">成功</font>

### 执行详情
- 步骤 1: 订单验证 ✅
- 步骤 2: 库存检查 ✅
- 步骤 3: 支付处理 ✅
- 步骤 4: 发货通知 ✅

**总耗时**: 2.5 秒
"""
    result = bot.send_markdown(markdown_content)
    print(f"响应：{result}")
    
    # 示例 3: 发送图文消息
    print("\n=== 发送图文消息 ===")
    articles = [
        {
            "title": "MVP 项目进度更新",
            "description": "Sprint 1 开发任务已完成 80%",
            "url": "https://example.com/mvp-progress",
            "picurl": "https://example.com/image.jpg"
        }
    ]
    result = bot.send_news(articles)
    print(f"响应：{result}")
    
    # 示例 4: 发送模板卡片（工作流审批）
    print("\n=== 发送模板卡片 ===")
    result = bot.send_template_card(
        card_type="button_interaction",
        source={
            "icon_url": "https://example.com/icon.png",
            "desc": "工作流审批"
        },
        main_title={
            "title": "新的审批请求",
            "desc": "订单 #12345 需要您的审批"
        },
        emphasis_content={
            "title": "审批金额：¥5,000",
            "desc": "申请人：张三"
        },
        actions=[
            {
                "type": "button",
                "text": "同意",
                "key": "approve",
                "style": "1"  # 蓝色
            },
            {
                "type": "button",
                "text": "拒绝",
                "key": "reject",
                "style": "0"  # 红色
            }
        ]
    )
    print(f"响应：{result}")


if __name__ == "__main__":
    example_usage()

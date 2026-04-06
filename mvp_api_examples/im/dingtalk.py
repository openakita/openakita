"""
钉钉消息推送集成示例
用于 MVP 工作流通知和消息推送
"""
import requests
import hmac
import hashlib
import base64
import time
import json
from typing import Optional, List, Dict
from urllib.parse import quote_plus


class DingTalkBot:
    """
    钉钉机器人消息推送客户端
    
    使用场景:
    - 工作流状态变更通知
    - 系统告警推送
    - 定时任务执行结果通知
    """
    
    def __init__(self, webhook_url: str, secret: Optional[str] = None):
        """
        初始化钉钉机器人
        
        Args:
            webhook_url: 机器人 webhook 地址（包含 access_token）
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
            URL 编码后的签名
        """
        if not self.secret:
            return ""
        
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256
        ).digest()
        
        sign = base64.b64encode(hmac_code).decode("utf-8")
        return quote_plus(sign)
    
    def _build_webhook_url(self) -> str:
        """
        构建带签名的 webhook URL
        
        Returns:
            完整的 webhook URL
        """
        if self.secret:
            timestamp = str(round(time.time() * 1000))
            sign = self._generate_sign(timestamp)
            return f"{self.webhook_url}&timestamp={timestamp}&sign={sign}"
        return self.webhook_url
    
    def send_text(self, content: str, at_mobiles: List[str] = None, 
                  at_user_ids: List[str] = None, is_at_all: bool = False) -> Dict:
        """
        发送文本消息
        
        Args:
            content: 消息内容
            at_mobiles: 要@的手机号列表
            at_user_ids: 要@的用户 ID 列表
            is_at_all: 是否@所有人
        
        Returns:
            API 响应字典
        """
        at = {}
        if at_mobiles:
            at["atMobiles"] = at_mobiles
        if at_user_ids:
            at["atUserIds"] = at_user_ids
        if is_at_all:
            at["isAtAll"] = True
        
        payload = {
            "msgtype": "text",
            "text": {
                "content": content
            },
            "at": at
        }
        return self._send(payload)
    
    def send_link(self, title: str, text: str, message_url: str, 
                  pic_url: Optional[str] = None) -> Dict:
        """
        发送链接消息
        
        Args:
            title: 消息标题
            text: 消息内容
            message_url: 点击消息跳转的 URL
            pic_url: 图片 URL（可选）
        
        Returns:
            API 响应字典
        """
        payload = {
            "msgtype": "link",
            "link": {
                "title": title,
                "text": text,
                "messageUrl": message_url,
                "picUrl": pic_url or ""
            }
        }
        return self._send(payload)
    
    def send_markdown(self, title: str, text: str, 
                      at_mobiles: List[str] = None, 
                      at_user_ids: List[str] = None,
                      is_at_all: bool = False) -> Dict:
        """
        发送 Markdown 消息
        
        Args:
            title: 消息标题
            text: Markdown 格式内容
            at_mobiles: 要@的手机号列表
            at_user_ids: 要@的用户 ID 列表
            is_at_all: 是否@所有人
        
        Returns:
            API 响应字典
        """
        at = {}
        if at_mobiles:
            at["atMobiles"] = at_mobiles
        if at_user_ids:
            at["atUserIds"] = at_user_ids
        if is_at_all:
            at["isAtAll"] = True
        
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text
            },
            "at": at
        }
        return self._send(payload)
    
    def send_action_card(self, title: str, text: str, 
                         btn_orientation: str = "0",
                         btns: List[Dict] = None) -> Dict:
        """
        发送行动卡片消息
        
        Args:
            title: 卡片标题
            text: 卡片内容
            btn_orientation: 按钮排列方向（0: 竖直，1: 水平）
            btns: 按钮列表，每项包含 title, actionURL
        
        Returns:
            API 响应字典
        """
        payload = {
            "msgtype": "actionCard",
            "actionCard": {
                "title": title,
                "text": text,
                "btnOrientation": btn_orientation,
                "btns": btns or []
            }
        }
        return self._send(payload)
    
    def send_feed_card(self, feeds: List[Dict]) -> Dict:
        """
        发送 Feed 卡片消息
        
        Args:
            feeds: Feed 列表，每项包含 title, messageURL, picURL
        
        Returns:
            API 响应字典
        """
        payload = {
            "msgtype": "feedCard",
            "feedCard": {
                "links": feeds
            }
        }
        return self._send(payload)
    
    def _send(self, payload: Dict) -> Dict:
        """
        发送消息到钉钉
        
        Args:
            payload: 消息体
        
        Returns:
            API 响应字典
        """
        url = self._build_webhook_url()
        
        headers = {"Content-Type": "application/json; charset=utf-8"}
        
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
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
    WEBHOOK_URL = "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN"
    SECRET = "YOUR_SECRET"  # 可选
    
    # 初始化机器人
    bot = DingTalkBot(WEBHOOK_URL, SECRET)
    
    # 示例 1: 发送文本消息
    print("=== 发送文本消息 ===")
    result = bot.send_text(
        content="🔔 工作流通知：订单 #12345 已处理完成",
        is_at_all=True  # @所有人
    )
    print(f"响应：{result}")
    
    # 示例 2: 发送 Markdown 消息
    print("\n=== 发送 Markdown 消息 ===")
    markdown_content = """## 工作流执行报告

**流程名称**: 订单处理流程
**执行时间**: 2026-03-17 21:00:00
**执行状态**: <font color="#32CD32">成功</font>

### 执行详情
- 步骤 1: 订单验证 ✅
- 步骤 2: 库存检查 ✅
- 步骤 3: 支付处理 ✅
- 步骤 4: 发货通知 ✅

**总耗时**: 2.5 秒
"""
    result = bot.send_markdown(
        title="工作流执行报告",
        text=markdown_content
    )
    print(f"响应：{result}")
    
    # 示例 3: 发送链接消息
    print("\n=== 发送链接消息 ===")
    result = bot.send_link(
        title="MVP 项目进度更新",
        text="Sprint 1 开发任务已完成 80%，预计本周五完成全部开发工作。",
        message_url="https://example.com/mvp-progress",
        pic_url="https://example.com/image.jpg"
    )
    print(f"响应：{result}")
    
    # 示例 4: 发送行动卡片（工作流审批）
    print("\n=== 发送行动卡片 ===")
    result = bot.send_action_card(
        title="新的审批请求",
        text=f"""## 订单审批
**订单号**: #12345
**申请人**: 张三
**审批金额**: ¥5,000
**申请时间**: 2026-03-17 21:00:00

请审批此订单。""",
        btn_orientation="1",  # 水平排列
        btns=[
            {"title": "同意", "actionURL": "https://example.com/approve/12345"},
            {"title": "拒绝", "actionURL": "https://example.com/reject/12345"}
        ]
    )
    print(f"响应：{result}")


if __name__ == "__main__":
    example_usage()

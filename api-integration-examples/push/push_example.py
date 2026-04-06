"""
推送通知 API 集成示例代码
功能：单推、群推、别名推送、通知栏消息
支持：极光推送、个推
"""

from typing import Optional, List
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import time
import hashlib
import hmac

load_dotenv()

# 极光推送配置
JPUSH_APP_KEY = os.getenv("JPUSH_APP_KEY", "your-app-key")
JPUSH_MASTER_SECRET = os.getenv("JPUSH_MASTER_SECRET", "your-master-secret")

# 个推配置
GETUI_APP_ID = os.getenv("GETUI_APP_ID", "your-app-id")
GETUI_APP_KEY = os.getenv("GETUI_APP_KEY", "your-app-key")
GETUI_MASTER_SECRET = os.getenv("GETUI_MASTER_SECRET", "your-master-secret")


class PushRequest(BaseModel):
    """推送请求"""
    title: str
    content: str
    extras: Optional[dict] = None
    platform: str = "all"  # all/ios/android
    audience: str = "all"  # all/tag/alias/registration_id
    target: Optional[List[str]] = None


class PushResponse(BaseModel):
    """推送响应"""
    success: bool
    message_id: Optional[str] = None
    message: str
    provider: str


# ============ 极光推送 ============

class JPushClient:
    """极光推送客户端"""
    
    def __init__(self):
        self.app_key = JPUSH_APP_KEY
        self.master_secret = JPUSH_MASTER_SECRET
        self.base_url = "https://api.jpush.cn/v3/push"
    
    def push(self, request: PushRequest) -> PushResponse:
        """
        推送通知
        
        Args:
            request: 推送请求
        
        Returns:
            推送响应
        """
        import base64
        
        # 构建请求体
        payload = {
            "platform": request.platform,
            "audience": {},
            "notification": {
                "alert": request.content,
                "android": {
                    "title": request.title,
                    "extras": request.extras or {}
                },
                "ios": {
                    "alert": request.content,
                    "sound": "default",
                    "extras": request.extras or {}
                }
            },
            "options": {
                "time_to_live": 86400,
                "apns_production": False
            }
        }
        
        # 设置受众
        if request.audience == "all":
            payload["audience"] = "all"
        elif request.audience == "tag":
            payload["audience"]["tag"] = request.target
        elif request.audience == "alias":
            payload["audience"]["alias"] = request.target
        elif request.audience == "registration_id":
            payload["audience"]["registration_id"] = request.target
        
        # 生成 Authorization
        auth_string = f"Basic {base64.b64encode(f'{self.master_secret}:'.encode()).decode()}"
        
        print(f"极光推送:")
        print(f"  URL: {self.base_url}")
        print(f"  标题：{request.title}")
        print(f"  内容：{request.content}")
        print(f"  平台：{request.platform}")
        print(f"  受众：{request.audience}")
        if request.target:
            print(f"  目标：{request.target}")
        print()
        
        # 模拟响应
        return PushResponse(
            success=True,
            message_id=f"JPUSH_{int(time.time())}",
            message="推送成功",
            provider="jpush"
        )
    
    def push_to_alias(
        self,
        alias: List[str],
        title: str,
        content: str,
        extras: Optional[dict] = None
    ) -> PushResponse:
        """
        推送给别名用户
        
        Args:
            alias: 别名列表
            title: 标题
            content: 内容
            extras: 额外数据
        
        Returns:
            推送响应
        """
        request = PushRequest(
            title=title,
            content=content,
            extras=extras,
            audience="alias",
            target=alias
        )
        return self.push(request)


# ============ 个推 ============

class GetuiClient:
    """个推客户端"""
    
    def __init__(self):
        self.app_id = GETUI_APP_ID
        self.app_key = GETUI_APP_KEY
        self.master_secret = GETUI_MASTER_SECRET
        self.base_url = "https://restapi.getui.com/v2"
        self.token = None
        self.token_expire = 0
    
    def _get_token(self) -> str:
        """获取访问 Token"""
        if self.token and time.time() < self.token_expire:
            return self.token
        
        # 生成签名
        timestamp = str(int(time.time() * 1000))
        sign = hashlib.sha256(
            f"{self.app_key}{timestamp}{self.master_secret}".encode()
        ).hexdigest()
        
        print(f"个推获取 Token:")
        print(f"  AppKey: {self.app_key}")
        print(f"  Timestamp: {timestamp}")
        print()
        
        # 模拟 Token
        self.token = f"TOKEN_{timestamp}"
        self.token_expire = time.time() + 86400
        
        return self.token
    
    def push(self, request: PushRequest) -> PushResponse:
        """
        推送通知
        
        Args:
            request: 推送请求
        
        Returns:
            推送响应
        """
        token = self._get_token()
        
        # 构建请求体
        payload = {
            "request_id": f"req_{int(time.time())}",
            "audience": {
                "cid": request.target if request.target else []
            },
            "push_message": {
                "notification": {
                    "title": request.title,
                    "body": request.content,
                    "click_type": "url",
                    "url": "https://example.com"
                },
                "extras": request.extras or {}
            }
        }
        
        print(f"个推推送:")
        print(f"  AppID: {self.app_id}")
        print(f"  标题：{request.title}")
        print(f"  内容：{request.content}")
        print()
        
        return PushResponse(
            success=True,
            message_id=f"GETUI_{int(time.time())}",
            message="推送成功",
            provider="getui"
        )
    
    def push_single(
        self,
        cid: str,
        title: str,
        content: str
    ) -> PushResponse:
        """
        单推
        
        Args:
            cid: 客户端 ID
            title: 标题
            content: 内容
        
        Returns:
            推送响应
        """
        request = PushRequest(
            title=title,
            content=content,
            audience="registration_id",
            target=[cid]
        )
        return self.push(request)
    
    def push_list(
        self,
        cid_list: List[str],
        title: str,
        content: str
    ) -> PushResponse:
        """
        群推
        
        Args:
            cid_list: 客户端 ID 列表
            title: 标题
            content: 内容
        
        Returns:
            推送响应
        """
        request = PushRequest(
            title=title,
            content=content,
            audience="registration_id",
            target=cid_list
        )
        return self.push(request)


# ============ 统一推送服务 ============

class PushService:
    """统一推送服务"""
    
    def __init__(self, provider: str = "jpush"):
        self.provider = provider
        if provider == "jpush":
            self.client = JPushClient()
        elif provider == "getui":
            self.client = GetuiClient()
        else:
            raise ValueError(f"不支持的服务商：{provider}")
    
    def push(
        self,
        title: str,
        content: str,
        audience: str = "all"
    ) -> PushResponse:
        """推送通知"""
        request = PushRequest(
            title=title,
            content=content,
            audience=audience
        )
        return self.client.push(request)
    
    def push_to_user(
        self,
        user_id: str,
        title: str,
        content: str
    ) -> PushResponse:
        """推送给指定用户"""
        if self.provider == "jpush":
            return self.client.push_to_alias([user_id], title, content)
        else:
            return self.client.push_single(user_id, title, content)


# ============ 使用示例 ============

def example_push():
    """推送通知示例"""
    print("=== 推送通知 API 示例 ===\n")
    
    # 1. 极光推送
    print("1. 极光推送:")
    jpush = JPushClient()
    response = jpush.push(PushRequest(
        title="系统通知",
        content="您有新的消息",
        extras={"type": "message", "id": "123"}
    ))
    print(f"   消息 ID: {response.message_id}")
    print(f"   结果：{response.message}\n")
    
    # 2. 极光推送给别名
    print("2. 极光推送给别名:")
    response = jpush.push_to_alias(
        alias=["user_001", "user_002"],
        title="订单通知",
        content="您的订单已发货",
        extras={"order_id": "ORDER_123"}
    )
    print(f"   结果：{response.message}\n")
    
    # 3. 个推单推
    print("3. 个推单推:")
    getui = GetuiClient()
    response = getui.push_single(
        cid="CID_123456",
        title="活动提醒",
        content="限时优惠即将结束"
    )
    print(f"   消息 ID: {response.message_id}")
    print(f"   结果：{response.message}\n")
    
    # 4. 个推群推
    print("4. 个推群推:")
    response = getui.push_list(
        cid_list=["CID_1", "CID_2", "CID_3"],
        title="系统维护",
        content="系统将于今晚 23:00 进行维护"
    )
    print(f"   结果：{response.message}\n")
    
    # 5. 统一推送服务
    print("5. 统一推送服务:")
    push_service = PushService(provider="jpush")
    response = push_service.push(
        title="测试通知",
        content="这是一条测试消息",
        audience="all"
    )
    print(f"   服务商：{push_service.provider}")
    print(f"   结果：{response.message}")


if __name__ == "__main__":
    example_push()

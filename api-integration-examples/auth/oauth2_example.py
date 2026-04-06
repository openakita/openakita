"""
OAuth2 认证示例代码
功能：第三方登录（GitHub/Google/微信等）、授权码流程、Token 交换
"""

from typing import Optional
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import httpx
from urllib.parse import urlencode

load_dotenv()

# OAuth2 配置（以 GitHub 为例）
OAUTH_PROVIDER = "github"  # 支持：github, google, wechat
OAUTH_CONFIGS = {
    "github": {
        "authorization_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "user_info_url": "https://api.github.com/user",
        "scope": "user:email",
    },
    "google": {
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "user_info_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scope": "openid email profile",
    },
    "wechat": {
        "authorization_url": "https://open.weixin.qq.com/connect/qrconnect",
        "token_url": "https://api.weixin.qq.com/sns/oauth2/access_token",
        "user_info_url": "https://api.weixin.qq.com/sns/userinfo",
        "scope": "snsapi_login",
    }
}

# 从环境变量加载配置
CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "your-client-id")
CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "your-client-secret")
REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8000/auth/callback")


class OAuthUser(BaseModel):
    """OAuth 用户信息"""
    id: str
    username: str
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    provider: str


class OAuthToken(BaseModel):
    """OAuth Token"""
    access_token: str
    token_type: str = "bearer"
    expires_in: Optional[int] = None
    refresh_token: Optional[str] = None
    scope: Optional[str] = None


def get_authorization_url(state: str = "") -> str:
    """
    获取 OAuth 授权 URL
    
    Args:
        state: 防 CSRF 攻击的随机状态字符串
    
    Returns:
        授权 URL
    """
    config = OAUTH_CONFIGS[OAUTH_PROVIDER]
    
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": config["scope"],
    }
    
    if state:
        params["state"] = state
    
    # GitHub 特殊处理
    if OAUTH_PROVIDER == "github":
        return f"{config['authorization_url']}?{urlencode(params)}"
    elif OAUTH_PROVIDER == "wechat":
        params["appid"] = CLIENT_ID
        return f"{config['authorization_url']}?{urlencode(params)}#wechat_redirect"
    else:
        return f"{config['authorization_url']}?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> Optional[OAuthToken]:
    """
    使用授权码交换 Access Token
    
    Args:
        code: 授权码
    
    Returns:
        OAuthToken 如果成功，否则 None
    """
    config = OAUTH_CONFIGS[OAUTH_PROVIDER]
    
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    
    headers = {"Accept": "application/json"}
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                config["token_url"],
                data=data,
                headers=headers
            )
            response.raise_for_status()
            
            token_data = response.json()
            
            return OAuthToken(
                access_token=token_data.get("access_token"),
                token_type=token_data.get("token_type", "bearer"),
                expires_in=token_data.get("expires_in"),
                refresh_token=token_data.get("refresh_token"),
                scope=token_data.get("scope")
            )
        except httpx.HTTPError as e:
            print(f"Token 交换失败：{e}")
            return None


async def get_user_info(access_token: str) -> Optional[OAuthUser]:
    """
    获取用户信息
    
    Args:
        access_token: Access Token
    
    Returns:
        OAuthUser 如果成功，否则 None
    """
    config = OAUTH_CONFIGS[OAUTH_PROVIDER]
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                config["user_info_url"],
                headers=headers
            )
            response.raise_for_status()
            
            user_data = response.json()
            
            # 不同提供商的字段映射
            if OAUTH_PROVIDER == "github":
                return OAuthUser(
                    id=str(user_data.get("id")),
                    username=user_data.get("login"),
                    email=user_data.get("email"),
                    avatar_url=user_data.get("avatar_url"),
                    provider=OAUTH_PROVIDER
                )
            elif OAUTH_PROVIDER == "google":
                return OAuthUser(
                    id=user_data.get("id"),
                    username=user_data.get("name"),
                    email=user_data.get("email"),
                    avatar_url=user_data.get("picture"),
                    provider=OAUTH_PROVIDER
                )
            elif OAUTH_PROVIDER == "wechat":
                return OAuthUser(
                    id=user_data.get("openid"),
                    username=user_data.get("nickname"),
                    avatar_url=user_data.get("headimgurl"),
                    provider=OAUTH_PROVIDER
                )
            else:
                return OAuthUser(
                    id=str(user_data.get("id", "")),
                    username=user_data.get("name", ""),
                    provider=OAUTH_PROVIDER
                )
        except httpx.HTTPError as e:
            print(f"获取用户信息失败：{e}")
            return None


async def refresh_access_token(refresh_token: str) -> Optional[OAuthToken]:
    """
    刷新 Access Token
    
    Args:
        refresh_token: Refresh Token
    
    Returns:
        新的 OAuthToken，如果失败返回 None
    """
    config = OAUTH_CONFIGS[OAUTH_PROVIDER]
    
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                config["token_url"],
                data=data
            )
            response.raise_for_status()
            
            token_data = response.json()
            
            return OAuthToken(
                access_token=token_data.get("access_token"),
                token_type=token_data.get("token_type", "bearer"),
                expires_in=token_data.get("expires_in"),
                refresh_token=token_data.get("refresh_token"),
                scope=token_data.get("scope")
            )
        except httpx.HTTPError as e:
            print(f"刷新 Token 失败：{e}")
            return None


# ============ FastAPI 集成示例 ============

"""
在 FastAPI 中的使用示例：

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse

app = FastAPI()

@app.get("/auth/login")
async def oauth_login():
    '''重定向到 OAuth 提供商'''
    state = generate_random_state()  # 实现随机状态生成
    # 将 state 存储到 session 或 Redis
    auth_url = get_authorization_url(state)
    return RedirectResponse(url=auth_url)

@app.get("/auth/callback")
async def oauth_callback(request: Request, code: str, state: str):
    '''OAuth 回调处理'''
    # 验证 state 防止 CSRF
    stored_state = get_stored_state()  # 从 session 或 Redis 获取
    if state != stored_state:
        raise HTTPException(status_code=400, detail="Invalid state")
    
    # 交换 Token
    token = await exchange_code_for_token(code)
    if not token:
        raise HTTPException(status_code=400, detail="Token exchange failed")
    
    # 获取用户信息
    user = await get_user_info(token.access_token)
    if not user:
        raise HTTPException(status_code=400, detail="Failed to get user info")
    
    # 这里可以创建或更新本地用户
    # local_user = await get_or_create_user(user)
    
    # 创建本地 session 或 JWT
    # return create_local_session(local_user)
    
    return {"message": "登录成功", "user": user.dict()}
"""


# ============ 使用示例 ============

async def example_oauth_flow():
    """模拟 OAuth 登录流程"""
    print(f"=== OAuth2 登录示例 ({OAUTH_PROVIDER}) ===\n")
    
    # 1. 生成授权 URL
    auth_url = get_authorization_url(state="random-state-123")
    print(f"1. 授权 URL:\n{auth_url}\n")
    print("用户访问此 URL 并授权...\n")
    
    # 2. 模拟用户授权后获得 code
    print("2. 用户授权后，重定向到回调 URL 并携带 code")
    mock_code = "mock-authorization-code-123"
    print(f"   获得授权码：{mock_code}\n")
    
    # 3. 交换 Token（需要真实 code，这里仅展示流程）
    print("3. 使用 code 交换 Access Token")
    print("   （实际调用需要真实的授权码）\n")
    
    # 4. 获取用户信息（需要真实 token，这里仅展示流程）
    print("4. 使用 Access Token 获取用户信息")
    print("   （实际调用需要真实的 Access Token）\n")
    
    print("OAuth 流程完成！")


if __name__ == "__main__":
    import asyncio
    asyncio.run(example_oauth_flow())

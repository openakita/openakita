"""
用户认证 API 示例 - OAuth2
功能：实现 OAuth2 授权码流程，支持第三方登录 (GitHub/Google/微信等)
"""
import requests
from urllib.parse import urlencode, parse_qs
from typing import Optional, Dict
import secrets


class OAuth2Config:
    """OAuth2 配置类"""
    
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, 
                 authorization_url: str, token_url: str, user_info_url: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.authorization_url = authorization_url
        self.token_url = token_url
        self.user_info_url = user_info_url
        self.state = secrets.token_urlsafe(32)  # 防止 CSRF 攻击


class OAuth2Client:
    """OAuth2 客户端"""
    
    def __init__(self, config: OAuth2Config):
        self.config = config
    
    def get_authorization_url(self, scope: list = None) -> str:
        """
        获取授权 URL
        
        Args:
            scope: 请求的权限范围
            
        Returns:
            授权页面 URL
        """
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": "code",
            "state": self.config.state,
        }
        
        if scope:
            params["scope"] = " ".join(scope)
        
        return f"{self.config.authorization_url}?{urlencode(params)}"
    
    def get_access_token(self, code: str) -> Optional[Dict]:
        """
        使用授权码获取访问令牌
        
        Args:
            code: 授权码
            
        Returns:
            Token 信息字典，失败返回 None
        """
        data = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "grant_type": "authorization_code",
        }
        
        try:
            response = requests.post(self.config.token_url, data=data)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"获取 Access Token 失败：{e}")
            return None
    
    def get_user_info(self, access_token: str) -> Optional[Dict]:
        """
        获取用户信息
        
        Args:
            access_token: 访问令牌
            
        Returns:
            用户信息字典，失败返回 None
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }
        
        try:
            response = requests.get(self.config.user_info_url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"获取用户信息失败：{e}")
            return None
    
    def verify_state(self, state: str) -> bool:
        """
        验证 state 参数 (防止 CSRF)
        
        Args:
            state: 回调中的 state 参数
            
        Returns:
            验证是否通过
        """
        return secrets.compare_digest(state, self.config.state)


# ==================== 第三方平台配置 ====================

def get_github_oauth_config() -> OAuth2Config:
    """GitHub OAuth2 配置"""
    return OAuth2Config(
        client_id="YOUR_GITHUB_CLIENT_ID",
        client_secret="YOUR_GITHUB_CLIENT_SECRET",
        redirect_uri="http://localhost:8000/auth/github/callback",
        authorization_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        user_info_url="https://api.github.com/user"
    )


def get_google_oauth_config() -> OAuth2Config:
    """Google OAuth2 配置"""
    return OAuth2Config(
        client_id="YOUR_GOOGLE_CLIENT_ID",
        client_secret="YOUR_GOOGLE_CLIENT_SECRET",
        redirect_uri="http://localhost:8000/auth/google/callback",
        authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        user_info_url="https://www.googleapis.com/oauth2/v2/userinfo"
    )


def get_wechat_oauth_config() -> OAuth2Config:
    """微信 OAuth2 配置 (网站应用)"""
    return OAuth2Config(
        client_id="YOUR_WECHAT_APP_ID",
        client_secret="YOUR_WECHAT_APP_SECRET",
        redirect_uri="http://localhost:8000/auth/wechat/callback",
        authorization_url="https://open.weixin.qq.com/connect/qrconnect",
        token_url="https://api.weixin.qq.com/sns/oauth2/access_token",
        user_info_url="https://api.weixin.qq.com/sns/userinfo"
    )


# ==================== 使用示例 ====================

def demo_oauth2_flow():
    """OAuth2 流程演示"""
    print("=" * 50)
    print("OAuth2 认证流程示例 (以 GitHub 为例)")
    print("=" * 50)
    
    # 1. 初始化 OAuth2 客户端
    config = get_github_oauth_config()
    oauth = OAuth2Client(config)
    
    print(f"\n1. 生成授权 URL:")
    auth_url = oauth.get_authorization_url(scope=["user:email"])
    print(f"   授权 URL: {auth_url[:80]}...")
    print(f"   State: {config.state}")
    print(f"   操作：用户在浏览器打开此 URL 进行授权")
    
    # 2. 用户授权后，GitHub 会重定向到 redirect_uri?code=xxx&state=xxx
    print(f"\n2. 用户授权后:")
    print(f"   GitHub 重定向到：{config.redirect_uri}?code=AUTH_CODE&state=STATE")
    print(f"   操作：从 URL 参数中提取 code 和 state")
    
    # 3. 验证 state 并获取 token
    print(f"\n3. 验证 state 并获取 Token:")
    mock_code = "mock_authorization_code"
    mock_state = config.state
    
    if oauth.verify_state(mock_state):
        print(f"   ✓ State 验证通过")
        print(f"   使用 code 换取 access_token...")
        # token_info = oauth.get_access_token(mock_code)
        print(f"   (实际调用需要真实 code)")
    else:
        print(f"   ✗ State 验证失败，可能存在 CSRF 攻击")
    
    # 4. 获取用户信息
    print(f"\n4. 获取用户信息:")
    print(f"   使用 access_token 调用 {config.user_info_url}")
    # user_info = oauth.get_user_info(access_token)
    print(f"   (实际调用需要真实 access_token)")
    
    print("\n" + "=" * 50)
    print("OAuth2 流程说明:")
    print("1. 用户点击'第三方登录'按钮")
    print("2. 后端生成授权 URL，前端重定向到第三方")
    print("3. 用户在第三方平台授权")
    print("4. 第三方重定向回回调 URL，附带 authorization_code")
    print("5. 后端用 code 换取 access_token")
    print("6. 后端用 access_token 获取用户信息")
    print("7. 创建本地会话或 JWT token")
    print("=" * 50)


if __name__ == "__main__":
    demo_oauth2_flow()

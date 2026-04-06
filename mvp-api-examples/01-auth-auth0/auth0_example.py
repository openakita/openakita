# Auth0 用户认证 API 集成示例
# 适用于 MVP 用户登录/注册/身份验证

import os
from auth0.authentication import GetToken
from auth0.management import Auth0

# 配置（从环境变量读取）
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "your-domain.auth0.com")
CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "your-client-id")
CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET", "your-client-secret")
API_AUDIENCE = os.getenv("AUTH0_API_AUDIENCE", "https://your-api")


class Auth0Client:
    """Auth0 认证客户端封装"""
    
    def __init__(self):
        self.get_token = GetToken(AUTH0_DOMAIN)
        self.auth0 = Auth0(
            domain=AUTH0_DOMAIN,
            token=self._get_management_token()
        )
    
    def _get_management_token(self) -> str:
        """获取管理 API 访问令牌"""
        token = self.get_token.client_credentials(
            audience=f"https://{AUTH0_DOMAIN}/api/v2/",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        )
        return token["access_token"]
    
    def register_user(self, email: str, password: str, user_metadata: dict = None) -> dict:
        """
        注册用户
        
        Args:
            email: 用户邮箱
            password: 密码
            user_metadata: 用户元数据（可选）
        
        Returns:
            用户信息字典
        """
        try:
            user = self.auth0.users.create({
                "email": email,
                "password": password,
                "connection": "Username-Password-Authentication",
                "user_metadata": user_metadata or {}
            })
            return {"success": True, "user_id": user["user_id"], "email": user["email"]}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def login(self, email: str, password: str) -> dict:
        """
        用户登录
        
        Args:
            email: 用户邮箱
            password: 密码
        
        Returns:
            包含 access_token 的字典
        """
        try:
            token = self.get_token.password(
                realm="Username-Password-Authentication",
                username=email,
                password=password,
                client_id=CLIENT_ID,
                audience=API_AUDIENCE
            )
            return {"success": True, "access_token": token["access_token"]}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_user_info(self, user_id: str) -> dict:
        """
        获取用户信息
        
        Args:
            user_id: 用户 ID
        
        Returns:
            用户信息字典
        """
        try:
            user = self.auth0.users.get(user_id)
            return {"success": True, "user": user}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def update_user_metadata(self, user_id: str, metadata: dict) -> dict:
        """
        更新用户元数据
        
        Args:
            user_id: 用户 ID
            metadata: 要更新的元数据
        
        Returns:
            更新结果
        """
        try:
            user = self.auth0.users.update(
                user_id,
                {"user_metadata": metadata}
            )
            return {"success": True, "user": user}
        except Exception as e:
            return {"success": False, "error": str(e)}


# 使用示例
if __name__ == "__main__":
    client = Auth0Client()
    
    # 1. 注册用户
    result = client.register_user(
        email="test@example.com",
        password="SecurePassword123!",
        user_metadata={"name": "Test User", "role": "admin"}
    )
    print(f"注册结果：{result}")
    
    # 2. 用户登录
    if result["success"]:
        login_result = client.login(
            email="test@example.com",
            password="SecurePassword123!"
        )
        print(f"登录结果：{login_result}")
        
        # 3. 获取用户信息
        if login_result["success"]:
            user_info = client.get_user_info(result["user_id"])
            print(f"用户信息：{user_info}")

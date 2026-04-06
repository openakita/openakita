"""
JWT 认证示例代码
功能：用户登录、Token 生成、Token 验证、Token 刷新
"""

import jwt
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel
from dotenv import load_dotenv
import os

load_dotenv()

# 配置
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "default-secret-key")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRATION_MINUTES = int(os.getenv("JWT_EXPIRATION_MINUTES", "30"))


class TokenPayload(BaseModel):
    """Token 载荷"""
    user_id: str
    username: str
    email: Optional[str] = None
    roles: list[str] = []


class TokenResponse(BaseModel):
    """Token 响应"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def hash_password(password: str) -> str:
    """密码哈希（示例使用 SHA256，生产环境请用 bcrypt）"""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    """验证密码"""
    return hash_password(password) == hashed


def create_access_token(payload: TokenPayload, expires_delta: Optional[timedelta] = None) -> str:
    """
    创建访问 Token
    
    Args:
        payload: Token 载荷
        expires_delta: 过期时间增量
    
    Returns:
        JWT Token 字符串
    """
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRATION_MINUTES)
    
    to_encode = {
        "sub": payload.user_id,
        "username": payload.username,
        "email": payload.email,
        "roles": payload.roles,
        "exp": expire,
        "iat": datetime.utcnow(),
        "type": "access"
    }
    
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def create_refresh_token(user_id: str) -> str:
    """
    创建刷新 Token（有效期更长）
    
    Args:
        user_id: 用户 ID
    
    Returns:
        JWT Refresh Token 字符串
    """
    expire = datetime.utcnow() + timedelta(days=7)
    
    to_encode = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.utcnow(),
        "type": "refresh"
    }
    
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> Optional[TokenPayload]:
    """
    验证 Token
    
    Args:
        token: JWT Token 字符串
    
    Returns:
        TokenPayload 如果验证成功，否则 None
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        
        # 检查 Token 类型
        token_type = payload.get("type")
        if token_type != "access":
            return None
        
        return TokenPayload(
            user_id=payload["sub"],
            username=payload["username"],
            email=payload.get("email"),
            roles=payload.get("roles", [])
        )
    except jwt.ExpiredSignatureError:
        print("Token 已过期")
        return None
    except jwt.InvalidTokenError as e:
        print(f"Token 无效：{e}")
        return None


def refresh_access_token(refresh_token: str) -> Optional[str]:
    """
    使用刷新 Token 获取新的访问 Token
    
    Args:
        refresh_token: Refresh Token 字符串
    
    Returns:
        新的 Access Token，如果失败返回 None
    """
    try:
        payload = jwt.decode(refresh_token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        
        # 检查 Token 类型
        if payload.get("type") != "refresh":
            return None
        
        user_id = payload["sub"]
        
        # 这里应该从数据库获取用户信息
        # 示例中直接创建新 Token
        new_payload = TokenPayload(
            user_id=user_id,
            username="user",  # 实际应从数据库获取
            roles=[]
        )
        
        return create_access_token(new_payload)
    except jwt.ExpiredSignatureError:
        print("Refresh Token 已过期")
        return None
    except jwt.InvalidTokenError:
        print("Refresh Token 无效")
        return None


# ============ 使用示例 ============

def example_login():
    """模拟用户登录流程"""
    print("=== JWT 登录示例 ===\n")
    
    # 1. 模拟用户登录（实际应从数据库验证）
    username = "testuser"
    password = "password123"
    stored_hash = hash_password(password)
    
    # 验证密码
    if not verify_password(password, stored_hash):
        print("密码错误")
        return
    
    print(f"用户 {username} 登录成功")
    
    # 2. 创建 Token
    payload = TokenPayload(
        user_id="123456",
        username=username,
        email="test@example.com",
        roles=["user", "admin"]
    )
    
    access_token = create_access_token(payload)
    refresh_token = create_refresh_token(payload.user_id)
    
    print(f"Access Token: {access_token[:50]}...")
    print(f"Refresh Token: {refresh_token[:50]}...\n")
    
    # 3. 验证 Token
    verified_payload = verify_token(access_token)
    if verified_payload:
        print(f"Token 验证成功:")
        print(f"  用户 ID: {verified_payload.user_id}")
        print(f"  用户名：{verified_payload.username}")
        print(f"  邮箱：{verified_payload.email}")
        print(f"  角色：{verified_payload.roles}\n")
    
    # 4. 刷新 Token
    new_access_token = refresh_access_token(refresh_token)
    if new_access_token:
        print(f"Token 刷新成功: {new_access_token[:50]}...\n")
    
    return access_token, refresh_token


def example_protected_route(token: str):
    """模拟受保护的路由"""
    print("=== 受保护的路由示例 ===\n")
    
    payload = verify_token(token)
    if not payload:
        print("访问拒绝：Token 无效或已过期")
        return
    
    print(f"欢迎，{payload.username}！")
    print(f"您的角色：{', '.join(payload.roles)}")
    print("访问资源成功！\n")


if __name__ == "__main__":
    # 运行示例
    access_token, refresh_token = example_login()
    example_protected_route(access_token)
    
    # 模拟 Token 过期场景
    print("=== Token 过期示例 ===\n")
    expired_payload = TokenPayload(
        user_id="123456",
        username="testuser",
        roles=["user"]
    )
    expired_token = create_access_token(expired_payload, expires_delta=timedelta(seconds=-1))
    result = verify_token(expired_token)
    print(f"过期 Token 验证结果：{result}\n")

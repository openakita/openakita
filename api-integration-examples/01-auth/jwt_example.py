"""
用户认证 API 示例 - JWT (JSON Web Token)
功能：生成、验证、刷新 JWT Token
"""
import jwt
import datetime
from typing import Optional, Dict
from functools import wraps

# 配置
SECRET_KEY = "your-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


class JWTAuth:
    """JWT 认证工具类"""
    
    @staticmethod
    def create_access_token(data: Dict, expires_delta: Optional[datetime.timedelta] = None) -> str:
        """
        创建访问令牌
        
        Args:
            data: 载荷数据 (如 user_id, username 等)
            expires_delta: 过期时间增量
            
        Returns:
            JWT token 字符串
        """
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.datetime.utcnow() + expires_delta
        else:
            expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": expire, "type": "access"})
        
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt
    
    @staticmethod
    def create_refresh_token(data: Dict) -> str:
        """
        创建刷新令牌
        
        Args:
            data: 载荷数据
            
        Returns:
            JWT refresh token 字符串
        """
        to_encode = data.copy()
        expire = datetime.datetime.utcnow() + datetime.timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        to_encode.update({"exp": expire, "type": "refresh"})
        
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt
    
    @staticmethod
    def verify_token(token: str, token_type: str = "access") -> Optional[Dict]:
        """
        验证令牌
        
        Args:
            token: JWT token 字符串
            token_type: 令牌类型 (access/refresh)
            
        Returns:
            解析后的载荷数据，验证失败返回 None
        """
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            
            # 检查令牌类型
            if payload.get("type") != token_type:
                return None
            
            # 检查是否过期 (jwt.decode 已自动检查 exp)
            return payload
            
        except jwt.ExpiredSignatureError:
            print("Token 已过期")
            return None
        except jwt.InvalidTokenError as e:
            print(f"Token 无效：{e}")
            return None
    
    @staticmethod
    def refresh_access_token(refresh_token: str) -> Optional[str]:
        """
        使用刷新令牌获取新的访问令牌
        
        Args:
            refresh_token: 刷新令牌
            
        Returns:
            新的访问令牌，失败返回 None
        """
        payload = JWTAuth.verify_token(refresh_token, token_type="refresh")
        
        if not payload:
            return None
        
        # 创建新的访问令牌 (不包含 refresh 相关字段)
        user_data = {
            "user_id": payload.get("user_id"),
            "username": payload.get("username")
        }
        
        return JWTAuth.create_access_token(user_data)


# ==================== 使用示例 ====================

def demo_jwt_usage():
    """JWT 使用示例"""
    print("=" * 50)
    print("JWT 认证示例")
    print("=" * 50)
    
    # 1. 用户登录，创建 token
    user_data = {
        "user_id": 12345,
        "username": "test_user",
        "email": "test@example.com"
    }
    
    access_token = JWTAuth.create_access_token(user_data)
    refresh_token = JWTAuth.create_refresh_token(user_data)
    
    print(f"\n1. 创建 Token:")
    print(f"   Access Token: {access_token[:50]}...")
    print(f"   Refresh Token: {refresh_token[:50]}...")
    
    # 2. 验证访问令牌
    print(f"\n2. 验证 Access Token:")
    payload = JWTAuth.verify_token(access_token)
    if payload:
        print(f"   ✓ 验证成功")
        print(f"   用户 ID: {payload.get('user_id')}")
        print(f"   用户名：{payload.get('username')}")
        print(f"   过期时间：{datetime.datetime.fromtimestamp(payload.get('exp'))}")
    else:
        print(f"   ✗ 验证失败")
    
    # 3. 使用刷新令牌获取新的访问令牌
    print(f"\n3. 刷新 Access Token:")
    new_access_token = JWTAuth.refresh_access_token(refresh_token)
    if new_access_token:
        print(f"   ✓ 刷新成功")
        print(f"   新 Token: {new_access_token[:50]}...")
    else:
        print(f"   ✗ 刷新失败")
    
    # 4. 验证过期 token
    print(f"\n4. 验证过期 Token:")
    expired_token = JWTAuth.create_access_token(
        user_data, 
        expires_delta=datetime.timedelta(seconds=-1)  # 已过期
    )
    payload = JWTAuth.verify_token(expired_token)
    if not payload:
        print(f"   ✓ 正确识别过期 Token")
    
    print("\n" + "=" * 50)


# ==================== FastAPI 集成示例 ====================

def jwt_dependency(token: str):
    """
    FastAPI 依赖注入示例
    用于保护 API 端点
    """
    payload = JWTAuth.verify_token(token)
    if not payload:
        raise Exception("Invalid or expired token")
    return payload


if __name__ == "__main__":
    demo_jwt_usage()

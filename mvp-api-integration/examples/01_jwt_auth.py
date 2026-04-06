"""
API 集成示例 01: JWT 用户认证

功能：
- 生成 JWT Token
- 验证 JWT Token
- Token 刷新机制

依赖：
pip install PyJWT cryptography
"""

import jwt
import datetime
from functools import wraps

# ============ 配置区域 ============
JWT_SECRET_KEY = "your-secret-key-change-in-production"
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRATION_HOURS = 24
REFRESH_TOKEN_EXPIRATION_DAYS = 7
# =================================


def generate_token(user_id, username, extra_claims=None):
    """
    生成 JWT Access Token
    
    Args:
        user_id: 用户 ID
        username: 用户名
        extra_claims: 额外的声明信息
    
    Returns:
        str: JWT Token
    """
    payload = {
        'user_id': user_id,
        'username': username,
        'iat': datetime.datetime.utcnow(),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=TOKEN_EXPIRATION_HOURS),
        'type': 'access'
    }
    
    if extra_claims:
        payload.update(extra_claims)
    
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token


def generate_refresh_token(user_id):
    """
    生成刷新 Token
    
    Args:
        user_id: 用户 ID
    
    Returns:
        str: Refresh Token
    """
    payload = {
        'user_id': user_id,
        'iat': datetime.datetime.utcnow(),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(days=REFRESH_TOKEN_EXPIRATION_DAYS),
        'type': 'refresh'
    }
    
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token


def verify_token(token, token_type='access'):
    """
    验证 JWT Token
    
    Args:
        token: JWT Token 字符串
        token_type: Token 类型 ('access' 或 'refresh')
    
    Returns:
        dict: 解析后的 payload，如果验证失败返回 None
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        
        # 验证 Token 类型
        if payload.get('type') != token_type:
            return None
        
        return payload
    except jwt.ExpiredSignatureError:
        print("❌ Token 已过期")
        return None
    except jwt.InvalidTokenError as e:
        print(f"❌ Token 无效：{e}")
        return None


def refresh_access_token(refresh_token):
    """
    使用刷新 Token 获取新的 Access Token
    
    Args:
        refresh_token: 刷新 Token
    
    Returns:
        str: 新的 Access Token，如果失败返回 None
    """
    payload = verify_token(refresh_token, token_type='refresh')
    
    if payload:
        # 生成新的 Access Token
        new_token = generate_token(
            user_id=payload['user_id'],
            username=payload.get('username', 'user')
        )
        return new_token
    
    return None


# Flask 装饰器示例
def token_required(f):
    """
    Flask 路由装饰器：要求有效的 JWT Token
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import request, jsonify
        
        token = None
        
        # 从 Authorization header 获取 token
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        if not token:
            return jsonify({'error': 'Token 缺失'}), 401
        
        payload = verify_token(token)
        if not payload:
            return jsonify({'error': 'Token 无效或已过期'}), 401
        
        # 将用户信息注入到请求上下文
        request.current_user = payload
        return f(*args, **kwargs)
    
    return decorated


# ============ 使用示例 ============
if __name__ == "__main__":
    print("=" * 50)
    print("JWT 认证 API 集成示例")
    print("=" * 50)
    
    # 1. 用户登录，生成 Token
    print("\n1️⃣  用户登录，生成 Token")
    user_id = 12345
    username = "test_user"
    
    access_token = generate_token(user_id, username, {'role': 'admin'})
    refresh_token = generate_refresh_token(user_id)
    
    print(f"Access Token: {access_token[:50]}...")
    print(f"Refresh Token: {refresh_token[:50]}...")
    
    # 2. 验证 Token
    print("\n2️⃣  验证 Access Token")
    payload = verify_token(access_token)
    if payload:
        print(f"✅ 验证成功 - 用户：{payload['username']}, ID: {payload['user_id']}")
    else:
        print("❌ 验证失败")
    
    # 3. 使用刷新 Token
    print("\n3️⃣  使用刷新 Token 获取新 Access Token")
    new_access_token = refresh_access_token(refresh_token)
    if new_access_token:
        print(f"✅ 刷新成功 - 新 Token: {new_access_token[:50]}...")
    else:
        print("❌ 刷新失败")
    
    # 4. 模拟 Token 过期
    print("\n4️⃣  模拟 Token 过期场景")
    expired_payload = {
        'user_id': user_id,
        'username': username,
        'iat': datetime.datetime.utcnow() - datetime.timedelta(hours=25),
        'exp': datetime.datetime.utcnow() - datetime.timedelta(hours=1),
        'type': 'access'
    }
    expired_token = jwt.encode(expired_payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    result = verify_token(expired_token)
    if not result:
        print("✅ 正确识别过期 Token")
    
    print("\n" + "=" * 50)
    print("示例执行完成")
    print("=" * 50)

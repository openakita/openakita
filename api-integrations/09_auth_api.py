"""
身份验证 API 集成示例
支持 JWT、OAuth2
"""

import jwt
import datetime
import hashlib
import secrets
from typing import Optional, Dict, Any
from functools import wraps


class JWTAuthAPI:
    """JWT 身份验证 API"""
    
    def __init__(self, secret_key: str, algorithm: str = 'HS256', 
                 token_expire_hours: int = 24, refresh_expire_days: int = 7):
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.token_expire_hours = token_expire_hours
        self.refresh_expire_days = refresh_expire_days
    
    def generate_token(self, user_id: str, username: str, extra_claims: Dict = None) -> str:
        """
        生成访问令牌
        
        Args:
            user_id: 用户 ID
            username: 用户名
            extra_claims: 额外声明
            
        Returns:
            str: JWT 令牌
        """
        now = datetime.datetime.utcnow()
        
        payload = {
            'user_id': user_id,
            'username': username,
            'iat': now,
            'exp': now + datetime.timedelta(hours=self.token_expire_hours),
            'type': 'access'
        }
        
        if extra_claims:
            payload.update(extra_claims)
        
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        print(f"✓ 访问令牌已生成（有效期：{self.token_expire_hours}小时）")
        return token
    
    def generate_refresh_token(self, user_id: str) -> str:
        """
        生成刷新令牌
        
        Args:
            user_id: 用户 ID
            
        Returns:
            str: 刷新令牌
        """
        now = datetime.datetime.utcnow()
        
        payload = {
            'user_id': user_id,
            'iat': now,
            'exp': now + datetime.timedelta(days=self.refresh_expire_days),
            'type': 'refresh',
            'jti': secrets.token_hex(16)  # 唯一标识
        }
        
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        print(f"✓ 刷新令牌已生成（有效期：{self.refresh_expire_days}天）")
        return token
    
    def verify_token(self, token: str) -> Optional[Dict]:
        """
        验证令牌
        
        Args:
            token: JWT 令牌
            
        Returns:
            dict: 解析后的 payload，验证失败返回 None
        """
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            print(f"✓ 令牌验证成功 - 用户：{payload.get('username')}")
            return payload
            
        except jwt.ExpiredSignatureError:
            print("✗ 令牌已过期")
            return None
        except jwt.InvalidTokenError as e:
            print(f"✗ 令牌无效：{e}")
            return None
    
    def refresh_access_token(self, refresh_token: str) -> Optional[str]:
        """
        使用刷新令牌获取新的访问令牌
        
        Args:
            refresh_token: 刷新令牌
            
        Returns:
            str: 新的访问令牌
        """
        payload = self.verify_token(refresh_token)
        
        if not payload or payload.get('type') != 'refresh':
            print("✗ 无效的刷新令牌")
            return None
        
        # 生成新的访问令牌
        new_token = self.generate_token(
            user_id=payload['user_id'],
            username=payload.get('username', '')
        )
        
        return new_token
    
    def token_required(self, f):
        """
        令牌验证装饰器
        
        使用示例:
        @app.route('/api/protected')
        @jwt_auth.token_required
        def protected_route():
            return "需要认证"
        """
        @wraps(f)
        def decorated(*args, **kwargs):
            from flask import request, jsonify
            
            token = request.headers.get('Authorization')
            
            if not token:
                return jsonify({'error': '缺少令牌'}), 401
            
            # 移除 "Bearer " 前缀
            if token.startswith('Bearer '):
                token = token[7:]
            
            payload = self.verify_token(token)
            
            if not payload:
                return jsonify({'error': '令牌无效或已过期'}), 401
            
            # 将用户信息注入到请求上下文
            request.current_user = payload
            return f(*args, **kwargs)
        
        return decorated


class OAuth2API:
    """OAuth2 身份验证 API（通用实现）"""
    
    def __init__(self, client_id: str, client_secret: str, 
                 authorize_url: str, token_url: str, 
                 redirect_uri: str, scope: str = ''):
        self.client_id = client_id
        self.client_secret = client_secret
        self.authorize_url = authorize_url
        self.token_url = token_url
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.access_token = None
        self.refresh_token = None
    
    def get_authorization_url(self, state: str = None) -> str:
        """
        获取授权 URL
        
        Args:
            state: 防 CSRF 攻击的随机状态值
            
        Returns:
            str: 授权 URL
        """
        import urllib.parse
        
        if not state:
            state = secrets.token_hex(16)
        
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': self.scope,
            'state': state
        }
        
        auth_url = f"{self.authorize_url}?{urllib.parse.urlencode(params)}"
        print(f"✓ 授权 URL 已生成")
        return auth_url
    
    def exchange_code(self, code: str) -> bool:
        """
        使用授权码换取访问令牌
        
        Args:
            code: 授权码
            
        Returns:
            bool: 是否成功
        """
        import requests
        
        try:
            response = requests.post(self.token_url, data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': self.redirect_uri,
                'client_id': self.client_id,
                'client_secret': self.client_secret
            })
            
            result = response.json()
            
            if 'access_token' in result:
                self.access_token = result['access_token']
                self.refresh_token = result.get('refresh_token')
                print(f"✓ 访问令牌已获取（有效期：{result.get('expires_in', '未知')}秒）")
                return True
            else:
                print(f"✗ 获取令牌失败：{result}")
                return False
                
        except Exception as e:
            print(f"✗ 请求异常：{e}")
            return False
    
    def refresh_token(self) -> bool:
        """使用刷新令牌刷新访问令牌"""
        import requests
        
        if not self.refresh_token:
            print("✗ 没有刷新令牌")
            return False
        
        try:
            response = requests.post(self.token_url, data={
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token,
                'client_id': self.client_id,
                'client_secret': self.client_secret
            })
            
            result = response.json()
            
            if 'access_token' in result:
                self.access_token = result['access_token']
                if 'refresh_token' in result:
                    self.refresh_token = result['refresh_token']
                print(f"✓ 访问令牌已刷新")
                return True
            else:
                print(f"✗ 刷新令牌失败：{result}")
                return False
                
        except Exception as e:
            print(f"✗ 请求异常：{e}")
            return False
    
    def get_user_info(self, userinfo_url: str) -> Optional[Dict]:
        """
        获取用户信息
        
        Args:
            userinfo_url: 用户信息接口 URL
            
        Returns:
            dict: 用户信息
        """
        import requests
        
        if not self.access_token:
            print("✗ 未登录")
            return None
        
        try:
            headers = {'Authorization': f'Bearer {self.access_token}'}
            response = requests.get(userinfo_url, headers=headers)
            
            user_info = response.json()
            print(f"✓ 用户信息已获取：{user_info.get('name', 'Unknown')}")
            return user_info
            
        except Exception as e:
            print(f"✗ 获取用户信息失败：{e}")
            return None


class PasswordHashAPI:
    """密码哈希 API"""
    
    @staticmethod
    def hash_password(password: str, salt: str = None) -> tuple:
        """
        哈希密码
        
        Args:
            password: 原始密码
            salt: 盐值（可选，不传则自动生成）
            
        Returns:
            tuple: (哈希值，盐值)
        """
        if not salt:
            salt = secrets.token_hex(16)
        
        # 使用 SHA-256 + 盐值
        salted_password = f"{salt}{password}".encode('utf-8')
        hashed = hashlib.sha256(salted_password).hexdigest()
        
        return hashed, salt
    
    @staticmethod
    def verify_password(password: str, hashed: str, salt: str) -> bool:
        """
        验证密码
        
        Args:
            password: 待验证密码
            hashed: 存储的哈希值
            salt: 盐值
            
        Returns:
            bool: 密码是否正确
        """
        computed_hash, _ = PasswordHashAPI.hash_password(password, salt)
        return computed_hash == hashed


# 使用示例
if __name__ == "__main__":
    # JWT 认证
    jwt_auth = JWTAuthAPI(secret_key="your-secret-key")
    
    # 生成令牌
    access_token = jwt_auth.generate_token(
        user_id="123",
        username="zacon",
        extra_claims={'role': 'admin'}
    )
    print(f"访问令牌：{access_token}")
    
    refresh_token = jwt_auth.generate_refresh_token(user_id="123")
    print(f"刷新令牌：{refresh_token}")
    
    # 验证令牌
    payload = jwt_auth.verify_token(access_token)
    print(f"令牌内容：{payload}")
    
    # 密码哈希
    password = "my_password123"
    hashed, salt = PasswordHashAPI.hash_password(password)
    print(f"哈希值：{hashed}")
    print(f"盐值：{salt}")
    
    # 验证密码
    is_valid = PasswordHashAPI.verify_password("my_password123", hashed, salt)
    print(f"密码验证：{'成功' if is_valid else '失败'}")

"""
API 集成示例 1: JWT 认证
"""
import jwt
import datetime

SECRET_KEY = "dev-secret-key"
ALGORITHM = "HS256"

def create_token(user_id: int, username: str) -> str:
    """创建 JWT Token"""
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str) -> dict:
    """验证 JWT Token"""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return {"error": "Token expired"}
    except jwt.InvalidTokenError:
        return {"error": "Invalid token"}

# 使用示例
if __name__ == "__main__":
    token = create_token(123, "test_user")
    print(f"Token: {token}")
    print(f"Verify: {verify_token(token)}")

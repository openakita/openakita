"""
JWT 认证集成示例
用于 MVP 用户系统的身份验证模块
"""
import jwt
import datetime
from functools import wraps
from flask import Flask, request, jsonify

# 配置
JWT_SECRET_KEY = "your-super-secret-key-change-in-production"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

app = Flask(__name__)


def generate_token(user_id: str, username: str, roles: list = None) -> str:
    """
    生成 JWT Token
    
    Args:
        user_id: 用户 ID
        username: 用户名
        roles: 用户角色列表
    
    Returns:
        JWT token 字符串
    """
    payload = {
        "user_id": user_id,
        "username": username,
        "roles": roles or ["user"],
        "iat": datetime.datetime.utcnow(),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRATION_HOURS)
    }
    
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token


def verify_token(token: str) -> dict:
    """
    验证 JWT Token
    
    Args:
        token: JWT token 字符串
    
    Returns:
        解析后的 payload 字典
    
    Raises:
        jwt.ExpiredSignatureError: Token 已过期
        jwt.InvalidTokenError: Token 无效
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise jwt.ExpiredSignatureError("Token 已过期，请重新登录")
    except jwt.InvalidTokenError as e:
        raise jwt.InvalidTokenError(f"Token 无效：{str(e)}")


def token_required(f):
    """
    Token 验证装饰器
    用于保护需要认证的 API 端点
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # 从 Authorization header 获取 token
        if "Authorization" in request.headers:
            auth_header = request.headers["Authorization"]
            try:
                token = auth_header.split(" ")[1]  # Bearer <token>
            except IndexError:
                return jsonify({"error": "无效的 Authorization header 格式"}), 401
        
        if not token:
            return jsonify({"error": "缺少认证 Token"}), 401
        
        try:
            current_user = verify_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token 已过期"}), 401
        except jwt.InvalidTokenError as e:
            return jsonify({"error": str(e)}), 401
        
        return f(current_user, *args, **kwargs)
    
    return decorated


# ============== API 示例 ==============

@app.route("/api/auth/login", methods=["POST"])
def login():
    """
    用户登录接口
    示例：POST /api/auth/login
    Body: {"username": "test", "password": "password123"}
    """
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")
    
    # TODO: 实际项目中这里应该查询数据库验证密码
    if username == "test" and password == "password123":
        user_id = "user_001"
        roles = ["admin", "user"]
        
        token = generate_token(user_id, username, roles)
        
        return jsonify({
            "success": True,
            "data": {
                "token": token,
                "expires_in": JWT_EXPIRATION_HOURS * 3600,
                "token_type": "Bearer"
            }
        }), 200
    else:
        return jsonify({"error": "用户名或密码错误"}), 401


@app.route("/api/auth/refresh", methods=["POST"])
@token_required
def refresh_token(current_user):
    """
    刷新 Token 接口
    示例：POST /api/auth/refresh
    Header: Authorization: Bearer <token>
    """
    new_token = generate_token(
        current_user["user_id"],
        current_user["username"],
        current_user.get("roles")
    )
    
    return jsonify({
        "success": True,
        "data": {
            "token": new_token,
            "expires_in": JWT_EXPIRATION_HOURS * 3600
        }
    })


@app.route("/api/profile", methods=["GET"])
@token_required
def get_profile(current_user):
    """
    获取用户信息（需要认证）
    示例：GET /api/profile
    Header: Authorization: Bearer <token>
    """
    return jsonify({
        "success": True,
        "data": {
            "user_id": current_user["user_id"],
            "username": current_user["username"],
            "roles": current_user.get("roles", [])
        }
    })


@app.route("/api/admin/users", methods=["GET"])
@token_required
def list_users(current_user):
    """
    管理员接口（需要 admin 角色）
    示例：GET /api/admin/users
    Header: Authorization: Bearer <token>
    """
    if "admin" not in current_user.get("roles", []):
        return jsonify({"error": "权限不足，需要 admin 角色"}), 403
    
    # TODO: 实际项目中这里应该查询数据库
    return jsonify({
        "success": True,
        "data": {
            "users": [
                {"user_id": "user_001", "username": "admin"},
                {"user_id": "user_002", "username": "user1"}
            ]
        }
    })


if __name__ == "__main__":
    # 测试示例
    print("=== JWT 认证示例 ===")
    
    # 生成 token
    token = generate_token("user_001", "test_user", ["admin", "user"])
    print(f"生成的 Token: {token}")
    
    # 验证 token
    try:
        payload = verify_token(token)
        print(f"验证成功，Payload: {payload}")
    except jwt.InvalidTokenError as e:
        print(f"验证失败：{e}")
    
    # 启动 Flask 服务（可选）
    # app.run(debug=True, port=5000)

# 用户认证 API 示例（JWT）
# 用于 MVP 用户登录认证

import jwt
import datetime
from functools import wraps
from flask import Flask, request, jsonify

app = Flask(__name__)
SECRET_KEY = "your-secret-key-here"  # 生产环境使用环境变量

def generate_token(user_id, username):
    """生成 JWT Token"""
    payload = {
        'user_id': user_id,
        'username': username,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24),
        'iat': datetime.datetime.utcnow()
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm='HS256')
    return token

def verify_token(token):
    """验证 JWT Token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def token_required(f):
    """装饰器：验证 Token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Token missing'}), 401
        
        # 移除 Bearer 前缀
        if token.startswith('Bearer '):
            token = token[7:]
        
        payload = verify_token(token)
        if not payload:
            return jsonify({'error': 'Token invalid'}), 401
        
        request.current_user = payload
        return f(*args, **kwargs)
    return decorated

@app.route('/api/login', methods=['POST'])
def login():
    """用户登录接口"""
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    # TODO: 验证用户名密码（从数据库查询）
    if username and password:
        # 模拟用户 ID
        user_id = 1
        token = generate_token(user_id, username)
        return jsonify({
            'token': token,
            'user_id': user_id,
            'username': username
        })
    
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/profile', methods=['GET'])
@token_required
def get_profile():
    """获取用户信息（需要认证）"""
    user = request.current_user
    return jsonify({
        'user_id': user['user_id'],
        'username': user['username']
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)

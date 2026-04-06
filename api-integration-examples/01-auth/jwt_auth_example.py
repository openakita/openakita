"""
API 集成示例 01: 用户认证 (JWT/OAuth2)
=====================================
功能：实现基于 JWT 的用户认证流程
依赖：pip install PyJWT fastapi python-jose
"""

import jwt
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

# 配置
SECRET_KEY = "your-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

app = FastAPI()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# 数据模型
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class User(BaseModel):
    username: str
    email: Optional[str] = None
    disabled: Optional[bool] = None

# 模拟数据库
fake_users_db = {
    "testuser": {
        "username": "testuser",
        "email": "test@example.com",
        "hashed_password": hashlib.sha256("password123".encode()).hexdigest(),
        "disabled": False,
    }
}

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """生成 JWT access token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password

def get_user(username: str):
    """从数据库获取用户"""
    if username in fake_users_db:
        user_dict = fake_users_db[username]
        return User(**user_dict)
    return None

def authenticate_user(username: str, password: str):
    """认证用户"""
    user = get_user(username)
    if not user:
        return False
    if not verify_password(password, user_dict["hashed_password"]):
        return False
    return user

async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """解析 token 获取当前用户"""
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except jwt.PyJWTError:
        raise credentials_exception
    user = get_user(username=token_data.username)
    if user is None:
        raise credentials_exception
    return user

# API 端点
@app.post("/token", response_model=Token)
async def login_for_access_token(username: str, password: str):
    """登录获取 access token"""
    user = authenticate_user(username, password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/users/me", response_model=User)
async def read_users_me(current_user: User = Depends(get_current_user)):
    """获取当前用户信息（需要认证）"""
    return current_user

@app.get("/protected-resource")
async def protected_resource(current_user: User = Depends(get_current_user)):
    """受保护的资源"""
    return {"message": f"Hello {current_user.username}, this is protected data"}

# 使用示例
if __name__ == "__main__":
    import uvicorn
    # 生成 token 示例
    token = create_access_token(data={"sub": "testuser"})
    print(f"Access Token: {token}")
    
    # 启动服务
    # uvicorn.run(app, host="0.0.0.0", port=8000)

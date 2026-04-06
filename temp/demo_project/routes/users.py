from fastapi import APIRouter, HTTPException, status, Depends
from typing import List
from sqlalchemy.orm import Session
from datetime import timedelta
from models import User, UserCreate, UserUpdate, UserListResponse, MessageResponse, UserDB
from main import get_db
from auth import (
    get_password_hash, verify_password, create_access_token, 
    get_current_active_user, ACCESS_TOKEN_EXPIRE_MINUTES
)

router = APIRouter()


@router.post("/", response_model=User, status_code=status.HTTP_201_CREATED)
async def create_user(user: UserCreate, db: Session = Depends(get_db)):
    """创建新用户"""
    # 检查用户名和邮箱是否已存在
    if db.query(UserDB).filter(UserDB.username == user.username).first():
        raise HTTPException(status_code=400, detail="Username already registered")
    if db.query(UserDB).filter(UserDB.email == user.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # 哈希密码（使用passlib）
    hashed_password = get_password_hash(user.password)
    
    # 创建用户记录
    db_user = UserDB(
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        hashed_password=hashed_password,
        is_active=user.is_active
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    # 返回 Pydantic 模型
    return User(
        id=db_user.id,
        username=db_user.username,
        email=db_user.email,
        full_name=db_user.full_name,
        is_active=db_user.is_active,
        created_at=db_user.created_at,
        updated_at=db_user.updated_at
    )


@router.get("/", response_model=UserListResponse)
async def get_users(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_active_user)
):
    """获取用户列表（支持分页）"""
    total = db.query(UserDB).count()
    db_users = db.query(UserDB).offset(skip).limit(limit).all()
    
    users = [
        User(
            id=u.id, username=u.username, email=u.email,
            full_name=u.full_name, is_active=u.is_active,
            created_at=u.created_at, updated_at=u.updated_at
        ) for u in db_users
    ]
    return UserListResponse(users=users, total=total)


@router.get("/{user_id}", response_model=User)
async def get_user(
    user_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_active_user)
):
    """获取指定用户"""
    db_user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    return User(
        id=db_user.id, username=db_user.username, email=db_user.email,
        full_name=db_user.full_name, is_active=db_user.is_active,
        created_at=db_user.created_at, updated_at=db_user.updated_at
    )


@router.put("/{user_id}", response_model=User)
async def update_user(
    user_id: int, 
    user: UserUpdate, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_active_user)
):
    """更新用户信息"""
    db_user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    update_data = user.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_user, field, value)
    
    db.commit()
    db.refresh(db_user)
    
    return User(
        id=db_user.id, username=db_user.username, email=db_user.email,
        full_name=db_user.full_name, is_active=db_user.is_active,
        created_at=db_user.created_at, updated_at=db_user.updated_at
    )


@router.delete("/{user_id}", response_model=MessageResponse)
async def delete_user(
    user_id: int, 
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_active_user)
):
    """删除用户"""
    db_user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    db.delete(db_user)
    db.commit()
    return MessageResponse(message=f"User {user_id} deleted successfully")


@router.post("/login")
async def login(username: str, password: str, db: Session = Depends(get_db)):
    """用户登录获取JWT token"""
    # 查找用户
    user = db.query(UserDB).filter(UserDB.username == username).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # 验证密码
    if not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # 检查用户是否活跃
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    
    # 创建access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username
    }


@router.get("/me", response_model=User)
async def read_users_me(current_user: UserDB = Depends(get_current_active_user)):
    """获取当前登录用户信息"""
    return User(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
        updated_at=current_user.updated_at
    )

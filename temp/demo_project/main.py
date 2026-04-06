from fastapi import FastAPI
from routes import users, tasks
from models import Base
from database import engine
import os

app = FastAPI(title="User & Task Management API", version="1.0.0")

# JWT 配置
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-here-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


# 启动时创建数据库表
@app.on_event("startup")
async def startup_event():
    Base.metadata.create_all(bind=engine)


# 注册路由
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])


@app.get("/")
async def root():
    return {"message": "Welcome to User & Task Management API"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# MLflow 本地部署配置（无 Docker 方案）

## 环境准备

```bash
# 安装依赖
pip install mlflow psycopg2-binary sqlalchemy
```

## 启动 MLflow 服务

### 方案 A：SQLite 快速启动（开发测试）

```bash
# 创建数据目录
mkdir mlflow-data

# 启动 MLflow 服务（SQLite 后端）
mlflow server --host 0.0.0.0 --port 5000 --backend-store-uri sqlite:///mlflow-data/mlflow.db --default-artifact-root ./mlflow-data/artifacts
```

### 方案 B：PostgreSQL 生产配置

```bash
# 确保 PostgreSQL 运行在 localhost:5432
# 数据库：mlflow_db, 用户：mlflow, 密码：mlflow123

mlflow server --host 0.0.0.0 --port 5000 --backend-store-uri postgresql://mlflow:mlflow123@localhost:5432/mlflow_db --default-artifact-root ./mlflow-data/artifacts
```

## 访问界面

- **Tracking UI**: http://localhost:5000
- **API Endpoint**: http://localhost:5000/api/2.0/mlflow

## 环境变量配置

```bash
# Windows PowerShell
$env:MLFLOW_TRACKING_URI="http://localhost:5000"

# Linux/Mac
export MLFLOW_TRACKING_URI="http://localhost:5000"
```

## 验证服务状态

```bash
# 检查服务是否运行
curl http://localhost:5000/health

# 查看 API 版本
curl http://localhost:5000/api/2.0/mlflow/get-version
```

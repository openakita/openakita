# MLflow 服务启动脚本
$env:MLFLOW_TRACKING_URI = "http://localhost:5000"

Write-Host "正在启动 MLflow 服务..."
Write-Host "后端存储：sqlite:///mlflow-data/mlflow.db"
Write-Host "访问地址：http://localhost:5000"

mlflow server `
    --host 0.0.0.0 `
    --port 5000 `
    --backend-store-uri "sqlite:///mlflow-data/mlflow.db" `
    --default-artifact-root "./mlflow-data/artifacts"

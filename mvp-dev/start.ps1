# MVP 开发环境启动脚本
# Windows PowerShell 版本

Write-Host "🚀 开始部署 MVP 开发环境..." -ForegroundColor Green

# 检查 Docker
Write-Host "`n📦 检查 Docker..." -ForegroundColor Cyan
docker --version
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Docker 未安装或未启动" -ForegroundColor Red
    exit 1
}
Write-Host "✅ Docker 检查通过" -ForegroundColor Green

# 创建数据目录
Write-Host "`n📁 创建数据目录..." -ForegroundColor Cyan
$directories = @(
    "data\qdrant_storage",
    "data\redis_data",
    "data\mlflow",
    "data\mlflow\artifacts"
)

foreach ($dir in $directories) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "  创建：$dir" -ForegroundColor Gray
    }
}
Write-Host "✅ 目录创建完成" -ForegroundColor Green

# 启动 Docker 服务
Write-Host "`n🐳 启动 Docker 服务 (Qdrant + Redis + MLflow)..." -ForegroundColor Cyan
docker-compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Docker 服务启动失败" -ForegroundColor Red
    exit 1
}
Write-Host "✅ Docker 服务启动成功" -ForegroundColor Green

# 等待服务就绪
Write-Host "`n⏳ 等待服务就绪..." -ForegroundColor Cyan
Start-Sleep -Seconds 10

# 检查服务状态
Write-Host "`n📊 检查服务状态..." -ForegroundColor Cyan
docker-compose ps

# 安装 Python 依赖
Write-Host "`n📦 安装 Python 依赖..." -ForegroundColor Cyan
if (Test-Path "..\.venv\Scripts\Activate.ps1") {
    Write-Host "激活虚拟环境..." -ForegroundColor Gray
    & "..\.venv\Scripts\Activate.ps1"
}

pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠️  Python 依赖安装失败，请手动执行：pip install -r requirements.txt" -ForegroundColor Yellow
} else {
    Write-Host "✅ Python 依赖安装完成" -ForegroundColor Green
}

# 验证服务
Write-Host "`n🔍 验证服务..." -ForegroundColor Cyan

# 验证 Qdrant
Write-Host "  检查 Qdrant (http://localhost:6333)..." -ForegroundColor Gray
try {
    $response = Invoke-WebRequest -Uri "http://localhost:6333/" -TimeoutSec 5 -UseBasicParsing
    if ($response.StatusCode -eq 200) {
        Write-Host "  ✅ Qdrant 运行正常" -ForegroundColor Green
    }
} catch {
    Write-Host "  ⚠️  Qdrant 未响应" -ForegroundColor Yellow
}

# 验证 Redis
Write-Host "  检查 Redis (localhost:6379)..." -ForegroundColor Gray
try {
    $redisTest = Test-NetConnection -ComputerName localhost -Port 6379 -WarningAction SilentlyContinue
    if ($redisTest.TcpTestSucceeded) {
        Write-Host "  ✅ Redis 运行正常" -ForegroundColor Green
    }
} catch {
    Write-Host "  ⚠️  Redis 未响应" -ForegroundColor Yellow
}

# 验证 MLflow
Write-Host "  检查 MLflow (http://localhost:5000)..." -ForegroundColor Gray
try {
    $response = Invoke-WebRequest -Uri "http://localhost:5000/" -TimeoutSec 5 -UseBasicParsing
    if ($response.StatusCode -eq 200) {
        Write-Host "  ✅ MLflow 运行正常" -ForegroundColor Green
    }
} catch {
    Write-Host "  ⚠️  MLflow 未响应" -ForegroundColor Yellow
}

Write-Host "`n🎉 MVP 开发环境部署完成！" -ForegroundColor Green
Write-Host "`n📝 访问地址:" -ForegroundColor Cyan
Write-Host "  - Qdrant:   http://localhost:6333" -ForegroundColor White
Write-Host "  - Redis:    localhost:6379" -ForegroundColor White
Write-Host "  - MLflow:   http://localhost:5000" -ForegroundColor White
Write-Host "  - Flower:   http://localhost:5555 (启动 Celery 后)" -ForegroundColor White

Write-Host "`n🚀 启动 Celery Worker:" -ForegroundColor Cyan
Write-Host "  celery -A celery_config worker --loglevel=info -Q high_priority,default,low_priority" -ForegroundColor White

Write-Host "`n📚 查看文档：README.md" -ForegroundColor Cyan

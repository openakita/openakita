# MVP 开发环境配置清单

**版本**: V1.0  
**编制人**: DevOps 工程师  
**编制时间**: 2026-03-11  
**适用阶段**: MVP 开发期（03-12 ~ 06-01）  
**预算周期**: 6 个月  

---

## 一、服务器资源配置

### 1.1 云资源总览

| 资源类型 | 配置 | 数量 | 月费用 | 6 个月合计 | 用途 |
|----------|------|------|--------|------------|------|
| **应用服务器 ECS** | 4 核 8G, 100G SSD | 2 台 | 8,000 元 | 48,000 元 | 应用服务 + 备份 |
| **云数据库 RDS** | MySQL 高可用版，4 核 16G | 1 套 | 4,000 元 | 24,000 元 | 主从架构，业务数据 |
| **向量数据库** | Qdrant 自托管，4 核 8G | 1 台 | 2,000 元 | 12,000 元 | 向量检索，嵌入存储 |
| **缓存数据库** | Redis 6.0, 2 核 4G | 1 套 | 1,000 元 | 6,000 元 | 会话缓存，队列 Broker |
| **对象存储 OSS** | 500GB+ 流量包 | 1 套 | 1,000 元 | 6,000 元 | 文件存储，静态资源 |
| **CDN 加速** | 100GB/月流量包 | 1 套 | 1,000 元 | 6,000 元 | 静态资源加速 |
| **负载均衡 SLB** | 性能保障型 | 1 套 | 500 元 | 3,000 元 | 流量分发，高可用 |
| **监控告警** | 云监控基础版 | 1 套 | 500 元 | 3,000 元 | 基础监控 |
| **备份服务** | 每日自动备份 | 1 套 | 500 元 | 3,000 元 | 数据安全保障 |
| **合计** | - | - | **18,500 元/月** | **111,000 元** | - |

### 1.2 应用服务器详细配置

#### 生产环境（2 台）

**ECS-APP-01（主）**
```yaml
实例规格：ecs.g6.xlarge
CPU: 4 核
内存：8GB
系统盘：100GB ESSD PL0
操作系统：Ubuntu 22.04 LTS
网络：VPC 专有网络，带宽 5Mbps
安全组：
  - 80/443: 公开（HTTP/HTTPS）
  - 22: 仅堡垒机 IP
  - 8000-9000: 内网（服务间通信）
部署服务:
  - Nginx（反向代理）
  - Gunicorn/Uvicorn（应用服务）
  - Celery Worker（异步任务）
```

**ECS-APP-02（备）**
```yaml
配置：同 ECS-APP-01
部署服务:
  - Nginx（反向代理）
  - Gunicorn/Uvicorn（应用服务）
  - Celery Worker（异步任务）
  - Prometheus Node Exporter（监控）
```

#### 跳板机（可选，后期添加）

**ECS-Bastion**
```yaml
实例规格：ecs.t6-c1m2.large
CPU: 2 核
内存：2GB
系统盘：40GB SSD
用途：运维入口，SSH 跳转
安全策略：仅允许公司 IP 访问 22 端口
```

### 1.3 网络架构

```
                    ┌─────────────────┐
                    │   负载均衡 SLB   │
                    │  (公网 IP)       │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
    ┌─────────▼─────────┐       ┌──────────▼──────────┐
    │   ECS-APP-01      │       │    ECS-APP-02       │
    │   (主应用节点)     │       │    (备应用节点)      │
    │   - Nginx         │       │    - Nginx          │
    │   - App Server    │       │    - App Server     │
    │   - Celery Worker │       │    - Celery Worker  │
    └─────────┬─────────┘       └──────────┬──────────┘
              │                             │
              └──────────────┬──────────────┘
                             │
              ┌──────────────┴──────────────────────────────┐
              │              VPC 内网                        │
              │                                             │
    ┌─────────▼─────────┐       ┌──────────▼──────────┐
    │   RDS MySQL       │       │    Qdrant           │
    │   (高可用版主从)   │       │    (向量数据库)      │
    └───────────────────┘       └─────────────────────┘
    
    ┌─────────▼─────────┐       ┌──────────▼──────────┐
    │   Redis           │       │    OSS              │
    │   (缓存 + 队列)    │       │    (对象存储)        │
    └───────────────────┘       └─────────────────────┘
```

---

## 二、数据库选型与配置

### 2.1 关系型数据库：PostgreSQL（推荐）/MySQL

**选型理由**:
- ✅ PostgreSQL 支持 JSONB 字段，适合半结构化数据
- ✅ 支持向量插件 pgvector，可替代部分 Qdrant 功能
- ✅ 开源免费，社区活跃
- ✅ 兼容性好，迁移成本低

**配置方案**:

```yaml
数据库类型：PostgreSQL 15
实例规格：4 核 16GB，500GB SSD 存储
高可用：主从架构，自动故障切换
备份策略：
  - 每日全量备份（保留 30 天）
  - WAL 日志实时备份（支持 PITR）
连接池：PgBouncer（最大连接数 200）
监控指标:
  - QPS/TPS
  - 连接数使用率
  - 慢查询数量
  - 主从延迟
  - 磁盘使用率
```

**初始化脚本**:
```sql
-- 创建数据库
CREATE DATABASE mvp_prod WITH ENCODING 'UTF8';

-- 创建用户
CREATE USER mvp_app WITH PASSWORD 'secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE mvp_prod TO mvp_app;

-- 启用向量扩展（如使用 pgvector）
CREATE EXTENSION IF NOT EXISTS vector;

-- 创建核心表
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE workflows (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    user_id INTEGER REFERENCES users(id),
    config JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_workflows_user_id ON workflows(user_id);
CREATE INDEX idx_workflows_config ON workflows USING GIN(config);
```

### 2.2 向量数据库：Qdrant

**选型理由**（基于技术部调研结论）:
- ✅ Rust 实现，性能优异（查询延迟~8ms）
- ✅ 部署简单，单二进制文件或 Docker
- ✅ 支持自托管 + 托管服务，扩展性好
- ✅ API 友好，Python SDK 完善
- ✅ 成本可控（自托管免费，托管$30/月起）

**配置方案**:

```yaml
部署方式：Docker 容器化部署
实例规格：4 核 8GB，100GB SSD
版本：Qdrant 1.7+
持久化：挂载云盘数据卷
备份策略：定期快照导出至 OSS
监控指标:
  - 向量数量
  - 查询延迟（P95/P99）
  - 内存使用率
  - 磁盘使用率
```

**Docker Compose 配置**:
```yaml
version: '3.8'

services:
  qdrant:
    image: qdrant/qdrant:v1.7.0
    container_name: qdrant
    ports:
      - "6333:6333"  # REST API
      - "6334:6334"  # gRPC
    volumes:
      - ./qdrant_storage:/qdrant/storage
      - ./qdrant_snapshots:/qdrant/snapshots
    environment:
      - QDRANT__SERVICE__GRPC_PORT=6334
      - QDRANT__LOG_LEVEL=INFO
    restart: unless-stopped
    networks:
      - mvp_network

networks:
  mvp_network:
    driver: bridge
```

**初始化 Collection**:
```python
from qdrant_client import QdrantClient
from qdrant_client.http import models

client = QdrantClient(host="localhost", port=6333)

# 创建工作流向量集合
client.create_collection(
    collection_name="workflows",
    vectors_config=models.VectorParams(
        size=1536,  # OpenAI/文心一言 embedding 维度
        distance=models.Distance.COSINE
    ),
    hnsw_config=models.HnswConfigDiff(
        m=16,
        ef_construct=100
    )
)

# 创建知识库向量集合
client.create_collection(
    collection_name="knowledge_base",
    vectors_config=models.VectorParams(
        size=1536,
        distance=models.Distance.COSINE
    )
)
```

### 2.3 缓存与队列：Redis

**配置方案**:

```yaml
版本：Redis 6.0+
部署方式：云托管 Redis（主从版）
实例规格：2 核 4GB
持久化：RDB+AOF 混合
用途:
  - 会话缓存（Session）
  - Celery 任务队列 Broker
  - 热点数据缓存
监控指标:
  - 内存使用率
  - 连接数
  - QPS
  - 命中率
```

---

## 三、监控告警方案

### 3.1 技术栈选型

| 组件 | 选型 | 用途 |
|------|------|------|
| **指标采集** | Prometheus | 系统/应用指标收集 |
| **可视化** | Grafana | 监控面板展示 |
| **日志收集** | Loki + Promtail | 日志聚合查询 |
| **告警通知** | Alertmanager | 告警路由与通知 |
| **链路追踪** | Jaeger（可选） | 分布式追踪 |

### 3.2 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                     监控告警架构                         │
└─────────────────────────────────────────────────────────┘

┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Node        │    │  Node        │    │  Node        │
│  Exporter    │    │  Exporter    │    │  Exporter    │
│  (ECS-01)    │    │  (ECS-02)    │    │  (DB)        │
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                  ┌────────▼────────┐
                  │   Prometheus    │
                  │   (指标收集)     │
                  └────────┬────────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
┌────────▼────────┐ ┌──────▼──────┐ ┌───────▼───────┐
│    Grafana      │ │Alertmanager │ │    Loki       │
│   (可视化)       │ │  (告警)      │ │   (日志)      │
└─────────────────┘ └──────┬──────┘ └───────────────┘
                           │
                  ┌────────▼────────┐
                  │  通知渠道        │
                  │  - 钉钉/飞书     │
                  │  - 邮件          │
                  │  - 短信（紧急）   │
                  └─────────────────┘
```

### 3.3 Prometheus 配置

**prometheus.yml**:
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093

rule_files:
  - "alert_rules.yml"

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'node'
    static_configs:
      - targets: ['ecs-app-01:9100', 'ecs-app-02:9100']

  - job_name: 'postgresql'
    static_configs:
      - targets: ['postgres-exporter:9187']

  - job_name: 'redis'
    static_configs:
      - targets: ['redis-exporter:9121']

  - job_name: 'qdrant'
    static_configs:
      - targets: ['qdrant:6333']

  - job_name: 'application'
    static_configs:
      - targets: ['ecs-app-01:8000', 'ecs-app-02:8000']
    metrics_path: '/metrics'
```

**alert_rules.yml**:
```yaml
groups:
  - name: infrastructure
    rules:
      - alert: HighCPUUsage
        expr: 100 - (avg by(instance) (irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "高 CPU 使用率"
          description: "{{ $labels.instance }} CPU 使用率超过 80% 持续 5 分钟"

      - alert: HighMemoryUsage
        expr: (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100 > 85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "高内存使用率"
          description: "{{ $labels.instance }} 内存使用率超过 85%"

      - alert: DiskSpaceLow
        expr: (node_filesystem_avail_bytes / node_filesystem_size_bytes) * 100 < 15
        for: 10m
        labels:
          severity: critical
        annotations:
          summary: "磁盘空间不足"
          description: "{{ $labels.instance }} 磁盘可用空间低于 15%"

  - name: application
    rules:
      - alert: ServiceDown
        expr: up{job="application"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "服务宕机"
          description: "{{ $labels.instance }} 应用服务不可用"

      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m]) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "高错误率"
          description: "应用错误率超过 5%"

      - alert: HighResponseTime
        expr: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 2
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "高响应时间"
          description: "P95 响应时间超过 2 秒"
```

### 3.4 Grafana 仪表板

**推荐导入的 Dashboard**:
- Node Exporter Full (ID: 1860)
- PostgreSQL Database (ID: 9628)
- Redis Dashboard (ID: 763)
- Qdrant Monitoring (自定义)
- Application Metrics (自定义)

**关键监控面板**:
1. **系统概览**: CPU/内存/磁盘/网络
2. **应用性能**: QPS/响应时间/错误率
3. **数据库监控**: 连接数/慢查询/主从延迟
4. **业务指标**: 用户数/工作流数/API 调用量

### 3.5 告警通知配置

**Alertmanager 配置**:
```yaml
global:
  resolve_timeout: 5m
  smtp_smarthost: 'smtp.example.com:587'
  smtp_from: 'alert@mvp-project.com'

route:
  group_by: ['alertname']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  receiver: 'default'
  routes:
    - match:
        severity: critical
      receiver: 'critical-alerts'

receivers:
  - name: 'default'
    email_configs:
      - to: 'dev-team@mvp-project.com'
        send_resolved: true

  - name: 'critical-alerts'
    dingtalk_configs:
      - webhook: 'https://oapi.dingtalk.com/robot/send?access_token=xxx'
        send_resolved: true
    email_configs:
      - to: 'cto@mvp-project.com'
        send_resolved: true
```

---

## 四、CI/CD 流水线设计

### 4.1 技术选型

| 组件 | 选型 | 用途 |
|------|------|------|
| **代码托管** | GitHub / GitLab | 版本控制 |
| **CI/CD 平台** | GitHub Actions | 自动化流水线 |
| **容器化** | Docker | 应用打包 |
| **镜像仓库** | Docker Hub / ACR | 镜像存储 |
| **部署工具** | Ansible / Kubernetes | 自动化部署 |

### 4.2 流水线架构

```
┌─────────────────────────────────────────────────────────────┐
│                      CI/CD 流水线                            │
└─────────────────────────────────────────────────────────────┘

代码提交 (git push)
       │
       ▼
┌──────────────────┐
│  1. 代码检查      │
│  - Lint (Ruff)   │
│  - Type Check    │
│  - Security Scan │
└────────┬─────────┘
         │ ✅
         ▼
┌──────────────────┐
│  2. 自动化测试    │
│  - Unit Tests    │
│  - Integration   │
│  - E2E Tests     │
└────────┬─────────┘
         │ ✅
         ▼
┌──────────────────┐
│  3. 构建镜像      │
│  - Docker Build  │
│  - Push to ACR   │
└────────┬─────────┘
         │ ✅
         ▼
┌──────────────────┐
│  4. 部署到测试    │
│  - Deploy Staging│
│  - Smoke Test    │
└────────┬─────────┘
         │ ✅
         ▼
┌──────────────────┐
│  5. 人工审批      │
│  - CTO/架构师     │
└────────┬─────────┘
         │ ✅
         ▼
┌──────────────────┐
│  6. 部署到生产    │
│  - Blue-Green    │
│  - Health Check  │
└──────────────────┘
```

### 4.3 GitHub Actions 工作流

**.github/workflows/ci.yml**:
```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

env:
  REGISTRY: registry.cn-hangzhou.aliyuncs.com
  IMAGE_NAME: mvp-project/app

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install ruff mypy pytest pytest-cov

      - name: Lint with Ruff
        run: ruff check src/

      - name: Type check with Mypy
        run: mypy src/openakita/

      - name: Run tests
        run: pytest tests/ --cov=src/openakita --cov-report=xml

      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage.xml

  build-and-push:
    needs: lint-and-test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main' || github.ref == 'refs/heads/develop'
    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Login to ACR
        uses: docker/login-action@v2
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ secrets.ACR_USERNAME }}
          password: ${{ secrets.ACR_PASSWORD }}

      - name: Build and push Docker image
        uses: docker/build-push-action@v4
        with:
          context: .
          push: true
          tags: |
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }}
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest

  deploy-staging:
    needs: build-and-push
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/develop'
    environment: staging
    steps:
      - name: Deploy to Staging
        uses: appleboy/ssh-action@master
        with:
          host: ${{ secrets.STAGING_HOST }}
          username: ${{ secrets.STAGING_USER }}
          key: ${{ secrets.STAGING_SSH_KEY }}
          script: |
            cd /opt/mvp-staging
            docker-compose pull
            docker-compose up -d

      - name: Health Check
        run: |
          curl -f https://staging.mvp-project.com/health || exit 1

  deploy-production:
    needs: build-and-push
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    environment: production
    steps:
      - name: Deploy to Production (Blue-Green)
        uses: appleboy/ssh-action@master
        with:
          host: ${{ secrets.PROD_HOST }}
          username: ${{ secrets.PROD_USER }}
          key: ${{ secrets.PROD_SSH_KEY }}
          script: |
            cd /opt/mvp-prod
            # Blue-Green 部署
            ./deploy.sh ${{ github.sha }}

      - name: Health Check
        run: |
          curl -f https://mvp-project.com/health || exit 1

      - name: Notify Success
        uses: 8398a7/action-slack@v3
        with:
          status: custom
          custom_payload: |
            {
              text: "✅ 生产环境部署成功\n版本：${{ github.sha }}\n时间：${{ github.event.head_commit.timestamp }}"
            }
```

### 4.4 Dockerfile

```dockerfile
# 多阶段构建
FROM python:3.11-slim as builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

FROM python:3.11-slim

WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . .

ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "src.openakita.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 4.5 部署脚本（deploy.sh）

```bash
#!/bin/bash
set -e

VERSION=$1
IMAGE="registry.cn-hangzhou.aliyuncs.com/mvp-project/app:${VERSION}"

echo "🚀 开始 Blue-Green 部署..."

# 拉取新镜像
docker pull $IMAGE

# 启动新容器（Green）
docker run -d --name app-green \
  --network mvp_network \
  -p 8001:8000 \
  --env-file .env \
  $IMAGE

# 健康检查
echo "⏳ 等待服务启动..."
sleep 10

for i in {1..10}; do
  if curl -f http://localhost:8001/health; then
    echo "✅ Green 环境健康检查通过"
    break
  fi
  if [ $i -eq 10 ]; then
    echo "❌ Green 环境健康检查失败，回滚"
    docker stop app-green && docker rm app-green
    exit 1
  fi
  sleep 5
done

# 切换流量（更新 Nginx 配置）
echo "🔄 切换流量到 Green 环境..."
sed -i 's/8000/8001/g' /etc/nginx/conf.d/mvp.conf
nginx -s reload

# 停止旧容器（Blue）
echo "🛑 停止 Blue 环境..."
docker stop app-blue || true
docker rm app-blue || true

# 重命名容器
docker rename app-green app-blue

echo "✅ 部署完成！版本：$VERSION"
```

---

## 五、安全配置

### 5.1 网络安全

```yaml
安全组规则:
  入站:
    - 80/443: 0.0.0.0/0 (HTTP/HTTPS)
    - 22: 公司 IP 段 (SSH)
    - 3306/5432: 内网 IP 段 (数据库)
    - 6379: 内网 IP 段 (Redis)
    - 6333: 内网 IP 段 (Qdrant)
  出站:
    - 全部允许

VPC 配置:
  - 生产环境与测试环境隔离
  - 数据库部署在私有子网
  -  NAT 网关用于 outbound 流量
```

### 5.2 数据安全

```yaml
加密策略:
  - 传输加密：TLS 1.3 (HTTPS)
  - 存储加密：云盘加密 + 数据库 TDE
  - 密钥管理：阿里云 KMS

备份策略:
  - RDS：每日全量 +WAL 实时备份
  - OSS：版本控制 + 跨区域复制
  - 配置：Git 版本控制 + 加密存储
```

### 5.3 访问控制

```yaml
身份认证:
  - SSH：密钥认证，禁用密码
  - 数据库：最小权限原则
  - 应用：JWT+OAuth2.0

审计日志:
  - 操作日志：所有运维操作记录
  - 访问日志：Nginx+ 应用日志
  - 日志保留：180 天
```

---

## 六、成本优化建议

### 6.1 初期优化（03-04 月）

1. **使用预留实例券**: 1 年期预留 ECS，节省 30-40% 成本
2. **按量付费 + 抢占式实例**: 测试环境使用抢占式实例，节省 70%
3. **OSS 生命周期管理**: 自动转储冷数据至低频访问，节省 50%
4. **CDN 流量包**: 预付费流量包，比按量节省 20%

### 6.2 中期优化（05-06 月）

1. **自动伸缩**: 根据负载自动扩缩容，避免资源浪费
2. **容器化改造**: 提高资源利用率，单 ECS 部署多服务
3. **数据库读写分离**: 只读实例按需创建，降低主库压力

### 6.3 预期成本对比

| 阶段 | 月费用 | 优化措施 |
|------|--------|----------|
| 初始方案 | 18,500 元 | - |
| 预留实例后 | 13,000 元 | ECS/RDS 预留 1 年 |
| 容器化后 | 10,000 元 | 资源利用率提升 |
| **最终目标** | **10,000 元/月** | **节省 46%** |

---

## 七、实施时间表

| 阶段 | 时间 | 任务 | 负责人 |
|------|------|------|--------|
| **阶段一** | 03-12 ~ 03-15 | 云资源采购与网络搭建 | DevOps |
| **阶段二** | 03-16 ~ 03-20 | 数据库部署与初始化 | DevOps+ 全栈 A |
| **阶段三** | 03-21 ~ 03-25 | 监控告警系统搭建 | DevOps |
| **阶段四** | 03-26 ~ 03-30 | CI/CD 流水线配置 | DevOps+ 全栈 B |
| **阶段五** | 04-01 ~ 04-05 | 安全加固与压力测试 | 全员 |
| **验收** | 04-06 | 环境验收，交付开发团队 | CTO+DevOps |

---

## 八、验收标准

### 8.1 基础设施验收

- [ ] ECS 实例可正常 SSH 登录
- [ ] 负载均衡可正常分发流量
- [ ] 网络安全组规则符合设计
- [ ] VPC 网络连通性测试通过

### 8.2 数据库验收

- [ ] PostgreSQL 主从复制正常
- [ ] Qdrant 向量查询延迟<50ms
- [ ] Redis 缓存命中率>90%
- [ ] 备份恢复演练成功

### 8.3 监控告警验收

- [ ] Prometheus 正常采集所有指标
- [ ] Grafana 仪表板展示完整
- [ ] 告警规则触发正常
- [ ] 钉钉/邮件通知送达

### 8.4 CI/CD 验收

- [ ] 代码提交自动触发流水线
- [ ] 自动化测试通过率 100%
- [ ] Docker 镜像构建成功
- [ ] 自动部署到测试环境
- [ ] 人工审批后部署到生产

---

## 九、风险与应对

| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|----------|
| 云资源交付延迟 | 低 | 高 | 提前 3 天申请，准备备选云厂商 |
| 数据库性能不达标 | 中 | 高 | 预留 20% 性能余量，监控调优 |
| 监控告警误报 | 中 | 中 | 设置合理阈值，逐步优化 |
| CI/CD 流水线故障 | 中 | 中 | 保留手动部署方案，定期演练 |
| 安全漏洞 | 低 | 高 | 定期安全扫描，及时打补丁 |

---

## 十、附录

### 10.1 相关文档

- [MVP 技术调研报告](./mvp-tech-research.md)
- [MVP 产品需求文档](./mvp-prd.md)
- [MVP 项目计划](./mvp-project-plan.md)

### 10.2 联系方式

| 角色 | 负责人 | 联系方式 |
|------|--------|----------|
| DevOps 工程师 | devops | devops@mvp-project.com |
| CTO | cto | cto@mvp-project.com |
| 架构师 | architect | architect@mvp-project.com |

---

**文档状态**: ✅ 完成  
**提交时间**: 2026-03-11  
**审核人**: CTO  
**下次更新**: 04-06（环境验收后）

[MVP, 开发环境，配置清单，服务器，数据库，监控，CI/CD, DevOps]

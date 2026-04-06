# MVP 项目启动会 - DevOps 技术方案

**会议时间**: 2026-03-17 14:00-16:00  
**汇报人**: DevOps 工程师  
**版本**: V1.0

---

## 一、CI/CD 流水线方案

### 1.1 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| **代码托管** | GitHub | 团队熟悉，Actions 生态成熟 |
| **CI 引擎** | GitHub Actions | 原生集成，免费额度充足 |
| **CD 部署** | GitHub Actions + Docker | 自动化部署，支持多环境 |
| **容器注册表** | Docker Hub / ACR | 镜像存储与分发 |

### 1.2 流水线设计

```yaml
# .github/workflows/ci-cd.yml
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  # 阶段 1: 代码质量检查
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install Dependencies
        run: pip install -e ".[dev]"
      
      - name: Run Linter
        run: ruff check src/
      
      - name: Run Type Checker
        run: mypy src/
      
      - name: Run Tests
        run: pytest tests/ -v --cov=src --cov-report=xml
        continue-on-error: true  # MVP 阶段允许测试失败
      
      - name: Upload Coverage
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage.xml

  # 阶段 2: 构建 Docker 镜像
  build-docker:
    needs: lint-and-test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      
      - name: Login to Docker Hub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}
      
      - name: Build and Push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: |
            ${{ secrets.DOCKER_USERNAME }}/mvp-app:${{ github.sha }}
            ${{ secrets.DOCKER_USERNAME }}/mvp-app:latest
          cache-from: type=registry,ref=${{ secrets.DOCKER_USERNAME }}/mvp-app:buildcache
          cache-to: type=registry,ref=${{ secrets.DOCKER_USERNAME }}/mvp-app:buildcache,mode=max

  # 阶段 3: 部署到开发环境
  deploy-dev:
    needs: build-docker
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/develop'
    steps:
      - name: Deploy to Dev Environment
        run: |
          # SSH 连接到开发服务器
          ssh -o StrictHostKeyChecking=no ${{ secrets.DEV_SSH_USER }}@${{ secrets.DEV_SSH_HOST }} << 'EOF'
            cd /opt/mvp-dev
            docker-compose pull
            docker-compose up -d
          EOF

  # 阶段 4: 部署到生产环境（手动触发）
  deploy-prod:
    needs: build-docker
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    environment: production
    steps:
      - name: Deploy to Production
        run: |
          ssh -o StrictHostKeyChecking=no ${{ secrets.PROD_SSH_USER }}@${{ secrets.PROD_SSH_HOST }} << 'EOF'
            cd /opt/mvp-prod
            docker-compose pull
            docker-compose up -d
          EOF
```

### 1.3 质量门禁

| 检查项 | 标准 | 执行阶段 |
|--------|------|----------|
| 代码风格 | Ruff 检查通过 | CI |
| 类型检查 | Mypy 无严重错误 | CI |
| 单元测试 | 覆盖率≥60% (MVP 阶段) | CI |
| 构建成功 | Docker 镜像构建成功 | CD |
| 部署验证 | 健康检查接口返回 200 | CD |

### 1.4 实施计划

| 时间 | 任务 | 负责人 |
|------|------|--------|
| 03-17 | 创建 GitHub 仓库，配置 Secrets | DevOps |
| 03-18 | 编写 Dockerfile 和 docker-compose.yml | DevOps |
| 03-19 | 配置 CI 流水线（lint+test） | DevOps |
| 03-20 | 配置 CD 流水线（自动部署） | DevOps |
| 03-21 | 联调测试，验证全流程 | 全员 |

---

## 二、监控告警平台方案

### 2.1 技术栈

| 组件 | 选型 | 用途 |
|------|------|------|
| **指标采集** | Prometheus | 系统指标、应用指标 |
| **可视化** | Grafana | 监控面板、数据展示 |
| **告警管理** | Alertmanager | 告警路由、通知 |
| **日志收集** | Loki (可选) | 日志聚合查询 |
| **链路追踪** | Tempo (可选) | 分布式追踪 |

### 2.2 监控指标设计

#### 系统层指标
- CPU 使用率
- 内存使用率
- 磁盘使用率
- 网络流量

#### 应用层指标
- HTTP 请求量 (QPS)
- 响应时间 (P50/P95/P99)
- 错误率 (4xx/5xx)
- 活跃连接数

#### 业务层指标
- 工作流执行次数
- 任务队列长度
- API 调用次数
- 用户活跃度

### 2.3 Docker Compose 部署配置

```yaml
# docker-compose.monitoring.yml
version: '3.8'

services:
  prometheus:
    image: prom/prometheus:v2.45.0
    container_name: prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/etc/prometheus/console_libraries'
      - '--web.console.templates=/etc/prometheus/consoles'
      - '--web.enable-lifecycle'
    ports:
      - "9090:9090"
    networks:
      - monitoring

  grafana:
    image: grafana/grafana:10.0.0
    container_name: grafana
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD:-admin123}
      - GF_USERS_ALLOW_SIGN_UP=false
    ports:
      - "3000:3000"
    networks:
      - monitoring
    depends_on:
      - prometheus

  alertmanager:
    image: prom/alertmanager:v0.25.0
    container_name: alertmanager
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml
      - alertmanager_data:/alertmanager
    command:
      - '--config.file=/etc/alertmanager/alertmanager.yml'
      - '--storage.path=/alertmanager'
    ports:
      - "9093:9093"
    networks:
      - monitoring

  node-exporter:
    image: prom/node-exporter:v1.6.0
    container_name: node-exporter
    command:
      - '--path.rootfs=/host'
    volumes:
      - '/:/host:ro,rslave'
    ports:
      - "9100:9100"
    networks:
      - monitoring

networks:
  monitoring:
    driver: bridge

volumes:
  prometheus_data:
  grafana_data:
  alertmanager_data:
```

### 2.4 告警规则示例

```yaml
# alert_rules.yml
groups:
  - name: application_alerts
    rules:
      # 高错误率告警
      - alert: HighErrorRate
        expr: sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m])) > 0.01
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "错误率过高 (当前值 {{ $value | humanizePercentage }})"
          description: "应用 {{ $labels.job }} 的错误率超过 1%"

      # 高响应时间告警
      - alert: HighResponseTime
        expr: histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le)) > 0.5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "响应时间过长 (P95: {{ $value }}s)"
          description: "应用 {{ $labels.job }} 的 P95 响应时间超过 500ms"

      # 服务宕机告警
      - alert: ServiceDown
        expr: up == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "服务宕机 ({{ $labels.job }})"
          description: "服务 {{ $labels.job }} 已宕机超过 1 分钟"

  - name: infrastructure_alerts
    rules:
      # CPU 使用率过高
      - alert: HighCPUUsage
        expr: 100 - (avg by(instance) (irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "CPU 使用率过高 ({{ $value }}%)"

      # 内存使用率过高
      - alert: HighMemoryUsage
        expr: (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100 > 85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "内存使用率过高 ({{ $value }}%)"

      # 磁盘使用率过高
      - alert: HighDiskUsage
        expr: (1 - (node_filesystem_avail_bytes / node_filesystem_size_bytes)) * 100 > 80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "磁盘使用率过高 ({{ $value }}%)"
```

### 2.5 通知渠道配置

| 级别 | 渠道 | 配置 |
|------|------|------|
| **P0 紧急** | 电话 + 短信 | 钉钉/企微机器人 + 短信网关 |
| **P1 重要** | IM 通知 | 钉钉/企微机器人 |
| **P2 一般** | 邮件 | SMTP 邮件服务 |

### 2.6 实施计划

| 时间 | 任务 | 负责人 |
|------|------|--------|
| 03-17 | 准备监控服务器资源 | DevOps |
| 03-18 | 部署 Prometheus+Grafana | DevOps |
| 03-19 | 配置应用指标采集 | DevOps + dev-a/b |
| 03-20 | 配置告警规则和通知 | DevOps |
| 03-21 | 创建监控面板 | DevOps |

---

## 三、Docker 标准化环境

### 3.1 目录结构

```
mvp-project/
├── docker/
│   ├── Dockerfile.dev          # 开发环境
│   ├── Dockerfile.prod         # 生产环境
│   └── nginx/
│       └── nginx.conf          # Nginx 配置
├── docker-compose.yml          # 本地开发
├── docker-compose.dev.yml      # 开发环境
├── docker-compose.prod.yml     # 生产环境
└── docker-compose.monitoring.yml  # 监控栈
```

### 3.2 Dockerfile 示例

```dockerfile
# docker/Dockerfile.prod
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY pyproject.toml poetry.lock* ./

# 安装 Python 依赖
RUN pip install --no-cache-dir .

# 复制应用代码
COPY src/ ./src/

# 创建非 root 用户
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 3.3 Docker Compose 配置

```yaml
# docker-compose.yml (本地开发)
version: '3.8'

services:
  app:
    build:
      context: .
      dockerfile: docker/Dockerfile.dev
    ports:
      - "8000:8000"
    volumes:
      - ./src:/app/src
      - ./tests:/app/tests
    environment:
      - ENVIRONMENT=development
      - DATABASE_URL=postgresql://postgres:devpass@db:5432/mvp
      - REDIS_URL=redis://redis:6379
    depends_on:
      - db
      - redis
    command: uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

  db:
    image: postgres:15
    environment:
      - POSTGRES_PASSWORD=devpass
      - POSTGRES_DB=mvp
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
    volumes:
      - qdrant_data:/qdrant/storage

volumes:
  postgres_data:
  redis_data:
  qdrant_data:
```

### 3.4 环境配置清单

| 环境 | 配置 | 用途 |
|------|------|------|
| **本地开发** | docker-compose.yml | 开发者本地环境 |
| **开发环境** | docker-compose.dev.yml | 团队共享开发服务器 |
| **测试环境** | docker-compose.test.yml | 自动化测试环境 |
| **生产环境** | docker-compose.prod.yml | 线上生产环境 |

### 3.5 实施计划

| 时间 | 任务 | 负责人 |
|------|------|--------|
| 03-17 | 编写 Dockerfile 模板 | DevOps |
| 03-18 | 编写 docker-compose 配置 | DevOps |
| 03-19 | 本地验证环境搭建 | 全员 |
| 03-20 | 文档化环境搭建流程 | DevOps |

---

## 四、服务器资源配置建议

### 4.1 MVP 阶段资源配置

| 资源 | 配置 | 数量 | 月成本 | 用途 |
|------|------|------|--------|------|
| **应用服务器 (ECS)** | 4 核 8G | 2 台 | ¥2,400 | 主备部署，运行应用服务 |
| **数据库 (RDS)** | PostgreSQL 高可用 4 核 8G | 1 套 | ¥1,500 | 主从架构，数据持久化 |
| **缓存 (Redis)** | 2 核 4G | 1 套 | ¥300 | 缓存/会话/任务队列 |
| **向量数据库** | 2 核 4G ECS 自托管 Qdrant | 1 台 | ¥600 | 向量检索 |
| **监控服务器** | 2 核 4G ECS | 1 台 | ¥600 | Prometheus+Grafana |
| **对象存储 (OSS)** | 100GB | 1 套 | ¥100 | 文件存储 |
| **CDN** | 100GB/月 | 1 套 | ¥100 | 静态资源加速 |
| **负载均衡 (SLB)** | 性能保障型 | 1 套 | ¥200 | 流量分发 |
| **合计** | - | - | **¥5,800/月** | - |

### 4.2 扩容策略

| 指标 | 阈值 | 动作 |
|------|------|------|
| CPU 使用率 | >70% 持续 5 分钟 | 自动扩容 1 台 ECS |
| 内存使用率 | >80% 持续 5 分钟 | 自动扩容 1 台 ECS |
| 数据库连接数 | >80% | 升级 RDS 规格 |
| 响应时间 P95 | >1s | 检查慢查询，优化索引 |

### 4.3 采购建议

| 供应商 | 优势 | 建议 |
|--------|------|------|
| **阿里云** | 国内领先，生态完整 | 首选，主力使用 |
| **腾讯云** | 性价比高，IM 集成好 | 备选，灾备使用 |
| **华为云** | 政企客户多 | 暂不考虑 |

**建议**: 采用阿里云为主，预留腾讯云灾备方案

---

## 五、资源需求与风险

### 5.1 资源需求

| 资源 | 数量 | 用途 | 预算 |
|------|------|------|------|
| **云服务器** | 5 台 ECS | 应用/数据库/监控 | ¥3,600/月 |
| **云数据库** | 1 套 RDS | PostgreSQL 高可用 | ¥1,500/月 |
| **云缓存** | 1 套 Redis | 缓存/队列 | ¥300/月 |
| **存储/CDN** | OSS+CDN | 文件/静态资源 | ¥200/月 |
| **监控工具** | Grafana Cloud (可选) | 增强监控 | ¥500/月 |
| **合计** | - | - | **¥6,100/月** |

**6 个月 MVP 总预算**: ¥36,600

### 5.2 风险与应对

| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|----------|
| **服务器成本超预算** | 中 | 中 | 按月监控，设置预算告警 |
| **单点故障** | 低 | 高 | 关键服务主备部署 |
| **数据丢失** | 低 | 高 | 每日自动备份，异地容灾 |
| **安全漏洞** | 中 | 高 | 定期安全扫描，及时打补丁 |

---

## 六、下一步行动

### 6.1 本周行动项 (03-17 ~ 03-21)

| 任务 | 负责人 | 截止时间 | 状态 |
|------|--------|----------|------|
| 创建 GitHub 仓库，配置 Secrets | DevOps | 03-17 | 待开始 |
| 编写 Dockerfile 和 docker-compose | DevOps | 03-18 | 待开始 |
| 部署 Prometheus+Grafana | DevOps | 03-19 | 待开始 |
| 配置 CI 流水线 | DevOps | 03-19 | 待开始 |
| 配置告警规则和通知 | DevOps | 03-20 | 待开始 |
| 环境搭建文档编写 | DevOps | 03-21 | 待开始 |

### 6.2 需要决策事项

1. **云服务商选择**: 确认使用阿里云作为主供应商
2. **预算审批**: 确认¥6,100/月的服务器预算
3. **告警通知渠道**: 确认使用钉钉/企微作为主要通知渠道
4. **代码仓库**: 确认使用 GitHub 作为代码托管平台

---

**文档状态**: ✅ 完成  
**最后更新**: 2026-03-17 00:48  
**汇报对象**: CEO, CTO, 项目组成员

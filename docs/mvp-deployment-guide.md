# MVP 部署文档

**版本**: V1.0  
**最后更新**: 2026-03-11  
**负责人**: DevOps 工程师  

---

## 一、快速开始

### 1.1 本地开发环境（推荐）

```bash
# 1. 克隆项目
git clone https://github.com/your-org/openakita.git
cd openakita

# 2. 复制环境配置
cp .env.example .env
# 编辑 .env 填入必要配置（至少 LLM_API_KEY）

# 3. 一键启动开发环境
docker-compose up -d

# 4. 验证服务
curl http://localhost:8000/health

# 5. 访问监控面板
# Grafana: http://localhost:3000 (admin/admin123)
# Prometheus: http://localhost:9090
```

### 1.2 服务端口一览

| 服务 | 端口 | 用途 |
|------|------|------|
| App | 8000 | 主应用服务 |
| PostgreSQL | 5432 | 关系型数据库 |
| Redis | 6379 | 缓存/消息队列 |
| Qdrant | 6333 | 向量数据库 |
| Prometheus | 9090 | 监控指标 |
| Grafana | 3000 | 可视化面板 |
| Loki | 3100 | 日志聚合 |
| Nginx | 80 | 反向代理（可选） |

---

## 二、云资源部署

### 2.1 前置准备

1. **阿里云账号**：确保已开通 ECS、RDS、OSS 等服务
2. **域名备案**：生产环境需完成 ICP 备案
3. **SSH 密钥**：生成 SSH 密钥对并配置到阿里云

```bash
ssh-keygen -t ed25519 -C "openakita-deploy"
# 将公钥 ~/.ssh/id_ed25519.pub 添加到阿里云 ECS
```

### 2.2 部署步骤

#### Step 1: 创建 VPC 网络

```bash
# 阿里云控制台 → VPC → 创建专有网络
VPC 名称：openakita-mvp-vpc
网段：192.168.0.0/16
地域：华东 1（杭州）
```

#### Step 2: 创建 ECS 实例

```bash
# 通过阿里云 CLI 或控制台创建
# 参考 docs/mvp-infra-config.md 1.2 节配置

# 安装 Docker
ssh root@<ecs-public-ip>
curl -fsSL https://get.docker.com | bash
systemctl enable docker
systemctl start docker

# 安装 Docker Compose
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose
```

#### Step 3: 配置 RDS PostgreSQL

```bash
# 阿里云控制台 → RDS → 创建实例
# 选择 PostgreSQL 15 高可用版
# 白名单：添加 ECS 内网 IP

# 创建数据库
psql -h <rds-endpoint> -U postgres
CREATE DATABASE openakita;
```

#### Step 4: 部署 Qdrant（Docker）

```bash
# 在 ECS 上执行
docker run -d \
  --name qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  -v /data/qdrant/storage:/qdrant/storage \
  qdrant/qdrant:latest
```

#### Step 5: 部署应用

```bash
# 在 ECS 上执行
cd /opt/openakita
git clone <repository-url> .
cp .env.example .env
# 编辑 .env 配置生产环境参数

# 构建并启动
docker-compose -f docker-compose.prod.yml up -d

# 查看日志
docker-compose logs -f app
```

#### Step 6: 配置 Nginx 反向代理

```nginx
# /etc/nginx/conf.d/openakita.conf
upstream openakita_backend {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name openakita.com;

    location / {
        proxy_pass http://openakita_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /health {
        proxy_pass http://openakita_backend/health;
        access_log off;
    }
}

# 重载 Nginx
nginx -t && nginx -s reload
```

#### Step 7: 配置 HTTPS（可选）

```bash
# 使用 Let's Encrypt
apt install certbot python3-certbot-nginx
certbot --nginx -d openakita.com
```

---

## 三、CI/CD 流水线

### 3.1 GitHub Secrets 配置

在 GitHub 仓库设置中添加以下 Secrets：

```
# 部署配置
TEST_SERVER=<测试服务器 IP>
PROD_SERVER=<生产服务器 IP>
DEPLOY_SSH_KEY=<SSH 私钥>

# 镜像仓库
GHCR_TOKEN=<GitHub Container Registry Token>

# 监控告警
DINGTALK_WEBHOOK_URL=<钉钉机器人 Webhook>
DINGTALK_SECRET=<钉钉签名密钥>
```

### 3.2 部署流程

```
代码提交 → CI 检查 → 构建镜像 → 部署测试环境 → 人工审批 → 部署生产环境
```

**Blue-Green 部署策略**：
1. 新镜像部署到非活跃槽位（如 Green）
2. 健康检查通过后切换流量
3. 观察 5 分钟无异常后停止旧槽位（Blue）
4. 如有问题，一键回滚到 Blue 槽位

### 3.3 手动触发部署

```bash
# GitHub Actions → MVP Deploy → Run workflow
# 选择环境：test / production
# 选择版本：留空（最新）或指定 tag
```

---

## 四、监控告警

### 4.1 访问监控面板

```bash
# Grafana（应用指标可视化）
http://<server-ip>:3000
用户名：admin
密码：admin123（首次登录后修改）

# Prometheus（指标查询）
http://<server-ip>:9090

# Loki 日志（日志查询）
http://<server-ip>:3100
```

### 4.2 核心监控指标

| 指标 | 告警阈值 | 说明 |
|------|----------|------|
| 接口错误率 | >5% (5min) | HTTP 5xx 错误占比 |
| P95 延迟 | >1s (5min) | 95% 请求响应时间 |
| CPU 使用率 | >80% (10min) | 服务器 CPU 负载 |
| 内存使用率 | >85% (10min) | 服务器内存占用 |
| 磁盘使用率 | >80% (10min) | 系统盘使用率 |
| PostgreSQL 连接数 | >100 (5min) | 数据库并发连接 |
| Redis 内存 | >90% (5min) | Redis 内存使用率 |

### 4.3 告警通知配置

#### 钉钉机器人

```bash
# 钉钉群 → 群设置 → 智能群助手 → 添加机器人
# 选择"自定义"，获取 Webhook URL 和签名密钥

# 填入 .env
DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=xxx
DINGTALK_SECRET=SECxxx
```

#### 邮件告警

```bash
# 填入 .env
ALERT_EMAIL_FROM=alerts@openakita.com
ALERT_EMAIL_TO=devops@openakita.com

# 配置 SMTP 服务器（参考 .env.example 邮件配置）
```

---

## 五、故障排查手册

### 5.1 应用无法启动

```bash
# 1. 查看应用日志
docker-compose logs app

# 2. 检查数据库连接
docker-compose exec app python -c "from sqlalchemy import create_engine; create_engine('$DATABASE_URL').connect()"

# 3. 检查端口占用
netstat -tlnp | grep 8000

# 4. 重启服务
docker-compose restart app
```

### 5.2 数据库连接失败

```bash
# 1. 检查数据库状态
docker-compose ps db

# 2. 查看数据库日志
docker-compose logs db

# 3. 测试连接
docker-compose exec db pg_isready -U postgres

# 4. 检查白名单（云数据库）
# 阿里云控制台 → RDS → 白名单设置
```

### 5.3 Redis 连接超时

```bash
# 1. 检查 Redis 状态
docker-compose ps redis

# 2. 测试连接
docker-compose exec redis redis-cli ping

# 3. 查看 Redis 内存
docker-compose exec redis redis-cli info memory

# 4. 清理过期键
docker-compose exec redis redis-cli MEMORY PURGE
```

### 5.4 Qdrant 向量库异常

```bash
# 1. 检查 Qdrant 状态
docker ps | grep qdrant

# 2. 查看日志
docker logs qdrant

# 3. 测试 API
curl http://localhost:6333/

# 4. 检查集合
curl http://localhost:6333/collections

# 5. 重启 Qdrant
docker restart qdrant
```

### 5.5 监控数据缺失

```bash
# 1. 检查 Prometheus 状态
docker-compose ps prometheus

# 2. 查看 Targets
# 访问 http://localhost:9090/targets

# 3. 检查 scrape 配置
docker-compose exec prometheus cat /etc/prometheus/prometheus.yml

# 4. 重载配置
curl -X POST http://localhost:9090/-/reload
```

### 5.6 告警未触发

```bash
# 1. 检查 Alertmanager
docker-compose ps alertmanager

# 2. 查看告警规则
# 访问 http://localhost:9090/rules

# 3. 测试告警
# 手动触发高负载或模拟服务宕机

# 4. 检查通知渠道配置
# .env 中的 DINGTALK_WEBHOOK_URL 和邮件配置
```

### 5.7 磁盘空间不足

```bash
# 1. 查看磁盘使用
df -h

# 2. 清理 Docker 日志
docker-compose logs --tail=100
docker system prune -a

# 3. 清理旧镜像
docker images | grep openakita
docker rmi <old-image-id>

# 4. 清理 Prometheus 历史数据
# 修改 prometheus.yml 中的 retention 设置
```

### 5.8 性能问题排查

```bash
# 1. 查看慢查询（PostgreSQL）
docker-compose exec db psql -U postgres -d openakita -c "SELECT * FROM pg_stat_activity WHERE state = 'active';"

# 2. 查看应用性能
# Grafana → Application Dashboard → Latency/Throughput

# 3. 查看资源使用
docker stats

# 4. 启用性能分析
# 在 .env 中设置 LOG_LEVEL=DEBUG
# 重启应用后查看详细日志
```

---

## 六、健康检查端点

### 6.1 基础健康检查

```bash
curl http://localhost:8000/health

# 响应示例
{
  "status": "healthy",
  "timestamp": "2026-03-11T12:00:00Z",
  "version": "1.0.0"
}
```

### 6.2 详细健康检查

```bash
curl http://localhost:8000/health/detailed

# 响应示例
{
  "status": "healthy",
  "checks": {
    "database": {"status": "up", "latency_ms": 5},
    "redis": {"status": "up", "latency_ms": 2},
    "qdrant": {"status": "up", "latency_ms": 10},
    "llm_api": {"status": "up", "latency_ms": 200}
  }
}
```

### 6.3 就绪检查

```bash
curl http://localhost:8000/ready

# 用于 Kubernetes 就绪探针
# 返回 200 表示服务已准备好接收流量
```

---

## 七、备份与恢复

### 7.1 数据库备份

```bash
# 每日自动备份（凌晨 2 点）
0 2 * * * pg_dump -h <rds-endpoint> -U postgres openakita > /backup/openakita_$(date +\%Y\%m\%d).sql

# 手动备份
docker-compose exec db pg_dump -U postgres openakita > backup.sql

# 恢复
cat backup.sql | docker-compose exec -T db psql -U postgres openakita
```

### 7.2 Qdrant 备份

```bash
# 使用 Qdrant 快照 API
curl -X POST http://localhost:6333/collections/<collection-name>/snapshots

# 快照文件位于 /qdrant/storage/snapshots/
# 定期备份到 OSS
```

### 7.3 配置文件备份

```bash
# 备份 .env 和 docker-compose.yml
tar -czf openakita-config-$(date +%Y%m%d).tar.gz .env docker-compose.yml

# 上传到 OSS
ossutil cp openakita-config-*.tar.gz oss://openakita-mvp/backups/
```

---

## 八、安全加固

### 8.1 最小权限原则

- 数据库账号按应用分离
- ECS 安全组仅开放必要端口
- 使用 RAM 子账号管理云资源

### 8.2 密钥管理

- 所有密钥存入 GitHub Secrets
- 生产环境使用 KMS 加密
- 定期轮换密钥（建议 90 天）

### 8.3 网络安全

- 启用 VPC 内网通信
- 配置 WAF 防火墙
- 启用 DDoS 防护

### 8.4 日志审计

- 所有操作记录到 Loki
- 敏感操作单独告警
- 日志保留 180 天

---

## 九、性能优化建议

### 9.1 数据库优化

```sql
-- 添加索引
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_tasks_created_at ON tasks(created_at DESC);

-- 分析慢查询
EXPLAIN ANALYZE SELECT * FROM tasks WHERE status = 'pending';

-- 定期 VACUUM
VACUUM ANALYZE;
```

### 9.2 缓存优化

```python
# 使用 Redis 缓存热点数据
from redis import Redis
redis = Redis.from_url(REDIS_URL)

# 设置缓存（5 分钟过期）
redis.setex('user:123', 300, user_data)

# 读取缓存
user_data = redis.get('user:123')
```

### 9.3 异步任务

```python
# 耗时操作使用 Celery
from celery import Celery
celery = Celery(broker=CELERY_BROKER_URL)

@celery.task
def send_email_task(user_id, content):
    # 发送邮件逻辑
    pass

# 异步调用
send_email_task.delay(user_id, content)
```

---

## 十、验收清单

### 10.1 环境验收

- [ ] 本地开发环境一键启动成功
- [ ] 所有服务健康检查通过
- [ ] 监控面板正常显示指标
- [ ] 告警测试通过（模拟故障触发通知）

### 10.2 CI/CD 验收

- [ ] 代码提交后自动触发 CI
- [ ] 自动部署至测试环境
- [ ] 人工审批后可部署生产环境
- [ ] Blue-Green 切换正常
- [ ] 一键回滚功能可用

### 10.3 监控验收

- [ ] Prometheus 采集到所有目标指标
- [ ] Grafana 仪表板显示完整
- [ ] 告警规则配置正确
- [ ] 钉钉/邮件告警通知正常

### 10.4 安全验收

- [ ] 所有密钥已加密存储
- [ ] 安全组配置正确
- [ ] 数据库白名单配置正确
- [ ] HTTPS 证书有效

---

**文档维护**: DevOps 工程师  
**更新频率**: 每次环境变更时更新  
**最后验证**: 2026-03-11

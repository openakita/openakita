# OpenAkita 私有化部署指南

**版本**: v1.0.0  
**更新日期**: 2026-03-11  
**适用对象**: 企业客户、系统管理员、DevOps 工程师

---

## 📋 目录

- [快速开始](#快速开始)
- [系统要求](#系统要求)
- [部署步骤](#部署步骤)
- [配置说明](#配置说明)
- [服务管理](#服务管理)
- [监控与日志](#监控与日志)
- [备份与恢复](#备份与恢复)
- [故障排查](#故障排查)
- [安全建议](#安全建议)

---

## 🚀 快速开始

### 首次部署（5 分钟）

```bash
# 1. 克隆或下载部署包
git clone <repository-url>
cd openakita-deploy

# 2. 初始化环境
chmod +x deploy.sh
./deploy.sh init

# 3. 编辑配置文件
vim .env  # 修改密码和 API 密钥

# 4. 启动服务
./deploy.sh start

# 5. 验证部署
curl http://localhost:3000/health
```

### 访问地址

| 服务 | 地址 | 说明 |
|------|------|------|
| 应用服务 | http://localhost:3000 | OpenAkita 主应用 |
| 数据库 | localhost:5432 | PostgreSQL |
| Redis | localhost:6379 | 缓存服务 |
| Grafana | http://localhost:3001 | 监控面板（可选） |
| Prometheus | http://localhost:9090 | 指标收集（可选） |

---

## 💻 系统要求

### 最低配置（小型部署，≤10 并发 Agent）

| 资源 | 要求 |
|------|------|
| CPU | 2 核 |
| 内存 | 4 GB |
| 磁盘 | 20 GB SSD |
| 操作系统 | Linux (Ubuntu 20.04+/CentOS 7+) |

### 推荐配置（中型部署，≤50 并发 Agent）

| 资源 | 要求 |
|------|------|
| CPU | 4 核 |
| 内存 | 8 GB |
| 磁盘 | 50 GB SSD |
| 操作系统 | Linux (Ubuntu 22.04 LTS) |

### 生产配置（大型部署，≤200 并发 Agent）

| 资源 | 要求 |
|------|------|
| CPU | 8 核+ |
| 内存 | 16 GB+ |
| 磁盘 | 200 GB+ NVMe SSD |
| 操作系统 | Linux (Ubuntu 22.04 LTS) |
| 网络 | 100 Mbps+ |

### 前置依赖

- **Docker**: 20.10+
- **Docker Compose**: 2.0+
- **Git**: 用于版本管理（可选）

#### 安装 Docker（Ubuntu）

```bash
# 卸载旧版本
sudo apt-get remove docker docker-engine docker.io containerd runc

# 安装依赖
sudo apt-get update
sudo apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release

# 添加 Docker 官方 GPG 密钥
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# 设置稳定版仓库
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 安装 Docker Engine
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io

# 安装 Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# 验证安装
docker --version
docker-compose --version

# 将当前用户加入 docker 组（可选，避免每次使用 sudo）
sudo usermod -aG docker $USER
```

---

## 📦 部署步骤

### 步骤 1：准备部署环境

```bash
# 创建部署目录
mkdir -p /opt/openakita
cd /opt/openakita

# 上传或克隆部署文件
# 方式 A: Git 克隆
git clone <repository-url> .

# 方式 B: 上传部署包
# 将 openakita-deploy.tar.gz 上传到此目录并解压
tar -xzf openakita-deploy.tar.gz --strip-components=1
```

### 步骤 2：初始化配置

```bash
# 赋予执行权限
chmod +x deploy.sh

# 初始化环境（自动生成目录结构和配置文件）
./deploy.sh init
```

初始化后会创建以下目录结构：

```
openakita/
├── docker-compose.prod.yml    # Docker Compose 配置
├── deploy.sh                  # 部署脚本
├── .env                       # 环境变量配置（需手动编辑）
├── data/                      # 应用数据目录
│   └── app/
├── logs/                      # 日志目录
├── backups/                   # 备份目录
│   ├── db/
│   └── redis/
├── skills/                    # 技能包目录
├── nginx/                     # Nginx 配置
│   ├── nginx.conf
│   └── conf.d/
├── ssl/                       # SSL 证书目录
└── monitoring/                # 监控配置
    ├── prometheus.yml
    └── grafana/
```

### 步骤 3：配置环境变量

编辑 `.env` 文件，修改以下关键配置：

```bash
vim .env
```

#### 必须修改的配置

| 变量 | 说明 | 示例 |
|------|------|------|
| `DB_PASSWORD` | 数据库密码（强密码） | `MyStr0ng!Pass#2026` |
| `REDIS_PASSWORD` | Redis 密码（强密码） | `Redis$ecure!2026` |
| `JWT_SECRET` | JWT 签名密钥（随机字符串） | `openssl rand -hex 32` |
| `LLM_API_KEY` | 大模型 API 密钥 | `sk-ant-xxx` |

#### 可选配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `APP_PORT` | 3000 | 应用服务端口 |
| `LLM_PROVIDER` | anthropic | 大模型提供商 |
| `MAX_CONCURRENT_AGENTS` | 10 | 最大并发 Agent 数 |
| `LOG_LEVEL` | info | 日志级别 |

#### 生成安全密钥

```bash
# 生成 JWT_SECRET
openssl rand -hex 32

# 生成强密码
openssl rand -base64 32
```

### 步骤 4：启动服务

```bash
# 启动所有服务
./deploy.sh start

# 查看启动日志
./deploy.sh logs

# 检查服务状态
./deploy.sh status
```

### 步骤 5：验证部署

```bash
# 健康检查
curl http://localhost:3000/health

# 预期响应
{"status":"healthy","timestamp":"2026-03-11T12:00:00Z","version":"1.0.0"}

# 测试 API（如有认证）
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:3000/api/v1/status
```

---

## ⚙️ 配置说明

### 环境变量详解

#### 应用配置

```bash
# 运行环境
NODE_ENV=production  # 不要修改

# 服务端口
APP_PORT=3000  # 可修改，避免端口冲突

# 日志级别：debug, info, warn, error
LOG_LEVEL=info

# 最大并发 Agent 数（根据服务器配置调整）
MAX_CONCURRENT_AGENTS=10
```

#### 数据库配置

```bash
# 数据库连接
DB_USER=openakita  # 建议保持默认
DB_PASSWORD=CHANGE_ME  # ⚠️ 必须修改
DB_NAME=openakita  # 建议保持默认
DB_PORT=5432  # 可修改
```

#### Redis 配置

```bash
REDIS_PASSWORD=CHANGE_ME  # ⚠️ 必须修改
REDIS_PORT=6379  # 可修改
```

#### 大模型配置

```bash
# 支持的提供商：anthropic, openai, azure, local
LLM_PROVIDER=anthropic

# API 密钥（根据提供商获取）
LLM_API_KEY=sk-ant-xxx

# 本地部署模型（如使用 Ollama）
# LLM_PROVIDER=local
# LLM_API_URL=http://localhost:11434
```

### Nginx 配置（可选）

如需启用 Nginx 反向代理和 HTTPS：

```bash
# 1. 启动 Nginx 服务
docker-compose --profile nginx up -d nginx

# 2. 配置 SSL 证书
# 将证书文件放入 ./ssl/ 目录
# fullchain.pem - 证书链
# privkey.pem - 私钥

# 3. 修改 nginx/conf.d/app.conf
# 取消 HTTPS 配置的注释

# 4. 重启 Nginx
docker-compose --profile nginx restart nginx
```

### 监控配置（可选）

启用 Prometheus + Grafana 监控：

```bash
# 启动监控服务
docker-compose --profile monitoring up -d prometheus grafana

# 访问 Grafana
# http://localhost:3001
# 默认账号：admin
# 默认密码：.env 中配置的 GRAFANA_ADMIN_PASSWORD
```

---

## 🔧 服务管理

### 常用命令

```bash
# 启动服务
./deploy.sh start

# 停止服务
./deploy.sh stop

# 重启服务
./deploy.sh restart

# 查看状态
./deploy.sh status

# 查看日志
./deploy.sh logs

# 查看实时日志（按 Ctrl+C 退出）
./deploy.sh logs | tail -f
```

### Docker Compose 原生命令

```bash
# 查看容器列表
docker-compose ps

# 查看容器日志
docker-compose logs -f app

# 重启单个服务
docker-compose restart app

# 进入容器
docker-compose exec app bash

# 查看资源使用
docker stats
```

### 服务升级

```bash
# 1. 备份数据
./deploy.sh backup

# 2. 拉取最新镜像
docker-compose pull

# 3. 重启服务
docker-compose up -d

# 4. 验证版本
curl http://localhost:3000/api/version
```

---

## 📊 监控与日志

### 日志位置

| 类型 | 位置 | 说明 |
|------|------|------|
| 应用日志 | `./logs/` | 应用运行日志 |
| Nginx 日志 | `./logs/nginx/` | 访问日志和错误日志 |
| 数据库日志 | Docker 容器内 | `docker-compose logs db` |
| Redis 日志 | Docker 容器内 | `docker-compose logs redis` |

### 日志级别

```bash
# 修改 .env 中的 LOG_LEVEL
LOG_LEVEL=debug   # 调试（详细日志，生产环境不推荐）
LOG_LEVEL=info    # 信息（默认推荐）
LOG_LEVEL=warn    # 警告
LOG_LEVEL=error   # 错误
```

### 监控指标

通过 Prometheus 收集以下指标：

- **应用指标**: 请求数、响应时间、错误率、Agent 并发数
- **系统指标**: CPU 使用率、内存使用率、磁盘 IO
- **数据库指标**: 连接数、查询延迟、缓存命中率
- **Redis 指标**: 内存使用、命中率、键数量

访问 Prometheus: http://localhost:9090

### Grafana 仪表板

预置仪表板包括：

- **系统概览**: CPU、内存、磁盘、网络
- **应用性能**: 请求量、响应时间、错误率
- **数据库监控**: 连接数、慢查询、锁等待
- **业务指标**: Agent 执行数、任务成功率

---

## 💾 备份与恢复

### 自动备份（推荐）

创建定时备份任务（crontab）：

```bash
# 编辑 crontab
crontab -e

# 添加每日凌晨 2 点备份任务
0 2 * * * /opt/openakita/deploy.sh backup >> /var/log/openakita-backup.log 2>&1
```

### 手动备份

```bash
# 执行备份
./deploy.sh backup

# 备份文件位置
ls -lh ./backups/*.tar.gz

# 备份内容
# - 数据库 SQL 导出
# - Redis 数据快照
# - 环境配置（不含敏感信息）
```

### 数据恢复

```bash
# 1. 停止服务
./deploy.sh stop

# 2. 恢复数据
./deploy.sh restore

# 3. 选择备份文件
# 输入备份文件名（如：20260311_020000.tar.gz）

# 4. 重启服务
./deploy.sh start
```

### 备份策略建议

| 数据类型 | 频率 | 保留周期 | 存储位置 |
|----------|------|----------|----------|
| 数据库 | 每日 | 30 天 | 本地 + 异地 |
| Redis | 每日 | 7 天 | 本地 |
| 配置文件 | 每次变更 | 永久 | Git 仓库 |
| 日志文件 | 每周 | 90 天 | 对象存储 |

---

## 🔍 故障排查

### 常见问题

#### 1. 服务无法启动

```bash
# 检查 Docker 服务
systemctl status docker

# 查看容器日志
docker-compose logs app

# 检查端口占用
netstat -tlnp | grep 3000

# 检查磁盘空间
df -h

# 检查内存
free -h
```

#### 2. 数据库连接失败

```bash
# 检查数据库容器状态
docker-compose ps db

# 查看数据库日志
docker-compose logs db

# 测试数据库连接
docker-compose exec db pg_isready -U openakita

# 检查 .env 配置
grep DB_PASSWORD .env
```

#### 3. Redis 连接失败

```bash
# 检查 Redis 容器状态
docker-compose ps redis

# 测试 Redis 连接
docker-compose exec redis redis-cli ping

# 检查密码配置
grep REDIS_PASSWORD .env
```

#### 4. 应用健康检查失败

```bash
# 查看应用日志
./deploy.sh logs

# 检查 LLM API 密钥
grep LLM_API_KEY .env

# 测试 API 端点
curl -v http://localhost:3000/health

# 检查资源限制
docker stats openakita-app
```

#### 5. 内存不足

```bash
# 查看内存使用
docker stats

# 调整资源限制（docker-compose.prod.yml）
# 修改 app 服务的 deploy.resources.limits.memory

# 重启服务
docker-compose restart app
```

### 获取帮助

```bash
# 查看部署脚本帮助
./deploy.sh help

# 查看完整文档
cat DEPLOYMENT.md

# 联系技术支持
# email: support@openakita.ai
# 文档：https://docs.openakita.ai
```

---

## 🔒 安全建议

### 生产环境必做

1. **修改默认密码**
   - 数据库密码（DB_PASSWORD）
   - Redis 密码（REDIS_PASSWORD）
   - Grafana 管理员密码（GRAFANA_ADMIN_PASSWORD）
   - JWT 密钥（JWT_SECRET）

2. **启用 HTTPS**
   - 配置 SSL 证书
   - 强制 HTTPS 重定向
   - 启用 HSTS

3. **防火墙配置**
   ```bash
   # 仅开放必要端口
   sudo ufw allow 80/tcp    # HTTP（可选，用于 HTTPS 重定向）
   sudo ufw allow 443/tcp   # HTTPS
   sudo ufw allow 22/tcp    # SSH
   sudo ufw enable
   ```

4. **定期更新**
   - 每月检查 Docker 镜像更新
   - 每季度更新系统补丁
   - 及时应用安全修复

5. **访问控制**
   - 限制数据库远程访问
   - 配置 API 访问白名单
   - 启用 API 速率限制

### 安全审计

```bash
# 检查 Docker 安全配置
docker bench security

# 查看开放端口
netstat -tlnp

# 检查容器权限
docker inspect openakita-app | grep -i security
```

---

## 📞 技术支持

- **文档**: https://docs.openakita.ai
- **邮箱**: support@openakita.ai
- **GitHub**: https://github.com/openakita/openakita
- **社区**: https://community.openakita.ai

---

**© 2026 OpenAkita. All rights reserved.**

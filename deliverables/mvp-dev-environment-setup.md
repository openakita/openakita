# MVP 开发环境配置清单

**创建时间**: 2026-03-11  
**负责人**: CTO / DevOps 工程师  
**状态**: 待预算批准后执行  

---

## 一、服务器资源配置

### 1.1 开发环境
| 资源 | 配置 | 数量 | 用途 | 月度成本 |
|------|------|------|------|----------|
| 云服务器 ECS | 2 核 4G | 2 台 | 开发 + 测试 | 4,000 元 |
| 云数据库 RDS | MySQL 基础版 | 1 个 | 开发数据库 | 2,000 元 |
| Redis 缓存 | 2GB 主从版 | 1 个 | 缓存 + 队列 | 1,000 元 |
| 对象存储 OSS | 100GB | 1 个 | 文件存储 | 500 元 |
| **合计** | - | - | - | **7,500 元/月** |

### 1.2 生产环境（MVP 上线后）
| 资源 | 配置 | 数量 | 用途 | 月度成本 |
|------|------|------|------|----------|
| 云服务器 ECS | 4 核 8G | 2 台 | 应用服务 + 备份 | 8,000 元 |
| 云数据库 RDS | MySQL 高可用版 | 1 个 | 主从架构 | 4,000 元 |
| Redis 缓存 | 4GB 集群版 | 1 个 | 高性能缓存 | 2,000 元 |
| 负载均衡 SLB | 基础版 | 1 个 | 流量分发 | 1,000 元 |
| CDN 加速 | 100GB/月 | 1 个 | 静态资源 | 1,000 元 |
| 对象存储 OSS | 500GB+ 流量 | 1 个 | 文件存储 | 1,000 元 |
| **合计** | - | - | - | **17,000 元/月** |

---

## 二、数据库配置

### 2.1 PostgreSQL（主数据库）
```bash
# 版本：PostgreSQL 15
# 初始化脚本
createdb mvp_workflow
psql mvp_workflow -c "CREATE USER workflow_user WITH PASSWORD 'your_password';"
psql mvp_workflow -c "GRANT ALL PRIVILEGES ON DATABASE mvp_workflow TO workflow_user;"

# 核心表结构
- workflows (工作流定义)
- workflow_nodes (节点配置)
- workflow_executions (执行记录)
- api_integrations (API 集成配置)
- users (用户账户)
```

### 2.2 Redis（缓存 + 队列）
```bash
# 版本：Redis 7
# 配置项
maxmemory: 2gb
maxmemory-policy: allkeys-lru
appendonly: yes
appendfsync: everysec

# 队列结构
- celery: 异步任务队列
- workflow:events: 事件总线
- cache:*: 各类缓存
```

### 2.3 Qdrant（向量数据库）
```bash
# 部署方式：Docker
docker run -p 6333:6333 -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant

# 集合：workflow_templates（工作流模板向量索引）
```

---

## 三、API 密钥配置

### 3.1 大模型 API
| 服务商 | 用途 | 密钥变量 | 预算 |
|--------|------|----------|------|
| 智谱 AI | 中文场景推理 | ZHIPU_API_KEY | 5,000 元/月 |
| Moonshot | 长文本处理 | MOONSHOT_API_KEY | 3,000 元/月 |
| Claude (备用) | 复杂推理 | ANTHROPIC_API_KEY | 按需 |

### 3.2 第三方集成
| 服务 | 用途 | 密钥变量 | 成本 |
|------|------|----------|------|
| 钉钉开放平台 | 企业 IM 集成 | DINGTALK_APP_KEY/SECRET | 免费 |
| 企业微信 | 企业 IM 集成 | WECOM_CORP_ID/SECRET | 免费 |
| 飞书开放平台 | 企业 IM 集成 | FEISHU_APP_ID/SECRET | 免费 |
| SendGrid | 邮件发送 | SENDGRID_API_KEY | 免费额度 |
| 阿里云短信 | 短信通知 | ALIYUN_ACCESS_KEY/SECRET | 按量付费 |

---

## 四、开发工具链

### 4.1 本地开发环境
```bash
# Python 环境
Python 3.11+
venv: .venv/
依赖：pip install -r requirements.txt

# Node.js 环境
Node.js 18+
npm: 9+
前端依赖：npm install (apps/workflow-editor/)

# Docker
Docker Desktop 4.x
Docker Compose 2.x
```

### 4.2 CI/CD 工具
| 工具 | 用途 | 配置状态 |
|------|------|----------|
| GitHub Actions | 自动化测试 + 构建 | ✅ 已有配置 |
| Docker Hub | 镜像仓库 | ⏳ 待创建 |
| 阿里云 ACR | 国内镜像加速 | ⏳ 待配置 |

### 4.3 监控工具
| 工具 | 用途 | 成本 |
|------|------|------|
| Prometheus + Grafana | 系统监控 | 免费（自托管） |
| Sentry | 错误追踪 | 免费额度 |
| 云监控 | 云服务器监控 | 包含在 ECS 费用中 |

---

## 五、部署流程

### 5.1 开发环境部署（Day 1）
```bash
# 1. 克隆代码
git clone git@github.com:company/mvp-workflow.git
cd mvp-workflow

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API 密钥

# 3. 启动 Docker 服务
docker-compose up -d postgres redis qdrant

# 4. 安装依赖
pip install -r requirements.txt
npm install --prefix apps/workflow-editor/

# 5. 数据库迁移
alembic upgrade head

# 6. 启动开发服务器
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

### 5.2 生产环境部署（MVP 上线）
```bash
# 1. 构建 Docker 镜像
docker build -t workflow-api:latest .
docker push registry.cn-hangzhou.aliyuncs.com/company/workflow-api:latest

# 2. K8s 部署（或使用 Docker Swarm）
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# 3. 配置负载均衡
# 阿里云 SLB 绑定后端服务

# 4. 配置域名和 SSL
# 阿里云 DNS 解析 + SSL 证书
```

---

## 六、安全检查清单

### 6.1 部署前检查
- [ ] 所有 API 密钥已轮换（不使用默认值）
- [ ] 数据库密码强度符合要求（16 位+ 特殊字符）
- [ ] 防火墙规则已配置（仅开放必要端口）
- [ ] SSL 证书已配置（HTTPS 强制）
- [ ] 敏感信息已加密存储（.env 文件不提交 Git）

### 6.2 运行时安全
- [ ] 启用数据库连接池加密
- [ ] 启用 Redis 密码认证
- [ ] 配置 API 限流（100 次/分钟/用户）
- [ ] 启用日志审计（记录所有敏感操作）
- [ ] 定期备份数据库（每日自动备份）

---

## 七、成本汇总

### 7.1 一次性投入
| 项目 | 金额 | 说明 |
|------|------|------|
| 域名注册 | 100 元 | .com 域名 |
| SSL 证书 | 0 元 | Let's Encrypt 免费 |
| 外包设计 | 20,000 元 | UI/UX 设计（已批准） |
| **合计** | **20,100 元** | - |

### 7.2 月度成本（开发期 03-04 月）
| 类别 | 金额 | 说明 |
|------|------|------|
| 服务器资源 | 7,500 元 | 开发环境 |
| 大模型 API | 8,000 元 | 智谱+Moonshot |
| 监控工具 | 0 元 | 自托管免费 |
| **合计** | **15,500 元/月** | - |

### 7.3 月度成本（生产期 05 月起）
| 类别 | 金额 | 说明 |
|------|------|------|
| 服务器资源 | 17,000 元 | 生产环境 |
| 大模型 API | 15,000 元 | 用户增长 |
| 监控 + 备份 | 2,000 元 | Sentry 付费版 + 备份 |
| **合计** | **34,000 元/月** | - |

---

## 八、负责人分工

| 任务 | 负责人 | 截止时间 | 状态 |
|------|--------|----------|------|
| 云服务器采购 | DevOps | 03-12 | ⏳ 待批准 |
| 数据库初始化 | 全栈工程师 A | 03-12 | ⏳ 待启动 |
| API 密钥申请 | CTO | 03-12 | ⏳ 待批准 |
| CI/CD 配置 | DevOps | 03-13 | ⏳ 待启动 |
| 监控部署 | DevOps | 03-14 | ⏳ 待启动 |
| 安全审计 | 架构师 | 03-15 | ⏳ 待启动 |

---

**审批状态**: 待 CEO 批准预算后执行  
**下一步**: 03-12 09:00 MVP 启动会后立即执行  

[开发环境，MVP 配置，技术准备，待执行]

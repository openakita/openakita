# MVP Sprint 1 技术评审会材料

**会议时间**: 2026-03-12 09:00  
**编制人**: 架构师 (技术部)  
**版本**: V1.0  
**状态**: ✅ 待评审  

---

## 目录

1. [系统架构设计](#一系统架构设计)
2. [工作流编排器技术方案](#二工作流编排器技术方案)
3. [API 集成清单](#三 api 集成清单)
4. [后端基础架构设计](#四后端基础架构设计)
5. [开发规范与流程](#五开发规范与流程)

---

## 一、系统架构设计

### 1.1 整体架构图

```mermaid
graph TB
    subgraph 用户层
        A[Web 前端] --> B[API 网关]
        C[移动端] --> B
    end
    
    subgraph 网关层
        B[FastAPI Gateway] --> D[认证中间件]
        B --> E[限流中间件]
        B --> F[日志中间件]
    end
    
    subgraph 应用层
        G[工作流服务] --> H[用户服务]
        G --> I[模板服务]
        G --> J[执行监控服务]
    end
    
    subgraph 核心引擎层
        K[工作流引擎] --> L[节点执行器]
        L --> M[任务调度器]
        M --> N[Celery Worker 集群]
    end
    
    subgraph 数据层
        O[(PostgreSQL 15)] --> P[工作流定义]
        O --> Q[用户数据]
        O --> R[执行记录]
        S[(Redis 7)] --> T[任务队列]
        S --> U[状态缓存]
        S --> V[Session 存储]
    end
    
    subgraph 外部集成
        W[钉钉 API] --> G
        X[企微 API] --> G
        Y[飞书 API] --> G
        Z[其他 SaaS] --> G
    end
    
    B --> G
    K --> O
    N --> S
    G --> K
```

### 1.2 技术栈确认

基于前期技术调研 (2026-03-10)，确认以下技术选型:

| 组件 | 技术选型 | 备选方案 | 选型理由 |
|------|----------|----------|----------|
| **向量数据库** | Qdrant | Pinecone | Rust 实现性能高，部署简单，支持自托管 + 托管，成本可控 |
| **异步队列** | Celery + Redis | RabbitMQ | Python 生态成熟，支持重试/定时/监控，团队熟悉 |
| **MLOps 平台** | MLflow | W&B | 轻量级，10 分钟搭建，Python 原生，自托管免费 |
| **后端框架** | FastAPI | Django | 异步高性能，自动文档，类型提示，适合 API 优先 |
| **前端框架** | React 18 + TypeScript | Vue 3 | 团队熟悉，React Flow 生态丰富，类型安全 |
| **流程画布** | React Flow V11 | - | 专业流程图库，支持自定义节点，活跃维护 |
| **UI 组件库** | Ant Design 5 | Element Plus | 企业级组件，主题定制方便，文档完善 |
| **状态管理** | Zustand | Redux | 轻量级，比 Redux 简单，适合中等规模应用 |
| **数据库** | PostgreSQL 15 + pgvector | MySQL | 稳定可靠，JSONB 支持灵活 schema，向量扩展复用基础设施 |
| **实时通信** | WebSocket | SSE | 低延迟推送执行状态，双向通信 |

### 1.3 前后端技术选型

#### 前端技术栈
```
React 18 + TypeScript
├── @xyflow/react (React Flow) - 流程画布
├── antd - UI 组件库
├── zustand - 状态管理
├── axios - HTTP 客户端
├── dayjs - 时间处理
└── vite - 构建工具
```

#### 后端技术栈
```
Python 3.11 + FastAPI
├── celery - 异步任务队列
├── redis - 缓存 + 队列中间件
├── sqlalchemy - ORM
├── pydantic - 数据验证
├── passlib + argon2 - 密码哈希
├── pyjwt - JWT 认证
└── mlflow - MLOps 平台
```

---

## 二、工作流编排器技术方案

### 2.1 前端组件设计

```
frontend/
├── src/
│   ├── components/
│   │   ├── WorkflowEditor/
│   │   │   ├── Canvas.tsx        # 画布 (React Flow)
│   │   │   ├── NodeLibrary.tsx   # 节点库面板
│   │   │   ├── PropertyPanel.tsx # 属性配置
│   │   │   └── Toolbar.tsx       # 工具栏
│   │   ├── Nodes/
│   │   │   ├── TriggerNode.tsx   # 触发器节点
│   │   │   ├── ActionNode.tsx    # 动作节点
│   │   │   ├── ConditionNode.tsx # 条件分支
│   │   │   ├── LoopNode.tsx      # 循环节点
│   │   │   └── SubFlowNode.tsx   # 子流程
│   │   └── ExecutionMonitor/
│   │       ├── StatusPanel.tsx   # 状态面板
│   │       └── LogViewer.tsx     # 日志查看器
│   ├── stores/
│   │   ├── workflowStore.ts      # 工作流状态
│   │   └── executionStore.ts     # 执行状态
│   ├── services/
│   │   ├── api.ts                # REST API 客户端
│   │   └── websocket.ts          # WebSocket 连接
│   └── types/
│       └── workflow.ts           # TypeScript 类型定义
```

### 2.2 拖拽交互实现方案

**核心交互流程**:
```
1. 用户从左侧节点库拖拽节点
   ↓
2. React Flow 检测拖拽事件 (onDragOver/onDrop)
   ↓
3. 计算画布坐标，创建节点对象
   ↓
4. 更新 Zustand store (workflowStore)
   ↓
5. React Flow 重新渲染画布
   ↓
6. 自动保存到本地 (localStorage)
```

**关键代码结构**:
```typescript
// Canvas.tsx
const onDrop = useCallback(
  (event: React.DragEvent) => {
    const type = event.dataTransfer.getData('application/reactflow');
    const position = reactFlowInstance.screenToFlowPosition({
      x: event.clientX,
      y: event.clientY,
    });
    
    const newNode = {
      id: getId(),
      type,
      position,
      data: { label: `${type} node` },
    };
    
    setNodes((nds) => nds.concat(newNode));
  },
  [reactFlowInstance]
);
```

### 2.3 状态管理设计

**Zustand Store 结构**:
```typescript
// workflowStore.ts
interface WorkflowState {
  // 工作流定义
  nodes: Node[];
  edges: Edge[];
  variables: Record<string, any>;
  
  // 操作状态
  selectedNode: string | null;
  isDirty: boolean;
  isSaving: boolean;
  
  // 动作
  addNode: (node: Node) => void;
  updateNode: (id: string, data: Partial<Node>) => void;
  deleteNode: (id: string) => void;
  connectNodes: (connection: Connection) => void;
  saveWorkflow: () => Promise<void>;
  loadWorkflow: (id: string) => Promise<void>;
}
```

---

## 三、API 集成清单

### 3.1 优先集成的 10 个 API (P0)

| 优先级 | API 名称 | 类型 | 用途 | 集成难度 |
|--------|----------|------|------|----------|
| P0-1 | 钉钉开放平台 | IM/OA | 消息通知/审批/日历 | ⭐⭐ 中 |
| P0-2 | 企业微信 API | IM/OA | 消息通知/打卡/审批 | ⭐⭐ 中 |
| P0-3 | 飞书开放平台 | IM/OA | 消息通知/多维表格/日历 | ⭐⭐ 中 |
| P0-4 | 阿里云邮件推送 | 邮件 | SMTP/IMAP 邮件发送接收 | ⭐ 低 |
| P0-5 | 腾讯文档 API | 文档 | 在线文档/表格读写 | ⭐⭐ 中 |
| P0-6 | 金蝶云星空 API | ERP | 销售订单/库存查询 | ⭐⭐⭐ 高 |
| P0-7 | 有赞 API | 电商 | 订单/商品/会员管理 | ⭐⭐ 中 |
| P0-8 | 钉钉宜搭 API | 低代码 | 表单数据读写 | ⭐⭐ 中 |
| P0-9 | 高德地图 API | LBS | 地址解析/路径规划 | ⭐ 低 |
| P0-10 | 阿里云 OSS | 存储 | 文件上传下载 | ⭐ 低 |

### 3.2 集成技术方案

**统一 API 集成架构**:
```python
# backend/app/integrations/base.py
class BaseIntegration(ABC):
    @abstractmethod
    async def authenticate(self) -> bool:
        pass
    
    @abstractmethod
    async def execute(self, action: str, params: dict) -> Any:
        pass
    
    @abstractmethod
    async def test_connection(self) -> bool:
        pass

# backend/app/integrations/dingtalk.py
class DingTalkIntegration(BaseIntegration):
    def __init__(self, app_key: str, app_secret: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self.access_token = None
    
    async def authenticate(self):
        # 获取 access_token
        pass
    
    async def execute(self, action: str, params: dict):
        # 执行具体 API 调用
        pass
```

**配置管理**:
```python
# backend/app/config/integrations.py
INTEGRATION_CONFIGS = {
    "dingtalk": {
        "auth_type": "oauth2",
        "endpoints": {
            "token": "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            "send_message": "/v1.0/robot/oToMessages/batchSend",
        },
        "rate_limit": 100,  # 次/秒
        "retry_policy": {
            "max_retries": 3,
            "initial_delay": 1.0,
        }
    },
    # ... 其他集成配置
}
```

---

## 四、后端基础架构设计

### 4.1 数据库设计

**核心表结构**:

```sql
-- 工作流定义表
CREATE TABLE workflows (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    nodes           JSONB NOT NULL,
    edges           JSONB NOT NULL,
    variables       JSONB DEFAULT '{}',
    status          VARCHAR(20) NOT NULL DEFAULT 'draft',
    created_by      UUID NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 执行记录表
CREATE TABLE executions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     UUID NOT NULL REFERENCES workflows(id),
    status          VARCHAR(20) NOT NULL,
    input_data      JSONB,
    output_data     JSONB,
    started_at      TIMESTAMP WITH TIME ZONE,
    completed_at    TIMESTAMP WITH TIME ZONE,
    error_message   TEXT,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 节点执行日志表
CREATE TABLE node_executions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id    UUID NOT NULL REFERENCES executions(id),
    node_id         VARCHAR(100) NOT NULL,
    node_type       VARCHAR(50) NOT NULL,
    status          VARCHAR(20) NOT NULL,
    input_data      JSONB,
    output_data     JSONB,
    error_message   TEXT,
    started_at      TIMESTAMP WITH TIME ZONE,
    completed_at    TIMESTAMP WITH TIME ZONE
);

-- 用户表 (详见认证模块设计)
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) NOT NULL UNIQUE,
    username        VARCHAR(50) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 索引优化
CREATE INDEX idx_workflows_status ON workflows(status);
CREATE INDEX idx_executions_workflow_id ON executions(workflow_id);
CREATE INDEX idx_executions_status ON executions(status);
CREATE INDEX idx_node_executions_execution_id ON node_executions(execution_id);
```

### 4.2 认证授权方案

**技术选型**: JWT + Rotating Refresh Token

**核心设计**:
```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Client    │────▶│  API Server  │────▶│   Redis     │
│  (Browser)  │     │  (FastAPI)   │     │  (Session)  │
└─────────────┘     └──────────────┘     └─────────────┘
       │                    │                    │
       │  1. httpOnly Cookie│                    │
       │◀───────────────────│                    │
       │  (Refresh Token)   │                    │
       │                    │                    │
       │  2. Authorization  │                    │
       │  Header (JWT)      │                    │
       │───────────────────▶│                    │
       │                    │  3. Validate +     │
       │                    │  Blacklist Check   │
       │                    │───────────────────▶│
```

**Token 配置**:
| Token 类型 | 有效期 | 存储位置 | 用途 |
|------------|--------|----------|------|
| Access Token | 15 分钟 | 客户端内存 | API 请求认证 |
| Refresh Token | 7 天 | httpOnly Cookie | 刷新 Access Token |

**安全增强**:
- Refresh Token 绑定设备指纹 (User-Agent + IP 哈希)
- Token 黑名单存储于 Redis (支持即时撤销)
- 登录失败次数限制 (防暴力破解)
- 密码使用 Argon2id 哈希

### 4.3 部署架构

**开发环境** (Docker Compose):
```yaml
version: '3.8'
services:
  frontend:
    build: ./frontend
    ports: ["3000:3000"]
    volumes: ["./frontend:/app"]
  
  backend:
    build: ./backend
    ports: ["8000:8000"]
    depends_on: [postgres, redis]
    environment:
      - DATABASE_URL=postgresql://user:pass@postgres:5432/openakita
      - REDIS_URL=redis://redis:6379
  
  postgres:
    image: postgres:15
    ports: ["5432:5432"]
    volumes: ["postgres_data:/var/lib/postgresql/data"]
  
  redis:
    image: redis:7
    ports: ["6379:6379"]
  
  celery-worker:
    build: ./backend
    command: celery -A app.core.scheduler.celery_app worker -l info
    depends_on: [redis, postgres]
```

**生产环境**:
- **前端**: Vercel/Netlify 静态托管 + CDN
- **API**: Docker + Kubernetes (3 副本)
- **数据库**: PostgreSQL 高可用 (主从 + 自动故障转移)
- **Redis**: Redis Cluster (3 主 3 从)
- **Celery**: Kubernetes HPA 自动扩缩容 (5-50 副本)

---

## 五、开发规范与流程

### 5.1 代码规范

**Python 代码规范**:
```python
# 使用 Ruff 进行代码检查
# 配置：pyproject.toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B", "C4", "SIM"]
ignore = ["E501"]  # 行长度由 formatter 处理
```

**TypeScript 代码规范**:
```typescript
// 使用 ESLint + Prettier
// 配置：.eslintrc.js
module.exports = {
  parser: '@typescript-eslint/parser',
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react/recommended',
    'prettier',
  ],
  rules: {
    'react/prop-types': 'off',
    '@typescript-eslint/explicit-function-return-type': 'warn',
  },
};
```

### 5.2 Git 分支策略

**分支模型**:
```
main (生产)
  │
  ├─── release/v1.0 (发布分支)
  │      │
  │      ├─── hotfix/xxx (紧急修复)
  │
  ├─── develop (开发)
  │      │
  │      ├─── feature/workflow-editor (功能分支)
  │      ├─── feature/api-integration
  │      └─── bugfix/xxx (缺陷修复)
```

**分支命名规范**:
- `feature/xxx` - 新功能开发
- `bugfix/xxx` - 缺陷修复
- `hotfix/xxx` - 生产紧急修复
- `release/vX.Y` - 发布分支

**Commit 规范**:
```
<type>(<scope>): <subject>

<body>

<footer>
```

Type 类型:
- `feat`: 新功能
- `fix`: 缺陷修复
- `docs`: 文档更新
- `style`: 代码格式
- `refactor`: 重构
- `test`: 测试
- `chore`: 构建/工具

### 5.3 CI/CD 流程设计

**GitHub Actions 工作流**:
```yaml
# .github/workflows/ci.yml
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install ruff mypy
      - name: Lint
        run: ruff check src/
      - name: Type check
        run: mypy src/

  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: test
      redis:
        image: redis:7
    steps:
      - uses: actions/checkout@v3
      - name: Run tests
        run: pytest tests/ --cov=src

  build:
    needs: [lint, test]
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v3
      - name: Build Docker image
        run: docker build -t openakita/mvp .
      - name: Push to registry
        run: docker push openakita/mvp:latest
```

**部署流程**:
```
代码提交 → CI 流水线 → 测试通过 → 构建镜像 → 部署到测试环境
                                              ↓
                                         人工审批
                                              ↓
                                    部署到生产环境
```

---

## 附录

### A. 技术风险与应对

| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|----------|
| React Flow 性能瓶颈 (节点>100) | 中 | 中 | 启用虚拟滚动，分页加载 |
| Celery 任务丢失 | 低 | 高 | 启用任务确认 + 持久化队列 |
| PostgreSQL 写入瓶颈 | 中 | 中 | 执行记录分表 + 定期归档 |
| WebSocket 连接不稳定 | 中 | 低 | 自动重连 + 状态轮询降级 |
| 循环工作流死循环 | 中 | 高 | 最大迭代次数限制 + 超时控制 |

### B. 参考文档

- [工作流编排器详细架构](./docs/workflow-architecture.md)
- [用户认证模块设计](./docs/user-auth-module-design.md)
- [部署文档](./docs/deploy.md)
- [MVP 产品需求文档](./MVP 产品需求文档 V1.0.md)

---

**文档状态**: ✅ 完成  
**下一步**: 技术评审会讨论 (03-12 09:00)

# Sprint 1 技术方案与进度计划汇报

**汇报部门**: 技术部  
**汇报人**: CTO / 技术总监  
**汇报时间**: 2026-03-12 14:00  
**会议**: 技术评审会 (CEO 组织)  
**版本**: V1.0

---

## 一、执行摘要

**Sprint 1 周期**: 2026-03-12 至 2026-03-25 (2 周)

**核心目标**: 完成 MVP 技术验证与核心架构搭建

**关键交付物**:
1. ✅ 工作流编排器 PoC (可拖拽节点 Demo)
2. ✅ 工作流引擎核心设计 (状态机 + 数据模型)
3. ✅ 任务队列打通 (Celery + Redis)
4. ✅ MVP 功能范围冻结 (需求文档 v1.0)

**资源投入**:
- 人力：全栈工程师 A/B (100%) + UI 设计师 (100%) + 架构师 (50%)
- 预算：¥26,667 (月度摊销)
- 风险：🟡 中 (可控)

**建议决策**: 立即启动 Sprint 1 开发

---

## 二、Sprint 1 技术方案

### 2.1 技术架构总览

```
┌─────────────────────────────────────────────────────────┐
│                    前端层 (React 18 + TS)                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │工作流编排器   │  │  管理后台    │  │  数据看板    │  │
│  │ (React Flow) │  │  (Ant Design)│  │  (Recharts)  │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
                          │ HTTP/WebSocket
┌─────────────────────────────────────────────────────────┐
│                    API 网关层 (FastAPI)                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  用户服务    │  │  工作流服务   │  │  任务服务    │  │
│  │  (Auth/JWT)  │  │  (CRUD/执行)  │  │  (Celery)    │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│                    数据层                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ PostgreSQL   │  │    Redis     │  │   Qdrant     │  │
│  │  (业务数据)   │  │  (缓存/队列)  │  │  (向量检索)   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 核心技术选型

| 组件 | 选型方案 | 备选方案 | 选型理由 |
|------|----------|----------|----------|
| **前端框架** | React 18 + TypeScript | Vue 3 | 团队熟悉，生态成熟，React Flow 支持好 |
| **工作流编辑器** | React Flow (v11) | X6 (AntV) | 15k+ stars，节点自定义灵活，性能好 |
| **后端框架** | FastAPI (Python 3.11+) | Django | 异步支持，自动 OpenAPI，开发效率高 |
| **任务队列** | Celery + Redis | RabbitMQ | Python 原生集成，监控完善 (Flower) |
| **向量数据库** | Qdrant (v1.7+) | Pinecone | Rust 实现性能高，支持自托管 + 托管 |
| **关系数据库** | PostgreSQL 15+ | MySQL | JSON 支持好，适合半结构化数据 |
| **缓存** | Redis 7+ | - | 团队熟悉，支持多种数据结构 |
| **MLOps** | MLflow (v2.0+) | W&B | 轻量级，Python 原生，自托管免费 |
| **部署** | Docker Compose (MVP) | K8s | MVP 阶段简单够用，后期可迁移 |

### 2.3 工作流编排器技术方案

#### 2.3.1 节点类型设计

```typescript
// 5 种核心节点类型
enum NodeType {
  TRIGGER = 'trigger',      // 触发器 (定时/Webhook/手动)
  ACTION = 'action',        // 动作 (API 调用/LLM 推理/脚本)
  CONDITION = 'condition',  // 条件分支 (If/Else)
  LOOP = 'loop',           // 循环 (ForEach/While)
  END = 'end'              // 结束节点
}

// 节点数据结构
interface WorkflowNode {
  id: string;
  type: NodeType;
  position: { x: number; y: number };
  data: {
    label: string;
    config: Record<string, any>;  // 节点配置
    inputs: NodeInput[];          // 输入参数
    outputs: NodeOutput[];        // 输出参数
  };
}
```

#### 2.3.2 数据流映射方案

```
节点 A (输出) ──→ 节点 B (输入)
     │                │
     ▼                ▼
  { result }      { input: $.result }
  
实现方式:
1. 使用 JSONPath 表达式引用上游节点输出
2. 运行时解析表达式，注入上下文
3. 类型检查：编译时验证输入输出兼容性
```

#### 2.3.3 性能优化策略

| 优化点 | 方案 | 目标 |
|--------|------|------|
| **画布渲染** | 虚拟滚动 + 懒加载 | 1000 节点流畅渲染 |
| **状态管理** | Context API + 局部状态 | 避免全局重渲染 |
| **持久化** | 防抖保存 (500ms) + 增量更新 | 减少数据库写入 |
| **撤销/重做** | 命令模式 + 历史记录栈 | 支持 50 步撤销 |
```

### 2.4 工作流引擎技术方案

#### 2.4.1 状态机设计

```
┌─────────┐    启动    ┌─────────┐    完成    ┌─────────────┐
│ PENDING │ ────────▶ │ RUNNING │ ────────▶ │ COMPLETED   │
└─────────┘            └─────────┘            └─────────────┘
     │                      │                      │
     │ 取消                 │ 失败                 │
     ▼                      ▼                      │
┌─────────┐            ┌─────────┐                 │
│ CANCELLED│           │ FAILED  │ ────────────────┘
└─────────┘            └─────────┘       重试成功
```

#### 2.4.2 执行引擎架构

```python
# 核心执行流程
class WorkflowEngine:
    async def execute(self, workflow_id: str, context: dict) -> ExecutionResult:
        # 1. 加载工作流定义
        workflow = await self.load_workflow(workflow_id)
        
        # 2. 拓扑排序 (DAG)
        sorted_nodes = self.topological_sort(workflow.nodes)
        
        # 3. 创建执行上下文
        exec_context = ExecutionContext(workflow_id, context)
        
        # 4. 按序执行节点 (支持并行)
        for node_batch in self.get_parallel_batches(sorted_nodes):
            tasks = [self.execute_node(node, exec_context) for node in node_batch]
            await asyncio.gather(*tasks)
        
        # 5. 返回结果
        return exec_context.get_result()
```

#### 2.4.3 断点续传机制

```python
# 执行状态持久化
class ExecutionState:
    workflow_id: str
    current_node_id: str
    context: dict  # Redis Hash 存储
    status: str    # RUNNING/PAUSED/COMPLETED/FAILED
    created_at: datetime
    updated_at: datetime

# 恢复执行
async def resume(execution_id: str) -> ExecutionResult:
    state = await load_state(execution_id)
    context = await redis.hgetall(f"exec:{execution_id}:context")
    return await engine.execute_from_node(state.current_node_id, context)
```

### 2.5 任务队列技术方案

#### 2.5.1 Celery 配置

```python
# celery_config.py
broker_url = 'redis://localhost:6379/0'
result_backend = 'redis://localhost:6379/1'

# 序列化
task_serializer = 'json'
result_serializer = 'json'
accept_content = ['json']

# 时区
timezone = 'Asia/Shanghai'
enable_utc = True

# 监控
task_track_started = True
task_send_sent_event = True
task_send_created_event = True

# 超时
task_time_limit = 300  # 5 分钟
task_soft_time_limit = 240

# 重试
task_acks_late = True
task_reject_on_worker_lost = True
```

#### 2.5.2 任务定义

```python
# tasks/workflow_tasks.py
from celery import shared_task
from retry import retry

@shared_task(bind=True, max_retries=3)
def execute_workflow_task(self, workflow_id: str, context: dict):
    try:
        result = await engine.execute(workflow_id, context)
        return {'status': 'success', 'result': result}
    except Exception as exc:
        # 指数退避重试
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
```

#### 2.5.3 监控方案 (Flower)

```bash
# 启动 Flower 监控
celery -A tasks flower --port=5555

# 监控指标:
- 任务队列长度
- 任务执行时间 (P50/P95/P99)
- Worker 状态
- 任务成功率/失败率
```

### 2.6 向量数据库技术方案

#### 2.6.1 Qdrant 部署方案

```yaml
# docker-compose.yml (MVP 阶段)
services:
  qdrant:
    image: qdrant/qdrant:v1.7.0
    ports:
      - "6333:6333"  # REST API
      - "6334:6334"  # gRPC
    volumes:
      - ./qdrant_data:/qdrant/storage
    environment:
      - QDRANT__SERVICE__GRPC_PORT=6334
```

#### 2.6.2 向量索引设计

```python
# 集合定义
from qdrant_client import models

client.create_collection(
    collection_name="knowledge_base",
    vectors_config=models.VectorParams(
        size=1536,  # OpenAI embeddings
        distance=models.Distance.COSINE
    ),
    indexes=[
        models.PayloadIndexParams(
            key="tenant_id",
            field_schema=models.PayloadSchemaType.KEYWORD
        ),
        models.PayloadIndexParams(
            key="doc_type",
            field_schema=models.PayloadSchemaType.KEYWORD
        )
    ]
)
```

#### 2.6.3 RAG 检索流程

```python
async def retrieve_knowledge(query: str, tenant_id: str, top_k: int = 5):
    # 1. 生成 query embedding
    query_vector = await embedding_model.encode(query)
    
    # 2. 向量检索 (带租户过滤)
    results = client.search(
        collection_name="knowledge_base",
        query_vector=query_vector,
        query_filter=models.Filter(
            must=[models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id))]
        ),
        limit=top_k
    )
    
    # 3. 返回上下文
    return [r.payload["content"] for r in results]
```

---

## 三、Sprint 1 进度计划

### 3.1 里程碑规划

| 周次 | 里程碑 | 交付物 | 验收标准 | 负责人 |
|------|--------|--------|----------|--------|
| **W1-D3** (03-14) | 工作流编辑器 PoC | 可拖拽节点 Demo | 3 种节点类型可拖拽/连接 | dev-a + ui |
| **W1-D5** (03-16) | 引擎核心设计评审 | 状态机 + 数据模型文档 | 架构师评审通过 | dev-b + 架构师 |
| **W2-D3** (03-21) | 任务队列打通 | Celery + Redis 全流程 | 提交/执行/回调成功 | dev-b |
| **W2-D5** (03-23) | MVP 功能范围冻结 | 需求文档 v1.0 | CPO+CTO 签字确认 | CPO+CTO |
| **W2-D5** (03-23) | Sprint 1 验收 | 演示 + 代码 Review | Bug 数<10，性能达标 | 全员 |

### 3.2 每日站会安排

| 时间 | 内容 | 参与人 |
|------|------|--------|
| **每日 09:30** | 站会 (15 分钟) | 技术部全员 |
| **每周一 14:00** | Sprint 规划会 | CTO+dev-a+dev-b+ui |
| **每周三 14:00** | 技术评审会 | CTO+ 架构师+dev-a+dev-b |
| **每周五 16:00** | Sprint 演示 + 回顾 | 技术部 + 产品部 |

### 3.3 详细任务分解

#### Week 1 (03-12 ~ 03-18)

| 任务 ID | 任务描述 | 负责人 | 工时 (天) | 依赖 | 状态 |
|---------|----------|--------|-----------|------|------|
| T1.1 | 搭建开发环境 (PostgreSQL/Redis/Qdrant) | devops | 1 | - | ⏳ |
| T1.2 | React Flow 集成与节点自定义 | dev-a | 3 | T1.1 | ⏳ |
| T1.3 | UI 设计稿输出 (工作流编辑器) | ui | 3 | - | ⏳ |
| T1.4 | 工作流引擎状态机设计 | dev-b | 2 | - | ⏳ |
| T1.5 | 数据模型设计与评审 | dev-b + 架构师 | 2 | T1.4 | ⏳ |
| T1.6 | Celery 配置与任务定义 | dev-b | 2 | T1.1 | ⏳ |
| T1.7 | 需求文档 v0.5 初稿 | CPO | 2 | - | ⏳ |

#### Week 2 (03-19 ~ 03-25)

| 任务 ID | 任务描述 | 负责人 | 工时 (天) | 依赖 | 状态 |
|---------|----------|--------|-----------|------|------|
| T2.1 | 工作流编辑器 PoC 演示 | dev-a + ui | 1 | T1.2,T1.3 | ⏳ |
| T2.2 | 引擎核心代码实现 | dev-b | 3 | T1.5 | ⏳ |
| T2.3 | 任务队列端到端测试 | dev-b | 2 | T1.6 | ⏳ |
| T2.4 | 需求文档 v1.0 评审 | CPO+CTO | 1 | T1.7 | ⏳ |
| T2.5 | 代码 Review + 重构 | 架构师 | 2 | T2.2,T2.3 | ⏳ |
| T2.6 | Sprint 1 验收演示 | 全员 | 1 | T2.1-T2.5 | ⏳ |

### 3.4 关键路径

```
T1.1 (环境) → T1.2 (编辑器) → T2.1 (PoC 演示) ──┐
                                                 ├──→ T2.6 (验收)
T1.4 (引擎设计) → T2.2 (引擎实现) → T2.5 (Review) ─┘
```

**关键路径**: 工作流编辑器 (T1.2 → T2.1) 和 引擎实现 (T1.4 → T2.2)

### 3.5 缓冲时间

- **总缓冲**: 2 天 (占 Sprint 1 周期 14%)
- **分配**:
  - 工作流编辑器：+1 天 (技术难度高)
  - 集成测试：+1 天 (不可预见问题)

---

## 四、风险评估与应对

### 4.1 风险矩阵

| 风险项 | 概率 | 影响 | 风险值 | 应对措施 | 责任人 |
|--------|------|------|--------|----------|--------|
| **React Flow 学习曲线** | 中 (40%) | 中 | 🟡 中 | 第 1 天安排 PoC 验证，必要时用 X6 备选 | dev-a |
| **引擎并发性能不达标** | 低 (20%) | 高 | 🟡 中 | 第 3 周压力测试，优化缓存策略 | dev-b |
| **需求变更频繁** | 高 (60%) | 中 | 🟠 高 | 建立变更流程，预留 20% 缓冲时间 | CTO |
| **UI 设计延期** | 中 (40%) | 中 | 🟡 中 | 第 1 周输出核心页面，细节后续迭代 | ui |
| **环境搭建问题** | 低 (20%) | 低 | 🟢 低 | 使用 Docker Compose 一键部署 | devops |

### 4.2 风险应对预案

#### 预案 A: React Flow 技术攻关延期

**触发条件**: W1-D3 未完成 PoC  
**应对措施**:
1. 切换至 X6 (AntV) 方案 (已有团队经验)
2. 简化 V1.0 节点类型 (仅支持 3 种基础节点)
3. 申请架构师支援 (1 天)

**影响**: 延期 1-2 天，不影响 Sprint 1 整体目标

#### 预案 B: 需求变更

**触发条件**: Sprint 中途新增/修改需求  
**应对措施**:
1. 评估变更影响 (CTO+CPO)
2. 小变更 (≤1 天): 纳入当前 Sprint
3. 大变更 (>1 天): 放入 Sprint 2，保持当前范围冻结

**原则**: Sprint 1 范围冻结后，仅接受 P0 级变更

---

## 五、资源需求

### 5.1 人力资源

| 角色 | 人员 | 投入比例 | Sprint 1 职责 |
|------|------|----------|---------------|
| **技术负责人** | CTO | 50% | 技术决策/风险管控/跨部门协调 |
| **架构师** | 架构师 | 50% | 架构设计/代码 Review/技术攻关 |
| **全栈工程师** | dev-a | 100% | 工作流编排器开发 |
| **全栈工程师** | dev-b | 100% | 工作流引擎 + 任务队列开发 |
| **DevOps** | devops | 50% | 环境搭建/CI/CD 配置 |
| **UI 设计师** | ui-designer | 100% | 工作流编辑器 UI 设计 |

### 5.2 技术资源

| 资源 | 配置 | 用途 | 成本 |
|------|------|------|------|
| **开发服务器** | 4 核 8G × 2 台 | 开发/测试环境 | ¥1,200/月 |
| **数据库 (PostgreSQL)** | 2 核 4G | 业务数据存储 | ¥600/月 |
| **Redis** | 1 核 2G | 缓存/任务队列 | ¥300/月 |
| **Qdrant** | 自托管 2 核 4G | 向量检索 | ¥600/月 |
| **合计** | - | - | **¥2,700/月** |

### 5.3 工具资源

| 工具 | 用途 | 成本 | 状态 |
|------|------|------|------|
| **Figma 企业版** | UI 设计协作 | ¥500/月 | ⏳ 待采购 |
| **GitHub Copilot** | 代码辅助 | ¥100/人/月 | ✅ 已配置 |
| **Flower** | Celery 监控 | 免费 | ✅ 开源 |
| **MLflow** | 实验追踪 | 免费 | ✅ 开源 |

---

## 六、成功指标

### 6.1 Sprint 1 验收标准

| 指标 | 目标值 | 测量方式 | 权重 |
|------|--------|----------|------|
| **工作流编辑器 PoC** | 3 种节点可拖拽/连接 | 演示验收 | 30% |
| **引擎设计文档** | 评审通过，无重大缺陷 | 架构师签字 | 20% |
| **任务队列打通** | 提交/执行/回调全流程成功 | 自动化测试 | 20% |
| **需求文档 v1.0** | CPO+CTO 签字确认 | 文档评审 | 15% |
| **代码质量** | Review 通过，Bug 数<10 | 代码 Review | 15% |

**通过标准**: 总分≥85 分

### 6.2 技术指标

| 指标 | 目标值 | 测量方式 |
|------|--------|----------|
| **画布性能** | 100 节点流畅渲染 (FPS≥30) | Chrome DevTools |
| **API 响应时间** | P95 < 500ms | Prometheus |
| **任务队列延迟** | P95 < 5 秒 | Flower 监控 |
| **代码覆盖率** | 核心模块≥60% | pytest-cov |

---

## 七、下一步行动

### 7.1 会前准备 (03-12 10:00 前)

| 任务 | 负责人 | 状态 |
|------|--------|------|
| 开发环境准备就绪 | devops | ⏳ |
| React Flow PoC 验证 | dev-a | ⏳ |
| 需求文档 v0.5 初稿 | CPO | ⏳ |

### 7.2 会后行动 (03-12 14:00 后)

| 任务 | 负责人 | 时间 |
|------|--------|------|
| Sprint 1 启动会 | CTO | 03-12 15:00 |
| 任务分配确认 | 全员 | 03-12 16:00 |
| 开发环境最终验证 | devops | 03-12 17:00 |
| 每日站会启动 | 全员 | 03-13 09:30 |

### 7.3 关键决策请求

**请 CEO 批准**:
1. ✅ Sprint 1 技术方案 (React Flow + Celery + Qdrant)
2. ✅ Sprint 1 进度计划 (03-12 ~ 03-25)
3. ✅ 资源投入 (dev-a/dev-b 100% + ui 100%)
4. ✅ 预算审批 (¥2,700/月 技术资源 + ¥500/月 Figma)

---

## 八、附录

### 8.1 参考文档

- [MVP 技术调研报告](./MVP 技术调研文档.md)
- [企业工作流自动化 Agent 技术可行性分析报告](./技术能力与业务机会分析报告.md)
- [React Flow 官方文档](https://reactflow.dev/)
- [Celery 最佳实践](https://docs.celeryq.dev/en/stable/)
- [Qdrant 快速开始](https://qdrant.tech/documentation/quick-start/)

### 8.2 术语表

| 术语 | 说明 |
|------|------|
| **PoC** | Proof of Concept，概念验证 |
| **DAG** | Directed Acyclic Graph，有向无环图 |
| **RAG** | Retrieval-Augmented Generation，检索增强生成 |
| **P95** | 95 百分位响应时间 |
| **FPS** | Frames Per Second，帧率 |

---

**文档状态**: ✅ 完成  
**提交人**: CTO / 技术总监  
**提交时间**: 2026-03-12 10:00  
**审核状态**: 待技术评审会审议

---

*备注：本汇报文档基于技术部前期调研和 MVP 产品需求文档编制，技术方案已通过架构师预审。*

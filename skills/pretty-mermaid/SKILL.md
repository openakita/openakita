---
name: openakita/skills@pretty-mermaid
description: Generate professional Mermaid diagrams with multiple themes, SVG/ASCII output, batch rendering, and a built-in template library for common diagram patterns. Supports flowcharts, sequence diagrams, class diagrams, state diagrams, ER diagrams, C4 architecture, and Gantt charts.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# Pretty Mermaid — 专业 Mermaid 图表生成

## When to Use

- 用户要求创建流程图、架构图、时序图、类图、状态图、ER 图、C4 架构图或甘特图
- 需要将图表渲染为 SVG 或 ASCII 文本输出
- 需要批量生成多张图表
- 用户要求使用特定主题或配色方案
- 需要可复用的图表模板

---

## Prerequisites

### 必需工具

| 工具 | 用途 | 安装方式 |
|------|------|---------|
| Node.js ≥ 18 | 运行 Mermaid CLI | 系统预装或 `nvm install 18` |
| `@mermaid-js/mermaid-cli` | 渲染 SVG/PNG | `npm install -g @mermaid-js/mermaid-cli` |

### 可选工具

| 工具 | 用途 | 安装方式 |
|------|------|---------|
| `monodraw` (macOS) | ASCII 图表编辑 | App Store |
| `graph-easy` | ASCII 渲染 | `cpan Graph::Easy` |

### 验证安装

```bash
mmdc --version
```

如果 `mmdc` 不可用，回退到纯 Mermaid 代码块输出，让用户在支持 Mermaid 的编辑器或浏览器中预览。

---

## Instructions

### 核心工作流

1. **理解需求** — 明确图表类型、数据来源、目标受众
2. **选择图表类型** — 根据场景匹配最合适的 Mermaid 图表
3. **选择主题** — 根据使用场景选择配色方案
4. **编写 Mermaid 代码** — 遵循可读性最佳实践
5. **渲染输出** — 按指定格式输出 SVG、PNG 或 ASCII
6. **校验与优化** — 检查语法、布局、可读性

### 图表类型选择指南

| 场景 | 推荐图表 | Mermaid 关键字 |
|------|---------|---------------|
| 业务流程、审批流 | 流程图 | `flowchart TD/LR` |
| API 调用、服务交互 | 时序图 | `sequenceDiagram` |
| 代码结构、OOP 设计 | 类图 | `classDiagram` |
| 生命周期、状态机 | 状态图 | `stateDiagram-v2` |
| 数据库设计 | ER 图 | `erDiagram` |
| 系统架构、微服务 | C4 架构图 | `C4Context/C4Container/C4Component` |
| 项目排期、里程碑 | 甘特图 | `gantt` |
| Git 分支策略 | Git 图 | `gitGraph` |
| 思维导图、脑暴 | 思维导图 | `mindmap` |
| 时间线 | 时间线 | `timeline` |

---

## Workflows

### Workflow 1: 单张图表生成

**步骤 1 — 收集信息**

向用户确认以下内容（如未提供则使用默认值）：

| 参数 | 默认值 | 可选值 |
|------|--------|-------|
| 图表类型 | flowchart | 见上方选择指南 |
| 方向 | TD（上到下） | TD, LR, RL, BT |
| 主题 | default | default, dark, forest, neutral, base |
| 输出格式 | Mermaid 代码块 | svg, png, ascii, mermaid |
| 主题方案 | Tokyo Night | Tokyo Night, Dracula, GitHub Light, Nord, Solarized |

**步骤 2 — 编写 Mermaid 代码**

遵循以下规范：

```
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1a1b26', 'primaryTextColor': '#a9b1d6', 'primaryBorderColor': '#7aa2f7', 'lineColor': '#565f89', 'secondaryColor': '#24283b', 'tertiaryColor': '#1a1b26' }}}%%
flowchart TD
    A[开始] --> B{条件判断}
    B -->|是| C[执行操作]
    B -->|否| D[跳过]
    C --> E[结束]
    D --> E
```

**步骤 3 — 渲染（如需 SVG/PNG）**

将 Mermaid 代码写入临时 `.mmd` 文件，然后调用：

```bash
mmdc -i diagram.mmd -o diagram.svg -t dark -b transparent
mmdc -i diagram.mmd -o diagram.png -t dark -b white -w 1200
```

**步骤 4 — 输出**

返回渲染后的文件路径，或直接在消息中嵌入 Mermaid 代码块。

---

### Workflow 2: 批量图表生成

适用于一次性生成多张相关图表（如系统设计文档）。

**步骤 1** — 收集所有图表的需求列表

**步骤 2** — 创建批处理配置：

```json
{
  "theme": "tokyo-night",
  "outputDir": "./diagrams",
  "outputFormat": "svg",
  "diagrams": [
    {
      "name": "system-overview",
      "type": "C4Context",
      "title": "系统总览"
    },
    {
      "name": "api-sequence",
      "type": "sequenceDiagram",
      "title": "API 调用时序"
    },
    {
      "name": "data-model",
      "type": "erDiagram",
      "title": "数据模型"
    }
  ]
}
```

**步骤 3** — 逐一生成 Mermaid 代码并渲染

**步骤 4** — 返回所有图表的路径和预览

---

### Workflow 3: ASCII 图表输出

当用户需要纯文本图表（用于 CLI、日志、README 等场景）：

**方法 A — 手动 ASCII 绘制**

对于简单图表，直接使用字符绘制：

```
┌─────────┐     ┌─────────┐     ┌─────────┐
│  Client  │────▶│  Server │────▶│Database │
└─────────┘     └─────────┘     └─────────┘
                     │
                     ▼
                ┌─────────┐
                │  Cache  │
                └─────────┘
```

**方法 B — 使用 graph-easy 转换**

```bash
echo "[ Client ] -> [ Server ] -> [ Database ]" | graph-easy --as=ascii
```

---

## 主题配置

### Tokyo Night（默认）

```
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#1a1b26',
  'primaryTextColor': '#a9b1d6',
  'primaryBorderColor': '#7aa2f7',
  'lineColor': '#565f89',
  'secondaryColor': '#24283b',
  'tertiaryColor': '#414868',
  'noteBkgColor': '#1a1b26',
  'noteTextColor': '#c0caf5',
  'noteBorderColor': '#7aa2f7'
}}}%%
```

### Dracula

```
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#282a36',
  'primaryTextColor': '#f8f8f2',
  'primaryBorderColor': '#bd93f9',
  'lineColor': '#6272a4',
  'secondaryColor': '#44475a',
  'tertiaryColor': '#383a59',
  'noteBkgColor': '#282a36',
  'noteTextColor': '#f8f8f2',
  'noteBorderColor': '#ff79c6'
}}}%%
```

### GitHub Light

```
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#ffffff',
  'primaryTextColor': '#24292f',
  'primaryBorderColor': '#d0d7de',
  'lineColor': '#656d76',
  'secondaryColor': '#f6f8fa',
  'tertiaryColor': '#eaeef2',
  'noteBkgColor': '#ddf4ff',
  'noteTextColor': '#24292f',
  'noteBorderColor': '#54aeff'
}}}%%
```

### Nord

```
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#2e3440',
  'primaryTextColor': '#eceff4',
  'primaryBorderColor': '#88c0d0',
  'lineColor': '#4c566a',
  'secondaryColor': '#3b4252',
  'tertiaryColor': '#434c5e',
  'noteBkgColor': '#2e3440',
  'noteTextColor': '#eceff4',
  'noteBorderColor': '#81a1c1'
}}}%%
```

### Solarized

```
%%{init: {'theme': 'base', 'themeVariables': {
  'primaryColor': '#002b36',
  'primaryTextColor': '#839496',
  'primaryBorderColor': '#268bd2',
  'lineColor': '#586e75',
  'secondaryColor': '#073642',
  'tertiaryColor': '#073642',
  'noteBkgColor': '#002b36',
  'noteTextColor': '#93a1a1',
  'noteBorderColor': '#2aa198'
}}}%%
```

---

## 模板库

### 模板 1: 微服务架构（C4 Container）

```mermaid
C4Container
    title 微服务系统架构

    Person(user, "用户", "通过浏览器或移动端访问系统")

    System_Boundary(system, "核心系统") {
        Container(web, "Web 前端", "React", "用户界面")
        Container(gateway, "API 网关", "Kong/Nginx", "路由、限流、鉴权")
        Container(auth, "认证服务", "Go", "JWT 签发与验证")
        Container(biz, "业务服务", "Python", "核心业务逻辑")
        Container(notify, "通知服务", "Node.js", "邮件/短信/推送")
        ContainerDb(db, "主数据库", "PostgreSQL", "业务数据存储")
        ContainerDb(cache, "缓存", "Redis", "会话与热数据缓存")
        ContainerQueue(mq, "消息队列", "RabbitMQ", "异步任务处理")
    }

    Rel(user, web, "使用", "HTTPS")
    Rel(web, gateway, "API 调用", "HTTPS")
    Rel(gateway, auth, "鉴权", "gRPC")
    Rel(gateway, biz, "业务请求", "gRPC")
    Rel(biz, db, "读写", "TCP")
    Rel(biz, cache, "缓存", "TCP")
    Rel(biz, mq, "发布消息", "AMQP")
    Rel(mq, notify, "消费消息", "AMQP")
```

### 模板 2: CI/CD 流水线（Flowchart）

```mermaid
flowchart LR
    A[代码提交] --> B[触发 CI]
    B --> C{代码检查}
    C -->|通过| D[单元测试]
    C -->|失败| Z[通知开发者]
    D -->|通过| E[构建镜像]
    D -->|失败| Z
    E --> F[推送镜像仓库]
    F --> G{环境选择}
    G -->|Staging| H[部署 Staging]
    G -->|Production| I[人工审批]
    I -->|批准| J[蓝绿部署]
    I -->|拒绝| Z
    H --> K[集成测试]
    K -->|通过| L[✅ Staging 就绪]
    K -->|失败| M[自动回滚]
    J --> N[健康检查]
    N -->|通过| O[✅ 上线成功]
    N -->|失败| P[自动回滚]
```

### 模板 3: 用户认证时序（Sequence）

```mermaid
sequenceDiagram
    actor U as 用户
    participant C as 客户端
    participant G as API 网关
    participant A as 认证服务
    participant D as 数据库

    U->>C: 输入用户名/密码
    C->>G: POST /api/auth/login
    G->>A: 转发认证请求
    A->>D: 查询用户记录
    D-->>A: 返回用户数据
    alt 密码正确
        A->>A: 生成 JWT Token
        A-->>G: 200 OK + Token
        G-->>C: 返回 Token
        C->>C: 存储 Token
        C-->>U: 登录成功
    else 密码错误
        A-->>G: 401 Unauthorized
        G-->>C: 认证失败
        C-->>U: 提示错误
    end

    Note over C,G: 后续请求携带 Token
    U->>C: 访问受保护资源
    C->>G: GET /api/data (Bearer Token)
    G->>A: 验证 Token
    A-->>G: Token 有效
    G->>G: 转发到业务服务
```

### 模板 4: 数据库 ER 图

```mermaid
erDiagram
    USER ||--o{ ORDER : places
    USER {
        int id PK
        string username UK
        string email UK
        string password_hash
        datetime created_at
        datetime updated_at
    }
    ORDER ||--|{ ORDER_ITEM : contains
    ORDER {
        int id PK
        int user_id FK
        decimal total_amount
        string status
        datetime created_at
    }
    ORDER_ITEM }|--|| PRODUCT : references
    ORDER_ITEM {
        int id PK
        int order_id FK
        int product_id FK
        int quantity
        decimal unit_price
    }
    PRODUCT ||--o{ PRODUCT_TAG : has
    PRODUCT {
        int id PK
        string name
        text description
        decimal price
        int stock
    }
    TAG ||--o{ PRODUCT_TAG : applied
    TAG {
        int id PK
        string name UK
    }
    PRODUCT_TAG {
        int product_id FK
        int tag_id FK
    }
```

### 模板 5: 项目甘特图

```mermaid
gantt
    title 项目开发排期
    dateFormat YYYY-MM-DD
    axisFormat %m/%d

    section 需求阶段
    需求调研           :done,    req1, 2025-01-01, 7d
    需求评审           :done,    req2, after req1, 3d
    PRD 定稿           :done,    req3, after req2, 2d

    section 设计阶段
    技术方案设计        :active,  des1, after req3, 5d
    UI/UX 设计         :         des2, after req3, 7d
    设计评审           :         des3, after des2, 2d

    section 开发阶段
    后端开发           :         dev1, after des1, 15d
    前端开发           :         dev2, after des3, 15d
    联调测试           :         dev3, after dev1, 5d

    section 发布阶段
    UAT 测试           :         rel1, after dev3, 5d
    修复 Bug           :         rel2, after rel1, 3d
    正式发布           :milestone, rel3, after rel2, 0d
```

---

## Output Format

### 默认输出

直接在回复中使用 Mermaid 代码块：

````
```mermaid
flowchart TD
    A --> B
```
````

### SVG 文件

```bash
mmdc -i input.mmd -o output.svg -t dark -b transparent --cssFile custom.css
```

### PNG 文件

```bash
mmdc -i input.mmd -o output.png -t dark -b white -w 1920 -H 1080 -s 2
```

参数说明：
- `-t` 主题：default, dark, forest, neutral
- `-b` 背景色：transparent, white, #hex
- `-w` 宽度（像素）
- `-H` 高度（像素）
- `-s` 缩放倍数

### ASCII 文本

直接使用字符绘制，适合嵌入代码注释、终端输出、纯文本文档。

---

## Common Pitfalls

### 1. 节点 ID 冲突

**错误**：在同一图表中使用重复的节点 ID

```mermaid
flowchart TD
    A[登录] --> B[验证]
    A[注册] --> B[检查]
```

**正确**：使用唯一 ID

```mermaid
flowchart TD
    login[登录] --> validate[验证]
    register[注册] --> check[检查]
```

### 2. 特殊字符未转义

**错误**：节点文本包含括号、引号等

```
A[用户输入(name)]
```

**正确**：使用引号包裹

```
A["用户输入(name)"]
```

### 3. 图表过于复杂

单张图表超过 20 个节点时，考虑拆分为多张子图：

```mermaid
flowchart TD
    subgraph 前端
        A --> B --> C
    end
    subgraph 后端
        D --> E --> F
    end
    C --> D
```

### 4. 方向选择不当

- 层级结构（组织架构、决策树）→ **TD**（上到下）
- 流程/管道 → **LR**（左到右）
- 时间线/历史 → **LR**
- 请求-响应 → **TD** 或 **LR**

### 5. 中文乱码

渲染 SVG/PNG 时如果出现中文乱码，使用 `--cssFile` 指定字体：

```css
* {
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
}
```

### 6. Mermaid CLI 超时

大型图表可能渲染超时，增加超时参数：

```bash
mmdc -i large-diagram.mmd -o output.svg --puppeteerConfigFile puppeteer.json
```

`puppeteer.json`:
```json
{
  "timeout": 60000
}
```

### 7. 子图嵌套限制

Mermaid 支持子图嵌套，但过深的嵌套（超过 3 层）可能导致渲染异常。保持层级扁平化。

---

## 最佳实践

1. **命名规范** — 节点 ID 使用有意义的英文命名，显示文本使用中文
2. **布局控制** — 善用 `subgraph` 对节点分组，改善布局
3. **颜色语义** — 绿色表示成功、红色表示失败、蓝色表示进行中
4. **注释标注** — 使用 `Note` 添加关键说明
5. **渐进呈现** — 复杂系统先画总览再画细节，用 C4 的 Context → Container → Component 层级
6. **版本控制** — Mermaid 代码是纯文本，适合纳入 Git 管理
7. **一致性** — 同一文档中的所有图表使用相同主题配置

---

## EXTEND.md 扩展

用户可在技能同目录下创建 `EXTEND.md` 添加自定义主题和模板，Agent 会自动合并使用。


# Finance Service

Project Eagle 的财务微服务，提供月度损益表计算功能。

## 技术栈

- Go 1.21
- gRPC
- StarRocks (通过 MySQL 协议连接)

## 目录结构

```
finance-svc/
├── cmd/server/          # 服务入口
├── internal/
│   ├── model/           # 数据模型
│   ├── repository/      # StarRocks 数据访问
│   ├── service/         # 业务逻辑
│   └── handler/         # gRPC handler
├── proto/               # Protobuf 定义
└── scripts/             # 工具脚本
```

## 快速开始

### 1. 环境要求

- Go 1.21+
- protoc (Protocol Buffers 编译器)
- protoc-gen-go 和 protoc-gen-go-grpc

### 2. 生成 protobuf 代码

```bash
# Windows
./scripts/generate_protos.ps1

# Linux/Mac
protoc --proto_path=proto \
       --go_out=proto/finance/v1 \
       --go_opt=paths=source_relative \
       --go-grpc_out=proto/finance/v1 \
       --go-grpc_opt=paths=source_relative \
       finance.proto
```

### 3. 配置环境变量

```bash
export STARROCKS_HOST=localhost
export STARROCKS_USER=root
export STARROCKS_PASSWORD=
export STARROCKS_DATABASE=finance
export GRPC_PORT=50051
```

### 4. 运行服务

```bash
go run ./cmd/server
```

### 5. 测试

```bash
go test ./internal/service/...
```

## API 接口

### GetMonthlyProfitLoss

获取月度损益表

**请求参数:**
- `tenant_id` (string): 租户ID
- `year` (int32): 年份
- `month` (int32): 月份

**响应:**
- 返回包含收入、支出、利润等详细数据的月度损益表

## 数据库表结构

### monthly_revenue_detail (月度收入明细)
```sql
CREATE TABLE monthly_revenue_detail (
    tenant_id VARCHAR(64),
    year INT,
    month INT,
    category VARCHAR(32), -- sales, service, other
    amount DECIMAL(18,2),
    created_at DATETIME
) ENGINE=OLAP
DUPLICATE KEY(tenant_id, year, month, category)
DISTRIBUTED BY HASH(tenant_id) BUCKETS 10;
```

### monthly_expense_detail (月度支出明细)
```sql
CREATE TABLE monthly_expense_detail (
    tenant_id VARCHAR(64),
    year INT,
    month INT,
    category VARCHAR(32), -- cogs, operating, administrative, financial, other
    amount DECIMAL(18,2),
    created_at DATETIME
) ENGINE=OLAP
DUPLICATE KEY(tenant_id, year, month, category)
DISTRIBUTED BY HASH(tenant_id) BUCKETS 10;
```

## 部署

### Docker

```bash
docker build -t finance-svc .
docker run -p 50051:50051 finance-svc
```

## 开发指南

1. 修改 proto 文件后，重新生成代码
2. 添加新的业务逻辑在 service 层
3. 数据库查询在 repository 层
4. gRPC 接口在 handler 层
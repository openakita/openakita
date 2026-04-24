# 生产部署

## 概述

本页面介绍如何在服务器上正式部署 OpenAkita。适用于需要长期运行、多人访问、高可用的场景。

::: tip 快速选择
- **个人本地使用** → 无需看本页，直接桌面应用或 `openakita` 启动即可
- **团队局域网共享** → 参考 [多端访问](/network/multi-access) 的场景 2
- **正式生产环境** → 继续阅读本页
:::

## Docker Compose 部署

推荐使用 Docker Compose 一键部署所有服务。

### docker-compose.yml 示例

```yaml
services:
  openakita:
    image: openakita/openakita:latest
    container_name: openakita
    restart: unless-stopped
    ports:
      - "18900:18900"
    volumes:
      - ./data:/app/data
      - ./identity:/app/identity
      - ./skills:/app/skills
    env_file:
      - .env
    environment:
      - API_HOST=0.0.0.0
      - API_PORT=18900
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:18900/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

### 启动

```bash
docker compose up -d
```

### 查看日志

```bash
docker compose logs -f openakita
```

## 环境变量清单

在 `.env` 文件中配置以下变量：

### 必需

| 变量 | 说明 | 示例 |
|------|------|------|
| `LLM_API_KEY` | LLM 供应商的 API Key | `sk-...` |
| `LLM_MODEL` | 默认使用的模型 | `claude-sonnet-4-20250514` |

### 网络

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `API_HOST` | `0.0.0.0` | 生产环境应设为 `0.0.0.0` |
| `API_PORT` | `18900` | 服务端口 |
| `TRUST_PROXY` | `true` | 反向代理后面必须开启 |
| `CORS_ORIGINS` | — | 允许的跨域来源 |
| `WEB_PASSWORD` | — | Web 访问密码 |

### IM 通道

| 变量 | 说明 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `FEISHU_APP_ID` | 飞书 App ID |
| `FEISHU_APP_SECRET` | 飞书 App Secret |
| `DINGTALK_CLIENT_ID` | 钉钉 Client ID |
| `DINGTALK_CLIENT_SECRET` | 钉钉 Client Secret |

仅填写你需要使用的通道。

## 数据持久化

以下目录包含重要数据，**必须挂载为 Volume**：

| 目录 | 内容 | 丢失后果 |
|------|------|---------|
| `data/` | 配置、记忆、会话历史、运行时状态 | 所有数据丢失 |
| `identity/` | SOUL.md、AGENT.md 等身份文件 | Agent 人格丢失 |
| `skills/` | 自定义技能文件 | 自定义技能丢失 |

::: warning 备份提醒
定期备份 `data/` 目录。推荐使用 cron 定时任务自动备份到外部存储。
:::

## 监控

### Prometheus + Grafana

OpenAkita 暴露 `/metrics` 端点，兼容 Prometheus 格式。

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'openakita'
    static_configs:
      - targets: ['openakita:18900']
    metrics_path: /metrics
    scrape_interval: 30s
```

可监控的指标：

| 指标 | 说明 |
|------|------|
| `openakita_requests_total` | API 请求总数 |
| `openakita_llm_tokens_total` | LLM Token 消耗总量 |
| `openakita_llm_latency_seconds` | LLM 请求延迟 |
| `openakita_active_sessions` | 当前活跃会话数 |
| `openakita_tool_calls_total` | 工具调用总数 |

Grafana Dashboard 可通过导入 `monitoring/grafana/dashboards.yml` 快速配置。

## 日志

### 日志级别

通过环境变量或 [Agent 配置](/web/#/config/agent) 设置：

```bash
LOG_LEVEL=info  # debug / info / warning / error
```

### 日志输出

| 输出 | 说明 |
|------|------|
| stdout | 默认输出到标准输出，Docker 自动收集 |
| 文件 | `data/logs/` 目录，按日期轮转 |

生产环境推荐配合日志收集工具（如 Loki、ELK）统一管理。

## 备份策略

### 自动备份脚本

```bash
#!/bin/bash
BACKUP_DIR="/backups/openakita"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"
tar czf "$BACKUP_DIR/openakita_$TIMESTAMP.tar.gz" \
    data/ identity/ skills/ .env

find "$BACKUP_DIR" -name "*.tar.gz" -mtime +30 -delete
```

### 配合 cron 定时执行

```bash
# 每天凌晨 3 点自动备份
0 3 * * * /opt/openakita/backup.sh
```

### 恢复

```bash
tar xzf openakita_20260318_030000.tar.gz -C /opt/openakita/
docker compose restart
```

## 安全加固

### 必做

- [ ] 设置 **Web 访问密码**
- [ ] 启用 **HTTPS**（通过反向代理）
- [ ] 配置 `CORS_ORIGINS` 限制跨域
- [ ] 设置 `TRUST_PROXY=true`

### 推荐

- [ ] 限制服务器安全组，仅开放 80/443 端口
- [ ] 使用非 root 用户运行容器
- [ ] 定期更新镜像 `docker compose pull && docker compose up -d`
- [ ] 配置防火墙规则（iptables / ufw）
- [ ] 启用 fail2ban 防暴力破解

### 反向代理配置

参考 [多端访问指南](/network/multi-access) 中的 Nginx / Caddy 配置示例。

## 相关页面

- [多端访问指南](/network/multi-access) — 各场景的访问配置
- [网络基础科普](/network/basics) — 网络概念入门
- [高级设置](/advanced/advanced) — 网络与安全配置项
- [CLI 命令参考](/advanced/cli) — `openakita serve` 等服务端命令


#!/bin/bash

# OpenAkita 私有化部署 - 一键部署脚本
# 用途：自动化完成环境检查、配置生成、服务启动和健康检查
# 版本：v1.0.0
# 更新日期：2026-03-11
# 使用方式：./deploy.sh [init|start|stop|restart|status|backup|restore]

set -e

# ==================== 颜色定义 ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ==================== 配置变量 ====================
PROJECT_NAME="openakita"
COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env"
BACKUP_DIR="./backups"
LOG_DIR="./logs"
DATA_DIR="./data"

# ==================== 帮助信息 ====================
show_help() {
    cat << EOF
${BLUE}OpenAkita 私有化部署脚本${NC}

用法：${YELLOW}./deploy.sh [命令]${NC}

命令:
  ${GREEN}init${NC}      初始化部署环境（生成配置、创建目录）
  ${GREEN}start${NC}     启动所有服务
  ${GREEN}stop${NC}      停止所有服务
  ${GREEN}restart${NC}   重启所有服务
  ${GREEN}status${NC}    查看服务状态
  ${GREEN}backup${NC}    备份数据库和 Redis 数据
  ${GREEN}restore${NC}   从备份恢复数据
  ${GREEN}logs${NC}      查看应用日志
  ${GREEN}help${NC}      显示此帮助信息

示例:
  ${YELLOW}./deploy.sh init${NC}      # 首次部署初始化
  ${YELLOW}./deploy.sh start${NC}     # 启动服务
  ${YELLOW}./deploy.sh backup${NC}    # 备份数据

EOF
}

# ==================== 日志函数 ====================
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ==================== 环境检查 ====================
check_prerequisites() {
    log_info "检查系统依赖..."
    
    # 检查 Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker 未安装，请先安装 Docker"
        exit 1
    fi
    
    # 检查 Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        log_error "Docker Compose 未安装，请先安装 Docker Compose"
        exit 1
    fi
    
    # 检查 Docker 版本
    DOCKER_VERSION=$(docker --version | cut -d' ' -f3 | cut -d',' -f1)
    log_info "Docker 版本：$DOCKER_VERSION"
    
    # 检查 Docker Compose 版本
    COMPOSE_VERSION=$(docker-compose --version | cut -d' ' -f4 | cut -d',' -f1)
    log_info "Docker Compose 版本：$COMPOSE_VERSION"
    
    log_success "系统依赖检查通过"
}

# ==================== 初始化环境 ====================
init_environment() {
    log_info "初始化部署环境..."
    
    # 创建必要目录
    log_info "创建目录结构..."
    mkdir -p $BACKUP_DIR/db
    mkdir -p $BACKUP_DIR/redis
    mkdir -p $LOG_DIR
    mkdir -p $DATA_DIR/app
    mkdir -p ./skills
    mkdir -p ./nginx/conf.d
    mkdir -p ./ssl
    mkdir -p ./monitoring/grafana/dashboards
    mkdir -p ./monitoring/grafana/datasources
    
    # 生成环境配置文件
    if [ ! -f "$ENV_FILE" ]; then
        log_info "生成环境配置文件..."
        generate_env_file
        log_warning "请编辑 .env 文件配置密码和 API 密钥"
    else
        log_info "环境配置文件已存在"
    fi
    
    # 生成 Nginx 配置
    if [ ! -f "./nginx/nginx.conf" ]; then
        log_info "生成 Nginx 配置文件..."
        generate_nginx_config
    fi
    
    # 生成 Prometheus 配置
    if [ ! -f "./monitoring/prometheus.yml" ]; then
        log_info "生成 Prometheus 配置文件..."
        generate_prometheus_config
    fi
    
    # 设置权限
    chmod 600 $ENV_FILE
    chmod +x deploy.sh
    
    log_success "环境初始化完成"
    echo ""
    log_info "下一步:"
    echo "  1. 编辑 .env 文件，配置密码和 API 密钥"
    echo "  2. 运行 ${YELLOW}./deploy.sh start${NC} 启动服务"
}

# ==================== 生成环境配置文件 ====================
generate_env_file() {
    cat > $ENV_FILE << 'EOF'
# ==================== OpenAkita 环境配置 ====================
# 请根据实际需求修改以下配置
# 生成时间：2026-03-11

# ==================== 应用配置 ====================
NODE_ENV=production
APP_PORT=3000
LOG_LEVEL=info
MAX_CONCURRENT_AGENTS=10

# ==================== 数据库配置 ====================
DB_USER=openakita
DB_PASSWORD=CHANGE_ME_STRONG_PASSWORD_123
DB_NAME=openakita
DB_PORT=5432

# ==================== Redis 配置 ====================
REDIS_PASSWORD=CHANGE_ME_REDIS_PASSWORD_456
REDIS_PORT=6379

# ==================== 安全配置 ====================
# 请生成强随机字符串作为 JWT_SECRET
# 可使用：openssl rand -hex 32
JWT_SECRET=CHANGE_ME_JWT_SECRET_KEY_789

# ==================== LLM 配置 ====================
# 支持的提供商：anthropic, openai, azure, local
LLM_PROVIDER=anthropic
LLM_API_KEY=CHANGE_ME_YOUR_LLM_API_KEY

# ==================== 监控配置（可选） ====================
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=CHANGE_ME_GRAFANA_PASSWORD

# ==================== 端口配置 ====================
PROMETHEUS_PORT=9090
GRAFANA_PORT=3001

EOF
    log_info "环境配置文件已生成：$ENV_FILE"
}

# ==================== 生成 Nginx 配置 ====================
generate_nginx_config() {
    cat > ./nginx/nginx.conf << 'EOF'
user  nginx;
worker_processes  auto;

error_log  /var/log/nginx/error.log warn;
pid        /var/run/nginx.pid;

events {
    worker_connections  1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    log_format  main  '$remote_addr - $remote_user [$time_local] "$request" '
                      '$status $body_bytes_sent "$http_referer" '
                      '"$http_user_agent" "$http_x_forwarded_for"';

    access_log  /var/log/nginx/access.log  main;

    sendfile        on;
    keepalive_timeout  65;

    # Gzip 压缩
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;

    include /etc/nginx/conf.d/*.conf;
}
EOF

    cat > ./nginx/conf.d/app.conf << 'EOF'
upstream openakita_app {
    server app:3000;
}

server {
    listen 80;
    server_name localhost;

    # 生产环境建议启用 HTTPS，取消下面 HTTPS 配置的注释
    # return 301 https://$server_name$request_uri;

    location / {
        proxy_pass http://openakita_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 90;
    }

    # 健康检查端点
    location /health {
        proxy_pass http://openakita_app/health;
        access_log off;
    }

    # 静态资源（如有）
    location /static {
        alias /usr/share/nginx/html/static;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}

# HTTPS 配置示例（生产环境启用）
# server {
#     listen 443 ssl http2;
#     server_name localhost;
#
#     ssl_certificate /etc/nginx/ssl/fullchain.pem;
#     ssl_certificate_key /etc/nginx/ssl/privkey.pem;
#     ssl_session_timeout 1d;
#     ssl_session_cache shared:SSL:50m;
#     ssl_session_tickets off;
#
#     ssl_protocols TLSv1.2 TLSv1.3;
#     ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
#     ssl_prefer_server_ciphers off;
#
#     add_header Strict-Transport-Security "max-age=63072000" always;
#
#     location / {
#         proxy_pass http://openakita_app;
#         # ... 其他代理配置同上
#     }
# }
EOF
    log_info "Nginx 配置文件已生成"
}

# ==================== 生成 Prometheus 配置 ====================
generate_prometheus_config() {
    cat > ./monitoring/prometheus.yml << 'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'openakita-app'
    static_configs:
      - targets: ['app:3000']
    metrics_path: '/metrics'
    scrape_interval: 30s

  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
EOF
    log_info "Prometheus 配置文件已生成"
}

# ==================== 启动服务 ====================
start_services() {
    log_info "启动 OpenAkita 服务..."
    
    # 检查环境配置
    if [ ! -f "$ENV_FILE" ]; then
        log_error "环境配置文件不存在，请先运行 ${YELLOW}./deploy.sh init${NC}"
        exit 1
    fi
    
    # 检查是否修改了默认密码
    if grep -q "CHANGE_ME" $ENV_FILE; then
        log_warning "检测到默认密码未修改，生产环境请务必修改 .env 中的密码！"
        read -p "是否继续启动？(y/N): " confirm
        if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
            log_info "已取消启动"
            exit 0
        fi
    fi
    
    # 启动服务
    docker-compose -f $COMPOSE_FILE up -d
    
    # 等待服务启动
    log_info "等待服务启动..."
    sleep 10
    
    # 健康检查
    check_health
    
    log_success "服务启动完成"
    show_status
}

# ==================== 停止服务 ====================
stop_services() {
    log_info "停止 OpenAkita 服务..."
    docker-compose -f $COMPOSE_FILE down
    log_success "服务已停止"
}

# ==================== 重启服务 ====================
restart_services() {
    log_info "重启 OpenAkita 服务..."
    stop_services
    sleep 2
    start_services
}

# ==================== 查看状态 ====================
show_status() {
    echo ""
    log_info "服务状态:"
    docker-compose -f $COMPOSE_FILE ps
    echo ""
    
    # 显示访问地址
    log_info "访问地址:"
    echo "  应用服务：http://localhost:3000"
    echo "  数据库：localhost:5432"
    echo "  Redis: localhost:6379"
    echo "  Grafana: http://localhost:3001 (如启用监控)"
    echo "  Prometheus: http://localhost:9090 (如启用监控)"
}

# ==================== 健康检查 ====================
check_health() {
    log_info "执行健康检查..."
    
    # 检查应用服务
    if curl -f -s http://localhost:3000/health > /dev/null 2>&1; then
        log_success "应用服务健康检查通过"
    else
        log_warning "应用服务健康检查失败，等待重试..."
        sleep 5
        if curl -f -s http://localhost:3000/health > /dev/null 2>&1; then
            log_success "应用服务健康检查通过（重试后）"
        else
            log_error "应用服务健康检查失败，请查看日志：./deploy.sh logs"
        fi
    fi
    
    # 检查数据库
    if docker exec openakita-db pg_isready -U openakita > /dev/null 2>&1; then
        log_success "数据库健康检查通过"
    else
        log_warning "数据库健康检查失败"
    fi
    
    # 检查 Redis
    if docker exec openakita-redis redis-cli ping > /dev/null 2>&1; then
        log_success "Redis 健康检查通过"
    else
        log_warning "Redis 健康检查失败"
    fi
}

# ==================== 备份数据 ====================
backup_data() {
    log_info "开始备份数据..."
    
    # 创建备份目录
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_PATH="$BACKUP_DIR/$TIMESTAMP"
    mkdir -p $BACKUP_PATH
    
    # 备份数据库
    log_info "备份数据库..."
    docker exec openakita-db pg_dump -U openakita openakita > "$BACKUP_PATH/db_backup.sql"
    
    # 备份 Redis
    log_info "备份 Redis..."
    docker exec openakita-redis redis-cli --pass "$(grep REDIS_PASSWORD $ENV_FILE | cut -d'=' -f2)" BGSAVE
    sleep 5
    cp -r ./backups/redis/* "$BACKUP_PATH/" 2>/dev/null || true
    
    # 备份环境变量（不含敏感信息）
    log_info "备份配置..."
    cp $ENV_FILE "$BACKUP_PATH/env_backup.txt"
    
    # 压缩备份
    log_info "压缩备份文件..."
    tar -czf "$BACKUP_PATH.tar.gz" -C "$BACKUP_DIR" "$TIMESTAMP"
    rm -rf "$BACKUP_PATH"
    
    log_success "备份完成：$BACKUP_PATH.tar.gz"
}

# ==================== 恢复数据 ====================
restore_data() {
    log_info "恢复数据..."
    
    # 列出可用备份
    log_info "可用备份:"
    ls -lh $BACKUP_DIR/*.tar.gz 2>/dev/null || {
        log_error "未找到备份文件"
        exit 1
    }
    
    echo ""
    read -p "请输入要恢复的备份文件名（不含路径）: " BACKUP_FILE
    
    if [ ! -f "$BACKUP_DIR/$BACKUP_FILE" ]; then
        log_error "备份文件不存在"
        exit 1
    fi
    
    # 解压备份
    log_info "解压备份文件..."
    tar -xzf "$BACKUP_DIR/$BACKUP_FILE" -C $BACKUP_DIR
    
    # 恢复数据库
    log_info "恢复数据库..."
    BACKUP_NAME=$(basename "$BACKUP_FILE" .tar.gz)
    docker exec -i openakita-db psql -U openakita -d openakita < "$BACKUP_DIR/$BACKUP_NAME/db_backup.sql"
    
    log_success "数据恢复完成"
    log_warning "请重启服务使更改生效：./deploy.sh restart"
}

# ==================== 查看日志 ====================
show_logs() {
    log_info "查看应用日志..."
    docker-compose -f $COMPOSE_FILE logs -f app
}

# ==================== 主函数 ====================
main() {
    case "${1:-help}" in
        init)
            check_prerequisites
            init_environment
            ;;
        start)
            check_prerequisites
            start_services
            ;;
        stop)
            stop_services
            ;;
        restart)
            restart_services
            ;;
        status)
            show_status
            ;;
        backup)
            backup_data
            ;;
        restore)
            restore_data
            ;;
        logs)
            show_logs
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "未知命令：$1"
            show_help
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"

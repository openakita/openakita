#!/bin/bash
# OpenAkita MVP 一键部署脚本
# 用途：全新服务器上一键部署完整系统
# 使用：curl -sSL https://raw.githubusercontent.com/your-repo/install.sh | bash
# 或：bash install.sh
# 版本：v1.0 | 日期：2026-03-11

set -e

# ── 颜色定义 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ── 配置变量 ──
INSTALL_DIR="${INSTALL_DIR:-/opt/openakita}"
DOCKER_COMPOSE_VERSION="2.24.0"
REGISTRY="${REGISTRY:-openakita}"
VERSION="${VERSION:-latest}"

# ── 日志函数 ──
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── 检查 root 权限 ──
check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "请使用 root 权限运行：sudo bash $0"
        exit 1
    fi
}

# ── 检查系统要求 ──
check_requirements() {
    log_info "检查系统要求..."
    
    # 检查操作系统
    if [ ! -f /etc/os-release ]; then
        log_error "无法识别操作系统"
        exit 1
    fi
    
    source /etc/os-release
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" && "$ID" != "centos" && "$ID" != "rhel" ]]; then
        log_warning "未测试的操作系统：$ID，可能不兼容"
    fi
    
    # 检查内存（至少 4GB）
    MEM_TOTAL=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    MEM_GB=$((MEM_TOTAL / 1024 / 1024))
    if [ $MEM_GB -lt 4 ]; then
        log_error "内存不足：当前 ${MEM_GB}GB，至少需要 4GB"
        exit 1
    fi
    log_success "内存检查通过：${MEM_GB}GB"
    
    # 检查磁盘空间（至少 20GB）
    DISK_AVAILABLE=$(df -P / | tail -1 | awk '{print $4}')
    DISK_GB=$((DISK_AVAILABLE / 1024 / 1024))
    if [ $DISK_GB -lt 20 ]; then
        log_error "磁盘空间不足：当前 ${DISK_GB}GB，至少需要 20GB"
        exit 1
    fi
    log_success "磁盘空间检查通过：${DISK_GB}GB 可用"
    
    # 检查 Docker 是否已安装
    if command -v docker &> /dev/null; then
        DOCKER_VERSION=$(docker --version | cut -d' ' -f3)
        log_success "Docker 已安装：$DOCKER_VERSION"
    else
        log_warning "Docker 未安装，将自动安装"
    fi
    
    # 检查 Docker Compose 是否已安装
    if command -v docker-compose &> /dev/null || docker compose version &> /dev/null 2>&1; then
        COMPOSE_VERSION=$(docker compose version 2>&1 | cut -d' ' -f4)
        log_success "Docker Compose 已安装：$COMPOSE_VERSION"
    else
        log_warning "Docker Compose 未安装，将自动安装"
    fi
}

# ── 安装 Docker ──
install_docker() {
    if command -v docker &> /dev/null; then
        log_info "Docker 已安装，跳过"
        return
    fi
    
    log_info "安装 Docker..."
    
    # Ubuntu/Debian
    if [[ "$ID" == "ubuntu" || "$ID" == "debian" ]]; then
        apt-get update
        apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release
        
        curl -fsSL https://download.docker.com/linux/$ID/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
        
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] \
            https://download.docker.com/linux/$ID $(lsb_release -cs) stable" | \
            tee /etc/apt/sources.list.d/docker.list > /dev/null
        
        apt-get update
        apt-get install -y docker-ce docker-ce-cli containerd.io
    
    # CentOS/RHEL
    elif [[ "$ID" == "centos" || "$ID" == "rhel" ]]; then
        yum install -y yum-utils
        yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        yum install -y docker-ce docker-ce-cli containerd.io
    fi
    
    systemctl start docker
    systemctl enable docker
    
    log_success "Docker 安装完成"
}

# ── 安装 Docker Compose ──
install_docker_compose() {
    if docker compose version &> /dev/null 2>&1; then
        log_info "Docker Compose 已安装，跳过"
        return
    fi
    
    log_info "安装 Docker Compose v$DOCKER_COMPOSE_VERSION..."
    
    curl -L "https://github.com/docker/compose/releases/download/v$DOCKER_COMPOSE_VERSION/docker-compose-$(uname -s)-$(uname -m)" \
        -o /usr/local/bin/docker-compose
    
    chmod +x /usr/local/bin/docker-compose
    
    # 创建软链接
    ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose
    
    log_success "Docker Compose 安装完成"
}

# ── 下载项目文件 ──
download_project() {
    log_info "下载项目文件到 $INSTALL_DIR..."
    
    mkdir -p $INSTALL_DIR
    cd $INSTALL_DIR
    
    # 从 GitHub 下载（或从本地复制）
    if [ -f /tmp/openakita-deploy.tar.gz ]; then
        tar -xzf /tmp/openakita-deploy.tar.gz -C $INSTALL_DIR --strip-components=1
    else
        # 从 GitHub release 下载
        curl -sSL "https://github.com/your-repo/openakita/releases/download/$VERSION/openakita-$VERSION.tar.gz" \
            -o /tmp/openakita.tar.gz
        tar -xzf /tmp/openakita.tar.gz -C $INSTALL_DIR --strip-components=1
    fi
    
    log_success "项目文件下载完成"
}

# ── 生成环境配置 ──
generate_env() {
    log_info "生成环境配置..."
    
    cd $INSTALL_DIR
    
    # 复制示例配置
    cp .env.example .env
    
    # 生成随机密钥
    SECRET_KEY=$(openssl rand -hex 32)
    JWT_SECRET=$(openssl rand -hex 32)
    DB_PASSWORD=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 20)
    GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9' | head -c 12)
    
    # 替换配置
    sed -i "s/SECRET_KEY=change_me/SECRET_KEY=$SECRET_KEY/" .env
    sed -i "s/JWT_SECRET=change_me/JWT_SECRET=$JWT_SECRET/" .env
    sed -i "s/DB_PASSWORD=change_me/DB_PASSWORD=$DB_PASSWORD/" .env
    sed -i "s/GRAFANA_ADMIN_PASSWORD=change_me/GRAFANA_ADMIN_PASSWORD=$GRAFANA_ADMIN_PASSWORD/" .env
    
    log_success "环境配置生成完成"
    
    # 显示重要信息
    echo ""
    log_warning "请保存以下重要信息："
    echo -e "${YELLOW}数据库密码：$DB_PASSWORD${NC}"
    echo -e "${YELLOW}Grafana 密码：$GRAFANA_ADMIN_PASSWORD${NC}"
    echo ""
    
    # 保存到安全文件
    cat > /root/openakita_credentials.txt << EOF
OpenAkita 部署凭证 - $(date)
================================
数据库密码：$DB_PASSWORD
Grafana 密码：$GRAFANA_ADMIN_PASSWORD
安装目录：$INSTALL_DIR
版本：$VERSION
EOF
    
    chmod 600 /root/openakita_credentials.txt
    log_info "凭证已保存到 /root/openakita_credentials.txt"
}

# ── 启动服务 ──
start_services() {
    log_info "启动 Docker 服务..."
    
    cd $INSTALL_DIR
    
    # 预拉取镜像
    log_info "预拉取 Docker 镜像（可能需要几分钟）..."
    docker-compose pull
    
    # 启动服务
    docker-compose up -d
    
    log_success "服务启动完成"
}

# ── 健康检查 ──
health_check() {
    log_info "执行健康检查..."
    
    sleep 10
    
    # 检查容器状态
    RUNNING=$(docker-compose ps | grep -c "Up" || true)
    if [ $RUNNING -lt 8 ]; then
        log_error "部分容器启动失败，当前运行：$RUNNING/10"
        docker-compose ps
        exit 1
    fi
    
    # 检查应用健康端点
    if curl -sf http://localhost:8000/health > /dev/null; then
        log_success "应用健康检查通过"
    else
        log_warning "应用健康检查失败，请稍后检查日志"
    fi
    
    # 检查数据库
    if docker exec openakita-db pg_isready -U postgres > /dev/null 2>&1; then
        log_success "数据库健康检查通过"
    else
        log_warning "数据库健康检查失败"
    fi
    
    log_success "健康检查完成"
}

# ── 显示完成信息 ──
show_completion() {
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  OpenAkita MVP 部署成功！${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "访问地址:"
    echo -e "  应用：${BLUE}http://$(hostname -I | awk '{print $1}'):8000${NC}"
    echo -e "  Grafana: ${BLUE}http://$(hostname -I | awk '{print $1}'):3000${NC}"
    echo -e "  Prometheus: ${BLUE}http://$(hostname -I | awk '{print $1}'):9090${NC}"
    echo ""
    echo "管理命令:"
    echo "  查看状态：cd $INSTALL_DIR && docker-compose ps"
    echo "  查看日志：cd $INSTALL_DIR && docker-compose logs -f"
    echo "  停止服务：cd $INSTALL_DIR && docker-compose down"
    echo "  重启服务：cd $INSTALL_DIR && docker-compose restart"
    echo ""
    echo "凭证文件：/root/openakita_credentials.txt"
    echo ""
    log_info "部署文档：$INSTALL_DIR/docs/deployment.md"
}

# ── 主流程 ──
main() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  OpenAkita MVP 一键部署脚本${NC}"
    echo -e "${BLUE}  版本：$VERSION${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    
    check_root
    check_requirements
    install_docker
    install_docker_compose
    download_project
    generate_env
    start_services
    health_check
    show_completion
}

# 执行
main "$@"

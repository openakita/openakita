#!/bin/bash
# OpenAkita MVP 部署脚本（离线包版本）
# 用途：无外网环境部署
# 使用：bash deploy.sh
# 版本：v1.0 | 日期：2026-03-11

set -e

# ── 配置 ──
INSTALL_DIR="${INSTALL_DIR:-/opt/openakita}"
OFFLINE_PACKAGE="${OFFLINE_PACKAGE:-/tmp/openakita-offline.tar.gz}"

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── 检查 root ──
if [ "$EUID" -ne 0 ]; then
    log_error "请使用 root 权限运行：sudo bash $0"
    exit 1
fi

# ── 检查离线包 ──
if [ ! -f "$OFFLINE_PACKAGE" ]; then
    log_error "离线包不存在：$OFFLINE_PACKAGE"
    exit 1
fi

log_info "开始离线部署..."

# ── 解压离线包 ──
mkdir -p $INSTALL_DIR
tar -xzf $OFFLINE_PACKAGE -C $INSTALL_DIR

cd $INSTALL_DIR

# ── 加载 Docker 镜像 ──
log_info "加载 Docker 镜像..."
if [ -f images.tar ]; then
    docker load -i images.tar
    log_success "镜像加载完成"
else
    log_error "镜像文件不存在：images.tar"
    exit 1
fi

# ── 生成配置 ──
log_info "生成环境配置..."
cp .env.example .env

SECRET_KEY=$(openssl rand -hex 32)
JWT_SECRET=$(openssl rand -hex 32)
DB_PASSWORD=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 20)
GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9' | head -c 12)

sed -i "s/SECRET_KEY=change_me/SECRET_KEY=$SECRET_KEY/" .env
sed -i "s/JWT_SECRET=change_me/JWT_SECRET=$JWT_SECRET/" .env
sed -i "s/DB_PASSWORD=change_me/DB_PASSWORD=$DB_PASSWORD/" .env
sed -i "s/GRAFANA_ADMIN_PASSWORD=change_me/GRAFANA_ADMIN_PASSWORD=$GRAFANA_ADMIN_PASSWORD/" .env

log_success "配置生成完成"

# ── 保存凭证 ──
cat > /root/openakita_credentials.txt << EOF
OpenAkita 部署凭证 - $(date)
================================
数据库密码：$DB_PASSWORD
Grafana 密码：$GRAFANA_ADMIN_PASSWORD
EOF
chmod 600 /root/openakita_credentials.txt

# ── 启动服务 ──
log_info "启动服务..."
docker-compose up -d

sleep 10

# ── 健康检查 ──
RUNNING=$(docker-compose ps | grep -c "Up" || true)
if [ $RUNNING -lt 8 ]; then
    log_error "部分容器启动失败：$RUNNING/10"
    exit 1
fi

log_success "部署完成！"
echo ""
echo "访问地址：http://$(hostname -I | awk '{print $1}'):8000"
echo "凭证文件：/root/openakita_credentials.txt"

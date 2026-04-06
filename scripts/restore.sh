#!/bin/bash
# OpenAkita 数据恢复脚本
# 用途：从备份恢复数据库和配置
# 使用：bash restore.sh <备份文件>
# 版本：v1.0 | 日期：2026-03-11

set -e

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }

# ── 检查参数 ──
if [ -z "$1" ]; then
    log_error "请指定备份文件：bash restore.sh <备份文件>"
    echo "示例:"
    echo "  恢复数据库：bash restore.sh /opt/openakita/backups/db/openakita_20260311_120000.sql.gz"
    echo "  恢复 Qdrant: bash restore.sh /opt/openakita/backups/qdrant/storage_20260311_120000.tar.gz"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    log_error "备份文件不存在：$BACKUP_FILE"
    exit 1
fi

# ── 确认恢复 ──
log_warning "警告：恢复操作将覆盖现有数据！"
echo "备份文件：$BACKUP_FILE"
read -p "确定继续吗？(yes/no): " -r
echo
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    log_info "取消恢复"
    exit 0
fi

# ── 恢复 PostgreSQL ──
if [[ "$BACKUP_FILE" == *.sql.gz ]]; then
    log_info "恢复 PostgreSQL 数据库..."
    
    # 解压并恢复
    gunzip -c "$BACKUP_FILE" | docker exec -i openakita-db psql -U postgres -d openakita
    
    log_success "数据库恢复完成"
    
    # 重启应用
    log_info "重启应用服务..."
    docker-compose restart app celery-worker
    
    log_success "应用重启完成"
fi

# ── 恢复 Qdrant ──
if [[ "$BACKUP_FILE" == *.tar.gz ]]; then
    log_info "恢复 Qdrant 向量数据库..."
    
    # 解压
    TEMP_DIR=$(mktemp -d)
    tar -xzf "$BACKUP_FILE" -C $TEMP_DIR
    
    # 停止 Qdrant
    log_info "停止 Qdrant 服务..."
    docker-compose stop qdrant
    
    # 备份现有数据
    log_info "备份现有数据..."
    docker cp openakita-qdrant:/qdrant/storage /tmp/qdrant_backup_$(date +%Y%m%d_%H%M%S)
    
    # 清空并恢复
    docker run --rm -v openakita-qdrant-data:/qdrant alpine rm -rf /qdrant/storage/*
    docker cp $TEMP_DIR/storage_* openakita-qdrant:/qdrant/storage
    
    # 启动 Qdrant
    log_info "启动 Qdrant 服务..."
    docker-compose start qdrant
    
    # 清理临时文件
    rm -rf $TEMP_DIR
    
    log_success "Qdrant 恢复完成"
fi

log_success "恢复完成！"
echo ""
echo "请检查服务状态：docker-compose ps"
echo "查看日志：docker-compose logs -f"

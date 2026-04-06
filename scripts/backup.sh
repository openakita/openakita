#!/bin/bash
# OpenAkita 数据备份脚本
# 用途：备份数据库、向量库、配置文件
# 使用：bash backup.sh [full|db|qdrant|config]
# 版本：v1.0 | 日期：2026-03-11

set -e

# ── 配置 ──
BACKUP_DIR="/opt/openakita/backups"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=7

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── 检查目录 ──
mkdir -p $BACKUP_DIR/{db,qdrant,config,logs}

# ── 备份 PostgreSQL ──
backup_db() {
    log_info "备份 PostgreSQL 数据库..."
    docker exec openakita-db pg_dump -U postgres openakita | gzip > $BACKUP_DIR/db/openakita_$DATE.sql.gz
    log_success "数据库备份完成：$BACKUP_DIR/db/openakita_$DATE.sql.gz"
}

# ── 备份 Qdrant ──
backup_qdrant() {
    log_info "备份 Qdrant 向量数据库..."
    docker cp openakita-qdrant:/qdrant/storage $BACKUP_DIR/qdrant/storage_$DATE
    tar -czf $BACKUP_DIR/qdrant/storage_$DATE.tar.gz -C $BACKUP_DIR/qdrant storage_$DATE
    rm -rf $BACKUP_DIR/qdrant/storage_$DATE
    log_success "Qdrant 备份完成：$BACKUP_DIR/qdrant/storage_$DATE.tar.gz"
}

# ── 备份配置文件 ──
backup_config() {
    log_info "备份配置文件..."
    tar -czf $BACKUP_DIR/config/config_$DATE.tar.gz \
        /opt/openakita/.env \
        /opt/openakita/docker-compose.prod.yml \
        /opt/openakita/nginx/ \
        /opt/openakita/monitoring/ \
        2>/dev/null || true
    log_success "配置文件备份完成：$BACKUP_DIR/config/config_$DATE.tar.gz"
}

# ── 清理旧备份 ──
cleanup_old() {
    log_info "清理 ${RETENTION_DAYS} 天前的备份..."
    find $BACKUP_DIR -type f -mtime +$RETENTION_DAYS -delete
    log_success "清理完成"
}

# ── 主流程 ──
case "${1:-full}" in
    full)
        backup_db
        backup_qdrant
        backup_config
        cleanup_old
        ;;
    db)
        backup_db
        cleanup_old
        ;;
    qdrant)
        backup_qdrant
        cleanup_old
        ;;
    config)
        backup_config
        cleanup_old
        ;;
    *)
        echo "用法：$0 [full|db|qdrant|config]"
        exit 1
        ;;
esac

log_success "备份完成！"

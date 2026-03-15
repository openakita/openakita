#!/usr/bin/env bash
# ============================================================
# OpenAkita 服务管理脚本
# 管理后端 (openakita serve) 和前端 seecrab (npm run dev)
# ============================================================

set -euo pipefail

# ---------- 配置 ----------
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$PROJECT_ROOT/apps/seecrab"
PID_DIR="$PROJECT_ROOT/devops/.pids"

BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"

BACKEND_PORT="${API_PORT:-18900}"
FRONTEND_PORT=5174

LOG_DIR="$PID_DIR"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

# ---------- 颜色 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $(date '+%H:%M:%S') $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date '+%H:%M:%S') $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; }
log_step()  { echo -e "${BLUE}[====]${NC} $*"; }

# ---------- 工具函数 ----------
ensure_pid_dir() {
    mkdir -p "$PID_DIR"
}

is_running() {
    local pid_file="$1"
    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$pid_file"
    fi
    return 1
}

wait_for_port() {
    local port="$1" name="$2" timeout="${3:-15}"
    local elapsed=0
    while ! lsof -iTCP:"$port" -sTCP:LISTEN -P -n >/dev/null 2>&1; do
        sleep 1
        elapsed=$((elapsed + 1))
        if [[ $elapsed -ge $timeout ]]; then
            log_warn "$name 未在 ${timeout}s 内监听端口 $port"
            return 1
        fi
    done
    return 0
}

# ---------- 后端 ----------
start_backend() {
    if is_running "$BACKEND_PID_FILE"; then
        log_warn "后端已在运行 (PID: $(cat "$BACKEND_PID_FILE"))"
        return 0
    fi

    log_info "启动后端服务 (port: $BACKEND_PORT) ..."
    cd "$PROJECT_ROOT"

    openakita serve > "$BACKEND_LOG" 2>&1 &
    local pid=$!
    echo "$pid" > "$BACKEND_PID_FILE"

    if wait_for_port "$BACKEND_PORT" "后端" 20; then
        log_info "后端启动成功 (PID: $pid, port: $BACKEND_PORT)"
    else
        log_warn "后端进程已启动 (PID: $pid)，但端口检测超时，请检查日志"
    fi
}

stop_backend() {
    if ! is_running "$BACKEND_PID_FILE"; then
        log_warn "后端未在运行"
        return 0
    fi

    local pid
    pid=$(cat "$BACKEND_PID_FILE")
    log_info "停止后端服务 (PID: $pid) ..."

    kill "$pid" 2>/dev/null || true
    # 等待进程退出
    local waited=0
    while kill -0 "$pid" 2>/dev/null; do
        sleep 1
        waited=$((waited + 1))
        if [[ $waited -ge 10 ]]; then
            log_warn "后端未响应 SIGTERM，强制终止 ..."
            kill -9 "$pid" 2>/dev/null || true
            break
        fi
    done

    rm -f "$BACKEND_PID_FILE"
    log_info "后端已停止"
}

# ---------- 前端 ----------
start_frontend() {
    if is_running "$FRONTEND_PID_FILE"; then
        log_warn "前端已在运行 (PID: $(cat "$FRONTEND_PID_FILE"))"
        return 0
    fi

    if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
        log_info "前端依赖未安装，执行 npm ci ..."
        cd "$FRONTEND_DIR" && npm ci
    fi

    log_info "启动前端服务 (port: $FRONTEND_PORT) ..."
    cd "$FRONTEND_DIR"

    npx vite --port "$FRONTEND_PORT" > "$FRONTEND_LOG" 2>&1 &
    local pid=$!
    echo "$pid" > "$FRONTEND_PID_FILE"

    if wait_for_port "$FRONTEND_PORT" "前端" 15; then
        log_info "前端启动成功 (PID: $pid, port: $FRONTEND_PORT)"
    else
        log_warn "前端进程已启动 (PID: $pid)，但端口检测超时，请检查日志"
    fi
}

stop_frontend() {
    if ! is_running "$FRONTEND_PID_FILE"; then
        log_warn "前端未在运行"
        return 0
    fi

    local pid
    pid=$(cat "$FRONTEND_PID_FILE")
    log_info "停止前端服务 (PID: $pid) ..."

    kill "$pid" 2>/dev/null || true
    # 同时清理可能的子进程 (node/vite)
    pkill -P "$pid" 2>/dev/null || true

    local waited=0
    while kill -0 "$pid" 2>/dev/null; do
        sleep 1
        waited=$((waited + 1))
        if [[ $waited -ge 10 ]]; then
            kill -9 "$pid" 2>/dev/null || true
            break
        fi
    done

    rm -f "$FRONTEND_PID_FILE"
    log_info "前端已停止"
}

# ---------- 组合操作 ----------
start_all() {
    log_step "启动所有服务"
    ensure_pid_dir
    start_backend
    start_frontend
    echo ""
    status_all
}

stop_all() {
    log_step "停止所有服务"
    stop_frontend
    stop_backend
    log_info "所有服务已停止"
}

restart_all() {
    log_step "重启所有服务"
    stop_all
    echo ""
    start_all
}

status_all() {
    log_step "服务状态"
    if is_running "$BACKEND_PID_FILE"; then
        log_info "后端:  ${GREEN}运行中${NC} (PID: $(cat "$BACKEND_PID_FILE"), port: $BACKEND_PORT)"
    else
        log_info "后端:  ${RED}已停止${NC}"
    fi
    if is_running "$FRONTEND_PID_FILE"; then
        log_info "前端:  ${GREEN}运行中${NC} (PID: $(cat "$FRONTEND_PID_FILE"), port: $FRONTEND_PORT)"
    else
        log_info "前端:  ${RED}已停止${NC}"
    fi
}

# ---------- 用法 ----------
usage() {
    cat <<EOF
用法: $(basename "$0") <命令> [服务]

命令:
  start   [backend|frontend]   启动服务 (默认全部)
  stop    [backend|frontend]   停止服务 (默认全部)
  restart [backend|frontend]   重启服务 (默认全部)
  status                       查看服务状态

示例:
  $(basename "$0") start             # 启动前后端
  $(basename "$0") start backend     # 仅启动后端
  $(basename "$0") stop frontend     # 仅停止前端
  $(basename "$0") restart           # 重启全部
  $(basename "$0") status            # 查看状态
EOF
}

# ---------- 主入口 ----------
main() {
    ensure_pid_dir

    local cmd="${1:-}"
    local svc="${2:-all}"

    case "$cmd" in
        start)
            case "$svc" in
                backend)  start_backend ;;
                frontend) start_frontend ;;
                all)      start_all ;;
                *)        log_error "未知服务: $svc"; usage; exit 1 ;;
            esac
            ;;
        stop)
            case "$svc" in
                backend)  stop_backend ;;
                frontend) stop_frontend ;;
                all)      stop_all ;;
                *)        log_error "未知服务: $svc"; usage; exit 1 ;;
            esac
            ;;
        restart)
            case "$svc" in
                backend)  stop_backend;  start_backend ;;
                frontend) stop_frontend; start_frontend ;;
                all)      restart_all ;;
                *)        log_error "未知服务: $svc"; usage; exit 1 ;;
            esac
            ;;
        status)
            status_all
            ;;
        *)
            usage
            exit 1
            ;;
    esac
}

main "$@"
